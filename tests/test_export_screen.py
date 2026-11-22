"""GUI smoke test of the Export tab on a synthetic Target_R.fit."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from pytestqt.qtbot import QtBot

from starex_reduce.core.fitsio import write_float32
from starex_reduce.core.session import Session
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow


@pytest.fixture

def win(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QApplication

    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    theme.apply(QApplication.instance())
    s = Session.create(tmp_path / "sess")
    n = 3200
    lam = 3900 + 1.2 * np.arange(n)
    flux = (1 + 2 * np.exp(-0.5 * ((lam - 6563.0) / 8) ** 2)).astype(np.float32)
    write_float32(s.folder / "Target_R.fit", flux, {
        "CRVAL1": 3900.0, "CDELT1": 1.2, "CRPIX1": 1, "CTYPE1": "Wavelength", "CUNIT1": "Angstrom",
        "DATE-OBS": "2026-08-21T03:42:58.415", "DATE-END": "2026-08-21T04:12:59.852", "EXPTIME": 300.0,
        "NCOMBINE": 6, "EXPTIME2": "6 x 300 s", "RSP_MODE": "library"}, ["test reduction"])
    for sid in ("files", "geometry", "extract", "wavelength", "response"):
        s.mark_done(sid, {}, "done")
    s.inputs["target"]["name"] = "V8055 Sgr"
    s.inputs["target"]["coords"] = {"source": "manual", "ra": "18 05 00", "dec": "-28 30 00"}
    w = MainWindow(session=s)
    w.opened = opened  # type: ignore[attr-defined]
    qtbot.addWidget(w)
    w.resize(1280, 960)
    w.show()
    return w


def save_tile(win: MainWindow, qtbot: QtBot, kind: str) -> None:
    ex = win.ensure_export()
    with qtbot.waitSignal(win.runner.finished, timeout=10000, check_params_cb=lambda slot, _r: slot == f"pub.{kind}"):
        assert ex.publish.save(kind)
    qtbot.wait(10)


def _fill(ex) -> None:
    ex.obscode.setText("xyzw")
    ex.site_name.setText("Test site")
    ex.lat.setText("31.96")
    ex.lon.setText("-111.60")
    ex.eq["name"].setText("Test equipment")
    ex._fields_changed()


def test_export_flow(win: MainWindow, qtbot: QtBot) -> None:
    win.show_tab("export")
    ex = win.ensure_export()
    qtbot.waitUntil(lambda: ex.flux is not None, timeout=10000)
    assert ex.objname.text() == "V8055 Sgr" and not ex.banner.isVisible()
    assert len(ex.markers["balmer"]) == 5 and ex.markers["telluric"]
    ex.zoom.set_index(2)  # Hα + telluric
    (a, b), _ = ex.plot.getPlotItem().vb.viewRange()
    assert a == pytest.approx(6200) and b == pytest.approx(7700)
    assert ex.peak.text().startswith("peak 6562.") and "from Hα" in ex.peak.text()
    assert ex.window.getRegion() == pytest.approx((a, b))

    _fill(ex)
    assert ex.plan.header["OBSERVER"] == "XYZW" and ex.plan.header["AAV_SITE"] == "Test site"
    assert win.settings["observer"]["obscode"] == "XYZW"  # remembered
    assert ex.plan.airmass is not None and 1 <= ex.plan.airmass <= 5

    body = ('{"VSXObject":{"Name":"V8055 Sgr","AUID":"000-BQQ-461","RA2000":"276.30313",'
            '"Declination2000":"-22.42486","VariabilityType":"NA","MaxMag":"8.1 V","MinMag":"21.6: V"}}')
    import io

    import starex_reduce.core.vsx as vsx_mod  # no network in tests: serve the verified answer shape

    class _Resp:
        def __enter__(self):
            return io.BytesIO(body.encode())

        def __exit__(self, *a: object) -> None:
            pass

    mp = pytest.MonkeyPatch()
    mp.setattr(vsx_mod.urllib.request, "urlopen", lambda req, timeout=0: _Resp())
    win.session.inputs["target"]["coords"] = {}
    ex.objname.setText("Nova Sgr 2026 No. 3")
    ex._objname_edited("x")
    ex.check_vsx()
    qtbot.waitUntil(lambda: ex.vsx is not None and ex.vsx.get("found"), timeout=5000)
    mp.undo()
    assert ex.objname.text() == "Nova Sgr 2026 No. 3"  # nothing applied until asked
    assert "AUID 000-BQQ-461" in ex.vsx_meta.text() and ex.vsx_use.isEnabled()
    ex.vsx_use.click()
    assert ex.objname.text() == "V8055 Sgr"
    assert win.session.inputs["target"]["coords"]["source"] == "vsx"  # coordinates filled (target had none)
    assert ex.plan.airmass is not None

    ex.show_step(1)
    if ex.ack_cb.isVisibleTo(ex.publish):  # the standing notes (first submission, …) are read before AVSpec saves
        ex.ack_cb.setChecked(True)
    ex.show_step(2)
    save_tile(win, qtbot, "avspec")  # writes the file and the log next to it; nothing opens
    path = ex.last_export
    assert path.name == "_v8055sgr_20260821_155_XYZW.fit"
    with fits.open(path) as hdul:
        assert hdul[0].header["OBJNAME"] == "V8055 Sgr" and hdul[0].header["CUNIT1"] == "Angstrom"
    log = path.with_name(path.stem + "_log.txt")
    assert log.exists() and "Export:" in log.read_text(encoding="utf-8")
    assert win.opened == []  # no web page, no folder popped up by saving
    ex.publish.folder_btn.click()
    assert win.opened and win.opened[-1].startswith("file:")
    assert "exports 3900" in ex.range_note.text()  # the exported range is the Response crop, not the zoom
    assert not hasattr(ex, "prepare_aras")
    for kind, suffix in (("csv", ".csv"), ("png", ".png")):
        save_tile(win, qtbot, kind)
        assert Path(ex.publish.tiles[kind].saved["path"]).suffix == suffix


def test_preflight_blocks_without_obscode(win: MainWindow, qtbot: QtBot) -> None:
    win.show_tab("export")
    ex = win.ensure_export()
    qtbot.waitUntil(lambda: ex.flux is not None, timeout=10000)
    ex.obscode.setText("")
    ex._fields_changed()
    assert ex.publish.save("avspec") is False
    assert "obscode" in ex.publish.block.text.text()


def test_export_values_persist_in_the_session(win: MainWindow, qtbot: QtBot) -> None:
    win.show_tab("export")
    ex = win.ensure_export()
    qtbot.waitUntil(lambda: ex.flux is not None, timeout=10000)
    ex.objname.setText("V8055 Sgr")
    ex._fields_changed()
    again = Session.load(win.session.folder)
    assert again.params["export"]["objname"] == "V8055 Sgr"


def test_objname_follows_files_until_edited(win: MainWindow, qtbot: QtBot) -> None:
    win.show_tab("export")
    ex = win.ensure_export()
    qtbot.waitUntil(lambda: ex.flux is not None, timeout=10000)
    win.show_tab("reduce")
    win.session.inputs["target"]["name"] = "Nova Sgr 2026"
    win.show_tab("export")
    assert ex.objname.text() == "Nova Sgr 2026"  # follows Select
    ex.objname.textEdited.emit("V8055 Sgr")
    ex.objname.setText("V8055 Sgr")
    ex._fields_changed()
    win.show_tab("reduce")
    win.session.inputs["target"]["name"] = "Something else"
    win.show_tab("export")
    assert ex.objname.text() == "V8055 Sgr"  # an edit made here wins


def test_site_defaults_to_frame_headers(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    lights = tmp_path / "l.fits"
    write_float32(lights, np.zeros((2, 2), np.float32), {"SITELAT": 31.958, "SITELONG": -111.598})
    win.session.inputs["target"]["lights"] = [{"path": str(lights), "used": True}]
    win.settings["site"] = {"name": "Home", "lat": 30.0, "lon": -95.0}
    assert win.site()["lat"] == 31.958 and win.site()["source"] == "headers"
    win.show_tab("export")
    ex = win.ensure_export()
    ex.load_session()
    assert ex.lat.text() == "31.958" and "frame headers" in ex.site_source.text()
    ex.lat.textEdited.emit("31")
    ex.lat.setText("31")
    ex._fields_changed()
    assert win.site()["source"] == "typed" and win.site()["lat"] == 31.0
    ex.site_from_headers()
    assert win.site()["lat"] == 31.958


def test_export_steps_collect_test_publish(win: MainWindow, qtbot: QtBot) -> None:
    from starex_reduce.ui.rail import StepState

    win.show_tab("export")
    ex = win.ensure_export()
    qtbot.waitUntil(lambda: ex.flux is not None, timeout=10000)
    assert len(ex.step_states) == 3 and not hasattr(ex, "steps")  # Collect, Test, Publish on the rail
    assert ex.pages.currentIndex() == 0 and _ex_state(ex, 0) == StepState.CURRENT
    ex.obscode.setText("")
    ex._fields_changed()
    ex.show_step(1)  # the Test step runs the test
    assert ex.test_summary.state == "err" and _ex_state(ex, 1) == StepState.MISSING  # red, as in Reduce
    texts = [ex.avspec_checks.itemAt(i).widget().text.text() for i in range(ex.avspec_checks.count())]
    assert any("obscode" in t for t in texts) and any("reads back in astropy" in t for t in texts)
    ex.show_step(2)
    assert _ex_state(ex, 1) == StepState.MISSING and ex.publish.save("avspec") is False
    ex.obscode.setText("XYZW")
    ex._fields_changed()
    assert ex.step_states[1][1] == "Needs re-run"  # changed data invalidates the test, never Collect
    ex.show_step(1)
    assert ex.test_summary.state == "ok" and _ex_state(ex, 1) == StepState.CURRENT
    ex.ack_cb.setChecked(True)
    ex.show_step(2)
    assert _ex_state(ex, 1) == StepState.DONE
    save_tile(win, qtbot, "avspec")
    path = ex.last_export
    assert path.exists() and path.with_name(path.stem + "_log.txt").exists()
    assert _ex_state(ex, 2) == StepState.CURRENT and ex.published_sig == ex._signature()
    ex.show_step(0)
    assert _ex_state(ex, 2) == StepState.DONE and "Saved" in ex.step_states[2][1]


def _ex_state(ex, i: int):
    """What the rail shows for Export step i (the open step is current unless it has missing data)."""
    from starex_reduce.ui.rail import StepState

    state = ex.step_states[i][0]
    return StepState.CURRENT if i == ex.step and state != StepState.MISSING else state
