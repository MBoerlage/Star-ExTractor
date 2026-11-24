"""About: opens from the logo, shows the splash and the approved credits; the roll moves and loops; the reduced-
motion list has the same text; the night palette renders it; the old wording is gone from the code and docs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from starextractor.core.session import Session
from starextractor.ui import about, theme
from starextractor.ui.about import CREDITS, ROLL_H, SPLASH, AboutDialog, CreditRoll
from starextractor.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]

# The approved wording (2026-11-20; reference star list and VizieR 2026-11-24), typed here on its own so a
# change to about.py cannot also change the test.
APPROVED = (
    ("title", "STAR EXTRACTOR"),
    ("sub", "Spectroscopy reduction for low-resolution slit spectrographs"),
    ("gap", ""),
    ("head", "CREDITS"),
    ("gap", ""),
    ("label", "Stellar flux library"),
    ("text", "Pickles, A. J. (1998). “A Stellar Spectral Flux Library: 1150–25000 Å.”"),
    ("text", "Publications of the Astronomical Society of the Pacific, 110, 863–878."),
    ("gap", ""),
    ("label", "Reference star list"),
    ("text", "Our own selection of bright B and A dwarfs, generated from"),
    ("text", "SIMBAD, the AAVSO VSX and the Washington Double Star Catalog"),
    ("gap", ""),
    ("label", "Spectrum submission and variable star data"),
    ("text", "AAVSO Spectroscopy Database (AVSpec) and Variable Star Index (VSX)"),
    ("gap", ""),
    ("label", "Target lookup"),
    ("text", "This application has made use of the SIMBAD database,"),
    ("text", "operated at CDS, Strasbourg, France, and of the VizieR"),
    ("text", "catalogue access tool, CDS, Strasbourg, France."),
    ("gap", ""),
    ("label", "Built with"),
    ("text", "Python, PySide6 (Qt), NumPy, SciPy, Astropy, pyqtgraph"),
    ("gap", ""),
    ("label", "With thanks and inspiration"),
    ("text", "The STAROS Projects community, the AAVSO Spectroscopy SIG,"),
    ("text", "the Society for Astronomical Sciences (SAS), the BASS Project,"),
    ("text", "Tom Field (RSpec), the French amateur spectroscopy community,"),
    ("text", "and many others."),
    ("gap", ""),
    ("gap", ""),
    ("text", "Thank you to everyone who shares their data, tools and knowledge"),
    ("text", "with the amateur spectroscopy community – to the stars and beyond!"),
)
BLOCKS = [  # (label, its text lines joined with single spaces), in order
    ("Stellar flux library", "Pickles, A. J. (1998). “A Stellar Spectral Flux Library: 1150–25000 Å.” Publications "
                             "of the Astronomical Society of the Pacific, 110, 863–878."),
    ("Reference star list", "Our own selection of bright B and A dwarfs, generated from SIMBAD, the AAVSO VSX and "
                            "the Washington Double Star Catalog"),
    ("Spectrum submission and variable star data",
     "AAVSO Spectroscopy Database (AVSpec) and Variable Star Index (VSX)"),
    ("Target lookup", "This application has made use of the SIMBAD database, operated at CDS, Strasbourg, France, "
                      "and of the VizieR catalogue access tool, CDS, Strasbourg, France."),
    ("Built with", "Python, PySide6 (Qt), NumPy, SciPy, Astropy, pyqtgraph"),
    ("With thanks and inspiration", "The STAROS Projects community, the AAVSO Spectroscopy SIG, the Society for "
     "Astronomical Sciences (SAS), the BASS Project, Tom Field (RSpec), the French amateur spectroscopy community, "
     "and many others."),
]
OLD = ("slit low res", "Francois Teyssier", "Reference star database", "Target resolution", "Teyssier", "Berardi",
       "Reference Star Finder", "subset, with amendments")
OLD_LIST_CREDIT = ("Teyssier", "Berardi", "Reference Star Finder", "subset, with amendments")


def _blocks(credits) -> list[tuple[str, str]]:
    out: list[tuple[str, list[str]]] = []
    for style, text in credits:
        if style == "label":
            out.append((text, []))
        elif style == "text" and out and not (style == "gap"):
            out[-1][1].append(text)
        elif style == "gap" and out and out[-1][1]:
            out.append(("", []))  # a block ends at its gap
    return [(label, " ".join(lines)) for label, lines in out if label]


def test_logo_click_opens_about(qtbot: QtBot, tmp_path: Path) -> None:
    w = MainWindow(session=Session.create(tmp_path / "s"))
    qtbot.addWidget(w)
    w.show()
    seen: list[AboutDialog] = []

    def close_it() -> None:
        dlg = w.about_dialog
        seen.append(dlg)
        dlg.accept()
    QTimer.singleShot(200, close_it)
    qtbot.mouseClick(w.logo, Qt.MouseButton.LeftButton)
    assert seen and seen[0].windowTitle() == "About Star ExTractor"


def test_credits_are_the_approved_text() -> None:
    assert tuple(CREDITS) == APPROVED  # every line, style and character (curly quotes, en dashes, ç)
    assert _blocks(CREDITS) == BLOCKS
    assert CREDITS[1] == ("sub", "Spectroscopy reduction for low-resolution slit spectrographs")
    assert [t for s, t in CREDITS if s == "text"][-2:] == [
        "Thank you to everyone who shares their data, tools and knowledge",
        "with the amateur spectroscopy community – to the stars and beyond!"]


def _scanned_files() -> list[Path]:
    files = [*ROOT.joinpath("starextractor").rglob("*.py"), *ROOT.joinpath("docs").glob("*.md"),
             *ROOT.joinpath("tools").glob("*.py"), *ROOT.joinpath("packaging").glob("*.*"),
             ROOT / "README.md", ROOT / "THIRD_PARTY_NOTICES.md", ROOT / "pyproject.toml"]
    return [f for f in files if f.is_file() and f.suffix in (".py", ".md", ".toml", ".iss", ".spec", ".ps1")]


def test_old_wording_is_gone_from_code_and_docs() -> None:
    hits = []
    for f in _scanned_files():
        text = f.read_text(encoding="utf-8")
        for old in OLD:
            if old.lower() in text.lower():
                hits.append(f"{f.relative_to(ROOT)}: {old}")
    assert not hits, hits


def test_generated_credits_match_the_roll() -> None:
    """README and THIRD_PARTY_NOTICES carry the roll as tools/notices.py writes it."""
    sys.path.insert(0, str(ROOT / "tools"))
    import notices

    block = notices.credits_markdown()
    for f in ("README.md", "THIRD_PARTY_NOTICES.md"):
        assert block.strip() in (ROOT / f).read_text(encoding="utf-8"), f


@pytest.mark.skipif(sys.platform != "win32", reason="measured with the Windows fonts")
def test_every_line_fits_the_roll() -> None:
    """QFontMetrics on the real platform (the offscreen one has no fonts): each line, in its own style, fits the
    roll's text area with 16 px to spare on each side. The About dialog has a fixed width (about.WIDTH), whatever
    the size of the main window."""
    code = ("import json, sys\nfrom PySide6.QtWidgets import QApplication\napp = QApplication(sys.argv[:1])\n"
            "from PySide6.QtGui import QFontMetrics\nfrom starextractor.ui import about, theme\n"
            "theme.apply(app, 'default')\n"
            "print(json.dumps([[t, QFontMetrics(about._font(s)).horizontalAdvance(t)] for s, t in about.CREDITS "
            "if t]))\n")
    env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORM",)}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, timeout=60,
                         check=True, encoding="utf-8")
    widths = json.loads(out.stdout.strip().splitlines()[-1])
    assert len(widths) == len([t for _s, t in CREDITS if t])
    too_wide = [(t, w) for t, w in widths if w > about.WIDTH - 32]
    assert not too_wide, too_wide
    assert min(w for _t, w in widths) > 40  # real metrics, not an empty font


def test_roll_moves_and_loops_with_the_longer_credits(qtbot: QtBot) -> None:
    assert SPLASH.exists()
    dlg = AboutDialog(animate=True)
    qtbot.addWidget(dlg)
    roll = dlg.roll
    assert isinstance(roll, CreditRoll) and roll.timer.isActive()
    assert roll.content_h == sum(about.LINE_H.get(s, 21) for s, _ in CREDITS)
    before = roll.offset
    qtbot.wait(200)
    assert roll.offset > before  # it rolls
    roll.timer.stop()
    steps, offsets = 0, []
    roll.offset = 0.0
    while True:  # one whole loop, one step at a time: every step is SPEED_PX until it wraps once
        roll._tick()
        steps += 1
        offsets.append(roll.offset)
        if roll.offset == 0.0:
            break
        assert steps < 10_000
    assert np.all(np.diff(offsets[:-1]) == about.SPEED_PX)
    assert offsets[-2] >= ROLL_H + roll.content_h  # the last line has left the top before it starts again


def test_reduced_motion_list_has_the_same_text(qtbot: QtBot) -> None:
    dlg = AboutDialog(animate=False)
    qtbot.addWidget(dlg)
    assert not isinstance(dlg.roll, CreditRoll)
    labels = [w.text() for w in dlg.roll.widget().findChildren(QLabel)]
    assert labels == [t for _s, t in CREDITS]
    dlg.show()
    qtbot.mouseClick(dlg, Qt.MouseButton.LeftButton, pos=QPointF(10, 10).toPoint())  # a click closes it
    assert not dlg.isVisible()


def _render(w) -> np.ndarray:
    img = QImage(w.width(), w.height(), QImage.Format.Format_RGB32)
    w.render(img)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())[:, : img.width() * 4]
    return a.reshape(img.height(), img.width(), 4)[..., [2, 1, 0]].astype(int)  # r, g, b


@pytest.mark.parametrize("palette", ["night", "red"])
def test_dark_room_palettes_render_the_roll(qtbot: QtBot, palette: str) -> None:
    theme.set_palette(palette, emit=False)
    try:
        roll = CreditRoll(animate=False)
        qtbot.addWidget(roll)
        roll.offset = ROLL_H / 2 + 60  # some lines in the middle of the view
        rgb = _render(roll)
        lit = rgb.sum(axis=2) > rgb.sum(axis=2).min() + 60
        assert lit.sum() > 500  # the text is drawn
        r, g, b = (rgb[..., i][lit] for i in range(3))
        assert np.all(r >= g) and np.all(g >= b)  # warm only: no green or blue light
    finally:
        theme.set_palette("default", emit=False)


def test_old_reference_list_credit_is_gone_from_user_facing_text() -> None:
    """The list is our own now: About, README, THIRD_PARTY_NOTICES, the Help guide and the Plan card never name the
    old one (CHANGELOG history is left as it was)."""
    from starextractor.ui.screens.plan import REF_INFO

    sources = {"About CREDITS": " ".join(t for _s, t in CREDITS), "Plan card (i)": REF_INFO}
    for f in ("README.md", "THIRD_PARTY_NOTICES.md", "starextractor/resources/help.md"):
        sources[f] = (ROOT / f).read_text(encoding="utf-8")
    hits = [f"{name}: {old}" for name, text in sources.items() for old in OLD_LIST_CREDIT
            if old.lower() in text.lower()]
    assert not hits, hits
