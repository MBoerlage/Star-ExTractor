"""About: opens from the logo, shows the splash and the credits; the roll moves and loops."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt, QTimer
from pytestqt.qtbot import QtBot

from starex_reduce.core.session import Session
from starex_reduce.ui.about import CREDITS, SPLASH, AboutDialog, CreditRoll
from starex_reduce.ui.main_window import MainWindow


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


def test_splash_and_credits(qtbot: QtBot) -> None:
    assert SPLASH.exists()
    dlg = AboutDialog(animate=True)
    qtbot.addWidget(dlg)
    texts = " ".join(t for _s, t in CREDITS)
    for must in ("Pickles, A. J. (1998)", "Francois Teyssier and Paolo Berardi", "AVSpec", "SIMBAD database",
                 "Strasbourg", "PySide6 (Qt), NumPy, SciPy, Astropy, pyqtgraph", "to the stars and beyond!"):
        assert must in texts
    roll = dlg.roll
    assert isinstance(roll, CreditRoll) and roll.timer.isActive()
    before = roll.offset
    qtbot.wait(200)
    assert roll.offset > before  # it rolls
    roll.offset = roll.content_h + roll.height() + 1
    roll._tick()
    assert roll.offset == 0.0  # and loops


def test_reduced_motion_is_a_still_list(qtbot: QtBot) -> None:
    dlg = AboutDialog(animate=False)
    qtbot.addWidget(dlg)
    assert not isinstance(dlg.roll, CreditRoll)
    dlg.show()
    qtbot.mouseClick(dlg, Qt.MouseButton.LeftButton, pos=QPointF(10, 10).toPoint())  # a click closes it
    assert not dlg.isVisible()
