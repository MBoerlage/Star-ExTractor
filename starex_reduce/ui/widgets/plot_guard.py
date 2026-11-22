"""Keeps wheel / right-drag zoom inside a usable range on every pyqtgraph plot.

pyqtgraph zooms without limit: zooming in long enough shrinks a view to ~1e-92 wide, then to zero, and the next
wheel step computes a NaN centre ("Cannot set range [nan, nan]"). Zooming out overflows the same way. The guard
refuses a zoom step that would leave the range non-finite, narrower than a millionth of its own position, or wider
than MAX_SPAN. Views set by the app (setRange, autoRange) are not affected.
"""
from __future__ import annotations

import math
from typing import Any

import pyqtgraph as pg

MAX_SPAN = 1e9
REL_MIN = 1e-6
ABS_MIN = 1e-9


def span_ok(lo: float, hi: float) -> bool:
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return False
    w = abs(hi - lo)
    return max(ABS_MIN, REL_MIN * max(abs(lo), abs(hi))) <= w <= MAX_SPAN


def _scale_allowed(vb: pg.ViewBox, s: Any, center: Any, x: Any, y: Any) -> bool:
    if s is not None:
        x, y = (s[0], s[1]) if hasattr(s, "__len__") else (s, s)
    rect = vb.targetRect()
    c = rect.center() if center is None else pg.Point(center)
    for factor, lo, hi, cc in ((x, rect.left(), rect.right(), c.x()), (y, rect.top(), rect.bottom(), c.y())):
        if factor is None:
            continue
        factor, cc = float(factor), float(cc)
        if not (math.isfinite(factor) and math.isfinite(cc)):
            return False
        if not span_ok(cc + (lo - cc) * factor, cc + (hi - cc) * factor):
            return False
    return True


def install() -> None:
    """Patch ViewBox.scaleBy once for the whole app (idempotent)."""
    if getattr(pg.ViewBox.scaleBy, "_zoom_guard", False):
        return
    original = pg.ViewBox.scaleBy

    def scale_by(self: pg.ViewBox, s: Any = None, center: Any = None, x: Any = None, y: Any = None) -> None:
        if _scale_allowed(self, s, center, x, y):
            original(self, s, center, x, y)

    scale_by._zoom_guard = True  # type: ignore[attr-defined]
    pg.ViewBox.scaleBy = scale_by


install()
