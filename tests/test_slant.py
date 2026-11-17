"""Optional slant correction (docs/GEOMETRY_SLANT.md): detection, one combined resample, saved geometry."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage

from starex_reduce.core import geometry as g
from starex_reduce.core.fitsio import write_float32
from starex_reduce.core.geometry_stage import GeometrySpec, geometry_job, slant_detect_job, slant_for
from starex_reduce.core.instrument import InstrumentProfile
from starex_reduce.core.jobs import Ctx
from starex_reduce.synth import slanted_frame

TILT = -0.67


def _straightened(img: np.ndarray) -> tuple[np.ndarray, g.TiltResult]:
    t = g.detect_tilt(img)
    return g.straighten(img, t.angle_deg, 0.0, (t.row, t.x_centre)), t


@pytest.mark.parametrize("slant", [0.5, 1.5])
def test_detects_and_removes_known_slant(slant: float) -> None:
    img = slanted_frame(TILT, slant, seed=3)
    st, t = _straightened(img)
    r = g.detect_slant(st, t.row, t.lit)
    assert r.ok and abs(r.angle_deg - slant) < 0.05
    assert r.sigma_deg is not None and r.sigma_deg < 0.05 and r.n_blocks >= 4
    fixed = g.straighten(img, t.angle_deg, r.angle_deg, (t.row, t.x_centre))
    after = g.detect_slant(fixed, t.row, t.lit)
    assert after.ok and abs(after.angle_deg) < 0.05  # lines now vertical
    # the spectrum row and profile stay where they were (pivot on the spectrum row)
    cols = slice(int(t.lit[0]) + 50, int(t.lit[1]) - 50)
    r0 = round(t.row)

    def centroid(a: np.ndarray) -> float:
        prof = np.median(a[r0 - 15: r0 + 16, cols], axis=1)
        prof = prof - prof.min()
        return float((np.arange(len(prof)) * prof).sum() / prof.sum())
    assert abs(centroid(fixed) - centroid(st)) < 0.1
    assert abs(g.flatness(fixed) - g.flatness(st)) < 0.1


def test_no_features_is_not_measured() -> None:
    st, t = _straightened(slanted_frame(TILT, 1.0, seed=4, lines=0))
    r = g.detect_slant(st, t.row, t.lit)
    assert not r.ok and r.angle_deg is None
    assert r.text().startswith("Slant could not be measured")


def _old_rotate(img: np.ndarray, angle_deg: float, centre: tuple[float, float], threads: int = g.THREADS) -> np.ndarray:
    """The tilt-only resample as it was before slant existed (reference for byte identity)."""
    a = math.radians(angle_deg)
    m = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])
    c = np.asarray(centre, dtype=float)
    offset = c - m @ c
    h, w = img.shape
    out = np.empty_like(img, dtype=np.float32)
    step = max(32, h // (threads * 2))

    def strip(y0: int) -> None:
        y1 = min(h, y0 + step)
        ndimage.affine_transform(img, m, offset=offset + m @ np.array([y0, 0.0]), output_shape=(y1 - y0, w),
                                 output=out[y0:y1], order=1, mode="nearest")

    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(strip, range(0, h, step)))
    return out


def test_slant_zero_is_byte_identical_to_tilt_only() -> None:
    img = slanted_frame(TILT, 0.8, seed=5, width=1200, height=900, row0=450)
    c = (450.0, 600.0)
    assert np.array_equal(g.straighten(img, TILT, 0.0, c), _old_rotate(img, TILT, c))
    assert np.array_equal(g.rotate(img, TILT, c), _old_rotate(img, TILT, c))
    p = g.GeometryParams(tilt_deg=TILT, hot_pixels=False)
    assert np.array_equal(g.correct(img, p, Ctx()).image, g.correct(img, g.GeometryParams(
        tilt_deg=TILT, hot_pixels=False, slant_deg=0.0), Ctx()).image)


def test_profile_round_trip_keeps_slant(tmp_path: Path) -> None:
    prof = InstrumentProfile.load(tmp_path / "inst.json")
    prof.tilt_deg, prof.slant_deg, prof.slant_date = -0.66, -1.45, "2026-11-17"
    prof.save()
    back = InstrumentProfile.load(tmp_path / "inst.json")
    assert (back.tilt_deg, back.slant_deg, back.slant_date) == (-0.66, -1.45, "2026-11-17")
    old = tmp_path / "old.json"  # a profile saved before slant existed still loads
    old.write_text('{"tilt_deg": -0.5}', encoding="utf-8")
    assert InstrumentProfile.load(old).slant_deg is None


def test_slant_is_used_only_when_ticked() -> None:
    p = {"use_saved": True, "saved_slant": -1.4}
    assert slant_for(p, "Target") == (None, "off")
    p["slant_on"] = True
    assert slant_for(p, "Target") == slant_for(p, "Reference") == (-1.4, "saved")
    p.update(slant=0.7, slant_source="detect")  # one value for the night, both images
    assert slant_for(p, "Target") == slant_for(p, "Reference") == (0.7, "detect")
    legacy = {"slant_on": True, "per_image": {"Target": {"slant": 0.5, "slant_source": "drawn"}}}
    assert slant_for(legacy, "Reference") == (0.5, "drawn")  # an old per-image value now covers both


def test_lean_on_the_original_image_is_slant_minus_tilt() -> None:
    """A line leaning by s after straightening leans by s − tilt on the stack as it is (what Draw angle on
    the Original view adds back)."""
    tilt, slant = -0.67, 1.0
    a, k = math.radians(tilt), math.tan(math.radians(slant))
    row, col = math.cos(a) + k * math.sin(a), -math.sin(a) + k * math.cos(a)  # direction (1, k) mapped back
    lean = math.degrees(math.atan(col / row))
    assert lean + tilt == pytest.approx(slant, abs=0.01)


def test_never_corrects_twice(tmp_path: Path) -> None:
    img = slanted_frame(TILT, 1.0, seed=6, width=1200, height=900, row0=450)
    src, out = tmp_path / "Target_S.fit", tmp_path / "Target_G.fit"
    write_float32(src, img, {}, [])
    spec = GeometrySpec("Target", src, out, g.GeometryParams(tilt_deg=TILT, hot_pixels=False, slant_deg=1.0),
                        "manual", None, None, "detect")
    run = geometry_job(Ctx(), [spec])
    assert "slant +1.000° (detect)" in run.log[0]
    from starex_reduce.core.fitsio import read_header
    assert read_header(out)["GEO_SLNT"] == 1.0
    again = GeometrySpec("Target", out, tmp_path / "x.fit", spec.params, "manual", None, None, "detect")
    with pytest.raises(ValueError, match="already geometry-corrected"):
        geometry_job(Ctx(), [again])
    with pytest.raises(ValueError, match="already geometry-corrected"):
        slant_detect_job(Ctx(), out, False, False, None)


def test_drawn_slant_in_image_pixels() -> None:
    assert g.drawn_slant(100, 0, 100 + math.tan(math.radians(1.5)) * 800, 800) == pytest.approx(1.5)
    with pytest.raises(ValueError):
        g.drawn_slant(0, 0, 100, 10)  # closer to horizontal: not a slit line


def test_drawn_slant_is_independent_of_the_view_zoom(qtbot) -> None:
    """The viewer stretches Y (rows shown taller than columns): scene points map back to image pixels."""
    from starex_reduce.ui.widgets.image_viewer import ImageViewer

    v = ImageViewer()
    qtbot.addWidget(v)
    v.resize(800, 400)
    v.show()
    v.vb.setLimits(xMin=0, xMax=2000, yMin=0, yMax=2000)
    v.vb.setXRange(0, 2000, padding=0)
    v.vb.setYRange(900, 1000, padding=0)  # 100 rows over 400 px, 2000 cols over 800 px: 10× stretched
    qtbot.wait(20)
    k = math.tan(math.radians(1.0))
    a, b = (1000.0, 910.0), (1000.0 + k * 80, 990.0)
    sa, sb = v.vb.mapViewToScene(pg_point(a)), v.vb.mapViewToScene(pg_point(b))
    pa, pb = v.vb.mapSceneToView(sa), v.vb.mapSceneToView(sb)
    screen_angle = math.degrees(math.atan((sb.x() - sa.x()) / (sb.y() - sa.y())))
    assert abs(screen_angle - 1.0) > 0.5  # measured in screen pixels the angle would be wrong
    assert g.drawn_slant(pa.x(), pa.y(), pb.x(), pb.y()) == pytest.approx(1.0, abs=1e-6)


def pg_point(p: tuple[float, float]):
    from PySide6.QtCore import QPointF

    return QPointF(*p)
