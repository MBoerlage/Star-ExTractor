"""Help me: the workflow guide (resources/help.md) opened from the top row or F1."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from starex_reduce.core.session import Session
from starex_reduce.ui import help as hp
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    theme.apply(QApplication.instance(), "default")
    w = MainWindow(session=Session.create(tmp_path / "pair"))
    qtbot.addWidget(w)
    w.show()
    return w


def test_guide_covers_the_whole_workflow() -> None:
    text = hp.help_text()
    for part in ("## 1. Plan", "### Planned pairs", "### Loading a pair into Acquisition", "## 2. Acquisition",
                 "### Stacking", "## 3. Reduce", "### Select", "### Geometry", "### 1D Extract", "### Wavelength",
                 "### Response", "## 4. Export", "### Collect data", "### Test", "### Publish", "## 5. Library",
                 "## Night mode", "## Keyboard", "## Internet and privacy", "## When something goes wrong"):
        assert part in text, part
    assert len(text.split()) > 3000  # an extensive guide, not a page of hints


def test_help_button_and_f1_open_it(win: MainWindow, qtbot: QtBot) -> None:
    assert win.help_btn.text() == "Help me" and not win.help_btn.icon().isNull()
    win.help_btn.click()
    dlg = win.help_dialog
    assert dlg.isVisible() and not dlg.isModal()
    assert [h[1] for h in dlg.headings][:2] == ["Before your first night", "1. Plan: what to observe tonight"]
    assert dlg.contents.count() == len(dlg.headings) > 20
    dlg.close()
    assert win.open_help("Wavelength") is dlg and dlg.isVisible()  # one window, reopened at a section
    assert dlg.browser.textCursor().block().text() == "Wavelength"
    assert ("F1", "Help me: the workflow guide", "open_help") in win.SHORTCUTS


def test_find_in_the_guide(win: MainWindow) -> None:
    dlg = win.open_help()
    dlg.find.setText("Load for acquisition")
    assert dlg.find_next() and dlg.browser.textCursor().selectedText() == "Load for acquisition"
    dlg.find.setText("no such words here")
    assert not dlg.find_next() and dlg.found.state == "warn"


@pytest.mark.parametrize("palette", ["night", "red"])
def test_help_in_the_night_palettes(win: MainWindow, qtbot: QtBot, palette: str) -> None:
    from starex_reduce.ui import retheme

    dlg = win.open_help()
    retheme.switch(palette)
    try:
        qtbot.wait(30)
        for w in (dlg.browser.viewport(), dlg.contents, win.help_btn):
            img = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
            a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
            px = a[:, : img.width(), [2, 1, 0]].astype(int)
            lit = px.sum(axis=2) > 24
            r, g, b = (px[..., i][lit] for i in range(3))
            if palette == "red":
                assert np.all(g <= 2) and np.all(b <= 2), type(w).__name__
            else:
                assert np.all(g <= r + 2) and np.all(b <= r + 2), type(w).__name__
    finally:
        retheme.switch("default")
