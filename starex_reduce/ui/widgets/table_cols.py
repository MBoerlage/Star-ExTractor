"""Table columns that share the table's width in fixed proportions, never narrower than their header text."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QHeaderView, QTableWidget

PAD = 16  # the header's padding


class ProportionalColumns(QObject):
    def __init__(self, table: QTableWidget, weights: list[float], mins: dict[int, int] | None = None) -> None:
        """`mins`: a column's own minimum width (e.g. the widest chip it shows), above its header text."""
        super().__init__(table)
        self.table, self.weights, self.mins = table, weights, dict(mins or {})
        hh = table.horizontalHeader()
        hh.setStretchLastSection(False)
        for c in range(len(weights)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.viewport().installEventFilter(self)
        self.apply()

    def header_min(self, c: int) -> int:
        it = self.table.horizontalHeaderItem(c)
        fm = self.table.horizontalHeader().fontMetrics()
        return max(fm.horizontalAdvance(it.text() if it else "") + PAD, self.mins.get(c, 0))

    def widths(self, total: int) -> list[int]:
        mins = [self.header_min(c) for c in range(len(self.weights))]
        spare = max(total - sum(mins), 0)
        w = sum(self.weights)
        out = [m + int(spare * k / w) for m, k in zip(mins, self.weights, strict=True)]
        out[-1] += max(total - sum(out), 0)  # rounding: the last column takes the rest
        return out

    def minimum_width(self) -> int:
        return sum(self.header_min(c) for c in range(len(self.weights))) + 2 * self.table.frameWidth()

    def apply(self) -> None:
        for c, w in enumerate(self.widths(self.table.viewport().width())):
            self.table.setColumnWidth(c, w)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.apply()
        return False
