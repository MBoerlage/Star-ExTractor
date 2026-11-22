"""Sky A above the spectrum, Sky B below it, never overlapping: auto-placement, saved rows and dragging."""
from __future__ import annotations

import os

import numpy as np
import pytest

from starex_reduce.core import subs as sb
from starex_reduce.core.extract import Band, Bands, auto_place


def in_order(b: Bands) -> bool:
    return (b.sky_a.top <= b.sky_a.bottom < b.spectrum.top <= b.spectrum.bottom < b.sky_b.top <= b.sky_b.bottom)


def star_strip(h: int, row: float, fwhm: float, lit: tuple[int, int] | None = None, w: int = 400) -> np.ndarray:
    rng = np.random.default_rng(1)
    y = np.arange(h)[:, None]
    img = 300 + 5000 * np.exp(-0.5 * ((y - row) / (fwhm / 2.355)) ** 2) * np.ones((1, w))
    if lit is not None:  # sky light only inside the slit
        img += np.where((y >= lit[0]) & (y <= lit[1]), 200.0, 0.0)
    return (img + rng.normal(0, 3, img.shape)).astype(np.float32)


@pytest.mark.parametrize("h,row,fwhm,lit", [
    (400, 200, 6, None),
    (400, 4, 6, None),  # star at the top edge of the frame
    (400, 395, 6, None),  # and at the bottom
    (135, 67, 15.5, None),  # a short window around a wide star (×16)
    (400, 200, 15.5, (190, 215)),  # a "slit edge" inside the star's wings
    (400, 200, 6, (195, 206)),  # a very narrow lit slit
])
def test_auto_place_never_overlaps(h, row, fwhm, lit):
    b = auto_place(star_strip(h, row, fwhm, lit)).bands
    assert in_order(b), b
    assert 0 <= b.sky_a.top and b.sky_b.bottom <= h - 1


def test_ordered_mirrors_and_cuts():
    b = Bands(Band(100, 110), sky_a=Band(115, 130), sky_b=Band(105, 140)).ordered(400)
    assert b.sky_a == Band(80, 95)  # on the wrong side: mirrored about the spectrum
    assert b.sky_b == Band(111, 140)  # reaching into the spectrum: cut back
    assert in_order(b)
    ok = Bands(Band(100, 110), Band(60, 80), Band(130, 150))
    assert ok.ordered(400) == ok  # already in order: unchanged


def test_saved_overlapping_rows_are_put_in_order():
    r = sb.Rows(1000, 0.2, 1920, (-16, 16), (0, 1), (-1, 0))  # what a ×16 Re-detect once saved
    back = sb.Rows.from_json(r.to_json())
    assert back.sky_a[1] < back.spec[0] and back.spec[1] < back.sky_b[0]
    assert back.spec == (-16, 16)


@pytest.fixture
def overlay():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from starex_reduce.ui.widgets.bands import BandOverlay
    from starex_reduce.ui.widgets.plot_guard import pg

    QApplication.instance() or QApplication([])
    ov = BandOverlay(pg.ViewBox())
    ov.set_bands(Bands(Band(100, 110), Band(60, 80), Band(130, 150)), 400)
    return ov


def test_dragging_a_sky_whole_stops_at_the_spectrum(overlay):
    got = []
    overlay.moved.connect(got.append)
    overlay.regions["sky_a"].setRegion((90, 111))  # whole band moved 30 rows down, into the spectrum
    overlay.regions["sky_a"].sigRegionChangeFinished.emit(overlay.regions["sky_a"])
    b = got[-1]
    assert b.sky_a == Band(79, 99)  # stopped against the spectrum, size kept
    assert in_order(b)


def test_dragging_an_edge_stops_at_the_neighbour(overlay):
    overlay.regions["spectrum"].setRegion((100, 140))  # bottom edge dragged into Sky B
    b = overlay.bands()
    assert b.spectrum == Band(100, 129) and in_order(b)
    overlay.regions["sky_b"].setRegion((95, 151))  # top edge of Sky B dragged up through the spectrum
    b = overlay.bands()
    assert b.sky_b.top == 130 and in_order(b)
