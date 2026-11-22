"""The night mode switch in the top row: a moon glyph and a 38×20 toggle, drawn with QPainter from theme tokens.
Off = the default palette, on = night (red-filter safe). Space toggles when focused; Ctrl+Shift+N from anywhere
(wired in MainWindow)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import theme

TRACK_W, TRACK_H = 38, 20
MOON = 16  # glyph box
GAP = 6


class NightSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, on: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on = on
        self.setObjectName("nightSwitch")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Night mode (red-filter safe) · Ctrl+Shift+N")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._sync_name()

    def sizeHint(self) -> QSize:
        return QSize(MOON + GAP + TRACK_W + 4, TRACK_H + 8)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def isChecked(self) -> bool:
        return self._on

    def setChecked(self, on: bool) -> None:
        """Show the state without emitting (the window calls this when the palette changed elsewhere)."""
        if on != self._on:
            self._on = on
            self._sync_name()
            self.update()

    def toggle(self) -> None:
        self._on = not self._on
        self._sync_name()
        self.update()
        self.toggled.emit(self._on)

    def _sync_name(self) -> None:
        self.setAccessibleName(f"Night mode, {'on' if self._on else 'off'}")
        self.setAccessibleDescription("Switches to the red-filter safe night palette (Ctrl+Shift+N)")

    def track_rect(self) -> QRectF:
        return QRectF(MOON + GAP + 1, (self.height() - TRACK_H) / 2, TRACK_W, TRACK_H)

    # ---- input --------------------------------------------------------------------------------------------
    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.toggle()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggle()
            return
        super().keyPressEvent(e)

    # ---- paint ---------------------------------------------------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        # moon: a disc with a disc cut out of it
        disc = QPainterPath()
        disc.addEllipse(QPointF(MOON / 2, cy), 6.5, 6.5)
        cut = QPainterPath()
        cut.addEllipse(QPointF(MOON / 2 + 4.2, cy - 3.0), 5.6, 5.6)
        p.fillPath(disc.subtracted(cut), QColor(theme.AMBER if self._on else theme.MUTED))
        # track and knob
        t = self.track_rect()
        r = TRACK_H / 2
        p.setPen(QPen(QColor(theme.AMBER if self._on else theme.BORDER), 1))
        p.setBrush(QColor(theme.AMBER_DIM if self._on else theme.BG))
        p.drawRoundedRect(t.adjusted(0.5, 0.5, -0.5, -0.5), r, r)
        k = TRACK_H - 6
        x = t.right() - 3 - k if self._on else t.left() + 3
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.AMBER if self._on else theme.MUTED))
        p.drawEllipse(QRectF(x, t.top() + 3, k, k))
        if self.hasFocus():
            p.setPen(QPen(QColor(theme.AMBER), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(t.adjusted(-2, -2, 2, 2), r + 2, r + 2)
        p.end()
