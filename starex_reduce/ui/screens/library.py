"""Library tab: every night and target + reference pair under the data root (docs/LIBRARY_TAB.md). The data comes
from core/library.py (logbooks, file stats and headers only, scanned in a worker); actions reuse the main
window's Load pair and core.publish.promote."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core import library as lib
from starex_reduce.core import logbook

from .. import theme
from ..widgets.common import Card, Segmented, label

if TYPE_CHECKING:
    from ..main_window import MainWindow

COLS = ("Target", "Reference", "Progress", "Latest note")
CHIPS = (("All", "all"), ("Stacked", "stacked"), ("Reduced", "reduced"), ("Published", "published"),
         ("Needs attention", "attention"))
NOTES_DELAY_MS = 1000
STEP_ICON = {logbook.DONE: "✓", logbook.STALE: "↻", logbook.FAILED: "✗", logbook.SKIPPED: "–", logbook.NONE: "·"}
def step_colour(state: str) -> str:
    return {logbook.DONE: theme.GREEN, logbook.STALE: theme.AMBER, logbook.FAILED: theme.RED,
                   logbook.SKIPPED: theme.MUTED, logbook.NONE: theme.DIM}.get(state, theme.DIM)
ROW_KEY = Qt.ItemDataRole.UserRole + 1


def local_time(utc: str | None) -> str:
    if not utc:
        return "no record"
    try:
        return datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return utc


# ---- table ---------------------------------------------------------------------------------------------------------
class LibraryModel(QAbstractTableModel):
    """Night header rows ("night") and pair / stack rows ("row"), newest night first."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[tuple[str, Any]] = []

    def set_rows(self, rows: list[lib.Row]) -> None:
        self.beginResetModel()
        self.items = []
        night = None
        for r in rows:
            if r.night != night:
                night = r.night
                n = sum(1 for x in rows if x.night == night)
                self.items.append(("night", (night, n)))
            self.items.append(("row", r))
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008 (Qt)
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008 (Qt)
        return 0 if parent.isValid() else len(COLS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        kind, _ = self.items[index.row()]
        if kind == "night":
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        kind, v = self.items[index.row()]
        col = index.column()
        if kind == "night":
            if role == Qt.ItemDataRole.DisplayRole and col == 0:
                night, n = v
                return f"{night}   ·   {n} item{'s' if n != 1 else ''}"
            if role == Qt.ItemDataRole.FontRole:
                f = QFont()
                f.setBold(True)
                return f
            if role == Qt.ItemDataRole.ForegroundRole:
                return QBrush(QColor(theme.MUTED))
            return None
        r: lib.Row = v
        if role == ROW_KEY:
            return r
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return r.target
            if col == 1:
                return r.reference or "no reference"
            if col == 2:
                return r.status_word
            return r.last_note
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 1 and not r.reference:
                return QBrush(QColor(theme.AMBER))
            if col == 3:
                return QBrush(QColor(theme.MUTED))
        if role == Qt.ItemDataRole.ToolTipRole and r.attention:
            return "Needs attention: " + "; ".join(r.attention)
        return None

    def row_at(self, i: int) -> lib.Row | None:
        if 0 <= i < len(self.items) and self.items[i][0] == "row":
            return self.items[i][1]
        return None

    def index_of(self, key: str) -> int:
        return next((i for i, (k, v) in enumerate(self.items) if k == "row" and v.key == key), -1)


class ProgressDelegate(QStyledItemDelegate):
    """Three dots (stacked, reduced, published) and the status word. Done = filled and bright, not yet = an
    open ring and dim: they differ by fill and brightness, not only by colour."""

    def paint(self, p: QPainter, opt: QStyleOptionViewItem, index: QModelIndex) -> None:
        r = index.data(ROW_KEY)
        if r is None:
            return super().paint(p, opt, index)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.widget.style().drawControl(opt.widget.style().ControlElement.CE_ItemViewItem, opt, p, opt.widget)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x, cy = opt.rect.left() + 10, opt.rect.center().y()
        for key in ("stacked", "reduced", "published"):
            done = bool(r.status.get(key))
            c = QColor(theme.TEXT if done else theme.DIM)
            p.setPen(QPen(c, 1.5))
            p.setBrush(QBrush(c) if done else Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(x, cy), 4.5, 4.5)
            x += 15
        word_c = QColor(theme.AMBER if r.attention else theme.TEXT)
        p.setPen(word_c)
        text = r.status_word + ("  !" if r.attention else "")
        p.drawText(QRectF(x + 4, opt.rect.top(), opt.rect.width() - (x - opt.rect.left()) - 4, opt.rect.height()),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        p.restore()


def _summary_card(title: str) -> tuple[Card, QLabel]:
    card = Card()
    card.setMinimumWidth(150)
    num = QLabel("0")
    num.setStyleSheet("font-size: 26px; font-weight: 600;")
    card.body.addWidget(num)
    card.body.addWidget(label(title, "muted", style="font-size: 12px;"))
    return card, num


# ---- the screen ------------------------------------------------------------------------------------------------------
class LibraryScreen(QWidget):
    def __init__(self, host: MainWindow) -> None:
        super().__init__()
        self.host = host
        self.library: lib.Library | None = None
        self.thumbs: dict[tuple[str, int], dict[str, Any]] = {}
        self._thumb_pending: set[tuple[str, int]] = set()
        self.selected: lib.Row | None = None
        self._doc: dict[str, Any] | None = None
        st = dict(host.settings["library"] or {})
        self._want_key = str(st.get("selected") or "")
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.setSpacing(12)
        lay.addLayout(self._build_header())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_main())
        self.pages.addWidget(self._message_page("No pairs yet. Stack a target in Acquisition, then load a pair in "
                                                "Select.", None))
        self.pages.addWidget(self._message_page("", "Open Settings › Data locations"))
        lay.addWidget(self.pages, 1)
        self.chips.set_index(max(0, [k for _t, k in CHIPS].index(st.get("filter", "all"))
                                 if st.get("filter") in [k for _t, k in CHIPS] else 0))
        self.search.setText(str(st.get("search") or ""))
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(NOTES_DELAY_MS)
        self._notes_timer.timeout.connect(self.save_notes)
        self._notes_dirty = False
        host.pairs_changed.connect(self.request_scan)
        host.notes_changed.connect(self._notes_elsewhere)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.search.setFocus(), self.search.selectAll()))

    # ---- construction ----------------------------------------------------------------------------------------------
    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label("Library", "title"))
        self.subtitle = label("", "muted")
        col.addWidget(self.subtitle)
        row.addLayout(col, 1)
        self.scan_note = label("", "muted", style="font-size: 12px;")
        row.addWidget(self.scan_note, 0, Qt.AlignmentFlag.AlignBottom)
        self.rescan_btn = QPushButton("Rescan")
        self.rescan_btn.clicked.connect(self.request_scan)
        self.open_root_btn = QPushButton("Open data folder")
        self.open_root_btn.clicked.connect(lambda: self._open(self.host.settings.paths().root))
        row.addWidget(self.rescan_btn, 0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.open_root_btn, 0, Qt.AlignmentFlag.AlignBottom)
        return row

    def _message_page(self, text: str, button: str | None) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addStretch(1)
        msg = label(text, "muted", wrap=True, style="font-size: 15px;")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(msg)
        if button:
            self.root_msg = msg
            b = QPushButton(button)
            b.setProperty("role", "primary")
            b.clicked.connect(lambda: (self.host.open_settings(), self.request_scan()))
            self.settings_btn = b
            v.addWidget(b, 0, Qt.AlignmentFlag.AlignCenter)
        else:
            self.empty_msg = msg
        v.addStretch(2)
        return w

    def _build_main(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.counts: dict[str, QLabel] = {}
        for key, title in (("nights", "Nights"), ("pairs", "Pairs"), ("published", "Published"),
                           ("attention", "Need attention")):
            card, num = _summary_card(title)
            self.counts[key] = num
            cards.addWidget(card, 1)
        v.addLayout(cards)
        f = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search target, reference and notes (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_changed)
        f.addWidget(self.search, 1)
        self.chips = Segmented([t for t, _k in CHIPS])
        self.chips.changed.connect(lambda _i: self._filter_changed())
        f.addWidget(self.chips)
        v.addLayout(f)
        self.model = LibraryModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(2, ProgressDelegate(self.table))
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(30)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        for c, wd in enumerate((220, 220, 220)):
            self.table.setColumnWidth(c, wd)
        self.table.setMinimumHeight(220)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        # the selected row is a current state: blue, never amber (amber is for warnings and actions)
        self.table.setStyleSheet(f"QTableView::item:selected {{ background: {theme.CURRENT_BG}; color: {theme.TEXT}; }}"
                                 f"QTableView {{ selection-background-color: {theme.CURRENT_BG}; }}")
        self.table.selectionModel().currentRowChanged.connect(lambda cur, _prev: self._row_selected(cur.row()))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.activated.connect(lambda _i: self.open_in_reduce())  # Enter / double-click
        v.addWidget(self.table, 3)
        self.detail = QStackedWidget()
        self.detail.addWidget(label("Select a pair or a stack above.", "muted"))
        self.detail.addWidget(self._build_pair_detail())
        self.detail.addWidget(self._build_stack_detail())
        v.addWidget(self.detail, 4)
        return w

    def _build_pair_detail(self) -> QWidget:
        card = Card()
        head = QHBoxLayout()
        col = QVBoxLayout()
        self.d_title = label("", style="font-size: 16px; font-weight: 600;")
        self.d_sub = label("", "muted", style="font-size: 12px;")
        col.addWidget(self.d_title)
        col.addWidget(self.d_sub)
        head.addLayout(col, 1)
        self.d_chip = label("", "chip")
        head.addWidget(self.d_chip, 0, Qt.AlignmentFlag.AlignTop)
        card.body.addLayout(head)
        body = QHBoxLayout()
        body.setSpacing(16)
        left = QVBoxLayout()
        left.addWidget(label("PROGRESS REPORT", "eyebrow"))
        self.report = QGridLayout()
        self.report.setHorizontalSpacing(10)
        self.report.setVerticalSpacing(4)
        left.addLayout(self.report)
        left.addStretch(1)
        body.addLayout(left, 1)
        right = QVBoxLayout()
        from ..widgets.plot_guard import pg

        self.plot = pg.PlotWidget()
        self.plot.setBackground(theme.CARD)
        self.plot.setMinimumHeight(130)
        self.plot.setMaximumHeight(170)
        self.plot.setMouseEnabled(False, False)
        self.plot.hideButtons()
        self.plot.getPlotItem().setLabel("bottom", "Wavelength (Å)")
        self.curve = self.plot.plot([], [], pen=pg.mkPen(theme.TEXT, width=1.2))
        right.addWidget(self.plot)
        self.no_spectrum = label("No reduced 1D spectrum in this pair yet.", "muted", style="font-size: 12px;")
        right.addWidget(self.no_spectrum)
        right.addWidget(label("NOTES", "eyebrow"))
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes for this pair…")
        self.notes.setFixedHeight(80)
        self.notes.textChanged.connect(self._notes_typed)
        right.addWidget(self.notes)
        body.addLayout(right, 1)
        card.body.addLayout(body)
        btns = QHBoxLayout()
        self.open_btn = QPushButton("Open in Reduce")
        self.open_btn.setProperty("role", "primary")
        self.open_btn.clicked.connect(self.open_in_reduce)
        self.promote_btn = QPushButton("Promote to Published")
        self.promote_btn.clicked.connect(self.promote)
        self.show_btn = QPushButton("Show in Explorer")
        self.show_btn.clicked.connect(self.show_in_explorer)
        for b in (self.open_btn, self.promote_btn, self.show_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        self.d_message = label("", wrap=True)
        btns.addWidget(self.d_message, 1)
        card.body.addLayout(btns)
        return card

    def _build_stack_detail(self) -> QWidget:
        card = Card()
        self.s_title = label("", style="font-size: 16px; font-weight: 600;")
        card.body.addWidget(self.s_title)
        self.s_info = label("", "mono", wrap=True)
        card.body.addWidget(self.s_info)
        card.body.addWidget(label("No pair yet: this target stack has no reference.", "warn", wrap=True,
                                  style=f"color: {theme.AMBER};"))
        row = QHBoxLayout()
        row.addWidget(label("Pick a reference", "muted"))
        self.ref_pick = QComboBox()
        self.ref_pick.setMinimumWidth(420)
        row.addWidget(self.ref_pick, 1)
        self.create_btn = QPushButton("Create pair and open in Reduce")
        self.create_btn.setProperty("role", "primary")
        self.create_btn.clicked.connect(self.create_pair)
        row.addWidget(self.create_btn)
        card.body.addLayout(row)
        self.s_message = label("", wrap=True)
        card.body.addWidget(self.s_message)
        card.body.addStretch(1)
        return card

    # ---- scanning ----------------------------------------------------------------------------------------------------
    def request_scan(self) -> None:
        """Scan in a worker (tab opened, Rescan, a step or a publish finished). Never blocks the UI."""
        from starex_reduce.core.datalocs import OK, status

        paths = self.host.settings.paths()
        self.subtitle.setText(f"Every night and pair under {paths.root}")
        if status(paths.root) != OK:
            self.root_msg.setText(f"The data root {paths.root} is missing or not writable (an unplugged drive?). "
                                  "Choose another location in Settings › Data locations.")
            self.pages.setCurrentIndex(2)
            return
        if self.host.runner.is_busy("library.scan"):
            self._rescan_again = True
            return
        self._rescan_again = False
        self.scan_note.setText("Scanning…")
        self.host.runner.submit("library.scan", lib.scan_job, paths)

    def on_job(self, slot: str, result: Any) -> None:
        if slot == "library.scan":
            self.on_scanned(result)
            if getattr(self, "_rescan_again", False):
                self.request_scan()
        elif slot.startswith("library.thumb"):
            key = (result["path"], result["mtime"])
            self.thumbs[key] = result
            self._thumb_pending.discard(key)
            self._show_thumb()

    def on_failed(self, slot: str, msg: str) -> None:
        if slot == "library.scan":
            self.scan_note.setText(f"Scan failed: {msg}")

    def on_scanned(self, library: lib.Library) -> None:
        self.library = library
        self.counts["nights"].setText(str(library.nights))
        self.counts["pairs"].setText(str(library.pairs))
        self.counts["published"].setText(str(library.published))
        n = library.attention
        self.counts["attention"].setText(str(n))
        self.counts["attention"].setStyleSheet("font-size: 26px; font-weight: 600;"
                                               + (f" color: {theme.AMBER};" if n else ""))
        self.scan_note.setText(f"Last scan {local_time(library.scanned_utc)[-5:]} · {library.ms:.0f} ms")
        self.pages.setCurrentIndex(1 if not library.rows else 0)
        key = self.selected.key if self.selected is not None else self._want_key
        self._fill(key)

    def _filter_changed(self) -> None:
        self._save_state()
        if self.library is not None:
            self._fill(self.selected.key if self.selected is not None else "")

    def chip(self) -> str:
        return CHIPS[self.chips.index()][1]

    def _fill(self, key: str) -> None:
        rows = self.library.filtered(self.chip(), self.search.text()) if self.library else []
        self.model.set_rows(rows)
        for i, (kind, _v) in enumerate(self.model.items):
            if kind == "night":
                self.table.setSpan(i, 0, 1, len(COLS))
        i = self.model.index_of(key) if key else -1
        if i < 0:
            i = next((k for k in range(len(self.model.items)) if self.model.items[k][0] == "row"), -1)
        if i >= 0:
            self.table.selectRow(i)
            self.table.setCurrentIndex(self.model.index(i, 0))
            self._row_selected(i)
        else:
            self.selected = None
            self.detail.setCurrentIndex(0)

    # ---- selection and details -------------------------------------------------------------------------------------
    def _row_selected(self, i: int) -> None:
        r = self.model.row_at(i)
        if r is None:
            return
        self.save_notes()
        self.selected = r
        self._want_key = r.key
        self._save_state()
        if r.kind == "pair":
            self._show_pair(r)
        else:
            self._show_stack(r)

    def _show_pair(self, r: lib.Row) -> None:
        self.detail.setCurrentIndex(1)
        root = self.host.settings.paths().root
        self._doc = logbook.load(r.folder) or {}
        self.d_title.setText(f"{r.target} + {r.reference}")
        rel = Path(r.folder).relative_to(root).as_posix() if r.folder and Path(r.folder).is_relative_to(root) \
            else str(r.folder)
        self.d_sub.setText(f"Night {r.night} · {rel}")
        pub = list(r.published)
        if pub:
            chip = f"Published v{pub[-1].get('version')} · {local_time(pub[-1].get('date_utc'))[:10]}"
        elif r.status.get("reduced"):
            chip = "Reduced · not published"
        else:
            chip = "Stacked · not reduced"
        self.d_chip.setText(chip + ("  ·  needs attention" if r.attention else ""))
        self._fill_report(r)
        self._loading = True
        self.notes.setPlainText(r.notes)
        self._loading = False
        self._notes_dirty = False
        avs = lib.avspec_file(r.folder, root) if r.folder else None  # type: ignore[arg-type]
        n = max((int(p.get("version") or 0) for p in pub), default=0)
        self.promote_btn.setText("Promote to Published" if n == 0 else f"Promote as v{n + 1}")
        self.promote_btn.setEnabled(avs is not None)
        self.promote_btn.setToolTip(f"Copy {avs.name} into Published as a new version" if avs is not None else
                                    "No AVSpec file in this pair yet: save one in Export › Publish first")
        self.d_message.setText("; ".join(r.attention) if r.attention else "")
        self.d_message.setStyleSheet(f"color: {theme.AMBER};")
        self._request_thumb(r)

    def _fill_report(self, r: lib.Row) -> None:
        while self.report.count():
            w = self.report.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        doc = self._doc or {}
        for i, name in enumerate(logbook.STEP_NAMES):
            if name == "export":
                continue
            rec = r.steps.get(name) or {"state": logbook.NONE}
            state = rec.get("state", logbook.NONE)
            text = self._step_text(name, rec, doc)
            icon = label(STEP_ICON.get(state, "·"), style=f"color: {step_colour(state)}; "
                                                         "font-weight: 600;")
            icon.setFixedWidth(14)
            self.report.addWidget(icon, i, 0)
            self.report.addWidget(label(logbook.STEP_TITLES[name], style="font-weight: 600;"), i, 1)
            self.report.addWidget(label(text, "muted" if state == logbook.NONE else None, wrap=True), i, 2)
            when = local_time(rec.get("finished_utc")) if rec.get("recorded", True) else "no record"
            self.report.addWidget(label(when, "mono", style="font-size: 11px;"), i, 3)
        self.report.setColumnStretch(2, 1)

    @staticmethod
    def _step_text(name: str, rec: dict[str, Any], doc: dict[str, Any]) -> str:
        state = rec.get("state", logbook.NONE)
        m = rec.get("metrics") or {}
        prefix = {logbook.STALE: "out of date: ", logbook.FAILED: "failed: ", logbook.SKIPPED: "skipped"}.get(state, "")
        if name == "stacked":
            parts = []
            for role in ("target", "reference"):
                s = doc.get(role) or {}
                if s.get("nsub"):
                    parts.append(f"{role} {s['nsub']} × {s.get('exp_s') or 0:g} s")
            return ", ".join(parts) or ("stacks present" if state == logbook.DONE else "no stacks")
        if name == "publish":
            pub = doc.get("published") or []
            return (f"v{pub[-1].get('version')} on {local_time(pub[-1].get('date_utc'))[:10]}" if pub
                    else "Not yet promoted")
        if name == "qc":
            warns = (doc.get("qc") or {}).get("warnings") or []
            if not warns:
                return "no warnings" if state == logbook.DONE else "not tested"
            acked = sum(bool(w.get("acknowledged")) for w in warns)
            return (f"{len(warns)} warning{'s' if len(warns) != 1 else ''}, "
                    + ("acknowledged" if acked == len(warns) else "not acknowledged") + ": "
                    + "; ".join(str(w.get("text")) for w in warns[:2]))
        if state == logbook.NONE:
            return "not run"
        if not rec.get("recorded", True):
            return "done (no record)"
        if name == "geometry" and m.get("tilt_deg") is not None:
            detail = f"tilt {m['tilt_deg']:+.3f}°" + (f", slant {m['slant_deg']:+.3f}°" if m.get("slant_deg") else "")
        elif name == "extract" and m.get("rows"):
            detail = f"rows {m['rows'][0]}–{m['rows'][1]} ({m.get('rows_source') or 'stored'})"
        elif name == "wavelength" and m.get("rms_A") is not None:
            detail = f"{m.get('fit') or 'fit'}, {m.get('nlines', 0)} lines, RMS {m['rms_A']:.3f} Å"
        elif name == "response" and m.get("library"):
            detail = f"library {m['library']}"
        else:
            detail = str(rec.get("summary") or "")
        return (prefix + detail) if state != logbook.SKIPPED else prefix

    def _show_stack(self, r: lib.Row) -> None:
        from starex_reduce.core.pair import read_stack

        self.detail.setCurrentIndex(2)
        paths = self.host.settings.paths()
        info = read_stack(r.stack) if r.stack else None  # type: ignore[arg-type]
        self.s_title.setText(f"{r.target}  ·  night {r.night}")
        if info is not None and info.ok:
            exp = f" × {info.subexp:g} s" if info.subexp else ""
            self.s_info.setText(f"{r.key}\n{info.nsub or '?'} subs{exp} · {info.size[0]} × {info.size[1]}")  # type: ignore[index]
        else:
            self.s_info.setText(r.key)
        self.ref_pick.clear()
        for text, path in lib.reference_candidates(paths, r.night, exclude=r.stack):
            self.ref_pick.addItem(text, str(path))
        if r.suggested_reference:
            want = str(paths.root / r.suggested_reference) if not Path(r.suggested_reference).is_absolute() \
                else r.suggested_reference
            i = self.ref_pick.findData(str(Path(want)))
            if i >= 0:
                self.ref_pick.setCurrentIndex(i)
        self.create_btn.setEnabled(self.ref_pick.count() > 0)
        self.create_btn.setToolTip("" if self.ref_pick.count() else "No other stack in this night")
        self.s_message.setText("")

    # ---- thumbnail -------------------------------------------------------------------------------------------------
    def _spectrum_path(self, r: lib.Row) -> Path | None:
        if r.folder is None:
            return None
        for name in ("Target_R.fit", "Target_W.fit"):
            p = Path(r.folder) / name
            if p.exists():
                return p
        return None

    def _request_thumb(self, r: lib.Row) -> None:
        p = self._spectrum_path(r)
        if p is None:
            self.curve.setData([], [])
            self.plot.hide()
            self.no_spectrum.show()
            return
        key = (str(p), p.stat().st_mtime_ns)
        if key in self.thumbs:
            self._show_thumb()
            return
        if key in self._thumb_pending:
            return
        self._thumb_pending.add(key)
        self.host.runner.submit(f"library.thumb.{len(self._thumb_pending)}", lib.thumb_job, str(p))

    def _show_thumb(self) -> None:
        r = self.selected
        p = self._spectrum_path(r) if r is not None else None
        if p is None:
            return
        t = self.thumbs.get((str(p), p.stat().st_mtime_ns))
        if t is None:
            return
        self.curve.setData(t["lam"], t["flux"])
        self.plot.show()
        self.no_spectrum.hide()

    # ---- notes -----------------------------------------------------------------------------------------------------
    def _notes_typed(self) -> None:
        if self._loading:
            return
        self._notes_dirty = True
        self._notes_timer.start()

    def save_notes(self) -> bool:
        """Only the notes field of the logbook (re-read first): never overwrites a step written meanwhile."""
        self._notes_timer.stop()
        r = self.selected
        if not self._notes_dirty or r is None or r.kind != "pair" or r.folder is None:
            return False
        text = self.notes.toPlainText()
        try:
            logbook.set_notes(r.folder, text)
        except OSError as exc:
            self.d_message.setText(f"Could not save the notes: {exc}")
            return False
        self._notes_dirty = False
        r.notes, r.last_note = text, logbook.last_note(text)
        self.host.notes_changed.emit(str(r.folder))
        return True

    def _notes_elsewhere(self, folder: str) -> None:
        r = self.selected
        if r is not None and r.folder is not None and str(r.folder) == folder and not self._notes_dirty:
            doc = logbook.load(r.folder) or {}
            r.notes = str(doc.get("notes") or "")
            self._loading = True
            self.notes.setPlainText(r.notes)
            self._loading = False

    def hideEvent(self, e: Any) -> None:
        self.save_notes()
        super().hideEvent(e)

    # ---- actions ----------------------------------------------------------------------------------------------------
    def open_in_reduce(self) -> bool:
        """Load the pair like Select › Load pair (the same function), then the first step that is not done."""
        from starex_reduce.core.datalocs import resolve
        from starex_reduce.core.pair import read_stack
        from starex_reduce.core.session import Session

        r = self.selected
        if r is None:
            return False
        if r.kind == "stack":
            return self.create_pair()
        self.save_notes()
        root = self.host.settings.paths().root
        doc = logbook.load(r.folder) or {}
        stacks = {role: resolve(str((doc.get(role) or {}).get("stack_file") or ""), root)
                  for role in ("target", "reference")}
        if all(str((doc.get(role) or {}).get("stack_file") or "") and p.exists() for role, p in stacks.items()):
            infos = {role: read_stack(p) for role, p in stacks.items()}
            names = {role: str((doc.get(role) or {}).get("name") or infos[role].name) for role in infos}
            problem = self.host.load_pair(infos, names)
        elif r.folder is not None and Session.is_session(r.folder):
            self.host.load_session(r.folder)  # the stacks moved: open what the pair folder has
            problem = ""
        else:
            problem = "The stacks of this pair are not in the Stacks folder any more."
        if problem:
            self.d_message.setText(problem)
            self.d_message.setStyleSheet(f"color: {theme.RED};")
            return False
        self._go_to_first_open_step()
        return True

    def _go_to_first_open_step(self) -> None:
        self.host.show_tab("reduce")
        self.host.open_step(lib.first_open_step(self.host.session))

    def create_pair(self) -> bool:
        """A target stack + the reference picked: Select › Load pair's function, then Reduce."""
        from starex_reduce.core.pair import read_stack

        r = self.selected
        ref = self.ref_pick.currentData()
        if r is None or r.stack is None or not ref:
            return False
        infos = {"target": read_stack(r.stack), "reference": read_stack(ref)}
        names = {role: infos[role].name or Path(infos[role].path).stem for role in infos}
        problem = self.host.load_pair(infos, names)
        if problem:
            self.s_message.setText(problem)
            self.s_message.setStyleSheet(f"color: {theme.RED};")
            return False
        self._want_key = Path(self.host.session.folder).name  # type: ignore[arg-type]
        self.selected = None
        self._go_to_first_open_step()
        return True

    def promote(self, confirm: bool = True) -> dict[str, Any] | None:
        """Export › Publish's Promote (core.publish.promote) after a confirmation that shows source and
        destination."""
        from starex_reduce.core.datalocs import OK, status
        from starex_reduce.core.naming import published_folder
        from starex_reduce.core.publish import next_version, promote

        r = self.selected
        if r is None or r.folder is None:
            return None
        paths = self.host.settings.paths()
        avs = lib.avspec_file(r.folder, paths.root)
        if avs is None:
            return None
        if status(paths.published) != OK:
            self.d_message.setText(f"The Published folder {paths.published} is missing or not writable.")
            return None
        doc = logbook.load(r.folder) or {}
        night = str(doc.get("night") or r.night)
        year = night[:4]
        n = next_version(paths.published, year, night, r.target, doc)
        dest = paths.published / published_folder(year, night, r.target, n)
        if confirm:
            ok = QMessageBox.question(self, "Promote to Published",
                                      f"Copy\n{avs}\n\ninto\n{dest}\n\nwith submission.json? The pair folder keeps "
                                      "its copy.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            if ok != QMessageBox.StandardButton.Yes:
                return None
        try:
            entry = promote(r.folder, avs, paths.published, paths.root)
        except OSError as exc:
            self.d_message.setText(f"Not promoted: {exc}")
            return None
        self.host.session.log(f"Library: promoted {r.target} v{entry['version']} → {entry['file']}")
        self.request_scan()
        return entry

    def show_in_explorer(self) -> None:
        r = self.selected
        if r is not None:
            self._open(r.folder if r.folder is not None else (r.stack.parent if r.stack else None))

    @staticmethod
    def _open(path: Path | None) -> None:
        if path is not None and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _menu(self, pos: Any) -> None:
        i = self.table.indexAt(pos).row()
        if self.model.row_at(i) is None:
            return
        self.table.selectRow(i)
        m = QMenu(self)
        m.addAction("Open in Reduce", self.open_in_reduce)
        pro = m.addAction(self.promote_btn.text(), self.promote)
        pro.setEnabled(self.selected is not None and self.selected.kind == "pair" and self.promote_btn.isEnabled())
        m.addAction("Show in Explorer", self.show_in_explorer)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _save_state(self) -> None:
        self.host.settings["library"] = {"selected": self._want_key, "filter": self.chip(),
                                         "search": self.search.text()}
        self.host.settings.save()

