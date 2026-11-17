"""Reduce > Files: the optional suggestion from the Acquisition tab's acquisition_sets.json, a sets file
chosen by hand, or a scan of an old folder. Read-only towards the sets file: Files never writes it. Uses only
core/acq_sets.py and core/subs.py (no Acquisition screen code)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core import acq_sets

from .. import theme
from ..widgets.common import Card, label

if TYPE_CHECKING:
    from ..main_window import MainWindow

SCAN_SLOT = "scanacq.files"
ASSIGN = ("ignore", "target", "reference")
ASSIGN_TITLE = {"ignore": "Ignore", "target": "Target", "reference": "Reference"}


class AssignDialog(QDialog):
    """Groups found by Scan folder; the user decides Target / Reference / Ignore (OBJECT is only a hint)."""

    def __init__(self, groups: list[dict[str, Any]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Assign the scanned subs")
        self.setMinimumWidth(820)
        self.groups = groups
        lay = QVBoxLayout(self)
        lay.addWidget(label("Choose what each group is. The OBJECT keyword and the exposure are shown as hints "
                            "only; nothing is assigned automatically.", "muted", wrap=True))
        self.table = QTableWidget(len(groups), 6)
        self.table.setHorizontalHeaderLabels(("Folder", "OBJECT (hint)", "Exposure", "Subs", "Flagged", "Use as"))
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.combos: list[QComboBox] = []
        for r, g in enumerate(groups):
            recs = g["records"]
            vals = (Path(g["folder"]).name, g["object"] or "—", f"{g['exptime']:g} s" if g["exptime"] else "—",
                    str(len(recs)), str(sum(1 for x in recs if x.flags)))
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))
            box = QComboBox()
            box.addItems([ASSIGN_TITLE[a] for a in ASSIGN])
            self.table.setCellWidget(r, 5, box)
            self.combos.append(box)
        lay.addWidget(self.table)
        names = QHBoxLayout()
        self.names = {}
        for role in ("target", "reference"):
            e = QLineEdit()
            e.setPlaceholderText(f"{ASSIGN_TITLE[role]} name (optional)")
            names.addWidget(e)
            self.names[role] = e
        lay.addLayout(names)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def set_assignment(self, assignment: list[str]) -> None:
        for box, a in zip(self.combos, assignment, strict=True):
            box.setCurrentIndex(ASSIGN.index(a))

    def assignment(self) -> list[str]:
        return [ASSIGN[b.currentIndex()] for b in self.combos]


class SuggestionCard(Card):
    def __init__(self, host: MainWindow, screen: Any) -> None:
        super().__init__("Suggestion")
        self.host, self.screen = host, screen
        self.doc: dict[str, Any] | None = None
        self.provenance = ""
        self.provenance_chip = label("", "chip")
        self.provenance_chip.hide()
        self.head.insertWidget(1, self.provenance_chip)
        self.problems = label("", style=f"color: {theme.RED};")
        self.problems.hide()
        self.head.insertWidget(2, self.problems)
        self.use_btn = QPushButton("Use suggestion")
        self.use_btn.setToolTip("Tick these files in the lists below and fill in the names. Nothing else changes.")
        self.use_btn.clicked.connect(lambda: self.use())
        load = QPushButton("Load sets file…")
        load.clicked.connect(lambda: self.load_file())
        self.scan_btn = QPushButton("Scan folder…")
        self.scan_btn.setToolTip("Analyse an old folder of subs and assign them to Target / Reference")
        self.scan_btn.clicked.connect(lambda: self.scan())
        for b in (self.use_btn, load, self.scan_btn):
            self.head.addWidget(b)
        self.text = label("", wrap=True)
        self.body.addWidget(self.text)
        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(lambda: self.host.runner.cancel(SCAN_SLOT))
        row.addWidget(self.progress, 1)
        row.addWidget(self.cancel_btn)
        self.progress.hide()
        self.cancel_btn.hide()
        self.body.addLayout(row)
        self.host.runner.progress.connect(self._progress)
        self.show_doc()

    # ---- sources ----------------------------------------------------------------------------------
    def load_session(self) -> None:
        folder = self.host.session.folder
        stored = self.host.session.params.get("acq_scan")
        lights = [self.host.session.inputs[r].get("folder", "") for r in ("target", "reference")]
        found = acq_sets.find_sets(folder, lights) if folder is not None else None
        if found is not None:
            self.read(found)
            if found.parent != folder:
                self.provenance += f" · in {found.parent.name}"
        elif isinstance(stored, dict) and stored.get("doc"):
            self.doc, self.provenance = stored["doc"], "folder scan"
            self.show_doc()
        else:
            self.doc, self.provenance = None, ""
            self.show_doc()

    def read(self, path: Path) -> bool:
        try:
            self.doc = acq_sets.load_sets(path)
        except (OSError, ValueError) as exc:
            self.doc = None
            self.text.setText(f"Could not read {path.name}: {exc}")
            return False
        when = self.doc["night"] or time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))
        self.provenance = f"sets file from {when}"
        self.show_doc()
        return True

    def load_file(self, path: str | None = None) -> bool:
        if path is None:
            start = str(self.host.session.folder or "")
            path, _ = QFileDialog.getOpenFileName(self, "Load sets file", start, "Sets (*.json)")
            if not path:
                return False
        ok = self.read(Path(path))
        if ok:
            self.host.session.log(f"Files: suggestion loaded from {path}")
        return ok

    def scan(self, folder: str | None = None) -> None:
        if folder is None:
            folder = QFileDialog.getExistingDirectory(self, "Folder with the subs of one night",
                                                      str(self.host.settings["last_folder"] or ""))
            if not folder:
                return
        self.progress.setValue(0)
        self.progress.show()
        self.cancel_btn.show()
        self.scan_btn.setEnabled(False)
        self.text.setText(f"Scanning {folder}…")
        self.host.runner.submit(SCAN_SLOT, acq_sets.scan_folder, folder)

    def _progress(self, slot: str, f: float) -> None:
        if slot == SCAN_SLOT:
            self.progress.setValue(int(f * 1000))

    def scan_finished(self, result: dict[str, Any] | None, assignment: list[str] | None = None,
                      names: dict[str, str] | None = None) -> None:
        """Worker done (or cancelled/failed with None). Opens the assignment dialog unless one is given."""
        self.progress.hide()
        self.cancel_btn.hide()
        self.scan_btn.setEnabled(True)
        if not result or not result["groups"]:
            self.text.setText("Scan stopped." if result is None else "No light subs found in that folder.")
            return
        if assignment is None:
            dlg = AssignDialog(result["groups"], self)
            if not dlg.exec():
                self.show_doc()
                return
            assignment = dlg.assignment()
            names = {r: e.text().strip() for r, e in dlg.names.items()}
        self.doc = acq_sets.sets_from_groups(result["groups"], assignment, names)
        self.doc = acq_sets.normalise(self.doc, source="folder scan")
        self.provenance = "folder scan"
        self.host.session.params["acq_scan"] = {"doc": self.doc}  # kept in the session, no sets file written
        self.host.session.save()
        self.host.session.log(f"Files: folder scan of {result['folder']}: {acq_sets.summary(self.doc)}")
        self.show_doc()

    # ---- display and apply -------------------------------------------------------------------------
    def show_doc(self) -> None:
        doc = self.doc
        self.use_btn.setEnabled(doc is not None)
        self.provenance_chip.setVisible(doc is not None)
        if doc is None:
            self.text.setText("No acquisition sets for this session. Choose lights below as usual, load a sets "
                              "file, or scan a folder of subs.")
            self.problems.hide()
            return
        self.provenance_chip.setText(self.provenance)
        self.text.setText("From Acquisition: " + acq_sets.summary(doc))
        bad = acq_sets.check_files(doc)
        parts = ([f"{len(bad['missing'])} missing"] if bad["missing"] else []) + (
            [f"{len(bad['changed'])} changed"] if bad["changed"] else [])
        self.problems.setText(" · ".join(parts))
        self.problems.setToolTip("\n".join(bad["missing"] + bad["changed"]))
        self.problems.setVisible(bool(parts))

    def use(self) -> None:
        """Tick the good files in the existing lists (adding them if absent), untick the set's excluded ones,
        fill the names. Other frames already listed are left as they are."""
        doc = self.doc
        if doc is None:
            return
        for role in ("target", "reference"):
            side = doc[role]
            good = [str(acq_sets.absolute(doc, f)) for f in side["good"]]
            bad = [str(acq_sets.absolute(doc, e["file"])) for e in side["excluded"]]
            if not good and not bad:
                continue
            card = self.screen.cards[role]
            lights = card.data["lights"]
            index = {str(Path(i["path"])): i for i in lights}
            for p, used in [(g, True) for g in good] + [(b, False) for b in bad]:
                if p in index:
                    index[p]["used"] = used
                else:
                    item = {"path": p, "used": used}
                    lights.append(item)
                    index[p] = item
            if good and not card.data.get("folder"):
                card.data["folder"] = str(Path(good[0]).parent)
            if side["name"]:
                card.data["name"] = side["name"]
            card.load_from_session()
            card._measure_peaks()
            card.changed.emit()
        self.host.session.log(f"Files: suggestion used ({self.provenance}): {acq_sets.summary(doc)}")
