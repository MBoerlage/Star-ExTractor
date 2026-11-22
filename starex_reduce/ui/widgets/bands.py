"""Draggable extraction bands on the image viewer: Spectrum (solid outline), Sky A / Sky B (dashed outline).

Each band is a horizontal pyqtgraph LinearRegionItem (drag an edge or the whole band). Edges snap
to whole rows; the row number follows the cursor while dragging. Overlay items only: the image is
never redrawn.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor

from starex_reduce.core.extract import Band, Bands

from .. import theme
from .plot_guard import pg

NAMES = {"spectrum": "Spectrum", "sky_a": "Sky A", "sky_b": "Sky B"}


def _colour(hex_colour: str, alpha: int) -> QColor:
    c = QColor(hex_colour)
    c.setAlpha(alpha)
    return c


class BandOverlay(QObject):
    moving = Signal(object)  # Bands, while dragging
    moved = Signal(object)  # Bands, after release

    def __init__(self, vb: pg.ViewBox) -> None:
        super().__init__()
        self.vb = vb
        self.regions: dict[str, pg.LinearRegionItem] = {}
        self.labels: dict[str, pg.TextItem] = {}
        self._updating = False
        self._last: dict[str, tuple[float, float]] = {}
        for key in ("sky_a", "sky_b", "spectrum"):
            text = theme.SPECTRUM_TEXT if key == "spectrum" else theme.SKY_TEXT
            r = pg.LinearRegionItem(orientation="horizontal", swapMode="block")
            self._style_region(key, r)
            r.setZValue(5 if key == "spectrum" else 4)
            r.sigRegionChanged.connect(lambda _r, k=key: self._changed(k, final=False))
            r.sigRegionChangeFinished.connect(lambda _r, k=key: self._changed(k, final=True))
            vb.addItem(r, ignoreBounds=True)
            lab = pg.TextItem("", color=text, anchor=(0, 0))
            lab.setFont(theme.mono_font(11))
            lab.setZValue(6)
            vb.addItem(lab, ignoreBounds=True)
            self.regions[key], self.labels[key] = r, lab
        self.cursor = pg.TextItem("", color=theme.TEXT, anchor=(0.5, -0.4), border=pg.mkPen(theme.BORDER),
                                  fill=pg.mkBrush(theme.BG))
        self.cursor.setFont(theme.mono_font(11))
        self.cursor.setZValue(11)
        self.cursor.hide()
        vb.addItem(self.cursor, ignoreBounds=True)
        self.width = 1
        self.set_visible(False)
        theme.notifier.changed.connect(self.retheme)

    def set_visible(self, on: bool) -> None:
        for it in (*self.regions.values(), *self.labels.values()):
            it.setVisible(on)

    def set_bands(self, bands: Bands | None, width: int) -> None:
        self.width = width
        if bands is None:
            self.set_visible(False)
            return
        self._updating = True
        for key, band in (("spectrum", bands.spectrum), ("sky_a", bands.sky_a), ("sky_b", bands.sky_b)):
            self.regions[key].setRegion((band.top, band.bottom + 1))  # edges sit between rows
            self._last[key] = (band.top, band.bottom + 1)
        self._updating = False
        self._relabel()
        self.set_visible(True)

    def bands(self) -> Bands:
        def band(key: str) -> Band:
            lo, hi = self.regions[key].getRegion()
            top, bot = round(lo), round(hi) - 1
            return Band(top, max(top, bot))
        return Bands(band("spectrum"), band("sky_a"), band("sky_b"))

    def _relabel(self) -> None:
        b = self.bands()
        for key, band in (("spectrum", b.spectrum), ("sky_a", b.sky_a), ("sky_b", b.sky_b)):
            word = "rows " if key == "spectrum" else ""
            self.labels[key].setText(f"{NAMES[key]} · {word}{band.top}–{band.bottom}")
            self.labels[key].setPos(self.width * 0.01, band.top)

    def _changed(self, key: str, final: bool) -> None:
        if self._updating:
            return
        r = self.regions[key]
        lo, hi = self._keep_order(key, *r.getRegion())
        snapped = (round(lo), max(round(lo) + 1, round(hi)))
        if (lo, hi) != tuple(r.getRegion()) or (final and (snapped[0] != lo or snapped[1] != hi)):
            self._updating = True
            r.setRegion(snapped if final else (lo, hi))
            self._updating = False
        self._last[key] = tuple(r.getRegion())
        self._relabel()
        b = self.bands()
        if final:
            self.cursor.hide()
            self.moved.emit(b)
        else:
            moved_edge = snapped[1] - 1 if abs(hi - snapped[1]) < abs(lo - snapped[0]) + 1e-9 else snapped[0]
            self.cursor.setText(f"row {moved_edge}")
            self.cursor.setPos(self.width * 0.8, moved_edge)
            self.cursor.show()
            self.moving.emit(b)

    def _keep_order(self, key: str, lo: float, hi: float) -> tuple[float, float]:
        """Sky A stays above the spectrum and Sky B below it; bands may touch but never overlap. A band
        dragged as a whole stops at its neighbour (keeps its size); a dragged edge stops there."""
        reg = {k: self.regions[k].getRegion() for k in self.regions}
        floor, ceil = {"sky_a": (-1e9, reg["spectrum"][0]),
                       "spectrum": (reg["sky_a"][1], reg["sky_b"][0]),
                       "sky_b": (reg["spectrum"][1], 1e9)}[key]
        prev = self._last.get(key, (lo, hi))
        whole = abs((lo - prev[0]) - (hi - prev[1])) < 1e-6 and lo != prev[0]
        if whole:
            size = hi - lo
            lo = min(max(lo, floor), ceil - size)
            return lo, lo + size
        lo, hi = max(lo, floor), min(hi, ceil)
        if hi - lo < 1:  # an edge pushed onto the other edge
            lo, hi = (lo, lo + 1) if prev[0] == lo else (hi - 1, hi)
        return lo, hi

    @staticmethod
    def _style_region(key: str, r: pg.LinearRegionItem) -> None:
        """Fill and outline from the palette. Night / red: the sky bands get a dashed outline and the spectrum band
        a solid one, so they differ without colour; fills use the palette's band alphas."""
        spec = key == "spectrum"
        fill, text = (theme.SPECTRUM_FILL, theme.SPECTRUM_TEXT) if spec else (theme.SKY_FILL, theme.SKY_TEXT)
        if theme.is_dark_room():
            brush = theme.band_brush("spectrum" if spec else "sky")
        else:
            brush = _colour(fill, 70 if spec else 55)
        pen = pg.mkPen(_colour(text, 220), width=1)
        if not spec and theme.is_dark_room():
            pen.setStyle(Qt.PenStyle.DashLine)
        r.setBrush(brush)
        r.setHoverBrush(_colour(fill, 100))
        for line in r.lines:
            line.setPen(pen)
            line.setHoverPen(pg.mkPen(theme.TEXT, width=2))
        r.update()

    def retheme(self, *_a: object) -> None:
        import shiboken6

        if not all(shiboken6.isValid(r) for r in self.regions.values()):  # the view is gone: stop listening
            theme.notifier.changed.disconnect(self.retheme)
            return
        for key, r in self.regions.items():
            self._style_region(key, r)
            self.labels[key].setColor(theme.SPECTRUM_TEXT if key == "spectrum" else theme.SKY_TEXT)

    def strip_marks(self) -> list[tuple[float, float, str]]:
        b = self.bands()
        return [(b.sky_a.top, b.sky_a.bottom + 1, theme.SKY_FILL), (b.sky_b.top, b.sky_b.bottom + 1, theme.SKY_FILL),
                (b.spectrum.top, b.spectrum.bottom + 1, theme.SPECTRUM_FILL)]
