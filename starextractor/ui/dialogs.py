"""App dialogs: always Qt's own (non-native) dialogs, styled from the palette (docs/THEMES.md › Dialogs).

* The look comes from the style sheet (theme.stylesheet: dialog background `panel`, views `bg` with alternating rows,
  sidebar and header `panel2`, fields `input`, an accent-tint selection and hover).
* `style_dialog` adds what a style sheet cannot: our own toolbar icons on a file dialog (back, forward, up, new
  folder, list, detail; SVG in the text colour, so crisp at any scale), palette file icons in the night palettes
  (the system ones are blue and yellow), a palette message-box icon in the night palettes, the disabled-item colour,
  and the dark title bar (best effort, every palette).
* The helpers below create and style each dialog before it is shown. Dialogs the app opens elsewhere (static
  QMessageBox / QInputDialog calls, custom dialogs) are styled when they first take the focus (`install`), so every
  dialog gets the same treatment.
* Directory mode shows the FITS files of the folder too, greyed out and not selectable, so the user can see that it
  is the right capture folder; only folders can be chosen.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QFileInfo, QModelIndex, QObject, QRectF, QSize, QSortFilterProxyModel, Qt
from PySide6.QtGui import QAbstractFileIconProvider, QColor, QFont, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHeaderView,
    QListView,
    QMessageBox,
    QSplitter,
    QToolButton,
    QTreeView,
    QWidget,
)

from . import theme

FITS_FILTER = "FITS files and folders (*.fit *.fits *.fts)"
TOOL_BUTTONS = ("backButton", "forwardButton", "toParentButton", "newFolderButton", "listModeButton",
                "detailModeButton")
TOOL_ICONS = dict(zip(TOOL_BUTTONS, ("back", "forward", "up", "new_folder", "list", "detail"), strict=True))
ICON_SIZES = (16, 20)
STYLED = "_starexStyled"

# 20 × 20 line icons; {c} = the colour. Strokes only (plus small filled dots), round ends.
_SVG = {
    "back": '<path d="M16 10H4.5M9 5.5 4.5 10 9 14.5"/>',
    "forward": '<path d="M4 10h11.5M11 5.5l4.5 4.5-4.5 4.5"/>',
    "up": '<path d="M10 16V4.5M5.5 9 10 4.5 14.5 9"/>',
    "new_folder": '<path d="M2.5 15.5v-10h5l1.6 1.8h8.4v8.2z"/><path d="M10 9v5M7.5 11.5h5"/>',
    "list": '<path d="M7.5 5.5h9.5M7.5 10h9.5M7.5 14.5h9.5"/>'
            '<g stroke="none" fill="{c}"><rect x="2.6" y="4.6" width="2" height="2"/>'
            '<rect x="2.6" y="9" width="2" height="2"/><rect x="2.6" y="13.4" width="2" height="2"/></g>',
    "detail": '<rect x="2.5" y="3.5" width="15" height="13" rx="1"/><path d="M2.5 7.5h15M2.5 12h15M8 7.5v9"/>',
    "folder": '<path d="M2.5 15.5v-10h5l1.6 1.8h8.4v8.2z" fill="{c}" fill-opacity="0.35"/>',
    "file": '<path d="M5 2.5h6.5L15 6v11.5H5z"/><path d="M11.5 2.5V6H15"/>',
    "drive": '<rect x="2.5" y="6" width="15" height="8" rx="1.5"/>'
             '<circle cx="14" cy="10" r="1" fill="{c}" stroke="none"/>',
    "computer": '<rect x="2.5" y="3.5" width="15" height="10" rx="1"/><path d="M7 16.5h6M10 13.5v3"/>',
    "help": '<circle cx="10" cy="10" r="7.5"/><path d="M7.7 7.9a2.4 2.4 0 1 1 3.3 2.2c-.6.3-1 .8-1 1.5v.6"/>'
            '<circle cx="10" cy="14.4" r=".9" fill="{c}" stroke="none"/>',
}


def icon_svg(kind: str, colour: str) -> str:
    body = _SVG[kind].replace("{c}", colour)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" '
            f'stroke="{colour}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


def _svg_file(kind: str, colour: str) -> str:
    out = Path(tempfile.gettempdir()) / "starextractor_icons" / "dialog" / f"{kind}_{colour.lstrip('#')}.svg"
    text = icon_svg(kind, colour)
    if not out.exists() or out.read_text(encoding="utf-8") != text:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return str(out)


def tool_icon(kind: str) -> QIcon:
    """One of our icons in the active palette: text colour, dim when disabled; drawn from SVG at each size and
    device pixel ratio (16 and 20 px, ×1 and ×2 stay crisp)."""
    ic = QIcon()
    for size in ICON_SIZES:
        for mode, colour in ((QIcon.Mode.Normal, theme.TEXT), (QIcon.Mode.Active, theme.TEXT),
                             (QIcon.Mode.Selected, theme.TEXT), (QIcon.Mode.Disabled, theme.DIM)):
            ic.addFile(_svg_file(kind, colour), QSize(size, size), mode)
    return ic


class PaletteIconProvider(QAbstractFileIconProvider):
    """Folder, file, drive and computer icons in the palette (night and red: no blue or yellow system icons).
    The icons are SVG files, rendered when painted (the file model asks from its own thread)."""

    def __init__(self) -> None:
        super().__init__()
        self._icons: dict[str, QIcon] = {}
        for k in ("folder", "file", "drive", "computer"):
            ic = QIcon(_svg_file(k, theme.MUTED if k == "file" else theme.AMBER))
            ic.addFile(_svg_file(k, theme.DIM), QSize(), QIcon.Mode.Disabled)  # Qt's own greying is grey
            self._icons[k] = ic

    def icon(self, what):
        if isinstance(what, QFileInfo):
            if what.isRoot():
                return self._icons["drive"]
            return self._icons["folder" if what.isDir() else "file"]
        t = QAbstractFileIconProvider.IconType
        return self._icons.get({t.Computer: "computer", t.Drive: "drive", t.File: "file"}.get(what, "folder"))


class FoldersOnly(QSortFilterProxyModel):
    """Directory mode: files are listed (greyed, as a check that it is the right folder) but cannot be selected."""

    def flags(self, index: QModelIndex):
        f = super().flags(index)
        src = self.sourceModel()
        if index.isValid() and src is not None and hasattr(src, "isDir") and not src.isDir(self.mapToSource(index)):
            f &= ~(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return f


# ---- styling ---------------------------------------------------------------------------------------------------------
def tool_buttons(dlg: QFileDialog) -> dict[str, QToolButton]:
    """The six toolbar buttons by object name; if a Qt version names them differently, by order of appearance."""
    found = {n: b for n in TOOL_BUTTONS if (b := dlg.findChild(QToolButton, n)) is not None}
    if len(found) < len(TOOL_BUTTONS):
        rest = [b for b in dlg.findChildren(QToolButton) if b not in found.values()]
        for n in TOOL_BUTTONS:
            if n not in found and rest:
                found[n] = rest.pop(0)
    return found


def _message_icon(kind: QMessageBox.Icon) -> QPixmap | None:
    """A palette glyph for a message box (night palettes): a ring with ?, !, i or ×."""
    glyph = {QMessageBox.Icon.Question: "?", QMessageBox.Icon.Warning: "!", QMessageBox.Icon.Information: "i",
             QMessageBox.Icon.Critical: "×"}.get(kind)
    if glyph is None:
        return None
    ratio = 2.0
    pm = QPixmap(int(40 * ratio), int(40 * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    colour = QColor(theme.ERR if kind == QMessageBox.Icon.Critical else theme.AMBER)
    p.setPen(QPen(colour, 2.4))
    p.drawEllipse(3, 3, 34, 34)
    p.setFont(theme.ui_font(20, QFont.Weight.Bold))
    p.drawText(QRectF(0, 0, 40, 40), Qt.AlignmentFlag.AlignCenter, glyph)
    p.end()
    return pm


def _disabled_palette(w: QWidget) -> None:
    pal = w.palette()
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(theme.NOTRUN_TEXT))
    w.setPalette(pal)


def style_file_dialog(dlg: QFileDialog) -> None:
    for name, b in tool_buttons(dlg).items():
        b.setIcon(tool_icon(TOOL_ICONS[name]))
        b.setIconSize(QSize(16, 16))
        b.setAutoRaise(True)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    for view in dlg.findChildren(QListView) + dlg.findChildren(QTreeView):
        if view.objectName() != "sidebar":
            view.setAlternatingRowColors(True)
        view.setMouseTracking(True)  # hover
        _disabled_palette(view)
    splitter = dlg.findChild(QSplitter, "splitter")
    if splitter is not None:
        splitter.setSizes([180, max(400, splitter.width() - 180)])  # the sidebar names stay readable
    tree = dlg.findChild(QTreeView, "treeView")
    if tree is not None:
        hh = tree.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, hh.count()):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    if theme.is_dark_room():
        dlg.setSizeGripEnabled(False)  # Fusion draws its dots in grey: not red-filter safe
        provider = PaletteIconProvider()
        dlg._icon_provider = provider  # type: ignore[attr-defined]  # kept alive with the dialog
        dlg.setIconProvider(provider)


def style_dialog(dlg: QDialog) -> QDialog:
    """Everything a style sheet cannot do (see the module text). Safe to call more than once."""
    if isinstance(dlg, QFileDialog) and not dlg.property(STYLED):
        dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        style_file_dialog(dlg)
    if isinstance(dlg, QMessageBox) and theme.is_dark_room() and not dlg.property(STYLED):
        pm = _message_icon(dlg.icon())
        if pm is not None:
            dlg.setIconPixmap(pm)
    dlg.setProperty(STYLED, True)
    theme.set_dark_title_bar(dlg, True)  # best effort, all palettes; failures are ignored
    return dlg


class _FocusStyler(QObject):
    """Styles any dialog when it first takes the focus (a signal, not an app-wide event filter: no cost per
    event). Covers static QMessageBox / QInputDialog calls and custom dialogs."""

    def changed(self, _old, new) -> None:
        w = new.window() if new is not None else None
        if isinstance(w, QDialog) and not w.property(STYLED):
            style_dialog(w)


_styler: _FocusStyler | None = None


def install(app: QApplication) -> None:
    global _styler
    if _styler is None:
        _styler = _FocusStyler()
        app.focusChanged.connect(_styler.changed)


# ---- the helpers -----------------------------------------------------------------------------------------------------
def file_dialog(parent: QWidget | None, title: str, start: str = "", name_filter: str = "",
                mode: str = "open") -> QFileDialog:
    """A styled, non-native file dialog: mode "open", "open_many", "save" or "directory" (not yet shown)."""
    dlg = QFileDialog(parent, title)
    dlg.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    style_dialog(dlg)  # the palette icon provider before any folder is listed
    if mode == "save":
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dlg.setFileMode(QFileDialog.FileMode.AnyFile)
    elif mode == "directory":
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, False)
        dlg.setProxyModel(FoldersOnly(dlg))
        dlg.setOption(QFileDialog.Option.HideNameFilterDetails, True)
        name_filter = name_filter or FITS_FILTER
    else:
        dlg.setFileMode(QFileDialog.FileMode.ExistingFiles if mode == "open_many"
                        else QFileDialog.FileMode.ExistingFile)
    if name_filter:
        dlg.setNameFilters(name_filter.split(";;"))
    if mode == "save":
        suffix = name_filter.split("*.")[1].split(")")[0].split()[0] if "*." in name_filter else ""
        if suffix:
            dlg.setDefaultSuffix(suffix)
    if start:
        p = Path(start)
        if mode == "save" and not p.is_dir():
            dlg.setDirectory(str(p.parent))
            dlg.selectFile(p.name)
        else:
            dlg.setDirectory(start)
    _palette_look_in(dlg)
    return dlg


def _palette_look_in(dlg: QFileDialog) -> None:
    """Night palettes: Qt shows its own (blue) folder icon in "Look in" until the folder is listed."""
    provider = getattr(dlg, "_icon_provider", None)
    combo = dlg.findChild(QComboBox, "lookInCombo")
    if provider is None or combo is None:
        return
    for i in range(combo.count()):
        combo.setItemIcon(i, provider.icon(QFileInfo(dlg.directory().absolutePath())))


def _run(dlg: QFileDialog) -> list[str]:
    return dlg.selectedFiles() if dlg.exec() == QDialog.DialogCode.Accepted else []


def existing_directory(parent: QWidget | None, title: str, start: str = "") -> str:
    """A folder ("" when cancelled); its FITS files are shown greyed."""
    files = _run(file_dialog(parent, title, start, mode="directory"))
    return files[0] if files else ""


def open_file(parent: QWidget | None, title: str, start: str = "", name_filter: str = "") -> str:
    files = _run(file_dialog(parent, title, start, name_filter, "open"))
    return files[0] if files else ""


def open_files(parent: QWidget | None, title: str, start: str = "", name_filter: str = "") -> list[str]:
    return _run(file_dialog(parent, title, start, name_filter, "open_many"))


def save_file(parent: QWidget | None, title: str, start: str = "", name_filter: str = "") -> str:
    files = _run(file_dialog(parent, title, start, name_filter, "save"))
    return files[0] if files else ""


def message(parent: QWidget | None, title: str, text: str, icon: QMessageBox.Icon = QMessageBox.Icon.Question,
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            default=QMessageBox.StandardButton.No) -> QMessageBox:
    """A styled message box (not yet shown)."""
    box = QMessageBox(icon, title, text, buttons, parent)
    box.setDefaultButton(default)
    return style_dialog(box)  # type: ignore[return-value]


def ask(parent: QWidget | None, title: str, text: str) -> bool:
    """Yes / No, No by default."""
    return message(parent, title, text).exec() == QMessageBox.StandardButton.Yes
