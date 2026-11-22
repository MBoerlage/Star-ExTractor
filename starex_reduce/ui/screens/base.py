"""Common frame of every Reduce step: header, missing-data banner, scrolling body, log panel."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from starex_reduce.core.pipeline import StageDef, input_name, output_name

from ..log_panel import LogPanel
from ..step_header import StepHeader
from ..widgets.common import StatusLine

MAX_CHECKS = 4


def card(title: str, body: str = "") -> QFrame:
    frame = QFrame()
    frame.setProperty("role", "card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    t = QLabel(title)
    t.setStyleSheet("font-size: 15px; font-weight: 600;")
    lay.addWidget(t)
    if body:
        b = QLabel(body)
        b.setProperty("role", "muted")
        b.setWordWrap(True)
        lay.addWidget(b)
    return frame


class StepScreen(QWidget):
    def __init__(self, stage: StageDef, total: int, parent: QWidget | None = None, two_images: bool = True) -> None:
        super().__init__(parent)
        self.stage = stage
        self.header = StepHeader(stage.number, total, stage.title, stage.description, two_images)
        self.banner = QLabel()
        self.banner.setProperty("role", "banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        self.checks = QWidget()  # consistency notes across the tabs (names, spectral type, airmass inputs)
        self.checks_lay = QVBoxLayout(self.checks)
        self.checks_lay.setContentsMargins(0, 0, 0, 0)
        self.checks_lay.setSpacing(2)
        self.checks.hide()

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.body)

        self.log = LogPanel()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self.header)
        lay.addWidget(self.banner)
        lay.addWidget(self.checks)
        lay.addWidget(scroll, 1)
        lay.addWidget(self.log)
        self.header.image_changed.connect(self._update_chips)
        self._update_chips(0)

    def _update_chips(self, idx: int) -> None:
        image = ("Target", "Reference")[idx]
        name = input_name(self.stage.id, image)
        self.header.set_files(name.rsplit("/", 1)[-1] if name else "Raw lights",
                              output_name(self.stage.id, image).rsplit("/", 1)[-1])

    def show_checks(self, items: list[tuple[str, str]]) -> None:
        while self.checks_lay.count():
            w = self.checks_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for level, text in items[:MAX_CHECKS]:
            self.checks_lay.addWidget(StatusLine("warn" if level == "warn" else "off", text))
        if len(items) > MAX_CHECKS:
            self.checks_lay.addWidget(StatusLine("off", f"… and {len(items) - MAX_CHECKS} more"))
        self.checks.setVisible(bool(items))

    def show_missing(self, message: str | None) -> None:
        self.banner.setText(message or "")
        self.banner.setVisible(bool(message))


class ExportScreen(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.setSpacing(8)
        title = QLabel("Export")
        title.setProperty("role", "title")
        lay.addWidget(title)
        sub = QLabel("Check the target spectrum against the Balmer and telluric lines, fill in the "
                     "observer details, then prepare the files for AAVSO AVSpec and ARAS.")
        sub.setProperty("role", "muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        lay.addWidget(card("Spectrum to publish · target only", "Built in M6."))
        lay.addStretch(1)
        self.log = LogPanel()
        lay.addWidget(self.log)
