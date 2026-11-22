"""Entry point: `starextractor` / `python -m starextractor.app`.

Options: `--session <folder>` opens a session; `--no-splash` starts without the splash; `--startup-time` prints
the time until the window is shown and exits (used by tools/perf.py). With STAREX_STARTUP_LOG=<file> the app
appends its start-up timings to that file as one JSON line and exits once ready (tools/startup_bench.py).

Start-up order (docs/SPEC.md › Start-up): this module imports only sys, os, time and PySide6 at top level. The
GUI path creates QApplication, shows the splash (ui/splash.py) and paints it, and only then imports numpy-heavy
app modules and builds the main window. Nothing heavy, no network and no folder scan runs before the first
paint of the window.
"""
from __future__ import annotations

import time

T0 = time.perf_counter()

import os  # noqa: E402 - T0 first
import sys  # noqa: E402

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

CLI_ARGS = frozenset({"--selftest", "--selftest-gui", "--version", "-h", "--help"})
ENV_LOG = "STAREX_STARTUP_LOG"  # write the timings to this file and exit when ready
ENV_STOP = "STAREX_STARTUP_STOP"  # "splash": report sys.modules right after the splash is shown, then exit (tests)
WATCHED = ("numpy", "scipy", "astropy", "pyqtgraph", "starextractor.ui.main_window")
# Non-Qt modules the first page needs, imported in a thread while the window is being built (the splash is
# already up). Saves ~250 ms to "ready" for ~60 ms later first paint (the GIL is shared). Third-party and core
# modules only: they never import app UI code, so the import locks cannot deadlock against the GUI thread.
WARM_FIRST = {
    "plan": ("numpy", "scipy.interpolate", "starextractor.core.response", "starextractor.core.reference_stars"),
    "acquisition": ("numpy", "scipy.ndimage", "starextractor.core.acq_sets"),
    "library": ("numpy",),
    "": ("numpy", "scipy.ndimage", "starextractor.core.pair"),  # Reduce › Select, built first on every step
}
FALLBACK_FINISH_MS = 3000  # close the splash even if the window never reports a paint (offscreen, minimised)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if CLI_ARGS.intersection(args) or any(a.startswith("--make-example") for a in args):
        from .ui.splash import close_bootloader_splash

        close_bootloader_splash()  # the frozen build's splash must not sit over a console run
        return _cli(args)
    return _gui(args)


# ---- command-line modes (no window, no splash) -------------------------------------------------------------------
def _parser():
    import argparse

    parser = argparse.ArgumentParser(prog="starextractor")
    parser.add_argument("--session", help="pair (session) folder to open")
    parser.add_argument("--no-splash", action="store_true", help="start without the splash screen")
    parser.add_argument("--startup-time", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--selftest", action="store_true",
                        help="reduce the small synthetic example without the GUI and validate the AVSpec file")
    parser.add_argument("--selftest-gui", action="store_true", help=argparse.SUPPRESS)  # every screen, offscreen
    parser.add_argument("--make-example", metavar="FOLDER", help="write the small example night to FOLDER")
    parser.add_argument("--version", action="store_true", help="print the version")
    return parser


def _cli(argv: list[str]) -> int:
    from pathlib import Path

    _console()
    args = _parser().parse_args(argv)
    from . import APP_NAME, version

    if args.version:
        print(f"{APP_NAME} {version()}")
        return 0
    try:
        from . import selftest

        if args.selftest_gui:
            return selftest.gui_smoke()
        return selftest.make_example(Path(args.make_example)) if args.make_example else selftest.run()
    except BaseException:  # noqa: BLE001 - a command-line run reports and exits, never a dialog
        import traceback

        traceback.print_exc(file=sys.stdout)
        print("self-test FAILED (crashed)" if args.selftest else "FAILED")
        return 1


# ---- the window --------------------------------------------------------------------------------------------------
def _gui(argv: list[str]) -> int:
    clock = StartupClock()
    from .ui.taskbar import set_taskbar_id  # stdlib only: brand.py would cost ~20 ms here

    set_taskbar_id()  # before the first window (the splash), so the taskbar shows our icon, not python.exe's
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from . import APP_NAME, version

    app.setApplicationName(APP_NAME)
    from .ui import splash as sp

    splash = sp.start(argv, version())
    sp.close_bootloader_splash()  # stage 1 ends only now that stage 2 is on screen (or at once without it)
    clock.mark("splash")
    if os.environ.get(ENV_STOP) == "splash":
        _report_modules_and_exit(splash)
    try:
        return _run(app, argv, splash, clock)
    except Exception as e:  # noqa: BLE001 - start-up failed: the splash goes first, then one clear message
        if splash is not None:
            splash.close_now()
        _startup_failed(e)
        return 1


def _run(app: QApplication, argv: list[str], splash, clock: StartupClock) -> int:
    from pathlib import Path

    from .ui import splash as sp

    args = _parser().parse_args(argv)
    from starextractor.core.session import Session

    from .core.settings import Settings
    from .ui import theme
    from .ui.brand import app_icon

    app.setWindowIcon(app_icon())
    settings = Settings()
    warm_first_page(str(settings["location"] or ""))
    # the palette before any window exists: no flash of the wrong colours at start
    theme.apply(app, str((settings["ui"] or {}).get("palette", "default")))
    from .ui.data_locations import FirstRunDialog, check_locations
    if not settings["data_root"] and not args.startup_time:  # first run: ask for the data root (not when timed)
        if splash is not None:
            splash.close_now()  # never over a dialog
        if not FirstRunDialog(Settings()).exec():
            return 0
    from .ui.main_window import MainWindow

    if splash is not None:
        splash.set_status(sp.STATUS_WINDOW)
    session = None
    if args.session and Session.is_session(Path(args.session)):
        session = Session.load(Path(args.session))
    win = MainWindow(session=session)
    if not args.startup_time:
        win.preselect_location()  # the right page's stub for the first paint; built right after it
    install_excepthook(win)
    for w in sp.WARNINGS:
        win.note(w)

    def first_paint() -> None:
        win.rail.painted.disconnect(first_paint)
        clock.mark("window")
        if args.startup_time:
            print(f"shown_s={clock.since_t0('window'):.3f}", flush=True)
        if splash is not None:
            from .ui.motion import reduced_motion

            splash.finish(fade=not reduced_motion())
        if not args.startup_time:
            QTimer.singleShot(0, win.restore_location)  # the last page or step, now that the window is up

    win.rail.painted.connect(first_paint)
    win.show()
    if splash is not None:
        QTimer.singleShot(FALLBACK_FINISH_MS, splash.close_now)
    if not args.startup_time:

        def locations() -> None:  # an unplugged drive: one clear message, never under the splash
            QTimer.singleShot(0, lambda: check_locations(win.settings, win))

        splash.after_close(locations) if splash is not None else locations()
    exit_when_ready = bool(args.startup_time or os.environ.get(ENV_LOG))
    clock.when_ready(win, lambda: _on_ready(app, win, clock, args.startup_time, exit_when_ready))
    return run_event_loop(app)


def run_event_loop(app: QApplication) -> int:
    return app.exec()


def quit_event_loop(app: QApplication) -> None:
    """End run_event_loop. (In-process tests replace both with a local QEventLoop: QApplication.quit leaves the
    thread's quit flag set, which would end every later event loop in that process at once.)"""
    app.quit()


def warm_first_page(location: str) -> None:
    """Import what the first page needs in a daemon thread (WARM_FIRST); errors surface later on the GUI
    thread, where the same import runs again."""
    import importlib
    import threading

    mods = WARM_FIRST.get(location, WARM_FIRST[""])

    def run() -> None:
        for m in mods:
            try:
                importlib.import_module(m)
            except Exception:  # noqa: BLE001 - reported by the real import on the GUI thread
                return

    threading.Thread(target=run, name="warm-first-page", daemon=True).start()


def _on_ready(app: QApplication, win, clock: StartupClock, print_times: bool, exit_now: bool) -> None:
    clock.mark("ready")
    win.note(clock.summary())
    if print_times:
        print(f"startup_s={clock.since_t0('ready'):.3f}", flush=True)
    path = os.environ.get(ENV_LOG)
    if path:
        clock.write(path)
    if exit_now:
        quit_event_loop(app)


class StartupClock:
    """perf_counter marks for splash / window / ready, reported in ms from the process start (Windows: the
    process creation time; elsewhere from T0, the first line of this module)."""

    def __init__(self) -> None:
        self.t: dict[str, float] = {}
        self._offset: float | None = None

    def mark(self, name: str) -> None:
        self.t.setdefault(name, time.perf_counter())

    def since_t0(self, name: str) -> float:
        return self.t[name] - T0

    def offset_ms(self) -> float:
        """ms from the process creation to T0 (0 when unknown)."""
        if self._offset is None:
            self._offset = _process_age_ms() - (time.perf_counter() - T0) * 1000.0
            if self._offset < 0 or self._offset > 60_000:
                self._offset = 0.0
        return self._offset

    def ms(self, name: str) -> float | None:
        return None if name not in self.t else self.since_t0(name) * 1000.0 + self.offset_ms()

    def summary(self) -> str:
        def f(name: str) -> str:
            v = self.ms(name)
            return "off" if v is None else f"{v:.0f} ms"

        return f"startup: splash {f('splash')}, window {f('window')}, ready {f('ready')}"

    def when_ready(self, win, fn) -> None:
        """fn once the window has painted and its first page is built (Reduce › Select, or the restored page),
        with the event loop idle."""

        def check() -> None:
            first = "window" in self.t and win.first_page_built()
            if not first:
                QTimer.singleShot(5, check)
                return
            fn()

        QTimer.singleShot(0, check)

    def write(self, path: str) -> None:
        import json

        row = {k: self.ms(k) for k in ("splash", "window", "ready")}
        row |= {"from_process_ms": self.offset_ms(), "t0_epoch": time.time() - (time.perf_counter() - T0)}
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass


def _process_age_ms() -> float:
    """ms since this process was created (Windows), else since T0."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            k = ctypes.windll.kernel32
            pft = ctypes.POINTER(wintypes.FILETIME)
            k.GetCurrentProcess.restype = wintypes.HANDLE  # a 64-bit pseudo-handle: never truncate it to int
            k.GetProcessTimes.argtypes = [wintypes.HANDLE, pft, pft, pft, pft]
            k.GetProcessTimes.restype = wintypes.BOOL
            created, a, b, c, now = (wintypes.FILETIME() for _ in range(5))
            if k.GetProcessTimes(k.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(a), ctypes.byref(b),
                                 ctypes.byref(c)):
                k.GetSystemTimePreciseAsFileTime(ctypes.byref(now))

                def ms(ft) -> float:
                    return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 10_000.0

                return ms(now) - ms(created)
        except (AttributeError, OSError):
            pass
    return (time.perf_counter() - T0) * 1000.0


def _report_modules_and_exit(splash) -> None:
    """Test hook: which heavy modules are loaded at the moment the splash is on screen."""
    import json

    print(json.dumps({"splash": splash is not None and splash.isVisible(),
                      "loaded": [m for m in WATCHED if m in sys.modules]}), flush=True)
    os._exit(0)


def _startup_failed(exc: BaseException) -> None:
    """Start-up raised: full traceback to error.log and stderr, then one message."""
    import traceback

    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        from starextractor.core.settings import app_dir

        with open(app_dir() / "error.log", "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} (start-up)\n{text}")
    except Exception:  # noqa: BLE001 - the report must not fail on its own
        pass
    sys.__stderr__.write(text) if sys.__stderr__ else None
    show_startup_error(f"{type(exc).__name__}: {exc}")


def show_startup_error(message: str) -> None:
    """The one start-up error message (the splash is already closed). Not on the offscreen platform, where
    nobody could close it."""
    if os.environ.get("QT_QPA_PLATFORM", "").startswith("offscreen"):
        return
    from PySide6.QtWidgets import QMessageBox

    from . import APP_NAME

    QMessageBox.critical(None, APP_NAME, f"{APP_NAME} could not start.\n\n{message}\n\nThe details are in "
                                         "error.log in the settings folder.")


def _console() -> None:
    """The frozen app is a windowed program without a console: for the command-line options, print to the
    console it was started from (or nowhere, without crashing)."""
    if sys.stdout is not None and sys.stderr is not None:
        for s in (sys.stdout, sys.stderr):  # a redirected console may be cp1252: never crash on '−' or '°'
            try:
                s.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass
        return
    if sys.platform == "win32":
        try:
            import ctypes

            if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                sys.stderr = sys.stdout
                return
        except (AttributeError, OSError):
            pass
    import io

    sys.stdout = sys.stdout or io.StringIO()
    sys.stderr = sys.stderr or io.StringIO()


def install_excepthook(win) -> None:
    """Unexpected errors in the UI thread: full traceback to error.log, one plain line to the user."""
    import traceback

    from starextractor.core.settings import app_dir

    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            with open(app_dir() / "error.log", "a", encoding="utf-8") as f:
                f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}")
        except OSError:
            pass
        sys.__stderr__.write(text) if sys.__stderr__ else None
        try:
            win.report_error(f"Something went wrong: {exc_type.__name__}: {exc}", details=text)
        except Exception:  # noqa: BLE001 — never raise from the hook
            pass

    sys.excepthook = hook


if __name__ == "__main__":
    sys.exit(main())
