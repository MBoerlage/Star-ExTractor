"""Dark theme taken from the approved mockups (docs/ui_mockups/*.html)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

BG = "#0E1116"
PANEL = "#12161C"
CARD = "#161A20"
RAISED = "#1B2028"
LINE = "#2A313B"
BORDER = "#3A424E"
TEXT = "#E8EBF0"
MUTED = "#A0A9B6"
DIM = "#5C6674"
AMBER = "#F2A33A"
AMBER_TEXT = "#1A1206"
TEAL = "#62D0C6"
BLUE = "#6CB4F5"
GREEN = "#7BD389"
RED = "#FF7B72"

DONE_BG = "#1E4D31"
CURRENT_BG = "#1A3352"  # selected step: neutral blue (orange is reserved for signals/actions)
MISSING_BG = "#4D1F1F"
NOTRUN_BG = RAISED

UI_FAMILIES = ("Inter", "Segoe UI Variable Text", "Segoe UI", "Arial")
MONO_FAMILIES = ("JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New")


def _first_available(candidates: tuple[str, ...]) -> str:
    families = set(QFontDatabase.families())
    return next((f for f in candidates if f in families), candidates[-1])


def ui_font_family() -> str:
    return _first_available(UI_FAMILIES)


def mono_font_family() -> str:
    return _first_available(MONO_FAMILIES)


def mono_font(px: int = 12) -> QFont:
    f = QFont(mono_font_family())
    f.setPixelSize(px)
    return f


def ui_font(px: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(ui_font_family())
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


ICONS = (Path(__file__).parent / "icons").as_posix()


def stylesheet() -> str:
    mono = mono_font_family()
    return f"""
* {{ color: {TEXT}; font-family: "{ui_font_family()}"; font-size: 13px; }}
QMainWindow, QWidget#central, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QWidget#topbar {{ background: {PANEL}; border-bottom: 1px solid {LINE}; }}
QLabel {{ background: transparent; border: 0; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="eyebrow"] {{ color: {MUTED}; font-size: 11px; letter-spacing: 1px; }}
QLabel[role="title"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="brand"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="chip"], QLabel[role="chip-out"] {{
    font-family: "{mono}"; font-size: 12px; color: {MUTED}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 6px 10px; background: {BG}; }}
QLabel[role="chip-out"] {{ color: {GREEN}; }}
QLabel[role="chip-warn"] {{ font-family: "{mono}"; font-size: 11px; color: {AMBER}; border: 1px solid #6B4F28;
    border-radius: 5px; padding: 1px 6px; background: transparent; }}
QToolButton#copyBtn {{ border: 0; padding: 4px; min-height: 0; background: transparent; }}
QToolButton#copyBtn:hover {{ background: {RAISED}; }}
QLabel[role="good"] {{ font-size: 11px; color: {GREEN}; background: transparent; }}
QLabel[role="mono"] {{ font-family: "{mono}"; font-size: 12px; color: {MUTED}; }}
QLabel[role="banner"] {{ background: {MISSING_BG}; color: {RED}; border: 1px solid {RED};
    border-radius: 8px; padding: 8px 12px; }}
QFrame[role="card"] {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame[role="card-error"] {{ background: #1A1416; border: 1px solid {RED}; border-radius: 12px; }}
QFrame[role="inner"], QFrame[role="inner-error"] {{ background: {BG}; border: 1px solid {LINE}; border-radius: 10px; }}
QPushButton[seg] {{ border-radius: 0; padding: 7px 12px; color: {MUTED}; background: {BG}; }}
QPushButton[seg="first"] {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
QPushButton[seg="last"] {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
QPushButton[seg="only"] {{ border-radius: 8px; }}
QPushButton[seg="mid"], QPushButton[seg="last"] {{ border-left: 0; }}
QPushButton[seg]:checked {{ background: {RAISED}; color: {TEXT}; }}
QTableWidget {{ background: {BG}; border: 1px solid {LINE}; border-radius: 8px; gridline-color: {LINE};
    alternate-background-color: {BG}; selection-background-color: {RAISED}; selection-color: {TEXT}; }}
QTableWidget::item {{ padding: 4px 8px; border-bottom: 1px solid {LINE}; }}
QHeaderView::section {{ background: {BG}; color: {MUTED}; border: 0; border-bottom: 1px solid {LINE};
    padding: 6px 8px; font-size: 11px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QTableWidget::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER};
    border-radius: 3px; background: {BG}; }}
QCheckBox::indicator:checked, QTableWidget::indicator:checked {{ background: {AMBER}; border-color: {AMBER};
    image: url("{ICONS}/check.svg"); }}
QComboBox::drop-down {{ border: 0; width: 18px; }}
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 12px; height: 12px; border: 1px solid {BORDER}; border-radius: 7px;
    background: {BG}; }}
QRadioButton::indicator:checked {{ border: 4px solid {BLUE}; background: {BG}; width: 6px; height: 6px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {RAISED}; }}
QLineEdit, QPlainTextEdit, QDoubleSpinBox, QSpinBox, QComboBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {AMBER}; selection-color: {AMBER_TEXT}; }}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {AMBER}; }}
QPushButton, QToolButton {{
    background: transparent; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 14px; min-height: 22px; }}
QPushButton:hover, QToolButton:hover {{ background: {RAISED}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {AMBER}; }}
QPushButton:disabled {{ color: {DIM}; border-color: {LINE}; }}
QPushButton[role="primary"] {{ background: {AMBER}; color: {AMBER_TEXT}; border: 0; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: #F5B358; }}
QPushButton[role="primary"]:disabled {{ background: #6B4F28; color: #2A1E0C; }}
QPushButton[role="nav"] {{ padding: 0; min-width: 30px; max-width: 30px; min-height: 30px;
    border-radius: 6px; }}
QToolButton#sessionChip {{
    font-family: "{mono}"; font-size: 12px; color: {MUTED}; border: 1px solid {BORDER};
    border-radius: 15px; padding: 6px 12px; background: {BG}; }}
QToolButton#sessionChip::menu-indicator {{ image: none; width: 0; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ padding: 10px 26px; font-size: 14px; color: {MUTED}; background: transparent;
    border: 1px solid transparent; border-top-left-radius: 8px; border-top-right-radius: 8px;
    margin-right: 4px; }}
QTabBar::tab:selected {{ color: {TEXT}; font-weight: 600; background: {RAISED}; border-color: {BORDER};
    border-bottom-color: {RAISED}; }}
QWidget#tabline {{ background: {BORDER}; }}
QPushButton#mainTab {{ padding: 10px 26px; font-size: 14px; color: {MUTED}; background: transparent;
    border: 1px solid transparent; border-radius: 0; border-top-left-radius: 8px; border-top-right-radius: 8px;
    min-height: 0; }}
QPushButton#mainTab:hover {{ color: {TEXT}; background: transparent; }}
QPushButton#mainTab:focus {{ border-color: transparent; }}
QPushButton#mainTab:checked {{ color: {TEXT}; font-weight: 600; background: {RAISED}; border-color: {BORDER};
    border-bottom-color: {RAISED}; }}
QProgressBar {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 4px; height: 8px;
    text-align: center; font-size: 10px; color: {MUTED}; }}
QProgressBar::chunk {{ background: {AMBER}; border-radius: 3px; }}
QPlainTextEdit#planOutput {{ font-family: "{mono}"; font-size: 13px; }}
QPlainTextEdit#log {{ font-family: "{mono}"; font-size: 12px; color: {MUTED}; border: 0;
    background: transparent; padding: 0; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {RAISED}; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 30px; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, colour in (
        (QPalette.ColorRole.Window, BG), (QPalette.ColorRole.Base, BG),
        (QPalette.ColorRole.AlternateBase, PANEL), (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Button, RAISED),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, AMBER),
        (QPalette.ColorRole.HighlightedText, AMBER_TEXT), (QPalette.ColorRole.PlaceholderText, DIM),
        (QPalette.ColorRole.ToolTipBase, PANEL), (QPalette.ColorRole.ToolTipText, TEXT),
    ):
        pal.setColor(role, QColor(colour))
    app.setPalette(pal)
    app.setFont(ui_font(13))
    app.setStyleSheet(stylesheet())


def repolish(widget) -> None:
    """Re-apply the stylesheet after a dynamic property such as `role` changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
