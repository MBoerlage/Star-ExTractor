"""A vertical scrollbar drawn in the window's right content margin, beside a scroll area, instead of inside it.

The app's tab area has the same 32 px margin left and right (main_window), and a scroll area's own scrollbar takes
room inside that, so the cards stop 10 px short and the right margin looks wider than the left. Here the scroll area
hides its own scrollbar (it still scrolls: the wheel, the keyboard and ensureWidgetVisible use it) and this one, a child
of the window, stands centred in the margin band to its right and follows it: range, page step and value both ways,
shown only while the area is visible and there is something to scroll. Used by the Plan tab (docs/PLAN_TAB.md)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QScrollBar, QWidget


class MarginScrollBar(QScrollBar):
    def __init__(self, area: QAbstractScrollArea) -> None:
        super().__init__(Qt.Orientation.Vertical)
        self.setObjectName("marginScrollBar")
        self.area = area
        self.inner = area.verticalScrollBar()
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner.rangeChanged.connect(self._sync_range)
        self.inner.valueChanged.connect(self._from_inner)
        self.valueChanged.connect(self._to_inner)
        self._watched: list[QObject] = []
        area.installEventFilter(self)
        self.hide()

    # ---- following the area ---------------------------------------------------------------------------------------
    def _attach(self) -> None:
        """Become a child of the window once the area is in one; watch everything that moves the area."""
        win = self.area.window()
        if win is self.area or self.parentWidget() is win:
            return
        self.setParent(win)
        for w in self._watched:
            w.removeEventFilter(self)
        self._watched = [win]
        p = self.area.parentWidget()
        while p is not None and p is not win:
            self._watched.append(p)
            p = p.parentWidget()
        for w in self._watched:
            w.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.Hide,
                            QEvent.Type.ParentChange):
            self.place()
        return False

    def place(self) -> None:
        self._attach()
        win = self.parentWidget()
        if win is None:
            return
        a = self.area
        top_left = a.mapTo(win, QPoint(0, 0))
        band = win.width() - (top_left.x() + a.width())  # from the area's right edge to the window's
        w = self.sizeHint().width()
        x = top_left.x() + a.width() + max((band - w) // 2, 0)
        self.setGeometry(x, top_left.y(), w, a.height())
        self._update_visible()

    def _update_visible(self) -> None:
        want = self.area.isVisibleTo(self.area.window()) and self.area.isVisible() and self.inner.maximum() > 0
        if want != self.isVisible():
            self.setVisible(want)
        if want:
            self.raise_()

    # ---- range and value, both ways -------------------------------------------------------------------------------
    def _sync_range(self, lo: int, hi: int) -> None:
        self.setRange(lo, hi)
        self.setPageStep(self.inner.pageStep())
        self.setSingleStep(self.inner.singleStep())
        self.setValue(self.inner.value())
        self.place()

    def _from_inner(self, v: int) -> None:
        if self.value() != v:
            self.setValue(v)

    def _to_inner(self, v: int) -> None:
        if self.inner.value() != v:
            self.inner.setValue(v)

    def overlaps(self, widget: QWidget) -> bool:
        """Whether this bar covers any part of `widget` (tests)."""
        if not self.isVisible() or self.parentWidget() is None:
            return False
        tl = widget.mapTo(self.parentWidget(), QPoint(0, 0))
        return self.geometry().intersects(widget.rect().translated(tl))
