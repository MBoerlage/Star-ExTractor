"""Plan tab: the target (name → Check with SIMBAD, or RA/Dec by hand) and the nearest reference-star
candidates from the bundled catalog. The result is an observing plan (text or JSON) to copy or save;
it is kept in session.json but does not fill in Files or Response."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core import plan as pl
from starex_reduce.core.coords import format_dec, format_ra, parse_coords
from starex_reduce.core.reference_stars import ReferenceStarMatch, find_reference_stars

from .. import theme
from ..log_panel import LogPanel
from ..widgets.common import Card, InfoButton, Segmented, StatusLine, label
from .files import simbad_job

if TYPE_CHECKING:
    from ..main_window import MainWindow

COLUMNS = ("Name", "Distance (°)", "V", "B−V", "Sp. type", "Flux ref")
NUMERIC = {1, 2, 3}
FLAG_COL = 5


class PlanScreen(QWidget):
    def __init__(self, host: MainWindow) -> None:
        super().__init__()
        self.host = host
        self.matches: list[ReferenceStarMatch] = []
        self._loading = False
        self._ref_cleared = False  # the last target change dropped a chosen reference

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 16, 0, 16)
        lay.setSpacing(12)
        lay.addWidget(label("Plan", "title"))
        lay.addWidget(label("Enter the target, then pick a reference star close to it on the sky. The observing plan "
                            "at the bottom can be copied or saved.", "muted", wrap=True))
        lay.addWidget(self._build_target_card())
        lay.addWidget(self._build_reference_card())
        lay.addWidget(self._build_output_card())
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        self.scroll = scroll
        self.log = LogPanel()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.log)

    # ---- construction ------------------------------------------------------------------------
    def _build_target_card(self) -> Card:
        card = Card("Target")
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name or designation, e.g. gam Cas, V8055 Sgr")
        self.name.returnPressed.connect(self.check_simbad)
        self.name.editingFinished.connect(self._name_edited)
        self.simbad_btn = QPushButton("Check with SIMBAD")
        self.simbad_btn.setToolTip("One online request to SIMBAD (CDS Sesame)")
        self.simbad_btn.clicked.connect(lambda: self.check_simbad())
        row.addWidget(self.name, 1)
        row.addWidget(self.simbad_btn)
        card.body.addLayout(row)
        self.simbad_status = StatusLine("off", "Check the name with SIMBAD, or type RA/Dec below.")
        card.body.addWidget(self.simbad_status)
        coords = QHBoxLayout()
        coords.setSpacing(8)
        self.ra = QLineEdit()
        self.ra.setPlaceholderText("hh mm ss.s or degrees")
        self.dec = QLineEdit()
        self.dec.setPlaceholderText("±dd mm ss or degrees")
        copy_icon = QIcon(f"{theme.ICONS}/copy.svg")
        self.copy_buttons: dict[str, QToolButton] = {}
        for title, e, part in (("RA (J2000)", self.ra, "ra"), ("Dec (J2000)", self.dec, "dec")):
            e.setFont(theme.mono_font(13))
            e.setFixedWidth(200)
            e.editingFinished.connect(self._coords_edited)
            b = QToolButton()
            b.setObjectName("copyBtn")
            b.setIcon(copy_icon)
            b.setToolTip(f"Copy {'RA' if part == 'ra' else 'Dec'} (bare value)")
            b.clicked.connect(lambda _=False, p=part: self.copy_coords("target", p))
            self.copy_buttons[part] = b
            coords.addWidget(label(title, "muted"))
            coords.addWidget(e)
            coords.addWidget(b)
            coords.addSpacing(8)
        self.source_chip = label("", "chip")
        self.source_chip.setToolTip("Where the position comes from: SIMBAD, or typed by hand (Manual)")
        coords.addWidget(self.source_chip)
        coords.addStretch(1)
        card.body.addLayout(coords)
        self.coord_error = StatusLine("err", "")
        self.coord_error.hide()
        card.body.addWidget(self.coord_error)
        return card

    def _build_reference_card(self) -> Card:
        card = Card("Reference star candidates")
        card.head.insertWidget(1, InfoButton(
            "Nearest stars on the sky from a bundled catalog of about 1000 bright (V < 6), mostly A and B "
            "main-sequence stars, from F. Teyssier's Reference Star Finder (v3, 2024, built from SIMBAD). "
            "Ranked by angular distance only: altitude, airmass and reddening are not considered. Offline."))
        card.head.addWidget(label("Show", "muted"))
        self.n_box = QSpinBox()
        self.n_box.setRange(1, 50)
        self.n_box.setValue(pl.DEFAULT_N)
        self.n_box.setFixedWidth(80)
        self.n_box.valueChanged.connect(self._n_changed)
        card.head.addWidget(self.n_box)
        self.use_btn = QPushButton("Use as reference")  # the one amber button of this view, amber only when usable
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(lambda: self.use_selected())
        card.head.addWidget(self.use_btn)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # sized to its rows
        hh = self.table.horizontalHeader()
        for c, w in ((0, 200), (1, 120), (2, 80), (3, 80), (4, 150)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(c, w)
        hh.setSectionResizeMode(FLAG_COL, QHeaderView.ResizeMode.Stretch)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for c in NUMERIC:  # numeric headers sit over their right-aligned values
            self.table.horizontalHeaderItem(c).setTextAlignment(right)
        self.table.itemSelectionChanged.connect(self._update_use_btn)
        self.table.cellDoubleClicked.connect(lambda r, _c: self.use_selected(r))
        card.body.addWidget(self.table)
        self.ref_status = StatusLine("off", "")
        card.body.addWidget(self.ref_status)
        return card

    # ---- session ↔ widgets ---------------------------------------------------------------------
    def load_session(self) -> None:
        self._loading = True
        p = pl.plan_state(self.host.session)
        t = p["target"]
        self.name.setText(t.get("name", ""))
        ra, dec = t.get("ra_deg"), t.get("dec_deg")
        self.ra.setText("" if ra is None else format_ra(ra))
        self.dec.setText("" if dec is None else format_dec(dec))
        self.n_box.setValue(int(p.get("n", pl.DEFAULT_N)))
        self._loading = False
        self.coord_error.hide()
        self._show_target()
        self._refresh_candidates()
        self._show_plan()

    def _show_target(self) -> None:
        t = pl.plan_state(self.host.session)["target"]
        src = t.get("source") if t.get("ra_deg") is not None else ""
        self.source_chip.setText({"simbad": "SIMBAD", "manual": "Manual"}.get(src, "No position"))
        if src == "simbad" and t.get("simbad_id"):
            self.simbad_status.set("ok", self._simbad_text({"query": t["name"], "main_id": t["simbad_id"], **t}))
        elif self.simbad_status.state == "ok":  # a SIMBAD answer for an earlier target no longer applies
            self.simbad_status.set("off", "Position typed by hand (not checked with SIMBAD).")

    @staticmethod
    def _simbad_text(res: dict[str, Any]) -> str:
        v = res.get("vmag")
        parts = [res.get("otype") or "", f"V {v:.2f}" if isinstance(v, (int, float)) else "V —",
                 res.get("sp_type") or ""]
        return f"SIMBAD: {res.get('query')} = {res.get('main_id')} · " + " · ".join(p for p in parts if p)

    # ---- target ------------------------------------------------------------------------------
    def check_simbad(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.simbad_status.set("warn", "Type a name first.")
            return
        self.simbad_btn.setEnabled(False)
        self.simbad_status.set("off", f"Asking SIMBAD for “{name}”…")
        self.host.runner.submit("plan.simbad", simbad_job, name)

    def on_simbad(self, res: dict[str, Any]) -> None:
        self.simbad_btn.setEnabled(True)
        s = self.host.session
        if res.get("found"):
            self.ra.setText(format_ra(res["ra"]))
            self.dec.setText(format_dec(res["dec"]))
            self.coord_error.hide()
            self._apply_target(res.get("query") or self.name.text(), res["ra"], res["dec"], "simbad", res)
            s.log(f"Plan: target {res['query']} = {res['main_id']} ({res.get('sp_type') or 'no type'}): "
                  f"{format_ra(res['ra'])} {format_dec(res['dec'])}, source simbad")
            self.simbad_status.set("ok", self._simbad_text(res))
        elif res.get("offline"):
            self.simbad_status.set("warn", "SIMBAD could not be reached: type RA/Dec below.")
            s.log(f"Plan: SIMBAD offline ({str(res.get('error', ''))[:80]})")
        else:
            self.simbad_status.set("warn", f"“{res.get('query')}” is not in SIMBAD (new nova or transient?): "
                                           "type RA/Dec below.")
            s.log(f"Plan: {res.get('query')} not in SIMBAD")

    def _name_edited(self) -> None:
        if self._loading:
            return
        t = pl.plan_state(self.host.session)["target"]
        name = self.name.text().strip()
        if name != t.get("name", ""):
            self._apply_target(name, t.get("ra_deg"), t.get("dec_deg"), t.get("source", "manual"))

    def _coords_edited(self) -> None:
        if self._loading or not (self.ra.text().strip() and self.dec.text().strip()):
            return
        try:
            ra, dec = parse_coords(self.ra.text(), self.dec.text())
        except ValueError as exc:
            self.coord_error.set("err", f"{exc}. Use hh mm ss.s / ±dd mm ss or decimal degrees.")
            self.coord_error.show()
            return
        self.coord_error.hide()
        t = pl.plan_state(self.host.session)["target"]
        same = (t.get("ra_deg") is not None and abs(t["ra_deg"] - ra) < 1e-6 and abs(t["dec_deg"] - dec) < 1e-6)
        if not same:
            self.host.session.log(f"Plan: target position typed: {format_ra(ra)} {format_dec(dec)}, source manual")
            self._apply_target(self.name.text(), ra, dec, "manual")

    def _apply_target(self, name: str, ra: float | None, dec: float | None, source: str,
                      res: dict[str, Any] | None = None) -> None:
        old_ref = pl.plan_state(self.host.session)["reference"].get("name")
        pl.set_target(self.host.session, name, ra, dec, source, res)
        if old_ref and not pl.plan_state(self.host.session)["reference"]:
            self._ref_cleared = True  # stays until a new reference is chosen
            self.host.session.log(f"Plan: reference star {old_ref} cleared (target changed)")
        self._changed()
        self._show_target()
        self._refresh_candidates()

    # ---- candidates --------------------------------------------------------------------------
    def _n_changed(self, n: int) -> None:
        if self._loading:
            return
        pl.plan_state(self.host.session)["n"] = n
        self.host.session.save()
        self._refresh_candidates()

    def _refresh_candidates(self) -> None:
        t = pl.plan_state(self.host.session)["target"]
        if t.get("ra_deg") is None:
            self.matches = []
        else:
            self.matches = find_reference_stars(t["ra_deg"], t["dec_deg"], self.n_box.value())
        chosen = pl.plan_state(self.host.session)["reference"]
        self.table.setRowCount(len(self.matches))
        for r, m in enumerate(self.matches):
            s = m.star
            mark = "✓ " if chosen.get("chosen") and chosen.get("name") == s.name else ""
            for c, text in enumerate((mark + s.name, f"{m.distance_deg:.2f}", f"{s.v_mag:.2f}", f"{s.b_v:+.2f}",
                                      s.sp_type)):
                it = QTableWidgetItem(text)
                if c in NUMERIC:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    it.setFont(theme.mono_font(12))
                self.table.setItem(r, c, it)
            self._set_flag(r, pl.flux_flag(s.sp_type))
        rows = sum(self.table.rowHeight(r) for r in range(self.table.rowCount()))
        empty = 3 * self.table.verticalHeader().defaultSectionSize()
        self.table.setFixedHeight(self.table.horizontalHeader().height() + max(rows, empty)
                                  + 2 * self.table.frameWidth() + 2)
        chosen_row = next((r for r, m in enumerate(self.matches)
                           if chosen.get("chosen") and m.star.name == chosen.get("name")), None)
        self.table.blockSignals(True)
        self.table.clearSelection()
        if chosen_row is not None:  # the chosen star stays highlighted, so selection and status agree
            self.table.selectRow(chosen_row)
        self.table.blockSignals(False)
        self._update_use_btn()
        self._show_reference()

    def _set_flag(self, row: int, flag: str | None) -> None:
        self.table.setItem(row, FLAG_COL, QTableWidgetItem(""))
        if flag is None:
            self.table.removeCellWidget(row, FLAG_COL)
            return
        tip = pl.FLUX_WARN_TIP if flag == "warn" else pl.FLUX_GOOD_TIP
        chip = label("! poor flux ref" if flag == "warn" else "✓ good", "chip-warn" if flag == "warn" else "good")
        chip.setToolTip(tip)
        chip.setObjectName(f"flux_{flag}")
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.addWidget(chip)
        lay.addStretch(1)
        box.setToolTip(tip)
        self.table.item(row, FLAG_COL).setToolTip(tip)
        self.table.item(row, 4).setToolTip(tip)
        self.table.setCellWidget(row, FLAG_COL, box)

    def flag_of(self, row: int) -> tuple[str | None, str]:
        """(flag, tooltip) shown in a row: 'warn', 'good' or None."""
        w = self.table.cellWidget(row, FLAG_COL)
        if w is None:
            return None, ""
        chip = w.findChild(QWidget, "flux_warn") or w.findChild(QWidget, "flux_good")
        return chip.objectName().removeprefix("flux_"), chip.toolTip()

    def _update_use_btn(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        chosen = pl.plan_state(self.host.session)["reference"]
        usable = bool(rows) and not (chosen.get("chosen") and rows[0].row() < len(self.matches)
                                     and self.matches[rows[0].row()].star.name == chosen.get("name"))
        self.use_btn.setEnabled(usable)
        self.use_btn.setProperty("role", "primary" if usable else None)
        theme.repolish(self.use_btn)

    def _changed(self) -> None:
        self.host.session.save()
        self._show_plan()

    def _show_reference(self) -> None:
        ref = pl.plan_state(self.host.session)["reference"]
        if ref.get("chosen"):
            self.ref_status.set("ok", f"Reference: {ref['name']} · {ref['sp_type']} · V {ref['v']:.2f} · "
                                      f"{ref['distance_deg']:.2f}° from the target")
        elif not self.matches:
            self.ref_status.set("off", "Enter the target position (SIMBAD or RA/Dec) to list candidates.")
        elif self._ref_cleared:
            self.ref_status.set("warn", "Target changed: pick a reference star again.")
        else:
            self.ref_status.set("off", "Pick a row and press Use as reference (optional).")

    def use_selected(self, row: int | None = None) -> None:
        if row is None:
            rows = self.table.selectionModel().selectedRows()
            if not rows:
                return
            row = rows[0].row()
        m = self.matches[row]
        if pl.plan_state(self.host.session)["reference"].get("name") == m.star.name:
            return  # already the reference: nothing changes, nothing to log
        pl.set_reference(self.host.session, m)
        self._ref_cleared = False
        self.host.session.log(f"Plan: reference star {m.star.name} ({m.star.sp_type}, V {m.star.v_mag:.2f}, "
                              f"{m.distance_deg:.2f}°)")
        self._changed()
        self._refresh_candidates()

    # ---- observing plan ----------------------------------------------------------------------
    def _build_output_card(self) -> Card:
        card = Card("Observing plan")
        card.head.insertWidget(1, InfoButton(
            "Target and reference star with V magnitude and J2000 position, ready to paste into your notes or "
            "acquisition software. It is not linked to the Reduce tab."))
        self.fmt = Segmented(["Text", "JSON"])
        self.fmt.changed.connect(lambda _i: self._show_plan())
        card.head.addWidget(self.fmt)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: self.copy_plan())
        save = QPushButton("Save…")
        save.clicked.connect(lambda: self.save_plan())
        card.head.addWidget(copy)
        card.head.addWidget(save)
        self.copy_target_btn = QPushButton("Target RA+Dec")
        self.copy_target_btn.setToolTip("Copy the bare target position, e.g. 20 17 47.2  +38 01 59")
        self.copy_target_btn.clicked.connect(lambda: self.copy_coords("target"))
        self.copy_ref_btn = QPushButton("Reference RA+Dec")
        self.copy_ref_btn.setToolTip("Copy the bare reference position")
        self.copy_ref_btn.clicked.connect(lambda: self.copy_coords("reference"))
        card.head.addWidget(self.copy_target_btn)
        card.head.addWidget(self.copy_ref_btn)
        self.plan_box = QPlainTextEdit()
        self.plan_box.setReadOnly(True)
        self.plan_box.setObjectName("planOutput")  # monospace via theme.py, so the two columns line up
        self.plan_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.plan_box.setFixedHeight(290)  # the whole JSON without scrolling
        card.body.addWidget(self.plan_box)
        self.plan_result = StatusLine("ok", "")
        self.plan_result.hide()
        card.body.addWidget(self.plan_result)
        return card

    def plan_output(self) -> str:
        s = self.host.session
        return pl.plan_json(s) if self.fmt.index() == 1 else pl.plan_text(s)

    def _show_plan(self) -> None:
        self.plan_box.setPlainText(self.plan_output())
        s = self.host.session
        self.copy_target_btn.setEnabled(pl.coords_text(s, "target") is not None)
        self.copy_ref_btn.setEnabled(pl.coords_text(s, "reference") is not None)
        for b in self.copy_buttons.values():
            b.setEnabled(pl.coords_text(s, "target") is not None)

    def copy_coords(self, which: str, part: str = "both") -> str | None:
        """Bare values only ('20 17 47.2  +38 01 59', or RA / Dec alone) to the clipboard."""
        text = pl.coords_text(self.host.session, which, part)
        if text is None:
            return None
        QGuiApplication.clipboard().setText(text)
        what = {"ra": "RA", "dec": "Dec"}.get(part, "RA+Dec")
        self.plan_result.set("ok", f"Copied {which} {what}: {text}")
        self.plan_result.show()
        if part != "both":
            QToolTip.showText(QCursor.pos(), f"Copied {text}")
        return text

    def copy_plan(self) -> None:
        QGuiApplication.clipboard().setText(self.plan_output())
        self.plan_result.set("ok", "Copied to the clipboard.")
        self.plan_result.show()

    def save_plan(self, path: str | None = None) -> Path | None:
        json_fmt = self.fmt.index() == 1
        if path is None:
            folder = self.host.session.folder or Path.home()
            name = "observing_plan.json" if json_fmt else "observing_plan.txt"
            path, _ = QFileDialog.getSaveFileName(self, "Save observing plan", str(folder / name),
                                                  "JSON (*.json)" if json_fmt else "Text (*.txt)")
            if not path:
                return None
        out = Path(path)
        out.write_text(self.plan_output() + "\n", encoding="utf-8")
        self.host.session.log(f"Plan: observing plan saved to {out}")
        self.plan_result.set("ok", f"Saved: {out}")
        self.plan_result.show()
        return out
