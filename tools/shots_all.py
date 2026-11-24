"""Every screen of the current build, driven through the real UI code paths, saved as
screenshots/latest/NN_screen_state.png plus INDEX.md. Called by `python tools/screenshot.py --all`.

Runs offscreen at 1920x1080 in temporary sessions and a temporary settings folder: the example data in
example_spectra/novasrg2026 is only read, never written. Observer values are illustrative (ABCD).
Optional: STAREX_SHOTS_PAIR=<folder> with AGDra/ and HD145454/ sub-folders of lights and STAREX_SHOTS_MASTERS=<a
master library folder> (both read only, copied to the temporary session) add the Select shots of that pair.
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


def filter_sim(img):
    """What a red filter leaves: only the red channel of every pixel (a QImage, returned as a new one)."""
    import numpy as np
    from PySide6.QtGui import QImage

    im = img.convertToFormat(QImage.Format.Format_RGB32)
    a = np.frombuffer(im.constBits(), np.uint8).reshape(im.height(), im.bytesPerLine() // 4, 4).copy()
    a[..., 0] = 0  # blue
    a[..., 1] = 0  # green
    out = QImage(a.data, im.width(), im.height(), im.bytesPerLine(), QImage.Format.Format_RGB32)
    return out.copy()


class Shooter:
    def __init__(self, out: Path, palette: str = "default", filter_out: Path | None = None) -> None:
        self.out = out
        self.palette = palette
        self.filter_out = filter_out  # also save each shot through a simulated red filter here
        self.index: list[tuple[str, str]] = []
        self.failed: list[str] = []
        self.skipped: list[tuple[str, str]] = []

    def setup(self, keep: bool = False) -> None:
        """keep (--only): the other shots stay; otherwise the folder starts empty."""
        for d in (self.out, self.filter_out):
            if d is None:
                continue
            for old in ([] if keep else d.glob("*.png")):  # never the sub-folders of other palettes
                old.unlink()
            d.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        if sys.platform == "win32":  # the offscreen platform has no font database of its own on Windows
            os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
            # the offscreen platform keeps every font file of that folder open (about 400): give the C runtime
            # room for its own file streams (numpy writes FITS data through them; the default limit is 512)
            import ctypes

            try:
                ctypes.CDLL("ucrtbase")._setmaxstdio(8192)
            except (OSError, AttributeError):
                pass
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        self.tmp = Path(tempfile.mkdtemp(prefix="starex_shots_"))
        # the data root lives under %PUBLIC% so the paths shown in the shots carry no user name
        self.public = Path(os.environ.get("PUBLIC", str(self.tmp))) / "StarExtractorShots"
        shutil.rmtree(self.public, ignore_errors=True)
        self.root = self.public / "StarExtractorData"
        os.environ["STAREXTRACTOR_HOME"] = str(self.tmp / "home")
        from PySide6.QtWidgets import QApplication

        from starextractor.ui import theme

        self.app = QApplication.instance() or QApplication([])
        from starextractor.ui import retheme

        if theme.palette != self.palette:
            retheme.switch(self.palette)
        theme.apply(self.app, self.palette)

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
        from PySide6.QtCore import QCoreApplication, QEvent

        # no event loop runs here, so widgets replaced with deleteLater() (table cell chips) would stay on screen
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def shot(self, name: str, what: str, widget=None) -> None:
        w = widget if widget is not None else self.win
        self.spin(0.15)
        path = self.out / f"{name}.png"
        img = w.grab().toImage()
        if not img.save(str(path)):
            self.failed.append(name)
            return
        if self.filter_out is not None:
            filter_sim(img).save(str(self.filter_out / f"{name}.png"))
        self.index.append((path.name, what))
        print("  ", path.name)

    def shot_image(self, name: str, what: str, img) -> None:
        """Save an image rendered by the caller (exact pixel size, any device pixel ratio)."""
        path = self.out / f"{name}.png"
        if not img.save(str(path)):
            self.failed.append(name)
            return
        if self.filter_out is not None:
            filter_sim(img).save(str(self.filter_out / f"{name}.png"))
        self.index.append((path.name, what))
        print("  ", path.name)

    def splash(self) -> None:
        """The start-up splash (ui/splash.py), rendered at an exact device pixel ratio: this set's palette at 1x,
        the night palette (dimmed) and 2x scaling."""
        from PySide6.QtGui import QImage

        from starextractor import version
        from starextractor.ui import splash as sp

        def render(palette: str, ratio: float, status: str):
            s = sp.Splash(version(), palette, ratio=ratio)
            s.status = status
            img = QImage(round(s.width() * ratio), round(s.height() * ratio), QImage.Format.Format_RGB32)
            img.setDevicePixelRatio(ratio)
            s.render(img)
            s.deleteLater()
            return img

        self.shot_image("29_splash", f"Start-up splash, {self.palette} palette, 1x (version and status line drawn "
                        "over the art)", render(self.palette, 1.0, sp.STATUS_LOADING))
        if self.palette == "default":
            self.shot_image("29b_splash_night", "Start-up splash with the night palette saved: dimmed 60 %",
                            render("night", 1.0, sp.STATUS_WINDOW))
            self.shot_image("29c_splash_2x", "Start-up splash at 200 % display scaling (the 2x image, 1600 × 896)",
                            render("default", 2.0, sp.STATUS_LOADING))

    def about(self) -> None:
        """About: the credit roll at the start, the middle and the end of its loop, and the reduced-motion list
        (this set's palette; the night set shoots the middle frame only)."""
        from starextractor.ui.about import ROLL_H, AboutDialog

        frames = [("start", "the title entering at the bottom", 70)]
        if self.palette == "default":
            frames = [*frames, ("middle", "half way", None), ("end", "the last lines leaving at the top", -40)]
        else:
            frames = [("middle", "half way", None)]
        for i, (key, what, at) in enumerate(frames):
            dlg = AboutDialog(None, animate=True)
            roll = dlg.roll
            roll.timer.stop()
            loop = ROLL_H + roll.content_h
            roll.offset = loop / 2 if at is None else (at if at > 0 else loop + at)
            dlg.show()
            self.spin(0.2)
            self.shot(f"22{'cde'[i] if self.palette == 'default' else 'c'}_about_roll_{key}",
                      f"About: the credit roll at the {key} of its loop ({what})", dlg)
            dlg.close()
        if self.palette == "default":
            from starextractor.ui.about import CREDITS, LINE_H

            at = next(i for i, (st, t) in enumerate(CREDITS) if st == "label" and t == "Reference star list")
            dlg = AboutDialog(None, animate=True)
            dlg.roll.timer.stop()
            above = sum(LINE_H.get(st, 21) for st, _ in CREDITS[:at])
            dlg.roll.offset = ROLL_H + above - ROLL_H / 2 + 20  # the reference-star and target-lookup credits in view
            dlg.show()
            self.spin(0.2)
            self.shot("22g_about_reference_credit", "About: the credit roll at the Reference star list (our own "
                      "selection from SIMBAD, the AAVSO VSX and the WDS) and Target lookup (SIMBAD and VizieR)", dlg)
            dlg.close()
            dlg = AboutDialog(None, animate=False)
            dlg.show()
            self.spin(0.2)
            self.shot("22f_about_reduced_motion", "About with reduced motion: the same credits as a still, "
                      "scrollable list", dlg)
            dlg.close()

    def feedback_shots(self, win, dlg) -> None:
        """Help › User Feedback: empty, filled in, exactly what is sent, sent and failed. A fake transport answers;
        nothing ever reaches the live relay."""
        from starextractor.core import feedback as fb
        from starextractor.ui import dialogs as dl
        from starextractor.ui import feedback_tab as ft

        answers = [(200, b'{"ok": true, "ref": "FB-7K2M9Q"}'), OSError("offline"), OSError("offline")]
        real = (fb.urllib_transport, fb.RETRY_AFTER_S, ft.FeedbackTab.ask_save, dl.save_file)

        def fake(_url, _body, _headers, _timeout):
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

        fb.urllib_transport = fake
        fb.RETRY_AFTER_S = 0.0
        ft.FeedbackTab.ask_save = lambda self, r: True
        zip_path = self.public / "Documents" / fb.default_zip_name()
        dl.save_file = lambda *_a, **_k: str(zip_path)
        try:
            win.session.log("Wavelength: 5 lines, RMS 0.71 A (an example log line)")
            dlg.show_feedback()
            t = dlg.feedback
            self.spin(0.2)
            self.shot("34c_feedback_empty", "Help › User Feedback: type, message with counter, optional email, attach "
                      "the log, See exactly what is sent, Save as file / Send, the privacy notice", dlg)
            t.message.setPlainText("The Wavelength step found only four Balmer lines on my reference star tonight. "
                                   "Hδ was there in the profile but not marked. The log is attached.")
            t.email.setText("observer@example.org")
            self.spin(0.1)
            self.shot("34d_feedback_filled", "User Feedback filled in: a Problem with the log attached; Send is amber",
                      dlg)
            view = t.show_what_is_sent()
            self.spin(0.2)
            self.shot("34e_feedback_what_is_sent", "See exactly what is sent: the exact JSON payload (redacted log, "
                      "the website field always empty)", view)
            view.close()
            t.send()
            self.spin(0.2, until=lambda: not t.sending)
            self.shot("34f_feedback_sent", "Sent: the reference FB-XXXXXX to quote, Send off for 30 s", dlg)
            t.cooldown.stop()
            t.message.setPlainText("A second message while offline.")
            t.send()
            self.spin(0.2, until=lambda: not t.sending)
            self.shot("34g_feedback_failed", "Sending failed (offline): the message saved as a zip, with what to do "
                      "next", dlg)
        finally:
            fb.urllib_transport, fb.RETRY_AFTER_S, ft.FeedbackTab.ask_save, dl.save_file = real
            dlg.tabs.setCurrentIndex(0)

    def window(self, folder: Path, root: Path | None = None):
        """A window whose data root is `root` (default: the shared one); `folder` is only for the Plan's session."""
        from starextractor.core import datalocs
        from starextractor.core.session import Session
        from starextractor.ui.main_window import MainWindow

        win = MainWindow(session=Session.create(folder) if folder is not None else Session())
        win.settings["data_root"] = str(root or self.root)
        win.settings["data_paths"] = {}
        datalocs.create_missing(win.settings.paths())
        win._library = None
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
        from starextractor.core import simbad

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
                                               "nearest good and ok reference candidates with their Grade chips")
        self.plan_grades(win, plan)
        plan.use_selected(0)
        sa = plan.copy_target_btn
        while sa is not None and not hasattr(sa, "ensureWidgetVisible"):
            sa = sa.parentWidget()
        if sa is not None:
            sa.ensureWidgetVisible(plan.copy_ref_btn)
        self.shot("03_plan_observing_plan", "Plan: reference chosen; observing plan with target and reference side "
                                            "by side and the RA/Dec copy buttons")
        win.close()

    def plan_grades(self, win, plan) -> None:
        """The reference list around Sheliak: the nearer-poor hint, Include poor-grade stars (best tag), Faintest V
        6.0; then back to P Cyg with the default settings."""
        from starextractor.core import plan as pl

        old = pl.plan_state(win.session)["target"]
        pl.set_target(win.session, "Sheliak", 282.51998, 33.36278, "manual", None)
        plan.load_session()
        self.spin(0.2)
        card = plan.table.parentWidget()
        while card is not None and card.property("role") != "card":
            card = card.parentWidget()
        self.shot("02b_plan_poor_hint", "Plan around Sheliak: good and ok stars only; the line under the table says a "
                                        "closer star is graded poor (HD 174585) and why", card)
        plan.include_poor.setChecked(True)
        self.spin(0.2)
        self.shot("02c_plan_include_poor", "Include poor-grade stars on: poor stars in the same distance order, each "
                                           "with its grade; the nearest good-or-ok star tagged best", card)
        plan.include_poor.setChecked(False)
        plan.v_box.setValue(6.0)
        self.spin(0.2)
        self.shot("02d_plan_faintest_v", "Faintest V set to 6.0: the faint (6.0-6.5) stars are left out", card)
        plan.v_box.setValue(6.5)
        pl.set_target(win.session, old["name"], old["ra_deg"], old["dec_deg"], old["source"], None)
        plan.load_session()
        self.spin(0.2)

    def plan_conditions(self) -> None:
        """Plan › Observing conditions at McDonald Observatory (a public site) on 2026-11-20; "now" fixed inside the
        range so the shots repeat."""
        from PySide6.QtCore import QDate, QTime

        from starextractor.core import plan as pl

        win = self.window(self.tmp / "plan_cond" / "2026-11-20")
        win.settings["site"] = {"lat": 30.6714, "lon": -104.0225}
        win.settings["plan"] = {}
        win.show_tab("plan")
        page = win.ensure_plan()
        card = page.conditions

        def settle() -> None:
            self.spin(0.05)
            self.spin(0.1, until=card.idle)

        def window(a: str, b: str, day: tuple[int, int, int] = (2026, 11, 20)) -> None:
            card.date.setDate(QDate(*day))
            card.t_from.setTime(QTime.fromString(a, "HH:mm"))
            card.t_to.setTime(QTime.fromString(b, "HH:mm"))
            settle()

        def target(name: str, ra: float, dec: float) -> None:
            pl.set_target(win.session, name, ra, dec, "manual", None)
            page.load_session()
            settle()

        def now_at(hours_after_start: float) -> None:
            t = card.window().start.timestamp() + hours_after_start * 3600
            card.polar.set_now(t)
            card.chart.set_now(t)

        def show_card() -> None:
            sa = card.parentWidget()
            while sa is not None and not hasattr(sa, "ensureWidgetVisible"):
                sa = sa.parentWidget()
            if sa is not None:
                sa.ensureWidgetVisible(card, 0, 0)
            self.spin(0.1)

        settle()
        window("20:00", "06:00")
        show_card()
        self.shot("30c_plan_conditions_no_target", "Plan › Observing conditions with no target: empty rings and the "
                  "prompt in the polar plot; the Moon curve and twilight bands in the chart", card)
        target("Vega", 279.2347, 38.7837)
        now_at(0.25)
        self.shot("30f_plan_polar_moon", "Plan › polar sky plot: Vega's path (solid) with its 'sets' and 'current pos' "
                  "dots, the Moon's path (dashed) and its hollow 'moon now' ring, legend underneath", card.polar)
        self.shot("30_plan_conditions_sets", "Plan › Observing conditions: Vega sets below 25° during the window "
                  "(orange 'sets' dot), Moon curve, min altitude line, now line; McDonald Observatory, 2026-11-20",
                  card)
        show_card()
        self.shot("30a_plan_with_conditions", "Plan: the Observing conditions card between Target and Reference star "
                  "candidates")
        target("Polaris", 37.9546, 89.2641)
        self.shot("30d_plan_conditions_circumpolar", "Observing conditions: a circumpolar target (Polaris)", card)
        target("Capella", 79.1723, 45.9980)
        window("22:00", "04:00")
        self.shot("30e_plan_conditions_midnight", "Observing conditions: a window crossing midnight (22:00-04:00), "
                  "Capella transiting in it", card)
        win.settings["site"] = {}
        card.refresh_site()
        settle()
        self.shot("30b_plan_conditions_no_location", "Observing conditions with no location set: the prompt",
                  card)
        win.close()

    def planned_pairs(self) -> None:
        """Plan › Planned pairs (three pairs, one selected, one loaded in Acquisition), the Acquisition strip and the
        load confirmation."""
        from PySide6.QtCore import QDate, QTime

        from starextractor.core import plan as pl
        from starextractor.ui import dialogs

        win = self.window(self.tmp / "planned" / "2026-11-20")
        win.settings["site"] = {"lat": 30.6714, "lon": -104.0225}
        win.settings["plan"] = {}
        win.show_tab("plan")
        page = win.ensure_plan()
        card, pairs = page.conditions, page.pairs

        def settle() -> None:
            self.spin(0.05)
            self.spin(0.1, until=lambda: card.idle() and pairs.idle())

        card.date.setDate(QDate(2026, 11, 20))
        card.t_from.setTime(QTime(19, 0))
        card.t_to.setTime(QTime(5, 0))
        settle()
        for name, ra, dec, ref in (("V1405 Cas", 351.1592, 61.1525, 0), ("P Cyg", 304.4466, 38.0329, None),
                                   ("Vega", 279.2347, 38.7837, 1)):
            pl.set_target(win.session, name, ra, dec, "simbad" if name != "Vega" else "manual", None)
            page.load_session()
            if ref is not None:
                page.use_selected(ref)
            pairs.add_pair()
            settle()
        pairs.status.hide()
        pairs.load_for_acquisition(0)  # V1405 Cas: In Acquisition
        self.spin(0.3)
        acq = win.acquisition_page
        self.shot("32b_acquisition_planned_strip", "Acquisition after Plan › Load for acquisition: the strip 'Planned "
                  "pair: … (from Plan)' with Change in Plan; names and coordinates tagged 'from plan', folders "
                  "untouched")
        win.show_tab("plan")
        pairs.select(2)  # Vega selected: the editor and the sky plots show it
        settle()
        page.scroll.ensureWidgetVisible(pairs, 0, 40)
        self.spin(0.2)
        self.shot("32_plan_planned_pairs", "Plan › Planned pairs: three pairs, Vega selected (shown in the editor and "
                  "the plots), V1405 Cas marked In Acquisition, P Cyg still needs a reference")
        self.shot("32a_planned_pairs_card", "The Planned pairs card on its own", pairs)
        # the question asked when Acquisition holds subs of another target (the text the tab builds)
        asked: list[str] = []
        acq.records.extend(type("Sub", (), {})() for _ in range(41))  # stand-ins: only the count is read first
        acq.load_planned_pair(pairs.pairs[1], ask=lambda t: asked.append(t) or False)
        acq.records.clear()
        box = dialogs.message(win, "Load planned pair", asked[0] if asked else "")
        box.show()
        self.spin(0.2)
        self.shot("32c_load_confirmation", "Load for acquisition when Acquisition holds subs of another target: the "
                  "confirmation (No changes nothing; Yes pauses watching, runs Clear all and empties the folders)", box)
        box.close()
        win.close()

    def dialogs(self) -> None:
        """The app-styled dialogs: the folder dialog in Directory mode (FITS files greyed, our toolbar icons) in the
        three palettes, Open and Save, a message box, an input dialog, and the Help me window."""
        from PySide6.QtCore import QUrl
        from PySide6.QtWidgets import QInputDialog, QLineEdit, QTreeView

        from starextractor.ui import dialogs, retheme, theme

        capture = self.public / "Capture" / "2026-11-20" / "V1405Cas"
        (capture / "darks").mkdir(parents=True, exist_ok=True)
        for k in range(1, 6):
            (capture / f"V1405Cas_{k:04d}.fits").write_bytes(b"\0" * 2880)
        (capture / "capture_log.txt").write_text("SharpCap log", encoding="utf-8")

        def folder_shot(name: str, what: str) -> None:
            dlg = dialogs.file_dialog(None, "Target folder to watch", str(capture), mode="directory")
            dlg.setSidebarUrls([QUrl.fromLocalFile(str(self.public)), QUrl.fromLocalFile(str(capture.parent))])
            dlg.resize(900, 560)
            dlg.show()
            view = dlg.findChild(QTreeView, "treeView")
            self.spin(0.2, until=lambda: view.model().rowCount(view.rootIndex()) >= 6)
            view.setFocus()
            self.shot(name, what, dlg)
            dlg.close()

        palettes = ("default", "night", "red") if self.palette == "default" else (self.palette,)
        for pal in palettes:
            if theme.palette != pal:
                retheme.switch(pal)
            folder_shot(f"33_folder_dialog_{pal}", f"Browse… for a capture folder ({pal} palette): Directory mode with "
                        "the folder's FITS files greyed (not selectable), our toolbar icons, sidebar and header in "
                        "panel2, list in bg, fields in input")
        if theme.palette != self.palette:
            retheme.switch(self.palette)
        for mode, title, flt in (("open", "Target stack", "FITS (*.fits *.fit *.fts)"),
                                 ("save", "Save observing plan", "JSON (*.json)")):
            dlg = dialogs.file_dialog(None, title, str(capture), flt, mode=mode)
            dlg.setSidebarUrls([QUrl.fromLocalFile(str(self.public)), QUrl.fromLocalFile(str(capture.parent))])
            dlg.resize(900, 560)
            dlg.show()
            self.spin(0.4)
            for e in dlg.findChildren(QLineEdit):
                e.clearFocus()
            self.shot(f"33a_{mode}_dialog", f"A file dialog in {mode.title()} mode, app-styled", dlg)
            dlg.close()
        box = dialogs.message(None, "Promote to Published", "Promote V1405 Cas (2026-11-20) as version 2? The "
                              "AVSpec file and its log are copied to Published.")
        box.show()
        self.spin(0.2)
        self.shot("33b_message_box", "A question (QMessageBox), app-styled", box)
        box.close()
        inp = QInputDialog()
        inp.setWindowTitle("Add a target line")
        inp.setLabelText("Wavelength (Å) of the line to add:")
        inp.setTextValue("4686")
        dialogs.style_dialog(inp)
        inp.resize(420, 150)
        inp.show()
        self.spin(0.2)
        self.shot("33c_input_dialog", "An input dialog (QInputDialog), app-styled", inp)
        inp.close()
        win = self.window(self.tmp / "help" / "2026-11-20")
        dlg = win.open_help("Planned pairs")
        self.spin(0.3)
        self.shot("34_help_me", "Help me (top row, F1): the workflow guide with its contents and find field, open at "
                  "Planned pairs", dlg)
        self.shot("34a_topbar_help", "The top row with the Help me button", win.topbar)
        dlg.show_section("Where the reference stars come from")
        self.spin(0.3)
        self.shot("34b_help_reference_stars", "Help me at Where the reference stars come from: sources and dates, the "
                  "stars listed, the rules, grades, coverage and limits", dlg)
        self.feedback_shots(win, dlg)
        dlg.close()
        win.close()

    def plan_layout(self) -> None:
        """The Plan tab's layout (2026-11-21): 1280x800 with three planned pairs (In Acquisition, Ready, pick
        reference), the window's minimum size, and many pairs with the fixed-height plan view scrolling."""
        from PySide6.QtCore import QDate, QTime

        from starextractor.core import plan as pl

        win = self.window(self.tmp / "layout" / "2026-11-20")
        win.settings["site"] = {"lat": 30.6714, "lon": -104.0225}  # McDonald Observatory
        win.settings["plan"] = {}
        win.show_tab("plan")
        page = win.ensure_plan()
        card, pairs = page.conditions, page.pairs

        def settle() -> None:
            self.spin(0.05)
            self.spin(0.1, until=lambda: card.idle() and pairs.idle())

        card.date.setDate(QDate(2026, 11, 20))
        card.t_from.setTime(QTime(19, 0))
        card.t_to.setTime(QTime(5, 0))
        settle()
        stars = [("bet Cas", 2.2945, 59.1498, True), ("V1405 Cas", 351.1592, 61.1525, True),
                 ("P Cyg", 304.4466, 38.0329, False)]
        for name, ra, dec, ref in stars:
            pl.set_target(win.session, name, ra, dec, "simbad", None)
            page.load_session()
            if ref:
                page.use_selected(0)
            pairs.add_pair()
            settle()
        pairs.load_for_acquisition(0)  # bet Cas: In Acquisition; V1405 Cas: Ready; P Cyg: pick reference
        win.show_tab("plan")
        pairs.select(0)
        settle()
        win.resize(1280, 800)
        settle()
        page.scroll.verticalScrollBar().setValue(0)
        self.shot("35_plan_layout_1280", f"Plan at 1280x800 ({self.palette} palette): the title across the page, "
                  "then two equal columns that start and end level (Target and Reference candidates | Observing "
                  "conditions, Sky and Altitude), Planned pairs and the Observing plan; pairs In Acquisition, Ready "
                  "and pick reference; equal side margins, the scrollbar in the right margin")
        self.shot("35a_plan_page_1280", "The whole Plan page at 1280x800 (scrolled content)", page.scroll.widget())
        win.resize(win.minimumWidth(), win.minimumHeight())
        settle()
        self.shot("35b_plan_layout_min", f"Plan at the minimum window size ({win.width()}x{win.height()}, "
                  f"{self.palette} palette): the columns stay equal and level, field rows wrap, nothing is clipped, "
                  "the scrollbar in the right margin")
        self.shot("35c_plan_page_min", "The whole Plan page at the minimum window size", page.scroll.widget())
        win.resize(1280, 800)
        if self.palette == "default":
            for k in range(9):
                name, ra, dec, ref = stars[k % 3]
                pl.set_target(win.session, f"{name} {k + 2}", (ra + 7 * (k + 1)) % 360, dec, "simbad", None)
                page.load_session()
                if ref:
                    page.use_selected(0)
                pairs.add_pair()
            settle()
            plan_card = page.plan_box.parentWidget()
            page.scroll.ensureWidgetVisible(plan_card, 0, 0)
            self.spin(0.2)
            self.shot("35d_plan_many_pairs", "Plan with twelve planned pairs: the Observing plan view keeps its "
                      "fixed height (nine lines) and scrolls on its own", plan_card)
        win.close()

    # ---- the nova night ----------------------------------------------------------------------------------
    def nova(self) -> None:
        win = self.window(None)
        win.show_tab("acquisition")
        a = win.ensure_acquisition()
        a.folders["target"].setText(str(NOVA / "SgrNov32026"))
        a.names["target"].setText("V8055 Sgr")
        a.folders["reference"].setText(str(NOVA / "hd163955"))
        a.names["reference"].setText("HD 163955")
        a.enqueue(sorted((NOVA / "SgrNov32026").glob("*.fits")))
        self.spin(0.2, until=lambda: not a.queue and not a.busy)
        a.collect.set_index(1)
        a.enqueue(sorted((NOVA / "hd163955").glob("*.fits")))
        self.spin(0.2, until=lambda: not a.queue and not a.busy)
        a.collect.set_index(0)
        a.view_mode.set_index(0)
        self.shot("04_acquisition_target_set", "Acquisition › Target tab, top: source box (folder, watching, name, "
                                               "coordinates), Sub card (ADU profile), Recommendation, Good subs")
        a.scroll.ensureWidgetVisible(a.logbook, 0, 40)
        self.shot("04b_acquisition_snr_logbook", "Acquisition › Target tab scrolled down: Stack SNR and Logbook notes")
        a.scroll.verticalScrollBar().setValue(0)
        a.collect.set_index(1)
        self.shot("05_acquisition_reference_set", "Acquisition › Reference tab (HD 163955, 10 subs): its own folder, "
                                                  "name and rows")
        a.collect.set_index(0)
        a.view_mode.set_index(1)
        self.spin(0.1, until=lambda: a._shown_view is not None)
        a.row_fields["half"].setValue(a.row_fields["half"].value() + 3)
        a.row_fields["gap"].setValue(a.row_fields["gap"].value() + 5)
        a._rows_edited()
        self.spin(0.3, until=lambda: not a.queue and not a.busy and a.rows_by["target"].locked_from == "edited")
        self.spin(0.5)
        self.shot("06_acquisition_rows_edited", "Acquisition: target rows edited by hand (±rows +3, sky gap +5), "
                                                "set re-analysed, sub image with the new bands")
        # Pre Processing settings: masters from the nova darks and bias, then Stack selected
        a.collect.open_settings()
        prep = a.prep
        prep.create_card.bins["dark"].set_folder(str(NOVA / "darks"))
        prep.create_card.bins["bias"].set_folder(str(NOVA / "bias"))
        prep.create_card.create_masters(["bias"])
        self.spin(0.3, until=self.busy("masters"))
        prep.create_card.create_masters(["dark"])
        self.spin(0.3, until=self.busy("masters"))
        a.scroll.verticalScrollBar().setValue(0)
        self.shot("06b_acq_preprocessing_top", "Acquisition › Pre Processing settings: library and project folders, "
                                               "Calibration frames (the shared master library), Create masters")
        a.scroll.ensureWidgetVisible(prep.cal_lines.parentWidget(), 0, 300)
        self.shot("06c_acq_preprocessing_stacking", "Acquisition › Pre Processing settings scrolled to Stacking "
                                                    "(method, rejection, dark/bias pickers, options)")
        a.collect.set_index(0)
        a.view_mode.set_index(0)
        t0 = time.perf_counter()
        assert a.stack_selected(), a.stack_result.text()
        self.spin(0.3, until=self.busy("acq.stack"))
        print(f"   Acquisition stack of {len(a._good('target'))} target subs in {time.perf_counter() - t0:.2f} s")
        a.scroll.ensureWidgetVisible(a.stack_result, 0, 200)
        self.shot("06d_acq_stack_selected", "Acquisition › Target: Stack selected done; the result line shows the "
                                            "real calibration and method")
        a.scroll.verticalScrollBar().setValue(0)

        a.collect.set_index(1)  # the reference set too
        assert a.stack_selected(), a.stack_result.text()
        self.spin(0.3, until=self.busy("acq.stack"))
        a.collect.set_index(0)
        a.update_handoff()
        a.scroll.verticalScrollBar().setValue(0)
        self.shot("06e_acq_handoff_strip", "Acquisition after stacking both sets: the strip \"Both stacks are ready.\" "
                                           "with Open in Reduce and Not now")

        # Reduce: Select picks the two stacks
        win.open_step(0)
        files = win.files_screen
        self.files_shots(win, files, "V8055 Sgr + HD 163955")

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

    def select_missing_shots(self, win, files) -> None:
        """Use Acquisition stacks with the record emptied (the labelled fallback offer) and with only the target
        recorded; the record is put back afterwards."""
        acq = dict(win.settings["acq"] or {})
        saved = dict(acq.get("last_stacks") or {})
        try:
            win.settings["acq"] = {**acq, "last_stacks": {}}
            files.use_acquisition()
            self.shot("07b_select_nothing_stacked", "Reduce › Select › Use Acquisition stacks with nothing recorded: "
                                                    "\"Nothing stacked in Acquisition yet.\" and the offer from the "
                                                    "Stacks folder (night labelled) with Use these / Cancel")
            files.cancel_offer()
            win.settings["acq"] = {**acq, "last_stacks": {"target": saved["target"]}}
            files.use_acquisition()
            self.spin(0.3, until=self.busy("preview.files.target"))
            self.shot("07c_select_only_target", "Reduce › Select › Use Acquisition stacks with only the target "
                                                "stacked: target filled, \"Reference not stacked yet…\"")
        finally:
            win.settings["acq"] = {**acq, "last_stacks": saved}
        for c in files.cards.values():
            c.set_file(None)

    def files_shots(self, win, files, pair: str, prefix: str = "") -> None:
        """Select empty, loaded from the Acquisition stacks, with an old stack, then Load pair (chevron done)."""
        from starextractor.core.fitsio import read_header, read_image, write_float32
        from starextractor.core.stack_keys import strip_sx

        for c in files.cards.values():
            c.set_file(None)
        files.message.setText("")
        files.notes.setPlainText("")
        self.shot(f"07{prefix}_select_empty", "Reduce › Select: nothing picked yet (two stack cards, Checks, Load "
                                             "pair, night queue)")
        if not prefix:
            self.select_missing_shots(win, files)
        files.use_acquisition()
        self.spin(0.3, until=self.busy("preview.files.target", "preview.files.reference"))
        self.shot(f"08{prefix}_select_loaded", f"Reduce › Select: {pair} from the Acquisition stacks (Use Acquisition "
                                              "stacks); rows read from the file header, previews, all checks")
        old = self.tmp / f"old_stack{prefix}.fits"  # what another program writes: pixels, no name/rows/camera
        stack = files.cards["target"].info.path
        h = {k: v for k, v in strip_sx(read_header(stack)).items() if k in ("DATE-OBS", "DATE-END", "EXPTIME")}
        write_float32(old, read_image(stack), h)
        files.cards["target"].set_file(old)
        files.load_pair()
        self.shot(f"08b{prefix}_select_old_stack", "Reduce › Select: an old stack (no name, rows or camera keywords): "
                                                  "✗ for the name, warnings for rows and camera; Load refused")
        files.use_acquisition()
        self.spin(0.3, until=self.busy("preview.files.target", "preview.files.reference"))
        assert files.load_pair(), files.message.text()
        win.refresh_ribbon()
        self.shot(f"09{prefix}_select_pair_loaded", f"Reduce › Select after Load pair: the Select step shows {pair} "
                                                   "(done), ✓ Pair loaded. Next: Geometry.")
        win.open_step(1)
        self.spin(0.3, until=self.busy("view.geometry.Target", "view.geometry.Reference"))
        self.shot(f"09c{prefix}_select_step_done", f"The next step open: the Select step is done (green) with "
                                                     f"{pair}; the rest of the rail as before")
        win.open_step(0)

    def donut(self) -> None:
        """A defocused (double-lobed) synthetic sub: Acquisition before (single peak) and after (both lobes), the
        ADU profile, and 1D Extract on the same profile."""
        import numpy as np

        from starextractor.core.session import Session
        from starextractor.synth import Donut, make_donut, write_donut

        sub = write_donut(self.tmp / "donut" / "Donut_00001.fits")
        win = self.window(None, self.public / "DonutData")
        win.show_tab("acquisition")
        a = win.ensure_acquisition()
        a.lobe_mode.setCurrentIndex(a.lobe_mode.findData("single"))
        a.names["target"].setText("Donut")
        a.enqueue([sub])
        self.spin(0.2, until=lambda: not a.queue and not a.busy)
        a.view_mode.set_index(1)
        self.spin(0.3, until=lambda: a._shown_view is not None)
        a._img_zoom("bands")
        self.shot("06e_acq_donut_before", "Acquisition › Sub image, defocused (double-lobed) synthetic sub with "
                                          "Single peak only: the band covers the stronger lobe only")
        a.lobe_mode.setCurrentIndex(a.lobe_mode.findData("auto"))
        a.redetect()
        self.spin(0.3, until=lambda: not a.busy and a.rows_by["target"].shape == "double-lobed")
        self.spin(0.5)
        a._img_zoom("bands")
        a.scroll.ensureWidgetVisible(a.lobe_chip, 0, 20)  # the note and the lobe-mode list under the sub image
        self.shot("06f_acq_donut_after", "Acquisition › Sub image, the same sub with Auto: the blue spectrum band "
                                         "spans both lobes, skies clear, the double-lobed note shown")
        a.view_mode.set_index(0)
        self.spin(0.5)
        self.shot("06g_acq_donut_adu", "Acquisition › ADU profile of the double-lobed sub (both lobes inside the band)")
        win.close()
        # 1D Extract on the same profile (a pair folder with the donut as the geometry-corrected stack)
        folder = self.public / "DonutData" / "Processed" / "2025-10-02_Donut__Ref"
        sess = Session.create(folder)
        from starextractor.core.fitsio import write_float32

        img = make_donut(Donut(), seed=1)
        for name in ("Target", "Reference"):
            for suffix in ("S", "G"):
                write_float32(folder / "work" / f"{name}_{suffix}.fit", img, {"OBJECT": name, "GEO_TILT": 0.0})
        sess.mark_done("files", {"Target": "work/Target_S.fit", "Reference": "work/Reference_S.fit"}, "pair")
        sess.mark_done("geometry", {"Target": "work/Target_G.fit", "Reference": "work/Reference_G.fit"}, "tilt 0")
        from starextractor.ui.main_window import MainWindow

        win = MainWindow(session=sess)
        win.resize(W, H)
        win.show()
        self.win = win
        win.open_step(2)
        es = win.ensure_screen("extract")
        self.spin(0.3, until=lambda: es.viewer.current is not None)
        es.zoom_chips.set_index(1)
        es.auto_place()
        self.spin(0.3, until=self.busy("auto.extract.Target"))
        self.spin(0.5)
        del np
        self.shot("11b_extract_donut", "Reduce › 1D Extract on the double-lobed profile: one band over both lobes, "
                                       "the double-lobed note in the Bands card")
        win.close()

    def library(self) -> None:
        """The Library tab on the synthetic multi-night data root (synth.write_library)."""
        from starextractor.core import datalocs
        from starextractor.core.datalocs import to_stored
        from starextractor.synth import write_library

        root = self.public / "LibraryData"
        made = write_library(root)
        win = self.window(None, root)
        win.show_tab("library")
        lp = win.library_page
        self.spin(0.2, until=lambda: lp.library is not None and not win.runner.is_busy("library.scan"))

        def select(key: str) -> None:
            i = lp.model.index_of(key)
            lp.table.selectRow(i)
            lp._row_selected(i)
            lp.table.scrollTo(lp.model.index(i, 0))
            self.spin(0.6, until=lambda: not any(win.runner.is_busy(f"library.thumb.{k}") for k in range(1, 30)))

        select("2025-09-26_AGDra__HD145454")
        self.shot("25_library_reduced", "Library: every night and pair; a reduced pair selected (progress report, "
                                        "spectrum preview, notes)")
        select("2025-09-27_RSOph__HD161868")
        self.shot("25b_library_stale_warning", "Library: a pair with stale steps and an unacknowledged QC warning "
                                               "(Needs attention)")
        select(to_stored(made["stack_only_target"], root))
        self.shot("25c_library_unpaired_stack", "Library: a target stack with no reference, the Pick a reference "
                                                "list and Create pair and open in Reduce")
        win.close()
        empty = self.public / "EmptyData"
        win = self.window(None, empty)
        datalocs.create_missing(win.settings.paths())
        win.show_tab("library")
        self.spin(0.5, until=lambda: not win.runner.is_busy("library.scan"))
        self.shot("25d_library_empty", "Library with no pairs yet: the empty-state message")
        win.settings["data_root"] = r"Q:\StarExtractorData"  # a drive that is not there
        win.library_page.request_scan()
        self.spin(0.3)
        self.shot("25e_library_missing_root", "Library when the data root is missing: the message and the button "
                                              "to Settings › Data locations")
        win.close()

    def rail(self) -> None:
        """The workflow rail in a few states (a session whose first steps are marked done)."""
        from starextractor.core.session import Session
        from starextractor.ui.main_window import MainWindow

        s = Session.create(self.tmp / "rail" / "pair")
        for sid in ("files", "geometry", "extract"):
            s.mark_done(sid, {}, {"files": "AG Dra + HD 145454", "geometry": "Tilt -0.23° · hot pixels fixed",
                                  "extract": "2 profiles · sky subtracted"}[sid])
        win = MainWindow(session=s)
        win.settings["data_root"] = str(self.root)
        win.resize(W, H)
        win.show()
        self.win = win
        self.spin(0.3)
        win.open_step(3)
        self.spin(0.6)
        self.shot("26_rail_wavelength_current", "The workflow rail: Reduce › Wavelength current (orange), Select, "
                                                "Geometry and 1D Extract done, 3 of 5 done")
        s.mark_missing("geometry", "Missing data: the stacks moved")
        win.open_step(1)
        win.refresh_ribbon()
        self.spin(0.4)
        self.shot("26b_rail_missing_data", "The rail with a step that has missing data (red outline) while it is "
                                           "the open step")
        win.resize(1100, H)
        win.open_step(3)
        self.spin(0.6)
        self.shot("26c_rail_narrow", "The rail at 1100 px: short labels (Extract, Capture), the rest elided with "
                                     "tooltips; nothing overlaps")
        win.close()

    def _cal_window(self, folder: Path):
        from starextractor.core.session import Session
        from starextractor.ui.main_window import MainWindow

        win = MainWindow(session=Session.load(folder))
        win.settings["data_root"] = str(self.root)
        win.resize(W, H)
        win.show()
        self.win = win
        win.open_step(3)
        ws = win.ensure_screen("wavelength")
        self.spin(0.3, until=lambda: ws.result is not None and (ws.result.get("target") or {}).get("cal") is not None,
                  timeout=60)
        self.spin(0.3)
        return win, ws

    def _cal_shots(self, ws, name: str, what: str, both: bool = True) -> None:
        sa = ws.cal_card
        while sa is not None and not hasattr(sa, "ensureWidgetVisible"):
            sa = sa.parentWidget()
        if sa is not None:
            sa.ensureWidgetVisible(ws.cal_card.table, 0, 400)
        self.spin(0.2)
        self.shot(name, what)
        if both and sa is not None:
            sa.ensureWidgetVisible(ws.cal_card.delta_plot, 0, 40)
            self.spin(0.2)
            self.shot(name + "_delta", what.split(":")[0] + ": the difference between the target and reference "
                                                             "solutions, its verdict and the reference's own sky check")

    def _synthetic_pair(self, name: str, **kw) -> Path:
        import numpy as np

        from starextractor.core import logbook
        from starextractor.core.fitsio import write_float32
        from starextractor.core.session import Session
        from starextractor.synth import WAVE_REF, wave_case

        c = wave_case(**kw)
        s = Session.create(self.tmp / "cal" / name)
        rows = {"EXT_SAT": 0, "EXT_SAB": 11, "EXT_SBT": 12, "EXT_SBB": 23}
        hdr = {"CRPIX1": 1, "CRVAL1": 1.0, "CDELT1": 1.0, "CTYPE1": "PIXEL", "BUNIT": "ADU", **rows}
        write_float32(s.folder / "Reference_X.fit", c.ref_prof.astype(np.float32), {**hdr, "OBJECT": "ref"})
        write_float32(s.folder / "Target_X.fit", c.tgt_prof.astype(np.float32), {**hdr, "OBJECT": "tgt"})
        (s.folder / "work").mkdir(exist_ok=True)
        write_float32(s.folder / "work" / "Target_G.fit", c.tgt_sky, {"OBJECT": "tgt"})
        write_float32(s.folder / "work" / "Reference_G.fit", c.ref_sky, {"OBJECT": "ref"})
        logbook.save(s.folder, logbook.empty(s.folder.name, "2026-11-19"))
        s.params["wavelength"] = {"saved": list(WAVE_REF)}
        for stage in ("files", "geometry", "extract"):
            s.mark_done(stage, {}, "done")
        s.save()
        return s.folder

    def target_cal(self) -> None:
        """Wavelength › Target calibration: the real nova pair (a copy), a synthetic scale, a clean sky and
        stellar lines only."""
        real = NOVA / "2026-11-16"
        if (real / "Target_G.fit").exists():
            pair = self.tmp / "cal" / "nova_pair"
            shutil.copytree(real, pair)
            (pair / "work").mkdir(exist_ok=True)
            for f in ("Target_G.fit", "Reference_G.fit"):
                if not (pair / "work" / f).exists():
                    shutil.copy(pair / f, pair / "work" / f)
            win, ws = self._cal_window(pair)
            self._cal_shots(ws, "28_target_cal_real", "Wavelength › Target calibration on the real novasrg2026 pair: "
                                                      "sky spectrum with the identified lines, anchors, residuals, "
                                                      "summary and chips")
            win.close()
        else:
            self.skipped.append(("28_target_cal_real", "example_spectra/novasrg2026/2026-11-16 not present"))
        for name, what, kw in (
                ("28b_target_cal_scale", "Target calibration, synthetic: shift 13.5 px and a 0.3 % scale (Auto "
                                         "accepts the scale)", {"shift": 13.5, "scale": 0.003, "seed": 1}),
                ("28c_target_cal_clean_sky", "Target calibration, synthetic clean sky: no sky lines, telluric and "
                                             "stellar anchors only", {"shift": 8.0, "sky_lines": False, "seed": 2}),
                ("28d_target_cal_stellar_only", "Target calibration, synthetic: stellar lines only, velocity not "
                                                "determined (amber note)",
                 {"shift": 5.0, "v_kms": 80.0, "sky_lines": False, "tellurics": False, "seed": 7})):
            win, ws = self._cal_window(self._synthetic_pair(name, **kw))
            self._cal_shots(ws, name, what, both=name.endswith("scale"))
            win.close()

    def palettes(self) -> None:
        """The top row with the night switch off and on, and one page in the red palette."""
        from starextractor.ui import retheme, theme

        win = self.window(self.tmp / "pal" / "2026-08-20")
        win.resize(W, H)
        win.show()
        self.win = win
        self.spin(0.4)
        bar = win.topbar
        self.shot("27_topbar_night_off", "The top row: Plan, Library, the pair chip, the night switch (off) and "
                                         "settings; Run all hidden for now (ui.show_run_all)", bar)
        win.set_night(True)
        self.spin(0.3)
        self.shot("27b_topbar_night_on", "The top row with the night switch on (night palette)", bar)
        retheme.switch("red")
        win.go_to("wavelength")
        self.spin(0.5)
        self.shot("27c_red_palette_wavelength", "Reduce › Wavelength in the red palette (settings ui.palette = "
                                                "\"red\": only the red channel)")
        retheme.switch("default")
        win.set_night(False)
        self.spin(0.2)
        assert theme.palette == "default"
        win.close()

    def pair_shots(self) -> None:
        """Optional (env STAREX_SHOTS_PAIR / STAREX_SHOTS_MASTERS): the AG Dra + HD 145454 pair stacked in
        Acquisition, then picked in Select."""
        src, lib = os.environ.get("STAREX_SHOTS_PAIR"), os.environ.get("STAREX_SHOTS_MASTERS")
        if not src or not lib or not Path(src).is_dir() or not Path(lib).is_dir():
            self.skipped.append(("07a-09a", "set STAREX_SHOTS_PAIR and STAREX_SHOTS_MASTERS"))
            return
        win = self.window(None, self.public / "PairData")
        shutil.copytree(lib, self.tmp / "pair_masters")
        win.set_masters_dir(str(self.tmp / "pair_masters"))
        win.show_tab("acquisition")
        a = win.ensure_acquisition()
        sets = (("target", "AGDra", "AG Dra"), ("reference", "HD145454", "HD 145454"))
        for idx, (role, sub, name) in enumerate(sets):
            a.collect.set_index(idx)
            files = sorted(Path(src, sub).rglob("*.fits"))
            a.folders[role].setText(str(files[0].parent))
            a.names[role].setText(name)
            a.enqueue(files)
            self.spin(0.2, until=lambda: not a.queue and not a.busy)
            t0 = time.perf_counter()
            assert a.stack_selected(), a.stack_result.text()
            self.spin(0.3, until=self.busy("acq.stack"))
            print(f"   {name}: stacked {len(a._good(role))} subs in {time.perf_counter() - t0:.2f} s")
        win.show_tab("reduce")
        win.open_step(0)
        self.files_shots(win, win.files_screen, "AG Dra + HD 145454", "a")
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
        from starextractor.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(win.settings, win)
        dlg.show()
        self.spin(0.3)
        self.shot("22_settings_dialog", "Settings › Data locations: the data root and Stacks, Masters, Processed, "
                                        "Published, all ready (✓)", dlg)
        dlg.locations.fields["published"].setText(r"Q:\StarExtractorData\Published")  # a drive that is not there
        self.spin(0.2)
        self.shot("22a_settings_path_missing", "Settings › Data locations with Published on a missing drive: ✗ and "
                                               "Create missing folders", dlg)
        dlg.reject()
        from starextractor.core.settings import Settings
        from starextractor.ui.data_locations import FirstRunDialog

        first = FirstRunDialog(Settings(self.tmp / "first_run.json"), win)
        first.show()
        self.spin(0.3)
        self.shot("22c_first_run_data_root", "First start: the data root dialog with the suggested folder and the "
                                             "layout it creates", first)
        first.reject()
        from starextractor.ui.about import AboutDialog

        about = AboutDialog(win, animate=True)  # what the logo opens
        about.show()
        about.roll.offset = 200  # part way through the roll, the same frame every run
        about.roll.timer.stop()
        about.roll.update()
        self.spin(0.3)
        self.shot("22b_about", "About (logo click): splash image with the credit roll under it (frozen part "
                               "way through)", about)
        about.reject()
        win.open_step(1)
        win.run_all(force=True)
        self.spin(0.0, until=lambda: win.runner.is_busy("geometry"))
        self.spin(0.3)
        self.shot("23_run_all_in_progress", "Run all (Shift: every step) in progress: Select skipped (the pair is "
                                            "loaded), Geometry running, 'Running…' in the rail tooltip, "
                                            "progress in the header")
        win.stop_current()
        self.spin(0.3, until=self.busy(*("files", "geometry", "extract", "wavelength", "response")))
        self.shot("24_run_cancelled", "The same run stopped (Esc): 'Geometry: stopped' in the log; the rail "
                                      "keeps the loaded pair (no error colour)")

    # ---- index -------------------------------------------------------------------------------------------
    def merge_index(self) -> None:
        """--only: replace or add the rows of the shots taken; every other row stays."""
        path = self.out / "INDEX.md"
        old = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        if not old:
            self.write_index()
            return
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                    text=True, check=True).stdout.strip() + " (+ uncommitted app changes)"
        except (OSError, subprocess.CalledProcessError):
            commit = "unknown"
        when = datetime.now().strftime("%Y-%m-%d %H:%M")
        new = {f: f"| {f} | {w} | {commit} | {when} |" for f, w in self.index}
        out = []
        for line in old:
            f = line.split("|")[1].strip() if line.startswith("| ") else ""
            out.append(new.pop(f) if f in new else line)
        rows = [i for i, line in enumerate(out)
                if line.startswith("| ") and line.split("|")[1].strip().endswith(".png")]
        at = (rows[-1] + 1) if rows else len(out)
        out[at:at] = sorted(new.values())
        path.write_text("\n".join(out) + "\n", encoding="utf-8")

    def write_index(self) -> None:
        try:
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                    text=True, check=True).stdout.strip()
            dirty = subprocess.run(["git", "status", "--porcelain", "--", "starextractor"], cwd=ROOT,
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


def run(out: Path, palette: str = "default", filter_out: Path | None = None, only: list[str] | None = None) -> int:
    s = Shooter(out, palette, filter_out)
    s.setup(keep=bool(only))
    parts = [s.splash, s.about, s.plan, s.plan_conditions, s.planned_pairs, s.plan_layout, s.dialogs, s.nova,
             s.rail, s.target_cal, s.donut, s.library, s.pair_shots]
    if palette == "default":
        parts.append(s.palettes)
    if only:
        parts = [p for p in parts if p.__name__ in only]
    for part in parts:
        try:
            part()
        except Exception as exc:  # noqa: BLE001 - report it, shoot the rest, fail at the end
            s.failed.append(f"{part.__name__}: {type(exc).__name__}: {exc}")
            print("FAILED", part.__name__, exc)
    if only:
        s.merge_index()
    else:
        s.write_index()
    shutil.rmtree(s.tmp, ignore_errors=True)
    shutil.rmtree(s.public, ignore_errors=True)
    print(f"{len(s.index)} screenshots in {out}" + (f", {len(s.failed)} failed" if s.failed else ""))
    return 1 if s.failed else 0


def run_all(out: Path) -> int:
    """The default set in `out`, the night set in out/night and the same night shots through a simulated red filter
    in out/night_filter (one run for both)."""
    rc = run(out)
    rc |= run(out / "night", "night", out / "night_filter")
    from starextractor.ui import retheme

    retheme.switch("default")
    return rc


if __name__ == "__main__":
    sys.exit(run_all(ROOT / "screenshots" / "latest"))
