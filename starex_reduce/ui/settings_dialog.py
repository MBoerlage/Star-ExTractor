"""Settings dialog: only settings that change what the app does (data locations, VSX endpoint).
Master tolerances live in Acquisition > Pre Processing settings. Observer, site and equipment live on the Export tab."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core.settings import Settings, app_dir
from starex_reduce.core.vsx import DEFAULT_URL

DEFAULT_VSX = DEFAULT_URL


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "muted")
    lbl.setWordWrap(True)
    return lbl



class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(720)
        self.settings = settings
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(10)

        from .data_locations import DataLocations

        title = QLabel("Data locations")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        lay.addWidget(title)
        self.locations = DataLocations(settings, self)
        lay.addWidget(self.locations)

        self.vsx = QLineEdit(settings["vsx_api_url"] or DEFAULT_VSX)
        reset = QPushButton("Default")
        reset.clicked.connect(lambda: self.vsx.setText(DEFAULT_VSX))
        vrow = QHBoxLayout()
        vrow.addWidget(self.vsx, 1)
        vrow.addWidget(reset)
        form.addRow("VSX lookup address", vrow)
        form.addRow("", _muted("Unofficial AAVSO interface; {name} is replaced by the object name. "
                               "Used only when you press Check VSX name."))
        self.clear_vsx = QPushButton("Clear VSX cache")
        self.clear_vsx.clicked.connect(self._clear_vsx)
        self.clear_vsx.setEnabled((app_dir() / "vsx_cache.json").exists())
        form.addRow("", self.clear_vsx)
        from .screens.export_publish import celebrate_enabled

        self.celebrate = QCheckBox("Celebrate saves")
        self.celebrate.setChecked(celebrate_enabled(settings))
        form.addRow("Export", self.celebrate)
        form.addRow("", _muted("A short confetti burst on a tile in Export → Publish after a successful save. "
                               "Off by default when Windows is set to show fewer animations."))
        self.splash = QCheckBox("Show splash screen")
        self.splash.setChecked(bool((settings["ui"] or {}).get("splash", True)))
        form.addRow("Start-up", self.splash)
        form.addRow("", _muted("The Star ExTractor image while the app starts (dimmed in the night palettes). "
                               "Takes effect at the next start; --no-splash also turns it off for one start."))
        lay.addLayout(form)

        self.error = QLabel("")
        self.error.setProperty("role", "warn")
        self.error.setWordWrap(True)
        lay.addWidget(self.error)
        folder = QPushButton("Open settings folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_dir()))))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(folder)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        lay.addLayout(bottom)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.adjustSize()  # the styled buttons are taller than the size estimated before the style applied

    def _clear_vsx(self) -> None:
        (app_dir() / "vsx_cache.json").unlink(missing_ok=True)
        self.clear_vsx.setEnabled(False)

    def values(self) -> dict | str:
        """New settings values, or a message saying what is wrong."""
        vsx = self.vsx.text().strip()
        if "{name}" not in vsx or not vsx.startswith(("http://", "https://")):
            return "The VSX address must start with http(s):// and contain {name}."
        return {
            **self.locations.values(),
            "vsx_api_url": "" if vsx == DEFAULT_VSX else vsx,
            "celebrate_saves": self.celebrate.isChecked(),
            "ui": {**(self.settings["ui"] or {}), "splash": self.splash.isChecked()},  # keeps the palette
        }

    def accept(self) -> None:
        v = self.values()
        if isinstance(v, str):
            self.error.setText(v)
            return
        self.settings.data.update(v)
        self.settings.save()
        super().accept()
