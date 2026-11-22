"""Prism logo: header widget, SVG sources, the multi-size .ico, the window icon."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from pytestqt.qtbot import QtBot

from starex_reduce.core.session import Session
from starex_reduce.ui import brand
from starex_reduce.ui.main_window import MainWindow

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


def _alpha_max(img) -> int:
    from PySide6.QtGui import QImage

    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    a = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(img.height(), -1)[:, 3::4].copy()
    return int(a.max())


def test_header_logo_is_28px_and_renders_at_1x_and_2x(qtbot: QtBot, tmp_path: Path) -> None:
    w = MainWindow(session=Session.create(tmp_path / "s"))
    qtbot.addWidget(w)
    logo = w.logo
    assert isinstance(logo, brand.Logo) and (logo.width(), logo.height()) == (28, 28)
    for dpr in (1.0, 2.0):
        pm = logo.pixmap(dpr)
        assert pm.width() == round(28 * dpr) and pm.devicePixelRatio() == dpr
        assert _alpha_max(pm.toImage()) > 0  # not all transparent


def test_svgs_load_and_ico_has_seven_sizes() -> None:
    from make_icon import SIZES, ico_sizes
    from PySide6.QtSvg import QSvgRenderer

    for svg in (brand.LOGO, brand.LOGO_SMALL):
        r = QSvgRenderer(str(svg))
        assert r.isValid() and r.viewBox().width() == 32
    assert brand.svg_for(16) == brand.svg_for(32) == brand.LOGO_SMALL and brand.svg_for(48) == brand.LOGO
    assert ico_sizes(brand.ICO) == list(SIZES) == [16, 24, 32, 48, 64, 128, 256]


def test_icon_has_a_dark_tile_for_light_taskbars() -> None:
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QIcon, QImage
    from PySide6.QtSvg import QSvgRenderer

    for svg in (brand.LOGO_ICON, brand.LOGO_ICON_SMALL):
        assert QSvgRenderer(str(svg)).isValid()
    assert brand.icon_svg_for(16) == brand.icon_svg_for(24) == brand.LOGO_ICON_SMALL
    assert brand.icon_svg_for(32) == brand.LOGO_ICON
    img = QIcon(str(brand.ICO)).pixmap(QSize(32, 32)).toImage().convertToFormat(QImage.Format.Format_ARGB32)
    assert img.size() == QSize(32, 32)
    corner = QColor(img.pixel(2, 2))
    assert img.pixelColor(2, 2).alpha() > 0  # the rounded tile reaches the corner region, not transparent
    edge = img.pixelColor(16, 3)  # tile background between the border and the prism, top centre
    assert edge.alpha() == 255 and max(edge.red(), edge.green(), edge.blue()) < 0x30, (corner.name(), edge.name())
    assert img.pixelColor(0, 0).alpha() < 255  # the very corner is rounded off


def test_window_icon_is_set(qtbot: QtBot, tmp_path: Path) -> None:
    w = MainWindow(session=Session.create(tmp_path / "s"))
    qtbot.addWidget(w)
    icon = w.windowIcon()
    assert not icon.isNull() and {s.width() for s in icon.availableSizes()} >= {16, 32, 48, 256}
