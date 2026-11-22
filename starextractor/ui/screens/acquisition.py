"""Acquisition tab: per set (Target, Reference) watch the capture folder, analyse each new sub in a worker, keep
the good subs, recommend exposure / number of subs, chart the stack SNR and stack the ticked subs; a third tab
holds the Pre Processing settings (docs/ACQUISITION_TAB.md). Pixels are only read by core/subs.py and the stack
inside JobRunner jobs; the folder is polled on a plain thread that only enqueues."""
from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from starextractor.core import acq_sets, coords
from starextractor.core import subs as sb
from starextractor.core.extract import Band, Bands
from starextractor.core.watcher import FolderWatcher

from .. import dialogs, theme
from ..log_panel import LogPanel
from ..retheme import dark_style
from ..widgets.bands import BandOverlay
from ..widgets.common import Card, InfoButton, Segmented, StatusLine, label
from ..widgets.image_viewer import ImageViewer
from ..widgets.plot_guard import pg

if TYPE_CHECKING:
    from ..main_window import MainWindow

ROLES = ("target", "reference")
ROLE_TITLE = {"target": "Target", "reference": "Reference"}
IMAGE_OF = {"target": "Target", "reference": "Reference"}
VIEW_CACHE = 3  # sub images (whole frames) kept in memory
IMG_ZOOMS = (("Full height", "full"), ("Fit to bands", "bands"), ("×2", 2), ("×4", 4), ("×8", 8), ("×16", 16))
FIT_SLACK = 40  # rows shown beyond the sky bands by "Fit to bands"
COLS = ("", "Sub", "Exp (s)", "Peak", "SNR", "Flags", "OBJECT (info)")
COORD_SOURCES = ("simbad", "header", "manual")
FITS_SUFFIXES = (".fit", ".fits", ".fts")
LOGBOOK_DELAY_MS = 1000


def _default_set() -> dict[str, Any]:
    return {"folder": "", "coord_source": "simbad", "ra": "", "dec": "", "simbad": {}, "notes": "", "lobes": "auto"}


LOBE_CHOICES = (("Auto", "auto"), ("Single peak only", "single"), ("Include all lobes", "all"))
DOUBLE_TEXT = ("Double-lobed profile (defocus?). Spectrum spans both lobes: rows {a}–{b}. "
               "A sharper focus gives more signal per row.")


STATE_FILE = "acquisition.json"  # in Stacks/<night>/, next to that night's stacks and acquisition_sets.json


def stack_name(folder: Path, name: str, role: str, raw_dir: str) -> str:
    """<Name>_target_S.fit / <Name>_ref_S.fit; a different set (another name or raw folder in the header) that
    would get an existing name gets _2, _3, … Re-stacking the same set replaces its file."""
    from starextractor.core.fitsio import read_header
    from starextractor.core.naming import stack_filename, with_suffix_number

    base = folder / stack_filename(name, role)
    n = 1
    while True:
        cand = with_suffix_number(base, n)
        if not cand.exists():
            return cand.name
        try:
            h = read_header(cand)
        except (OSError, ValueError):
            h = {}
        if str(h.get("SXNAME") or "") == name and str(h.get("SXRAWDIR") or "") == raw_dir:
            return cand.name
        n += 1


def first_fits(folder: str) -> Path | None:
    try:
        files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in FITS_SUFFIXES and p.is_file())
    except OSError:
        return None
    return files[0] if files else None


def calibration_text(plan: Any) -> str:
    """"Dark 240 s − Bias 1 ms" from the plan the stack used."""
    from starextractor.core.masters import fmt_exp

    parts = []
    if plan.dark is not None:
        parts.append(f"Dark {fmt_exp(plan.dark.exptime)}")
    if plan.bias is not None and (plan.subtract_bias_level or plan.mode != "exact"):
        parts.append(f"Bias {fmt_exp(plan.bias.exptime)}")
    text = " − ".join(parts) or "no calibration"
    return text + (" ÷ Flat" if plan.flat is not None else "")


def method_text(params: Any) -> str:
    if params.method == "sigma_clip_mean":
        return f"sigma-clip mean {params.sigma:.1f} σ"
    return {"median": "median", "mean": "mean"}.get(params.method, params.method)


class _Bridge(QObject):
    files = Signal(list, str)  # watcher thread → UI thread: (paths, set of the watcher that found them)


class SetTabs(QWidget):
    """"Collecting for [Target] [Reference]" and, at the right, [Pre Processing settings]: tabs attached to the
    box below. `index()` is the set (0 Target, 1 Reference), also while the settings tab is open."""

    changed = Signal(int)  # set chosen
    page = Signal(int)  # 0 acquisition, 1 pre processing settings

    def __init__(self) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(label("Collecting for", "muted"))
        lay.addSpacing(6)
        self.group = QButtonGroup(self)
        self.buttons: list[QPushButton] = []
        for i, text in enumerate(("Target", "Reference", "Pre Processing settings")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("acqtab", True)
            self.group.addButton(b, i)
            if i == 2:
                lay.addStretch(1)
            else:
                self.buttons.append(b)
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignBottom)
        self.pre_btn = self.group.button(2)
        for b in self.group.buttons():  # room for the bold text of the selected tab
            f = b.font()
            f.setBold(True)
            b.setMinimumWidth(QFontMetrics(f).horizontalAdvance(b.text()) + 44)
        self.group.button(0).setChecked(True)
        self._set = 0
        self.group.idToggled.connect(self._toggled)

    def _toggled(self, i: int, on: bool) -> None:
        if not on:
            return
        if i < 2:
            changed = i != self._set
            self._set = i
            self.page.emit(0)
            if changed:
                self.changed.emit(i)
        else:
            self.page.emit(1)

    def index(self) -> int:
        return self._set

    def set_index(self, i: int) -> None:
        self.group.button(i).setChecked(True)

    def open_settings(self) -> None:
        self.pre_btn.setChecked(True)

    def on_settings(self) -> bool:
        return self.pre_btn.isChecked()


TAB_STYLE = f"""
QPushButton[acqtab="true"] {{ background: transparent; color: {theme.MUTED}; border: 1px solid transparent;
    border-bottom: 0; border-top-left-radius: 8px; border-top-right-radius: 8px; border-bottom-left-radius: 0;
    border-bottom-right-radius: 0; padding: 8px 18px; margin-bottom: -1px; }}
QPushButton[acqtab="true"]:hover {{ color: {theme.TEXT}; }}
QPushButton[acqtab="true"]:checked {{ background: {theme.CARD}; color: {theme.TEXT}; font-weight: 600;
    border: 1px solid {theme.BLUE}; border-bottom: 1px solid {theme.CARD}; }}
QFrame#acqbox {{ background: {theme.CARD}; border: 1px solid {theme.LINE}; border-radius: 10px;
    border-top-left-radius: 0; }}
"""


class AcquisitionScreen(QWidget):
    def __init__(self, host: MainWindow) -> None:
        super().__init__()
        self.host = host
        self.records: list[sb.SubRecord] = []
        self.rows_by: dict[str, sb.Rows] = {}  # locked per set: target and reference sit on different rows
        self.queue: list[tuple[str, str]] = []  # (path, role) waiting for analysis
        self.busy = False
        self._adu_width = 0
        self._fields_dirty = False  # a row field was changed by the user and not applied yet
        self._discard = False  # the job running when Clear all was pressed: drop its result
        self.watcher: FolderWatcher | None = None
        self.watch_role: str | None = None  # the set whose folder is watched (one at a time)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.bridge = _Bridge()
        self.bridge.files.connect(self.enqueue)
        self.view_role = "target"
        self.name_source = {"target": "typed", "reference": "typed"}
        self.sets: dict[str, dict[str, Any]] = {r: _default_set() for r in ROLES}
        self.analyse = sb.analyse_sub  # replaced in tests by a slow fake
        self.shown_path: str | None = None  # sub picked in the table; None = follow the latest
        self.views: dict[str, dict[str, Any]] = {}  # sub image data by path (a few, newest last)
        self._shown_view: dict[str, Any] | None = None
        self._redetect_role = "target"
        self._loading = False
        self._stack_job: dict[str, Any] | None = None
        self._watch_text = ""
        self._rows_timer = QTimer(self)  # spin-box arrows: apply once the clicking stops
        self._rows_timer.setSingleShot(True)
        self._rows_timer.setInterval(500)
        self._rows_timer.timeout.connect(lambda: self._rows_edited())
        self._log_timer = QTimer(self)  # logbook autosave
        self._log_timer.setSingleShot(True)
        self._log_timer.setInterval(LOGBOOK_DELAY_MS)
        self._log_timer.timeout.connect(lambda: self.save_logbook())
        self._logbook_dirty = False
        self.night = ""  # the night folder in Stacks that holds this tab's state (acquisition.json)
        self.store: dict[str, Any] = {}
        self._store_loaded = False

        self.setStyleSheet(TAB_STYLE)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 16, 0, 16)
        lay.setSpacing(12)
        lay.addWidget(label("Acquisition", "title"))
        lay.addWidget(label("Watch the capture folder while you image. Each new sub is checked for saturation and "
                            "signal to noise and joins the set you are collecting for.", "muted", wrap=True))
        lay.addWidget(self._build_planned_strip())
        head = QVBoxLayout()
        head.setSpacing(0)
        self.collect = SetTabs()
        self.collect.changed.connect(lambda i: self._view(ROLES[i]))
        self.collect.page.connect(self._page)
        head.addWidget(self.collect)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_acq_page())
        from .acq_prep import PrepPage

        self.prep = PrepPage(host, self)
        self.pages.addWidget(self.prep)
        self._loading = True
        self._page(0)
        self._loading = False
        head.addWidget(self.pages)
        lay.addLayout(head)
        lay.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(page)
        self.log = LogPanel()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(self.scroll, 1)
        outer.addWidget(self.log)

    def _build_acq_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(self._build_source_box())
        cols = QHBoxLayout()
        cols.setSpacing(12)
        cols.addWidget(self._build_latest_card(), 3, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_reco_card())
        right.addWidget(self._build_table_card(), 1)
        cols.addLayout(right, 2)
        lay.addLayout(cols)
        low = QHBoxLayout()
        low.setSpacing(12)
        low.addWidget(self._build_snr_card(), 3)
        low.addWidget(self._build_logbook_card(), 2)
        lay.addLayout(low)
        return w

    # ---- construction ------------------------------------------------------------------------------
    def _settings(self) -> dict[str, Any]:
        from starextractor.core.settings import DEFAULTS

        return {**DEFAULTS["acq"], **(self.host.settings["acq"] or {})}

    def _save_settings(self, **kw: Any) -> None:
        self.host.settings["acq"] = {**self._settings(), **kw}
        self.host.settings.save()

    def _build_source_box(self) -> QFrame:
        box = QFrame()
        box.setObjectName("acqbox")
        body = QVBoxLayout(box)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(10)
        s = self._settings()
        self.folders: dict[str, QLineEdit] = {}
        self.names: dict[str, QLineEdit] = {}
        self.coord_srcs: dict[str, Segmented] = {}
        self.ras: dict[str, QLineEdit] = {}
        self.decs: dict[str, QLineEdit] = {}
        self.ident_notes: dict[str, StatusLine] = {}
        self.plan_tags: dict[str, QLabel] = {}
        self.folder_stack = QStackedWidget()
        self.ident_stack = QStackedWidget()
        for role in ROLES:
            self.folder_stack.addWidget(self._folder_row(role))
            self.ident_stack.addWidget(self._ident_rows(role))
        self.folder_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout()  # Row 1: Folder … Start watching / Pause, Clear all
        row.addWidget(label("Folder", "muted"))
        row.addWidget(self.folder_stack, 1)
        self.watch_btn = QPushButton("Start watching")
        self.watch_btn.setProperty("role", "primary")  # the one amber button of this page
        self.watch_btn.clicked.connect(lambda: self.toggle_watch())
        self.clear_btn = QPushButton("Clear all")
        self.clear_btn.setToolTip("Start with a clean slate: forget every sub, both sets, the locked rows and the "
                                  "set names of this session. Your image files are not touched.")
        self.clear_btn.clicked.connect(lambda: self.clear_all())
        row.addWidget(self.watch_btn)
        row.addWidget(self.clear_btn)
        body.addLayout(row)
        row = QHBoxLayout()  # Row 2: interval, process existing files
        row.addWidget(label("every", "muted"))
        self.interval = QDoubleSpinBox()
        self.interval.setRange(1, 10)
        self.interval.setDecimals(0)
        self.interval.setSuffix(" s")
        self.interval.setValue(float(s["interval_s"]))
        self.interval.setFixedWidth(80)
        self.interval.valueChanged.connect(lambda v: self._save_settings(interval_s=float(v)))
        row.addWidget(self.interval)
        row.addSpacing(12)
        self.existing = QCheckBox("Process existing files")
        self.existing.setChecked(bool(s["process_existing"]))
        self.existing.toggled.connect(lambda on: self._save_settings(process_existing=on))
        row.addWidget(self.existing)
        row.addWidget(label("On: Start watching also loads what is already in the folder.", "muted",
                            style="font-size: 12px;"))
        row.addStretch(1)
        self.role_hint = label("", "chip-warn")
        self.role_hint.hide()
        row.addWidget(self.role_hint)
        body.addLayout(row)
        self.watch_status = StatusLine("off", "Not watching.")
        body.addWidget(self.watch_status)
        body.addWidget(self.ident_stack)  # Rows 3-4: name and coordinates of the selected set
        return box

    def _folder_row(self, role: str) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        e = QLineEdit()
        e.setPlaceholderText(f"Folder your capture program saves the {ROLE_TITLE[role].lower()} subs to")
        e.editingFinished.connect(lambda r=role: self._folder_typed(r))
        b = QPushButton("Browse…")
        b.clicked.connect(lambda _=False, r=role: self.browse(r))
        row.addWidget(e, 1)
        row.addWidget(b)
        self.folders[role] = e
        return w

    def _ident_rows(self, role: str) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(8)
        name = QLineEdit()
        name.setPlaceholderText(f"{ROLE_TITLE[role]} name")
        name.textEdited.connect(lambda _t, r=role: self._name_typed(r))
        simbad = QPushButton("SIMBAD")
        simbad.setToolTip("Resolve the name with SIMBAD (one online request) and fill the coordinates")
        simbad.clicked.connect(lambda _=False, r=role: self.resolve_simbad(r))
        hdr = QPushButton("Header")
        hdr.setToolTip("Take the name from OBJECT in the first FITS file of this set's folder. The header never "
                       "decides whether a sub is Target or Reference: the tab does.")
        hdr.clicked.connect(lambda _=False, r=role: self.name_from_header(r))
        g.addWidget(label("Name", "muted"), 0, 0)
        g.addWidget(name, 0, 1, 1, 3)
        g.addWidget(simbad, 0, 4)
        g.addWidget(hdr, 0, 5)
        src = Segmented(["SIMBAD", "From header", "Enter by hand"])
        src.changed.connect(lambda i, r=role: self._coord_source_changed(r, i))
        crow = QHBoxLayout()
        crow.addWidget(label("Coordinates from", "muted"))
        crow.addWidget(src)
        crow.addSpacing(12)
        ra, dec = QLineEdit(), QLineEdit()
        ra.setPlaceholderText("hh mm ss.s")
        dec.setPlaceholderText("+dd mm ss")
        for e in (ra, dec):
            e.setAlignment(Qt.AlignmentFlag.AlignRight)
            e.setReadOnly(True)
            e.editingFinished.connect(lambda r=role: self._coords_edited(r))
        crow.addWidget(label("RA", "muted"))
        crow.addWidget(ra, 1)
        crow.addWidget(label("Dec", "muted"))
        crow.addWidget(dec, 1)
        tag = label("from plan", "tag")
        tag.setToolTip("Name and coordinates set by Plan › Load for acquisition")
        tag.hide()
        crow.addWidget(tag, 0, Qt.AlignmentFlag.AlignVCenter)
        self.plan_tags[role] = tag
        g.addLayout(crow, 1, 0, 1, 6)
        note = StatusLine("off", "")
        note.hide()
        g.addWidget(note, 2, 0, 1, 6)
        g.setColumnStretch(1, 1)
        self.names[role], self.coord_srcs[role], self.ras[role], self.decs[role] = name, src, ra, dec
        self.ident_notes[role] = note
        return w

    @property
    def folder(self) -> QLineEdit:
        """The folder field of the set shown."""
        return self.folders[self.view_role]

    def _page(self, i: int) -> None:
        for k in range(self.pages.count()):  # the stack takes the height of the page shown, not the tallest
            pol = QSizePolicy.Policy.Preferred if k == i else QSizePolicy.Policy.Ignored
            self.pages.widget(k).setSizePolicy(QSizePolicy.Policy.Preferred, pol)
        self.pages.setCurrentIndex(i)
        self.pages.adjustSize()
        self.save_logbook()
        if i == 1:
            self.prep.refresh()
        if not self._loading:
            self._state()["tab"] = "pre" if i == 1 else self.view_role
            self.save_state()

    def open_prep(self) -> None:
        self.collect.open_settings()

    def _build_latest_card(self) -> Card:
        card = Card("Sub")
        self.view_mode = Segmented(["ADU profile", "Sub image"])
        self.view_mode.setToolTip("ADU profile: column maximum of the spectrum rows against the linearity limit. "
                                  "Sub image: the rows used for the spectrum and the sky of one sub.")
        self.view_mode.changed.connect(lambda _i: self._show_sub())
        card.head.insertWidget(1, self.view_mode)
        self.latest_note = label("", "mono")
        card.head.insertWidget(2, self.latest_note)
        self.sub_stack = QStackedWidget()
        self.sub_stack.addWidget(self._build_profile_plot())
        self.sub_stack.addWidget(self._build_image_view())
        card.body.addWidget(self.sub_stack)
        rows = QHBoxLayout()  # line 1: what the rows are, Re-detect
        self.rows_chip = label("Rows: found on the first sub", "chip")
        rows.addWidget(self.rows_chip)
        self.move_hint = label("", "chip-warn")
        self.move_hint.hide()
        rows.addWidget(self.move_hint)
        rows.addStretch(1)
        from PySide6.QtWidgets import QComboBox

        self.lobe_mode = QComboBox()
        for text, key in LOBE_CHOICES:
            self.lobe_mode.addItem(text, key)
        self.lobe_mode.setToolTip("How the spectrum rows are found for this set. Auto: one band over both lobes of "
                                  "a defocused (ring-shaped) star, a single peak otherwise. Single peak only: the "
                                  "brightest peak. Include all lobes: every lobe above the threshold. Used by "
                                  "Re-detect and the set's first sub.")
        self.lobe_mode.currentIndexChanged.connect(self._lobe_mode_changed)
        rows.addWidget(self.lobe_mode)
        self.redetect_btn = QPushButton("Re-detect")
        self.redetect_btn.setToolTip("Find the spectrum rows again on the sub shown. With the sub image "
                                      "zoomed in, only the rows in view are searched (finds a faint spectrum).")
        self.redetect_btn.clicked.connect(lambda: self.redetect())
        rows.addWidget(self.redetect_btn)
        card.body.addLayout(rows)
        self.lobe_chip = label("", "chip", wrap=True)  # neutral note: a double-lobed (defocused) profile
        self.lobe_chip.hide()
        card.body.addWidget(self.lobe_chip)
        fields = QHBoxLayout()  # line 2: the rows by hand
        self.row_fields: dict[str, QDoubleSpinBox] = {}
        for key, title, lo, hi in (("row", "Row", 0, 5000), ("half", "± rows", 1, 40), ("gap", "Sky gap", 1, 200),
                                   ("width", "Sky width", 2, 100)):
            f = QDoubleSpinBox()
            f.setRange(lo, hi)
            f.setDecimals(1 if key == "row" else 0)
            f.setFixedWidth(76)
            f.valueChanged.connect(lambda _v: self._field_typed())
            f.editingFinished.connect(self._rows_edited)
            fields.addWidget(label(title, "muted"))
            fields.addWidget(f)
            fields.addSpacing(6)
            self.row_fields[key] = f
        fields.addStretch(1)
        card.body.addLayout(fields)
        return card

    @staticmethod
    def _plot(left: str, bottom: str) -> pg.PlotWidget:
        pw = pg.PlotWidget(enableMenu=False)
        pw.setBackground(theme.CARD)
        pi = pw.getPlotItem()
        pi.hideButtons()
        pi.showGrid(x=True, y=True, alpha=0.12)
        pi.setLabel("left", left)
        pi.setLabel("bottom", bottom)
        return pw

    @staticmethod
    def _legend(pi: pg.PlotItem, offset: tuple[int, int] = (-10, 34)) -> pg.LegendItem:
        return pi.addLegend(offset=offset, labelTextColor=theme.TEXT, brush=pg.mkBrush(theme.BG + "CC"),
                            pen=pg.mkPen(theme.BORDER), labelTextSize="9pt")

    def _build_profile_plot(self) -> pg.PlotWidget:
        self.plot = self._plot("ADU (hot pixels filtered)", "Column (px)")
        self.plot.setMinimumHeight(280)
        pi = self.plot.getPlotItem()
        # ADU and columns are never negative, also when zoomed out; nothing lies above the 16-bit full scale
        pi.vb.setLimits(xMin=0, yMin=0, yMax=1.1 * sb.FULL_SCALE)
        pi.vb.enableAutoRange(x=False)  # x is always the frame's columns, set from the data (_plot_latest)
        pi.vb.sigRangeChanged.connect(lambda *_: self._place_peak_label())
        legend = self._legend(pi)
        self.c_max = pg.PlotDataItem(pen=pg.mkPen(theme.SPECTRUM_TEXT, width=1.2), name="Spectrum rows: column max")
        self.c_sky = pg.PlotDataItem(pen=pg.mkPen(theme.SKY_TEXT, width=1), name="Sky rows: median")
        dark_style(self.c_sky, Qt.PenStyle.DotLine)  # night / red: sky dotted, the spectrum solid
        self.limit_line = pg.InfiniteLine(angle=0, movable=False,
                                          pen=pg.mkPen(theme.AMBER, width=1, style=Qt.PenStyle.DashLine),
                                          label="linearity limit", labelOpts={"position": 0.08, "color": theme.AMBER})
        self.sat_fill = pg.BarGraphItem(x=[], height=[], width=sb.DECIMATE,
                                        brush=pg.mkBrush(theme.with_alpha(theme.SAT, 70)),
                                        pen=pg.mkPen(None))
        self.peak_dot = pg.ScatterPlotItem(size=8, brush=pg.mkBrush(theme.AMBER), pen=pg.mkPen(None))
        self.peak_text = pg.TextItem(color=theme.AMBER, anchor=(0.5, 1.0))
        for it in (self.sat_fill, self.c_sky, self.c_max, self.limit_line, self.peak_dot):
            pi.addItem(it)
        legend.clear()
        legend.addItem(self.c_max, self.c_max.name())
        legend.addItem(self.c_sky, self.c_sky.name())
        # A TextItem's size in data units depends on the zoom; counted in auto-range it shrinks the view each pass
        # (down to ~1e-91, after which a wheel step gives NaN).
        pi.addItem(self.peak_text, ignoreBounds=True)
        return self.plot

    def _build_image_view(self) -> QWidget:
        """One sub: tilt-straightened strip with the spectrum and sky rows (drag to adjust), and its row profile
        with the sky level that is subtracted."""
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)
        zr = QHBoxLayout()
        zr.addWidget(label("Y ZOOM", "eyebrow"))
        self.zoom_chips = Segmented([t for t, _ in IMG_ZOOMS])
        for b in self.zoom_chips.buttons:
            b.setProperty("seg", "only")
        self.zoom_chips.layout().setSpacing(6)
        self.zoom_chips.set_index(1)  # start on the bands; Full height shows every row of the frame
        self.zoom_chips.changed.connect(lambda i: self._img_zoom(IMG_ZOOMS[i][1]))
        zr.addWidget(self.zoom_chips)
        zr.addStretch(1)
        self.img_rows_label = label("", "mono")
        zr.addWidget(self.img_rows_label)
        col.addLayout(zr)
        # whole frame, Y-only zoom/pan with the frame strip, as in Extract
        self.viewer = ImageViewer()
        self.viewer.setMinimumHeight(280)
        self.viewer.rows_changed.connect(lambda *_: self._rows_label())
        self.img_plot = self.viewer.plot
        ivb = self.viewer.vb
        self.band_overlay = BandOverlay(ivb)  # drag a band: the set is re-analysed with the new rows
        self.band_overlay.moved.connect(self._bands_moved)
        self._img_geom: tuple[int, int] | None = None
        self.prof_plot = self._plot("", "")
        self.prof_plot.setFixedWidth(230)
        ppi = self.prof_plot.getPlotItem()
        ppi.hideAxis("left")
        # No bottom axis: like the image, so both view boxes have the same height and top and a row sits at the
        # same pixel in both (with an axis the profile was squeezed and drifted from the image).
        ppi.hideAxis("bottom")
        self.prof_plot.setBackground(theme.BG)
        ppi.vb.invertY(True)
        ppi.vb.setLimits(xMin=0)
        # zoom / pan either plot: the same rows in both; same view-box height, so the same pixel per row
        self._syncing = False

        def sync(src: pg.ViewBox, dst: pg.ViewBox) -> None:
            if self._syncing:
                return
            self._syncing = True
            try:
                dst.setYRange(*src.viewRange()[1], padding=0)
            finally:
                self._syncing = False
        ivb.sigYRangeChanged.connect(lambda *_: sync(ivb, ppi.vb))
        ivb.sigYRangeChanged.connect(lambda *_: self._fit_profile_x())
        ppi.vb.sigYRangeChanged.connect(lambda *_: sync(ppi.vb, ivb))
        ppi.vb.setMouseEnabled(x=False, y=True)
        self._legend(ppi)  # named items join it as they are added
        self.p_prof = pg.PlotDataItem(pen=pg.mkPen(theme.TEXT, width=1.2), name="Row profile")
        self.p_sky = pg.PlotDataItem(pen=pg.mkPen(theme.SKY_TEXT, width=1.2, style=Qt.PenStyle.DashLine),
                                     name="Sky level (subtracted)")
        for it in (self.p_prof, self.p_sky):
            ppi.addItem(it)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(self.viewer, 1)
        row.addWidget(self.prof_plot)
        col.addLayout(row)
        self.img_note = label("", "muted", wrap=True, style="font-size: 12px;")
        col.addWidget(self.img_note)
        return box

    def _rows_label(self) -> None:
        """'rows a–b of N' from the range actually shown (rows a..b inclusive)."""
        y0, y1 = self.viewer.y_range()
        n = self.viewer.shape[0]
        a, b = max(0, round(y0)), min(n, round(y1)) - 1
        self.img_rows_label.setText(f"rows {a}–{b} of {n}")

    def _build_reco_card(self) -> Card:
        card = Card("Recommendation")
        self.reco_state = StatusLine("off", "Waiting for the first sub.")
        card.body.addWidget(self.reco_state)
        self.reco_headroom = label("", wrap=True)
        self.reco_headroom.setToolTip("Assumes the signal above the pedestal grows linearly with exposure time.")
        self.reco_snr = label("", wrap=True)
        self.reco_snr.setToolTip("Per-sub SNR is assumed to grow with the square root of the exposure "
                                 "(signal-limited); the stack SNR is the quadrature sum of the subs.")
        for w in (self.reco_headroom, self.reco_snr):
            card.body.addWidget(w)
        return card

    def _build_table_card(self) -> Card:
        card = Card("Good subs")
        self.table_card = card
        self.set_total = label("", "mono")
        foot = QHBoxLayout()
        foot.addWidget(self.set_total)
        foot.addStretch(1)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setMinimumHeight(260)
        self.table.itemSelectionChanged.connect(self._picked)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._ticked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)
        card.body.addWidget(self.table)
        card.body.addLayout(foot)
        card.body.addWidget(label("Click a sub to show it on the left. Right-click to move it to the other set, "
                                  "exclude it or copy the list.", "muted", wrap=True, style="font-size: 12px;"))
        row = QHBoxLayout()
        self.stack_btn = QPushButton("Stack selected")
        self.stack_btn.setProperty("role", "primary")
        self.stack_btn.setToolTip("Calibrate and stack the ticked subs of this set into Stacks/<night>/ "
                                  "(<Name>_target_S.fit or <Name>_ref_S.fit), with the Pre Processing settings")
        self.stack_btn.clicked.connect(lambda: self.stack_selected())
        self.stack_cancel = QPushButton("Cancel")
        self.stack_cancel.clicked.connect(lambda: self.host.runner.cancel("acq.stack"))
        self.stack_cancel.hide()
        self.stack_progress = QProgressBar()
        self.stack_progress.setRange(0, 1000)
        self.stack_progress.setTextVisible(False)
        self.stack_progress.setFixedSize(120, 8)
        self.stack_progress.hide()
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(lambda: self.open_project_folder())
        for b in (self.stack_btn, self.stack_progress, self.stack_cancel, open_btn):
            row.addWidget(b)
        row.addStretch(1)
        card.body.addLayout(row)
        card.body.addWidget(label("Open folder shows Stacks/<night>, where the stacks are written.", "muted",
                                  wrap=True, style="font-size: 12px;"))
        self.stack_result = QLabel("")
        self.stack_result.setWordWrap(True)
        self.stack_result.setTextFormat(Qt.TextFormat.RichText)
        self.stack_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.stack_result.setOpenExternalLinks(False)
        self.stack_result.linkActivated.connect(self._stack_link)
        self.stack_result.hide()
        card.body.addWidget(self.stack_result)
        self.rows_hint = label("", "muted", wrap=True, style="font-size: 12px;")
        self.rows_hint.setToolTip("The spectrum and sky rows travel inside the stack file (SXROW/SXSKA/SXSKB "
                                  "keywords), so Reduce › Select and 1D Extract find them without the sets file.")
        self.rows_hint.hide()
        card.body.addWidget(self.rows_hint)
        return card

    def _build_logbook_card(self) -> Card:
        card = Card("Notes for this set")
        card.head.insertWidget(1, InfoButton(
            "Free notes for the set shown (weather, seeing, what you changed). Saved with the set in "
            "acquisition_sets.json about a second after you stop typing; Load pair in Reduce › Select copies them "
            "into the pair logbook."))
        self.logbook = QPlainTextEdit()
        self.logbook.setPlaceholderText("Notes for tonight…")
        self.logbook.setMinimumHeight(240)
        self.logbook.textChanged.connect(self._logbook_typed)
        card.body.addWidget(self.logbook, 1)
        self.logbook_note = label("", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.logbook_note)
        return card

    def _build_snr_card(self) -> Card:
        card = Card("Stack SNR")
        card.head.insertWidget(1, InfoButton(
            "Good subs of the selected set. Measured stack SNR = quadrature sum of the per-sub continuum SNRs "
            "(independent noise). Dotted: √N from the first sub. Dashed: projection with the mean per-sub SNR "
            "up to the number of subs that reaches the target."))
        self.transp = label("Transparency or guiding event?", "chip-warn")
        self.transp.hide()
        card.head.addWidget(self.transp)
        card.head.addWidget(label("Target SNR", "muted"))
        self.snr_target = QDoubleSpinBox()
        self.snr_target.setRange(5, 2000)
        self.snr_target.setDecimals(0)
        self.snr_target.setValue(float(self._settings()["snr_target"]))
        self.snr_target.setFixedWidth(90)
        self.snr_target.setToolTip("Stack SNR you want for this set (default 100)")
        self.snr_target.valueChanged.connect(lambda v: (self._save_settings(snr_target=float(v)), self.refresh()))
        card.head.addWidget(self.snr_target)
        self.snr_plot = self._plot("Stack SNR", "Good subs")
        self.snr_plot.setMinimumHeight(240)
        pi = self.snr_plot.getPlotItem()
        pi.vb.setLimits(xMin=0, yMin=0)
        legend = self._legend(pi, offset=(-10, -40))  # bottom right: the curves rise to the top right
        self.s_meas = pg.PlotDataItem(pen=pg.mkPen(theme.TEAL, width=2), symbol="o", symbolSize=6,
                                      symbolBrush=theme.TEAL, symbolPen=None, name="Measured")
        self.s_ref = pg.PlotDataItem(pen=pg.mkPen(theme.MUTED, width=1, style=Qt.PenStyle.DotLine),
                                     name="√N from sub 1")
        self.s_proj = pg.PlotDataItem(pen=pg.mkPen(theme.TEAL, width=1, style=Qt.PenStyle.DashLine),
                                      name="Projection")
        self.s_target = pg.InfiniteLine(angle=0, movable=False,
                                        pen=pg.mkPen(theme.AMBER, width=1, style=Qt.PenStyle.DashLine),
                                        label="target", labelOpts={"position": 0.06, "color": theme.AMBER})
        self.s_reach = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(theme.MUTED, width=1),
                                       label="", labelOpts={"position": 0.9, "color": theme.MUTED})
        for it in (self.s_ref, self.s_proj, self.s_meas, self.s_target, self.s_reach):
            pi.addItem(it)
        legend.clear()
        for it in (self.s_meas, self.s_ref, self.s_proj):
            legend.addItem(it, it.name())
        card.body.addWidget(self.snr_plot)
        self.snr_note = label("", "muted", wrap=True)
        card.body.addWidget(self.snr_note)
        return card

    # ---- session ---------------------------------------------------------------------------------------
    def _state(self) -> dict[str, Any]:
        """This tab's state: Stacks/<night>/acquisition.json (not the Reduce session)."""
        return self.store

    def night_folder(self) -> Path:
        return self.host.settings.paths().stacks / self.night

    def _open_night(self, night: str) -> None:
        import json

        self.night = night
        try:
            d = json.loads((self.night_folder() / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        self.store = d if isinstance(d, dict) else {}

    def save_state(self) -> Path | None:
        """Write acquisition.json into Stacks/<night>/. The Stacks location must exist: an unplugged drive is
        never replaced by a folder somewhere else (the state stays in memory and a warning is logged)."""
        from starextractor.core.datalocs import OK, status
        from starextractor.core.settings import write_json_atomic

        stacks = self.host.settings.paths().stacks
        if status(stacks) != OK:
            if not getattr(self, "_warned_stacks", False):
                self._warned_stacks = True
                self.host.session.log(f"Acquisition: the Stacks folder {stacks} is missing or not writable; "
                                      "nothing is saved until it is back (Settings → Data locations)")
            return None
        self._warned_stacks = False
        path = self.night_folder() / STATE_FILE
        write_json_atomic(path, self.store)
        return path

    def saved_raw_tilt(self) -> float | None:
        """The spectrograph's saved tilt (Geometry, measured after the flips) in detector coordinates: each
        flip mirrors the slope. None when nothing is saved: then it is measured on the first sub."""
        prof = self.host.profile
        if prof.tilt_deg is None:
            return None
        return float(prof.tilt_deg) * (-1.0 if bool(prof.flip_x) != bool(prof.flip_y) else 1.0)

    def apply_orientation(self) -> None:
        """Show columns and rows the way Reduce does (the Flip settings): a view change only, the numbers
        stay in detector pixels."""
        prof = self.host.profile
        self.plot.getPlotItem().vb.invertX(bool(prof.flip_x))
        self.viewer.vb.invertX(bool(prof.flip_x))
        self.viewer.vb.invertY(not prof.flip_y)
        self.prof_plot.getPlotItem().vb.invertY(not prof.flip_y)

    def load_session(self) -> None:
        """Load this tab's state once (the night last used, else tonight). Opening another Reduce session or
        pair does not change it."""
        if self._store_loaded:
            return
        self._store_loaded = True
        from starextractor.core.naming import observing_night

        last = str(self._settings().get("night") or "")
        stacks = self.host.settings.paths().stacks
        self._open_night(last if last and (stacks / last / STATE_FILE).exists() else observing_night())
        self._load_store()

    def _load_store(self) -> None:
        self._loading = True
        self.apply_orientation()
        st = self._state()
        self.records = [sb.SubRecord.from_json(d) for d in st.get("subs", [])]
        self.rows_by = {k: sb.Rows.from_json(v) for k, v in (st.get("rows") or {}).items() if v}
        saved = st.get("sets") or {}
        for role in ROLES:
            d = {**_default_set(), **(saved.get(role) or {})}
            if role == "target" and not d["folder"] and not saved:
                d["folder"] = self._settings()["folder"]  # sessions from before per-set folders
            self.sets[role] = d
            self.folders[role].setText(d["folder"])
            self.names[role].setText(st.get("names", {}).get(role, ""))
            self.name_source[role] = st.get("name_source", {}).get(role, "typed")
            self._show_coords(role)
        tab = st.get("tab", "target")
        self.collect.set_index(ROLES.index(tab) if tab in ROLES else 0)
        self.view_role = ROLES[self.collect.index()]
        self._select_stacks()
        if tab == "pre":
            self.collect.open_settings()
        else:
            self._page(0)
        self.load_logbook()
        self._show_stack_result()
        self._loading = False
        self.update_strip()
        self._show_rows()
        self.refresh()
        self.prep.load_session()

    def persist(self) -> None:
        st = self._state()
        keep = []
        for r in self.records:
            d = r.to_json()
            d["col_max"], d["sky"] = [], []  # plot arrays stay in memory only
            keep.append(d)
        for role in ROLES:
            self.sets[role]["folder"] = self.folders[role].text().strip()
        st.update(subs=keep, rows={k: v.to_json() for k, v in self.rows_by.items()},
                  names={r: self.names[r].text().strip() for r in ROLES}, name_source=dict(self.name_source),
                  sets={r: dict(self.sets[r]) for r in ROLES})
        self._follow_night()
        self.save_state()
        self.save_sets()  # autosave on every change

    def _follow_night(self) -> None:
        """The state belongs to the night of its first sub: a set started on old data moves its (small) state
        files to that night's folder, once."""
        from starextractor.core.naming import observing_night

        first = next((r.time for r in self.records if r.time), "")
        try:
            night = observing_night(first) if first else ""
        except ValueError:
            night = ""
        if not night or night == self.night:
            return
        old = self.night_folder()
        self.night = night
        self._save_settings(night=night)
        for name in (STATE_FILE, acq_sets.FILE_NAME):  # only the files this tab wrote; stacks stay
            (old / name).unlink(missing_ok=True)

    def sets_doc(self) -> dict[str, Any]:
        from starextractor.core.datalocs import to_stored

        idents = {r: {"ra": self.sets[r].get("ra", ""), "dec": self.sets[r].get("dec", ""),
                      "sp_type": (self.sets[r].get("simbad") or {}).get("sp_type", "")
                      if (self.sets[r].get("simbad") or {}).get("found") else "",
                      "notes": self.sets[r].get("notes", ""), "lobe_mode": self._lobes(r)} for r in ROLES}
        root = self.host.settings.paths().root
        stacked = {r: {**st, "file": to_stored(st["file"], root)} if st.get("file") else st
                   for r, st in (self._state().get("stacked") or {}).items()}
        return acq_sets.make_sets(self.records, {r: self.names[r].text().strip() for r in ROLES},
                                  dict(self.name_source), stacked=stacked, idents=idents)

    def save_sets(self) -> Path | None:
        """acquisition_sets.json in Stacks/<night>/, next to the stacks: Reduce › Select reads it for a stack whose
        header lacks the name, position or rows; the notes of each set go into the pair logbook on Load pair."""
        from starextractor.core.datalocs import OK, status

        if not self.records or status(self.host.settings.paths().stacks) != OK:
            return None
        return acq_sets.save_sets(self.night_folder() / acq_sets.FILE_NAME, self.sets_doc())

    # ---- name and coordinates ---------------------------------------------------------------------------
    def _name_typed(self, role: str) -> None:
        self.name_source[role] = "typed"
        self.persist()
        self.refresh_link()

    def resolve_simbad(self, role: str) -> None:
        from starextractor.core.simbad import simbad_job

        name = self.names[role].text().strip()
        note = self.ident_notes[role]
        if not name:
            note.set("warn", "Type a name first.")
            note.show()
            return
        note.set("off", f"Asking SIMBAD for “{name}”…")
        note.show()
        self.host.runner.submit(f"acq.simbad.{role}", simbad_job, name)

    def on_simbad(self, role: str, res: dict[str, Any]) -> None:
        note = self.ident_notes[role]
        self.sets[role]["simbad"] = res
        self.sets[role]["from_plan"] = False
        if res.get("found"):
            self.sets[role]["coord_source"] = "simbad"
            sp = f" · {res['sp_type']}" if res.get("sp_type") else ""
            note.set("ok", f"Resolved by SIMBAD as {res.get('main_id') or res['query']}{sp}.")
            self.host.session.log(f"Acquisition: SIMBAD {res['query']} = {res.get('main_id')}")
        elif res.get("offline"):
            note.set("warn", "SIMBAD could not be reached (offline?). Pick “From header” or enter the coordinates "
                             "by hand.")
        else:
            note.set("warn", f"“{res.get('query', '')}” is not known to SIMBAD.")
        note.show()
        self._show_coords(role)
        self.persist()

    def _header_of(self, role: str) -> dict[str, Any] | None:
        from starextractor.core.fitsio import read_header

        path = first_fits(self.folders[role].text().strip())
        if path is None:
            return None
        try:
            return read_header(path)  # header cards only, no pixels
        except (OSError, ValueError):
            return None

    def name_from_header(self, role: str) -> bool:
        """The name only, from OBJECT of the first FITS file in this set's folder. Never assigns a set."""
        h = self._header_of(role)
        note = self.ident_notes[role]
        obj = str((h or {}).get("OBJECT") or "").strip()
        if not obj:
            note.set("warn", "No OBJECT in the first FITS header of this set's folder." if h is not None
                     else "No FITS file in this set's folder yet.")
            note.show()
            return False
        self.names[role].setText(obj)
        self.name_source[role] = "header"
        note.set("ok", f"Name from OBJECT in the header: {obj}")
        note.show()
        self.persist()
        self.refresh_link()
        return True

    def _coord_source_changed(self, role: str, idx: int) -> None:
        if self._loading:
            return
        d = self.sets[role]
        d["coord_source"] = COORD_SOURCES[idx]
        d["from_plan"] = False
        note = self.ident_notes[role]
        note.hide()
        if d["coord_source"] == "header":
            from starextractor.core.files_stage import header_coords
            from starextractor.core.frames import LIGHT, load_frame

            path = first_fits(self.folders[role].text().strip())
            ra, dec = header_coords([load_frame(path, LIGHT)]) if path is not None else (None, None)
            if ra is None:
                note.set("err", "The first FITS file of this set's folder has no RA/Dec in its header.")
                note.show()
            else:
                d.update(ra=coords.format_ra(ra), dec=coords.format_dec(dec))
        self._show_coords(role)
        self.persist()

    def _show_coords(self, role: str) -> None:
        d = self.sets[role]
        src = d.get("coord_source", "simbad")
        res = d.get("simbad") or {}
        if src == "simbad" and res.get("found"):
            d.update(ra=coords.format_ra(res["ra"]), dec=coords.format_dec(res["dec"]))
        cs = self.coord_srcs[role]
        cs.blockSignals(True)
        cs.group.blockSignals(True)
        cs.set_index(COORD_SOURCES.index(src) if src in COORD_SOURCES else 0)
        cs.group.blockSignals(False)
        cs.blockSignals(False)
        for e, key in ((self.ras[role], "ra"), (self.decs[role], "dec")):
            e.setText(d.get(key, ""))
            e.setReadOnly(src != "manual")
        self.plan_tags[role].setVisible(bool(d.get("from_plan")))

    def _coords_edited(self, role: str) -> None:
        if self._loading or self.sets[role].get("coord_source") != "manual":
            return
        ra, dec = self.ras[role].text().strip(), self.decs[role].text().strip()
        note = self.ident_notes[role]
        try:
            if ra:
                coords.parse_ra(ra)
            if dec:
                coords.parse_dec(dec)
        except ValueError as exc:
            note.set("err", f"{exc}. Use e.g. RA 18 36 56.3 and Dec +38 47 01.")
            note.show()
            return
        note.hide()
        if (ra, dec) != (self.sets[role].get("ra"), self.sets[role].get("dec")):
            self.sets[role]["from_plan"] = False
            self.plan_tags[role].hide()
        self.sets[role].update(ra=ra, dec=dec)
        self.persist()

    # ---- watching (one set at a time) ------------------------------------------------------------------------
    def _folder_typed(self, role: str) -> None:
        if self._loading:
            return
        self.sets[role]["folder"] = self.folders[role].text().strip()
        if role == "target":
            self._save_settings(folder=self.sets[role]["folder"])
        self.persist()

    def browse(self, role: str | None = None) -> None:
        role = role or self.view_role
        d = dialogs.existing_directory(self, f"{ROLE_TITLE[role]} folder to watch", self.folders[role].text())
        if d:
            self.folders[role].setText(d)
            self._folder_typed(role)

    def watching(self) -> bool:
        return self._thread is not None

    def toggle_watch(self) -> None:
        """Start watching the shown set's folder, or pause it. Starting another set's folder pauses the one
        being watched: only one set is collected at a time."""
        role = self.view_role
        if self.watching():
            same = self.watch_role == role
            self.stop_watch()
            if same:
                return
        folder = Path(self.folders[role].text().strip())
        if not self.folders[role].text().strip() or not folder.is_dir():
            self.watch_status.set("warn", f"Choose an existing {ROLE_TITLE[role]} folder first.")
            return
        self.sets[role]["folder"] = str(folder)
        self.watcher = FolderWatcher(folder, "", self.existing.isChecked(), self.interval.value())
        self.watch_role = role
        self._stop = threading.Event()
        emit = self.bridge.files.emit
        self._thread = threading.Thread(target=self.watcher.run, args=(self._stop, lambda f, r=role: emit(f, r)),
                                        name="acq-watch", daemon=True)
        self._thread.start()
        self._watch_text = f"Watching {folder} every {self.interval.value():.0f} s."
        self._update_watch_btn()
        self.watch_status.set("ok", self._watch_text)
        self.host.session.log(f"Acquisition: watching {folder} for the {ROLE_TITLE[role]} set"
                              + (" (existing files first)" if self.existing.isChecked() else ""))
        self.persist()

    def stop_watch(self) -> None:
        was = self.watching()
        self._stop.set()
        self._thread = None
        self.watch_role = None
        self._update_watch_btn()
        if was:
            self.watch_status.set("off", "Paused.")

    def _update_watch_btn(self) -> None:
        self.watch_btn.setText("Pause" if self.watching() and self.watch_role == self.view_role
                               else "Start watching")

    def clear_all(self, confirm: bool = True) -> bool:
        """Forget everything collected in this session (subs of both sets, rows, names). Files on disk stay."""
        if confirm and (self.records or self.queue or any(e.text().strip() for e in self.names.values())):
            ask = QMessageBox.question(
                self, "Clear all", f"Forget all {len(self.records)} subs, both sets, the locked rows and the set "
                "names? Watching is paused. The image files are not touched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if ask != QMessageBox.StandardButton.Yes:
                return False
        if self.watching():
            self.stop_watch()
        self._discard = self.busy  # a sub still being analysed comes back later: ignore it
        self.queue.clear()
        self.records.clear()
        self.rows_by.clear()
        self.views.clear()
        self._shown_view = None
        self.shown_path = None
        self._rows_timer.stop()
        for role in ROLES:
            self.names[role].clear()
            self.name_source[role] = "typed"
            self.ident_notes[role].hide()
        self.collect.set_index(0)
        self.role_hint.hide()
        self.move_hint.hide()
        for role in ROLES:
            self.sets[role]["notes"] = ""
        self.load_logbook()
        self.persist()
        (self.night_folder() / acq_sets.FILE_NAME).unlink(missing_ok=True)  # it would still list the old subs
        self._show_rows()
        self.refresh()
        self.watch_status.set("off", "Cleared. Not watching.")
        self.host.session.log("Acquisition: cleared (all subs, sets, rows and names)")
        for role in ROLES:
            self.sets[role]["from_plan"] = False
            self.plan_tags[role].hide()
        self._set_link(None)  # the names are gone: no planned pair is loaded any more
        return True

    # ---- the planned pair (Plan › Load for acquisition) ------------------------------------------------------
    def _build_planned_strip(self) -> QFrame:
        strip = self.strip = QFrame()
        strip.setObjectName("plannedStrip")
        row = QHBoxLayout(strip)
        row.setContentsMargins(12, 6, 8, 6)
        row.setSpacing(8)
        self.strip_text = QLabel("")
        row.addWidget(self.strip_text)
        self.strip_edited = label("edited", "chip-warn")
        self.strip_edited.setToolTip("The names were changed here after loading the pair")
        row.addWidget(self.strip_edited)
        row.addStretch(1)
        self.strip_link = QPushButton("Change in Plan")
        self.strip_link.setFlat(True)
        self.strip_link.setProperty("role", "link")
        self.strip_link.setToolTip("Open the Plan tab with this pair selected")
        self.strip_link.clicked.connect(lambda: self.change_in_plan())
        row.addWidget(self.strip_link)
        strip.hide()
        return strip

    def planned_link(self) -> dict[str, Any]:
        """The planned pair loaded here (settings plan.in_acq: id, target, reference, edited), or {}."""
        d = (self.host.settings["plan"] or {}).get("in_acq")
        return d if isinstance(d, dict) else {}

    def _set_link(self, link: dict[str, Any] | None) -> None:
        plan = dict(self.host.settings["plan"] or {})
        if link is None and "in_acq" not in plan:
            self.update_strip()
            return
        if link is None:
            plan.pop("in_acq", None)
        else:
            plan["in_acq"] = link
        self.host.settings["plan"] = plan
        self.host.settings.save()
        self.update_strip()
        self.host.planned_link_changed.emit()

    def link_edited(self) -> bool:
        ln = self.planned_link()
        if not ln:
            return False
        t, r = self.names["target"].text().strip(), self.names["reference"].text().strip()
        return t != ln.get("target", "") or (bool(ln.get("reference")) and r != ln.get("reference"))

    def refresh_link(self) -> None:
        """The names changed here: the strip says "edited" and Plan drops its In Acquisition chip."""
        ln = self.planned_link()
        if ln and bool(ln.get("edited")) != self.link_edited():
            self._set_link({**ln, "edited": self.link_edited()})
        else:
            self.update_strip()

    def update_strip(self) -> None:
        ln = self.planned_link()
        self.strip.setVisible(bool(ln))
        if not ln:
            return
        both = f"{ln.get('target', '')} + {ln['reference']}" if ln.get("reference") else ln.get("target", "")
        self.strip_text.setText(f"Planned pair: {both} (from Plan)")
        self.strip_edited.setVisible(self.link_edited())

    def change_in_plan(self) -> None:
        pid = self.planned_link().get("id")
        self.host.show_tab("plan")
        if pid:
            self.host.ensure_plan().pairs.select_id(str(pid))

    def _ask_load(self, text: str) -> bool:
        from .. import dialogs

        return dialogs.ask(self, "Load planned pair", text)

    def load_planned_pair(self, pair: dict[str, Any], ask=None) -> bool:
        """Plan › Load for acquisition: the sets' names (name_source "plan") and coordinates with a "from plan" tag.
        Never a folder. A pair without a reference sets only the target. When subs or a watch belong to another
        target or reference, ask first; Yes pauses the watch, runs Clear all and empties the folder fields."""
        t, r = pair["target"], pair.get("reference")

        def other(role: str, name: str) -> bool:
            cur = self.names[role].text().strip()
            return bool(cur) and "".join(cur.split()).casefold() != "".join(name.split()).casefold()

        different = other("target", t["name"]) or (r is not None and other("reference", r["name"]))
        if different and (self.records or self.watching()):
            n = len(self.records)
            old = self.names["target"].text().strip() or self.names["reference"].text().strip()
            text = (f"Acquisition has {n} sub{'s' if n != 1 else ''} for {old}." if n
                    else f"Acquisition is watching the folder for {old}.")
            text += f" Load {t['name']} and clear {'them' if n > 1 else 'it'}? Saved stacks stay in your data folder."
            if not (ask or self._ask_load)(text):
                return False
            self.stop_watch()
            self.clear_all(confirm=False)
            for role in ROLES:
                self.folders[role].clear()
                self.sets[role]["folder"] = ""
        self._apply_planned("target", t, t.get("source", "manual"))
        if r is not None:
            self._apply_planned("reference", r, "simbad")  # the bundled catalog's positions come from SIMBAD
        self.persist()
        self._set_link({"id": pair["id"], "target": t["name"], "reference": r["name"] if r else "", "edited": False})
        self.host.session.log(f"Acquisition: planned pair loaded from Plan: {t['name']}"
                              + (f" + {r['name']}" if r else " (no reference yet)"))
        return True

    def _apply_planned(self, role: str, star: dict[str, Any], source: str) -> None:
        self.names[role].setText(star["name"])
        self.name_source[role] = "plan"
        d = self.sets[role]
        ra, dec = star.get("ra_deg"), star.get("dec_deg")
        if ra is None or dec is None:
            d.update(coord_source="manual", simbad={}, ra="", dec="")
        elif source == "simbad":
            d.update(coord_source="simbad", simbad={
                "found": True, "query": star["name"], "main_id": star["name"], "ra": ra, "dec": dec,
                "sp_type": star.get("sp_type", ""), "vmag": star.get("vmag"), "from_plan": True})
        else:
            d.update(coord_source="manual", simbad={}, ra=coords.format_ra(ra), dec=coords.format_dec(dec))
        d["from_plan"] = True
        note = self.ident_notes[role]
        note.set("ok", f"From Plan: {star['name']}" + (f" · {star['sp_type']}" if star.get("sp_type") else ""))
        note.show()
        self._show_coords(role)

    def enqueue(self, paths: list, role: str | None = None) -> None:
        """New stable files (any thread → UI thread via the bridge). They join the set whose watcher found
        them; called directly (no watcher), the set shown."""
        role = role or self.watch_role or ROLES[self.collect.index()]
        known = {r.path for r in self.records} | {p for p, _ in self.queue}
        self.queue += [(str(p), role) for p in paths if str(p) not in known]
        self._next()

    def _next(self) -> None:
        if self.busy or not self.queue:
            self._status()
            return
        self.busy = True
        path, role = self.queue[0]
        rows = self.rows_by.get(role)
        if rows is None:  # first sub of this set: find and lock its rows
            tilt, mode = self.saved_raw_tilt(), self._lobes(role)
            self.host.runner.submit("acq.rows", lambda _ctx, p: sb.detect_rows(p, tilt, mode), path)
        else:
            lin = float(self._settings()["linearity_frac"])
            self.host.runner.submit("acq.sub", lambda _ctx, p, r: self.analyse(p, r, lin), path, rows)
        self._status()

    def on_job(self, slot: str, result: Any) -> None:
        if slot == "acq.view":
            self._view_loaded(result)
            return
        if slot == "acq.prof":
            self._profile_loaded(result)
            return
        if slot.startswith("acq.simbad."):
            self.on_simbad(slot.rsplit(".", 1)[1], result)
            return
        if slot == "acq.stack":
            self.on_stacked(result)
            return
        if slot.startswith("acq.hdr."):
            self.on_rows_written(slot.rsplit(".", 1)[1])
            return
        self.busy = False
        if self._discard:
            self._discard = False
            self._next()
            return
        if slot == "acq.rows":
            role = self.queue[0][1] if self.queue else self.view_role
            self.rows_by[role] = result
            self._show_rows()
            self.host.session.log(f"Acquisition: {ROLE_TITLE[role]} rows locked from {result.locked_from} "
                                  f"(row {result.row:.0f}, tilt {result.tilt_deg:+.2f}°)")
        elif slot == "acq.redetect":
            role = self._redetect_role
            self.rows_by[role] = result
            self._show_rows()
            self.move_hint.hide()
            self.host.session.log(f"Acquisition: {ROLE_TITLE[role]} rows re-detected on {result.locked_from}")
            self.reanalyse(role)
        elif slot == "acq.sub":
            _path, role = self.queue.pop(0)
            self._add(result, role)
        self._next()

    def on_failed(self, slot: str, msg: str) -> None:
        if slot == "acq.view":
            self.img_note.setText(f"Could not read the sub: {msg}")
            return
        if slot == "acq.prof":
            self.latest_note.setText(f"Could not read the sub: {msg}")
            return
        if slot.startswith("acq.simbad."):
            self.on_simbad(slot.rsplit(".", 1)[1], {"query": "", "found": False, "offline": True, "error": msg})
            return
        if slot == "acq.stack":
            self._stack_finished()
            self._show_result("err", f"Stacking failed: {msg}")
            return
        if slot.startswith("acq.hdr."):
            self.on_rows_written(slot.rsplit(".", 1)[1], msg)
            return
        self.busy = False
        if self._discard:
            self._discard = False
            self._next()
            return
        if self.queue and slot in ("acq.rows", "acq.sub"):
            path, _ = self.queue.pop(0)
            self.host.session.log(f"Acquisition: {Path(path).name} skipped ({msg})")
        self._next()

    def on_progress(self, slot: str, f: float) -> None:
        if slot == "acq.stack":
            self.stack_progress.setValue(int(f * 1000))

    def on_cancelled(self, slot: str) -> None:
        if slot == "acq.stack":
            self._stack_finished()
            self._show_result("off", "Stacking stopped. Nothing was written.")
            self.host.session.log("Acquisition: stacking stopped")

    # ---- stacking -----------------------------------------------------------------------------------------
    def stack_selected(self) -> bool:
        """Calibrate and stack the ticked subs of the shown set into Stacks/<night>/<Name>_target_S.fit (or _ref_)
        with core.files_stage.spec_from and stack_job, using the Pre Processing settings. The night is that of the
        first sub (core.naming)."""
        from starextractor.core.datalocs import OK, status
        from starextractor.core.files_stage import spec_from, stack_job, tolerances
        from starextractor.core.frames import LIGHT, load_frame
        from starextractor.core.naming import observing_night

        role = self.view_role
        if self.host.runner.is_busy("acq.stack"):
            return False
        stacks = self.host.settings.paths().stacks
        if status(stacks) != OK:
            self._show_result("err", f"The Stacks folder {stacks} is missing or not writable. "
                                     "<a href='settings'>Choose another location</a>.")
            return False
        good = self._good(role)
        if not good:
            self._show_result("err", f"Tick at least one {ROLE_TITLE[role]} sub to stack.")
            return False
        first = min((r.time for r in good if r.time), default="")
        try:
            night = observing_night(first) if first else self.night
        except ValueError:
            night = self.night
        folder = stacks / night
        frames = [load_frame(r.path, LIGHT) for r in good]
        spec, problem = spec_from(IMAGE_OF[role], frames, self.prep.cal(), self.prep.stack_params(),
                                  self.names[role].text().strip(), self.host.library,
                                  tolerances(self.host.settings["tolerances"]))
        if spec is None:
            self._show_result("err", f"{problem}. Nothing was stacked. "
                                     "<a href='pre'>Open the Pre Processing settings</a>.")
            self.host.session.log(f"Acquisition: cannot stack: {problem}")
            return False
        rows = self.rows_by.get(role)
        spec.rows = rows.to_json() if rows is not None else None
        spec.coords = self._coords_deg(role)
        spec.out_name = stack_name(folder, self.names[role].text().strip() or IMAGE_OF[role], role,
                                   str(Path(good[0].path).resolve().parent))
        folder.mkdir(parents=True, exist_ok=True)
        self._stack_job = {"role": role, "folder": str(folder), "plan": spec.plan, "params": spec.params,
                           "subs": [r.name for r in good],
                           "rows": rows.to_json() if rows is not None else None}
        self.stack_btn.setEnabled(False)
        self.stack_progress.setValue(0)
        self.stack_progress.show()
        self.stack_cancel.show()
        self._show_result("off", f"Stacking {len(good)} {ROLE_TITLE[role]} subs…")
        self.host.runner.submit("acq.stack", stack_job, [spec], self.host.library, folder,
                                log=self.host.session.log)
        return True

    def _stack_finished(self) -> None:
        self.stack_btn.setEnabled(True)
        self.stack_progress.hide()
        self.stack_cancel.hide()

    def on_stacked(self, run: Any) -> None:
        self._stack_finished()
        job, self._stack_job = self._stack_job, None
        if job is None:
            return
        role = job["role"]
        image = IMAGE_OF[role]
        out = run.outputs[image]
        n = run.data[image]["n"]
        text = (f"✓ Stacked {n} subs → {out} · calibrated with {calibration_text(job['plan'])} · "
                f"{method_text(job['params'])}")
        for line in run.log:
            self.host.session.log(f"Acquisition: {line}")
        self.host.session.log(f"Acquisition: {text}")
        st = self._state().setdefault("stacked", {})
        st[role] = {"file": str(Path(job["folder"]) / out), "result": text, "n": n,
                    "subs": job["subs"], "rows": job["rows"],
                    "time": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")}
        self.persist()
        self._show_stack_result()

    def _coords_deg(self, role: str) -> tuple[float | None, float | None]:
        d = self.sets[role]
        try:
            if d.get("ra") and d.get("dec"):
                return coords.parse_coords(d["ra"], d["dec"])
        except ValueError:
            pass
        return None, None

    # ---- rows saved in the stack file ---------------------------------------------------------------------
    def _sync_stack_rows(self, role: str) -> None:
        """The rows of a set changed after it was stacked: rewrite only the row keywords of its stack file (in a
        worker, header-only, atomic) and keep the sets file in step. A missing or locked file only logs a
        warning; the sets file then carries the rows."""
        st = (self._state().get("stacked") or {}).get(role)
        rows = self.rows_by.get(role)
        if not st or not st.get("file") or rows is None or st.get("rows") == rows.to_json():
            return
        st["rows"] = rows.to_json()
        st["rows_in_file"] = False
        self.save_sets()
        self._show_rows_hint()
        from starextractor.core.stack_keys import rows1_of, update_rows

        self.host.runner.submit(f"acq.hdr.{role}", lambda _ctx, p, r: update_rows(Path(p), r),
                                st["file"], rows1_of(st["rows"]), log=self.host.session.log)

    def on_rows_written(self, role: str, error: str | None = None) -> None:
        st = (self._state().get("stacked") or {}).get(role)
        if not st:
            return
        if error is None:
            st["rows_in_file"] = True
            self.host.session.log(f"Acquisition: {ROLE_TITLE[role]} rows updated in {Path(st['file']).name}")
        else:
            self.host.session.log(f"Warning: could not update the rows in {Path(st['file']).name} ({error}); "
                                  "the sets file keeps them")
            st["rows_error"] = error
        self.save_state()
        self._show_rows_hint()

    def _show_rows_hint(self) -> None:
        st = (self._state().get("stacked") or {}).get(self.view_role)
        if not st or not st.get("file") or not st.get("rows"):
            self.rows_hint.hide()
            return
        pending = st.get("rows_in_file") is False and not st.get("rows_error")
        if self.host.runner.is_busy(f"acq.hdr.{self.view_role}") or pending:
            text = "Rows changed since stacking: updating stack file…"
        elif st.get("rows_error"):
            text = "Rows changed since stacking; the stack file could not be updated, the sets file keeps them."
        else:
            text = "Rows saved in the stack file."
        self.rows_hint.setText(text)
        self.rows_hint.show()

    def _show_stack_result(self) -> None:
        self._show_rows_hint()
        st = (self._state().get("stacked") or {}).get(self.view_role)
        if st and st.get("result") and not self.host.runner.is_busy("acq.stack"):
            self._show_result("ok", st["result"])
        elif not self.host.runner.is_busy("acq.stack"):
            self.stack_result.hide()

    def _show_result(self, state: str, html: str) -> None:
        colour = {"ok": theme.GREEN, "err": theme.RED, "warn": theme.AMBER}.get(state, theme.MUTED)
        self.stack_result.setText(f"<span style='color:{colour}'>{html}</span>"
                                  .replace("<a ", f"<a style='color:{theme.BLUE}' "))
        self.stack_result.show()

    def _stack_link(self, href: str) -> None:
        if href == "pre":
            self.open_prep()
        elif href == "settings":
            self.host.open_settings()

    def open_project_folder(self) -> None:
        """Open Stacks/<night>/ (where this night's stacks and the sets file are)."""
        folder = self.night_folder()
        if not folder.is_dir():
            self._show_result("err", f"{folder} does not exist yet: it is made with the first stack or sub.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # ---- notes of the shown set (sets file; imported into the pair logbook on Load pair) -----------------
    def load_logbook(self) -> None:
        self.logbook.blockSignals(True)
        self.logbook.setPlainText(self.sets[self.view_role].get("notes", ""))
        self.logbook.blockSignals(False)
        self._logbook_dirty = False
        self.logbook_note.setText(f"{ROLE_TITLE[self.view_role]} set. Saved in the sets file; Load pair in Reduce › "
                                  "Select copies them into the pair logbook.")

    def _logbook_typed(self) -> None:
        self._logbook_dirty = True
        self._notes_role = self.view_role
        self._log_timer.start()

    def save_logbook(self) -> Path | None:
        """Keep the typed notes with their set (autosave about a second after typing stops)."""
        self._log_timer.stop()
        if not self._logbook_dirty:
            return None
        role = getattr(self, "_notes_role", self.view_role)
        self.sets[role]["notes"] = self.logbook.toPlainText()
        self._logbook_dirty = False
        self.persist()
        return self.night_folder() / acq_sets.FILE_NAME

    def hideEvent(self, e) -> None:
        self.save_logbook()
        super().hideEvent(e)

    def _add(self, rec: sb.SubRecord, role: str) -> None:
        old = next((i for i, r in enumerate(self.records) if r.path == rec.path), None)
        if old is not None:
            self._replace(old, rec)
            return
        prev = self.records[-1] if self.records else None
        rec.role = role
        self.records.append(rec)
        self._refresh_flags(role)
        hint = sb.moved_hint(rec)
        self.move_hint.setText(hint or "")
        self.move_hint.setVisible(hint is not None)
        other = "Reference" if role == "target" else "Target"
        changed = prev is not None and (prev.object != rec.object or prev.exptime != rec.exptime)
        self.role_hint.setText(f"Looks like a new object. Switch to {other}?")
        self.role_hint.setVisible(changed)
        self.host.session.log(f"Acquisition: {rec.name} → {ROLE_TITLE[role]}: peak {100 * rec.peak_fill:.0f} % "
                              f"of limit, SNR {rec.continuum_snr:.0f}, {rec.ms:.0f} ms"
                              + (f" [{', '.join(rec.flags)}]" if rec.flags else ""))
        self.persist()
        self.refresh(latest=rec)

    def _replace(self, i: int, rec: sb.SubRecord) -> None:
        """Re-analysed sub (new rows): new numbers, same set and the user's own tick."""
        old = self.records[i]
        rec.role, rec.user_override = old.role, old.user_override
        rec.included = old.included if old.user_override else True
        self.records[i] = rec
        self._refresh_flags(rec.role or "")
        if not any(p == rec.path or r == rec.role for p, r in self.queue):  # last one of this re-analysis
            self.host.session.log(f"Acquisition: {ROLE_TITLE[rec.role or 'target']} set re-analysed with the "
                                  f"new rows: {acq_sets.summary(self.sets_doc())}")
            self.persist()
        self.refresh()

    def reanalyse(self, role: str) -> None:
        """Analyse every sub of the set again with the set's current rows (about 15 ms per sub, in workers)."""
        waiting = {p for p, _ in self.queue[1:]}
        paths = [r.path for r in self._set(role) if r.path not in waiting]
        if not paths:
            return
        self.queue += [(p, role) for p in paths]
        self.host.session.log(f"Acquisition: re-analysing {len(paths)} {ROLE_TITLE[role]} subs with the new rows")
        self._next()

    def _refresh_flags(self, role: str) -> None:
        sb.refresh_flags([r for r in self.records if r.role == role], float(self._settings()["snr_sigma"]))

    def _status(self) -> None:
        last = self.records[-1] if self.records else None
        parts = [f"{len(self.records)} subs"]
        if last:
            parts.append(f"last {last.name} in {last.ms:.0f} ms")
        if self.queue:
            parts.append(f"{len(self.queue)} waiting")
        if self._thread is not None:
            other = f" ({ROLE_TITLE[self.watch_role]} set)" if self.watch_role != self.view_role else ""
            self.watch_status.set("ok", self._watch_text.rstrip(".") + other + " · " + " · ".join(parts))
        elif self.records or self.queue:
            self.watch_status.set("off", " · ".join(parts))

    # ---- rows --------------------------------------------------------------------------------------------
    def _show_rows(self) -> None:
        r = self.rows_by.get(self.view_role)
        self._show_lobe_mode()
        if r is not None and r.shape == "double-lobed":
            r0 = round(float(r.row))
            self.lobe_chip.setText(DOUBLE_TEXT.format(a=r0 + r.spec[0], b=r0 + r.spec[1]))
            self.lobe_chip.show()
        else:
            self.lobe_chip.hide()
        if r is None:
            self.rows_chip.setText(f"{ROLE_TITLE[self.view_role]} rows: found on its first sub")
            return
        n = next((i + 1 for i, x in enumerate(self._set(self.view_role)) if x.name == r.locked_from), 1)
        self.rows_chip.setText(f"{ROLE_TITLE[self.view_role]}: auto, locked from sub {n}"
                               if r.locked_from != "edited" else f"{ROLE_TITLE[self.view_role]}: rows edited")
        vals = {"row": r.row, "half": (r.spec[1] - r.spec[0]) / 2, "gap": r.spec[0] - r.sky_a[1],
                "width": r.sky_a[1] - r.sky_a[0] + 1}
        for k, f in self.row_fields.items():
            f.blockSignals(True)
            f.setValue(float(vals[k]))
            f.blockSignals(False)

    def _field_typed(self) -> None:
        self._fields_dirty = True  # programmatic updates block signals, so this is always the user
        self._rows_timer.start()

    def _rows_edited(self) -> None:
        """Apply the four fields. Only after the user changed one: a field losing focus (clicking ADU profile)
        must not rebuild symmetric rows over bands dragged in the image."""
        cur = self.rows_by.get(self.view_role)
        if cur is None or not self._fields_dirty:
            return
        self._fields_dirty = False
        self._rows_timer.stop()
        v = {k: f.value() for k, f in self.row_fields.items()}
        half, gap, width = int(v["half"]), int(v["gap"]), int(v["width"])
        new = sb.Rows(v["row"], cur.tilt_deg, cur.x_centre, (-half, half), (-half - gap - width + 1, -half - gap),
                      (half + gap, half + gap + width - 1), "edited", cur.fwhm, cur.shape)
        self._set_rows(self.view_role, new)

    def _set_rows(self, role: str, new: sb.Rows) -> None:
        cur = self.rows_by.get(role)
        if cur is not None and (cur.row, cur.spec, cur.sky_a, cur.sky_b) == (new.row, new.spec, new.sky_a, new.sky_b):
            return  # nothing changed (e.g. focus left a field)
        self.rows_by[role] = new
        self._show_rows()
        self.persist()
        self._sync_stack_rows(role)
        self.reanalyse(role)

    def _bands_moved(self, b: Bands) -> None:
        v = self._shown_view
        if v is None:
            return
        r = v["rows"]
        r0 = round(float(r.row))
        off = [(band.top - r0, band.bottom - r0) for band in (b.spectrum, b.sky_a, b.sky_b)]
        self._set_rows(self.view_role, sb.Rows(r.row, r.tilt_deg, r.x_centre, *off, "edited", r.fwhm, r.shape))

    def redetect(self) -> None:
        """Find the rows again on the sub shown (the latest unless one is picked in the table). With the sub
        image zoomed in, only the rows in view are searched (with the set's tilt)."""
        rec = self._shown_record()
        if rec is None:
            return
        if self.busy:
            self.watch_status.set("warn", "Wait until the subs in the queue are analysed, then Re-detect.")
            return
        self.busy = True
        self._redetect_role = self.view_role
        win = self.redetect_window()
        cur = self._rows_of(rec)
        mode = self._lobes(self.view_role)
        if win is not None and cur is not None:
            top, bot = win
            self.host.runner.submit("acq.redetect", lambda _ctx, p: sb.detect_rows_in(
                p, cur.tilt_deg, cur.x_centre, top, bot, mode), rec.path)
        else:
            tilt = self.saved_raw_tilt()
            self.host.runner.submit("acq.redetect", lambda _ctx, p: sb.detect_rows(p, tilt, mode), rec.path)

    def _lobes(self, role: str) -> str:
        """The set's lobe mode: auto | single | all (core.extract.auto_place)."""
        m = str(self.sets[role].get("lobes") or "auto")
        return m if m in ("auto", "single", "all") else "auto"

    def _lobe_mode_changed(self, _i: int) -> None:
        if self._loading:
            return
        self.sets[self.view_role]["lobes"] = self.lobe_mode.currentData() or "auto"
        self.persist()

    def _show_lobe_mode(self) -> None:
        self.lobe_mode.blockSignals(True)
        self.lobe_mode.setCurrentIndex(max(0, self.lobe_mode.findData(self._lobes(self.view_role))))
        self.lobe_mode.blockSignals(False)

    def redetect_window(self) -> tuple[float, float] | None:
        """Rows in view when the sub image is shown and not the whole frame (any zoom, or the frame-strip window
        moved), else None (whole frame)."""
        if self.view_mode.index() != 1 or self.viewer.current is None or self._shown_view is None:
            return None
        y0, y1 = self.viewer.y_range()
        h = self.viewer.current.shape[0]
        return None if y1 - y0 >= 0.99 * h else (max(0.0, y0), min(float(h), y1))

    # ---- table -----------------------------------------------------------------------------------------------
    def _view(self, role: str) -> None:
        """Show another set: only the display changes; a running watcher keeps running."""
        self.save_logbook()  # the notes typed for the set shown so far
        self.view_role = role
        self.load_logbook()
        self.shown_path = None
        self._select_stacks()
        self._update_watch_btn()
        self._show_stack_result()
        if not self._loading:
            self._state()["tab"] = role
            self.save_state()
            self.prep.refresh()
        self._show_rows()
        self.refresh()

    def _select_stacks(self) -> None:
        i = ROLES.index(self.view_role)
        self.folder_stack.setCurrentIndex(i)
        self.ident_stack.setCurrentIndex(i)

    def _set(self, role: str) -> list[sb.SubRecord]:
        return [r for r in self.records if r.role == role]

    def _good(self, role: str) -> list[sb.SubRecord]:
        return [r for r in self._set(role) if r.included]

    def refresh(self, latest: sb.SubRecord | None = None) -> None:
        recs = self._set(self.view_role)
        self.table_card.title.setText(f"Good subs   {ROLE_TITLE[self.view_role]} · "
                                      f"{len(self._good(self.view_role))} of {len(recs)} used")
        self.table.blockSignals(True)
        self.table.setRowCount(len(recs))
        for r, rec in enumerate(recs):
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            tick.setCheckState(Qt.CheckState.Checked if rec.included else Qt.CheckState.Unchecked)
            tick.setData(Qt.ItemDataRole.UserRole, rec.path)
            self.table.setItem(r, 0, tick)
            vals = (rec.name, f"{rec.exptime:g}", f"{100 * rec.peak_fill:.0f} %", f"{rec.continuum_snr:.0f}",
                    ", ".join(rec.flags) + (" (kept by hand)" if rec.user_override and rec.included and rec.flags
                                            else ""), rec.object)
            for c, text in enumerate(vals, start=1):
                it = QTableWidgetItem(text)
                if c in (2, 3, 4):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    it.setFont(theme.mono_font(12))
                if c == 5 and rec.flags:
                    it.setForeground(pg.mkColor(theme.AMBER))
                if c == 6:
                    it.setForeground(pg.mkColor(theme.MUTED))
                self.table.setItem(r, c, it)
        if self.shown_path is not None:
            row = next((i for i, rec in enumerate(recs) if rec.path == self.shown_path), None)
            if row is None:
                self.table.clearSelection()
            else:
                self.table.selectRow(row)
        else:
            self.table.clearSelection()
        self.table.blockSignals(False)
        good = self._good(self.view_role)
        self.set_total.setText(f"{len(good)} good · {sum(r.exptime for r in good):g} s total")
        if latest is not None and latest.role == self.view_role:
            self.shown_path = None  # a new sub arrived: follow it
        self._show_sub()
        self._recommend()
        self._plot_snr()
        self._status()

    def _picked(self) -> None:
        items = self.table.selectedItems()
        if not items:
            return
        self.shown_path = self.table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        self._show_sub()

    def _shown_record(self) -> sb.SubRecord | None:
        recs = self._set(self.view_role)
        picked = next((r for r in recs if r.path == self.shown_path), None)
        return picked or (recs[-1] if recs else None)

    def _show_sub(self) -> None:
        """The Sub card: ADU profile or sub image of the picked sub (else the latest of the set)."""
        rec = self._shown_record()
        image = self.view_mode.index() == 1
        self.sub_stack.setCurrentIndex(1 if image else 0)
        self._plot_latest(rec)
        if image:
            self._request_view(rec)
        elif rec is not None and not rec.col_max and self._rows_of(rec) is not None:
            # reopened session: the curves are not stored, read this one sub again (same rows, same numbers)
            lin = float(self._settings()["linearity_frac"])
            self.host.runner.submit("acq.prof", lambda _ctx, p, r: self.analyse(p, r, lin), rec.path,
                                    self._rows_of(rec))

    def _profile_loaded(self, fresh: sb.SubRecord) -> None:
        rec = next((r for r in self.records if r.path == fresh.path), None)
        if rec is None:
            return
        rec.col_max, rec.sky = fresh.col_max, fresh.sky
        if rec is self._shown_record():
            self._plot_latest(rec)

    def _rows_of(self, rec: sb.SubRecord) -> sb.Rows | None:
        if rec.rows:
            return sb.Rows.from_json(rec.rows)
        return self.rows_by.get(rec.role or "")

    def _request_view(self, rec: sb.SubRecord | None) -> None:
        rows = self._rows_of(rec) if rec is not None else None
        if rec is None or rows is None:
            self.viewer.set_images(None, None)
            self.band_overlay.set_visible(False)
            self.p_prof.setData([], [])
            self.p_sky.setData([], [])
            self.img_note.setText(f"No {ROLE_TITLE[self.view_role]} sub yet.")
            return
        v = self.views.get(rec.path)
        if v is not None and v["rows"] == rows:
            self._draw_view(v)
            return
        self.img_note.setText(f"Loading {rec.name}…")
        self.host.runner.submit("acq.view", lambda _ctx, p, r: sb.sub_view(p, r), rec.path, rows)

    def _view_loaded(self, v: dict[str, Any]) -> None:
        self.views.pop(v["path"], None)
        self.views[v["path"]] = v
        while len(self.views) > VIEW_CACHE:
            self.views.pop(next(iter(self.views)))
        rec = self._shown_record()
        if rec is not None and rec.path == v["path"] and self.view_mode.index() == 1:
            self._draw_view(v)

    def _fit_profile_x(self) -> None:
        """Profile X follows the rows in view: lowest value at the left, highest at 80 % of the width."""
        v = self._shown_view
        if v is None:
            return
        prof = v["profile"]
        y0, y1 = self.viewer.y_range()
        seg = prof[max(0, int(y0)): min(len(prof), int(np.ceil(y1)) + 1)]
        if seg.size == 0:
            return
        lo, hi = float(np.min(seg)), float(np.max(seg))
        lo = min(lo, v["sky"])  # keep the sky line in view
        span = max(hi - lo, 1.0)
        self.prof_plot.getPlotItem().vb.setXRange(max(0.0, lo - 0.05 * span), lo + span / 0.8, padding=0)

    def zoom_rows(self, mode: str | int) -> tuple[float, float] | None:
        """The rows a zoom chip shows: Full height = every row, ×N = height/N rows centred on the spectrum
        band (moved inside the frame at the edges), Fit to bands = sky A top to sky B bottom ± FIT_SLACK."""
        d = self.viewer.current
        if d is None:
            return None
        h = d.shape[0]
        if mode == "full":
            return 0.0, float(h)
        if self._shown_view is not None:
            bs = self.band_overlay.bands()
            top = min(b.top for b in (bs.spectrum, bs.sky_a, bs.sky_b))
            bot = max(b.bottom for b in (bs.spectrum, bs.sky_a, bs.sky_b)) + 1
            centre = (bs.spectrum.top + bs.spectrum.bottom + 1) / 2
        else:
            top, bot, centre = d.spectrum_row - 60, d.spectrum_row + 60, d.spectrum_row + 0.5
        if mode == "bands":
            top, bot = max(0.0, top - FIT_SLACK), min(float(h), bot + FIT_SLACK)
            return float(top), float(bot)
        span = h / int(mode)
        y0 = min(max(0.0, centre - span / 2), h - span)
        return y0, y0 + span

    def _img_zoom(self, mode: str | int) -> None:
        r = self.zoom_rows(mode)
        if r is not None:
            self.viewer.set_rows(*r)

    def _draw_view(self, v: dict[str, Any]) -> None:
        self._shown_view = v
        d, r = v["display"], v["rows"]
        h, w = d.shape
        keep = self._img_geom == (w, h) and self.viewer.current is not None
        y_keep = self.viewer.y_range()
        self.viewer.set_images(d, None)
        r0 = round(float(d.spectrum_row))
        bands = Bands(*(Band(r0 + a, r0 + b) for a, b in (r.spec, r.sky_a, r.sky_b)))
        self.band_overlay.set_bands(bands, w)
        self.viewer.strip.marks = [(bands.spectrum.top, bands.spectrum.bottom + 1, theme.SPECTRUM_TEXT),
                                   (bands.sky_a.top, bands.sky_a.bottom + 1, theme.SKY_TEXT),
                                   (bands.sky_b.top, bands.sky_b.bottom + 1, theme.SKY_TEXT)]
        self.p_prof.setData(v["profile"], np.arange(h) + 0.5)
        self.p_sky.setData([v["sky"], v["sky"]], [0, h])
        self.prof_plot.getPlotItem().vb.setLimits(yMin=0, yMax=h, minYRange=8)
        if keep:  # same frame size: keep the user's zoom
            self.viewer.set_rows(*y_keep)
        else:
            self._img_geom = (w, h)
            self._img_zoom(IMG_ZOOMS[self.zoom_chips.index()][1])
        self._fit_profile_x()
        cur = self.rows_by.get(self.view_role)
        note = (f"{v['name']}: rows used for this sub. Spectrum {bands.spectrum.top}–{bands.spectrum.bottom} "
                f"({r.n_rows} rows summed), sky {bands.sky_a.top}–{bands.sky_a.bottom} and "
                f"{bands.sky_b.top}–{bands.sky_b.bottom}; the sky level ({v['sky']:.0f} ADU at the continuum) "
                f"is subtracted from every spectrum row. Straightened along the tilt ({r.tilt_deg:+.2f}°).")
        if cur is not None and (cur.row, cur.spec, cur.sky_a, cur.sky_b) != (r.row, r.spec, r.sky_a, r.sky_b):
            note += " The set's rows have changed since; new subs use the new rows."
        self.img_note.setText(note)

    def _ticked(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        rec = next(r for r in self.records if r.path == path)
        rec.included = item.checkState() == Qt.CheckState.Checked
        rec.user_override = True  # later flag recalculations keep this choice
        self.persist()
        self.refresh()

    def _row_menu(self, pos) -> None:
        it = self.table.itemAt(pos)
        if it is None:
            return
        path = self.table.item(it.row(), 0).data(Qt.ItemDataRole.UserRole)
        other = "reference" if self.view_role == "target" else "target"
        menu = QMenu(self)
        move = QAction(f"Move to {ROLE_TITLE[other]}", menu)
        move.triggered.connect(lambda: self.move(path, other))
        excl = QAction("Exclude", menu)
        excl.triggered.connect(lambda: self.exclude(path))
        menu.addAction(move)
        menu.addAction(excl)
        menu.addSeparator()
        copy = QAction("Copy list", menu)
        copy.triggered.connect(lambda: self.copy_list())
        save = QAction("Save list…", menu)
        save.triggered.connect(lambda: self.save_list())
        menu.addAction(copy)
        menu.addAction(save)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def move(self, path: str, role: str) -> None:
        rec = next(r for r in self.records if r.path == path)
        old, rec.role = rec.role, role
        for r in {old, role} - {None}:
            self._refresh_flags(r)
        self.host.session.log(f"Acquisition: {rec.name} moved to {ROLE_TITLE[role]}")
        self.persist()
        self.refresh()

    def exclude(self, path: str) -> None:
        rec = next(r for r in self.records if r.path == path)
        rec.included, rec.user_override = False, True
        self.host.session.log(f"Acquisition: {rec.name} excluded by hand")
        self.persist()
        self.refresh()

    def list_text(self) -> str:
        return "\n".join(r.name for r in self._good(self.view_role))

    def copy_list(self) -> None:
        QGuiApplication.clipboard().setText(self.list_text())

    def save_list(self, path: str | None = None) -> Path | None:
        if path is None:
            path = dialogs.save_file(self, "Save list", f"{self.view_role}_subs.txt", "Text (*.txt)")
            if not path:
                return None
        out = Path(path)
        out.write_text(self.list_text() + "\n", encoding="utf-8")
        return out

    # ---- plots and recommendation ----------------------------------------------------------------------------
    def _plot_latest(self, rec: sb.SubRecord | None) -> None:
        self.limit_line.setPos(sb.limit_adu(float(self._settings()["linearity_frac"])))
        if rec is None or not rec.col_max:
            for c in (self.c_max, self.c_sky, self.peak_dot):
                c.setData([], [])
            self.peak_text.setText("")
            self.latest_note.setText(f"no {ROLE_TITLE[self.view_role]} sub yet" if rec is None else
                                     f"{rec.name} · reading the sub…")
            return
        x = np.arange(len(rec.col_max)) * sb.DECIMATE
        cm = np.asarray(rec.col_max)
        lim = sb.limit_adu(float(self._settings()["linearity_frac"]))
        self.c_max.setData(x, cm)  # replacing data keeps the user's zoom
        self.c_sky.setData(x, np.asarray(rec.sky))
        self.limit_line.setPos(lim)
        sat = x[cm >= lim]
        self.sat_fill.setOpts(x=sat, height=np.full(len(sat), lim * 1.1), width=sb.DECIMATE)
        self.peak_dot.setData([rec.peak_col], [rec.peak_adu])
        self.peak_text.setText(f"peak {rec.peak_adu:.0f} ({100 * rec.peak_fill:.0f} %)")
        self.peak_text.setPos(rec.peak_col, rec.peak_adu)
        width = len(rec.col_max) * sb.DECIMATE  # the frame's columns
        vb = self.plot.getPlotItem().vb
        if vb.state["limits"]["xLimits"][1] != width:
            vb.setLimits(xMin=0, xMax=width)  # the wheel can never widen x past the frame
        x0, x1 = vb.viewRange()[0]
        if x0 < 0 or x1 > width or x1 - x0 > width or self._adu_width != width:
            vb.setXRange(0, width, padding=0)
        self._adu_width = width
        self._place_peak_label()
        which = "picked" if rec.path == self.shown_path else "latest"
        self.latest_note.setText(f"{rec.name} · {rec.exptime:g} s · {ROLE_TITLE.get(rec.role or '', '')} ({which})")

    def _place_peak_label(self) -> None:
        """Peak label above the marker, moved right/left of it near the plot's sides and below it near the top,
        so it is always fully inside the plot."""
        t = self.peak_text
        if not t.toPlainText():
            return
        vb = self.plot.getPlotItem().vb
        (x0, x1), (y0, y1) = vb.viewRange()
        pw, ph = max(vb.width(), 1.0), max(vb.height(), 1.0)
        br = t.textItem.boundingRect()
        tw, th = br.width() + 4, br.height() + 4
        px = (t.pos().x() - x0) / max(x1 - x0, 1e-9) * pw  # marker in pixels from the left / bottom
        py = (t.pos().y() - y0) / max(y1 - y0, 1e-9) * ph
        ax = 0.0 if px < tw / 2 + 6 else (1.0 if px > pw - tw / 2 - 6 else 0.5)
        ay = 0.0 if py > ph - th - 8 else 1.0  # 1: above the marker, 0: below it
        if ax != 0.5:
            ay = 0.5 if th / 2 < py < ph - th / 2 else ay  # beside the marker when it fits
        # a few pixels of air between the text and the dot
        t.setAnchor((ax + (-6 / tw if ax == 0 else 6 / tw if ax == 1 else 0),
                     ay + (6 / th if ay == 1 else -6 / th if ay == 0 else 0)))

    def _recommend(self) -> None:
        good = self._good(self.view_role)
        latest = next((r for r in reversed(self.records) if r.role == self.view_role), None)
        target = self.snr_target.value()
        if latest is None:
            self.reco_state.set("off", "Waiting for the first sub.")
            self.reco_headroom.setText("")
            self.reco_snr.setText("")
            return
        s = self._settings()
        lim = sb.limit_adu(float(s["linearity_frac"]))
        safe = sb.safe_exposure(latest.exptime, latest.peak_adu, latest.pedestal, lim, float(s["safety_frac"]))
        if latest.peak_fill >= 1.0:
            self.reco_state.set("err", f"Saturated: {latest.sat_cols} columns above the linearity limit.")
            self.reco_headroom.setText(f"Use a shorter exposure: at most {safe:.0f} s keeps the peak under "
                                       f"{100 * float(s['safety_frac']):.0f} % of the limit." if safe else
                                       "Use a shorter exposure.")
        else:
            self.reco_state.set("ok", f"Peak at {100 * latest.peak_fill:.0f} % of the linearity limit.")
            self.reco_headroom.setText(f"Longest safe exposure ≈ {safe:.0f} s "
                                       f"({100 * float(s['safety_frac']):.0f} % of the limit)." if safe else "")
        if not good:
            self.reco_snr.setText("No good subs in this set yet.")
            return
        per = float(np.mean([r.continuum_snr for r in good]))
        now = sb.stack_snr([r.continuum_snr for r in good])
        need = sb.subs_needed(target, per)
        more = max(0, (need or 0) - len(good))
        text = f"Stack SNR {now:.0f} from {len(good)} subs (target {target:.0f}). "
        if now >= target:
            text += "Target reached."
        else:
            text += f"{more} more at {latest.exptime:g} s"
            if safe and safe > latest.exptime * 1.05 and latest.peak_fill < 1.0:
                per_safe = sb.scaled_snr(per, latest.exptime, safe)
                need_safe = sb.subs_needed(max(0.0, (target ** 2 - now ** 2)) ** 0.5, per_safe) or 0
                text += f", or {need_safe} more at {safe:.0f} s."
            else:
                text += "."
        self.reco_snr.setText(text)

    def _plot_snr(self) -> None:
        good = self._good(self.view_role)
        snrs = [r.continuum_snr for r in good]
        target = self.snr_target.value()
        self.s_target.setPos(target)
        if not snrs:
            for c in (self.s_meas, self.s_ref, self.s_proj):
                c.setData([], [])
            self.transp.hide()
            self.snr_note.setText("")
            return
        n = np.arange(1, len(snrs) + 1)
        self.s_meas.setData(n, sb.stack_curve(snrs))
        need = sb.subs_needed(target, float(np.mean(snrs))) or len(snrs)
        last = max(need, len(snrs)) + 1
        nn = np.arange(1, last + 1)
        self.s_ref.setData(nn, snrs[0] * np.sqrt(nn))
        proj_n = np.arange(len(snrs), last + 1)
        now = sb.stack_snr(snrs)
        self.s_proj.setData(proj_n, np.sqrt(now ** 2 + (proj_n - len(snrs)) * float(np.mean(snrs)) ** 2))
        self.s_reach.setPos(need)
        top = max(target, float(np.max(self.s_proj.yData)), float(sb.stack_curve(snrs)[-1])) * 1.1
        self.snr_plot.getPlotItem().setRange(xRange=(0, last), yRange=(0, top), padding=0)
        self.s_reach.label.setFormat(f"n = {need}")
        self.transp.setVisible(sb.transparency_hint(snrs))
        self.snr_note.setText(f"{ROLE_TITLE[self.view_role]}: SNR {now:.0f} from {len(snrs)} good subs; "
                              f"the target {target:.0f} is reached at n = {need}.")

    def closeEvent(self, e) -> None:
        self._stop.set()
        self.save_logbook()
        super().closeEvent(e)
