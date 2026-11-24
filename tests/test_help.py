"""Help me: the workflow guide (resources/help.md) opened from the top row or F1."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from starextractor.core.session import Session
from starextractor.ui import help as hp
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow


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
    from starextractor.ui import retheme

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


def test_reference_star_section_matches_the_bundled_list(win: MainWindow) -> None:
    """Where the reference stars come from: its numbers are the meta file's, it is in the contents, and the old
    "ranked by distance only" is gone."""
    from starextractor.core.reference_stars import meta

    m = meta()
    text = hp.help_text()
    section = text.split("### Where the reference stars come from", 1)[1].split("\n### ", 1)[0]
    g = m["counts"]["grades"]
    assert (f"{m['counts']['kept']} stars: {g['good']} good, {g['ok']} ok and {g['poor']} poor" in section)
    vc = m["counts"]["v_class"]
    assert f"{vc['normal']} normal, {vc['faint']} faint" in section
    assert f"starex_refcat\n**{m['generator_version']}**" in section or f"starex_refcat **{m['generator_version']}**" \
        in section.replace("\n", " ")
    assert f"preset **{m['preset']}**" in section
    assert m["simbad_retrieved"][:10] in section and m["simbad_endpoints"][0] in section
    assert "ranked by distance only" not in text.lower() and "by distance only" not in text.lower()
    assert "Flux ref" not in text
    win.help_btn.click()
    dlg = win.help_dialog
    titles = [h[1] for h in dlg.headings]
    assert "Where the reference stars come from" in titles
    assert titles.index("Where the reference stars come from") == titles.index("The reference star") + 1
    dlg.close()
