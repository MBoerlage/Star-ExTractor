"""Plan › Planned pairs: a list of target + reference pairs to observe (core/planned_pairs.py), kept in the settings
(plan.pairs). The Target card and the Reference candidates above are the editor: selecting a row loads it there
(the sky plots then show that target), Add pair adds what the editor holds, Update pair writes the editor back to
the selected row. Load for acquisition sets the names and coordinates of the Acquisition sets (never a folder).

Visible window and Moon separation per row are computed in a worker thread (core/sky.py, a few ms per pair)
whenever the observing conditions change; they are never stored. "Planned pairs" are not the Pair selector of the
top row: that one is the pair loaded in the reduce flow and is not touched here.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from starex_reduce.core import planned_pairs as pp

from .. import theme
from ..widgets.common import Card, InfoButton, StatusLine, label
from ..widgets.table_cols import ProportionalColumns

if TYPE_CHECKING:
    from .plan import PlanScreen

COLUMNS = ("#", "Target", "Reference", "Visible", "Moon sep.", "Status")
STATUS_COL = 5
EMPTY = "No pairs yet. Enter a target, pick a reference star, then choose Add pair."
IN_ACQ = "● In Acquisition"  # chip-ok (green family; in night/red the text and the dot carry it)
PICK_REF = "! pick reference"  # chip-warn, amber
READY = "Ready"  # plain muted text, no chip: nothing to do before loading
WEIGHTS = [3, 20, 20, 16, 10, 31]  # #, Target, Reference, Visible, Moon sep., Status (widest)


class _Bridge(QObject):
    done = Signal(int, object)


class PairsCard(Card):
    def __init__(self, screen: PlanScreen) -> None:
        super().__init__("Planned pairs")
        self.screen = screen
        self.host = screen.host
        self.head.insertWidget(1, InfoButton(
            "Targets with their reference star that you plan to observe. Select a row to show it in the Target "
            "card, the reference list and the sky plots above; Load for acquisition sets the names and coordinates "
            "of the Target and Reference sets in Acquisition (not the folders). Not the Pair selector at the top: "
            "that one is the pair loaded for reduction."))
        self.pairs: list[dict[str, Any]] = pp.load(self._plan())
        self.infos: dict[str, dict[str, Any]] = {}
        self.selected_id: str | None = str(self._plan().get("selected") or "") or None
        self.computing = False
        self._gen = 0
        self._filling = False
        self.bridge = _Bridge()
        self.bridge.done.connect(self._on_infos)
        self._build()
        self.refresh()
        self.host.planned_link_changed.connect(self.refresh)

    # ---- construction ------------------------------------------------------------------------------------------
    def _build(self) -> None:
        t = self.table = QTableWidget(0, len(COLUMNS))
        t.setObjectName("plannedPairs")
        t.setHorizontalHeaderLabels(COLUMNS)
        t.verticalHeader().hide()
        t.setShowGrid(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.verticalHeader().setDefaultSectionSize(34)
        t.setWordWrap(False)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hh = t.horizontalHeader()
        self.columns = ProportionalColumns(t, WEIGHTS)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        t.itemSelectionChanged.connect(self._selection_changed)
        t.cellDoubleClicked.connect(lambda r, _c: self.load_for_acquisition(r))
        self.body.addWidget(t)
        self.empty = label(EMPTY, "muted", wrap=True)
        self.body.addWidget(self.empty)

        note = QHBoxLayout()
        note.addWidget(label("Note", "muted"))
        self.note = QLineEdit()
        self.note.setPlaceholderText("A note for the selected pair (kept with it, printed in the plan)")
        self.note.editingFinished.connect(self._note_edited)
        note.addWidget(self.note, 1)
        self.body.addLayout(note)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.add_btn = QPushButton("Add pair")
        self.add_btn.setToolTip("Add the target and the chosen reference star above as a new pair")
        self.add_btn.clicked.connect(lambda: self.add_pair())
        self.update_btn = QPushButton("Update pair")
        self.update_btn.setToolTip("Write the target and reference above into the selected pair")
        self.update_btn.clicked.connect(lambda: self.update_pair())
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(lambda: self.remove_pair())
        self.up_btn = QPushButton("Move up")
        self.up_btn.clicked.connect(lambda: self.move_selected(-1))
        self.down_btn = QPushButton("Move down")
        self.down_btn.clicked.connect(lambda: self.move_selected(1))
        self.sort_btn = QPushButton("Sort by transit time")
        self.sort_btn.setToolTip("Order the pairs by the time each target crosses the meridian in the window")
        self.sort_btn.clicked.connect(lambda: self.sort_by_transit())
        self.load_btn = QPushButton("Load for acquisition")  # amber when usable (one amber button per view)
        self.load_btn.setToolTip("Set the names and coordinates of the Target and Reference sets in Acquisition. "
                                 "You still choose the capture folders there.")
        self.load_btn.clicked.connect(lambda: self.load_for_acquisition())
        for b in (self.add_btn, self.update_btn, self.remove_btn, self.up_btn, self.down_btn, self.sort_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.load_btn)
        self.body.addLayout(row)
        self.status = StatusLine("off", "")
        self.status.hide()
        self.body.addWidget(self.status)

    # ---- settings ----------------------------------------------------------------------------------------------
    def _plan(self) -> dict[str, Any]:
        return dict(self.host.settings["plan"] or {})

    def save(self) -> None:
        plan = self._plan()
        plan["pairs"] = pp.dump(self.pairs)
        if self.selected_id:
            plan["selected"] = self.selected_id
        else:
            plan.pop("selected", None)
        self.host.settings["plan"] = plan
        self.host.settings.save()

    def link(self) -> dict[str, Any]:
        """The pair loaded in Acquisition (settings plan.in_acq: id, target, reference, edited), or {}."""
        d = self._plan().get("in_acq")
        return d if isinstance(d, dict) else {}

    # ---- the table ---------------------------------------------------------------------------------------------
    def index_of(self, pid: str | None) -> int | None:
        return next((i for i, p in enumerate(self.pairs) if p["id"] == pid), None)

    def selected(self) -> dict[str, Any] | None:
        i = self.index_of(self.selected_id)
        return None if i is None else self.pairs[i]

    def in_acquisition(self, pair: dict[str, Any]) -> bool:
        """The chip: this pair was loaded and the Acquisition names still match it."""
        ln = self.link()
        ref = (pair.get("reference") or {}).get("name", "")
        return (ln.get("id") == pair["id"] and not ln.get("edited") and ln.get("target") == pair["target"]["name"]
                and (not ref or ln.get("reference") == ref))

    def status_of(self, pair: dict[str, Any]) -> list[str]:
        out = [IN_ACQ] if self.in_acquisition(pair) else []
        if not pair.get("reference"):
            out.append(PICK_REF)
        return out

    def refresh(self) -> None:
        """Rebuild the rows (cheap) and keep the selection."""
        self._filling = True
        t = self.table
        t.setRowCount(len(self.pairs))
        mono = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        for r, p in enumerate(self.pairs):
            info = self.infos.get(p["id"]) or {}
            sep = info.get("moon_sep")
            cells = (str(r + 1), p["target"]["name"], (p.get("reference") or {}).get("name", "—"),
                     info.get("visible", "—"), "—" if sep is None else f"{sep:.0f}°")
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if c in (0, 4):
                    it.setTextAlignment(mono)
                if c == 2 and not p.get("reference"):
                    it.setToolTip("No reference star yet: pick one above and choose Update pair")
                t.setItem(r, c, it)
            self._set_status(r, self.status_of(p))
        rows = sum(t.rowHeight(r) for r in range(t.rowCount()))
        t.setFixedHeight(t.horizontalHeader().height() + max(rows, t.verticalHeader().defaultSectionSize())
                         + 2 * t.frameWidth() + 2)
        t.setVisible(bool(self.pairs))
        self.empty.setVisible(not self.pairs)
        i = self.index_of(self.selected_id)
        t.clearSelection()
        if i is not None:
            t.selectRow(i)
        else:
            self.selected_id = None
        self._filling = False
        self._update_buttons()

    def _set_status(self, row: int, chips: list[str]) -> None:
        self.table.setItem(row, STATUS_COL, QTableWidgetItem(""))
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(6)
        for text in chips:
            chip = label(text, "chip-ok" if text == IN_ACQ else "chip-warn")
            chip.setObjectName("inAcq" if text == IN_ACQ else "pickRef")
            lay.addWidget(chip)
        if not chips:
            ready = label(READY, "muted")
            ready.setObjectName("ready")
            lay.addWidget(ready)
        lay.addStretch(1)
        self.table.setCellWidget(row, STATUS_COL, box)

    def chips_of(self, row: int) -> list[str]:
        w = self.table.cellWidget(row, STATUS_COL)
        return [c.text() for c in w.findChildren(QLabel) if c.objectName() != "ready"] if w else []

    def status_text(self, row: int) -> str:
        """Everything the Status cell says: its chips, or Ready."""
        w = self.table.cellWidget(row, STATUS_COL)
        return " ".join(c.text() for c in w.findChildren(QLabel)) if w else ""

    def _selection_changed(self) -> None:
        if self._filling:
            return
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        self.select(rows[0].row())

    def select(self, row: int) -> None:
        """Select a row: its target and reference go into the editor above (and so into the sky plots)."""
        pair = self.pairs[row]
        self.selected_id = pair["id"]
        self.save()
        self.screen.load_editor(pair)
        self.note.setText(pair["note"])
        self._filling = True
        self.table.selectRow(row)
        self._filling = False
        self._update_buttons()

    def select_id(self, pid: str) -> bool:
        i = self.index_of(pid)
        if i is None:
            return False
        self.select(i)
        self.screen.scroll.ensureWidgetVisible(self.table)
        return True

    def editor_pair(self) -> dict[str, Any] | None:
        from starex_reduce.core import plan as pl

        return pp.from_editor(pl.plan_state(self.host.session))

    def editor_changed(self) -> None:
        self._update_buttons()

    def _update_buttons(self) -> None:
        sel = self.selected()
        ed = self.editor_pair()
        self.update_btn.setEnabled(sel is not None and ed is not None and not pp.same_content(sel, ed))
        for b in (self.remove_btn, self.load_btn, self.note):
            b.setEnabled(sel is not None)
        i = self.index_of(self.selected_id)
        self.up_btn.setEnabled(i is not None and i > 0)
        self.down_btn.setEnabled(i is not None and i < len(self.pairs) - 1)
        self.sort_btn.setEnabled(len(self.pairs) > 1)
        if sel is None:
            self.note.clear()
        use = getattr(self.screen, "use_btn", None)
        primary = sel is not None and not (use is not None and use.isEnabled())
        if (self.load_btn.property("role") == "primary") != primary:
            self.load_btn.setProperty("role", "primary" if primary else None)
            theme.repolish(self.load_btn)

    def _say(self, state: str, text: str) -> None:
        self.status.set(state, text)
        self.status.show()

    # ---- editing the list --------------------------------------------------------------------------------------
    def add_pair(self) -> dict[str, Any] | None:
        pair = self.editor_pair()
        if pair is None or pair["target"]["ra_deg"] is None:
            self._say("err", "Enter a target with a position first (Check with SIMBAD, or type RA/Dec).")
            return None
        dup = pp.duplicate_of(self.pairs, pair)
        self.pairs.append(pair)
        self.selected_id = pair["id"]
        self.save()
        self.refresh()
        self.note.setText("")
        name = pair["target"]["name"]
        if dup is not None:
            self._say("warn", f"{name} is already planned (row {dup + 1}). Added anyway.")
        elif pair.get("reference") is None:
            self._say("warn", f"Added {name} without a reference star: pick one and choose Update pair.")
        else:
            self._say("ok", f"Added {name} + {pair['reference']['name']}.")
        self.host.session.log(f"Plan: planned pair added: {name}"
                              + (f" + {pair['reference']['name']}" if pair.get("reference") else ""))
        self.recompute()
        return pair

    def update_pair(self) -> bool:
        sel, ed = self.selected(), self.editor_pair()
        if sel is None or ed is None or pp.same_content(sel, ed):
            return False
        i = self.index_of(sel["id"])
        assert i is not None
        dup = pp.duplicate_of(self.pairs, {**ed, "id": sel["id"]})
        self.pairs[i] = {**sel, "target": ed["target"], "reference": ed["reference"]}
        self.save()
        self.refresh()
        name = ed["target"]["name"]
        self._say("warn" if dup is not None else "ok", f"{name} is also in row {dup + 1}." if dup is not None
                  else f"Updated row {i + 1}: {name}" + (f" + {ed['reference']['name']}" if ed["reference"] else ""))
        self.recompute()
        return True

    def remove_pair(self) -> None:
        i = self.index_of(self.selected_id)
        if i is None:
            return
        gone = self.pairs.pop(i)
        self.selected_id = self.pairs[min(i, len(self.pairs) - 1)]["id"] if self.pairs else None
        self.save()
        self.refresh()
        self._say("off", f"Removed {gone['target']['name']}.")
        if self.selected_id:
            self.select(self.index_of(self.selected_id) or 0)
        self.host.session.log(f"Plan: planned pair removed: {gone['target']['name']}")

    def move_selected(self, delta: int) -> None:
        i = self.index_of(self.selected_id)
        if i is None:
            return
        pp.move(self.pairs, i, delta)
        self.save()
        self.refresh()
        self.screen._show_plan()

    def sort_by_transit(self) -> bool:
        if self.screen.conditions.current() is None:
            self._say("warn", "Set your location (Observing conditions) to sort by transit time.")
            return False
        pp.sort_by_transit(self.pairs, {pid: i.get("transit") for pid, i in self.infos.items()})
        self.save()
        self.refresh()
        self.screen._show_plan()
        self._say("ok", "Sorted by transit time.")
        return True

    def _note_edited(self) -> None:
        sel = self.selected()
        if sel is None or sel["note"] == self.note.text().strip():
            return
        sel["note"] = self.note.text().strip()
        self.save()
        self.screen._show_plan()

    def replace_all(self, pairs: list[dict[str, Any]]) -> None:
        self.pairs = pairs
        self.selected_id = None
        self.save()
        self.refresh()
        self.recompute()

    # ---- visibility per row (a worker) ---------------------------------------------------------------------------
    def idle(self) -> bool:
        return not self.computing

    def recompute(self) -> None:
        """Visible window, Moon separation and transit of every pair for the current conditions."""
        self._gen += 1
        gen = self._gen
        cond = self.screen.conditions
        loc = cond.current()
        pairs = [dict(p) for p in self.pairs]
        if loc is None or not pairs:
            self._on_infos(gen, {})
            return
        w, min_alt = cond.window(), float(cond.min_alt.value())
        self.computing = True

        def run() -> None:
            out: dict[str, dict[str, Any]] = {}
            for p in pairs:
                try:
                    out[p["id"]] = pp.row_info(pp.sky_of(p, loc.lat, loc.lon, w, min_alt))
                except Exception:  # noqa: BLE001 - one odd pair: an empty row, not an error dialog
                    out[p["id"]] = pp.row_info(None)
            self.bridge.done.emit(gen, out)

        threading.Thread(target=run, name="plan-pairs", daemon=True).start()

    def _on_infos(self, gen: int, infos: dict[str, dict[str, Any]]) -> None:
        if gen != self._gen:
            return
        self.computing = False
        self.infos = infos
        self.refresh()
        self.screen._show_plan()

    # ---- Acquisition -------------------------------------------------------------------------------------------
    def load_for_acquisition(self, row: int | None = None, ask=None) -> bool:
        """Set the Acquisition sets' names and coordinates from the pair (asks first when Acquisition holds subs
        or a watch for another target). Switches to Acquisition when done."""
        if row is not None:
            self.select(row)
        pair = self.selected()
        if pair is None:
            return False
        acq = self.host.ensure_acquisition()
        if not acq.load_planned_pair(pair, ask=ask):
            return False
        self.refresh()
        self.host.show_tab("acquisition")
        return True
