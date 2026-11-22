"""Settings › Data locations (data root + Stacks, Masters, Processed, Published), the first-run dialog that asks
for the data root, and the start-up check for missing or read-only locations (core/datalocs.py)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from starextractor.core import datalocs as dl
from starextractor.core.settings import Settings

from . import dialogs, theme

ROW_H = 42  # a styled button is about 40 px tall: rows never squeeze it
def icon(state: str) -> tuple[str, str]:
    return {dl.OK: ("✓", theme.GREEN), dl.MISSING: ("✗", theme.RED), dl.READONLY: ("!", theme.AMBER)}[state]
HELP = {"stacks": "Stacked images, one folder per night, with the night's sets file.",
        "masters": "Calibration masters (bias, darks, flats) in <camera>\\<year>, with index.json.",
        "processed": "One folder per target + reference pair: logbook.json, 1D spectra, work\\ (stage cache).",
        "published": "Promoted AVSpec files: <year>\\<night>_<Target>_v<N>\\ with submission.json."}


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "muted")
    lbl.setWordWrap(True)
    lbl.setStyleSheet("font-size: 12px;")
    return lbl


class DataLocations(QWidget):
    """The data root and the four paths. A path left empty is <root>\\<Name>; one typed is used as is."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(8)
        self.root = QLineEdit(str(settings.paths().root) if settings["data_root"] else "")
        self.root.setPlaceholderText(str(dl.default_root()))
        self.root.textChanged.connect(self.refresh)
        b = QPushButton("Browse…")
        b.clicked.connect(lambda: self._browse(self.root))
        g.addWidget(QLabel("Data root"), 0, 0)
        g.addWidget(self.root, 0, 2)
        g.addWidget(b, 0, 3)
        g.setRowMinimumHeight(0, ROW_H)
        over = dict(settings["data_paths"] or {})
        self.fields: dict[str, QLineEdit] = {}
        self.icons: dict[str, QLabel] = {}
        for i, key in enumerate(dl.KEYS, start=1):
            icon = QLabel()
            icon.setFixedWidth(14)
            e = QLineEdit(str(over.get(key) or ""))
            e.setToolTip(HELP[key])
            e.textChanged.connect(self.refresh)
            br = QPushButton("Browse…")
            br.clicked.connect(lambda _=False, f=e: self._browse(f))
            g.addWidget(QLabel(dl.NAMES[key]), i, 0)
            g.addWidget(icon, i, 1)
            g.addWidget(e, i, 2)
            g.addWidget(br, i, 3)
            g.setRowMinimumHeight(i, ROW_H)
            self.fields[key], self.icons[key] = e, icon
        g.setColumnStretch(2, 1)
        row = QHBoxLayout()
        self.create_btn = QPushButton("Create missing folders")
        self.create_btn.clicked.connect(self.create_missing)
        self.summary = _muted("")
        row.addWidget(self.create_btn)
        row.addWidget(self.summary, 1)
        g.setRowMinimumHeight(len(dl.KEYS) + 1, ROW_H + 4)
        g.addLayout(row, len(dl.KEYS) + 1, 0, 1, 4)
        hint = QLabel(r"An empty path means <data root>\<Name>. Raw captures stay where they are.")
        hint.setProperty("role", "muted")
        hint.setStyleSheet("font-size: 12px;")
        hint.setToolTip("Type or browse to put one location elsewhere, e.g. Published on another drive.")
        g.addWidget(hint, len(dl.KEYS) + 2, 0, 1, 4)
        self.refresh()

    def _browse(self, field: QLineEdit) -> None:
        d = dialogs.existing_directory(self, "Choose a folder", field.text() or field.placeholderText())
        if d:
            field.setText(str(Path(d)))

    def values(self) -> dict[str, Any]:
        """{data_root, data_paths} as typed (empty path entries = the default under the root)."""
        root = self.root.text().strip() or self.root.placeholderText()
        paths = {}
        for k, e in self.fields.items():
            v = e.text().strip()
            paths[k] = "" if not v or Path(v) == Path(root) / dl.NAMES[k] else v
        return {"data_root": str(Path(root)), "data_paths": paths}

    def paths(self) -> dl.DataPaths:
        return dl.data_paths(self.values())

    def refresh(self) -> None:
        p = self.paths()
        bad = 0
        for k, e in self.fields.items():
            e.setPlaceholderText(str(p.root / dl.NAMES[k]))
            st = dl.status(p.get(k))
            text, colour = icon(st)
            self.icons[k].setText(text)
            self.icons[k].setStyleSheet(f"color: {colour}; font-weight: 600;")
            self.icons[k].setToolTip(st)
            bad += st != dl.OK
        self.summary.setText("All locations are ready." if not bad else
                             f"{bad} location{'s are' if bad > 1 else ' is'} missing or not writable.")
        self.create_btn.setEnabled(bool(bad))

    def create_missing(self) -> None:
        try:
            dl.create_missing(self.paths())
        except OSError as exc:
            self.summary.setText(f"Could not create: {exc}")
            return
        self.refresh()


class FirstRunDialog(QDialog):
    """No data root yet: ask for one (with a suggestion) and create the layout."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose a data folder")
        self.setMinimumWidth(620)
        self.settings = settings
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        title = QLabel("Where should Star ExTractor keep its data?")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        lay.addWidget(title)
        lay.addWidget(_muted("One folder holds everything the app makes: Stacks, Masters, Processed and Published. "
                             "Your raw captures stay where they are and are only read. You can change any of these "
                             "later in Settings › Data locations."))
        row = QHBoxLayout()
        self.root = QLineEdit(str(dl.default_root()))
        b = QPushButton("Browse…")
        b.clicked.connect(self._browse)
        row.addWidget(self.root, 1)
        row.addWidget(b)
        lay.addLayout(row)
        self.preview = _muted("")
        lay.addWidget(self.preview)
        self.error = QLabel("")
        self.error.setProperty("role", "warn")
        self.error.setWordWrap(True)
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create and continue")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.root.textChanged.connect(self._show_tree)
        self._show_tree()

    def _browse(self) -> None:
        d = dialogs.existing_directory(self, "Data folder", self.root.text())
        if d:
            self.root.setText(str(Path(d)))

    def _show_tree(self) -> None:
        r = self.root.text().strip()
        self.preview.setText("\n".join([r] + [f"   {dl.NAMES[k]}\\" for k in dl.KEYS]) if r else "")

    def accept(self) -> None:
        root = self.root.text().strip()
        if not root:
            self.error.setText("Choose a folder.")
            return
        self.settings["data_root"] = str(Path(root))
        try:
            dl.create_missing(self.settings.paths())
        except OSError as exc:
            self.settings["data_root"] = ""
            self.error.setText(f"Cannot create the folders there: {exc}")
            return
        self.settings.save()
        super().accept()


def check_locations(settings: Settings, parent: QWidget | None = None) -> bool:
    """At start-up: one clear message when a location is missing or not writable (an unplugged drive). Offers
    Choose another location (the Settings dialog); nothing is created elsewhere. True = all fine."""
    bad = dl.problems(settings.paths())
    if not bad:
        return True
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Data location not available")
    box.setText("Some data locations cannot be used:")
    box.setInformativeText("\n".join(f"{name}: {path} ({state})" for name, path, state in bad)
                           + "\n\nIs a drive unplugged? Nothing is written to these locations until they are back.")
    choose = box.addButton("Choose another location", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Continue", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is choose:
        from .settings_dialog import SettingsDialog

        SettingsDialog(settings, parent).exec()
    return False
