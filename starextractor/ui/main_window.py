"""Main window: top bar, Reduce | Export tabs, ribbon, step screens; wires session + job runner."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from starextractor import APP_NAME
from starextractor.core.pipeline import IMAGES, STAGE_IDS, STAGES, StageRun
from starextractor.core.session import Session, Status
from starextractor.core.settings import Settings
from starextractor.workers.runner import JobRunner

from . import dialogs, theme
from .brand import Logo, app_icon
from .rail import Rail, StepState, legend_line
from .screens.base import ExportScreen, StepScreen
from .widgets.night_switch import NightSwitch

if TYPE_CHECKING:  # numpy/scipy/pyqtgraph-heavy modules load after the window is on screen
    from starextractor.core.instrument import InstrumentProfile
    from starextractor.core.masters import MasterLibrary

    from .screens.files import FilesScreen
    from .screens.geometry import GeometryScreen

LAZY_SCREENS = {"files": ("files", "FilesScreen"), "geometry": ("geometry", "GeometryScreen"),
                "extract": ("extract", "ExtractScreen"), "wavelength": ("wavelength", "WavelengthScreen"),
                "response": ("response", "ResponseScreen")}




TABS = ("plan", "acquisition", "reduce", "export", "library")
SHOW_RUN_ALL = "show_run_all"  # settings ui.show_run_all: the Run all button and shortcuts (hidden for now)
TAB_TITLES = {"plan": "Plan", "acquisition": "Acquisition", "reduce": "Reduce", "export": "Export",
              "library": "Library"}


def placeholder_page(title: str, text: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 16, 0, 0)
    t = QLabel(title)
    t.setProperty("role", "title")
    lay.addWidget(t)
    d = QLabel(text)
    d.setProperty("role", "muted")
    d.setWordWrap(True)
    lay.addWidget(d)
    lay.addStretch(1)
    return w


def kind_of(slot: str) -> str:
    return slot.partition(".")[0]


class MainWindow(QMainWindow):
    warmed = Signal()  # heavy imports finished in the background
    library_changed = Signal()  # master library folder or contents changed (Select and Acquisition panels)
    pairs_changed = Signal()  # a pair was loaded, a step finished or a file was published: the Library rescans
    planned_link_changed = Signal()  # Plan › Load for acquisition, or the Acquisition names changed
    notes_changed = Signal(str)  # a pair folder whose logbook notes changed (Select › Pair notes or the Library)
    def __init__(self, settings: Settings | None = None, session: Session | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumHeight(700)
        self.resize(1280, 960)
        self.settings = settings or Settings()
        from starextractor.core import devflags  # no heavy imports here: the start-up budget

        devflags.centre_method = (self.settings["wavelength"] or {}).get("centre_method")  # developer setting
        self.runner = JobRunner(self)
        self._library: MasterLibrary | None = None
        self.masters_owner: Any = None  # the Create masters card that started the "masters" job
        self._profile: InstrumentProfile | None = None
        self._built: dict[str, StepScreen] = {}  # real screens that replaced their stubs
        self.current = 0
        self._run_queue: list[str] = []
        self._checks_timer = QTimer(self)
        self._checks_timer.setSingleShot(True)
        self._checks_timer.setInterval(150)
        self._checks_timer.timeout.connect(self.refresh_checks)
        self._steps_timer = QTimer(self)  # coalesced: logbook steps that went stale
        self._steps_timer.setSingleShot(True)
        self._steps_timer.setInterval(300)
        self._steps_timer.timeout.connect(self._sync_steps)
        self.session = session or self._initial_session()

        central = QWidget()
        central.setObjectName("central")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.topbar = self._build_topbar()
        outer.addWidget(self.topbar)
        rail_box = QWidget()  # the workflow rail: always visible, on every page
        rl = QVBoxLayout(rail_box)
        rl.setContentsMargins(32, 10, 32, 0)
        rl.setSpacing(0)
        self.rail = Rail()
        self.rail.step_clicked.connect(self.go_to)
        self.rail.group_clicked.connect(self.go_to_group)
        rl.addWidget(self.rail)
        self.legend = legend_line()  # Reduce and Export pages only
        rl.addWidget(self.legend)
        outer.addWidget(rail_box)

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(32, 16, 32, 16)
        pl.setSpacing(0)

        self.stack = QStackedWidget()
        self.screens: list[StepScreen] = []
        for stage in STAGES:
            # Real screens are swapped in after the first paint (see ensure_screen) so the window
            # shows before numpy, pyqtgraph and the heavier widgets load.
            screen = StepScreen(stage, len(STAGES))
            self.screens.append(screen)
            self.stack.addWidget(screen)
        self.export_screen = ExportScreen()
        self.stack.addWidget(self.export_screen)
        # light stubs, replaced by the real screens on first use (also what the first paint shows when the app
        # reopens on one of these pages: preselect_location)
        self.plan_page = placeholder_page("Plan", "Opening the observing plan…")
        self.acquisition_page = placeholder_page("Acquisition", "Opening Acquisition…")
        self.stack.addWidget(self.plan_page)
        self.stack.addWidget(self.acquisition_page)
        self.library_page: QWidget = placeholder_page("Library", "Every night and pair under the data root.")
        self.stack.addWidget(self.library_page)
        pl.addWidget(self.stack, 1)
        outer.addWidget(page, 1)
        self.setCentralWidget(central)

        self.runner.progress.connect(self._on_progress)
        self.runner.finished.connect(self._on_finished)
        self.runner.failed.connect(self._on_failed)
        self.runner.cancelled.connect(self._on_cancelled)
        self.runner.busy_changed.connect(self._on_busy)
        self._install_shortcuts()
        self._restore_geometry()

        self._tab = ""
        self.show_tab("reduce")
        self._attach_session(self.session)
        self.warmed.connect(self._on_warmed)
        self.rail.painted.connect(self._after_first_paint)

    def _after_first_paint(self) -> None:
        """Once the shell is on screen (keeps startup under 1 s): when Reduce is showing, build the Select
        screen. The rail is painted on every page; on another page the screens are built when first opened."""
        self.rail.painted.disconnect(self._after_first_paint)
        QTimer.singleShot(0, self._build_first_screen)

    def _build_first_screen(self) -> None:
        if self._tab != "reduce":
            return
        _ = self.files_screen
        threading.Thread(target=self._warm_imports, name="warm-imports", daemon=True).start()

    def _warm_imports(self) -> None:
        """Import numpy/scipy/pyqtgraph off the GUI thread; the screens are built when done."""
        import importlib

        for mod in ("numpy", "scipy.ndimage", "pyqtgraph", "starextractor.core.geometry_stage",
                    "starextractor.core.extract_stage", "starextractor.core.wavelength_stage",
                    "scipy.optimize", "starextractor.core.response_stage", "scipy.interpolate",
                    "starextractor.ui.widgets.image_viewer"):
            importlib.import_module(mod)
        self.warmed.emit()

    def _on_warmed(self) -> None:
        for sid in LAZY_SCREENS:
            self.ensure_screen(sid)
        self.ensure_export()

    def ensure_plan(self):
        """The Plan screen is built on first use (keeps startup light)."""
        from .screens.plan import PlanScreen

        if isinstance(self.plan_page, PlanScreen):
            return self.plan_page
        stub = self.plan_page
        real = PlanScreen(self)
        real.log.set_lines(self.session.log_lines)
        showing = self.stack.currentWidget() is stub
        idx = self.stack.indexOf(stub)
        self.stack.removeWidget(stub)
        stub.deleteLater()
        self.stack.insertWidget(idx, real)
        self.plan_page = real
        real.load_session()
        if showing:
            self.stack.setCurrentWidget(real)
        return real

    def ensure_acquisition(self):
        """The Acquisition screen is built on first use (keeps startup light)."""
        from .screens.acquisition import AcquisitionScreen

        if isinstance(self.acquisition_page, AcquisitionScreen):
            return self.acquisition_page
        stub = self.acquisition_page
        real = AcquisitionScreen(self)
        real.log.set_lines(self.session.log_lines)
        showing = self.stack.currentWidget() is stub
        idx = self.stack.indexOf(stub)
        self.stack.removeWidget(stub)
        stub.deleteLater()
        self.stack.insertWidget(idx, real)
        self.acquisition_page = real
        real.load_session()
        if showing:
            self.stack.setCurrentWidget(real)
        return real

    def ensure_library(self):
        """The Library tab, built on first use."""
        from .screens.library import LibraryScreen

        if isinstance(self.library_page, LibraryScreen):
            return self.library_page
        stub = self.library_page
        real = LibraryScreen(self)
        showing = self.stack.currentWidget() is stub
        idx = self.stack.indexOf(stub)
        self.stack.removeWidget(stub)
        stub.deleteLater()
        self.stack.insertWidget(idx, real)
        self.library_page = real
        if showing:
            self.stack.setCurrentWidget(real)
        return real

    def ensure_export(self):
        from .screens.export import ExportScreen as RealExport

        if isinstance(self.export_screen, RealExport):
            return self.export_screen
        stub = self.export_screen
        real = RealExport(self)
        real.log.set_lines(self.session.log_lines)
        showing = self.stack.currentWidget() is stub
        idx = self.stack.indexOf(stub)
        self.stack.removeWidget(stub)
        stub.deleteLater()
        self.stack.insertWidget(idx, real)
        self.export_screen = real
        real.load_session()
        if showing:
            self.stack.setCurrentWidget(real)
        return real

    def site(self) -> dict:
        """Site position for airmass and export: the frame headers (SITELAT/SITELONG) by default, else the
        saved one; a position typed on the Export tab (from_headers = False) wins."""
        from starextractor.core.export import site_from_frames

        saved = dict(self.settings["site"] or {})
        if saved.get("from_headers", True):
            from starextractor.core.pair_state import active_pair

            pair = active_pair(self.session) or {}
            paths = [Path(str(e["source"])) for e in (pair.get(r) or {} for r in ("target", "reference"))
                     if e.get("source")]
            paths += [Path(f["path"]) for side in ("target", "reference")
                      for f in self.session.inputs[side].get("lights") or []]  # sessions from before pairs
            paths += [self.library.abspath(m) for m in self.library.masters]
            hdr = site_from_frames(paths)
            if hdr is not None:
                return {**saved, **{k: v for k, v in hdr.items() if v is not None}, "source": "headers"}
            return {**saved, "source": "settings" if saved.get("lat") is not None else "none"}
        return {**saved, "source": "typed"}

    @property
    def profile(self) -> InstrumentProfile:
        if self._profile is None:
            from starextractor.core.instrument import InstrumentProfile

            self._profile = InstrumentProfile.load()
        return self._profile

    @property
    def library(self) -> MasterLibrary:
        if self._library is None:
            from starextractor.core.masters import MasterLibrary

            self._library = MasterLibrary(self.settings.masters_dir())
        return self._library

    # ---- construction ------------------------------------------------------------------------
    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(24, 10, 24, 10)
        lay.setSpacing(16)
        brand_box = QWidget()  # fixed: never takes width the tabs need
        brand = QHBoxLayout(brand_box)
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(10)
        self.logo = Logo(f"About {APP_NAME}")
        self.logo.clicked.connect(self.open_about)
        brand.addWidget(self.logo)
        name = QLabel(APP_NAME)
        name.setProperty("role", "brand")
        brand.addWidget(name)
        brand_box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay.addWidget(brand_box)
        lay.addSpacing(12)
        lay.addStretch(1)
        # Plan and Library: tools, not workflow steps (the rail below holds those)
        self.plan_btn = QPushButton("Plan")
        self.plan_btn.setObjectName("mainTab")
        self.plan_btn.setCheckable(True)
        self.plan_btn.setToolTip("Plan your target and pick a reference star")
        self.plan_btn.setAccessibleName("Plan")
        self.plan_btn.setAccessibleDescription("Plan your target and pick a reference star")
        self.plan_btn.clicked.connect(lambda: self.show_tab("plan"))
        lay.addWidget(self.plan_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.library_btn = QPushButton("Library")
        self.library_btn.setObjectName("mainTab")
        self.library_btn.setCheckable(True)
        self.library_btn.setToolTip("Every night and pair under the data root")
        self.library_btn.clicked.connect(lambda: self.show_tab("library"))
        lay.addWidget(self.library_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.session_chip = QToolButton()
        self.session_chip.setObjectName("sessionChip")
        self.session_chip.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.session_menu = QMenu(self)
        self.session_menu.aboutToShow.connect(self._fill_session_menu)
        self.session_chip.setMenu(self.session_menu)
        lay.addWidget(self.session_chip)
        self.help_btn = QToolButton()
        self.help_btn.setObjectName("helpBtn")
        self.help_btn.setText("Help me")
        self.help_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.help_btn.setToolTip("The whole workflow, step by step (F1)")
        self.help_btn.setAccessibleName("Help me")
        self._help_icon()
        theme.notifier.changed.connect(self._help_icon)
        self.help_btn.clicked.connect(lambda: self.open_help())
        lay.addWidget(self.help_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.night_switch = NightSwitch(on=theme.is_dark_room())
        self.night_switch.toggled.connect(self.set_night)
        lay.addWidget(self.night_switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.settings_btn = QToolButton()
        self.settings_btn.setObjectName("sessionChip")
        self.settings_btn.setText("⚙")
        self._gear_font()
        theme.notifier.changed.connect(self._gear_font)
        self.settings_btn.setToolTip("Settings (Ctrl+,)")
        self.settings_btn.clicked.connect(lambda: self.open_settings())
        lay.addWidget(self.settings_btn)
        self.run_all_btn = QPushButton("Run all")
        self.run_all_btn.setProperty("role", "primary")
        self.run_all_btn.setToolTip("Run every step that is out of date, in order (Ctrl+Enter). "
                                    "Shift+click or Ctrl+Shift+Enter re-runs every step.")
        self.run_all_btn.clicked.connect(self._run_all_clicked)
        lay.addWidget(self.run_all_btn)
        self.run_all_btn.setVisible(self.run_all_shown())  # hidden for now (ui.show_run_all); the action stays
        return bar

    def _help_icon(self, *_a) -> None:
        self.help_btn.setIcon(dialogs.tool_icon("help"))
        self.help_btn.setIconSize(QSize(16, 16))

    def open_help(self, section: str = ""):
        """Help me: the workflow guide, beside the app (one window, raised when open)."""
        from .help import HelpDialog

        dlg = getattr(self, "help_dialog", None)
        if dlg is None:
            dlg = self.help_dialog = HelpDialog(self)
        if section:
            dlg.show_section(section)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def run_all_shown(self) -> bool:
        """Feature flag: the Run all button and its shortcuts (settings ui.show_run_all, default off)."""
        return bool((self.settings["ui"] or {}).get(SHOW_RUN_ALL, False))

    def set_run_all_shown(self, on: bool) -> None:
        """Show or hide Run all with its shortcuts (the flag; tests and a later Settings switch)."""
        ui = dict(self.settings["ui"] or {})
        ui[SHOW_RUN_ALL] = bool(on)
        self.settings["ui"] = ui
        self.run_all_btn.setVisible(bool(on))
        self.run_all_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus if on else Qt.FocusPolicy.NoFocus)
        for shortcut in self._run_all_shortcuts:
            shortcut.setEnabled(bool(on))

    def _wire_screen(self, screen: StepScreen) -> None:
        sid = screen.stage.id
        screen.header.run_this.connect(
            lambda s=sid, sc=screen: self.run_stage(s, [IMAGES[sc.header.image_idx]]))
        screen.header.run_both.connect(lambda s=sid: self.run_stage(s))
        screen.header.stop.connect(lambda s=sid: self.runner.cancel(s))

    def ensure_screen(self, sid: str) -> StepScreen:
        """Build a real screen in place of its stub (first use or right after startup)."""
        if sid in self._built:
            return self._built[sid]
        idx = STAGE_IDS.index(sid)
        if sid not in LAZY_SCREENS:
            return self.screens[idx]
        import importlib

        mod, cls = LAZY_SCREENS[sid]
        screen = getattr(importlib.import_module(f".screens.{mod}", __package__), cls)(STAGES[idx], len(STAGES), self)
        self._built[sid] = screen
        self._wire_screen(screen)
        screen.log.set_lines(self.session.log_lines)
        if hasattr(screen, "load_session") and sid != "files":
            screen.load_session()
        stub = self.screens[idx]
        showing = self.stack.currentWidget() is stub
        self.stack.removeWidget(stub)
        stub.deleteLater()
        self.stack.insertWidget(idx, screen)
        self.screens[idx] = screen
        if showing:
            self.stack.setCurrentWidget(screen)
        self.refresh_ribbon()
        return screen

    @property
    def files_screen(self) -> FilesScreen:
        return self.ensure_screen("files")  # type: ignore[return-value]

    @property
    def geometry_screen(self) -> GeometryScreen:
        return self.ensure_screen("geometry")  # type: ignore[return-value]

    # ---- session -----------------------------------------------------------------------------
    def _initial_session(self) -> Session:
        for folder in self.settings["recent_sessions"]:
            p = Path(folder)
            if Session.is_session(p):
                try:
                    return Session.load(p)
                except (OSError, ValueError, KeyError):
                    continue
        return Session(None)

    def _attach_session(self, session: Session) -> None:
        if hasattr(self.acquisition_page, "save_logbook"):
            self.acquisition_page.save_logbook()  # into the folder of the session being left
        self.runner.cancel_all(keep_prefix="acq.")  # Acquisition does not belong to a session: it keeps going
        self.session = session
        session.on_log(self._append_log)
        session.on_status(self.refresh_ribbon)
        session.on_status(self._steps_timer.start)  # logbook: steps whose results went out of date
        folder = session.folder
        self.session_chip.setText(f"Pair …\\{folder.name} ▾" if folder else "No pair ▾")
        self.session_chip.setToolTip(str(folder) if folder else "Load a pair in Reduce › Select, or Open pair…")
        # never narrower than the top row (brand + all tabs + session + settings + Run all) or 1100 px
        self.setMinimumWidth(max(1100, self.topbar.minimumSizeHint().width()))
        for screen in self._log_panels():
            screen.set_lines(session.log_lines)
        for screen in self._built.values():
            screen.load_session()  # type: ignore[attr-defined]
        if hasattr(self.export_screen, "load_session"):
            self.export_screen.load_session()
        if hasattr(self.plan_page, "load_session"):
            self.plan_page.load_session()
        if hasattr(self.acquisition_page, "load_session"):
            self.acquisition_page.load_session()  # its own state (Stacks/<night>), loaded once; watching goes on
        self.refresh_ribbon()

    def _log_panels(self) -> list:
        extra = [w.log for w in (self.export_screen, self.plan_page, self.acquisition_page) if hasattr(w, "log")]
        return [s.log for s in self.screens] + extra

    def _append_log(self, line: str) -> None:
        for panel in self._log_panels():
            panel.append(line)

    def note(self, message: str) -> None:
        """An app-level line in the Log panels (start-up timings, warnings) that is not written to the pair's log
        file."""
        import time

        line = f"{time.strftime('%H:%M:%S')}  {message}"
        self.session.log_lines.append(line)
        self._append_log(line)

    def _fill_session_menu(self) -> None:
        m = self.session_menu
        m.clear()
        m.addAction("Open pair…", self.open_session)
        recent = m.addMenu("Recent")
        items = [p for p in self.settings["recent_sessions"] if Session.is_session(Path(p))]
        for p in items:
            recent.addAction(p, lambda path=p: self.load_session(Path(path)))
        recent.setEnabled(bool(items))

    def open_session(self) -> None:
        """Open a pair folder (Processed\\<night>_<Target>__<Ref>) or a session folder from before the data root."""
        start = self.settings.paths().processed
        folder = dialogs.existing_directory(self, "Open a pair folder", str(start) if start.is_dir() else "")
        if folder:
            self.load_session(Path(folder))

    def load_session(self, folder: Path) -> None:
        if not Session.is_session(folder):
            self.session.log(f"No session.json in {folder}")
            return
        try:
            session = Session.load(folder)
        except (OSError, ValueError, KeyError) as exc:
            self.report_error(f"Could not open the session in {folder}: {exc}")
            return
        self._remember(folder)
        self._attach_session(session)

    def _remember(self, folder: Path) -> None:
        self.settings.add_recent(folder)
        self.settings.save()

    def load_pair(self, infos: dict[str, Any], names: dict[str, str]) -> str:
        """Reduce › Select' Load pair, also used by the Library (Open in Reduce, Create pair): the pair folder in
        Processed (made or reused), the Reduce session moved into it, the stacks copied to work/, the logbook
        written (core.pair). Returns "" on success, else the message to show."""
        from starextractor.core import pair as pr
        from starextractor.core.datalocs import OK, status

        paths = self.settings.paths()
        if status(paths.processed) != OK:
            return (f"The Processed folder {paths.processed} is missing or not writable: choose another location "
                    "in Settings › Data locations.")
        folder, reused = pr.pair_folder_for(paths.processed, infos, names, paths.root)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            session = self.session
            new = session.folder is None or Path(session.folder).resolve() != folder.resolve()
            if new:
                session = Session.load(folder) if Session.is_session(folder) else Session.create(folder)
            lines = pr.load_pair(session, infos, names, data_root=paths.root, masters_root=paths.masters)
        except OSError as exc:
            return f"Could not set up the pair folder {folder}: {exc}"
        if new:
            self._remember(folder)
        # attach (or re-attach) only now: every screen fills from the pair as stored, with its names and SIMBAD
        # types (Response's "Auto · <class>" read the type before it was there and showed A0V)
        self._attach_session(session)
        self.session.log(f"Select: pair folder {folder} ({'reused' if reused else 'new'})")
        for line in lines:
            self.session.log(line)
        self.session.save()
        self.files_screen.load_session()  # the cards and notes from the pair just stored
        self.pairs_changed.emit()
        return ""

    def inputs_changed(self) -> None:
        """Select screen edited inputs in place: re-derive status, persist, update the top bar."""
        self.session.changed()
        self.session.save()
        self.refresh_ribbon()

    # ---- navigation --------------------------------------------------------------------------
    def open_step(self, idx: int) -> None:
        self.current = idx
        screen = self.ensure_screen(STAGE_IDS[idx])
        if self.tab_name() != "reduce":
            self.show_tab("reduce")
        self.stack.setCurrentWidget(screen)
        if hasattr(screen, "on_shown"):
            screen.on_shown()
        self.refresh_ribbon()

    def tab_name(self) -> str:
        return self._tab

    def show_tab(self, name: str) -> None:
        if name != self._tab:
            self._tab = name
            self._on_tab(name)
        self.plan_btn.setChecked(name == "plan")
        self.library_btn.setChecked(name == "library")

    def _cycle_tab(self, delta: int) -> None:
        i = TABS.index(self._tab)
        self.show_tab(TABS[max(0, min(len(TABS) - 1, i + delta))])

    # ---- the rail ----------------------------------------------------------------------------
    RAIL_OF_STAGE: ClassVar[dict[str, str]] = {
        "files": "select", "geometry": "geometry", "extract": "extract", "wavelength": "wavelength",
        "response": "response"}
    EXPORT_STEPS = ("collect", "test", "publish")

    def go_to(self, rid: str) -> None:
        """A rail step: its page and inner step (where the old tabs and ribbons went)."""
        if rid == "acquisition":
            self.show_tab("acquisition")
        elif rid in self.EXPORT_STEPS:
            self.show_tab("export")
            self.ensure_export().show_step(self.EXPORT_STEPS.index(rid))
        else:
            sid = next(k for k, v in self.RAIL_OF_STAGE.items() if v == rid)
            self.open_step(STAGE_IDS.index(sid))
        self._save_location()

    def go_to_group(self, group: str) -> None:
        """A group header: the group's current step, else its first step not done, else its first step."""
        from .rail import GROUPS

        g = next(x for x in GROUPS if x.id == group)
        cur = self.rail.current
        if cur in g.steps:
            self.go_to(cur)  # type: ignore[arg-type]
            return
        todo = next((s for s in g.steps if self.rail.by_id[s].state != StepState.DONE), g.steps[0])
        self.go_to(todo)

    def rail_current(self) -> str | None:
        if self._tab == "reduce":
            return self.RAIL_OF_STAGE[STAGE_IDS[self.current]]
        if self._tab == "export":
            return self.EXPORT_STEPS[getattr(self.export_screen, "step", 0)]
        if self._tab == "acquisition":
            return "acquisition"
        return None  # Plan, Library: no current step

    def _acquisition_state(self) -> StepState:
        """Done when the loaded pair's target and reference stacks exist (in Stacks)."""
        from starextractor.core.pair_state import active_pair

        pair = active_pair(self.session) or {}
        srcs = [(pair.get(r) or {}).get("source") for r in ("target", "reference")]
        return StepState.DONE if all(s and Path(s).exists() for s in srcs) else StepState.NOT_RUN

    def refresh_export_steps(self) -> None:
        """Collect / Test / Publish from the Export screen's own state (built on first use)."""
        states = getattr(self.export_screen, "step_states", None)
        for rid, (state, text) in zip(self.EXPORT_STEPS, states or [(StepState.NOT_RUN, "Not run")] * 3, strict=True):
            self.rail.set_step(rid, state=state, status=text)
        self.rail.set_current(self.rail_current())

    def _save_location(self) -> None:
        loc = self.rail_current() or self._tab
        if self.settings["location"] != loc:
            self.settings["location"] = loc
            self.settings.save()

    STUB_TABS = ("plan", "library", "acquisition")

    def _saved_location(self) -> str:
        loc = str(self.settings["location"] or "")
        return {"files": "select"}.get(loc, loc)

    def preselect_location(self) -> None:
        """Start-up, before the window is shown: when the app reopens on Plan, Library or Acquisition, show that
        page's light stub, so the first paint is already the right page and nothing heavy runs before it.
        restore_location (after the first paint) then builds the real page in place."""
        loc = self._saved_location()
        if loc not in self.STUB_TABS:
            return
        self._tab = loc
        self.legend.setVisible(False)
        self.stack.setCurrentWidget({"plan": self.plan_page, "library": self.library_page,
                                     "acquisition": self.acquisition_page}[loc])
        self.plan_btn.setChecked(loc == "plan")
        self.library_btn.setChecked(loc == "library")
        self.refresh_ribbon()

    def first_page_built(self) -> bool:
        """The page on screen is a real screen, not a start-up stub."""
        w = self.stack.currentWidget()
        if self._tab == "reduce":
            sid = STAGE_IDS[self.current]
            return sid in self._built or sid not in LAZY_SCREENS
        if self._tab == "export":
            return type(w) is not ExportScreen
        return type(w) is not QWidget

    def restore_location(self) -> None:
        """Reopen where the app was last closed ("files", the old id of Select, still works). Called once at app
        start (app.py), after the first paint, never by other windows (tests, screenshots)."""
        loc = self._saved_location()
        if loc in self.STUB_TABS and self._tab == loc:  # preselected stub: build the real page now
            self._on_tab(loc)
        elif loc in ("plan", "library"):
            self.show_tab(loc)
        elif loc in self.rail.by_id and loc != self.rail_current():
            self.go_to(loc)

    def _on_tab(self, name: str) -> None:
        self.legend.setVisible(name in ("reduce", "export"))
        if name == "export":
            self.stack.setCurrentWidget(self.ensure_export())
        elif name == "reduce":
            self.stack.setCurrentWidget(self.screens[self.current])
        elif name == "plan":
            self.stack.setCurrentWidget(self.ensure_plan())
        elif name == "library":
            self.stack.setCurrentWidget(self.ensure_library())
            self.library_page.request_scan()  # type: ignore[attr-defined]
        else:
            self.stack.setCurrentWidget(self.ensure_acquisition())
        self.refresh_ribbon()
        if name in ("plan", "library", "acquisition"):
            self._save_location()

    def refresh_ribbon(self) -> None:
        """The rail's Reduce steps (from the session, as the old ribbon), Acquisition, Export and the current
        location."""
        on_reduce = self.tab_name() == "reduce"
        keys = self.session.compute_keys()
        for i, sid in enumerate(STAGE_IDS):
            st = self.session.status[sid]
            done = [img for img in IMAGES if self.session.image_done(sid, img, keys)]
            if st.status in (Status.MISSING, Status.ERROR):
                state, text = StepState.MISSING, st.message or "Missing data"
            elif st.status == Status.VALID:
                state, text = StepState.DONE, self._done_text(sid)
            elif done:
                todo = next(img for img in IMAGES if img not in done)
                state, text = StepState.NOT_RUN, f"{done[0]} done · run {todo}"
            else:
                state, text = StepState.NOT_RUN, "Needs re-run" if st.key else "Not run"
            if self.runner.is_busy(sid):
                text = "Running…"
            self.rail.set_step(self.RAIL_OF_STAGE[sid], state=state, status=text)
            screen = self.screens[i]
            screen.header.set_output_ready(st.status == Status.VALID)
            prev_missing = next(
                (s for s in STAGE_IDS[:i] if self.session.status[s].status != Status.VALID), None)
            screen.show_missing(
                f"Missing data: run {STAGES[STAGE_IDS.index(prev_missing)].title} first."
                if prev_missing else None)
        self.rail.set_step("acquisition", state=self._acquisition_state(),
                           status="Stacks of the loaded pair present" if self._acquisition_state() == StepState.DONE
                           else "No stacks for a loaded pair yet")
        self.refresh_export_steps()  # also sets the current step
        if on_reduce:
            self._checks_timer.start()  # coalesced: header reads stay off the hot path and out of start-up

    def refresh_checks(self) -> None:
        """Consistency notes between Plan, Select, the frames and the airmass inputs, on every step."""
        from starextractor.core.identity import checks
        from starextractor.core.response_stage import params_of as response_params

        site = self.site()
        airmass_on = bool(response_params(self.session, self.profile).get("airmass", True))
        items = checks(self.session, site.get("lat") is not None and site.get("lon") is not None, airmass_on)
        for screen in self.screens:
            if hasattr(screen, "show_checks"):
                screen.show_checks(items)

    def _done_text(self, sid: str) -> str:
        st = self.session.status[sid]
        if sid == "files":
            from starextractor.core.pair_state import active_pair, pair_text

            pair = active_pair(self.session)
            if pair is not None:
                return pair_text(pair)
            n = [len(self.session.used_lights(side)) for side in ("target", "reference")]
            return f"2 stacks · {n[0]} + {n[1]} frames"  # sessions stacked by the old Files step
        return st.summary or "Done"

    # ---- running -----------------------------------------------------------------------------
    def run_stage(self, sid: str, images: list[str] | None = None) -> bool:
        images = images or list(IMAGES)
        title = STAGES[STAGE_IDS.index(sid)].title
        if self.runner.is_busy(sid):
            return False
        if not self.session.upstream_valid(sid, images[0] if len(images) == 1 else None):
            self.session.log(f"{title}: earlier steps are not done yet")
            return False
        if sid == "files":  # nothing to compute: the pair is loaded on the Select screen
            self.session.log("Load a pair in Select first.")
            return False
        if sid == "geometry":
            from starextractor.core.geometry_stage import geometry_job
            from starextractor.core.geometry_stage import specs_for as geometry_specs

            gspecs, problems = geometry_specs(self.session, images, self.profile, self.library)
            for p in problems:
                self.session.log(f"Cannot run Geometry: {p}")
            if problems or not gspecs:
                return False
            self.session.clear_problem(sid)
            self.runner.submit(sid, geometry_job, gspecs, log=self.session.log)
            return True
        if sid == "response":
            from starextractor.core.response_stage import response_job
            from starextractor.core.response_stage import specs_for as response_specs

            rspecs, problems = response_specs(self.session, self.profile, {**self.settings.data, "site": self.site()})
            for p in problems:
                self.session.log(f"Cannot run Response: {p}")
            if problems or not rspecs:
                return False
            self.session.clear_problem(sid)
            self.runner.submit(sid, response_job, rspecs[0], log=self.session.log)
            return True
        if sid == "wavelength":
            from starextractor.core.wavelength_stage import specs_for as wavelength_specs
            from starextractor.core.wavelength_stage import wavelength_job

            wspecs, problems = wavelength_specs(self.session, images, self.profile)
            for p in problems:
                self.session.log(f"Cannot run Wavelength: {p}")
            if problems or not wspecs:
                return False
            self.session.clear_problem(sid)
            self.runner.submit(sid, wavelength_job, wspecs[0], log=self.session.log)
            return True
        if sid == "extract":
            from starextractor.core.extract_stage import extract_job
            from starextractor.core.extract_stage import specs_for as extract_specs

            especs, problems = extract_specs(self.session, images, self.profile.bands)
            for p in problems:
                self.session.log(f"Cannot run 1D Extract: {p}")
            if problems or not especs:
                return False
            self.session.clear_problem(sid)
            self.runner.submit(sid, extract_job, especs, log=self.session.log)
            return True
        return False

    def run_all(self, force: bool = False) -> None:
        """Run every step that is out of date, in order (a step re-run upstream makes the later ones out of
        date, so they follow). force=True (Shift+click, Ctrl+Shift+Enter) re-runs every step. Stops at the
        first step that cannot run."""
        self._run_force = force
        todo = list(STAGE_IDS) if force else [s for s in STAGE_IDS if not self._stage_current(s)]
        if not todo:
            self.session.log("Run all: every step is up to date (Shift+Run all re-runs them all)")
            return
        self._run_queue = list(STAGE_IDS) if force else list(STAGE_IDS[STAGE_IDS.index(todo[0]):])
        self.session.log(("Run all (every step): " if force else "Run all (out of date): ")
                         + ", ".join(self._run_queue))
        self._run_next()

    def _run_all_clicked(self) -> None:
        self.run_all(bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier))

    def _stage_current(self, sid: str) -> bool:
        keys = self.session.compute_keys()
        return all(self.session.image_done(sid, img, keys) for img in IMAGES)

    def _run_next(self) -> None:
        while self._run_queue:
            sid = self._run_queue.pop(0)
            if sid == "files" and self._stage_current(sid):
                continue  # a loaded pair: nothing to run (Select only picks the stacks)
            if not getattr(self, "_run_force", False) and self._stage_current(sid):
                continue  # still valid after the steps before it ran
            if self.run_stage(sid):
                return
            self._run_queue.clear()

    # ---- job results (slots are stage ids, or "<kind>.<side>" / "masters" helpers) -----------
    def _on_progress(self, slot: str, f: float) -> None:
        if slot in STAGE_IDS:
            self.screens[STAGE_IDS.index(slot)].header.set_progress(f)
            self.rail.set_step(self.RAIL_OF_STAGE[slot], status=f"Running… {f:.0%}")
        elif slot == "masters":
            self._masters_card().masters_progress(f)
        elif kind_of(slot) == "acq":
            self.ensure_acquisition().on_progress(slot, f)

    def _on_busy(self, slot: str, busy: bool) -> None:
        if slot in STAGE_IDS:
            self.screens[STAGE_IDS.index(slot)].header.set_busy(busy)
            self.refresh_ribbon()

    def _on_finished(self, slot: str, result: object) -> None:
        kind, _, side = slot.partition(".")
        if kind == "view":
            screen_id, _, _image = side.partition(".")
            self.ensure_screen(screen_id).on_display(result)  # type: ignore[attr-defined]
            return
        if kind == "detect":
            self.geometry_screen.on_detected(side, result)  # type: ignore[arg-type]
            return
        if kind == "slant":
            self.geometry_screen.on_slant(side, result)  # type: ignore[arg-type]
            return
        if kind == "pub":
            self.ensure_export().publish.on_saved(side, result)  # type: ignore[arg-type]
            return
        if kind == "vsx":
            self.ensure_export().on_vsx(result)
            return
        if slot == "plan.simbad":
            self.ensure_plan().on_simbad(result)  # type: ignore[arg-type]
            return
        if kind == "library":
            self.ensure_library().on_job(slot, result)
            return
        if kind == "acq":
            self.ensure_acquisition().on_job(slot, result)
            return
        if kind in ("preview", "auto"):  # "<kind>.<screen>.<image>" → screen.on_<kind>(image, result)
            screen_id, _, image = side.partition(".")
            getattr(self.ensure_screen(screen_id), f"on_{kind}")(image, result)
            return
        if kind == "masters":
            self._masters_card().masters_done(result)  # type: ignore[arg-type]
        elif isinstance(result, StageRun):
            self._stage_done(slot, result)
        else:
            summary = result.get("summary", "Done") if isinstance(result, dict) else "Done"
            self.session.mark_done(slot, {}, summary)
            self.session.log(f"{STAGES[STAGE_IDS.index(slot)].title}: {summary}")
            self.session.save()
            self._run_next()

    def _stage_done(self, sid: str, run: StageRun) -> None:
        for line in run.log:
            self.session.log(line)
        for img, out in run.outputs.items():
            self.session.mark_image_done(sid, img, out, run.summaries.get(img, ""))
        self.session.status[sid].summary = run.ribbon or run.summary
        screen = self._built.get(sid)
        if screen is not None and hasattr(screen, "stage_done"):
            screen.stage_done(run)
        self.session.save()
        self._log_stage(sid, run)
        self.refresh_ribbon()
        self._run_next()

    def _log_stage(self, sid: str, run: StageRun) -> None:
        """Record a finished step in the pair logbook (and wavelength.json / response.json in the pair folder)."""
        from starextractor.core.pair_steps import record_step

        if self.session.folder is not None:
            try:
                record_step(self.session, sid, run)
            except OSError as exc:
                self.session.log(f"Logbook: could not record {sid}: {exc}")
            self.pairs_changed.emit()

    def _sync_steps(self) -> None:
        """Mark logbook steps stale whose results the session now has as out of date (coalesced)."""
        from starextractor.core.pair_steps import sync_stale

        try:
            if sync_stale(self.session):
                self.pairs_changed.emit()
        except OSError:
            pass

    def _on_failed(self, slot: str, msg: str) -> None:
        first = msg.splitlines()[0]
        if slot in STAGE_IDS:
            self.session.mark_error(slot, first)
            self._run_queue.clear()
            if self.session.folder is not None:
                from starextractor.core import logbook

                logbook.set_step(self.session.folder, slot, logbook.FAILED, first)
                self.pairs_changed.emit()
        elif kind_of(slot) == "library":
            self.ensure_library().on_failed(slot, first)
            return
        elif slot == "masters":
            self._masters_card().masters_done(None, first)
            return
        elif slot.startswith("detect."):
            self.geometry_screen.detect_btn.setEnabled(True)
        elif slot.startswith("slant."):
            self.geometry_screen.on_slant_failed(first)
        elif slot.startswith("pub."):
            self.ensure_export().publish.on_failed(slot[4:], msg)
        elif slot.startswith("vsx."):
            self.ensure_export().on_vsx({"query": "", "found": False, "offline": True,
                                         "message": f"Could not reach VSX ({first})"})
        elif slot.startswith("auto.extract."):
            self.ensure_screen("extract").auto_btn.setEnabled(True)  # type: ignore[attr-defined]
        elif kind_of(slot) == "acq":
            self.ensure_acquisition().on_failed(slot, first)
        elif slot == "plan.simbad":
            self.ensure_plan().on_simbad({"query": "", "found": False, "offline": True, "error": first})
        self.session.log(f"{slot} failed: {first}")

    def _on_cancelled(self, slot: str) -> None:
        if slot in STAGE_IDS:
            self.session.log(f"{STAGES[STAGE_IDS.index(slot)].title}: stopped")
            self._run_queue.clear()
            self.refresh_ribbon()
        elif slot == "masters":
            self._masters_card().masters_done(["Create masters: stopped"])
        elif kind_of(slot) == "acq":
            self.ensure_acquisition().on_cancelled(slot)
        elif slot == "plan.simbad":
            plan = self.ensure_plan()
            plan.simbad_btn.setEnabled(True)
            plan.simbad_status.set("off", "SIMBAD lookup stopped.")

    # ---- keyboard, window, settings, errors ---------------------------------------------------
    SHORTCUTS = (
        ("Ctrl+Return", "Run all steps that are out of date", "run_all"),
        ("Ctrl+Shift+Return", "Re-run every step", ""),
        ("F5", "Run the current step (both images)", "run_current"),
        ("Esc", "Stop the running step", "stop_current"),
        ("Ctrl+1 … Ctrl+5", "Go to step 1 … 5", ""),
        ("Alt+Left / Alt+Right", "Previous / next step", ""),
        ("Ctrl+PgUp / Ctrl+PgDown", "Previous / next tab", ""),
        ("Ctrl+O", "Open a pair folder", ""),
        ("Ctrl+,", "Settings", "open_settings"),
        ("Ctrl+Shift+N", "Night mode on / off (red-filter safe)", ""),
        ("F1", "Help me: the workflow guide", "open_help"),
    )

    def _install_shortcuts(self) -> None:
        def sc(keys: str, fn) -> QShortcut:
            return QShortcut(QKeySequence(keys), self, activated=fn)

        self._run_all_shortcuts = [sc("Ctrl+Return", lambda: self.run_all()), sc("Ctrl+Enter", lambda: self.run_all()),
                                   sc("Ctrl+Shift+Return", lambda: self.run_all(True)),
                                   sc("Ctrl+Shift+Enter", lambda: self.run_all(True))]
        for shortcut in self._run_all_shortcuts:
            shortcut.setEnabled(self.run_all_shown())
        sc("F5", self.run_current)
        sc("Esc", self.stop_current)
        for i in range(len(STAGES)):
            sc(f"Ctrl+{i + 1}", lambda i=i: self.open_step(i))
        sc("Alt+Left", lambda: self.open_step(max(0, self.current - 1)))
        sc("Alt+Right", lambda: self.open_step(min(len(STAGES) - 1, self.current + 1)))
        sc("Ctrl+PgUp", lambda: self._cycle_tab(-1))
        sc("Ctrl+PgDown", lambda: self._cycle_tab(1))
        sc("Ctrl+O", self.open_session)
        sc("Ctrl+,", self.open_settings)
        sc("Ctrl+Shift+N", self.night_switch.toggle)
        sc("F1", lambda: self.open_help())

    def run_current(self) -> None:
        if self.tab_name() != "reduce" or self.runner.is_busy(STAGE_IDS[self.current]):
            return
        self.run_stage(STAGE_IDS[self.current])

    def stop_current(self) -> None:
        self._run_queue.clear()
        for sid in STAGE_IDS:
            if self.runner.is_busy(sid):
                self.runner.cancel(sid)

    def open_about(self) -> None:
        from .about import AboutDialog
        from .widgets.confetti import reduced_motion

        dlg = AboutDialog(self, animate=not reduced_motion())
        self.about_dialog = dlg  # tests and screenshots reach it while it is open
        dlg.exec()

    # ---- night mode ---------------------------------------------------------------------------------------
    def set_night(self, on: bool) -> None:
        """The switch: default ↔ night, live (style sheet, palette, plots, image views, rail, logo), and kept in
        settings ui.palette. The red palette is only chosen in the settings file; the switch turns it off too."""
        from . import retheme

        name = "night" if on else "default"
        retheme.switch(name)
        self.night_switch.setChecked(on)
        ui = dict(self.settings["ui"] or {})
        ui["palette"] = name
        self.settings["ui"] = ui
        self.settings.save()
        self.session.log(f"Night mode {'on' if on else 'off'}")

    def _gear_font(self, *_a: object) -> None:
        """Night / red: the gear from a monochrome symbol font (the default font draws a grey-blue emoji)."""
        self.settings_btn.setStyleSheet('font-family: "Segoe UI Symbol";' if theme.is_dark_room() else "")

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if theme.is_dark_room():
            theme.set_dark_title_bar(self, True)  # best effort; Windows draws it dark grey, never red

    def open_settings(self) -> bool:
        from .settings_dialog import SettingsDialog

        before = (self.settings.masters_dir(), dict(self.settings["tolerances"] or {}))
        dlg = SettingsDialog(self.settings, self)
        if not dlg.exec():
            return False
        if self.settings.masters_dir() != before[0]:
            self._library = None
            self.session.log(f"Master library: {self.settings.masters_dir()}")
        if (self.settings.masters_dir(), self.settings["tolerances"]) != before:
            self.library_changed.emit()
        return True

    def set_masters_dir(self, folder: str) -> None:
        """The one Masters location (Settings › Data locations, and Acquisition > Pre Processing settings). The
        folder is made when it does not exist (the user typed or picked it)."""
        from starextractor.core.datalocs import NAMES

        folder = folder.strip()
        paths = self.settings.paths()
        default = paths.root / NAMES["masters"]
        value = "" if folder in ("", str(default)) else folder
        current = dict(self.settings["data_paths"] or {})
        if value == (current.get("masters") or ""):
            return
        try:
            Path(value or default).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.report_error(f"Cannot use {value or default} as the Masters folder: {exc}")
            return
        current["masters"] = value
        self.settings["data_paths"] = current
        self.settings.save()
        self._library = None
        self.session.log(f"Master library: {self.settings.masters_dir()}")
        self.library_changed.emit()

    def _masters_card(self) -> Any:
        return self.masters_owner if self.masters_owner is not None else self.ensure_acquisition().prep.create_card

    def _restore_geometry(self) -> None:
        geo = (self.settings["window"] or {}).get("geometry")
        if geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            except (ValueError, UnicodeError):
                pass

    def report_error(self, text: str, details: str = "") -> None:
        """Plain-language error: one log line, plus a dialog unless running headless."""
        self.session.log(f"Error: {text}")
        if details:
            self.session.log("Details are in error.log in the settings folder")
        if self.isVisible() and not getattr(self, "_quiet_errors", False):
            box = QMessageBox(QMessageBox.Icon.Warning, APP_NAME, text, QMessageBox.StandardButton.Ok, self)
            if details:  # an unexpected error: the traceback is in error.log
                from starextractor.core.settings import app_dir

                box.setInformativeText(f"The details are in error.log in {app_dir()}.")
                folder = box.addButton("Open log folder", QMessageBox.ButtonRole.ActionRole)
                folder.clicked.disconnect()  # keep the dialog open after opening the folder
                folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_dir()))))
            self.last_error_box = box
            box.exec()

    def closeEvent(self, e: QCloseEvent) -> None:
        if hasattr(self.acquisition_page, "stop_watch"):
            self.acquisition_page.stop_watch()
            self.acquisition_page.save_logbook()
        self.runner.cancel_all()
        self.runner.wait(3000)
        self.session.save()
        self.settings["window"] = {"geometry": bytes(self.saveGeometry().toBase64()).decode("ascii")}
        self.settings.save()
        super().closeEvent(e)
