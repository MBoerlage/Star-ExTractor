"""Small shared widgets in the mockup style: card, section title, segmented control, status line."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme

ICONS = {"ok": "✓", "warn": "!", "err": "!", "off": "–", "info": "·"}
def colours() -> dict[str, str]:
    return {"ok": theme.GREEN, "warn": theme.AMBER, "err": theme.RED, "off": theme.MUTED, "info": theme.MUTED}


def label(text: str = "", role: str | None = None, wrap: bool = False, style: str = "") -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    if wrap:
        lbl.setWordWrap(True)
    if style:
        lbl.setStyleSheet(style)
    return lbl


class Card(QFrame):
    """Rounded panel with a title row (title, optional subtitle, right-side widgets) and a body."""

    def __init__(self, title: str = "", subtitle: str = "", parent: QWidget | None = None,
                 inner: bool = False) -> None:
        super().__init__(parent)
        self.setProperty("role", "inner" if inner else "card")
        self.outer = QVBoxLayout(self)
        m = 12 if inner else 16
        self.outer.setContentsMargins(m, 12 if inner else 14, m, 12 if inner else 14)
        self.outer.setSpacing(10)
        self.head = QHBoxLayout()
        self.head.setSpacing(8)
        if title:
            self.title = label(title, "eyebrow" if inner else None,
                               style="" if inner else "font-size: 15px; font-weight: 600;")
            self.head.addWidget(self.title)
        if subtitle:
            self.head.addWidget(label(subtitle, "muted", style="font-size: 12px;"))
        self.head.addStretch(1)
        if title or subtitle:
            self.outer.addLayout(self.head)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        self.outer.addLayout(self.body)

    def set_error(self, error: bool) -> None:
        base = "inner" if self.property("role") in ("inner",) else "card"
        self.setProperty("role", f"{base}-error" if error else base)
        theme.repolish(self)


def section_title(letter: str, title: str, subtitle: str) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 12, 0, 0)
    lay.setSpacing(10)
    lay.addWidget(label(letter, "eyebrow"), 0, Qt.AlignmentFlag.AlignBottom)
    lay.addWidget(label(title, style="font-size: 19px; font-weight: 600;"), 0, Qt.AlignmentFlag.AlignBottom)
    lay.addWidget(label(subtitle, "muted"), 0, Qt.AlignmentFlag.AlignBottom)
    lay.addStretch(1)
    return w


class Segmented(QWidget):
    """Exclusive row of buttons (e.g. SIMBAD | From header | Enter by hand)."""

    changed = Signal(int)

    def __init__(self, options: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.buttons: list[QPushButton] = []
        for i, text in enumerate(options):
            b = QPushButton(text)
            b.setCheckable(True)
            pos = "only" if len(options) == 1 else ("first" if i == 0 else
                                                   "last" if i == len(options) - 1 else "mid")
            b.setProperty("seg", pos)
            self.group.addButton(b, i)
            self.buttons.append(b)
            lay.addWidget(b)
        self.buttons[0].setChecked(True)
        self.group.idToggled.connect(lambda i, on: on and self.changed.emit(i))
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def index(self) -> int:
        return self.group.checkedId()

    def set_index(self, i: int) -> None:
        if 0 <= i < len(self.buttons):
            self.buttons[i].setChecked(True)


class StatusLine(QWidget):
    """Icon + text in the severity colour (✓ green, ! amber/red, – muted), wraps."""

    def __init__(self, state: str = "off", text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFixedWidth(14)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.icon)
        lay.addWidget(self.text, 1)
        self.set(state, text)

    def set(self, state: str, text: str) -> None:
        colour = colours().get(state, theme.MUTED)
        weight = "600" if state in ("warn", "err") else "400"
        self.icon.setText(ICONS.get(state, ""))
        self.icon.setStyleSheet(f"color: {colour}; font-weight: {weight};")
        self.text.setText(text)
        self.text.setStyleSheet(f"color: {colour if state != 'off' else theme.MUTED};")
        self.state = state


class InfoButton(QPushButton):
    """Small round (i) after a heading; click shows the explanation in a popup under it."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__("i", parent)
        self.text_ = text
        self.setFixedSize(20, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("More information")
        self.setAccessibleName("More information")
        self.setStyleSheet(f"QPushButton {{ padding: 0; min-height: 0; border: 1px solid {theme.BORDER};"
                           f"border-radius: 10px; color: {theme.MUTED}; font-size: 11px; font-weight: 600;"
                           f"font-style: italic; background: transparent; }}"
                           f"QPushButton:hover {{ color: {theme.TEXT}; border-color: {theme.BLUE}; }}")
        self.clicked.connect(self.show_popup)
        self.popup: QFrame | None = None

    def show_popup(self) -> None:
        pop = QFrame(self, Qt.WindowType.Popup)
        pop.setStyleSheet(f"QFrame {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER};"
                          f"border-radius: 8px; }} QLabel {{ color: {theme.TEXT}; font-size: 12px; border: 0; }}")
        lay = QVBoxLayout(pop)
        lay.setContentsMargins(12, 10, 12, 10)
        lbl = QLabel(self.text_)
        lbl.setWordWrap(True)
        lbl.setFixedWidth(340)
        lay.addWidget(lbl)
        pop.adjustSize()
        pop.move(self.mapToGlobal(self.rect().bottomLeft()) + QPoint(0, 6))
        pop.show()
        self.popup = pop


class _LineSwatch(QWidget):
    def __init__(self, colour: str, width: float) -> None:
        super().__init__()
        self.colour, self.width_ = colour, width
        self.dark_style: Qt.PenStyle | None = None  # night / red: the curve's line style (retheme.dark_style)
        self.setFixedSize(22, 14)

    def recolour(self, mp: dict[str, str]) -> None:
        """The palette changed (retheme.switch): the same token in the new palette."""
        self.colour = mp.get(self.colour.upper(), self.colour)

    def paintEvent(self, _e) -> None:
        from PySide6.QtGui import QColor, QPainter, QPen

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        from .. import theme

        pen = QPen(QColor(self.colour), self.width_)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if self.dark_style is not None and theme.is_dark_room():
            pen.setStyle(self.dark_style)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawLine(3, self.height() // 2, self.width() - 3, self.height() // 2)
        p.end()


class LegendToggle(QWidget):
    """Show/hide checkbox for a plot curve: tick + a line in the curve's own colour and width + name."""

    toggled = Signal(bool)

    def __init__(self, text: str, colour: str, width: float = 2.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(6)
        self.box = QCheckBox()
        self.box.setChecked(True)
        self.box.setAccessibleName(f"Show {text}")
        self.box.toggled.connect(self.toggled)
        self.swatch = _LineSwatch(colour, max(width, 1.5))
        self.text = QLabel(text)
        lay.addWidget(self.box)
        lay.addWidget(self.swatch)
        lay.addWidget(self.text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, _e) -> None:
        self.box.toggle()

    def isChecked(self) -> bool:
        return self.box.isChecked()

    def setChecked(self, on: bool) -> None:
        self.box.setChecked(on)


def hline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background: {theme.LINE};")
    return f
