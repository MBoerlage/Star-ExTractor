"""Master library, calibration rules (SPEC §4) and stacking, on small synthetic frames."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import synth

from starex_reduce.core import files_stage
from starex_reduce.core.combine import sigma_clip_mean
from starex_reduce.core.fitsio import read_header, read_image
from starex_reduce.core.frames import BIAS, DARK, scan
from starex_reduce.core.jobs import Cancelled, CancelToken, Ctx
from starex_reduce.core.masters import (
    LightSettings,
    Master,
    MasterLibrary,
    Tolerances,
    build_master,
    calibration_frame,
    group_for_masters,
    pick_calibration,
    zwo_gain_ratio,
)
from starex_reduce.core.session import Session
from starex_reduce.core.stack import StackParams, run_stack

SCALE = 0.1  # 384 × 216 frames


@pytest.fixture(scope="module")
def night(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[Path]]:
    out = tmp_path_factory.mktemp("night")
    return synth.write_set(out, "nova", n_target=4, n_ref=5, n_cal=5, scale=SCALE)


@pytest.fixture
def lib(tmp_path: Path, night: dict[str, list[Path]]) -> MasterLibrary:
    lib = MasterLibrary(tmp_path / "masters")
    for kind, key in ((BIAS, "bias"), (DARK, "darks")):
        for g in group_for_masters(scan(night[key][0].parent)):
            build_master(g, kind, lib, Ctx())
    return lib


def _light(exptime: float = 300.0, gain: float = 200, offset: float = 3, temp: float = -5.5) -> LightSettings:
    return LightSettings(exptime, gain, offset, temp, (384, 216))


def _master(lib: MasterLibrary, kind: str, exptime: float, **kw: object) -> Master:
    m = Master(id=f"{kind}{exptime}{len(lib.masters)}", kind=kind, exptime=exptime,
               gain=kw.get("gain", 200), offset=kw.get("offset", 3), temp=kw.get("temp", -5.5),  # type: ignore[arg-type]
               size=kw.get("size", (384, 216)), binning=(1, 1), nframes=5, date="2026-08-21",  # type: ignore[arg-type]
               created="2026-08-21", path="x.fits", bias_subtracted=bool(kw.get("bias_subtracted", False)))
    lib.masters.append(m)
    return m


# ---- library and masters ------------------------------------------------------------------------
def test_masters_built_and_persisted(lib: MasterLibrary, night: dict[str, list[Path]]) -> None:
    kinds = sorted((m.kind, m.exptime, m.bias_subtracted) for m in lib.masters)
    assert kinds == [(BIAS, 0.001, False), (DARK, 300.0, True)]  # dark stored bias-subtracted
    dark = next(m for m in lib.masters if m.kind == DARK)
    assert (dark.gain, dark.offset, dark.size, dark.nframes) == (251, 20, (384, 216), 5)
    signal = lib.data(dark)
    assert abs(float(np.median(signal)) - 32) < 3  # dark − bias ≈ +32 ADU per 300 s
    h = read_header(lib.abspath(dark))
    assert h["NCOMBINE"] == 5 and h["BIASSUB"] is True and h["BITPIX"] == -32
    again = MasterLibrary(lib.folder)
    assert [m.id for m in again.masters] == [m.id for m in lib.masters]


def test_master_build_never_touches_originals(tmp_path: Path, night: dict[str, list[Path]]) -> None:
    import hashlib

    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in night["bias"]}
    lib = MasterLibrary(tmp_path / "m")
    build_master(scan(night["bias"][0].parent), BIAS, lib, Ctx())
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in night["bias"]} == before


def test_build_can_be_cancelled(tmp_path: Path, night: dict[str, list[Path]]) -> None:
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(Cancelled):
        build_master(scan(night["bias"][0].parent), BIAS, MasterLibrary(tmp_path / "m"), Ctx(cancel=tok))


# ---- calibration rules ------------------------------------------------------------------------
def test_exact_dark_without_bias_is_valid(tmp_path: Path) -> None:
    lib = MasterLibrary(tmp_path)
    _master(lib, DARK, 300.0)
    plan = pick_calibration(_light(), lib)
    assert plan.ok and plan.mode == "exact" and plan.lines[0][2] == "Dark 300 s · exact match"


def test_dark_without_bias_at_other_exposure_is_an_error(tmp_path: Path) -> None:
    lib = MasterLibrary(tmp_path)
    _master(lib, DARK, 90.0)
    plan = pick_calibration(_light(30.0), lib)
    assert not plan.ok
    assert any(i.code == "dark_needs_bias" for i in plan.issues)
    assert "scaling it needs a bias" in plan.lines[0][2]


def test_scaled_dark_with_bias(tmp_path: Path) -> None:
    lib = MasterLibrary(tmp_path)
    _master(lib, DARK, 90.0)
    _master(lib, BIAS, 0.001)
    plan = pick_calibration(_light(30.0), lib)
    assert plan.ok and plan.mode == "scaled" and plan.dark_scale == pytest.approx(1 / 3)
    assert "scaled ×0.333" in plan.lines[0][2]


def test_closest_exposure_and_size_must_match(tmp_path: Path) -> None:
    lib = MasterLibrary(tmp_path)
    _master(lib, DARK, 5.0)
    _master(lib, DARK, 120.0)
    _master(lib, DARK, 90.0, size=(3840, 2160))
    _master(lib, BIAS, 0.001)
    plan = pick_calibration(_light(90.0), lib)
    assert plan.dark is not None and plan.dark.exptime == 120.0


def test_no_dark_says_which_side(tmp_path: Path) -> None:
    plan = pick_calibration(_light(10.0), MasterLibrary(tmp_path), side="Reference")
    assert not plan.ok and plan.issues[0].text == "Reference needs a dark"


def test_pedestal_match_default_on_for_gain_offset_mismatch(tmp_path: Path) -> None:
    lib = MasterLibrary(tmp_path)
    _master(lib, DARK, 300.0, gain=251, offset=20, bias_subtracted=True)
    plan = pick_calibration(_light(), lib, gain_scale=True)
    assert plan.ok and plan.mode == "pedestal"
    assert plan.gain_scale == pytest.approx(10 ** (-51 / 200)) and plan.gain_scale == pytest.approx(0.556, abs=1e-3)
    assert "gain 251/offset 20, lights at 200/3: using dark-bias only" in plan.issues[0].text


def test_pedestal_off_needs_use_anyway(tmp_path: Path) -> None:
    lib = MasterLibrary(tmp_path)
    _master(lib, DARK, 300.0, gain=251, offset=20)
    plan = pick_calibration(_light(), lib, match_pedestal=False)
    assert not plan.ok and any(i.action == "use_anyway" for i in plan.issues)
    plan = pick_calibration(_light(), lib, match_pedestal=False, accepted={"calib_mismatch"})
    assert plan.ok and any(i.code == "calib_mismatch_accepted" for i in plan.issues)


def test_pedestal_needs_dark_signal(tmp_path: Path) -> None:
    lib = MasterLibrary(tmp_path)
    _master(lib, DARK, 300.0, gain=251, offset=20)  # raw dark, no bias in the library
    plan = pick_calibration(_light(), lib)
    assert not plan.ok and plan.issues[0].code == "pedestal_needs_bias"


def test_temperature_mismatch_is_visible_and_acceptable(tmp_path: Path) -> None:
    lib = MasterLibrary(tmp_path)
    _master(lib, DARK, 300.0, temp=-15.0)
    plan = pick_calibration(_light(), lib)
    assert not plan.ok and plan.issues[0].code == "temp_mismatch"
    assert pick_calibration(_light(), lib, accepted={"temp_mismatch"}).ok
    assert pick_calibration(_light(), lib, tol=Tolerances(enabled=False)).ok


def test_calibration_frame_formulas(lib: MasterLibrary) -> None:
    bias = next(m for m in lib.masters if m.kind == BIAS)
    dark = next(m for m in lib.masters if m.kind == DARK)
    S, B = lib.data(dark), lib.data(bias)
    ped = pick_calibration(_light(30.0), lib, gain_scale=False)
    np.testing.assert_allclose(calibration_frame(ped, lib), S * 0.1, rtol=1e-6)
    normal = pick_calibration(_light(30.0, gain=251, offset=20), lib)
    assert normal.mode == "scaled"
    np.testing.assert_allclose(calibration_frame(normal, lib), B + S * 0.1, rtol=1e-6)


def test_zwo_gain_ratio() -> None:
    assert zwo_gain_ratio(251, 200) == pytest.approx(1.8, abs=0.01)
    assert zwo_gain_ratio(200, 200) == 1.0


# ---- stacking ----------------------------------------------------------------------------------
def test_sigma_clip_rejects_outlier() -> None:
    d = np.full((7, 2, 3), 100.0, np.float32)
    d += np.random.default_rng(0).normal(0, 1, d.shape).astype(np.float32)
    d[3, 0, 0] = 60000.0
    out = sigma_clip_mean(d)
    assert abs(out[0, 0] - 100) < 2 and out.dtype == np.float32
    same = np.full((5, 1, 1), 7.0, np.float32)
    assert sigma_clip_mean(same)[0, 0] == 7.0


def test_stack_with_pedestal_match_stays_positive(tmp_path: Path, lib: MasterLibrary,
                                                  night: dict[str, list[Path]]) -> None:
    frames = scan(night["target"][0].parent)
    plan = pick_calibration(LightSettings.from_frames(frames), lib)
    assert plan.mode == "pedestal"
    res = run_stack(Ctx(), frames, calibration_frame(plan, lib), StackParams(), tmp_path / "Target_S.fit",
                    plan.describe(), "V8055 Sgr")
    data = read_image(res.path)
    assert 340 < res.background < 400  # light pedestal kept (~380), never negative
    assert abs(res.peak_row - 107) <= 2  # row 1070 at scale 0.1
    h = read_header(res.path)
    assert h["NCOMBINE"] == 4 and h["EXPTIME2"] == "4 x 300 s" and h["OBJNAME"] == "V8055 Sgr"
    assert h["DATE-OBS"] < h["DATE-END"] and h["STACKMTH"].startswith("sigma_clip_mean")
    assert data.shape == (216, 384) and data.dtype == np.float32


def _specs(lib: MasterLibrary, night: dict[str, list[Path]], drop_first: bool = False) -> list:
    from starex_reduce.core.frames import LIGHT, load_frame

    out = []
    for image, key in (("Target", "target"), ("Reference", "reference")):
        paths = night[key][1:] if drop_first and key == "target" else night[key]
        spec, problem = files_stage.spec_from(image, [load_frame(p, LIGHT) for p in paths], {}, {}, key, lib,
                                              Tolerances())
        assert spec is not None, problem
        out.append(spec)
    return out


def test_unticking_a_frame_changes_stack_and_key(tmp_path: Path, lib: MasterLibrary,
                                                  night: dict[str, list[Path]]) -> None:
    """Restacking with one sub fewer (Acquisition) changes the file, so the loaded pair goes out of date."""
    import time

    from starex_reduce.core.pair import load_pair, read_stack

    s = Session.create(tmp_path / "sess")
    files_stage.stack_job(Ctx(), _specs(lib, night), lib, s.folder)
    infos = {r: read_stack(s.folder / f"work/{r.title()}_S.fit") for r in ("target", "reference")}
    load_pair(s, infos, {"target": "T", "reference": "R"})
    assert s.status["files"].status == "valid"
    first = read_image(s.folder / "work/Target_S.fit")
    time.sleep(0.02)  # a new mtime on coarse file systems
    files_stage.stack_job(Ctx(), _specs(lib, night, drop_first=True)[:1], lib, s.folder)
    assert s.changed() == ["files"]
    assert not s.image_done("files", "Target") and s.image_done("files", "Reference")
    assert not np.array_equal(first, read_image(s.folder / "work/Target_S.fit"))
    assert read_header(s.folder / "work/Target_S.fit")["NCOMBINE"] == 3


def test_specs_report_plain_problems(tmp_path: Path) -> None:
    spec, problem = files_stage.spec_from("Target", [], {}, {}, "", MasterLibrary(tmp_path), Tolerances())
    assert spec is None and problem == "Target lights: no frames ticked"
