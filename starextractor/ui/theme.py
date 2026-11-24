"""Palettes and the style sheet: default (the approved mockups), night (red-filter safe) and red (docs/THEMES.md).

Every colour in the UI comes from the tokens below. The module attributes (theme.BG, theme.AMBER, …) always hold
the active palette: `set_palette` rebinds them and tells listeners (`notifier.changed`); `apply` re-styles the app.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPalette
from PySide6.QtWidgets import QApplication

# Order = priority when a baked-in colour is translated between palettes (`colour_map`): first token wins.
PALETTES: dict[str, dict[str, str]] = {
    "default": {  # the look of the approved mockups (unchanged)
        "BG": "#0E1116", "PANEL": "#12161C", "CARD": "#161A20", "RAISED": "#1B2028", "LINE": "#2A313B",
        "BORDER": "#3A424E", "TEXT": "#E8EBF0", "MUTED": "#A0A9B6", "DIM": "#5C6674",
        "AMBER": "#F2A33A", "AMBER_TEXT": "#1A1206", "AMBER_HOVER": "#F5B358", "AMBER_DIM": "#6B4F28",
        "AMBER_DIM_TEXT": "#2A1E0C", "TEAL": "#62D0C6", "BLUE": "#6CB4F5", "GREEN": "#7BD389", "RED": "#FF7B72",
        "CARD_ERROR": "#1A1416", "DONE_BG": "#1E4D31", "CURRENT_BG": "#1A3352", "MISSING_BG": "#4D1F1F",
        "NOTRUN_BG": "#1B2028", "INPUT": "#0E1116", "DONE_TEXT": "#7BD389", "NOTRUN_TEXT": "#A0A9B6",
        "OK": "#7BD389", "WARN": "#F2A33A", "ERR": "#FF7B72", "MISSING": "#FF7B72",
        "SPRING": "#5FD08A", "VIOLET": "#B48CFF", "SKYBLUE": "#5AA7FF", "PINK": "#FF6FB5", "SAT": "#FF6B6B",
        "SPECTRUM_FILL": "#2F6BFF", "SPECTRUM_TEXT": "#9DB8FF", "SKY_FILL": "#B9822E", "SKY_TEXT": "#F0C27A",
        "ICON": "#9AA4B2", "WHITE": "#FFFFFF",
    },
    "night": {  # red-filter safe: dark and warm, no green or blue (G <= R and B <= R everywhere)
        "BG": "#120A06", "PANEL": "#1C110B", "CARD": "#26170E", "RAISED": "#2B1A10", "LINE": "#4F2D1A",
        "BORDER": "#80502D", "TEXT": "#FBE3C8", "MUTED": "#F4AA7A", "DIM": "#8A5A38",
        "AMBER": "#FF9A2E", "AMBER_TEXT": "#1A0C00", "AMBER_HOVER": "#FFB25E", "AMBER_DIM": "#6A3F14",
        "AMBER_DIM_TEXT": "#2E1A06", "TEAL": "#FFCB8E", "BLUE": "#E0A070", "GREEN": "#F4BA7A", "RED": "#FF6A3D",
        "CARD_ERROR": "#2A0F06", "DONE_BG": "#6E3E1E", "CURRENT_BG": "#4A2A14", "MISSING_BG": "#3A1408",
        "NOTRUN_BG": "#24160D", "INPUT": "#150D08", "DONE_TEXT": "#FFD9AE", "NOTRUN_TEXT": "#C8936A",
        "OK": "#F4BA7A", "WARN": "#FFB04A", "ERR": "#FF6A3D", "MISSING": "#FF5C3A",
        "SPRING": "#F7D2A6", "VIOLET": "#D98B4E", "SKYBLUE": "#CC9466", "PINK": "#FFAE6B", "SAT": "#FF7A4A",
        "SPECTRUM_FILL": "#FF9A2F", "SPECTRUM_TEXT": "#FFC48A", "SKY_FILL": "#9E5A22", "SKY_TEXT": "#E0A878",
        "ICON": "#BF8A5F", "WHITE": "#FFE4C4",
    },
    "red": {  # pure red, only the red channel (G = B = 0); not on the switch: settings ui.palette = "red"
        "BG": "#0A0000", "PANEL": "#150000", "CARD": "#1F0000", "RAISED": "#1A0000", "LINE": "#4A0000",
        "BORDER": "#7A0000", "TEXT": "#FB0000", "MUTED": "#F40000", "DIM": "#600000",
        "AMBER": "#FF0000", "AMBER_TEXT": "#120000", "AMBER_HOVER": "#F70000", "AMBER_DIM": "#580000",
        "AMBER_DIM_TEXT": "#2A0000", "TEAL": "#F60000", "BLUE": "#C80000", "GREEN": "#F90000", "RED": "#FE0000",
        "CARD_ERROR": "#1C0000", "DONE_BG": "#6A0000", "CURRENT_BG": "#400000", "MISSING_BG": "#300000",
        "NOTRUN_BG": "#1A0000", "INPUT": "#0F0000", "DONE_TEXT": "#FC0000", "NOTRUN_TEXT": "#C00000",
        "OK": "#F90000", "WARN": "#FA0000", "ERR": "#FE0000", "MISSING": "#FD0000",
        "SPRING": "#DC0000", "VIOLET": "#B40000", "SKYBLUE": "#A80000", "PINK": "#F30000", "SAT": "#F10000",
        "SPECTRUM_FILL": "#F80000", "SPECTRUM_TEXT": "#F50000", "SKY_FILL": "#900000", "SKY_TEXT": "#D00000",
        "ICON": "#9B0000", "WHITE": "#F20000",
    },
}
PALETTE_NAMES = ("default", "night", "red")
TOKENS = tuple(PALETTES["default"])
# the token names of docs/THEMES.md → the attribute that holds them
SPEC_TOKENS = {
    "bg": "BG", "panel": "PANEL", "panel2": "CARD", "border": "LINE", "border_strong": "BORDER", "text": "TEXT",
    "text_muted": "MUTED", "accent": "AMBER", "accent_text": "AMBER_TEXT", "done": "DONE_BG",
    "done_text": "DONE_TEXT", "not_run": "NOTRUN_BG", "not_run_text": "NOTRUN_TEXT", "missing": "MISSING",
    "ok": "OK", "warn": "WARN", "err": "ERR", "input": "INPUT", "band_spectrum": "SPECTRUM_FILL",
    "band_sky": "SKY_FILL",
}
BAND_ALPHA = {"default": (0.42, 0.22), "night": (0.40, 0.14), "red": (0.38, 0.12)}  # spectrum, sky

# the active palette (module attributes, rebound by set_palette)
palette = "default"
BG = PANEL = CARD = RAISED = LINE = BORDER = TEXT = MUTED = DIM = AMBER = AMBER_TEXT = AMBER_HOVER = ""
AMBER_DIM = AMBER_DIM_TEXT = TEAL = BLUE = GREEN = RED = CARD_ERROR = DONE_BG = CURRENT_BG = MISSING_BG = ""
NOTRUN_BG = INPUT = DONE_TEXT = NOTRUN_TEXT = OK = WARN = ERR = MISSING = SPRING = VIOLET = SKYBLUE = PINK = ""
SAT = SPECTRUM_FILL = SPECTRUM_TEXT = SKY_FILL = SKY_TEXT = ICON = WHITE = ""
globals().update(PALETTES["default"])


def is_dark_room() -> bool:
    """night or red: the dark-observatory rules apply (no light, states by brightness and glyph, not hue)."""
    return palette != "default"


def token(name: str) -> str:
    """A colour by its THEMES.md name (bg, accent, …) or its attribute name (BG, AMBER, …)."""
    return PALETTES[palette][SPEC_TOKENS.get(name, name)]


def colour_map(old: str, new: str) -> dict[str, str]:
    """#RRGGBB (upper case) of palette `old` → the same token's value in `new` (the first token wins)."""
    out: dict[str, str] = {}
    for t in TOKENS:
        out.setdefault(PALETTES[old][t].upper(), PALETTES[new][t])
    return out


_HEX = re.compile(r"#[0-9A-Fa-f]{6}(?=[0-9A-Fa-f]{2}\b|\b)")


def translate_css(text: str, mp: dict[str, str]) -> str:
    """Swap every #RRGGBB (and the colour part of #RRGGBBAA) of a style sheet through `mp`."""
    return _HEX.sub(lambda m: mp.get(m.group(0).upper(), m.group(0)), text)


def translate_colour(c: QColor, mp: dict[str, str]) -> QColor:
    new = mp.get(c.name(QColor.NameFormat.HexRgb).upper())
    if new is None:
        return QColor(c)
    out = QColor(new)
    out.setAlpha(c.alpha())
    return out


def with_alpha(hex_colour: str, alpha: int) -> QColor:
    c = QColor(hex_colour)
    c.setAlpha(alpha)
    return c


def band_brush(kind: str) -> QColor:
    """The extraction band fill: kind "spectrum" or "sky" (THEMES.md band_spectrum / band_sky)."""
    a = BAND_ALPHA[palette][0 if kind == "spectrum" else 1]
    return with_alpha(SPECTRUM_FILL if kind == "spectrum" else SKY_FILL, round(255 * a))


def image_lut():
    """The 2D image colour map: None = grey (default); night and red: black → accent (a monochrome ramp)."""
    if palette == "default":
        return None
    import numpy as np

    c = QColor(AMBER)
    ramp = np.linspace(0.0, 1.0, 256)[:, None]
    return np.round(ramp * np.array([c.red(), c.green(), c.blue()], float)).astype(np.ubyte)


def colour_table() -> list[int] | None:
    """`image_lut` as a QImage colour table (Indexed8)."""
    lut = image_lut()
    if lut is None:
        return None
    from PySide6.QtGui import qRgb

    return [qRgb(int(r), int(g), int(b)) for r, g, b in lut]


def tinted(img: QImage) -> QImage:
    """A grey QImage through the palette's ramp (returned unchanged in the default palette)."""
    table = colour_table()
    if table is None or img.isNull():
        return img
    g = img.convertToFormat(QImage.Format.Format_Grayscale8)
    ind = QImage(g.constBits(), g.width(), g.height(), g.bytesPerLine(), QImage.Format.Format_Indexed8).copy()
    ind.setColorTable(table)
    return ind


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
ICON_COLOURS = {"check.svg": "AMBER_TEXT", "copy.svg": "ICON"}  # the one colour each icon is drawn in


def icon_path(name: str) -> str:
    """An icon from ui/icons in the active palette (the original file in the default palette)."""
    if palette == "default":
        return f"{ICONS}/{name}"
    import tempfile

    out = Path(tempfile.gettempdir()) / "starextractor_icons" / palette / name
    src = (Path(ICONS) / name).read_text(encoding="utf-8")
    text = src.replace(PALETTES["default"][ICON_COLOURS[name]], PALETTES[palette][ICON_COLOURS[name]])
    if not out.exists() or out.read_text(encoding="utf-8") != text:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return out.as_posix()


def icon(name: str):
    """A QIcon from ui/icons in the active palette. Night / red also give the disabled look explicitly (Qt's own
    greys it, which is neutral grey and so not pure red)."""
    from PySide6.QtGui import QIcon

    ic = QIcon(icon_path(name))
    if is_dark_room():
        from PySide6.QtCore import QByteArray
        from PySide6.QtGui import QPixmap

        src = (Path(ICONS) / name).read_text(encoding="utf-8")
        pm = QPixmap()
        pm.loadFromData(QByteArray(src.replace(PALETTES["default"][ICON_COLOURS[name]], DIM).encode()), "SVG")
        ic.addPixmap(pm, QIcon.Mode.Disabled)
    return ic


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
QLabel[role="chip-warn"] {{ font-family: "{mono}"; font-size: 11px; color: {AMBER}; border: 1px solid {AMBER_DIM};
    border-radius: 5px; padding: 1px 6px; background: transparent; }}
QLabel[role="chip-ok"] {{ font-family: "{mono}"; font-size: 11px; color: {DONE_TEXT}; border: 1px solid {DONE_BG};
    border-radius: 9px; padding: 1px 8px; background: {DONE_BG}; font-weight: 600; }}
QLabel[role="grade-good"] {{ font-family: "{mono}"; font-size: 11px; color: {GREEN}; border: 1px solid {GREEN};
    border-radius: 5px; padding: 1px 6px; background: transparent; font-weight: 600; }}
QLabel[role="grade-ok"] {{ font-family: "{mono}"; font-size: 11px; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 5px; padding: 1px 6px; background: transparent; }}
QLabel[role="grade-poor"] {{ font-family: "{mono}"; font-size: 11px; color: {AMBER}; border: 1px dashed {AMBER_DIM};
    border-radius: 5px; padding: 1px 6px; background: transparent; }}
QLabel[role="tag"] {{ font-size: 11px; color: {MUTED}; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 0 5px; background: transparent; }}
QFrame#plannedStrip {{ background: {CARD}; border: 1px solid {LINE}; border-left: 3px solid {AMBER};
    border-radius: 8px; }}
QToolButton#copyBtn {{ border: 0; padding: 4px; min-height: 0; background: transparent; }}
QToolButton#copyBtn:hover {{ background: {RAISED}; }}
QLabel[role="good"] {{ font-size: 11px; color: {GREEN}; background: transparent; }}
QLabel[role="mono"] {{ font-family: "{mono}"; font-size: 12px; color: {MUTED}; }}
QLabel[role="banner"] {{ background: {MISSING_BG}; color: {RED}; border: 1px solid {RED};
    border-radius: 8px; padding: 8px 12px; }}
QFrame[role="card"] {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame[role="card-error"] {{ background: {CARD_ERROR}; border: 1px solid {RED}; border-radius: 12px; }}
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
QTableWidget#planRefs::item {{ padding: 4px 6px; }}
QHeaderView::section {{ background: {BG}; color: {MUTED}; border: 0; border-bottom: 1px solid {LINE};
    padding: 6px 8px; font-size: 11px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QTableWidget::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER};
    border-radius: 3px; background: {BG}; }}
QCheckBox::indicator:checked, QTableWidget::indicator:checked {{ background: {AMBER}; border-color: {AMBER};
    image: url("{icon_path("check.svg")}"); }}
QComboBox::drop-down {{ border: 0; width: 18px; }}
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 12px; height: 12px; border: 1px solid {BORDER}; border-radius: 7px;
    background: {BG}; }}
QRadioButton::indicator:checked {{ border: 4px solid {BLUE}; background: {BG}; width: 6px; height: 6px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {RAISED}; }}
QLineEdit, QPlainTextEdit, QDoubleSpinBox, QSpinBox, QDateEdit, QTimeEdit, QComboBox {{
    background: {INPUT}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {AMBER}; selection-color: {AMBER_TEXT}; }}
QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QDateEdit:focus, QTimeEdit:focus,
QComboBox:focus {{ border-color: {AMBER}; }}
QPushButton, QToolButton {{
    background: transparent; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 8px 14px; min-height: 22px; }}
QPushButton:hover, QToolButton:hover {{ background: {RAISED}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {AMBER}; }}
QPushButton:disabled {{ color: {DIM}; border-color: {LINE}; }}
QPushButton[role="primary"] {{ background: {AMBER}; color: {AMBER_TEXT}; border: 0; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {AMBER_HOVER}; }}
QPushButton[role="primary"]:disabled {{ background: {AMBER_DIM}; color: {AMBER_DIM_TEXT}; }}
QPushButton[role="link"] {{ border: 0; background: transparent; color: {AMBER}; padding: 2px 4px;
    min-height: 0; text-decoration: underline; }}
QPushButton[role="link"]:hover {{ color: {AMBER_HOVER}; background: transparent; }}
QPushButton[role="nav"] {{ padding: 0; min-width: 30px; max-width: 30px; min-height: 30px;
    border-radius: 6px; }}
QToolButton#sessionChip {{
    font-family: "{mono}"; font-size: 12px; color: {MUTED}; border: 1px solid {BORDER};
    border-radius: 15px; padding: 6px 12px; background: {BG}; }}
QToolButton#helpBtn {{ font-size: 12px; color: {MUTED}; border: 1px solid {BORDER}; border-radius: 15px;
    padding: 5px 10px 5px 7px; background: {BG}; min-height: 0; }}
QToolButton#helpBtn:hover {{ color: {TEXT}; background: {RAISED}; }}
QTextBrowser#helpText {{ background: {BG}; border: 1px solid {LINE}; border-radius: 8px; color: {TEXT};
    selection-background-color: {AMBER}; selection-color: {AMBER_TEXT}; }}
QListWidget#helpContents {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 8px; padding: 4px;
    outline: 0; }}
QListWidget#helpContents::item {{ padding: 5px 6px; border-radius: 4px; color: {MUTED}; }}
QListWidget#helpContents::item:hover {{ background: {RAISED}; color: {TEXT}; }}
QListWidget#helpContents::item:selected {{ background: {RAISED}; color: {TEXT}; }}
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
{dialog_css()}
"""


def mix(a: str, b: str, t: float) -> str:
    """#RRGGBB between a (t = 0) and b (t = 1): channel by channel, so a red-only palette stays red-only."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name().upper()


def dialog_css() -> str:
    """Every dialog (file and folder dialogs, message boxes, input dialogs, custom dialogs): docs/THEMES.md ›
    Dialogs. Background panel; views bg with an alternating row and an accent-tint selection and hover; sidebar and
    header panel2; fields input; small toolbar buttons with our icons (ui/dialogs.py)."""
    alt, hover, sel = mix(BG, TEXT, 0.035), mix(BG, AMBER, 0.16), mix(BG, AMBER, 0.34)
    v = "QFileDialog QListView, QFileDialog QTreeView"
    item = "QFileDialog QListView::item, QFileDialog QTreeView::item"
    return f"""
QDialog, QMessageBox, QInputDialog, QFileDialog {{ background: {PANEL}; }}
QDialog QLabel {{ color: {TEXT}; }}
QDialog QLabel:disabled {{ color: {NOTRUN_TEXT}; }}
{v} {{ background: {BG}; alternate-background-color: {alt}; border: 1px solid {LINE}; border-radius: 6px;
    selection-background-color: {sel}; selection-color: {TEXT}; outline: 0; }}
{item} {{ padding: 3px 4px; color: {TEXT}; }}
{item.replace("::item", "::item:hover")} {{ background: {hover}; }}
{item.replace("::item", "::item:selected")} {{ background: {sel}; color: {TEXT}; }}
{item.replace("::item", "::item:disabled")} {{ color: {NOTRUN_TEXT}; }}
QFileDialog QListView#sidebar {{ background: {CARD}; alternate-background-color: {CARD}; }}
QFileDialog QHeaderView {{ background: {CARD}; border: 0; }}
QFileDialog QHeaderView::section {{ background: {CARD}; color: {MUTED}; border: 0; border-right: 1px solid {LINE};
    border-bottom: 1px solid {LINE}; padding: 5px 8px; font-size: 12px; }}
QFileDialog QToolButton {{ padding: 4px; min-height: 0; min-width: 0; border: 1px solid transparent;
    border-radius: 6px; background: transparent; }}
QFileDialog QToolButton:hover {{ background: {RAISED}; border-color: {BORDER}; }}
QFileDialog QToolButton:pressed {{ background: {LINE}; border-color: {BORDER}; }}
QFileDialog QToolButton:checked {{ background: {mix(BG, AMBER, 0.22)}; border-color: {AMBER_DIM}; }}
QFileDialog QToolButton:disabled {{ background: transparent; border-color: transparent; }}
QFileDialog QSplitter::handle {{ background: {PANEL}; }}
QFileDialog QComboBox:disabled, QDialog QLineEdit:disabled {{ color: {NOTRUN_TEXT}; border-color: {LINE}; }}
QDialogButtonBox QPushButton {{ min-width: 72px; }}
QMessageBox QLabel#qt_msgbox_informativelabel {{ color: {MUTED}; }}
"""


class _Notifier(QObject):
    changed = Signal(str, str)  # old palette, new palette


notifier = _Notifier()


def qpalette() -> QPalette:
    pal = QPalette()
    roles = [
        (QPalette.ColorRole.Window, BG), (QPalette.ColorRole.Base, BG),
        (QPalette.ColorRole.AlternateBase, PANEL), (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Button, RAISED),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, AMBER),
        (QPalette.ColorRole.HighlightedText, AMBER_TEXT), (QPalette.ColorRole.PlaceholderText, DIM),
        (QPalette.ColorRole.ToolTipBase, PANEL), (QPalette.ColorRole.ToolTipText, TEXT),
    ]
    if is_dark_room():  # Fusion draws bevels, arrows, frames and links from these: keep them in the palette too
        roles += [
            (QPalette.ColorRole.Light, BORDER), (QPalette.ColorRole.Midlight, LINE),
            (QPalette.ColorRole.Mid, LINE), (QPalette.ColorRole.Dark, PANEL), (QPalette.ColorRole.Shadow, BG),
            (QPalette.ColorRole.BrightText, TEXT), (QPalette.ColorRole.Link, AMBER),
            (QPalette.ColorRole.LinkVisited, AMBER), (QPalette.ColorRole.Accent, AMBER),
        ]
    for role, colour in roles:
        pal.setColor(role, QColor(colour))
    if is_dark_room():
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(DIM))
    return pal


def apply(app: QApplication, name: str | None = None) -> None:
    """Style the app; `name` switches to that palette first (an unknown name means default)."""
    if name is not None:
        set_palette(name, emit=False)
    from PySide6.QtCore import Qt

    app.setStyle("Fusion")
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)  # file dialogs drawn by Qt, in the palette
    app.setPalette(qpalette())
    app.setFont(ui_font(13))
    app.setStyleSheet(stylesheet())
    from . import dialogs

    dialogs.install(app)  # dialogs that take the focus get the dark title bar and our icons


def set_palette(name: str, emit: bool = True) -> dict[str, str]:
    """Rebind the colour attributes to palette `name`. Returns the old → new map for colours already baked into
    widgets and plot items; listeners of `notifier.changed` translate and repaint them."""
    global palette
    name = name if name in PALETTES else "default"
    old, palette = palette, name
    globals().update(PALETTES[name])
    if emit and old != name:
        notifier.changed.emit(old, name)
    return colour_map(old, name)


def set_dark_title_bar(widget, on: bool) -> bool:
    """Windows 10/11: ask DWM for the dark title bar (best effort). It can be dark grey, never red or amber."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        hwnd = int(widget.winId())
        val = ctypes.c_int(1 if on else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 on Windows 10 before 20H1)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                return True
    except (AttributeError, OSError, ValueError):
        pass
    return False


def repolish(widget) -> None:
    """Re-apply the stylesheet after a dynamic property such as `role` changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
