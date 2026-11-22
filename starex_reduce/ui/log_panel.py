"""Compact log at the bottom of each screen: four timestamped one-liners, scroll for more."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QWidget

MAX_LINES = 500
VISIBLE_LINES = 4


class LogPanel(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 6, 6)
        lay.setSpacing(12)
        title = QLabel("Log")
        title.setStyleSheet("font-size: 12px; font-weight: 500;")
        lay.addWidget(title, 0, Qt.AlignmentFlag.AlignTop)
        self.text = QPlainTextEdit()
        self.text.setObjectName("log")
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(MAX_LINES)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)  # one line per entry
        self.text.document().setDocumentMargin(1)
        lay.addWidget(self.text, 1)
        self._size_to_lines()

    def _size_to_lines(self) -> None:
        fm = QFontMetrics(self.text.font())
        bar = self.text.horizontalScrollBar().sizeHint().height()
        self.text.setFixedHeight(fm.lineSpacing() * VISIBLE_LINES + 2 * self.text.frameWidth() + 4 + bar)

    def showEvent(self, e) -> None:
        self._size_to_lines()
        super().showEvent(e)
        self.scroll_to_end()

    def scroll_to_end(self) -> None:
        bar = self.text.verticalScrollBar()
        bar.setValue(bar.maximum())

    def last_visible_line(self) -> str:
        """The bottom line in view (tests: the newest entry must always be visible)."""
        vp = self.text.viewport()
        return self.text.cursorForPosition(vp.rect().bottomLeft()).block().text()

    def set_lines(self, lines: list[str]) -> None:
        self.text.setPlainText("\n".join(lines[-MAX_LINES:]))
        self.scroll_to_end()

    def append(self, line: str) -> None:
        # Always follow the newest line. Qt only auto-scrolls when the view is already at the bottom, which
        # is not the case for a panel filled while hidden: new lines then landed below the visible four.
        self.text.appendPlainText(line)
        self.scroll_to_end()
