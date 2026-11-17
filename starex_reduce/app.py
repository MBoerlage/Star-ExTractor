"""Entry point: `starex-reduce` / `python -m starex_reduce.app`.

Options: `--session <folder>` opens a session; `--startup-time` prints the time until the window
is shown and exits (used by tools/perf.py).
"""
from __future__ import annotations

import argparse
import sys
import time

T0 = time.perf_counter()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="starex-reduce")
    parser.add_argument("--session", help="session folder to open")
    parser.add_argument("--startup-time", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--selftest", action="store_true",
                        help="reduce the small synthetic example without the GUI and validate the AVSpec file")
    parser.add_argument("--selftest-gui", action="store_true", help=argparse.SUPPRESS)  # every screen, offscreen
    parser.add_argument("--make-example", metavar="FOLDER", help="write the small example night to FOLDER")
    parser.add_argument("--version", action="store_true", help="print the version")
    args = parser.parse_args(argv)

    from pathlib import Path

    if args.selftest or args.selftest_gui or args.make_example or args.version:
        _console()
        from . import APP_NAME, __version__

        if args.version:
            print(f"{APP_NAME} {__version__}")
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

    from PySide6.QtWidgets import QApplication

    from starex_reduce.core.session import Session

    from .ui import theme
    from .ui.brand import app_icon, set_taskbar_id
    from .ui.main_window import MainWindow

    set_taskbar_id()  # before the first window, so the taskbar shows our icon, not python.exe's
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from . import APP_NAME

    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    theme.apply(app)
    session = None
    if args.session and Session.is_session(Path(args.session)):
        session = Session.load(Path(args.session))
    win = MainWindow(session=session)
    install_excepthook(win)
    win.show()
    if args.startup_time:
        _report_startup(app, win)
    return app.exec()


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

    from starex_reduce.core.settings import app_dir

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


def _report_startup(app, win) -> None:
    """Print time to the first painted ribbon (window visible) and to the Files screen being ready."""
    from PySide6.QtCore import QEvent, QObject, QTimer

    class FirstPaint(QObject):
        def eventFilter(self, obj, event) -> bool:
            if event.type() == QEvent.Type.Paint and not hasattr(self, "done"):
                self.done = True
                print(f"shown_s={time.perf_counter() - T0:.3f}", flush=True)
            return False

    win._first_paint = FirstPaint(win)
    win.ribbon.installEventFilter(win._first_paint)

    def ready() -> None:
        if "files" not in win._built or not hasattr(win._first_paint, "done"):
            QTimer.singleShot(5, ready)
            return
        print(f"startup_s={time.perf_counter() - T0:.3f}", flush=True)
        app.quit()

    QTimer.singleShot(0, ready)


if __name__ == "__main__":
    sys.exit(main())
