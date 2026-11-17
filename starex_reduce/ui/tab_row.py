"""Top-row tabs: exclusive checkable buttons. Unlike QTabBar this cannot scroll or clip: each button is
fixed to the width of its label in the selected (bold) style, so selecting a tab never changes the row width."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QSizePolicy, QWidget

PAD_X = 26  # matches QPushButton#mainTab padding in theme.py
FONT_PX = 14


class TabRow(QWidget):
    currentChanged = Signal(int)

    def __init__(self, titles: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mainTabs")
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for i, title in enumerate(titles):
            b = QPushButton(title)
            b.setObjectName("mainTab")
            b.setCheckable(True)
            b.setFixedWidth(self._width_for(b, title))
            self.group.addButton(b, i)
            lay.addWidget(b)
            self.buttons.append(b)
        self.buttons[0].setChecked(True)
        self._current = 0
        self.group.idToggled.connect(self._toggled)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _width_for(b: QPushButton, title: str) -> int:
        bold = QFont(b.font())
        bold.setPixelSize(FONT_PX)
        bold.setWeight(QFont.Weight.DemiBold)
        return QFontMetrics(bold).horizontalAdvance(title) + 2 * PAD_X + 2 + 4  # + border + a little slack

    def _toggled(self, idx: int, checked: bool) -> None:
        if checked and idx != self._current:
            self._current = idx
            self.currentChanged.emit(idx)

    # QTabBar-compatible API used by the main window
    def count(self) -> int:
        return len(self.buttons)

    def tabText(self, idx: int) -> str:
        return self.buttons[idx].text()

    def currentIndex(self) -> int:
        return self._current

    def setCurrentIndex(self, idx: int) -> None:
        if 0 <= idx < len(self.buttons) and idx != self._current:
            self.buttons[idx].setChecked(True)  # emits currentChanged via _toggled
