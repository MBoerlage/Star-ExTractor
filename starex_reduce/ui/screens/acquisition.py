"""Acquisition tab: watch the capture folder, analyse each new sub in a worker, keep the Target and
Reference good-subs sets, recommend exposure / number of subs, chart the stack SNR (docs/ACQUISITION_TAB.md).
Pixels are only read by core/subs.py inside JobRunner jobs; the folder is polled on a plain thread."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core import acq_sets
from starex_reduce.core import subs as sb
from starex_reduce.core.extract import Band, Bands
from starex_reduce.core.watcher import FolderWatcher

from .. import theme
from ..log_panel import LogPanel
from ..widgets.bands import SKY_TEXT, SPECTRUM_TEXT, BandOverlay
from ..widgets.common import Card, InfoButton, Segmented, StatusLine, label
from ..widgets.image_viewer import ImageViewer
from ..widgets.plot_guard import pg

if TYPE_CHECKING:
    from ..main_window import MainWindow

ROLES = ("target", "reference")
ROLE_TITLE = {"target": "Target", "reference": "Reference"}
VIEW_CACHE = 3  # sub images (whole frames) kept in memory
IMG_ZOOMS = (("Full height", "full"), ("Fit to bands", "bands"), ("×2", 2), ("×4", 4), ("×8", 8), ("×16", 16))
FIT_SLACK = 40  # rows shown beyond the sky bands by "Fit to bands"
COLS = ("", "Sub", "Exp (s)", "Peak", "SNR", "Flags", "OBJECT (info)")


class _Bridge(QObject):
    files = Signal(list)  # watcher thread → UI thread


def parse_plan_clipboard(text: str) -> tuple[str, str] | None:
    """(target, reference) from the Plan tab's observing-plan text block or its JSON."""
    t = text.strip()
    if t.startswith("{"):
        try:
            d = json.loads(t)
        except ValueError:
            return None
        tgt = (d.get("target") or {}).get("name") or ""
        ref = (d.get("reference") or {}).get("name") or ""
        return (tgt, ref) if tgt or ref else None
    m = re.search(r"Target:\s+(.+?)(?:\s{2,}Reference:\s+(.+?))?\s*$", t.splitlines()[0] if t else "")
    if not m:
        return None
    ref = (m.group(2) or "").strip()
    return m.group(1).strip(), "" if ref == "-" else ref


class AcquisitionScreen(QWidget):
    def __init__(self, host: MainWindow) -> None:
        super().__init__()
        self.host = host
        self.records: list[sb.SubRecord] = []
        self.rows_by: dict[str, sb.Rows] = {}  # locked per set: target and reference sit on different rows
        self.queue: list[tuple[str, str]] = []  # (path, role) waiting for analysis
        self.busy = False
        self.watcher: FolderWatcher | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.bridge = _Bridge()
        self.bridge.files.connect(self.enqueue)
        self.view_role = "target"
        self.name_source = {"target": "typed", "reference": "typed"}
        self.analyse = sb.analyse_sub  # replaced in tests by a slow fake
        self.shown_path: str | None = None  # sub picked in the table; None = follow the latest
        self.views: dict[str, dict[str, Any]] = {}  # sub image data by path (a few, newest last)
        self._shown_view: dict[str, Any] | None = None
        self._redetect_role = "target"
        self._rows_timer = QTimer(self)  # spin-box arrows: apply once the clicking stops
        self._rows_timer.setSingleShot(True)
        self._rows_timer.setInterval(500)
        self._rows_timer.timeout.connect(lambda: self._rows_edited())

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 16, 0, 16)
        lay.setSpacing(12)
        lay.addWidget(label("Acquisition", "title"))
        lay.addWidget(label("Watch the capture folder while you image. Each new sub is checked for saturation and "
                            "signal to noise and joins the set you are collecting for.", "muted", wrap=True))
        lay.addWidget(self._build_watch_card())
        lay.addWidget(self._build_sets_card())
        cols = QHBoxLayout()
        cols.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._build_latest_card())
        left.addWidget(self._build_snr_card())
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_reco_card())
        right.addWidget(self._build_table_card(), 1)
        cols.addLayout(left, 3)
        cols.addLayout(right, 2)
        lay.addLayout(cols)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        self.log = LogPanel()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.log)

    # ---- construction ------------------------------------------------------------------------------
    def _settings(self) -> dict[str, Any]:
        from starex_reduce.core.settings import DEFAULTS

        return {**DEFAULTS["acq"], **(self.host.settings["acq"] or {})}

    def _save_settings(self, **kw: Any) -> None:
        self.host.settings["acq"] = {**self._settings(), **kw}
        self.host.settings.save()

    def _build_watch_card(self) -> Card:
        card = Card("Watch folder")
        s = self._settings()
        row = QHBoxLayout()
        self.folder = QLineEdit(s["folder"])
        self.folder.setPlaceholderText("Folder your capture program saves the subs to")
        self.folder.editingFinished.connect(lambda: self._save_settings(folder=self.folder.text().strip()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self.browse())
        self.filter = QLineEdit(s["filter"])
        self.filter.setPlaceholderText("Filter, e.g. *Sgr*")
        self.filter.setFixedWidth(150)
        self.filter.editingFinished.connect(lambda: self._save_settings(filter=self.filter.text().strip()))
        self.interval = QDoubleSpinBox()
        self.interval.setRange(1, 10)
        self.interval.setDecimals(0)
        self.interval.setSuffix(" s")
        self.interval.setValue(float(s["interval_s"]))
        self.interval.setFixedWidth(80)
        self.interval.valueChanged.connect(lambda v: self._save_settings(interval_s=float(v)))
        self.existing = QCheckBox("Process existing files")
        self.existing.setChecked(bool(s["process_existing"]))
        self.existing.toggled.connect(lambda on: self._save_settings(process_existing=on))
        self.watch_btn = QPushButton("Start watching")
        self.watch_btn.setProperty("role", "primary")  # the one amber button of this tab
        self.watch_btn.clicked.connect(lambda: self.toggle_watch())
        for w in (self.folder, browse, self.filter, label("every", "muted"), self.interval, self.existing,
                  self.watch_btn):
            row.addWidget(w, 1 if w is self.folder else 0)
        card.body.addLayout(row)
        self.watch_status = StatusLine("off", "Not watching.")
        card.body.addWidget(self.watch_status)
        return card

    def _build_sets_card(self) -> Card:
        card = Card("Sets")
        card.head.insertWidget(1, InfoButton(
            "Name the two sets by hand, from the Plan tab, or paste the Plan's observing plan (text or JSON). "
            "The OBJECT keyword in the subs is shown for information only: it never decides which set a sub "
            "joins. Flip 'Collecting for' yourself when you move to the reference star."))
        g = QGridLayout()
        g.setHorizontalSpacing(8)
        self.names: dict[str, QLineEdit] = {}
        for i, role in enumerate(ROLES):
            e = QLineEdit()
            e.setPlaceholderText(f"{ROLE_TITLE[role]} name")
            e.textEdited.connect(lambda _t, r=role: self._name_typed(r))
            b = QPushButton("From Plan")
            b.clicked.connect(lambda _=False, r=role: self.from_plan(r))
            g.addWidget(label(ROLE_TITLE[role], "muted"), 0, 3 * i)
            g.addWidget(e, 0, 3 * i + 1)
            g.addWidget(b, 0, 3 * i + 2)
            self.names[role] = e
        paste = QPushButton("Paste")
        paste.setToolTip("Fill both names from the Plan tab's observing plan (text block or JSON) on the clipboard")
        paste.clicked.connect(lambda: self.paste_names())
        g.addWidget(paste, 0, 6)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(4, 1)
        card.body.addLayout(g)
        row = QHBoxLayout()
        row.addWidget(label("Collecting for", "muted"))
        self.collect = Segmented(["Target", "Reference"])
        row.addWidget(self.collect)
        self.role_hint = label("", "chip-warn")
        self.role_hint.hide()
        row.addWidget(self.role_hint)
        row.addStretch(1)
        self.next_to_images = QCheckBox("Also save next to the images")
        self.next_to_images.setToolTip("Writes acquisition_sets.json into the image folder too. "
                                       "Never overwrites a file this app did not write.")
        self.next_to_images.setChecked(bool(self._settings()["save_next_to_images"]))
        self.next_to_images.toggled.connect(lambda on: (self._save_settings(save_next_to_images=on), self.persist()))
        row.addWidget(self.next_to_images)
        self.save_sets_btn = QPushButton("Save sets")
        self.save_sets_btn.setToolTip("acquisition_sets.json in the session folder (also saved on every change)")
        self.save_sets_btn.clicked.connect(lambda: self.save_sets(report=True))
        row.addWidget(self.save_sets_btn)
        card.body.addLayout(row)
        self.sets_status = StatusLine("off", "")
        self.sets_status.hide()
        card.body.addWidget(self.sets_status)
        return card

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
        self.redetect_btn = QPushButton("Re-detect")
        self.redetect_btn.setToolTip("Find the spectrum rows again on the sub shown. With the sub image "
                                      "zoomed in, only the rows in view are searched (finds a faint spectrum).")
        self.redetect_btn.clicked.connect(lambda: self.redetect())
        rows.addWidget(self.redetect_btn)
        card.body.addLayout(rows)
        fields = QHBoxLayout()  # line 2: the rows by hand
        self.row_fields: dict[str, QDoubleSpinBox] = {}
        for key, title, lo, hi in (("row", "Row", 0, 5000), ("half", "± rows", 1, 40), ("gap", "Sky gap", 1, 200),
                                   ("width", "Sky width", 2, 100)):
            f = QDoubleSpinBox()
            f.setRange(lo, hi)
            f.setDecimals(1 if key == "row" else 0)
            f.setFixedWidth(76)
            f.valueChanged.connect(lambda _v: self._rows_timer.start())
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
        legend = self._legend(pi)
        self.c_max = pg.PlotDataItem(pen=pg.mkPen(SPECTRUM_TEXT, width=1.2), name="Spectrum rows: column max")
        self.c_sky = pg.PlotDataItem(pen=pg.mkPen(SKY_TEXT, width=1), name="Sky rows: median")
        self.limit_line = pg.InfiniteLine(angle=0, movable=False,
                                          pen=pg.mkPen(theme.AMBER, width=1, style=Qt.PenStyle.DashLine),
                                          label="linearity limit", labelOpts={"position": 0.08, "color": theme.AMBER})
        self.sat_fill = pg.BarGraphItem(x=[], height=[], width=sb.DECIMATE, brush=pg.mkBrush(255, 107, 107, 70),
                                        pen=pg.mkPen(None))
        self.peak_dot = pg.ScatterPlotItem(size=8, brush=pg.mkBrush(theme.AMBER), pen=pg.mkPen(None))
        self.peak_text = pg.TextItem(color=theme.AMBER, anchor=(0.5, 1.2))
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
        self.viewer.rows_changed.connect(lambda a, b, n: self.img_rows_label.setText(f"rows {a}–{b} of {n}"))
        self.img_plot = self.viewer.plot
        ivb = self.viewer.vb
        self.band_overlay = BandOverlay(ivb)  # drag a band: the set is re-analysed with the new rows
        self.band_overlay.moved.connect(self._bands_moved)
        self._img_geom: tuple[int, int] | None = None
        self.prof_plot = self._plot("", "ADU")
        self.prof_plot.setFixedWidth(230)
        ppi = self.prof_plot.getPlotItem()
        ppi.hideAxis("left")
        ppi.vb.invertY(True)
        ppi.vb.setLimits(xMin=0)
        # zoom / pan either plot: the same rows in both (range copied, not pixel-linked: the heights differ)
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
        self.p_sky = pg.PlotDataItem(pen=pg.mkPen(SKY_TEXT, width=1.2, style=Qt.PenStyle.DashLine),
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
        self.set_tabs = Segmented(["Target", "Reference"])
        self.set_tabs.changed.connect(lambda i: self._view(ROLES[i]))
        card.head.insertWidget(1, self.set_tabs)
        self.set_total = label("", "mono")
        copy = QPushButton("Copy list")
        copy.clicked.connect(lambda: self.copy_list())
        save = QPushButton("Save list…")
        save.clicked.connect(lambda: self.save_list())
        foot = QHBoxLayout()
        foot.addWidget(self.set_total)
        foot.addStretch(1)
        foot.addWidget(copy)
        foot.addWidget(save)
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
        card.body.addWidget(label("Click a sub to show it on the left. Right-click to move it to the other set "
                                  "or exclude it.", "muted", wrap=True, style="font-size: 12px;"))
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
        s = self.host.session.params.get("acq")
        if not isinstance(s, dict):
            s = self.host.session.params["acq"] = {}
        return s

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
        self.apply_orientation()
        st = self._state()
        self.records = [sb.SubRecord.from_json(d) for d in st.get("subs", [])]
        self.rows_by = {k: sb.Rows.from_json(v) for k, v in (st.get("rows") or {}).items() if v}
        for role in ROLES:
            self.names[role].setText(st.get("names", {}).get(role, ""))
            self.name_source[role] = st.get("name_source", {}).get(role, "typed")
        self._show_rows()
        self.refresh()

    def persist(self) -> None:
        st = self._state()
        keep = []
        for r in self.records:
            d = r.to_json()
            d["col_max"], d["sky"] = [], []  # plot arrays stay in memory only
            keep.append(d)
        st.update(subs=keep, rows={k: v.to_json() for k, v in self.rows_by.items()},
                  names={r: self.names[r].text().strip() for r in ROLES}, name_source=dict(self.name_source))
        self.host.session.save()
        self.save_sets()  # autosave on every change

    def sets_doc(self) -> dict[str, Any]:
        return acq_sets.make_sets(self.records, {r: self.names[r].text().strip() for r in ROLES},
                                  dict(self.name_source))

    def save_sets(self, report: bool = False) -> Path | None:
        folder = self.host.session.folder
        if folder is None or not self.records:
            if report:
                self.sets_status.set("warn", "Needs a session folder and at least one sub.")
                self.sets_status.show()
            return None
        doc = self.sets_doc()
        out = acq_sets.save_sets(folder / acq_sets.FILE_NAME, doc)
        extra = acq_sets.save_next_to_images(doc) if self.next_to_images.isChecked() else None
        if report:
            self.sets_status.set("ok", f"Saved {out}" + (f" and {extra}" if extra else ""))
            self.sets_status.show()
            self.host.session.log(f"Acquisition: sets saved ({acq_sets.summary(doc)})")
        return out

    # ---- names -------------------------------------------------------------------------------------------
    def _name_typed(self, role: str) -> None:
        self.name_source[role] = "typed"
        self.persist()

    def from_plan(self, role: str) -> None:
        from starex_reduce.core.plan import plan_rows

        target, ref = plan_rows(self.host.session)
        name = target["name"] if role == "target" else (ref or {}).get("name", "")
        if name:
            self.names[role].setText(name)
            self.name_source[role] = "plan"
            self.persist()

    def paste_names(self, text: str | None = None) -> bool:
        got = parse_plan_clipboard(QGuiApplication.clipboard().text() if text is None else text)
        if not got:
            self.watch_status.set("warn", "The clipboard holds no observing plan from the Plan tab.")
            return False
        for role, name in zip(ROLES, got, strict=True):
            if name:
                self.names[role].setText(name)
                self.name_source[role] = "clipboard"
        self.persist()
        return True

    # ---- watching ------------------------------------------------------------------------------------------
    def browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder to watch", self.folder.text())
        if d:
            self.folder.setText(d)
            self._save_settings(folder=d)

    def toggle_watch(self) -> None:
        if self._thread is not None:
            self.stop_watch()
            return
        folder = Path(self.folder.text().strip())
        if not folder.is_dir():
            self.watch_status.set("warn", "Choose an existing folder first.")
            return
        self.watcher = FolderWatcher(folder, self.filter.text(), self.existing.isChecked(), self.interval.value())
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self.watcher.run, args=(self._stop, self.bridge.files.emit),
                                        name="acq-watch", daemon=True)
        self._thread.start()
        self.watch_btn.setText("Pause")
        self.watch_status.set("ok", f"Watching {folder} every {self.interval.value():.0f} s.")
        self.host.session.log(f"Acquisition: watching {folder}")

    def stop_watch(self) -> None:
        self._stop.set()
        self._thread = None
        self.watch_btn.setText("Start watching")
        self.watch_status.set("off", "Paused.")

    def enqueue(self, paths: list) -> None:
        """New stable files (any thread → UI thread via the bridge). They join the set selected now."""
        role = ROLES[self.collect.index()]
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
            tilt = self.saved_raw_tilt()
            self.host.runner.submit("acq.rows", lambda _ctx, p: sb.detect_rows(p, tilt), path)
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
        self.busy = False
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
        self.busy = False
        if self.queue and slot in ("acq.rows", "acq.sub"):
            path, _ = self.queue.pop(0)
            self.host.session.log(f"Acquisition: {Path(path).name} skipped ({msg})")
        self._next()

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
            self.watch_status.set("ok", "Watching · " + " · ".join(parts))
        elif self.records or self.queue:
            self.watch_status.set("off", " · ".join(parts))

    # ---- rows --------------------------------------------------------------------------------------------
    def _show_rows(self) -> None:
        r = self.rows_by.get(self.view_role)
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

    def _rows_edited(self) -> None:
        cur = self.rows_by.get(self.view_role)
        if cur is None:
            return
        self._rows_timer.stop()
        v = {k: f.value() for k, f in self.row_fields.items()}
        half, gap, width = int(v["half"]), int(v["gap"]), int(v["width"])
        new = sb.Rows(v["row"], cur.tilt_deg, cur.x_centre, (-half, half), (-half - gap - width + 1, -half - gap),
                      (half + gap, half + gap + width - 1), "edited", cur.fwhm)
        self._set_rows(self.view_role, new)

    def _set_rows(self, role: str, new: sb.Rows) -> None:
        cur = self.rows_by.get(role)
        if cur is not None and (cur.row, cur.spec, cur.sky_a, cur.sky_b) == (new.row, new.spec, new.sky_a, new.sky_b):
            return  # nothing changed (e.g. focus left a field)
        self.rows_by[role] = new
        self._show_rows()
        self.persist()
        self.reanalyse(role)

    def _bands_moved(self, b: Bands) -> None:
        v = self._shown_view
        if v is None:
            return
        r = v["rows"]
        r0 = round(float(r.row))
        off = [(band.top - r0, band.bottom - r0) for band in (b.spectrum, b.sky_a, b.sky_b)]
        self._set_rows(self.view_role, sb.Rows(r.row, r.tilt_deg, r.x_centre, *off, "edited", r.fwhm))

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
        if win is not None and cur is not None:
            top, bot = win
            self.host.runner.submit("acq.redetect", lambda _ctx, p: sb.detect_rows_in(
                p, cur.tilt_deg, cur.x_centre, top, bot), rec.path)
        else:
            tilt = self.saved_raw_tilt()
            self.host.runner.submit("acq.redetect", lambda _ctx, p: sb.detect_rows(p, tilt), rec.path)

    def redetect_window(self) -> tuple[float, float] | None:
        """Rows in view when the sub image is shown and zoomed in, else None (whole frame)."""
        if self.view_mode.index() != 1 or self.viewer.current is None or self._shown_view is None:
            return None
        y0, y1 = self.viewer.y_range()
        h = self.viewer.current.shape[0]
        return None if y1 - y0 >= 0.95 * h else (max(0.0, y0), min(float(h), y1))

    # ---- table -----------------------------------------------------------------------------------------------
    def _view(self, role: str) -> None:
        self.view_role = role
        self.shown_path = None
        self._show_rows()
        self.refresh()

    def _set(self, role: str) -> list[sb.SubRecord]:
        return [r for r in self.records if r.role == role]

    def _good(self, role: str) -> list[sb.SubRecord]:
        return [r for r in self._set(role) if r.included]

    def refresh(self, latest: sb.SubRecord | None = None) -> None:
        for i, role in enumerate(ROLES):
            good, allr = self._good(role), self._set(role)
            self.set_tabs.buttons[i].setText(f"{ROLE_TITLE[role]} ({len(good)} of {len(allr)})")
        recs = self._set(self.view_role)
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

    def _img_zoom(self, mode: str | int) -> None:
        if mode == "bands" and self.viewer.current is not None and self._shown_view is not None:
            bs = self.band_overlay.bands()
            top = min(b.top for b in (bs.spectrum, bs.sky_a, bs.sky_b))
            bot = max(b.bottom for b in (bs.spectrum, bs.sky_a, bs.sky_b))
            self.viewer.set_rows(top - FIT_SLACK, bot + 1 + FIT_SLACK)
        else:
            self.viewer.set_zoom("fit" if mode == "bands" else mode)

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
        self.viewer.strip.marks = [(bands.spectrum.top, bands.spectrum.bottom + 1, SPECTRUM_TEXT),
                                   (bands.sky_a.top, bands.sky_a.bottom + 1, SKY_TEXT),
                                   (bands.sky_b.top, bands.sky_b.bottom + 1, SKY_TEXT)]
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
            path, _ = QFileDialog.getSaveFileName(self, "Save list", f"{self.view_role}_subs.txt", "Text (*.txt)")
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
        which = "picked" if rec.path == self.shown_path else "latest"
        self.latest_note.setText(f"{rec.name} · {rec.exptime:g} s · {ROLE_TITLE.get(rec.role or '', '')} ({which})")

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
        super().closeEvent(e)
