"""Grab the main window per screen and save it next to the mockup for a side-by-side check.

    python tools/screenshot.py [--out screenshots] [--step 0..5] [--nova]
    python tools/screenshot.py --all     # every screen/state → screenshots/latest/ + INDEX.md

`--nova` loads the real V8055 Sgr set (example_spectra/novasrg2026, read-only) into a temporary
session, builds masters in a temporary library and stacks inside the app before grabbing.
Writes `<out>/<Mockup>_ours.png` and `<out>/<Mockup>_compare.png` (mockup left, ours right).
The window is as tall as the mockup so long pages (Files) can be compared whole.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MOCKUPS = ["Step1Files", "Step2Geometry", "Step3Extract", "Step4Wavelength", "Step5Response",
           "Step6Export"]
NOVA = ROOT / "example_spectra" / "novasrg2026"


def wait(app, cond, timeout: float = 60.0) -> None:
    t = time.perf_counter()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.perf_counter() - t > timeout:
            raise TimeoutError("timed out waiting for the app")


def load_nova(app, win) -> None:
    """Drive the real Files screen like a user would: pick folders, create masters, stack."""
    files = win.files_screen
    for side, sub in (("target", "SgrNov32026"), ("reference", "hd163955")):
        from starex_reduce.core.files_stage import scan_lights

        win.runner.submit(f"scan.{side}", scan_lights, str(NOVA / sub))
        wait(app, lambda s=side: bool(win.session.inputs[s]["lights"]) and not win.runner.is_busy(f"peaks.{s}")
             and not win.runner.is_busy(f"scan.{s}"))
    files.cards["target"].set_name("V8055 Sgr")
    files.cards["reference"].set_name("HD 163955")
    t = time.perf_counter()
    files.bins["dark"].set_folder(str(NOVA / "darks"))
    files.bins["bias"].set_folder(str(NOVA / "bias"))
    files.create_masters()
    wait(app, lambda: not win.runner.is_busy("masters"))
    print(f"masters built in {time.perf_counter() - t:.2f} s (in the app, incl. UI)")
    t = time.perf_counter()
    assert win.run_stage("files"), win.session.log_lines[-3:]
    wait(app, lambda: not win.runner.is_busy("files"))
    print(f"stacked both in {time.perf_counter() - t:.2f} s (in the app, incl. UI)")
    t = time.perf_counter()
    win.open_step(1)
    assert win.run_stage("geometry"), win.session.log_lines[-3:]
    wait(app, lambda: not win.runner.is_busy("geometry"))
    print(f"geometry both in {time.perf_counter() - t:.2f} s (in the app, incl. UI)")
    wait(app, lambda: not any(win.runner.is_busy(f"view.geometry.{i}") for i in ("Target", "Reference")))
    t = time.perf_counter()
    win.open_step(2)
    assert win.run_stage("extract"), win.session.log_lines[-3:]
    wait(app, lambda: not win.runner.is_busy("extract"))
    print(f"extract both in {time.perf_counter() - t:.2f} s (in the app, incl. UI)")
    es = win.ensure_screen("extract")
    wait(app, lambda: es.viewer.current is not None)
    t = time.perf_counter()
    win.open_step(3)
    ws = win.ensure_screen("wavelength")
    wait(app, lambda: ws.result is not None)
    assert win.run_stage("wavelength"), win.session.log_lines[-3:]
    wait(app, lambda: not win.runner.is_busy("wavelength"))
    print(f"wavelength in {time.perf_counter() - t:.2f} s (in the app, incl. preview and UI)")
    t = time.perf_counter()
    win.open_step(4)
    rs = win.ensure_screen("response")
    wait(app, lambda: rs.result is not None)
    assert win.run_stage("response"), win.session.log_lines[-3:]
    wait(app, lambda: not win.runner.is_busy("response"))
    print(f"response in {time.perf_counter() - t:.2f} s (in the app, incl. preview and UI)")
    ex = win.ensure_export()  # illustrative observer values (the mockup's), never real ones
    ex.obscode.setText("ABCD")
    ex.site_name.setText("Example site")
    ex.lat.setText("31.96")
    ex.lon.setText("-111.60")
    ex.eq["name"].setText("Example equipment")
    ex.remember.setChecked(False)
    ex._fields_changed()
    for line in win.session.log_lines[-6:]:
        print("  log:", line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "screenshots")
    ap.add_argument("--step", type=int, action="append")
    ap.add_argument("--nova", action="store_true")
    ap.add_argument("--size", help="WxH: grab at this window size instead of the mockup size (no compare)")
    ap.add_argument("--all", action="store_true", help="every screen and state, offscreen, to screenshots/latest/ "
                                                       "with INDEX.md (tools/shots_all.py); non-zero if any fails")
    a = ap.parse_args()
    if a.all:
        sys.path.insert(0, str(Path(__file__).parent))
        from shots_all import run

        return run(ROOT / "screenshots" / "latest")
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"  # 1:1 pixels, comparable with the mockups
    home = Path(tempfile.gettempdir()) / "starex_shot_home"
    shutil.rmtree(home, ignore_errors=True)
    os.environ["STAREX_REDUCE_HOME"] = str(home)

    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication

    from starex_reduce.core.session import Session
    from starex_reduce.ui import theme
    from starex_reduce.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    win = MainWindow(session=Session.create(home / "sessions" / "2026-08-21") if a.nova else None)
    win.resize(1280, 960)
    win.show()
    if a.nova:
        load_nova(app, win)
    a.out.mkdir(parents=True, exist_ok=True)
    for idx in a.step or range(len(MOCKUPS)):
        name = MOCKUPS[idx]
        mock = QImage(str(ROOT / "docs" / "ui_mockups" / f"{name}.png"))
        if a.size:
            win.resize(*(int(v) for v in a.size.lower().split("x")))
        else:
            win.resize(1280, max(960, mock.height()))
        if idx < 5:
            win.open_step(idx)
        else:
            win.show_tab("export")
        for _ in range(5):
            app.processEvents()
        if idx == 5 and a.nova:
            ex = win.ensure_export()
            wait(app, lambda ex=ex: ex.flux is not None)
            ex.zoom.set_index(2)  # Hα + telluric, as in the mockup
            for _ in range(5):
                app.processEvents()
        if idx == 2 and a.nova:
            es = win.ensure_screen("extract")
            wait(app, lambda es=es: es.viewer.current is not None and es.curve.yData is not None)
            es.zoom_chips.set_index(1)  # Fit to bands
            es.view_toggle.set_index(0)
            for _ in range(5):
                app.processEvents()
        if idx == 1 and a.nova:
            gs = win.geometry_screen
            wait(app, lambda gs=gs: gs.viewer.current is not None)
            gs.zoom_chips.set_index(3)  # ×4, as in the mockup
            for _ in range(5):
                app.processEvents()
        ours = win.grab().toImage()
        if a.size:
            ours.save(str(a.out / f"{name}_{a.size}.png"))
            continue
        ours.save(str(a.out / f"{name}_ours.png"))
        h = max(mock.height(), ours.height())
        both = QImage(mock.width() + ours.width() + 16, h, QImage.Format.Format_RGB32)
        both.fill(QColor("#000000"))
        p = QPainter(both)
        p.drawImage(0, 0, mock)
        p.drawImage(mock.width() + 16, 0, ours)
        p.end()
        both.save(str(a.out / f"{name}_compare.png"))
        print(a.out / f"{name}_compare.png")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
