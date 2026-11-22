"""The workflow rail under the top row: three groups (Acquisition, Reduce, Export) of chevron steps, all nine
visible at once, plus the legend line. One custom-painted widget fed by a small model; the main window sets the
states from the same sources the old ribbons used, and a state change only repaints."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolTip, QWidget

from . import theme

NOTCH = 10.0  # chevron point depth
OVERLAP = 6.0  # a step starts this far inside the previous one's point (they interlock)
GROUP_GAP = 18.0
HEADER_H = 22.0
RULE_H = 2.0
STEP_H = 34.0
HEIGHT = int(HEADER_H + RULE_H + 6 + STEP_H + 2)
NARROW_W = 1100  # below this the long labels give way to the short ones
ACQ_WEIGHT = 1.35  # the Acquisition group is a little wider than one step so its label fits


class StepState(StrEnum):
    DONE = "done"
    CURRENT = "current"
    MISSING = "missing"
    NOT_RUN = "notrun"


@dataclass
class RailStep:
    id: str
    group: str
    label: str
    short: str
    tip: str  # what the step does (the old ribbon subtitle)
    state: StepState = StepState.NOT_RUN  # done | missing | notrun (current is the location, kept apart)
    status: str = "Not run"  # the old second line: now in the tooltip


@dataclass
class RailGroup:
    id: str
    title: str
    steps: list[str]
    progress: bool  # show "N of M done"


GROUPS = (RailGroup("acquisition", "Acquisition", ["acquisition"], False),
          RailGroup("reduce", "Reduce", ["select", "geometry", "extract", "wavelength", "response"], True),
          RailGroup("export", "Export", ["collect", "test", "publish"], True))


def default_steps() -> list[RailStep]:
    return [
        RailStep("acquisition", "acquisition", "Capture, stack", "Capture",
                 "Watch the capture folder, check each sub, stack the target and the reference"),
        RailStep("select", "reduce", "Select", "Select", "Pick the stacked target and the stacked reference star"),
        RailStep("geometry", "reduce", "Geometry", "Geometry", "Flip, tilt, hot pixels"),
        RailStep("extract", "reduce", "1D Extract", "Extract", "Bands, 1D profile"),
        RailStep("wavelength", "reduce", "Wavelength", "Wavelength", "Balmer auto-fit, R value"),
        RailStep("response", "reduce", "Response", "Response", "Response, normalise, crop"),
        RailStep("collect", "export", "Collect", "Collect", "Spectrum, object, observer"),
        RailStep("test", "export", "Test", "Test", "AAVSO AVSpec checks"),
        RailStep("publish", "export", "Publish", "Publish", "Save the file and the log"),
    ]


class Rail(QWidget):
    step_clicked = Signal(str)  # a step id
    group_clicked = Signal(str)  # a group id
    painted = Signal()  # after every paint; the main window uses the first one

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.steps = default_steps()
        self.by_id = {s.id: s for s in self.steps}
        self.current: str | None = None
        self._hover = -1
        self._focus = 0
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._update_accessible()

    def sizeHint(self) -> QSize:
        return QSize(1200, HEIGHT)

    # ---- model -----------------------------------------------------------------------------------------------
    def set_step(self, sid: str, *, state: StepState | None = None, status: str | None = None) -> None:
        s = self.by_id[sid]
        if state is not None:
            s.state = StepState.NOT_RUN if state == StepState.CURRENT else state
        if status is not None:
            s.status = status
        self._update_accessible()
        self.update()  # a repaint only: no layout work

    def set_current(self, sid: str | None) -> None:
        if sid != self.current:
            self.current = sid
            if sid in self.by_id:
                self._focus = self.steps.index(self.by_id[sid])
            self._update_accessible()
            self.update()

    def shown_state(self, sid: str) -> StepState:
        """What a step looks like: current wins unless the step has missing data."""
        s = self.by_id[sid]
        if sid == self.current and s.state != StepState.MISSING:
            return StepState.CURRENT
        return s.state

    def progress_text(self, group: RailGroup) -> str:
        if not group.progress:
            return ""
        n = sum(self.by_id[s].state == StepState.DONE for s in group.steps)
        return f"{n} of {len(group.steps)} done" if group.id == "reduce" else f"{n} of {len(group.steps)}"

    def label_of(self, s: RailStep) -> str:
        return s.short if self.width() < NARROW_W else s.label

    def font_px(self) -> int:
        """12 px, 11 px on a narrow window."""
        return 11 if self.width() < NARROW_W + 200 else 12

    def _text_box(self, i: int, r: QRectF, first: bool) -> QRectF:
        left = r.left() + (8 if first else NOTCH + 5)
        return QRectF(left, r.top(), r.right() - NOTCH - 2 - left, r.height())

    def _update_accessible(self) -> None:
        f = self.steps[self._focus]
        self.setAccessibleName(f"Workflow step {f.label}, {self.shown_state(f.id).value}")
        self.setAccessibleDescription("; ".join(f"{s.label}: {self.shown_state(s.id).value}" for s in self.steps))

    # ---- geometry --------------------------------------------------------------------------------------------
    def _group_rects(self) -> list[tuple[RailGroup, QRectF]]:
        weights = [ACQ_WEIGHT if g.id == "acquisition" else len(g.steps) for g in GROUPS]
        avail = self.width() - GROUP_GAP * (len(GROUPS) - 1)
        x, out = 0.0, []
        for g, wt in zip(GROUPS, weights, strict=True):
            w = avail * wt / sum(weights)
            out.append((g, QRectF(x, 0, w, HEIGHT)))
            x += w + GROUP_GAP
        return out

    def _step_rects(self) -> list[QRectF]:
        rects: list[QRectF] = []
        top = HEADER_H + RULE_H + 6
        for g, gr in self._group_rects():
            n = len(g.steps)
            w = (gr.width() + OVERLAP * (n - 1)) / n
            for i in range(n):
                rects.append(QRectF(gr.left() + i * (w - OVERLAP), top, w, STEP_H))
        return rects

    def _path(self, r: QRectF, first: bool) -> QPainterPath:
        p = QPainterPath()
        p.moveTo(r.left(), r.top())
        p.lineTo(r.right() - NOTCH, r.top())
        p.lineTo(r.right(), r.center().y())
        p.lineTo(r.right() - NOTCH, r.bottom())
        p.lineTo(r.left(), r.bottom())
        if not first:
            p.lineTo(r.left() + NOTCH, r.center().y())
        p.closeSubpath()
        return p

    def _firsts(self) -> list[bool]:
        out = []
        for g in GROUPS:
            out += [i == 0 for i in range(len(g.steps))]
        return out

    def step_at(self, pos: QPointF) -> int:
        firsts = self._firsts()
        for i in reversed(range(len(self.steps))):  # the later step is on top where they overlap
            if self._path(self._step_rects()[i], firsts[i]).contains(pos):
                return i
        return -1

    def group_at(self, pos: QPointF) -> str | None:
        for g, r in self._group_rects():
            if r.left() <= pos.x() <= r.right() and pos.y() < HEADER_H + RULE_H + 4:
                return g.id
        return None

    # ---- painting --------------------------------------------------------------------------------------------
    def paintEvent(self, _e: QEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        head_f = theme.ui_font(11, QFont.Weight.DemiBold)
        prog_f = theme.ui_font(11)
        cur_group = self.by_id[self.current].group if self.current in self.by_id else None
        for g, r in self._group_rects():
            p.fillRect(QRectF(r.left(), HEADER_H, r.width(), RULE_H),
                       QColor(theme.AMBER if g.id == cur_group else theme.LINE))
            self._text(p, head_f, theme.TEXT if g.id == cur_group else theme.MUTED,
                       QRectF(r.left(), 2, r.width() * 0.6, HEADER_H - 4), g.title.upper(), Qt.AlignmentFlag.AlignLeft)
            self._text(p, prog_f, theme.MUTED, QRectF(r.left() + r.width() * 0.4, 2, r.width() * 0.6, HEADER_H - 4),
                       self.progress_text(g), Qt.AlignmentFlag.AlignRight)
        firsts = self._firsts()
        for i, (s, r) in enumerate(zip(self.steps, self._step_rects(), strict=True)):
            state = self.shown_state(s.id)
            path = self._path(r, firsts[i])
            fill, text_c, weight = {
                StepState.CURRENT: (theme.AMBER, theme.AMBER_TEXT, QFont.Weight.DemiBold),
                StepState.DONE: (theme.DONE_BG, theme.DONE_TEXT, QFont.Weight.Normal),
                StepState.MISSING: (theme.NOTRUN_BG, theme.TEXT, QFont.Weight.Normal),
                StepState.NOT_RUN: (theme.NOTRUN_BG, theme.NOTRUN_TEXT, QFont.Weight.Normal),
            }[state]
            c = QColor(fill)
            if i == self._hover:
                c = c.lighter(115)
            p.fillPath(path, c)
            p.setPen(QPen(QColor(theme.BG), 1.5))  # a thin dark seam where the chevrons interlock
            p.drawPath(path)
            if state == StepState.MISSING:
                p.strokePath(path, QPen(QColor(theme.MISSING), 1.5))
            if self.hasFocus() and i == self._focus:
                pen = QPen(QColor(theme.TEXT), 1.2, Qt.PenStyle.DashLine)
                p.strokePath(path, pen)
            box = self._text_box(i, r, firsts[i])
            mark = {StepState.DONE: "✓ "}
            if theme.is_dark_room():  # night / red: missing data also by its glyph, not only the red outline
                mark[StepState.MISSING] = "! "
            name = mark.get(state, "") + self.label_of(s)
            self._text(p, theme.ui_font(self.font_px(), weight), text_c, box, name, Qt.AlignmentFlag.AlignLeft)
        p.end()
        self.painted.emit()

    @staticmethod
    def _text(p: QPainter, font: QFont, colour: str, box: QRectF, text: str, align: Qt.AlignmentFlag) -> None:
        p.setFont(font)
        p.setPen(QColor(colour))
        fm = QFontMetrics(font)
        p.drawText(box, int(align | Qt.AlignmentFlag.AlignVCenter),
                   fm.elidedText(text, Qt.TextElideMode.ElideRight, int(max(box.width(), 0))))

    def elided(self, i: int) -> bool:
        """True when the step's label does not fit its chevron (it is elided; the tooltip has it whole)."""
        width = self._text_box(i, self._step_rects()[i], self._firsts()[i]).width()
        s = self.steps[i]
        text = ("✓ " if self.shown_state(s.id) == StepState.DONE else "") + self.label_of(s)
        weight = QFont.Weight.DemiBold if self.shown_state(s.id) == StepState.CURRENT else QFont.Weight.Normal
        return QFontMetrics(theme.ui_font(self.font_px(), weight)).horizontalAdvance(text) > width

    def tooltip_of(self, i: int) -> str:
        s = self.steps[i]
        return f"{s.label} · {self.shown_state(s.id).value.replace('notrun', 'not run')}\n{s.tip}\n{s.status}"

    # ---- interaction -----------------------------------------------------------------------------------------
    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.ToolTip:
            i = self.step_at(QPointF(e.pos()))
            if i >= 0:
                QToolTip.showText(e.globalPos(), self.tooltip_of(i), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        i = self.step_at(e.position())
        over = i >= 0 or self.group_at(e.position()) is not None
        if i != self._hover:
            self._hover = i
            self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, _e: QEvent) -> None:
        self._hover = -1
        self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        i = self.step_at(e.position())
        if i >= 0:
            self._focus = i
            self.step_clicked.emit(self.steps[i].id)
            return
        g = self.group_at(e.position())
        if g is not None:
            self.group_clicked.emit(g)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._focus = (self._focus + (-1 if e.key() == Qt.Key.Key_Left else 1)) % len(self.steps)
            self._update_accessible()
            self.update()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.step_clicked.emit(self.steps[self._focus].id)
        else:
            super().keyPressEvent(e)


class _Swatch(QWidget):
    def __init__(self, fill: str, outline: str) -> None:
        super().__init__()
        self.fill, self.outline = fill, outline  # token names: the swatch follows the palette
        self.setFixedSize(10, 10)

    def paintEvent(self, _e: QEvent) -> None:
        p = QPainter(self)
        p.setPen(QPen(QColor(getattr(theme, self.outline)), 1))
        p.setBrush(QColor(getattr(theme, self.fill)))
        p.drawRect(QRectF(0.5, 0.5, 9, 9))
        p.end()


def legend_line() -> QWidget:
    """The one muted legend line under the rail (Reduce and Export pages), right aligned."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 4, 0, 0)
    lay.setSpacing(6)
    lay.addStretch(1)
    marks = (("DONE_BG", "DONE_TEXT", "Done"), ("AMBER", "AMBER", "Current"),
             ("NOTRUN_BG", "MISSING", "Missing data"), ("NOTRUN_BG", "BORDER", "Not run"))
    for fill, outline, text in marks:
        lay.addWidget(_Swatch(fill, outline))
        lab = QLabel(text)
        lab.setProperty("role", "muted")
        lab.setStyleSheet("font-size: 12px;")
        lay.addWidget(lab)
        lay.addSpacing(8)
    note = QLabel("Changing a step turns every later step grey until it is re-run.")
    note.setProperty("role", "muted")
    note.setStyleSheet("font-size: 12px;")
    lay.addWidget(note)
    return w
