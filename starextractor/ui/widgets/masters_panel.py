"""The master library table and Create masters of Acquisition > Pre Processing settings. Both are bound to
the main window's one MasterLibrary; any change emits `host.library_changed`, so
every panel shows the same data without a restart."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from starextractor.core.files_stage import build_masters_job, tolerances
from starextractor.core.frames import BIAS, DARK, FLAT, scan
from starextractor.core.masters import fmt_exp, group_for_masters

from .. import dialogs, theme
from .common import Card, label

if TYPE_CHECKING:
    from ..main_window import MainWindow

UsedBy = Callable[[], dict[str, list[str]]]  # master id → the sets whose plan uses it


def short_path(path: str, parts: int = 2) -> str:
    """`…\\2026-08-20\\target` style: the last folders only (the full path goes in the tooltip)."""
    p = Path(path)
    tail = p.parts[-parts:]
    return path if len(p.parts) <= parts + 1 else "…\\" + "\\".join(tail)


def theme_colour(hex_colour: str):
    from PySide6.QtGui import QColor
    return QColor(hex_colour)


def mono_item(text: str, right: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFont(theme.mono_font(12))
    if right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


class LibraryCard(Card):
    """Master library: tick the masters the auto-pick may use; Import master… and Remove."""

    def __init__(self, host: MainWindow, used_by: UsedBy, title: str = "Master library") -> None:
        super().__init__(title, "Tick the masters the auto-pick may use. The best match by "
                                "exposure, gain, temperature and size is picked automatically.")
        self.host, self.used_by = host, used_by
        imp = QPushButton("Import master…")
        imp.clicked.connect(self.import_master)
        rm = QPushButton("Remove")
        rm.clicked.connect(self.remove_master)
        self.head.addWidget(imp)
        self.head.addWidget(rm)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["USE", "TYPE", "EXPOSURE", "GAIN", "OFFSET", "TEMP", "SIZE", "FRAMES", "CREATED", "USED BY"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 48)
        self.table.itemChanged.connect(self._item_changed)
        self.body.addWidget(self.table)
        self.empty = label("The library is empty. Create masters below from raw darks and bias.", "muted")
        self.body.addWidget(self.empty)
        host.library_changed.connect(self.refresh)

    def refresh(self) -> None:
        lib = self.host.library
        used = self.used_by()
        t = self.table
        t.blockSignals(True)
        t.setRowCount(len(lib.masters))
        for r, m in enumerate(sorted(lib.masters, key=lambda m: (m.kind, m.exptime))):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            use.setCheckState(Qt.CheckState.Checked if m.enabled else Qt.CheckState.Unchecked)
            use.setData(Qt.ItemDataRole.UserRole, m.id)
            t.setItem(r, 0, use)
            kind = m.kind.title() + (" (−bias)" if m.bias_subtracted else "")
            vals = [kind, fmt_exp(m.exptime), "—" if m.gain is None else f"{m.gain:g}",
                    "—" if m.offset is None else f"{m.offset:g}",
                    "—" if m.temp is None else f"{m.temp:.1f} °C".replace("-", "−"),
                    f"{m.size[0]}×{m.size[1]}", str(m.nframes), m.created]
            for c, v in enumerate(vals, start=1):
                it = mono_item(v) if c > 1 else QTableWidgetItem(v)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                t.setItem(r, c, it)
            u = " and ".join(used.get(m.id, [])) or "—"
            ui = QTableWidgetItem(u)
            ui.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if m.id in used:
                ui.setForeground(theme_colour(theme.GREEN))
            t.setItem(r, 9, ui)
        t.setFixedHeight(42 + 38 * max(1, min(len(lib.masters), 8)))
        t.blockSignals(False)
        t.setVisible(bool(lib.masters))
        self.empty.setVisible(not lib.masters)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        mid = item.data(Qt.ItemDataRole.UserRole)
        self.host.library.set_enabled(mid, item.checkState() == Qt.CheckState.Checked)
        self.host.library_changed.emit()

    def import_master(self) -> None:
        files = dialogs.open_files(self, "Import master frames", "", "FITS (*.fits *.fit *.fts)")
        for f in files:
            try:
                m = self.host.library.import_file(Path(f))
                self.host.session.log(f"Imported master {m.label()} from {Path(f).name}")
            except ValueError as exc:
                self.host.session.log(f"Import failed: {exc}")
        if files:
            self.host.library_changed.emit()

    def remove_master(self) -> None:
        rows = {i.row() for i in self.table.selectedItems()}
        for r in rows:
            mid = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            m = self.host.library.get(mid)
            if m is not None:
                self.host.library.remove(mid)
                self.host.session.log(f"Removed master {m.label()} from the library")
        if rows:
            self.host.library_changed.emit()


class MasterBin(Card):
    """One drop/choose bin in Create masters (Darks, Bias, Flats)."""

    def __init__(self, kind: str, title: str, note: str, host: MainWindow) -> None:
        super().__init__(title, note, inner=True)
        self.kind, self.host = kind, host
        self.title.setProperty("role", None)
        self.title.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.groups: list = []
        row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        self.folder.setPlaceholderText(f"Folder with raw {title.lower()}…")
        b = QPushButton("Browse…")
        b.clicked.connect(self.browse)
        row.addWidget(self.folder, 1)
        row.addWidget(b)
        self.body.addLayout(row)
        self.found = label("", "mono", wrap=True)
        self.body.addWidget(self.found)

    def browse(self) -> None:
        start = self.host.settings["last_folder"] or ""
        folder = dialogs.existing_directory(self, f"Raw {self.kind} frames", start)
        if folder:
            self.set_folder(folder)

    def set_folder(self, folder: str) -> None:
        self.folder.setText(short_path(folder))
        self.folder.setToolTip(folder)
        frames = [f for f in scan(folder, self.kind) if f.frame_type == self.kind or f.error]
        self.groups = group_for_masters([f for f in frames if not f.error])
        parts = [f"{fmt_exp(g[0].exptime or 0)} × {len(g)}" for g in self.groups]
        bad = sum(1 for f in frames if f.error)
        self.found.setText(
            (f"Found: {' · '.join(parts)}\nWill create {len(self.groups)} master"
             f"{'s' if len(self.groups) != 1 else ''}" if parts else f"No {self.kind} frames found")
            + (f"\n{bad} unreadable file(s) skipped" if bad else ""))


class CreateMastersCard(Card):
    """Darks (required), Bias and Flats (optional): one master per exposure time, added to the library. The
    "masters" job slot reports back to the card that started it (`host.masters_owner`)."""

    def __init__(self, host: MainWindow) -> None:
        super().__init__("Create masters", "Point at raw calibration frames. One master is made per "
                                           "exposure time found, then added to the library.")
        self.host = host
        bins = QHBoxLayout()
        bins.setSpacing(12)
        self.bins = {
            DARK: MasterBin(DARK, "Darks", "required", host),
            BIAS: MasterBin(BIAS, "Bias", "optional", host),
            FLAT: MasterBin(FLAT, "Flats", "optional", host),
        }
        self.bin_buttons: dict[str, QPushButton] = {}
        for kind, b in self.bins.items():
            bins.addWidget(b, 1)
            btn = QPushButton("Create masters")  # one per bin
            btn.setProperty("role", "primary" if kind == DARK else None)
            btn.clicked.connect(lambda _=False, k=kind: self.create_masters([k]))
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(btn)
            b.body.addLayout(row)
            self.bin_buttons[kind] = btn
        self.body.addLayout(bins)
        row = QHBoxLayout()
        row.addWidget(label("Method", "muted"))
        self.method = QComboBox()
        self.method.addItem("Median", "median")
        self.method.addItem("Sigma-clip mean", "sigma_clip_mean")
        row.addWidget(self.method)
        self.sub_bias = QCheckBox("Subtract bias from darks and flats")
        self.sub_bias.setChecked(True)
        row.addSpacing(12)
        row.addWidget(self.sub_bias)
        row.addStretch(1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(160, 8)
        self.progress.hide()
        row.addWidget(self.progress)
        self.body.addLayout(row)
        self.status = label("", "muted", wrap=True)
        self.status.hide()
        self.body.addWidget(self.status)

    def _buttons(self) -> list[QPushButton]:
        return list(self.bin_buttons.values())

    def create_masters(self, kinds: list[str] | None = None) -> None:
        groups = [(kind, g) for kind, b in self.bins.items() if kinds is None or kind in kinds for g in b.groups]
        if not groups:
            self.host.session.log("Create masters: choose a folder with raw darks or bias first")
            return
        if self.host.runner.is_busy("masters"):
            self.host.session.log("Create masters: wait until the masters being made are done")
            return
        tol = tolerances(self.host.settings["tolerances"])
        for b in self._buttons():
            b.setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self.host.masters_owner = self
        self.host.runner.submit("masters", build_masters_job, groups, self.host.library,
                                self.method.currentData(), self.sub_bias.isChecked(), tol,
                                log=self.host.session.log)

    def masters_progress(self, f: float) -> None:
        self.progress.setValue(int(f * 1000))

    def masters_done(self, lines: list[str] | None, error: str | None = None) -> None:
        for b in self._buttons():
            b.setEnabled(True)
        self.progress.hide()
        for line in lines or []:
            self.host.session.log(line)
        if error:
            self.host.session.log(f"Create masters failed: {error}")
        shown = [ln for ln in lines or [] if ln.startswith("Master")] or ([f"Failed: {error}"] if error else [])
        self.status.setText("\n".join(shown))
        self.status.setVisible(bool(shown))
        self.host.library_changed.emit()  # the worker added the new masters to the shared library object
