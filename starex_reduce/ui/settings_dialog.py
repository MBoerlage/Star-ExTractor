"""Settings dialog: only settings that change what the app does (master library, calibration
tolerances, VSX endpoint). Observer, site and equipment live on the Export tab."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core.settings import DEFAULTS, Settings, app_dir
from starex_reduce.core.vsx import DEFAULT_URL

DEFAULT_VSX = DEFAULT_URL


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "muted")
    lbl.setWordWrap(True)
    return lbl


def _num(value: float, width: int = 80) -> QLineEdit:
    e = QLineEdit(f"{value:g}")
    e.setFixedWidth(width)
    return e


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        self.settings = settings
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        form = QFormLayout()
        form.setSpacing(10)

        self.masters = QLineEdit(str(settings.masters_dir()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.masters, 1)
        row.addWidget(browse)
        form.addRow("Master library folder", row)

        tol = {**DEFAULTS["tolerances"], **(settings["tolerances"] or {})}
        self.tol_enabled = QCheckBox("Warn when masters do not match the lights")
        self.tol_enabled.setChecked(bool(tol["enabled"]))
        self.gain = _num(100 * tol["gain_rel"])
        self.offset = _num(100 * tol["offset_rel"])
        self.temp = _num(tol["temp_c"])
        trow = QHBoxLayout()
        for title, e in (("gain ±%", self.gain), ("offset ±%", self.offset), ("temperature ±°C", self.temp)):
            trow.addWidget(QLabel(title))
            trow.addWidget(e)
        trow.addStretch(1)
        form.addRow("Master tolerances", self.tol_enabled)
        form.addRow("", trow)

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

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Master library folder", self.masters.text())
        if d:
            self.masters.setText(d)

    def _clear_vsx(self) -> None:
        (app_dir() / "vsx_cache.json").unlink(missing_ok=True)
        self.clear_vsx.setEnabled(False)

    def values(self) -> dict | str:
        """New settings values, or a message saying what is wrong."""
        try:
            gain, offset, temp = (float(e.text().replace(",", ".")) for e in (self.gain, self.offset, self.temp))
        except ValueError:
            return "Tolerances must be numbers."
        if min(gain, offset, temp) < 0:
            return "Tolerances cannot be negative."
        vsx = self.vsx.text().strip()
        if "{name}" not in vsx or not vsx.startswith(("http://", "https://")):
            return "The VSX address must start with http(s):// and contain {name}."
        masters = self.masters.text().strip()
        default_masters = str(app_dir() / "masters")
        return {
            "masters_dir": "" if masters in ("", default_masters) else masters,
            "tolerances": {"gain_rel": gain / 100, "offset_rel": offset / 100, "temp_c": temp,
                           "enabled": self.tol_enabled.isChecked()},
            "vsx_api_url": "" if vsx == DEFAULT_VSX else vsx,
            "celebrate_saves": self.celebrate.isChecked(),
        }

    def accept(self) -> None:
        v = self.values()
        if isinstance(v, str):
            self.error.setText(v)
            return
        if v["masters_dir"]:
            try:
                Path(v["masters_dir"]).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.error.setText(f"Cannot use that master folder: {exc}")
                return
        self.settings.data.update(v)
        self.settings.save()
        super().accept()
