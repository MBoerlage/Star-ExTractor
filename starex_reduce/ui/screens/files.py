"""Reduce › Select (step 1 of 5): pick the stacked target and the stacked reference star, check the pair, load it
into the reduce flow. Stacking happens in Acquisition (Stack selected); stacks from older sessions or other
programs work too. Reading and checks are in core/pair.py; the preview is built in a worker."""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from starex_reduce.core import coords
from starex_reduce.core import pair as pr
from starex_reduce.core.pipeline import StageDef

from .. import dialogs, theme
from ..widgets.common import Card, StatusLine, label
from .base import StepScreen

if TYPE_CHECKING:
    from ..main_window import MainWindow

DESCRIPTION = ("Pick the stacked target and the stacked reference star. Stacking is done in Acquisition; stacks "
               "from older sessions or other programs work too. The pair you load here goes through all the "
               "following steps.")
PREVIEW_H = 150
NOTES_DELAY_MS = 1000
ICON = {pr.OK: "✓", pr.WARN: "!", pr.ERR: "✗", pr.NOTE: "–"}
STATE = {pr.OK: "ok", pr.WARN: "warn", pr.ERR: "err", pr.NOTE: "off"}
TAG_STYLE = ("font-size: 11px; padding: 1px 7px; border-radius: 8px; border: 1px solid {c}; color: {c};")


def tag(text: str = "", colour: str = theme.MUTED) -> QLabel:
    t = QLabel(text)
    t.setStyleSheet(TAG_STYLE.format(c=colour))
    t.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return t


def set_tag(t: QLabel, text: str, colour: str = theme.MUTED) -> None:
    t.setText(text)
    t.setStyleSheet(TAG_STYLE.format(c=colour))
    t.setVisible(bool(text))


def shown_path(path: str | Path) -> str:
    """The path with the home folder written as ~ (the full path is in the tooltip)."""
    p, home = str(Path(path)), str(Path.home())
    return "~" + p[len(home):] if p.lower().startswith(home.lower() + os.sep.lower()) else p


def check_line(state: str, text: str) -> StatusLine:
    line = StatusLine(STATE[state], text)
    line.icon.setText(ICON[state])
    return line


class Preview(QLabel):
    """The stack around its spectrum rows with the spectrum and sky bands drawn over it."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(PREVIEW_H)
        self.setMinimumWidth(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(f"background: {theme.BG}; border: 1px solid {theme.LINE}; border-radius: 6px;")
        self.result: dict[str, Any] | None = None

    def set_result(self, res: dict[str, Any] | None) -> None:
        self.result = res
        self._draw()

    def retheme(self) -> None:
        """The palette changed: redraw the preview through the new colour map."""
        self._draw()

    def resizeEvent(self, e: Any) -> None:
        super().resizeEvent(e)
        self._draw()

    def _draw(self) -> None:
        res = self.result
        if res is None:
            self.clear()
            return
        u8 = res["u8"]
        h, w = u8.shape
        img = theme.tinted(QImage(u8.data, w, h, w, QImage.Format.Format_Grayscale8).copy())
        pm = QPixmap(max(self.width() - 2, 10), PREVIEW_H - 2)
        pm.fill(QColor(theme.BG))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        target = QRectF(0, 0, pm.width(), pm.height())
        p.drawImage(target, img)
        sy = pm.height() / (res["y1"] - res["y0"])
        sky, spec = (theme.SKY_FILL, theme.SKY_TEXT, 55), (theme.SPECTRUM_FILL, theme.SPECTRUM_TEXT, 70)
        for key, (fill, edge, alpha) in (("sky_a", sky), ("sky_b", sky), ("spec", spec)):
            band = res["rows"].get(key)
            if not band:
                continue
            top = (band[0] - 1 - res["y0"]) * sy  # rows are 1-based inclusive; edges sit between rows
            bot = (band[1] - res["y0"]) * sy
            c = QColor(fill)
            c.setAlpha(alpha)
            p.fillRect(QRectF(0, top, pm.width(), bot - top), c)
            pen = QPen(QColor(edge), 1)
            if key != "spec" and theme.is_dark_room():  # sky dashed, spectrum solid: told apart without colour
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(0, round(top), pm.width(), round(top))
            p.drawLine(0, round(bot), pm.width(), round(bot))
        p.end()
        self.setPixmap(pm)


class StackCard(Card):
    """One side of the pair: path + Browse, name, what the header says, preview."""

    def __init__(self, role: str, screen: FilesScreen) -> None:
        super().__init__(f"{pr.TITLE[role]} stack")
        self.role, self.screen, self.host = role, screen, screen.host
        self.info: pr.StackInfo | None = None
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText(f"No {role} stack picked")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        self.body.addLayout(row)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(6)
        g.setColumnStretch(1, 1)
        name_row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Object name")
        self.name.textEdited.connect(self._name_typed)
        self.name_tag = tag()
        name_row.addWidget(self.name, 1)
        name_row.addWidget(self.name_tag)
        g.addWidget(label("Name", "muted"), 0, 0)
        g.addLayout(name_row, 0, 1)
        self.values: dict[str, QLabel] = {}
        self.tags: dict[str, QLabel] = {}
        for i, key in enumerate(("Size", "Stack", "Taken", "Camera", "Position", "Rows"), start=1):
            g.addWidget(label(key, "muted"), i, 0, Qt.AlignmentFlag.AlignTop)
            line = QHBoxLayout()
            v = label("", "mono", wrap=True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.values[key] = v
            line.addWidget(v, 1)
            if key in ("Position", "Rows"):
                self.tags[key] = tag()
                line.addWidget(self.tags[key], 0, Qt.AlignmentFlag.AlignTop)
            g.addLayout(line, i, 1)
        self.body.addLayout(g)
        self.preview = Preview()
        self.body.addWidget(self.preview)
        self.no_rows = label("No rows in this file. They will be placed automatically in 1D Extract.", "muted",
                             wrap=True, style="font-size: 12px;")
        self.body.addWidget(self.no_rows)
        self.show_info(None)

    # ---- picking ------------------------------------------------------------------------------------------
    def browse(self) -> None:
        stacks = self.host.settings.paths().stacks
        start = str(Path(self.info.path).parent) if self.info is not None else (str(stacks) if stacks.is_dir() else "")
        path = dialogs.open_file(self, f"{pr.TITLE[self.role]} stack", start, pr.FILE_FILTER)
        if path:
            self.set_file(path)

    def set_file(self, path: str | Path | None, name: str | None = None) -> None:
        """Read the header (and sets file) now, build the preview in a worker. name: keep a typed name."""
        if path is None:
            self.info = None
            self.path.clear()
            self.path.setToolTip("")
            self.name.clear()
            self.show_info(None)
            self.screen.update_checks()
            return
        self.info = pr.read_stack(path)
        self.path.setText(shown_path(path))
        self.path.setToolTip(str(path))
        self.name.setText(name if name is not None else self.info.name)
        self.show_info(self.info)
        self.screen.update_checks()

    def _name_typed(self) -> None:
        self._show_name_tag()
        self.screen.update_checks()

    def _show_name_tag(self) -> None:
        text = self.name.text().strip()
        if not text:
            set_tag(self.name_tag, "needed", theme.RED)
        elif self.info is not None and text == self.info.name and self.info.name_src:
            set_tag(self.name_tag, self.info.name_src)
        else:
            set_tag(self.name_tag, "typed")

    # ---- showing ------------------------------------------------------------------------------------------
    def show_info(self, info: pr.StackInfo | None) -> None:
        v = self.values
        self.preview.set_result(None)
        self.preview.hide()
        self.no_rows.hide()
        for t in self.tags.values():
            t.hide()
        if info is None or not info.ok:
            for lab in v.values():
                lab.setText("")
                lab.setStyleSheet("")
            if info is not None:
                v["Size"].setText(info.error)
                v["Size"].setStyleSheet(f"color: {theme.RED};")
            self._show_name_tag()
            return
        v["Size"].setStyleSheet("")
        v["Size"].setText(f"{info.size[0]} × {info.size[1]}")  # type: ignore[index]
        stack = []
        if info.nsub:
            stack.append(f"{info.nsub} subs" + (f" × {info.subexp:g} s" if info.subexp is not None else ""))
        self._value("Stack", " ".join(stack))
        self._value("Taken", info.start.strftime("%Y-%m-%d %H:%M:%S") + " UT" if info.start is not None else "")
        cam = []
        if info.gain is not None:
            cam.append(f"gain {info.gain:g}")
        if info.offset is not None:
            cam.append(f"offset {info.offset:g}")
        if info.temp is not None:
            cam.append(f"{info.temp:.1f} °C")
        if info.binning is not None and info.binning != (1, 1):
            cam.append(f"bin {info.binning[0]}×{info.binning[1]}")
        self._value("Camera", " · ".join(cam))
        if info.ra is not None and info.dec is not None:
            self._value("Position", f"{coords.format_ra(info.ra)}  {coords.format_dec(info.dec)}")
            set_tag(self.tags["Position"], info.pos_src)
        else:
            v["Position"].setText("none, only needed for airmass")
            v["Position"].setStyleSheet(f"color: {theme.MUTED};")
        if info.rows.get("spec"):
            r = info.rows
            lines = [f"Spectrum {r['spec'][0]}–{r['spec'][1]}"]
            sky = [f"Sky {k[-1].upper()} {r[k][0]}–{r[k][1]}" for k in ("sky_a", "sky_b") if k in r]
            if sky:
                lines.append(" · ".join(sky))
            v["Rows"].setText("\n".join(lines))
            v["Rows"].setStyleSheet("")
            set_tag(self.tags["Rows"], info.rows_src)
            self.preview.show()
            self.host.runner.submit(f"preview.files.{self.role}", pr.preview_job, str(info.path), dict(info.rows))
        else:
            v["Rows"].setText("not in the file")
            v["Rows"].setStyleSheet(f"color: {theme.AMBER};")
            self.no_rows.show()
        self._show_name_tag()

    def _value(self, key: str, text: str) -> None:
        lab = self.values[key]
        lab.setText(text or "not in header")
        lab.setStyleSheet("" if text else f"color: {theme.MUTED};")

    def on_preview(self, res: dict[str, Any]) -> None:
        if self.info is not None and res.get("path") == str(self.info.path):
            self.preview.set_result(res)
            self.screen.preview_ms[self.role] = res["ms"]


class FilesScreen(StepScreen):
    def __init__(self, stage: StageDef, total: int, host: MainWindow) -> None:
        super().__init__(stage, total)
        self.host = host
        self.preview_ms: dict[str, float] = {}
        self._items: list[tuple[str, str]] = []
        hd = self.header
        hd.description.setText(DESCRIPTION)
        hd.description.setMaximumWidth(760)
        for w in (hd.chip_in, hd.arrow, hd.chip_out, hd.prev_btn, hd.next_btn, hd.counter, hd.btn_this,
                  hd.btn_both):
            w.setParent(None)  # Select has no per-image run: the pair is loaded below
        hd.set_files = lambda *_a: None  # type: ignore[method-assign]
        hd.set_output_ready = lambda *_a: None  # type: ignore[method-assign]
        self.latest_btn = QPushButton("Use latest stacks")
        self.latest_btn.setToolTip("The newest night in the Stacks folder: its newest <Name>_target_S.fit and "
                                   "<Name>_ref_S.fit, written by Acquisition > Stack selected")
        self.latest_btn.clicked.connect(self.use_latest)
        hd.layout().addWidget(self.latest_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.load_btn = QPushButton("Load pair")  # right of Use latest stacks: pick, then load
        self.load_btn.setProperty("role", "primary")
        self.load_btn.setToolTip("Load the two stacks into the reduce flow (the pair folder, its logbook and work/)")
        self.load_btn.clicked.connect(self.load_pair)
        hd.layout().addWidget(self.load_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        lay = self.body_layout
        self.message = label("", wrap=True)  # what Use latest stacks picked, or what Load pair did
        lay.addWidget(self.message)
        cards = QHBoxLayout()
        cards.setSpacing(16)
        self.cards = {role: StackCard(role, self) for role in pr.ROLES}
        for c in self.cards.values():
            cards.addWidget(c, 1, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(cards)
        self.checks_card = Card("Checks")
        self.checks_status = label("", style="font-size: 12px;")
        self.checks_card.head.insertWidget(1, self.checks_status)
        self.check_lines = QVBoxLayout()
        self.check_lines.setSpacing(4)
        self.checks_card.body.addLayout(self.check_lines)
        lay.addWidget(self.checks_card)
        lay.addWidget(self._build_notes())
        lay.addWidget(self._build_queue())
        lay.addStretch(1)
        self.load_session()

    def _build_notes(self) -> Card:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QPlainTextEdit

        card = Card("Pair notes", "Saved in the pair's logbook.json about a second after you stop typing.")
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes for this pair (seeing, clouds, what you changed)…")
        self.notes.setFixedHeight(84)
        self.notes.textChanged.connect(self._notes_typed)
        card.body.addWidget(self.notes)
        self.notes_note = label("", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.notes_note)
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.setInterval(NOTES_DELAY_MS)
        self._notes_timer.timeout.connect(self.save_notes)
        self._notes_dirty = False
        self.host.notes_changed.connect(self._notes_elsewhere)
        return card

    def _notes_typed(self) -> None:
        self._notes_dirty = True
        self._notes_timer.start()

    def save_notes(self) -> bool:
        """Write the notes into the pair logbook (never loses text: kept in the box until a logbook exists)."""
        from starex_reduce.core import logbook

        self._notes_timer.stop()
        folder = self.host.session.folder
        if not self._notes_dirty or folder is None or logbook.load(folder) is None:
            return False
        try:
            logbook.set_notes(folder, self.notes.toPlainText())
        except OSError as exc:
            self.notes_note.setText(f"Could not save the notes: {exc}")
            return False
        self._notes_dirty = False
        self.host.notes_changed.emit(str(folder))  # the Library shows the same notes
        return True

    def _notes_elsewhere(self, folder: str) -> None:
        """The Library edited the notes of the pair loaded here: show them (unless typing is in progress)."""
        cur = self.host.session.folder
        if cur is not None and str(cur) == folder and not self._notes_dirty:
            self._load_notes()

    def _load_notes(self) -> None:
        from starex_reduce.core import logbook

        doc = logbook.load(self.host.session.folder)
        self.notes.blockSignals(True)
        if doc is not None:
            self.notes.setPlainText(str(doc.get("notes") or ""))
            self._notes_dirty = False
        self.notes.blockSignals(False)
        self.notes_note.setText(f"In {self.host.session.folder.name}/logbook.json." if doc is not None  # type: ignore[union-attr]
                                else "Kept here until the pair is loaded; the notes of the two sets from "
                                     "Acquisition are added then.")

    def hideEvent(self, e: Any) -> None:
        self.save_notes()
        super().hideEvent(e)

    def _build_queue(self) -> QFrame:
        strip = QFrame()
        strip.setObjectName("nightqueue")
        strip.setStyleSheet(f"#nightqueue {{ border: 1px dashed {theme.BORDER}; border-radius: 8px; }}")
        row = QHBoxLayout(strip)
        row.setContentsMargins(14, 10, 14, 10)
        row.addWidget(label("Night queue: later, several target and reference pairs here for batch processing.",
                            "muted", wrap=True), 1)
        add = QPushButton("+ Add pair")
        add.setEnabled(False)
        add.setToolTip("Batch processing comes later")
        self.add_pair_btn = add
        row.addWidget(add)
        return strip

    # ---- session -----------------------------------------------------------------------------------------
    def load_session(self) -> None:
        """Show the loaded pair (re-reading its source files), or nothing."""
        p = pr.active_pair(self.host.session)
        for role, c in self.cards.items():
            entry = (p or {}).get(role) or {}
            src = entry.get("source")
            folder = self.host.session.folder
            if src and not Path(src).exists() and folder is not None and entry.get("file"):
                src = str(folder / entry["file"])  # moved since: the copy Geometry reads
            c.set_file(src if src and Path(src).exists() else None, entry.get("name"))
        self.message.setText("")
        self._load_notes()

    def names(self) -> dict[str, str]:
        return {role: c.name.text().strip() for role, c in self.cards.items()}

    def update_checks(self) -> None:
        if not hasattr(self, "check_lines"):
            return
        t, r = self.cards["target"].info, self.cards["reference"].info
        self._items = pr.checks(t, r, self.names())
        while self.check_lines.count():
            w = self.check_lines.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for state, text in self._items:
            self.check_lines.addWidget(check_line(state, text))
        state, text = pr.summary(self._items)
        colour = {pr.OK: theme.GREEN, pr.WARN: theme.AMBER, pr.ERR: theme.RED}[state]
        self.checks_status.setText(text)
        self.checks_status.setStyleSheet(f"font-size: 12px; color: {colour};")
        if not self.blocked() and self.message.text().startswith("Fix the items"):
            self.message.setText("")

    def blocked(self) -> bool:
        return any(s == pr.ERR for s, _ in self._items)

    # ---- actions -------------------------------------------------------------------------------------------
    def load_pair(self) -> bool:
        self.update_checks()
        if self.blocked():
            self._say("err", "Fix the items marked ✗ first.")
            return False
        infos = {role: c.info for role, c in self.cards.items()}
        typed_notes = self.notes.toPlainText() if self._notes_dirty else ""
        problem = self.host.load_pair(infos, self.names())  # type: ignore[arg-type]  # shared with the Library
        if problem:
            self._say("err", problem)
            return False
        current = self.notes.toPlainText()
        if typed_notes.strip() and typed_notes.strip() not in current:  # typed before the pair existed: keep it
            self.notes.setPlainText((current.rstrip() + "\n\n" if current.strip() else "") + typed_notes)
            self._notes_dirty = True
            self.save_notes()
        self.host.inputs_changed()
        self.host.refresh_ribbon()
        self._say("ok", "✓ Pair loaded. Next: Geometry.")
        return True

    def use_latest(self) -> None:
        """The newest night in Stacks: its newest target and reference stacks; says what it picked."""
        found, night = pr.latest_stacks(self.host.settings.paths().stacks)
        for role, path in found.items():
            if path is not None:
                self.cards[role].set_file(path)
        picked = [f"{p.name}" for p in found.values() if p is not None]
        missing = [pr.TITLE[r].lower() for r, p in found.items() if p is None]
        if not picked:
            self._say("warn", "No stacks in the Stacks folder yet. Stack in Acquisition, or Browse… for them.")
        elif missing:
            self._say("warn", f"Picked {night}: {picked[0]}; no {missing[0]} stack in that night.")
        else:
            self._say("off", f"Picked {night}: {' and '.join(picked)}.")

    def _say(self, state: str, text: str) -> None:
        colour = {"ok": theme.GREEN, "err": theme.RED, "warn": theme.AMBER}.get(state, theme.MUTED)
        self.message.setText(text)
        self.message.setStyleSheet(f"color: {colour};")

    def on_preview(self, role: str, res: dict[str, Any]) -> None:
        self.cards[role].on_preview(res)

