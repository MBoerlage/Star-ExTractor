"""App dialogs (ui/dialogs.py + theme.dialog_css): pixel checks on offscreen grabs in all three palettes. The file
dialog used to be one near-black surface with unreadable toolbar dots; now panel / bg / panel2 / input form a
hierarchy, our icons are drawn in the text colour, and Directory mode shows the folder's FITS files greyed."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QModelIndex, QSize, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QPushButton,
    QTreeView,
    QWidget,
)
from pytestqt.qtbot import QtBot

from starextractor.ui import dialogs, theme

PALETTES = ("default", "night", "red")


@pytest.fixture(params=PALETTES)
def palette(request) -> str:
    theme.apply(QApplication.instance(), request.param)
    yield request.param
    theme.apply(QApplication.instance(), "default")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    d = tmp_path / "capture"
    (d / "darks").mkdir(parents=True)
    for name in ("vega_001.fits", "vega_002.fit", "notes.txt"):
        (d / name).write_bytes(b"x" * 10)
    return d


# ---- pixel helpers -----------------------------------------------------------------------------------------------
def rgb(w: QWidget) -> np.ndarray:
    img = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return a[:, : img.width(), [2, 1, 0]].astype(int)


def lum(c) -> float:
    def ch(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = c
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mode(px: np.ndarray) -> tuple[int, int, int]:
    return Counter(map(tuple, px.reshape(-1, 3))).most_common(1)[0][0]


def best_contrast(px: np.ndarray) -> tuple[float, tuple]:
    """(the highest contrast of any pixel against the most common one, the background)."""
    bg = mode(px)
    flat = {tuple(p) for p in px.reshape(-1, 3)}
    return max(contrast(p, bg) for p in flat), bg


def tok(name: str) -> tuple[int, int, int]:
    c = QColor(getattr(theme, name))
    return c.red(), c.green(), c.blue()


def differ(a, b, min_step: int = 4) -> bool:
    return max(abs(x - y) for x, y in zip(a, b, strict=True)) >= min_step


def assert_dark_room(px: np.ndarray, palette: str) -> None:
    lit = px.sum(axis=2) > 24
    if palette == "red":
        bad = lit & ((px[..., 1] > 2) | (px[..., 2] > 2))
        assert not bad.any(), ("red palette: only the red channel", Counter(map(tuple, px[bad])).most_common(3),
                               np.argwhere(bad)[:3].tolist())
    elif palette == "night":
        bad = lit & ((px[..., 1] > px[..., 0] + 2) | (px[..., 2] > px[..., 0] + 2))
        assert not bad.any(), ("night palette: no green or blue", Counter(map(tuple, px[bad])).most_common(3),
                               np.argwhere(bad)[:3].tolist())


def no_caret(dlg: QWidget) -> None:
    """Qt draws the text caret inverted (NotDestination: bluish on a warm background), in every line edit of the
    app, so it is left out of the grabs; a read-only field shows none."""
    for e in dlg.findChildren(QLineEdit):
        e.setReadOnly(True)
    QApplication.processEvents()


def shown(dlg: QWidget, qtbot: QtBot, size=(820, 520)) -> QWidget:
    qtbot.addWidget(dlg)
    dlg.resize(*size)
    dlg.show()
    qtbot.waitExposed(dlg)
    return dlg


def dir_dialog(folder: Path, qtbot: QtBot) -> QFileDialog:
    dlg = shown(dialogs.file_dialog(None, "Target folder to watch", str(folder), mode="directory"), qtbot)
    view = dlg.findChild(QTreeView, "treeView")
    # QFileSystemModel fills in the background, which is slow on a cold disk cache
    qtbot.waitUntil(lambda: view.model().rowCount(view.rootIndex()) >= 3, timeout=20000)
    no_caret(dlg)
    qtbot.wait(50)
    return dlg


def row_of(view: QTreeView, name: str) -> QModelIndex:
    m, root = view.model(), view.rootIndex()
    return next(m.index(r, 0, root) for r in range(m.rowCount(root)) if m.index(r, 0, root).data() == name)


# ---- the file dialog -------------------------------------------------------------------------------------------------
def test_folder_dialog_surfaces_form_a_hierarchy(palette: str, folder: Path, qtbot: QtBot) -> None:
    dlg = dir_dialog(folder, qtbot)
    img = rgb(dlg)
    panel = tok("PANEL")
    assert tuple(img[2, 2]) == panel and tuple(img[-2, dlg.width() // 2]) == panel  # the dialog: panel, not bg
    view = dlg.findChild(QTreeView, "treeView")
    vp = view.viewport()
    list_bg = mode(rgb(vp)[-30:])  # below the rows
    side = dlg.findChild(QListView, "sidebar").viewport()
    side_bg = mode(rgb(side)[-30:])
    header_bg = mode(rgb(view.header())[2:-2, 4:60])
    field = dlg.findChild(QLineEdit, "fileNameEdit")
    field_bg = mode(rgb(field)[6:-6, 6:-6])
    assert list_bg == tok("BG") and side_bg == tok("CARD") and header_bg == tok("CARD") and field_bg == tok("INPUT")
    assert differ(list_bg, side_bg) and differ(list_bg, header_bg) and differ(panel, list_bg)
    assert differ(field_bg, panel)  # a field stands out from the dialog around it (and has its border)
    if theme.BG.upper() != "#000000":
        assert not np.any(np.all(img == 0, axis=2)), "pure black"
    assert_dark_room(img, palette)


def test_text_contrast(palette: str, folder: Path, qtbot: QtBot) -> None:
    dlg = dir_dialog(folder, qtbot)
    for name in ("lookInLabel", "fileNameLabel", "fileTypeLabel"):
        c, _bg = best_contrast(rgb(dlg.findChild(QLabel, name)))
        assert c >= 4.5, (palette, name, c)
    for b in dlg.findChildren(QPushButton):
        if b.isVisible():
            c, _bg = best_contrast(rgb(b)[6:-6, 6:-6])
            assert c >= 4.5, (palette, b.text(), c)
    view = dlg.findChild(QTreeView, "treeView")
    r = view.visualRect(row_of(view, "darks"))
    c, _bg = best_contrast(rgb(view.viewport())[r.top():r.bottom(), r.left() + 26:r.right()])
    assert c >= 4.5, (palette, "folder name", c)


def test_toolbar_icons_are_readable(palette: str, folder: Path, qtbot: QtBot) -> None:
    dlg = dir_dialog(folder, qtbot)
    buttons = dialogs.tool_buttons(dlg)
    assert list(buttons) == list(dialogs.TOOL_BUTTONS)
    assert all(b.objectName() == n for n, b in buttons.items())  # found by object name in this Qt version
    for name, b in buttons.items():
        assert not b.icon().isNull() and b.width() >= 22, name
        px = rgb(b)
        c, _bg = best_contrast(px)
        if b.isEnabled():
            assert c >= 3.0, (palette, name, c)
        icon_px = (np.abs(px - np.array(mode(px))).max(axis=2) > 40).sum()
        assert icon_px >= 12, (palette, name, icon_px)  # an icon, not a dot
        assert_dark_room(px, palette)
    detail = buttons["detailModeButton"]
    assert detail.isDown() and differ(mode(rgb(detail)), tok("PANEL"))  # the active view mode shows (Qt: down)


def test_icons_are_crisp_at_two_scales() -> None:
    for kind in dialogs.TOOL_ICONS.values():
        ic = dialogs.tool_icon(kind)
        for size in (16, 20):
            for ratio in (1.0, 2.0):
                pm = ic.pixmap(QSize(size, size), ratio)
                assert pm.width() == round(size * ratio), (kind, size, ratio)
                img = pm.toImage()
                alpha = [img.pixelColor(x, y).alpha() for x in range(img.width()) for y in range(img.height())]
                assert sum(a > 200 for a in alpha) >= size * ratio, (kind, size, ratio)  # solid strokes drawn
        assert theme.TEXT.lower() in dialogs.icon_svg(kind, theme.TEXT).lower()


def test_selection_and_hover_are_visible(palette: str, folder: Path, qtbot: QtBot) -> None:
    dlg = dir_dialog(folder, qtbot)
    view = dlg.findChild(QTreeView, "treeView")
    idx = row_of(view, "darks")
    view.setCurrentIndex(idx)
    view.selectionModel().select(idx, view.selectionModel().SelectionFlag.ClearAndSelect
                                 | view.selectionModel().SelectionFlag.Rows)
    qtbot.wait(20)
    r = view.visualRect(idx)
    row_bg = mode(rgb(view.viewport())[r.top() + 1:r.bottom() - 1, r.right() - 40:r.right()])
    assert row_bg == tok("BG") or differ(row_bg, tok("BG"), 12)
    assert contrast(row_bg, tok("BG")) > 1.15 or differ(row_bg, tok("BG"), 25), (palette, row_bg)
    hover = theme.mix(theme.BG, theme.AMBER, 0.16)
    assert f"::item:hover {{ background: {hover}" in theme.dialog_css()
    assert differ(tok("BG"), QColor(hover).getRgb()[:3], 10)


def test_directory_mode_shows_fits_files_greyed(palette: str, folder: Path, qtbot: QtBot) -> None:
    dlg = dir_dialog(folder, qtbot)
    assert dlg.fileMode() == QFileDialog.FileMode.Directory
    assert not dlg.testOption(QFileDialog.Option.ShowDirsOnly)
    assert dlg.findChild(QComboBox, "fileTypeCombo").currentText() == "FITS files and folders"
    view = dlg.findChild(QTreeView, "treeView")
    m, root = view.model(), view.rootIndex()
    names = {m.index(r, 0, root).data() for r in range(m.rowCount(root))}
    assert names == {"darks", "vega_001.fits", "vega_002.fit"}  # FITS shown, other files not
    sub, fits = row_of(view, "darks"), row_of(view, "vega_001.fits")
    assert m.flags(sub) & Qt.ItemFlag.ItemIsSelectable and m.flags(sub) & Qt.ItemFlag.ItemIsEnabled
    assert not m.flags(fits) & Qt.ItemFlag.ItemIsSelectable and not m.flags(fits) & Qt.ItemFlag.ItemIsEnabled
    vp = rgb(view.viewport())

    def brightest(i: QModelIndex) -> float:
        r = view.visualRect(i)
        return max(lum(p) for p in vp[r.top():r.bottom(), r.left() + 26:r.right()].reshape(-1, 3))

    assert brightest(fits) < brightest(sub)  # greyed out (not_run_text)


def test_open_and_save_dialogs(palette: str, folder: Path, qtbot: QtBot) -> None:
    for mode_, flt in (("open", "FITS (*.fits *.fit *.fts)"), ("save", "Text (*.txt)")):
        dlg = shown(dialogs.file_dialog(None, f"{mode_} test", str(folder), flt, mode=mode_), qtbot)
        no_caret(dlg)
        assert dlg.testOption(QFileDialog.Option.DontUseNativeDialog)
        img = rgb(dlg)
        assert tuple(img[2, 2]) == tok("PANEL")
        assert all(not b.icon().isNull() for b in dialogs.tool_buttons(dlg).values())
        assert_dark_room(img, palette)
        dlg.close()
    save = dialogs.file_dialog(None, "s", str(folder), "Text (*.txt)", mode="save")
    assert save.acceptMode() == QFileDialog.AcceptMode.AcceptSave and save.defaultSuffix() == "txt"


# ---- message boxes and input dialogs ---------------------------------------------------------------------------------
def test_message_box_and_input_dialog(palette: str, qtbot: QtBot) -> None:
    box = shown(dialogs.message(None, "Load planned pair", "Acquisition has 41 subs for beta cas. Load Vega and clear "
                                "them? Saved stacks stay in your data folder."), qtbot, (520, 180))
    inp = QInputDialog()
    inp.setWindowTitle("Add a target line")
    inp.setLabelText("Wavelength (Å)")
    dialogs.style_dialog(inp)
    shown(inp, qtbot, (360, 140))
    no_caret(inp)
    for dlg in (box, inp):
        img = rgb(dlg)
        assert tuple(img[2, 2]) == tok("PANEL"), (palette, type(dlg).__name__)
        for lbl in dlg.findChildren(QLabel):
            if lbl.isVisible() and lbl.text():
                c, _bg = best_contrast(rgb(lbl))
                assert c >= 4.5, (palette, lbl.text()[:20], c)
        for b in dlg.findChildren(QPushButton):
            if b.isVisible():
                c, _bg = best_contrast(rgb(b)[6:-6, 6:-6])
                assert c >= 4.5, (palette, b.text(), c)
        assert_dark_room(img, palette)


def test_every_dialog_gets_the_dark_title_bar(palette: str, monkeypatch: pytest.MonkeyPatch, qtbot: QtBot) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(theme, "set_dark_title_bar", lambda w, on: calls.append(on) or False)
    box = QMessageBox(QMessageBox.Icon.Information, "t", "x")
    qtbot.addWidget(box)
    dialogs.style_dialog(box)
    assert calls == [True]  # in every palette; failures are ignored
    # a dialog opened elsewhere (a static call, a custom dialog) is styled when it takes the focus
    other = QInputDialog()
    qtbot.addWidget(other)
    edit = QLineEdit(other)
    dialogs._FocusStyler().changed(None, edit)
    assert other.property(dialogs.STYLED) and calls == [True, True]


def test_no_native_dialogs_and_no_static_file_dialogs() -> None:
    assert QApplication.instance().testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    root = Path(__file__).resolve().parents[1] / "starextractor"
    static = re.compile(r"QFileDialog\.get(ExistingDirectory|OpenFileNames?|SaveFileName)\(")
    found = [f.name for f in root.rglob("*.py") if static.search(f.read_text(encoding="utf-8"))]
    assert found == []  # every file and folder picker goes through ui/dialogs.py


def test_dialog_styling_uses_tokens_only() -> None:
    root = Path(__file__).resolve().parents[1] / "starextractor" / "ui"
    literal = re.compile(r"#[0-9A-Fa-f]{6}\b|QColor\(\s*[\d\"]")
    for f in ("dialogs.py", "screens/plan_pairs.py"):
        assert not [ln for ln in (root / f).read_text(encoding="utf-8").splitlines() if literal.search(ln)], f
