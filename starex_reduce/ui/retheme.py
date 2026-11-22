"""Live palette switch: re-style the app and translate every colour already baked into a widget or plot item.

Style sheets and QPalette come back from `theme.apply`. What was baked in at construction (a widget's own style
sheet, pyqtgraph pens and brushes, plot backgrounds) is translated through `theme.colour_map(old, new)`: the same
token in the new palette, alpha kept. Widgets with a `retheme()` method (image views, band overlays, previews) redo
their own part (colour maps, dashed outlines). Custom-painted widgets read the theme when they paint, so an
`update()` is enough. Nothing is recomputed: no pixel arrays, no stage.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget

from . import theme

# dark-room line styles for curves that differ only by colour in the default palette (item → style)
_DARK_STYLES: dict[int, tuple[Any, Any]] = {}


def dark_style(item: Any, style: Any, width: float | None = None) -> Any:
    """Give a plot curve a line style (and width) in night / red, its own pen in default. Returns the item."""
    import pyqtgraph as pg

    pen = pg.mkPen(item.opts["pen"])
    item._sx_default = (pen.style(), pen.widthF())
    item._sx_dark = (style, width if width is not None else pen.widthF())
    _apply_style(item)
    return item


def _apply_style(item: Any) -> None:
    import pyqtgraph as pg

    if not hasattr(item, "_sx_dark"):
        return
    style, width = item._sx_dark if theme.is_dark_room() else item._sx_default
    pen = pg.mkPen(item.opts["pen"])
    pen.setStyle(style)
    pen.setWidthF(width)
    item.setPen(pen)


def _pen(p: Any, mp: dict[str, str]):
    import pyqtgraph as pg

    if p is None:
        return None
    pen = pg.mkPen(p)
    pen.setColor(theme.translate_colour(pen.color(), mp))
    return pen


def _brush(b: Any, mp: dict[str, str]):
    import pyqtgraph as pg

    if b is None:
        return None
    br = pg.mkBrush(b)
    br.setColor(theme.translate_colour(br.color(), mp))
    return br


def _col(c: Any, mp: dict[str, str]) -> QColor:
    import pyqtgraph as pg

    return theme.translate_colour(pg.mkColor(c), mp)


def recolour_item(item: Any, mp: dict[str, str]) -> None:
    """One pyqtgraph item (or plain QGraphicsItem) through the colour map."""
    import pyqtgraph as pg
    from PySide6.QtWidgets import QAbstractGraphicsShapeItem

    if isinstance(item, pg.PlotDataItem):
        o = item.opts
        if o.get("pen") is not None:
            item.setPen(_pen(o["pen"], mp))
        if o.get("shadowPen") is not None:
            item.setShadowPen(_pen(o["shadowPen"], mp))
        if o.get("fillBrush") is not None:
            item.setFillBrush(_brush(o["fillBrush"], mp))
        if o.get("symbolPen") is not None:
            item.setSymbolPen(_pen(o["symbolPen"], mp))
        if o.get("symbolBrush") is not None:
            item.setSymbolBrush(_brush(o["symbolBrush"], mp))
        _apply_style(item)
    elif isinstance(item, pg.PlotCurveItem):
        if isinstance(item.parentItem(), pg.PlotDataItem):
            return  # follows its PlotDataItem
        item.setPen(_pen(item.opts["pen"], mp))
        if item.opts.get("brush") is not None:
            item.setBrush(_brush(item.opts["brush"], mp))
    elif isinstance(item, pg.ScatterPlotItem):
        if isinstance(item.parentItem(), pg.PlotDataItem):
            return
        item.setPen(_pen(item.opts["pen"], mp), update=False)
        item.setBrush(_brush(item.opts["brush"], mp), update=False)
        pens, brushes = item.data["pen"], item.data["brush"]
        for i in range(len(item.data)):  # per-spot colours
            if pens[i] is not None:
                pens[i] = _pen(pens[i], mp)
            if brushes[i] is not None:
                brushes[i] = _brush(brushes[i], mp)
        item.updateSpots()
    elif isinstance(item, pg.InfiniteLine):
        item.setPen(_pen(item.pen, mp))
        item.setHoverPen(_pen(item.hoverPen, mp))
    elif isinstance(item, pg.LinearRegionItem):
        item.setBrush(_brush(item.brush, mp))
        item.setHoverBrush(_brush(item.hoverBrush, mp))
    elif isinstance(item, pg.TextItem):
        item.setColor(_col(item.color, mp))
        if item.fill is not None:
            item.fill = _brush(item.fill, mp)
        if item.border is not None:
            item.border = _pen(item.border, mp)
        item.update()
    elif isinstance(item, pg.BarGraphItem):
        o = item.opts
        kw = {}
        for k in ("pen", "brush"):
            if o.get(k) is not None:
                kw[k] = (_pen if k == "pen" else _brush)(o[k], mp)
        if kw:
            item.setOpts(**kw)
    elif isinstance(item, pg.AxisItem):
        item.setPen(_pen(item.pen(), mp))
        item.setTextPen(_pen(item.textPen(), mp))
    elif isinstance(item, pg.LegendItem):
        item.setBrush(_brush(item.opts["brush"], mp))
        item.setPen(_pen(item.opts["pen"], mp))
        if item.opts.get("labelTextColor") is not None:
            item.setLabelTextColor(_col(item.opts["labelTextColor"], mp))
    elif isinstance(item, pg.LabelItem):
        if item.opts.get("color") is not None:
            item.setText(item.text, color=_col(item.opts["color"], mp))
    elif isinstance(item, QAbstractGraphicsShapeItem):
        item.setPen(_pen(item.pen(), mp))
        item.setBrush(_brush(item.brush(), mp))


def recolour_widget(w: QWidget, mp: dict[str, str]) -> None:
    import pyqtgraph as pg

    css = w.styleSheet()
    if css:
        new = theme.translate_css(css, mp)
        if new != css:
            w.setStyleSheet(new)
    if hasattr(w, "recolour") and callable(w.recolour):
        w.recolour(mp)  # widgets that keep a colour of their own (legend swatches)
    if isinstance(w, pg.GraphicsView):
        bg = w.backgroundBrush().color()
        w.setBackground(theme.translate_colour(bg, mp))
        for item in w.scene().items():
            recolour_item(item, mp)


def switch(name: str) -> bool:
    """Switch the whole app to palette `name` live. Returns False when it was already active."""
    import pyqtgraph as pg

    old = theme.palette
    if name == old:
        return False
    mp = theme.set_palette(name, emit=False)
    app = QApplication.instance()
    theme.apply(app)
    pg.setConfigOptions(background=theme.BG, foreground=theme.MUTED)
    for w in app.allWidgets():
        recolour_widget(w, mp)
    for w in app.allWidgets():
        if hasattr(w, "retheme") and callable(w.retheme):
            w.retheme()
    theme.notifier.changed.emit(old, name)
    for w in app.topLevelWidgets():
        if w.isVisible():
            theme.set_dark_title_bar(w, theme.is_dark_room())
        w.update()
        for c in w.findChildren(QWidget):
            c.update()
    return True
