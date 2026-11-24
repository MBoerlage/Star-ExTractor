"""Help me: the workflow guide (resources/help.md, bundled) in a window beside the app (not modal): contents on the
left, the guide on the right, a find field. Opened from the "Help me" button in the top row or F1. A second tab,
User Feedback (ui/feedback_tab.py), sends a message to the developer; nothing goes online until its Send."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .widgets.common import StatusLine, label

HELP_FILE = Path(__file__).resolve().parents[1] / "resources" / "help.md"
TITLE = "Help me"
GUIDE_TAB, FEEDBACK_TAB = "Guide", "User Feedback"


def help_text() -> str:
    return HELP_FILE.read_text(encoding="utf-8")


class HelpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{TITLE}: the Star ExTractor workflow")
        self.setObjectName("helpDialog")
        self.setModal(False)
        self.resize(1040, 760)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(label("Help me", "title"))
        top.addStretch(1)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find in the guide (Enter for the next match)")
        self.find.setFixedWidth(320)
        self.find.returnPressed.connect(lambda: self.find_next())
        top.addWidget(self.find)
        lay.addLayout(top)
        self.found = StatusLine("off", "")
        self.found.hide()

        split = QSplitter(Qt.Orientation.Horizontal)
        self.contents = QListWidget()
        self.contents.setObjectName("helpContents")
        self.contents.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.contents.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.contents.itemClicked.connect(self._go)
        self.contents.currentItemChanged.connect(lambda it, _old: it and self._go(it))
        self.browser = QTextBrowser()
        self.browser.setObjectName("helpText")
        self.browser.setOpenExternalLinks(False)
        self.browser.setMarkdown(help_text())
        self.browser.document().setDocumentMargin(18)
        self._polish_document()
        split.addWidget(self.contents)
        split.addWidget(self.browser)
        split.setSizes([260, 780])
        split.setStretchFactor(1, 1)
        guide = QWidget()
        gl = QVBoxLayout(guide)
        gl.setContentsMargins(0, 8, 0, 0)
        gl.addWidget(split, 1)
        gl.addWidget(self.found)
        from .feedback_tab import FeedbackTab

        self.feedback = FeedbackTab(self._application_log)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("helpTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(guide, GUIDE_TAB)
        self.tabs.addTab(self.feedback, FEEDBACK_TAB)
        self.tabs.currentChanged.connect(lambda i: self.find.setVisible(i == 0))
        lay.addWidget(self.tabs, 1)
        self.headings: list[tuple[int, str, int]] = []  # (level, text, block position)
        self._fill_contents()

    def _polish_document(self) -> None:
        """Room between paragraphs and before headings; inline code in the app's mono font."""
        from . import theme

        doc = self.browser.document()
        mono = theme.mono_font_family()
        block = doc.begin()
        while block.isValid():
            cur = QTextCursor(block)
            fmt = block.blockFormat()
            level = fmt.headingLevel()
            fmt.setTopMargin({1: 4, 2: 22, 3: 14}.get(level, fmt.topMargin()))
            fmt.setBottomMargin(6 if level else 7)
            fmt.setLineHeight(118 if not level else 100, 1)  # 1 = ProportionalHeight (%)
            cur.setBlockFormat(fmt)
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                cf = frag.charFormat()
                if frag.isValid() and cf.fontFixedPitch():
                    c = QTextCursor(doc)
                    c.setPosition(frag.position())
                    c.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                    cf.setFontFamilies([mono])
                    c.setCharFormat(cf)
                it += 1
            block = block.next()

    def _fill_contents(self) -> None:
        block = self.browser.document().begin()
        while block.isValid():
            level = block.blockFormat().headingLevel()
            if level in (2, 3) and block.text().strip():
                self.headings.append((level, block.text().strip(), block.position()))
                it = QListWidgetItem(("    " if level == 3 else "") + block.text().strip())
                it.setData(Qt.ItemDataRole.UserRole, block.position())
                self.contents.addItem(it)
            block = block.next()

    def _application_log(self) -> str:
        """The Log panel's lines of the open pair and the end of error.log (the feedback tab attaches it, redacted)."""
        from starextractor.core.feedback import application_log
        from starextractor.core.settings import app_dir

        session = getattr(self.parent(), "session", None)
        return application_log(list(getattr(session, "log_lines", []) or []), app_dir() / "error.log")

    def show_feedback(self) -> None:
        self.tabs.setCurrentWidget(self.feedback)
        self.feedback.message.setFocus()

    def _go(self, item: QListWidgetItem) -> None:
        self.show_at(int(item.data(Qt.ItemDataRole.UserRole)))

    def show_at(self, position: int) -> None:
        """Scroll so the block at `position` is the first line shown."""
        doc = self.browser.document()
        block = doc.findBlock(position)
        cur = QTextCursor(block)
        self.browser.setTextCursor(cur)
        top = doc.documentLayout().blockBoundingRect(block).top()
        self.browser.verticalScrollBar().setValue(int(top))

    def show_section(self, title: str) -> bool:
        """Open the guide at the first heading containing `title` (case ignored)."""
        self.tabs.setCurrentIndex(0)
        for i, (_lvl, text, _pos) in enumerate(self.headings):
            if title.casefold() in text.casefold():
                self.contents.setCurrentRow(i)
                return True
        return False

    def find_next(self) -> bool:
        text = self.find.text().strip()
        if not text:
            self.found.hide()
            return False
        if self.browser.find(text):
            self.found.hide()
            return True
        self.browser.moveCursor(QTextCursor.MoveOperation.Start)  # wrap round once
        if self.browser.find(text, QTextDocument.FindFlag(0)):
            self.found.set("off", "Back at the start of the guide.")
            self.found.show()
            return True
        self.found.set("warn", f"“{text}” is not in the guide.")
        self.found.show()
        return False
