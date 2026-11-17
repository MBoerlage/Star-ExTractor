"""Five-step chevron ribbon (done / current / missing / not run) plus its legend row."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from . import theme

NOTCH = 20.0
GAP = 4.0
HEIGHT = 74


class StepState(StrEnum):
    DONE = "done"
    CURRENT = "current"
    MISSING = "missing"
    NOT_RUN = "notrun"


_BG = {
    StepState.DONE: theme.DONE_BG, StepState.CURRENT: theme.CURRENT_BG,
    StepState.MISSING: theme.MISSING_BG, StepState.NOT_RUN: theme.NOTRUN_BG,
}
_ACCENT = {
    StepState.DONE: theme.GREEN, StepState.CURRENT: theme.BLUE,
    StepState.MISSING: theme.RED, StepState.NOT_RUN: theme.MUTED,
}


@dataclass
class RibbonStep:
    title: str
    subtitle: str
    status: str = "Not run"
    state: StepState = StepState.NOT_RUN


class Ribbon(QWidget):
    step_clicked = Signal(int)
    painted = Signal()  # after every paint; the main window uses the first one

    def __init__(self, steps: list[RibbonStep], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.steps = steps
        self._hover = -1
        self._focus_idx = 0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Reduction steps")

    def sizeHint(self) -> QSize:
        return QSize(1200, HEIGHT)

    def set_step(self, idx: int, *, state: StepState | None = None, status: str | None = None) -> None:
        step = self.steps[idx]
        if state is not None:
            step.state = state
        if status is not None:
            step.status = status
        self.update()

    # ---- geometry ----------------------------------------------------------------------------
    def _rects(self) -> list[QRectF]:
        n = len(self.steps)
        w = (self.width() - GAP * (n - 1)) / n
        return [QRectF(i * (w + GAP), 0, w, HEIGHT) for i in range(n)]

    def _path(self, r: QRectF, first: bool) -> QPainterPath:
        p = QPainterPath()
        p.moveTo(r.left(), r.top())
        p.lineTo(r.right() - NOTCH, r.top())
        p.lineTo(r.right(), r.center().y())
        p.lineTo(r.right() - NOTCH, r.bottom())
        p.lineTo(r.left(), r.bottom())
        if not first:
            p.lineTo(r.left() + NOTCH, r.center().y())
        p.closeSubpath()
        return p

    def _index_at(self, pos: QPointF) -> int:
        for i, r in enumerate(self._rects()):
            if self._path(r, i == 0).contains(pos):
                return i
        return -1

    # ---- painting ----------------------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        title_f = theme.ui_font(14, QFont.Weight.DemiBold)
        status_f = theme.ui_font(12)
        sub_f = theme.ui_font(11)
        for i, (r, step) in enumerate(zip(self._rects(), self.steps, strict=True)):
            path = self._path(r, i == 0)
            bg = QColor(_BG[step.state])
            if i == self._hover:
                bg = bg.lighter(118)
            p.fillPath(path, bg)
            if self.hasFocus() and i == self._focus_idx:
                p.strokePath(path, QPen(QColor(theme.BLUE), 1.5))
            x = r.left() + (16 if i == 0 else 30)
            self._paint_badge(p, QPointF(x + 12, r.center().y()), i + 1, step.state)
            tx = x + 34
            tw = r.right() - NOTCH - 6 - tx
            self._text(p, title_f, theme.TEXT, tx, r.top() + 14, tw, step.title)
            self._text(p, status_f, _ACCENT[step.state], tx, r.top() + 34, tw, step.status)
            self._text(p, sub_f, theme.MUTED, tx, r.top() + 52, tw, step.subtitle)
        p.end()
        self.painted.emit()

    @staticmethod
    def _text(p: QPainter, font: QFont, colour: str, x: float, y: float, w: float, text: str) -> None:
        p.setFont(font)
        p.setPen(QColor(colour))
        fm = QFontMetrics(font)
        p.drawText(QRectF(x, y, max(w, 0), fm.height()), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(text, Qt.TextElideMode.ElideRight, int(max(w, 0))))

    @staticmethod
    def _paint_badge(p: QPainter, c: QPointF, number: int, state: StepState) -> None:
        r = 12.0
        box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        if state == StepState.DONE:
            p.setBrush(QColor(theme.BG))
            p.setPen(QPen(QColor(theme.GREEN), 2))
            p.drawEllipse(box)
            tick = QPainterPath(QPointF(c.x() - 5, c.y() + 0.5))
            tick.lineTo(c.x() - 1.5, c.y() + 4)
            tick.lineTo(c.x() + 5, c.y() - 4)
            pen = QPen(QColor(theme.GREEN), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.strokePath(tick, pen)
            return
        if state == StepState.CURRENT:
            p.setBrush(QColor(theme.BLUE))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(box)
            p.setPen(QColor(theme.BG))
            p.setFont(theme.ui_font(13, QFont.Weight.DemiBold))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(number))
            return
        if state == StepState.MISSING:
            p.setBrush(QColor(theme.RED))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(box)
            p.setPen(QColor(theme.MISSING_BG))
            p.setFont(theme.ui_font(14, QFont.Weight.Bold))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, "!")
            return
        pen = QPen(QColor(theme.DIM), 2, Qt.PenStyle.DashLine)
        pen.setDashPattern([2.2, 1.6])
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        p.drawEllipse(box.adjusted(1, 1, -1, -1))
        p.setPen(QColor(theme.MUTED))
        p.setFont(theme.ui_font(12))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(number))

    # ---- interaction -------------------------------------------------------------------------
    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        idx = self._index_at(e.position())
        if idx != self._hover:
            self._hover = idx
            self.setCursor(Qt.CursorShape.PointingHandCursor if idx >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = -1
        self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        idx = self._index_at(e.position())
        if idx >= 0 and e.button() == Qt.MouseButton.LeftButton:
            self._focus_idx = idx
            self.step_clicked.emit(idx)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            delta = -1 if e.key() == Qt.Key.Key_Left else 1
            self._focus_idx = (self._focus_idx + delta) % len(self.steps)
            self.update()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.step_clicked.emit(self._focus_idx)
        else:
            super().keyPressEvent(e)


class _Swatch(QWidget):
    def __init__(self, bg: str, border: str) -> None:
        super().__init__()
        self.bg, self.border = bg, border
        self.setFixedSize(10, 10)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setBrush(QColor(self.bg))
        p.setPen(QColor(self.border))
        p.drawRoundedRect(QRectF(0.5, 0.5, 9, 9), 2, 2)
        p.end()


def legend_row() -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 8, 0, 0)
    lay.setSpacing(6)
    for text, bg, border in (
        ("Done", theme.DONE_BG, theme.GREEN), ("Current", theme.CURRENT_BG, theme.BLUE),
        ("Missing data", theme.MISSING_BG, theme.RED), ("Not run", theme.NOTRUN_BG, theme.BORDER),
    ):
        lay.addWidget(_Swatch(bg, border))
        lbl = QLabel(text)
        lbl.setProperty("role", "muted")
        lbl.setStyleSheet("font-size: 12px;")
        lay.addWidget(lbl)
        lay.addSpacing(10)
    lay.addStretch(1)
    note = QLabel("Changing a step turns every later step grey until it is re-run.")
    note.setProperty("role", "muted")
    note.setStyleSheet("font-size: 12px;")
    lay.addWidget(note)
    return w
