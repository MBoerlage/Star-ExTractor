"""A row of controls that wraps to the next line when it runs out of width (Qt's flow layout example, trimmed).
Each item keeps its size hint, so nothing is clipped; hidden widgets take no room. Used by the Plan tab's field rows
(docs/PLAN_TAB.md)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLayout, QLayoutItem, QWidget, QWidgetItem

KEEP = "flowKeepWithNext"


def keep_with_next(widget: QWidget) -> QWidget:
    """A label that never ends a line: it wraps together with the field after it."""
    widget.setProperty(KEEP, True)
    return widget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, hspacing: int = 8, vspacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h, self._v = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    # QLayout plumbing ----------------------------------------------------------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def sizeHint(self) -> QSize:
        """One line: everything side by side."""
        shown = [i.sizeHint() for i in self._items if not i.isEmpty()]
        w = sum(s.width() for s in shown) + self._h * max(len(shown) - 1, 0)
        h = max((s.height() for s in shown), default=0)
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:
        """The widest single item: below that something would clip."""
        size = QSize()
        for group in self.groups():
            w = sum(i.sizeHint().width() for i in group) + self._h * (len(group) - 1)
            size = size.expandedTo(QSize(w, max(i.sizeHint().height() for i in group)))
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # placing -------------------------------------------------------------------------------------------------------
    def groups(self) -> list[list[QLayoutItem]]:
        """Shown items, with every widget marked keep_with_next() bound to the item after it."""
        out: list[list[QLayoutItem]] = []
        bind = False
        for item in self._items:
            if item.isEmpty():
                continue
            if bind and out:
                out[-1].append(item)
            else:
                out.append([item])
            w = item.widget()
            bind = bool(w is not None and w.property(KEEP))
        return out

    def lines(self, width: int) -> list[list[QLayoutItem]]:
        """The items of each line at this width (for tests)."""
        out: list[list[QLayoutItem]] = [[]]
        x = 0
        for group in self.groups():
            w = sum(i.sizeHint().width() for i in group) + self._h * (len(group) - 1)
            if out[-1] and x + w > width:
                out.append([])
                x = 0
            out[-1].extend(group)
            x += w + self._h
        return [line for line in out if line]

    def _place(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        inner = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        y = inner.y()
        lines = self.lines(inner.width())
        for n, line in enumerate(lines):
            h = max(i.sizeHint().height() for i in line)
            x = inner.x()
            for item in line:
                s = item.sizeHint()
                if apply:
                    item.setGeometry(QRect(QPoint(x, y + (h - s.height()) // 2), s))
                x += s.width() + self._h
            y += h + (self._v if n < len(lines) - 1 else 0)
        return y - rect.y() + m.bottom()


def unit(*widgets: QWidget, spacing: int = 6) -> QWidget:
    """Widgets that stay together on one line of a flow (a label and its field)."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        lay.addWidget(w)
    return box


def add(flow: FlowLayout, widget: QWidget) -> QWidget:
    flow.addItem(QWidgetItem(widget))
    flow.addChildWidget(widget)
    return widget
