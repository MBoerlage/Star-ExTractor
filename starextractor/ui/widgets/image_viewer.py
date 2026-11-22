"""2D spectrum viewer: Y-only zoom/pan, row ruler, frame strip with a draggable window, cursor
readout, Original | Corrected swap and a draw-a-line mode. Views never touch pixel data: they swap
pre-stretched uint8 images (core.display) and change ViewBox ranges."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from starextractor.core.display import DisplayImage

from .. import theme
from .plot_guard import pg

pg.setConfigOptions(imageAxisOrder="row-major", antialias=False, background=theme.BG, foreground=theme.MUTED)
STRIP_W = 50
FIT_HALF_ROWS = 60


class DrawViewBox(pg.ViewBox):
    """ViewBox that pans/zooms Y only, and in draw mode turns a left drag into a line."""

    line_moved = Signal(float, float, float, float)
    line_done = Signal(float, float, float, float)

    def __init__(self) -> None:
        super().__init__(invertY=True, enableMenu=False)
        self.setMouseEnabled(x=False, y=True)
        self.draw_mode = False
        self._start: QPointF | None = None

    def mouseDragEvent(self, ev, axis=None) -> None:
        if not self.draw_mode or ev.button() != Qt.MouseButton.LeftButton:
            super().mouseDragEvent(ev, axis)
            return
        ev.accept()
        pos = self.mapSceneToView(ev.scenePos())
        if ev.isStart():
            self._start = self.mapSceneToView(ev.buttonDownScenePos())
        if self._start is None:
            return
        args = (self._start.x(), self._start.y(), pos.x(), pos.y())
        if ev.isFinish():
            self._start = None
            self.line_done.emit(*args)
        else:
            self.line_moved.emit(*args)


class FrameStrip(QWidget):
    """Whole-frame thumbnail with an amber window showing the visible rows; drag to move,
    wheel to zoom. Pure painting: the thumbnail is computed in a worker."""

    window_moved = Signal(float)  # new centre row
    zoom = Signal(float)  # factor > 1 zooms in

    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(STRIP_W)
        self.setMinimumHeight(80)
        self.image: QImage | None = None
        self.rows = 1
        self.window = (0.0, 1.0)
        self.marks: list[tuple[float, float, str]] = []  # (top, bottom, colour) e.g. bands
        self._drag_offset: float | None = None
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("Drag the amber window to move up and down; roll the wheel to zoom")

    def set_image(self, thumb: np.ndarray | None, rows: int) -> None:
        if thumb is None:
            self.image = None
        else:
            t = np.ascontiguousarray(thumb)
            self.image = QImage(t.data, t.shape[1], t.shape[0], t.strides[0], QImage.Format.Format_Grayscale8).copy()
        self.rows = max(rows, 1)
        self.update()

    def _shown(self) -> QImage:
        """The thumbnail through the palette's colour map (cached per palette)."""
        key = (theme.palette, id(self.image))
        if getattr(self, "_tint_key", None) != key:
            self._tint_key, self._tint = key, theme.tinted(self.image)
        return self._tint

    def set_window(self, top: float, bottom: float) -> None:
        self.window = (top, bottom)
        self.update()

    def _y(self, row: float) -> float:
        return row / self.rows * (self.height() - 2) + 1

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.fillRect(r, QColor(theme.BG))
        if self.image is not None:
            p.drawImage(r.adjusted(1, 1, -1, -1), self._shown())
        for top, bot, colour in self.marks:
            c = QColor(colour)
            c.setAlpha(110)
            p.fillRect(QRectF(1, self._y(top), self.width() - 2, max(1.0, self._y(bot) - self._y(top))), c)
        p.setPen(QPen(QColor(theme.BORDER)))
        p.drawRoundedRect(r, 4, 4)
        y0, y1 = self._y(self.window[0]), self._y(self.window[1])
        win = QRectF(1.5, y0, self.width() - 3, max(6.0, y1 - y0))
        amber = QColor(theme.AMBER)
        fill = QColor(amber)
        fill.setAlpha(50)
        p.fillRect(win, fill)
        p.setPen(QPen(amber, 2))
        p.drawRect(win)
        p.setBrush(amber)
        for gy in (win.top(), win.bottom()):  # grip tabs
            p.drawRoundedRect(QRectF(win.center().x() - 9, gy - 3, 18, 6), 2, 2)
        p.end()

    def _row_at(self, y: float) -> float:
        return (y - 1) / max(self.height() - 2, 1) * self.rows

    def mousePressEvent(self, e: QMouseEvent) -> None:
        row = self._row_at(e.position().y())
        top, bot = self.window
        centre = (top + bot) / 2
        self._drag_offset = row - centre if top <= row <= bot else 0.0
        self.window_moved.emit(row - self._drag_offset)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self.window_moved.emit(self._row_at(e.position().y()) - self._drag_offset)

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:
        self._drag_offset = None

    def wheelEvent(self, e: QWheelEvent) -> None:
        self.zoom.emit(1.25 if e.angleDelta().y() > 0 else 0.8)


class ImageViewer(QWidget):
    """Frame strip + image with row ruler. Y zoom/pan only; X always shows the full width."""

    readout = Signal(str)
    rows_changed = Signal(int, int, int)  # top, bottom, total
    line_moved = Signal(float, float, float, float)
    line_drawn = Signal(float, float, float, float)

    def __init__(self, strip: bool = True, ruler: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.images: dict[str, DisplayImage | None] = {"orig": None, "corr": None}
        self.which = "orig"
        self.shape = (1, 1)
        self.vb = DrawViewBox()
        self.plot = pg.PlotWidget(viewBox=self.vb, enableMenu=False)
        self.plot.setBackground(theme.BG)
        pi = self.plot.getPlotItem()
        pi.hideAxis("bottom")
        pi.hideButtons()
        if ruler:
            ax = pi.getAxis("left")
            ax.setWidth(48)
            ax.setPen(pg.mkPen(theme.BORDER))
            ax.setTextPen(pg.mkPen(theme.MUTED))
            ax.setStyle(tickFont=theme.mono_font(11), tickLength=-6)
        else:
            pi.hideAxis("left")
        # Pre-rendered layers: (orig|corr) × (full|low). Zoom picks full or low, the toggle picks
        # orig or corr; both only change visibility, so no image is re-rendered while viewing.
        self.layers: dict[tuple[str, str], pg.ImageItem] = {}
        for which in ("orig", "corr"):
            for level in ("full", "low"):
                it = pg.ImageItem(axisOrder="row-major")
                it.setAutoDownsample(False)
                it.setVisible(False)
                self.vb.addItem(it)
                self.layers[(which, level)] = it
        self.item = self.layers[("orig", "full")]
        self.level = "full"
        self.vb.sigYRangeChanged.connect(self._range_changed)
        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)
        self.vb.line_moved.connect(self.line_moved)
        self.vb.line_done.connect(self.line_drawn)

        self.strip = FrameStrip() if strip else None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        if self.strip is not None:
            self.strip.window_moved.connect(self.centre_on)
            self.strip.zoom.connect(self.zoom_by)
            lay.addWidget(self.strip)
        lay.addWidget(self.plot, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ---- data ------------------------------------------------------------------------------------
    def set_images(self, original: DisplayImage | None, corrected: DisplayImage | None) -> None:
        first = self.images["orig"] is None and self.images["corr"] is None
        old_shape = self.shape
        self.images = {"orig": original, "corr": corrected}
        if self.which == "corr" and corrected is None:
            self.which = "orig"
        self._load_layers()
        self._show()
        if first or self.shape != old_shape:
            self.set_zoom("full")

    def retheme(self) -> None:
        """The palette changed: the same pixels through the new colour map (grey, or black → accent)."""
        lut = theme.image_lut()
        for it in self.layers.values():
            it.setLookupTable(lut)
        if self.strip is not None:
            self.strip.update()

    def show_corrected(self, on: bool) -> None:
        self.which = "corr" if on and self.images["corr"] is not None else "orig"
        self._show()

    @property
    def current(self) -> DisplayImage | None:
        return self.images[self.which] or self.images["orig"] or self.images["corr"]

    def _load_layers(self) -> None:
        for which in ("orig", "corr"):
            d = self.images[which]
            for level in ("full", "low"):
                it = self.layers[(which, level)]
                if d is None:
                    it.clear()
                    continue
                data = d.u8 if level == "full" or d.low is None else d.low
                it.setImage(data, autoLevels=False, levels=(0, 255), lut=theme.image_lut())
                h, w = d.shape
                it.setRect(QRectF(0, 0, w, h))

    def _pick_layer(self) -> None:
        which = self.which if self.images[self.which] is not None else ("orig" if self.images["orig"] else "corr")
        y0, y1 = self.y_range()
        rows_per_px = (y1 - y0) / max(self.vb.height(), 1.0)
        self.level = "low" if rows_per_px > 2.5 else "full"
        for key, it in self.layers.items():
            it.setVisible(self.images[key[0]] is not None and key == (which, self.level))
        self.item = self.layers[(which, self.level)]

    def _show(self) -> None:
        d = self.current
        if d is None:
            for it in self.layers.values():
                it.setVisible(False)
            if self.strip is not None:
                self.strip.set_image(None, 1)
            return
        self.shape = d.shape
        h, w = d.shape
        self.vb.setLimits(xMin=0, xMax=w, yMin=0, yMax=h, minYRange=8, maxYRange=h)
        self.vb.setXRange(0, w, padding=0)
        if self.strip is not None:
            self.strip.set_image(d.strip, h)
        self._range_changed()

    # ---- zoom ------------------------------------------------------------------------------------
    def y_range(self) -> tuple[float, float]:
        (_, _), (y0, y1) = self.vb.viewRange()
        return y0, y1

    def set_rows(self, top: float, bottom: float) -> None:
        h = self.shape[0]
        span = min(max(bottom - top, 8), h)
        top = min(max(0.0, top), h - span)
        self.vb.setYRange(top, top + span, padding=0)

    def centre_on(self, row: float) -> None:
        y0, y1 = self.y_range()
        half = (y1 - y0) / 2
        self.set_rows(row - half, row + half)

    def zoom_by(self, factor: float) -> None:
        y0, y1 = self.y_range()
        c, half = (y0 + y1) / 2, (y1 - y0) / 2 / factor
        self.set_rows(c - half, c + half)

    def set_zoom(self, mode: str | int) -> None:
        """'full', 'fit' (spectrum ± 60 rows) or an exaggeration N: rows shown N× taller than
        columns (×1 = true aspect)."""
        d = self.current
        if d is None:
            return
        h, w = d.shape
        if mode == "full":
            self.set_rows(0, h)
        elif mode == "fit":
            self.set_rows(d.spectrum_row - FIT_HALF_ROWS, d.spectrum_row + FIT_HALF_ROWS)
        else:
            vw, vh = max(self.vb.width(), 1.0), max(self.vb.height(), 1.0)
            rows = vh / (int(mode) * vw / w)
            y0, y1 = self.y_range()
            c = (y0 + y1) / 2 if y1 - y0 < h else d.spectrum_row
            self.set_rows(c - rows / 2, c + rows / 2)

    def _range_changed(self, *_a) -> None:
        self._pick_layer()
        y0, y1 = self.y_range()
        if self.strip is not None:
            self.strip.set_window(y0, y1)
        self.rows_changed.emit(int(max(0, y0)), int(min(self.shape[0], y1)), self.shape[0])

    # ---- cursor and drawing ----------------------------------------------------------------------
    def _mouse_moved(self, pos: QPointF) -> None:
        d = self.current
        if d is None or not self.vb.sceneBoundingRect().contains(pos):
            return
        p = self.vb.mapSceneToView(pos)
        x, y = math.floor(p.x()), math.floor(p.y())
        h, w = d.shape
        if 0 <= x < w and 0 <= y < h:
            v = float(d.raw[y, x])
            self.readout.emit(f"X {x} · Y {y} · {v:,.0f} ADU".replace(",", " "))

    def set_draw_mode(self, on: bool) -> None:
        self.vb.draw_mode = on
        self.plot.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)


class DrawnLine:
    """Amber line with end dots and a label, as in the mockup (overlay items, no redraw)."""

    def __init__(self, vb: pg.ViewBox) -> None:
        amber = QColor(theme.AMBER)
        self.curve = pg.PlotCurveItem(pen=pg.mkPen(amber, width=2))
        self.dots = pg.ScatterPlotItem(size=11, brush=pg.mkBrush(theme.BG), pen=pg.mkPen(amber, width=2))
        self.label = pg.TextItem("", color=theme.AMBER, anchor=(0.5, 1.4), border=pg.mkPen(amber),
                                 fill=pg.mkBrush(theme.BG))
        self.label.setFont(theme.mono_font(11))
        for it in (self.curve, self.dots, self.label):
            it.setZValue(10)
            vb.addItem(it, ignoreBounds=True)
        self.hide()

    def set(self, x0: float, y0: float, x1: float, y1: float, text: str = "") -> None:
        self.curve.setData([x0, x1], [y0, y1])
        self.dots.setData([x0, x1], [y0, y1])
        self.label.setText(text)
        self.label.setPos((x0 + x1) / 2, min(y0, y1))
        for it in (self.curve, self.dots, self.label):
            it.setVisible(True)
        self.label.setVisible(bool(text))

    def hide(self) -> None:
        for it in (self.curve, self.dots, self.label):
            it.setVisible(False)


class PointsOverlay:
    """Detected spectrum centres (teal dots) and the fitted line."""

    def __init__(self, vb: pg.ViewBox) -> None:
        self.dots = pg.ScatterPlotItem(size=7, brush=pg.mkBrush(theme.TEAL), pen=pg.mkPen(None))
        self.dots.setZValue(9)
        vb.addItem(self.dots, ignoreBounds=True)

    def set(self, points: list[tuple[float, float]]) -> None:
        if points:
            xs, ys = zip(*points, strict=True)
            self.dots.setData(list(xs), list(ys))
            self.dots.setVisible(True)
        else:
            self.dots.setVisible(False)
