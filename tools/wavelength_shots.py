"""Screenshots of the Wavelength step on a real pair (docs/investigations/wavelength_2025-10-02_omiand.md).

    python tools/wavelength_shots.py <pair folder> --out screenshots/wavelength
        [--saved 370.734,0.120155,1.765e-07 --saved-source "2025-12-30_betacas__HD2772"]

Works on a temporary COPY of the pair (the data folder is never written) with a temporary settings folder whose
saved solution is the given prior. Grabs: the Reference and Target profiles with markers and fitted profiles, the
Solution card's "saved solution does not fit this night", a marker stopped at its neighbour (order rule) and the
Calibration points table.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STAREX_NO_SPLASH", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")


def wait(app, cond, timeout: float = 60.0) -> None:
    t = time.perf_counter()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.perf_counter() - t > timeout:
            raise TimeoutError("timed out waiting for the app")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pair", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "screenshots" / "latest")
    ap.add_argument("--saved", default="370.73426695313015,0.12015521400990345,1.7650618359488387e-07")
    ap.add_argument("--saved-source", default="2025-12-30_betacas__HD2772, free, 5 lines, RMS 0.212 Å")
    ap.add_argument("--saved-date", default="2026-11-22")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="wl_shots_"))
    home = tmp / "home"
    home.mkdir()
    os.environ["STAREXTRACTOR_HOME"] = str(home)
    pair = tmp / a.pair.name
    shutil.copytree(a.pair, pair, ignore=shutil.ignore_patterns("*_W.fit", "*_R.fit", "export", "response*"))
    from starextractor.core.instrument import InstrumentProfile

    prof = InstrumentProfile.load(home / "instrument_starex_lr.json")
    coeffs = [float(c) for c in a.saved.split(",")]
    prof.dispersion = {"coeffs_nm": coeffs, "order": len(coeffs) - 1, "date": a.saved_date,
                       "source": a.saved_source}
    prof.save()
    sess = json.loads((pair / "session.json").read_text(encoding="utf-8"))
    sess.setdefault("params", {}).setdefault("wavelength", {})
    for k in ("saved", "saved_source", "centre", "mode_user"):
        sess["params"]["wavelength"].pop(k, None)  # as an existing pair opens in this version
    (pair / "session.json").write_text(json.dumps(sess, ensure_ascii=False), encoding="utf-8")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from starextractor.core.session import Session
    from starextractor.core.settings import Settings
    from starextractor.ui import theme
    from starextractor.ui.main_window import MainWindow

    theme.apply(app)
    win = MainWindow(session=Session.load(pair), settings=Settings())
    win.resize(1440, 1500)
    win.show()
    app.processEvents()
    win.open_step(3)
    ws = win.ensure_screen("wavelength")
    wait(app, lambda: ws.result is not None and ws.result.get("target") is not None)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.02)
    a.out.mkdir(parents=True, exist_ok=True)
    shots = []

    def grab(name: str, widget=None) -> None:
        for m in ws.markers:  # no real mouse offscreen: a programmatic zoom leaves stale hover states behind
            m.setMouseHover(False)
        for _ in range(10):
            app.processEvents()
            time.sleep(0.02)
        path = a.out / f"{name}.png"
        (widget or win).grab().save(str(path))
        shots.append(path)

    grab("12b_wavelength_real_pair")
    ws.plot.getPlotItem().setXRange(300, 1180, padding=0)
    grab("12c_wavelength_real_pair_lines", ws.plot)
    grab("12d_wavelength_solution_card", ws.mode_buttons["refine"].parentWidget())
    grab("12e_wavelength_points_table", ws.table.parentWidget())
    # the order rule: drag Hδ towards Hγ; the marker stops 15 px before it
    m = ws.marker_by_line["Hδ"]
    hg = next(q["pixel"] for q in ws.result["points"] if q["line"] == "Hγ")
    m.setValue(hg + 40.0)  # a drag past Hγ…
    stopped = m.value()
    pi = ws.plot.getPlotItem()
    pi.setXRange(420, 740, padding=0)
    grab("12f_wavelength_order_stop", ws.plot)
    print(f"Hδ dragged to {hg + 40:.1f}: stopped at {stopped:.1f} (Hγ at {hg:.1f})")
    m.setValue(next(q["pixel"] for q in ws.result["points"] if q["line"] == "Hδ"))
    ws.tabs.set_index(1)
    ws._tab_changed(1)
    wait(app, lambda: ws.view == "Target")
    ws._x_range("lines")
    grab("12g_wavelength_real_pair_target", ws.plot)
    r = ws.result
    print("reference:", r["mode"], r["status"], r.get("mode_reason"), "|", r["prior"]["message"])
    print("fits:", {k: (round(v["rms"], 3), v["n"]) for k, v in r["fits"].items()})
    print("target:", (r.get("target") or {}).get("shift"), (r.get("target") or {}).get("manual_reset"))
    print("log tail:")
    print("\n".join(win.session.log_lines[-8:]) if hasattr(win.session, "log_lines") else "")
    for p in shots:
        print("wrote", p)
    win.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
