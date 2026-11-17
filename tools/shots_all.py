"""Every screen of the current build, driven through the real UI code paths, saved as
screenshots/latest/NN_screen_state.png plus INDEX.md. Called by `python tools/screenshot.py --all`.

Runs offscreen at 1920x1080 in temporary sessions and a temporary settings folder: the example data in
example_spectra/novasrg2026 is only read, never written. Observer values are illustrative (ABCD).
The Plan's SIMBAD answer is a fixed P Cyg result fed through the real lookup path (no network in a
screenshot run, and the same answer every run)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOVA = ROOT / "example_spectra" / "novasrg2026"
W, H = 1920, 1080


class Shooter:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.index: list[tuple[str, str]] = []
        self.failed: list[str] = []
        self.skipped: list[tuple[str, str]] = []

    def setup(self) -> None:
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        if sys.platform == "win32":  # the offscreen platform has no font database of its own on Windows
            os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        self.tmp = Path(tempfile.mkdtemp(prefix="starex_shots_"))
        os.environ["STAREX_REDUCE_HOME"] = str(self.tmp / "home")
        from PySide6.QtWidgets import QApplication

        from starex_reduce.ui import theme

        self.app = QApplication.instance() or QApplication([])
        theme.apply(self.app)

    # ---- helpers ---------------------------------------------------------------------------------------
    def spin(self, seconds: float = 0.0, until: Callable[[], bool] | None = None, timeout: float = 120.0) -> None:
        t0 = time.perf_counter()
        while True:
            self.app.processEvents()
            el = time.perf_counter() - t0
            if until is None and el >= seconds:
                break
            if until is not None and until() and el >= seconds:
                break
            if el > timeout:
                raise TimeoutError("timed out waiting for the app")
            time.sleep(0.005)
        for _ in range(10):
            self.app.processEvents()

    def shot(self, name: str, what: str, widget=None) -> None:
        w = widget if widget is not None else self.win
        self.spin(0.15)
        path = self.out / f"{name}.png"
        if not w.grab().save(str(path)):
            self.failed.append(name)
            return
        self.index.append((path.name, what))
        print("  ", path.name)

    def window(self, folder: Path):
        from starex_reduce.core.session import Session
        from starex_reduce.ui.main_window import MainWindow

        win = MainWindow(session=Session.create(folder))
        win.settings["celebrate_saves"] = True
        win.resize(W, H)
        win.show()
        self.spin(0.3)
        self.win = win
        return win

    def busy(self, *slots: str) -> Callable[[], bool]:
        return lambda: not any(self.win.runner.is_busy(s) for s in slots)

    # ---- Plan ------------------------------------------------------------------------------------------
    def plan(self) -> None:
        from starex_reduce.core import simbad

        win = self.window(self.tmp / "plan" / "2026-08-20")
        win.show_tab("plan")
        plan = win.ensure_plan()
        self.shot("01_plan_empty", "Plan tab, new session, nothing entered")
        res = simbad.SimbadResult("P Cyg", True, "* P Cyg", 304.44667, 38.03293, "s*b", "B1-2Ia-0ep", 4.82)
        real = simbad.lookup
        simbad.lookup = lambda name, timeout=0, fetch=None: res  # fixed answer, through the real job + slot
        try:
            plan.name.setText("P Cyg")
            plan.check_simbad()
            self.spin(0.1, until=lambda: plan.simbad_btn.isEnabled())
        finally:
            simbad.lookup = real
        self.shot("02_plan_simbad_candidates", "Plan: P Cyg resolved (SIMBAD answer fixed for the run), the 10 "
                                               "nearest reference candidates with flux flags")
        plan.use_selected(0)
        sa = plan.copy_target_btn
        while sa is not None and not hasattr(sa, "ensureWidgetVisible"):
            sa = sa.parentWidget()
        if sa is not None:
            sa.ensureWidgetVisible(plan.copy_ref_btn)
        self.shot("03_plan_observing_plan", "Plan: reference chosen; observing plan with target and reference side "
                                            "by side and the RA/Dec copy buttons")
        win.close()

    # ---- the nova night ----------------------------------------------------------------------------------
    def nova(self) -> None:
        win = self.window(self.tmp / "sessions" / "2026-08-20")
        win.show_tab("acquisition")
        a = win.ensure_acquisition()
        a.paste_names("Target:      V8055 Sgr          Reference:   HD 163955")
        a.enqueue(sorted((NOVA / "SgrNov32026").glob("*.fits")))
        self.spin(0.2, until=lambda: not a.queue and not a.busy)
        a.collect.set_index(1)
        a.enqueue(sorted((NOVA / "hd163955").glob("*.fits")))
        self.spin(0.2, until=lambda: not a.queue and not a.busy)
        a.set_tabs.set_index(0)
        a.view_mode.set_index(0)
        self.shot("04_acquisition_target_set", "Acquisition: target set (V8055 Sgr, 6 subs), ADU/saturation plot, "
                                               "stack SNR, sub list with flags")
        a.set_tabs.set_index(1)
        self.shot("05_acquisition_reference_set", "Acquisition: reference set (HD 163955, 10 subs)")
        a.set_tabs.set_index(0)
        a.view_mode.set_index(1)
        self.spin(0.1, until=lambda: a._shown_view is not None)
        a.row_fields["half"].setValue(a.row_fields["half"].value() + 3)
        a.row_fields["gap"].setValue(a.row_fields["gap"].value() + 5)
        a._rows_edited()
        self.spin(0.3, until=lambda: not a.queue and not a.busy and a.rows_by["target"].locked_from == "edited")
        self.spin(0.5)
        self.shot("06_acquisition_rows_edited", "Acquisition: target rows edited by hand (±rows +3, sky gap +5), "
                                                "set re-analysed, sub image with the new bands")

        # Reduce: Files from the Acquisition suggestion
        win.open_step(0)
        files = win.files_screen
        files.load_session()
        files.suggestion.use()
        self.spin(0.5, until=self.busy("peaks.target", "peaks.reference"))
        files.cards["target"].set_name("V8055 Sgr")
        files.cards["reference"].set_name("HD 163955")
        self.shot("07_files_use_suggestion", "Reduce › Files: Acquisition sets file found, Use suggestion applied "
                                             "(good subs ticked, names filled)")
        files.bins["dark"].set_folder(str(NOVA / "darks"))
        files.bins["bias"].set_folder(str(NOVA / "bias"))
        files.create_masters()
        self.spin(0.3, until=self.busy("masters"))
        card = files.cards["target"]
        card.pedestal_cb.setChecked(True)
        self._scroll_to(card.pedestal_cb)
        self.shot("08_files_calibration_pedestal", "Reduce › Files: masters built from the nova darks/bias; "
                                                   "calibration lines with Match pedestal on")
        assert win.run_stage("files"), win.session.log_lines[-3:]
        self.spin(0.3, until=self.busy("files"))
        self._scroll_to(files.btn_both)
        self.shot("09_files_stacked", "Reduce › Files: both sets stacked (there is no separate Stack screen: "
                                      "stacking is the Files step)")

        # Geometry: tilt, slant detected and applied
        win.open_step(1)
        gs = win.geometry_screen
        assert win.run_stage("geometry")
        self.spin(0.3, until=self.busy("geometry"))
        gs.detect_slant()
        self.spin(0.3, until=self.busy("slant.Target"))
        gs.slant_on.setChecked(True)
        assert win.run_stage("geometry")
        self.spin(0.3, until=self.busy("geometry", "view.geometry.Target"))
        self.spin(0.5, until=lambda: (gs.display.get("Target") or {}).get("corr") is not None)
        gs.zoom_chips.set_index(1)
        gs.view_toggle.set_index(0)
        gs.viewer.show_corrected(False)
        self.shot("10_geometry_before", "Reduce › Geometry: Original stack (before), tilt and slant detected, "
                                        "Correct slant on")
        gs.view_toggle.set_index(1)
        gs.viewer.show_corrected(True)
        self.shot("10_geometry_after", "Reduce › Geometry: Corrected (after): tilt + slant removed in one resample")

        win.open_step(2)
        es = win.ensure_screen("extract")
        assert win.run_stage("extract")
        self.spin(0.3, until=self.busy("extract"))
        self.spin(0.3, until=lambda: es.viewer.current is not None and es.curve.yData is not None)
        es.zoom_chips.set_index(1)
        es.view_toggle.set_index(0)
        self.shot("11_extract", "Reduce › 1D Extract: spectrum and sky bands on the corrected stack, 1D profile")

        win.open_step(3)
        ws = win.ensure_screen("wavelength")
        self.spin(0.3, until=lambda: ws.result is not None)
        assert win.run_stage("wavelength")
        self.spin(0.5, until=self.busy("wavelength"))
        self._scroll_to(ws.table)  # the residuals table sits below the plot
        self.shot("12_wavelength", "Reduce › Wavelength: Balmer lines identified (markers on the profile), "
                                   "calibration points with their residuals")

        win.open_step(4)
        rs = win.ensure_screen("response")
        self.spin(0.3, until=lambda: rs.result is not None)
        assert win.run_stage("response")
        self.spin(0.5, until=self.busy("response"))
        self.shot("13_response", "Reduce › Response: Pickles A0V, Balmer/telluric masks shaded, upper-envelope "
                                 "curve bridging the gaps")

        self.export(win)
        self.misc(win)
        win.close()

    def _scroll_to(self, w) -> None:
        sa = w.parentWidget()
        while sa is not None and not hasattr(sa, "ensureWidgetVisible"):
            sa = sa.parentWidget()
        if sa is not None:
            sa.ensureWidgetVisible(w, 0, 200)

    # ---- Export ------------------------------------------------------------------------------------------
    def export(self, win) -> None:
        win.show_tab("export")
        ex = win.ensure_export()
        self.spin(0.3, until=lambda: ex.flux is not None)
        ex.obscode.setText("ABCD")  # illustrative, never real observer values
        ex.site_name.setText("Example site")
        ex.eq["name"].setText("Example equipment")
        ex.remember.setChecked(False)
        ex._fields_changed()
        ex.zoom.set_index(2)
        self.shot("14_export_collect", "Export › Collect data: object name and the spectrum (Hα + telluric zoom) with "
                                       "Balmer/telluric markers; observer cards and header preview are below the fold")
        ex.show_step(1)
        self.shot("15_export_test_pass", "Export › Test: all AVSpec requirements pass, with 2 notes (airmass not "
                                         "computed: no coordinates/site; the standing first-submission note)")
        ex.site_name.setText("")
        ex._fields_changed()
        ex.show_step(1)
        self.shot("16_export_test_warning", "Export › Test: a warning (site nickname empty) and the standing note; "
                                            "the acknowledge tick now lives in the Publish AVSpec tile")
        ex.site_name.setText("Example site")
        ex.obscode.setText("")
        ex._fields_changed()
        ex.show_step(1)
        self.shot("17_export_test_error", "Export › Test: an error (obscode cleared, temporary session only)")
        ex.obscode.setText("ABCD")
        ex._fields_changed()
        ex.show_step(1)
        ex.show_step(2)
        self.shot("18_publish_nothing_saved", "Export › Publish: four tiles, nothing saved")
        ex.publish.save("avspec")
        self.spin(0.2)
        self.shot("19_publish_avspec_needs_tick", "Export › Publish: AVSpec blocked until the warning is read "
                                                  "(tick in the tile)")
        ex.ack_cb.setChecked(True)
        ex.publish.save("avspec")
        self.spin(0.0, until=lambda: ex.publish.tiles["avspec"].saved is not None)
        self.spin(0.25)
        self.shot("20_publish_avspec_confetti", "Export › Publish: AVSpec saved, confetti mid-burst")
        self.spin(1.2)
        for kind in ("fits", "csv", "png"):
            ex.publish.save(kind)
            self.spin(0.0, until=lambda k=kind: ex.publish.tiles[k].saved is not None)
        self.spin(1.3)  # let the last burst finish
        self.shot("21_publish_all_saved", "Export › Publish: all four tiles saved")

    # ---- settings, run all, cancel ---------------------------------------------------------------------
    def misc(self, win) -> None:
        from starex_reduce.ui.settings_dialog import SettingsDialog

        win.settings["masters_dir"] = r"D:\Spectra\masters"  # illustrative: never the user name in a temp path
        dlg = SettingsDialog(win.settings, win)
        dlg.show()
        self.spin(0.3)
        self.shot("22_settings_dialog", "Settings dialog (the dialog window alone, not the main window)", dlg)
        dlg.reject()
        from starex_reduce.ui.about import AboutDialog

        about = AboutDialog(win, animate=True)  # what the logo opens
        about.show()
        about.roll.offset = 200  # part way through the roll, the same frame every run
        about.roll.timer.stop()
        about.roll.update()
        self.spin(0.3)
        self.shot("22b_about", "About (logo click): splash image with the credit roll under it (frozen part "
                               "way through)", about)
        about.reject()
        win.open_step(0)
        win.run_all(force=True)
        self.spin(0.0, until=lambda: win.runner.is_busy("files"))
        self.spin(0.8)
        self.shot("23_run_all_in_progress", "Run all (Shift: every step) in progress: Files stacking, ribbon "
                                            "'Running…', progress in the step header")
        win.stop_current()
        self.spin(0.3, until=self.busy(*("files", "geometry", "extract", "wavelength", "response")))
        self.shot("24_run_cancelled", "The same run stopped (Esc): 'Files: stopped' in the log; the ribbon keeps "
                                      "the earlier valid stack (no error colour)")

    # ---- index -------------------------------------------------------------------------------------------
    def write_index(self) -> None:
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                    text=True, check=True).stdout.strip()
            dirty = subprocess.run(["git", "status", "--porcelain", "--", "starex_reduce"], cwd=ROOT,
                                   capture_output=True, text=True).stdout.strip()
            commit += " (+ uncommitted app changes)" if dirty else ""
        except (OSError, subprocess.CalledProcessError):
            commit = "unknown"
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = ["# Screenshots of the current build", "",
                 f"App commit {commit}, taken {when}, offscreen at {W}×{H}, data example_spectra/novasrg2026.", "",
                 "| File | What it shows | Commit | Date/time |", "|---|---|---|---|"]
        lines += [f"| {f} | {w} | {commit} | {when} |" for f, w in self.index]
        if self.failed:
            lines += ["", "Failed:", *[f"- {f}" for f in self.failed]]
        (self.out / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(out: Path) -> int:
    s = Shooter(out)
    s.setup()
    for part in (s.plan, s.nova):
        try:
            part()
        except Exception as exc:  # noqa: BLE001 - report it, shoot the rest, fail at the end
            s.failed.append(f"{part.__name__}: {type(exc).__name__}: {exc}")
            print("FAILED", part.__name__, exc)
    s.write_index()
    shutil.rmtree(s.tmp, ignore_errors=True)
    print(f"{len(s.index)} screenshots in {out}" + (f", {len(s.failed)} failed" if s.failed else ""))
    return 1 if s.failed else 0


if __name__ == "__main__":
    sys.exit(run(ROOT / "screenshots" / "latest"))
