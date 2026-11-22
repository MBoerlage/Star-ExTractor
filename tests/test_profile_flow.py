"""The spectrograph record carries through: bands follow the spectrum, start from the night's Acquisition
rows or the saved default rows; Acquisition uses the saved tilt and flips; crop/normalisation are kept."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import synth

from starextractor.core import extract_stage as es
from starextractor.core.extract import Band
from starextractor.core.geometry import spectrum_row
from starextractor.core.session import Session

P = synth.scaled(synth.PRESETS["nova"], 0.25)


def _straight(shift: int = 0) -> np.ndarray:
    from starextractor.core import geometry as g
    from starextractor.core.jobs import Ctx

    img = g.correct(synth.make_light(P, "reference").astype(np.float32), g.GeometryParams(), Ctx()).image
    return np.roll(img, shift, axis=0)


def test_stored_bands_follow_the_spectrum() -> None:
    img = _straight()
    r = spectrum_row(img)
    bands = es.bands_at(r, {"spec": [-4, 4], "sky_a": [-30, -15], "sky_b": [15, 30]})
    stored = {"bands": bands.to_json(), "anchor": r}
    same, how, _ = es.resolve_bands(img, stored, None)
    assert same == bands and how == "stored"
    moved, how, _ = es.resolve_bands(_straight(7), stored, None)  # Geometry moved the spectrum down 7 rows
    assert how == "followed the spectrum (+7 rows)"
    assert moved.spectrum == Band(bands.spectrum.top + 7, bands.spectrum.bottom + 7)
    legacy, how, _ = es.resolve_bands(_straight(7), {"bands": bands.to_json(), "anchor": None}, None)
    assert legacy == bands and how == "stored"  # older sessions: kept as they were


def test_new_image_starts_from_acquisition_rows_then_default_rows(tmp_path: Path) -> None:
    s = Session.create(tmp_path / "s")
    default = {"spec": [-5, 5], "sky_a": [-40, -20], "sky_b": [20, 40]}
    assert es.default_offsets(s, "Target", default) == (default, "default rows")
    s.params["acq"] = {"rows": {"target": {"row": 1070.0, "tilt_deg": -0.67, "x_centre": 1900.0, "spec": [-9, 9],
                                           "sky_a": [-60, -31], "sky_b": [31, 60], "locked_from": "a", "fwhm": 5.0}}}
    off, src = es.default_offsets(s, "Target", default)
    assert src == "Acquisition rows" and off["spec"] == [-9, 9]
    assert es.default_offsets(s, "Reference", default) == (default, "default rows")
    img = _straight()
    b, how, placed = es.resolve_bands(img, None, off, src)
    r = round(spectrum_row(img))
    assert how == "Acquisition rows" and placed is None and b.spectrum == Band(r - 9, r + 9)
    assert es.offsets_of(b, r) == off


def test_acquisition_uses_the_saved_tilt_in_detector_pixels(qtbot, tmp_path: Path) -> None:
    from starextractor.ui.main_window import MainWindow

    w = MainWindow(session=Session.create(tmp_path / "s"))
    qtbot.addWidget(w)
    a = w.ensure_acquisition()
    prof = w.profile
    prof.tilt_deg, prof.flip_x, prof.flip_y = -0.67, False, False
    assert a.saved_raw_tilt() == -0.67
    prof.flip_x = True  # Geometry measured it mirrored
    assert a.saved_raw_tilt() == 0.67
    a.apply_orientation()
    assert a.plot.getPlotItem().vb.state["xInverted"] and a.viewer.vb.state["xInverted"]
    prof.tilt_deg = None
    assert a.saved_raw_tilt() is None


def test_crop_and_norm_are_kept_for_the_next_session(qtbot, tmp_path: Path) -> None:
    from starextractor.core.instrument import InstrumentProfile
    from starextractor.ui.main_window import MainWindow

    w = MainWindow(session=Session.create(tmp_path / "s"))
    qtbot.addWidget(w)
    w.open_step(4)
    rs = w.ensure_screen("response")
    rs.crop_hi.setText("7800")
    rs._norm_edited()
    assert tuple(InstrumentProfile.load().crop_a) == (3900.0, 7800.0)
