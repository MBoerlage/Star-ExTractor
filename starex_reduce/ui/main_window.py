"""Main window: top bar, Reduce | Export tabs, ribbon, step screens; wires session + job runner."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
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

from starex_reduce import APP_NAME
from starex_reduce.core.pipeline import IMAGES, STAGE_IDS, STAGES, StageRun
from starex_reduce.core.session import Session, Status
from starex_reduce.core.settings import Settings
from starex_reduce.workers.runner import JobRunner

from .brand import Logo, app_icon
from .ribbon import Ribbon, RibbonStep, StepState, legend_row
from .screens.base import ExportScreen, StepScreen
from .tab_row import TabRow

if TYPE_CHECKING:  # numpy/scipy/pyqtgraph-heavy modules load after the window is on screen
    from starex_reduce.core.instrument import InstrumentProfile
    from starex_reduce.core.masters import MasterLibrary

    from .screens.files import FilesScreen
    from .screens.geometry import GeometryScreen

LAZY_SCREENS = {"files": ("files", "FilesScreen"), "geometry": ("geometry", "GeometryScreen"),
                "extract": ("extract", "ExtractScreen"), "wavelength": ("wavelength", "WavelengthScreen"),
                "response": ("response", "ResponseScreen")}




TABS = ("plan", "acquisition", "reduce", "export")
TAB_TITLES = {"plan": "Plan", "acquisition": "Acquisition", "reduce": "Reduce", "export": "Export"}


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
    def __init__(self, settings: Settings | None = None, session: Session | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumHeight(700)
        self.resize(1280, 960)
        self.settings = settings or Settings()
        self.runner = JobRunner(self)
        self._library: MasterLibrary | None = None
        self._profile: InstrumentProfile | None = None
        self._built: dict[str, StepScreen] = {}  # real screens that replaced their stubs
        self.current = 0
        self._run_queue: list[str] = []
        self._checks_timer = QTimer(self)
        self._checks_timer.setSingleShot(True)
        self._checks_timer.setInterval(150)
        self._checks_timer.timeout.connect(self.refresh_checks)
        self.session = session or self._initial_session()

        central = QWidget()
        central.setObjectName("central")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.topbar = self._build_topbar()
        outer.addWidget(self.topbar)

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(32, 16, 32, 16)
        pl.setSpacing(0)
        self.ribbon_box = QWidget()  # the step ribbon belongs to Reduce only
        rb = QVBoxLayout(self.ribbon_box)
        rb.setContentsMargins(0, 0, 0, 0)
        rb.setSpacing(0)
        self.ribbon = Ribbon([RibbonStep(s.title, s.subtitle) for s in STAGES])
        self.ribbon.step_clicked.connect(self.open_step)
        rb.addWidget(self.ribbon)
        rb.addWidget(legend_row())
        pl.addWidget(self.ribbon_box)

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
        self.plan_page = placeholder_page(
            "Plan", "Plan an observing night: targets, reference stars and times. Not built yet.")
        self.acquisition_page = placeholder_page(
            "Acquisition", "Capture the lights and calibration frames for a session. Not built yet.")
        self.stack.addWidget(self.plan_page)
        self.stack.addWidget(self.acquisition_page)
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

        self.tabs.setCurrentIndex(TABS.index("reduce"))
        self.tabs.currentChanged.connect(self._on_tab)
        self._attach_session(self.session)
        self.warmed.connect(self._on_warmed)
        self.ribbon.painted.connect(self._after_first_paint)

    def _after_first_paint(self) -> None:
        """Build the Files screen once the shell is on screen (keeps startup under 1 s)."""
        self.ribbon.painted.disconnect(self._after_first_paint)
        QTimer.singleShot(0, lambda: self.files_screen)
        threading.Thread(target=self._warm_imports, name="warm-imports", daemon=True).start()

    def _warm_imports(self) -> None:
        """Import numpy/scipy/pyqtgraph off the GUI thread; the screens are built when done."""
        import importlib

        for mod in ("numpy", "scipy.ndimage", "pyqtgraph", "starex_reduce.core.geometry_stage",
                    "starex_reduce.core.extract_stage", "starex_reduce.core.wavelength_stage",
                    "scipy.optimize", "starex_reduce.core.response_stage", "scipy.interpolate",
                    "starex_reduce.ui.widgets.image_viewer"):
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
        from starex_reduce.core.export import site_from_frames

        saved = dict(self.settings["site"] or {})
        if saved.get("from_headers", True):
            paths = [Path(f["path"]) for side in ("target", "reference") for f in self.session.inputs[side]["lights"]]
            paths += [self.library.abspath(m) for m in self.library.masters]
            hdr = site_from_frames(paths)
            if hdr is not None:
                return {**saved, **{k: v for k, v in hdr.items() if v is not None}, "source": "headers"}
            return {**saved, "source": "settings" if saved.get("lat") is not None else "none"}
        return {**saved, "source": "typed"}

    @property
    def profile(self) -> InstrumentProfile:
        if self._profile is None:
            from starex_reduce.core.instrument import InstrumentProfile

            self._profile = InstrumentProfile.load()
        return self._profile

    @property
    def library(self) -> MasterLibrary:
        if self._library is None:
            from starex_reduce.core.masters import MasterLibrary

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
        self.tabs = TabRow([TAB_TITLES[key] for key in TABS])
        lay.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)

        self.session_chip = QToolButton()
        self.session_chip.setObjectName("sessionChip")
        self.session_chip.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.session_menu = QMenu(self)
        self.session_menu.aboutToShow.connect(self._fill_session_menu)
        self.session_chip.setMenu(self.session_menu)
        lay.addWidget(self.session_chip)
        self.settings_btn = QToolButton()
        self.settings_btn.setObjectName("sessionChip")
        self.settings_btn.setText("⚙")
        self.settings_btn.setToolTip("Settings (Ctrl+,)")
        self.settings_btn.clicked.connect(lambda: self.open_settings())
        lay.addWidget(self.settings_btn)
        self.run_all_btn = QPushButton("Run all")
        self.run_all_btn.setProperty("role", "primary")
        self.run_all_btn.setToolTip("Run every step that is out of date, in order (Ctrl+Enter). "
                                    "Shift+click or Ctrl+Shift+Enter re-runs every step.")
        self.run_all_btn.clicked.connect(self._run_all_clicked)
        lay.addWidget(self.run_all_btn)
        return bar

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
        self.runner.cancel_all()
        self.session = session
        session.on_log(self._append_log)
        session.on_status(self.refresh_ribbon)
        folder = session.folder
        self.session_chip.setText(f"Session …\\{folder.name} ▾" if folder else "No session ▾")
        self.session_chip.setToolTip(str(folder) if folder else "Choose New or Open")
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
            self.acquisition_page.stop_watch()
            self.acquisition_page.load_session()
        self.refresh_ribbon()

    def _log_panels(self) -> list:
        extra = [w.log for w in (self.export_screen, self.plan_page, self.acquisition_page) if hasattr(w, "log")]
        return [s.log for s in self.screens] + extra

    def _append_log(self, line: str) -> None:
        for panel in self._log_panels():
            panel.append(line)

    def _fill_session_menu(self) -> None:
        m = self.session_menu
        m.clear()
        m.addAction("New session…", self.new_session)
        m.addAction("Open session…", self.open_session)
        recent = m.addMenu("Recent")
        items = [p for p in self.settings["recent_sessions"] if Session.is_session(Path(p))]
        for p in items:
            recent.addAction(p, lambda path=p: self.load_session(Path(path)))
        recent.setEnabled(bool(items))

    def new_session(self, keep_inputs: bool = False) -> None:
        parent = QFileDialog.getExistingDirectory(
            self, "Folder for the new session (a dated subfolder is made)")
        if parent:
            folder = Path(parent) / self._night_name(keep_inputs)
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.report_error(f"Cannot create the session folder {folder}: {exc}")
                return
            if Session.is_session(folder):
                session = Session.load(folder)
            elif keep_inputs:
                self.session.folder = folder
                folder.mkdir(parents=True, exist_ok=True)
                self.session.save()
                session = self.session
            else:
                session = Session.create(folder)
            self._remember(folder)
            self._attach_session(session)
            session.log(f"Session {folder}")

    def _night_name(self, keep_inputs: bool) -> str:
        """Folder name: the observing night (local noon to noon) of the lights already chosen, else tonight's
        (before noon that is last night), so the night's Acquisition session and its reduction match."""
        from starex_reduce.core.session import night_of

        if keep_inputs:
            from starex_reduce.core.fitsio import read_header

            for side in ("target", "reference"):
                for p in self.session.used_lights(side)[:1]:
                    try:
                        if read_header(Path(p)).get("DATE-OBS"):
                            return night_of(str(read_header(Path(p))["DATE-OBS"]))
                    except (OSError, ValueError):
                        pass
        return night_of()

    def open_session(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open a session folder")
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

    def inputs_changed(self) -> None:
        """Files screen edited inputs in place: re-derive status, persist, update the top bar."""
        self.session.changed()
        self.session.save()
        self.refresh_ribbon()

    def require_folder(self) -> bool:
        """Outputs need a session folder; offer to create one."""
        if self.session.folder is None:
            self.session.log("Choose a folder for this session first (Session ▾ → New session)")
            self.new_session(keep_inputs=True)
        return self.session.folder is not None

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
        return TABS[self.tabs.currentIndex()]

    def show_tab(self, name: str) -> None:
        self.tabs.setCurrentIndex(TABS.index(name))

    def _on_tab(self, idx: int) -> None:
        name = TABS[idx]
        self.ribbon_box.setVisible(name == "reduce")
        if name == "export":
            self.stack.setCurrentWidget(self.ensure_export())
        elif name == "reduce":
            self.stack.setCurrentWidget(self.screens[self.current])
        elif name == "plan":
            self.stack.setCurrentWidget(self.ensure_plan())
        else:
            self.stack.setCurrentWidget(self.ensure_acquisition())
        self.refresh_ribbon()

    def refresh_ribbon(self) -> None:
        on_reduce = self.tab_name() == "reduce"
        keys = self.session.compute_keys()
        files = self._built.get("files")
        files_problem = files.ribbon_problem() if files is not None else None  # type: ignore[attr-defined]
        for i, sid in enumerate(STAGE_IDS):
            st = self.session.status[sid]
            done = [img for img in IMAGES if self.session.image_done(sid, img, keys)]
            if st.status in (Status.MISSING, Status.ERROR):
                state, text = StepState.MISSING, st.message or "Missing data"
            elif sid == "files" and files_problem and st.status != Status.VALID:
                state, text = StepState.MISSING, files_problem
            elif st.status == Status.VALID:
                state, text = StepState.DONE, self._done_text(sid)
            elif done:
                todo = next(img for img in IMAGES if img not in done)
                state, text = StepState.NOT_RUN, f"{done[0]} done · run {todo}"
            else:
                state, text = StepState.NOT_RUN, "Needs re-run" if st.key else "Not run"
            if on_reduce and i == self.current and state != StepState.MISSING:
                state = StepState.CURRENT
            if self.runner.is_busy(sid):
                text = "Running…"
            self.ribbon.set_step(i, state=state, status=text)
            screen = self.screens[i]
            screen.header.set_output_ready(st.status == Status.VALID)
            prev_missing = next(
                (s for s in STAGE_IDS[:i] if self.session.status[s].status != Status.VALID), None)
            screen.show_missing(
                f"Missing data: run {STAGES[STAGE_IDS.index(prev_missing)].title} first."
                if prev_missing else None)
        if on_reduce:
            self._checks_timer.start()  # coalesced: header reads stay off the hot path and out of start-up

    def refresh_checks(self) -> None:
        """Consistency notes between Plan, Files, the frames and the airmass inputs, on every step."""
        from starex_reduce.core.identity import checks
        from starex_reduce.core.response_stage import params_of as response_params

        site = self.site()
        airmass_on = bool(response_params(self.session, self.profile).get("airmass", True))
        items = checks(self.session, site.get("lat") is not None and site.get("lon") is not None, airmass_on)
        for screen in self.screens:
            if hasattr(screen, "show_checks"):
                screen.show_checks(items)
        files = self._built.get("files")
        if files is not None:
            for c in files.cards.values():  # type: ignore[attr-defined]
                c._show_plan_hint()  # the Plan may have changed on its own tab

    def _done_text(self, sid: str) -> str:
        st = self.session.status[sid]
        if sid == "files":
            n = [len(self.session.used_lights(side)) for side in ("target", "reference")]
            return f"2 stacks · {n[0]} + {n[1]} frames"
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
        if sid == "files":
            from starex_reduce.core.files_stage import specs_for, stack_job, tolerances

            specs, problems = specs_for(self.session, images, self.library,
                                        tolerances(self.settings["tolerances"]))
            if problems:
                for p in problems:
                    self.session.log(f"Cannot stack: {p}")
                return False
            if not self.require_folder():
                return False
            self.session.clear_problem(sid)
            self.runner.submit(sid, stack_job, specs, self.library, self.session.folder,
                               log=self.session.log)
            return True
        if sid == "geometry":
            from starex_reduce.core.geometry_stage import geometry_job
            from starex_reduce.core.geometry_stage import specs_for as geometry_specs

            gspecs, problems = geometry_specs(self.session, images, self.profile, self.library)
            for p in problems:
                self.session.log(f"Cannot run Geometry: {p}")
            if problems or not gspecs:
                return False
            self.session.clear_problem(sid)
            self.runner.submit(sid, geometry_job, gspecs, log=self.session.log)
            return True
        if sid == "response":
            from starex_reduce.core.response_stage import response_job
            from starex_reduce.core.response_stage import specs_for as response_specs

            rspecs, problems = response_specs(self.session, self.profile, {**self.settings.data, "site": self.site()})
            for p in problems:
                self.session.log(f"Cannot run Response: {p}")
            if problems or not rspecs:
                return False
            self.session.clear_problem(sid)
            self.runner.submit(sid, response_job, rspecs[0], log=self.session.log)
            return True
        if sid == "wavelength":
            from starex_reduce.core.wavelength_stage import specs_for as wavelength_specs
            from starex_reduce.core.wavelength_stage import wavelength_job

            wspecs, problems = wavelength_specs(self.session, images, self.profile)
            for p in problems:
                self.session.log(f"Cannot run Wavelength: {p}")
            if problems or not wspecs:
                return False
            self.session.clear_problem(sid)
            self.runner.submit(sid, wavelength_job, wspecs[0], log=self.session.log)
            return True
        if sid == "extract":
            from starex_reduce.core.extract_stage import extract_job
            from starex_reduce.core.extract_stage import specs_for as extract_specs

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
            if not getattr(self, "_run_force", False) and self._stage_current(sid):
                continue  # still valid after the steps before it ran
            if self.run_stage(sid):
                return
            self._run_queue.clear()

    # ---- job results (slots are stage ids, or "<kind>.<side>" / "masters" helpers) -----------
    def _on_progress(self, slot: str, f: float) -> None:
        if slot in STAGE_IDS:
            self.screens[STAGE_IDS.index(slot)].header.set_progress(f)
            self.ribbon.set_step(STAGE_IDS.index(slot), status=f"Running… {f:.0%}")
        elif slot == "masters":
            self.files_screen.masters_progress(f)

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
        if kind == "acq":
            self.ensure_acquisition().on_job(slot, result)
            return
        if kind == "scanacq":
            self.files_screen.suggestion.scan_finished(result)  # type: ignore[arg-type]
            return
        if kind in ("preview", "auto"):  # "<kind>.<screen>.<image>" → screen.on_<kind>(image, result)
            screen_id, _, image = side.partition(".")
            getattr(self.ensure_screen(screen_id), f"on_{kind}")(image, result)
            return
        cards = self.files_screen.cards
        if kind == "scan":
            cards[side].on_scanned(result)  # type: ignore[arg-type]
        elif kind == "peaks":
            cards[side].on_peaks(result)  # type: ignore[arg-type]
        elif kind == "simbad":
            cards[side].on_simbad(result)  # type: ignore[arg-type]
        elif kind == "masters":
            self.files_screen.masters_done(result)  # type: ignore[arg-type]
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
        self.refresh_ribbon()
        self._run_next()

    def _on_failed(self, slot: str, msg: str) -> None:
        first = msg.splitlines()[0]
        if slot in STAGE_IDS:
            self.session.mark_error(slot, first)
            self._run_queue.clear()
        elif slot == "masters":
            self.files_screen.masters_done(None, first)
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
        elif kind_of(slot) == "scanacq":
            self.files_screen.suggestion.scan_finished(None)
        elif slot == "plan.simbad":
            self.ensure_plan().on_simbad({"query": "", "found": False, "offline": True, "error": first})
        elif slot.startswith("simbad."):
            self.files_screen.cards[slot.split(".")[1]].on_simbad(
                {"query": "", "found": False, "offline": True, "error": first})
        self.session.log(f"{slot} failed: {first}")

    def _on_cancelled(self, slot: str) -> None:
        if slot in STAGE_IDS:
            self.session.log(f"{STAGES[STAGE_IDS.index(slot)].title}: stopped")
            self._run_queue.clear()
            self.refresh_ribbon()
        elif slot == "masters":
            self.files_screen.masters_done(["Create masters: stopped"])
        elif kind_of(slot) == "scanacq":
            self.files_screen.suggestion.scan_finished(None)

    # ---- keyboard, window, settings, errors ---------------------------------------------------
    SHORTCUTS = (
        ("Ctrl+Return", "Run all steps that are out of date", "run_all"),
        ("Ctrl+Shift+Return", "Re-run every step", ""),
        ("F5", "Run the current step (both images)", "run_current"),
        ("Esc", "Stop the running step", "stop_current"),
        ("Ctrl+1 … Ctrl+5", "Go to step 1 … 5", ""),
        ("Alt+Left / Alt+Right", "Previous / next step", ""),
        ("Ctrl+PgUp / Ctrl+PgDown", "Previous / next tab", ""),
        ("Ctrl+N / Ctrl+O", "New / open session", ""),
        ("Ctrl+,", "Settings", "open_settings"),
    )

    def _install_shortcuts(self) -> None:
        def sc(keys: str, fn) -> None:
            QShortcut(QKeySequence(keys), self, activated=fn)

        sc("Ctrl+Return", lambda: self.run_all())
        sc("Ctrl+Enter", lambda: self.run_all())
        sc("Ctrl+Shift+Return", lambda: self.run_all(True))
        sc("Ctrl+Shift+Enter", lambda: self.run_all(True))
        sc("F5", self.run_current)
        sc("Esc", self.stop_current)
        for i in range(len(STAGES)):
            sc(f"Ctrl+{i + 1}", lambda i=i: self.open_step(i))
        sc("Alt+Left", lambda: self.open_step(max(0, self.current - 1)))
        sc("Alt+Right", lambda: self.open_step(min(len(STAGES) - 1, self.current + 1)))
        sc("Ctrl+PgUp", lambda: self.tabs.setCurrentIndex(max(0, self.tabs.currentIndex() - 1)))
        sc("Ctrl+PgDown", lambda: self.tabs.setCurrentIndex(min(len(TABS) - 1, self.tabs.currentIndex() + 1)))
        sc("Ctrl+N", lambda: self.new_session())
        sc("Ctrl+O", self.open_session)
        sc("Ctrl+,", self.open_settings)

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

    def open_settings(self) -> bool:
        from .settings_dialog import SettingsDialog

        before = (self.settings.masters_dir(), dict(self.settings["tolerances"] or {}))
        dlg = SettingsDialog(self.settings, self)
        if not dlg.exec():
            return False
        if self.settings.masters_dir() != before[0]:
            self._library = None
            self.session.log(f"Master library: {self.settings.masters_dir()}")
        if "files" in self._built and (self.settings.masters_dir(), self.settings["tolerances"]) != before:
            self.files_screen.refresh_library()
            self.files_screen._replan()
        return True

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
                from starex_reduce.core.settings import app_dir

                box.setInformativeText(f"The details are in error.log in {app_dir()}.")
                folder = box.addButton("Open log folder", QMessageBox.ButtonRole.ActionRole)
                folder.clicked.disconnect()  # keep the dialog open after opening the folder
                folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_dir()))))
            self.last_error_box = box
            box.exec()

    def closeEvent(self, e: QCloseEvent) -> None:
        if hasattr(self.acquisition_page, "stop_watch"):
            self.acquisition_page.stop_watch()
        self.runner.cancel_all()
        self.runner.wait(3000)
        self.session.save()
        self.settings["window"] = {"geometry": bytes(self.saveGeometry().toBase64()).decode("ascii")}
        self.settings.save()
        super().closeEvent(e)
