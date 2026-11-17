"""Step header: STEP n OF 5, title, description, input → output chips, ◀ 1/2 ▶, run buttons,
plus a progress bar and Stop button shown while the step's job runs."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme


def _label(text: str, role: str | None = None) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    return lbl


class StepHeader(QWidget):
    run_this = Signal()
    run_both = Signal()
    stop = Signal()
    image_changed = Signal(int)

    def __init__(self, number: int, total: int, title: str, description: str,
                 two_images: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.image_idx = 0
        self.two_images = two_images

        left = QVBoxLayout()
        left.setSpacing(2)
        left_align = Qt.AlignmentFlag.AlignLeft  # width-limited texts would otherwise be centred
        left.addWidget(_label(f"STEP {number} OF {total}", "eyebrow"), 0, left_align)
        self.title = _label(title, "title")
        left.addWidget(self.title, 0, left_align)
        self.description = _label(description, "muted")
        self.description.setWordWrap(True)
        self.description.setMaximumWidth(560)
        desc_row = QHBoxLayout()  # left-anchored without shrinking the wrapped text to its size hint
        desc_row.setContentsMargins(0, 0, 0, 0)
        desc_row.addWidget(self.description, 1)
        desc_row.addStretch(0)
        left.addLayout(desc_row)

        self.chip_in = _label("", "chip")
        self.chip_out = _label("", "chip-out")
        self.arrow = _label("→", "muted")
        self.prev_btn = QPushButton("◀")
        self.next_btn = QPushButton("▶")
        for b, tip in ((self.prev_btn, "Previous image (target)"), (self.next_btn, "Next image (reference)")):
            b.setProperty("role", "nav")
            b.setToolTip(tip)
        self.counter = _label("1/2", "mono")
        self.prev_btn.clicked.connect(lambda: self._step_image(-1))
        self.next_btn.clicked.connect(lambda: self._step_image(1))

        self.btn_this = QPushButton("Run this image" if two_images else "Run")
        self.btn_both = QPushButton("Run both")
        self.btn_both.setProperty("role", "primary")
        self.btn_this.clicked.connect(self.run_this)
        self.btn_both.clicked.connect(self.run_both)
        if not two_images:
            self.btn_this.setProperty("role", "primary")
            self.btn_this.setFixedWidth(80)
            self.btn_both.hide()
            for w in (self.prev_btn, self.next_btn, self.counter):
                w.hide()

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setFixedWidth(160)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.stop)
        self.set_busy(False)

        files = QHBoxLayout()
        files.setSpacing(8)
        for w in (self.chip_in, self.arrow, self.chip_out):
            w.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            files.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        files.addSpacing(6)
        for w in (self.prev_btn, self.counter, self.next_btn):
            files.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        for w in (self.progress, self.btn_stop, self.btn_this, self.btn_both):
            buttons.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 16, 0, 8)
        row.setSpacing(16)
        row.addLayout(left, 1)
        row.addLayout(files)
        row.addLayout(buttons)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def set_files(self, src: str, dst: str) -> None:
        self.chip_in.setText(src)
        self.chip_out.setText(dst)
        self.chip_in.setVisible(bool(src))
        self.arrow.setVisible(bool(src))

    def set_output_ready(self, ready: bool) -> None:
        self.chip_out.setProperty("role", "chip-out" if ready else "chip")
        theme.repolish(self.chip_out)

    def set_busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.btn_stop.setVisible(busy)
        if busy:
            self.progress.setValue(0)
        self.btn_this.setEnabled(not busy)
        self.btn_both.setEnabled(not busy)

    def set_progress(self, f: float) -> None:
        self.progress.setValue(int(f * 1000))

    def _step_image(self, delta: int) -> None:
        idx = max(0, min(1, self.image_idx + delta))
        if idx != self.image_idx:
            self.image_idx = idx
            self.counter.setText(f"{idx + 1}/2")
            self.image_changed.emit(idx)
