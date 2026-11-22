"""Step 4 · Wavelength: Balmer lines of the reference star → dispersion solution → linear Å grid."""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QMenu,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core import wavelength as w
from starex_reduce.core.pipeline import StageDef, StageRun, input_name
from starex_reduce.core.target_wave import TARGET_LINES, Adjust
from starex_reduce.core.wavelength_stage import (
    match_job,
    params_of,
    preview_job,
    target_adjust,
    target_cal_params,
    target_inputs,
)

from .. import theme
from ..widgets.common import Card, InfoButton, Segmented, StatusLine, label
from ..widgets.plot_guard import pg
from .base import StepScreen
from .wavelength_target import TargetCalCard

if TYPE_CHECKING:
    from ..main_window import MainWindow

MODES = (("refine", "Refine the saved solution (shift only)"), ("free", "Fit freely from these lines"),
         ("fixed", "Use the pasted coefficients"))


def mono_item(text: str, colour: str | None = None) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFont(theme.mono_font(12))
    it.setFlags(Qt.ItemFlag.ItemIsEnabled)
    if colour:
        from PySide6.QtGui import QColor
        it.setForeground(QColor(colour))
    return it


class WavelengthScreen(StepScreen):
    def __init__(self, stage: StageDef, total: int, host: MainWindow) -> None:
        super().__init__(stage, total, two_images=False)
        self.host = host
        self.result: dict[str, Any] | None = None
        self.view = "Reference"
        self.unit = "A"  # table unit
        self.x_unit = "px"  # chart X axis
        self._loading = False
        self.markers: list[pg.InfiniteLine] = []
        self.header.set_files("Reference_X.fit", "Reference_W.fit")
        row = QHBoxLayout()
        row.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._build_plot_card())
        left.addWidget(self._build_points_card())
        self.cal_card = TargetCalCard()
        self.cal_card.mode_changed.connect(self.set_cal_mode)
        self.cal_card.fit_requested.connect(self.fit_target)
        self.cal_card.anchor_toggled.connect(self.toggle_anchor)
        left.addWidget(self.cal_card)
        left.addWidget(self._build_coeff_card())
        row.addLayout(left, 1)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_solution_card())
        right.addWidget(self._build_target_card())
        right.addWidget(self._build_resolution_card())
        right.addWidget(self._build_lines_card())
        right.addStretch(1)
        side = QWidget()
        side.setLayout(right)
        side.setFixedWidth(300)
        row.addWidget(side, 0, Qt.AlignmentFlag.AlignTop)
        self.body_layout.addLayout(row)
        self.body_layout.addStretch(1)

    def _update_chips(self, idx: int) -> None:  # one run for both images, named after the reference
        self.header.set_files("Reference_X.fit", "Reference_W.fit")

    # ---- construction ------------------------------------------------------------------------
    def _build_plot_card(self) -> Card:
        card = Card()
        top = QHBoxLayout()
        self.tabs = Segmented(["Reference profile", "Target profile"])
        self.tabs.changed.connect(self._tab_changed)
        top.addWidget(self.tabs)
        top.addStretch(1)
        for text, tip, fn in (("Fit && auto", "Whole profile, auto scale", lambda: self._x_range("full")),
                              ("Fit", "Zoom to the Balmer lines", lambda: self._x_range("lines")),
                              ("×1", "Whole profile", lambda: self._x_range("full")),
                              ("↔", "Show the full width", lambda: self._x_range("full"))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            top.addWidget(b)
        top.addWidget(label("Display", "muted"))
        self.axis_toggle = Segmented(["px", "Å"])
        self.axis_toggle.setToolTip("Show the profile against detector column (px) or wavelength (Å)")
        self.axis_toggle.changed.connect(self._axis_changed)
        self.axis_toggle.setEnabled(False)
        top.addWidget(self.axis_toggle)
        self.readout = label("", "mono")
        self.readout.setMinimumWidth(0)  # may shrink on narrow windows instead of pushing the side panel out
        self.readout.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.readout, 1)
        card.body.addLayout(top)
        self.plot = pg.PlotWidget(enableMenu=False)
        self.plot.setBackground(theme.CARD)
        self.plot.setMinimumHeight(280)
        pi = self.plot.getPlotItem()
        pi.hideButtons()
        pi.showGrid(x=True, y=False, alpha=0.12)
        pi.setLabel("bottom", "Detector column (px)")
        pi.hideAxis("left")
        pi.setMouseEnabled(x=True, y=False)
        self.curve = pg.PlotDataItem(pen=pg.mkPen(theme.TEAL, width=1.5))
        self.curve.setClipToView(True)
        self.curve.setDownsampling(auto=True, method="peak")
        pi.addItem(self.curve)
        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)
        card.body.addWidget(self.plot)
        card.body.addWidget(label("Drag a dashed marker onto its line if the automatic centre is wrong; the fit "
                                  "follows. Roll the wheel to zoom X.", "muted", wrap=True, style="font-size: 12px;"))
        return card

    def _build_points_card(self) -> Card:
        card = Card("Calibration points")
        card.head.addWidget(label("Fit order", "muted"))
        self.order = Segmented(["Linear", "Quadratic"])
        self.order.changed.connect(self._order_changed)
        card.head.addWidget(self.order)
        self.order_note = label("", "muted", wrap=True, style="font-size: 12px;")
        card.head.addWidget(self.order_note, 1)
        card.head.addWidget(label("Unit", "muted"))
        self.unit_box = QComboBox()
        self.unit_box.addItems(["Å", "nm"])
        self.unit_box.currentIndexChanged.connect(self._unit_changed)
        card.head.addWidget(self.unit_box)
        self.table = QTableWidget(0, 6)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(34)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for c, width in ((0, 50), (5, 40)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(c, width)
        self.table.cellClicked.connect(self._cell_clicked)
        card.body.addWidget(self.table)
        self.footer = label("", "mono", wrap=True)
        card.body.addWidget(self.footer)
        self.messages = StatusLine("warn", "")
        self.messages.hide()
        card.body.addWidget(self.messages)
        return card

    def _build_coeff_card(self) -> Card:
        card = Card("Coefficients")
        card.head.insertWidget(1, InfoButton(
            "Same layout as the BASS Project coefficients box: comma separated, offset first, always in nm, "
            "whatever unit the table shows. Order 2 means three terms: f(x) = a + b·x + c·x², with x the pixel "
            "(0 = first column) and f(x) the wavelength in nm. Copy puts this line on the clipboard; Paste to "
            "apply reads 2 or 3 numbers from the clipboard and uses them as the solution."))
        self.copy_btn = copy_b = QPushButton("Copy")
        copy_b.clicked.connect(self.copy_coeffs)
        self.paste_btn = paste_b = QPushButton("Paste to apply")
        paste_b.clicked.connect(lambda: self.paste_coeffs())  # clicked(bool) must not reach `text`
        card.head.addWidget(copy_b)
        card.head.addWidget(paste_b)
        box_css = (f"color: {theme.TEXT}; font-size: 13px; padding: 8px 10px; border: 1px solid {theme.BORDER};"
                   f"border-radius: 8px; background: {theme.BG};")
        self.coeff_text = label("", "mono", wrap=True, style=box_css)
        self.coeff_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.body.addWidget(self.coeff_text)
        self.paste_status = StatusLine("err", "")
        self.paste_status.hide()
        card.body.addWidget(self.paste_status)
        g = QGridLayout()
        self.coeff_cells = {}
        for col, name in enumerate(("a", "b", "c")):
            g.addWidget(label(name, "muted"), 0, 2 * col)
            v = label("", "mono", style=f"color: {theme.TEXT};")
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            g.addWidget(v, 0, 2 * col + 1)
            self.coeff_cells[name] = v
        card.body.addLayout(g)
        self.fits_terms = label("", "mono", wrap=True)
        card.body.addWidget(self.fits_terms)
        return card

    def _build_solution_card(self) -> Card:
        card = Card("Solution")
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QRadioButton] = {}
        for i, (key, text) in enumerate(MODES):
            rb = QRadioButton(text)
            self.mode_group.addButton(rb, i)
            self.mode_buttons[key] = rb
            card.body.addWidget(rb)
        self.mode_group.idToggled.connect(lambda i, on: on and self._mode_changed(MODES[i][0]))
        self.shift_note = label("", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.shift_note)
        self.saved_note = label("", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.saved_note)
        return card

    def _build_target_card(self) -> Card:
        """Shown on the Target tab only: the target's own offset from the reference solution."""
        card = self.target_card = Card("Target adjustment")
        card.body.addWidget(label("Manual shift: added after the target calibration (Auto, Shift only, Shift and "
                                  "scale). In Manual mode, the whole target setting as before: the reference "
                                  "solution unchanged by default, Auto-match, free fit.", "muted", wrap=True,
                                  style="font-size: 12px;"))
        row = QHBoxLayout()
        row.addWidget(label("Shift (px)", "muted"))
        self.t_shift = QDoubleSpinBox()
        self.t_shift.setRange(-200.0, 200.0)
        self.t_shift.setDecimals(2)
        self.t_shift.setSingleStep(0.1)
        self.t_shift.setFont(theme.mono_font(12))
        self.t_shift.valueChanged.connect(lambda v: self._shift_to(v, "manual"))
        row.addWidget(self.t_shift, 1)
        card.body.addLayout(row)
        steps = QHBoxLayout()
        steps.setSpacing(4)
        for d in (-1.0, -0.1, 0.1, 1.0):
            b = QPushButton(f"{d:+g}".replace("-", "−"))
            b.setToolTip(f"Move the target's markers by {d:+g} px")
            b.setStyleSheet("padding-left: 2px; padding-right: 2px;")
            b.setMinimumWidth(0)
            b.clicked.connect(lambda _=False, d=d: self.t_shift.setValue(self.t_shift.value() + d))
            steps.addWidget(b, 1)
        card.body.addLayout(steps)
        acts = QHBoxLayout()
        self.t_reset = QPushButton("Reset")
        self.t_reset.setToolTip("Back to the reference solution, unchanged")
        self.t_reset.clicked.connect(self.reset_target)
        self.t_match_btn = QPushButton("Auto-match")
        self.t_match_btn.setToolTip("Estimate the target's shift from its telluric bands (O₂ A, O₂ B, "
                                    "H₂O 7168) against the reference profile, and from the target lines "
                                    "picked below")
        self.t_match_btn.clicked.connect(self.auto_match)
        acts.addWidget(self.t_match_btn, 1)
        acts.addWidget(self.t_reset)
        card.body.addLayout(acts)
        self.t_match = StatusLine("off", "")
        self.t_match.hide()
        card.body.addWidget(self.t_match)
        acc = QHBoxLayout()
        self.t_accept = QPushButton("Accept")
        self.t_accept.clicked.connect(self.accept_match)
        self.t_ignore = QPushButton("Ignore")
        self.t_ignore.clicked.connect(self._hide_match)
        acc.addWidget(self.t_accept)
        acc.addWidget(self.t_ignore)
        acc.addStretch(1)
        card.body.addLayout(acc)
        self.t_accept.hide()
        self.t_ignore.hide()
        self.t_free = QCheckBox("Fit freely from target lines")
        self.t_free.setToolTip("Fit the target's own solution from the lines picked: 1–2 lines shift only, "
                               "3–4 linear, 5 or more quadratic")
        self.t_free.toggled.connect(self._free_toggled)
        card.body.addWidget(self.t_free)
        self.t_lines_btn = QPushButton("Add or remove lines…")
        self.t_lines_menu = QMenu(self)
        self.t_line_actions: dict[str, QAction] = {}
        self.t_lines_btn.setMenu(self.t_lines_menu)
        card.body.addWidget(self.t_lines_btn)
        self.t_note = label("", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.t_note)
        card.hide()
        return card

    def _fill_target_lines(self, adj: Adjust) -> None:
        self.t_lines_menu.clear()
        self.t_line_actions = {}
        for name, lam in {**TARGET_LINES, **adj.custom}.items():
            act = QAction(f"{name}  {lam:.2f} Å", self, checkable=True)
            act.setChecked(name in adj.lines)
            act.toggled.connect(lambda _on: self._target_lines_changed())
            self.t_lines_menu.addAction(act)
            self.t_line_actions[name] = act
        self.t_lines_menu.addSeparator()
        other = QAction("Other line…", self)
        other.triggered.connect(lambda: self.add_target_line())
        self.t_lines_menu.addAction(other)

    def _build_resolution_card(self) -> Card:
        card = Card("Resolution")
        g = QGridLayout()
        self.fwhm = label("", "mono", style=f"color: {theme.TEXT};")
        self.rval = label("", "mono", style=f"color: {theme.TEXT};")
        for r, (name, w_) in enumerate((("FWHM (Å)", self.fwhm), ("R value", self.rval))):
            g.addWidget(label(name, "muted"), r, 0)
            w_.setAlignment(Qt.AlignmentFlag.AlignRight)
            g.addWidget(w_, r, 1)
        card.body.addLayout(g)
        self.res_note = label("", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.res_note)
        return card

    def _build_lines_card(self) -> Card:
        card = Card("Lines")
        self.lines_btn = QPushButton("Add or remove lines…")
        self.lines_menu = QMenu(self)
        self.line_actions: dict[str, QAction] = {}
        for name, lam in w.BALMER.items():
            act = QAction(f"{name} {lam:.2f} Å", self, checkable=True)
            act.toggled.connect(lambda _on, n=name: self._lines_changed())
            self.lines_menu.addAction(act)
            self.line_actions[name] = act
        self.lines_btn.setMenu(self.lines_menu)
        card.body.addWidget(self.lines_btn)
        self.again_btn = again = QPushButton("Fit again · Reference")
        again.setToolTip("Forget dragged markers and find the line centres again")
        again.clicked.connect(self.fit_again)
        card.body.addWidget(again)
        return card

    # ---- state -------------------------------------------------------------------------------
    def params(self) -> dict[str, Any]:
        return params_of(self.host.session, self.host.profile)

    def _set_params(self, **values: Any) -> None:
        s = self.host.session
        s.params["wavelength"] = {**self.params(), **values}
        self.host.inputs_changed()
        self.preview()

    def load_session(self) -> None:
        self._loading = True
        p = self.params()
        self.mode_buttons[p["mode"] if p["mode"] in self.mode_buttons else "refine"].setChecked(True)
        self.mode_buttons["fixed"].setVisible(bool(p.get("fixed")))
        for name, act in self.line_actions.items():
            act.setChecked(name in p["lines"])
        prof = self.host.profile.dispersion
        self.saved_note.setText(f"Saved solution: {prof.get('date', '?')} · {prof.get('source', '')}")
        self._load_target(target_adjust(p))
        self.cal_card.set_mode(target_cal_params(p)["mode"])
        self._loading = False
        self.result = None
        if self.isVisible():
            self.preview()

    # ---- target calibration (v2) -------------------------------------------------------------
    def cal_params(self) -> dict[str, Any]:
        return target_cal_params(self.params())

    def _set_cal(self, why: str, **values: Any) -> None:
        cp = {**self.cal_params(), **values}
        s = self.host.session
        s.set_image_params("wavelength", "Target", {"cal": cp})
        s.log(f"Target wavelength: {why}")
        self.host.inputs_changed()
        self.preview()

    def set_cal_mode(self, mode: str) -> None:
        if not self._loading and mode != self.cal_params()["mode"]:
            self._set_cal(f"calibration mode {mode}", mode=mode)

    def toggle_anchor(self, key: str, use: bool) -> None:
        off = set(self.cal_params()["disabled"])
        off.discard(key) if use else off.add(key)
        self._set_cal(f"anchor {key.split(':', 1)[1]} {'used' if use else 'left out'}", disabled=sorted(off))

    def fit_target(self) -> None:
        """Measure and fit the target again (the fit runs with the preview, in a worker)."""
        self.host.session.log("Target wavelength: fit again")
        self.preview()

    # ---- target adjustment --------------------------------------------------------------------
    def adjust(self) -> Adjust:
        return target_adjust(self.params())

    def _load_target(self, adj: Adjust) -> None:
        was = self._loading
        self._loading = True
        self.t_shift.setValue(adj.shift)
        self.t_free.setChecked(adj.mode == "free")
        self._fill_target_lines(adj)
        self._loading = was

    def _set_adjust(self, adj: Adjust, why: str = "") -> None:
        """Store the target's adjustment (per target, in the session) and preview it."""
        s = self.host.session
        s.set_image_params("wavelength", "Target", {"adjust": adj.to_params()})
        if why:
            s.log(f"Target wavelength: {why}")
        self.host.inputs_changed()
        t = (self.result or {}).get("target")
        if t is not None and adj.mode == "shift":  # move the markers at once; the preview follows
            ref = w.Solution(self.result["coeffs"])
            t["coeffs"] = list(ref.shifted(adj.shift).coeffs_nm if adj.shift else ref.coeffs_nm)
            self._show()
        self.preview()

    def _shift_to(self, value: float, source: str) -> None:
        if self._loading:
            return
        adj = self.adjust()
        adj.shift, adj.source = round(float(value), 3), source if value else ""
        self._set_adjust(adj)

    def reset_target(self) -> None:
        cur = self.adjust()
        self._set_adjust(Adjust(lines=cur.lines, custom=cur.custom), "reset to the reference solution")
        self._load_target(self.adjust())
        self._hide_match()

    def _free_toggled(self, on: bool) -> None:
        if self._loading:
            return
        adj = self.adjust()
        adj.mode = "free" if on else "shift"
        self._set_adjust(adj, "fit freely from target lines" if on else "shift only")

    def _target_lines_changed(self) -> None:
        if self._loading:
            return
        adj = self.adjust()
        adj.lines = [n for n, a in self.t_line_actions.items() if a.isChecked()]
        adj.manual = {k: v for k, v in adj.manual.items() if k in adj.lines}
        self._set_adjust(adj, "target lines " + (", ".join(adj.lines) or "none"))

    def add_target_line(self, text: str | None = None) -> None:
        """Free entry: a name and a rest wavelength, for example "Fe II 5169 5169.03"."""
        if text is None:
            text, ok = QInputDialog.getText(self, "Add a target line",
                                            "Name and rest wavelength (Å), for example: Fe II 5169 5169.03")
            if not ok:
                return
        parts = text.strip().rsplit(None, 1)
        try:
            name, lam = parts[0].strip(), float(parts[1].replace(",", "."))
        except (IndexError, ValueError):
            self.t_note.setText("Type a name and a wavelength in Å, for example: Fe II 5169 5169.03")
            return
        adj = self.adjust()
        adj.custom[name] = lam
        if name not in adj.lines:
            adj.lines.append(name)
        self._set_adjust(adj, f"line {name} {lam:.2f} Å added")
        self._load_target(adj)

    def auto_match(self) -> None:
        s = self.host.session
        if s.folder is None:
            return
        ref = s.folder / (input_name("wavelength", "Reference") or "")
        tgt = s.folder / (input_name("wavelength", "Target") or "")
        if ref.exists() and tgt.exists():
            self.t_match_btn.setEnabled(False)
            self.t_match.set("off", "Matching…")
            self.t_match.show()
            self.host.runner.submit("preview.wavelength.match", match_job, ref, tgt, self.params())

    def on_match(self, res: dict[str, Any]) -> None:
        self.t_match_btn.setEnabled(True)
        self.match = res
        self.t_match.set("ok" if res["reliable"] else "warn", res["text"])
        self.t_match.show()
        self.t_accept.setVisible(bool(res["reliable"]))
        self.t_ignore.setVisible(True)
        if self._apply_match:
            self._apply_match = False
            if res["reliable"]:
                self.accept_match()

    def accept_match(self) -> None:
        m = getattr(self, "match", None)
        if not m or not m["reliable"]:
            return
        adj = self.adjust()
        adj.shift, adj.source, adj.mode, adj.manual = round(m["shift"], 3), "auto-match", "shift", {}
        self._load_target(adj)
        self._set_adjust(adj, f"auto-match {m['shift']:+.2f} ± {m['err']:.2f} px ({m['shift_a']:+.2f} Å) "
                              "accepted")
        self._hide_match()

    def _hide_match(self) -> None:
        self.t_match.hide()
        self.t_accept.hide()
        self.t_ignore.hide()

    def on_shown(self) -> None:
        if self.result is None:
            self.preview()

    def preview(self) -> None:
        s = self.host.session
        if s.folder is None:
            return
        ref = s.folder / (input_name("wavelength", "Reference") or "")
        if not ref.exists():
            self.curve.setData([], [])
            return
        tgt = s.folder / (input_name("wavelength", "Target") or "")
        self.host.runner.submit("preview.wavelength.Reference", preview_job, ref, tgt, self.params(),
                                target_inputs(s))

    def on_preview(self, image: str, res: dict[str, Any]) -> None:
        if image == "match":
            self.on_match(res)
            return
        self.result = res
        self._show()

    def stage_done(self, run: StageRun) -> None:
        self.result = run.data
        self._show()
        self._save_profile(run.data or {})

    def _save_profile(self, d: dict[str, Any]) -> None:
        """Keep tonight's solution as the spectrograph's saved one (used by 'Refine' next time)."""
        if not d.get("points") or d.get("rms") is None or d["rms"] > 1.0 or len(d["points"]) < 3:
            return
        prof = self.host.profile
        prof.dispersion = {"coeffs_nm": list(d["coeffs"]), "order": len(d["coeffs"]) - 1,
                           "date": time.strftime("%Y-%m-%d"),
                           "source": f"{Path(str(self.host.session.folder)).name}, {d['mode']}, "
                                     f"{len(d['points'])} lines, RMS {d['rms']:.3f} Å"}
        if d.get("fwhm"):
            prof.fwhm_a, prof.resolving_power = d["fwhm"], d["R"]
        prof.save()
        self.saved_note.setText(f"Saved solution: {prof.dispersion['date']} · {prof.dispersion['source']}")
        self.host.session.log("Wavelength solution saved for this spectrograph")

    # ---- display -----------------------------------------------------------------------------
    def _view(self) -> dict[str, Any] | None:
        """The solution of the profile on screen: the reference's, or the target's own."""
        r = self.result
        if r is None:
            return None
        if self.view == "Target" and r.get("target") is not None:
            return {**r, **r["target"], "target": r["target"]}
        return r

    def _show(self) -> None:
        r = self._view()
        if r is None:
            return
        prof = r["profiles"].get(self.view)
        if prof is None:
            prof = r["profiles"]["Reference"]
        self.axis_toggle.setEnabled(True)
        x = self._to_axis(np.arange(len(prof), dtype=np.float64))
        peak = float(np.nanpercentile(prof, 99.5)) or 1.0
        self.curve.setData(x, prof / peak)
        self.plot.getPlotItem().setLabel("bottom", "Wavelength (Å)" if self.x_unit == "A" else "Detector column (px)")
        self.plot.getPlotItem().setYRange(-0.05, 1.25, padding=0)
        self._markers(r)
        self._table(r)
        self._coeffs(r)
        self._target_panel()
        t = (self.result or {}).get("target") or {}
        self.cal_card.show_result(t.get("cal"), None, manual=self.cal_params()["mode"] == "manual")
        r = self.result
        p = self.params()
        mode = r["mode"]
        note = (f"Shift from the saved solution: {r['shift']:+.1f} px" if mode != "fixed" else
                "Pasted coefficients, used as they are")
        free_rms = r.get("free_rms")
        if mode == "refine" and free_rms is not None and r.get("rms") and free_rms < 0.5 * r["rms"]:
            note += (f". The saved shape fits this night less well (RMS {r['rms']:.2f} Å): "
                     f"Fit freely gives RMS {free_rms:.2f} Å.")
        self.shift_note.setText(note)
        refine = p["mode"] == "refine"
        n = len(r["points"])
        cap = w.max_order(n, True)
        self._loading = True
        self.order.set_index(len(r["coeffs"]) - 2)
        self._loading = False
        for i, b in enumerate(self.order.buttons):
            b.setEnabled(p["mode"] == "free" and i + 1 <= max(cap, 1))
        self.order_note.setText("Fixed by the saved solution. Choose \"Fit freely\" to change it." if refine else
                                "3 lines: quadratic would fit exactly (no check)." if n == 3 and len(r["coeffs"]) == 3
                                else "")
        if r.get("fwhm"):
            self.fwhm.setText(f"{r['fwhm']:.2f}")
            self.rval.setText(f"{r['R']:.0f}")
            self.res_note.setText(f"Measured on the {r['fwhm_line']} line. Saved to the FITS header (BSS_ESRP). "
                                  "On a hot reference star the Balmer lines are broad, so this is a lower limit.")
        else:
            self.fwhm.setText("—")
            self.rval.setText("—")
            self.res_note.setText("No clean Balmer line to measure.")
        msgs = r.get("messages") or []
        self.messages.set("warn", " · ".join(msgs))
        self.messages.setVisible(bool(msgs))

    def _target_panel(self) -> None:
        on = self.view == "Target"
        self.target_card.setVisible(on)
        self.again_btn.setText("Fit again · Target" if on else "Fit again · Reference")
        self.again_btn.setToolTip(
            "Refit the TARGET: with Fit freely, find its line centres again; else Auto-match its shift" if on else
            "Refit the REFERENCE: forget dragged markers and find the Balmer line centres again")
        t = (self.result or {}).get("target")
        if not on or t is None:
            return
        adj = self.adjust()
        if t["mode"] == "reference":
            txt = "Using the reference solution unchanged."
        else:
            how = {"auto-match": "auto-match", "marker": "marker dragged", "free fit": "free fit"}.get(
                t["source"], "set by hand")
            txt = f"Offset {t['shift']:+.2f} px ({t['shift_a']:+.2f} Å) from the reference solution ({how})."
            if t["mode"] == "free" and t.get("rms") is not None:
                txt += f" {len(t['points'])} lines, RMS {t['rms']:.2f} Å."
        if adj.mode == "free" and not adj.lines:
            txt += " Pick target lines with Add or remove lines…"
        self.t_note.setText(" ".join([txt, *t.get("messages", [])]))

    def _markers(self, r: dict[str, Any]) -> None:
        pi = self.plot.getPlotItem()
        for m in self.markers:
            pi.removeItem(m)
        self.markers = []
        sol = w.Solution(r["coeffs"])
        pts = {q["line"]: q for q in r["points"]}
        target = self.view == "Target" and r.get("target") is not None
        lines = {n: w.BALMER[n] for n in self.params()["lines"]}
        if target:
            lines.update(self.adjust().wavelengths())
        for k, (name, lam) in enumerate(sorted(lines.items(), key=lambda kv: kv[1])):
            px = pts[name]["pixel"] if name in pts and not target else sol.pixel(lam)
            found = name in pts
            pen = pg.mkPen(theme.AMBER if found else theme.DIM, width=1, style=Qt.PenStyle.DashLine)
            m = pg.InfiniteLine(pos=float(self._to_axis(px)), angle=90, movable=True, pen=pen,
                                hoverPen=pg.mkPen(theme.TEXT, width=2),
                                label=name if any(ch.isdigit() for ch in name) else f"{name} {lam:.0f}",
                                labelOpts={"position": 0.95 - 0.07 * (k % 2),  # staggered: blue lines are close
                                           "color": theme.AMBER if found else theme.DIM,
                                           "fill": pg.mkBrush(theme.CARD)})
            if target:
                m.sigPositionChangeFinished.connect(
                    lambda line, n=name, p0=px: self._target_marker_moved(n, p0, self._to_px(line.value())))
            else:
                m.sigPositionChangeFinished.connect(
                    lambda line, n=name: self._marker_moved(n, self._to_px(line.value())))
            pi.addItem(m)
            self.markers.append(m)

    def _fmt_lam(self, a: float) -> str:
        return f"{a:.2f}" if self.unit == "A" else f"{a / 10:.4f}"

    def _table(self, r: dict[str, Any]) -> None:
        u = "Å" if self.unit == "A" else "nm"
        self.table.setHorizontalHeaderLabels(["#", "LINE", "PIXEL", f"WAVELENGTH {u}", f"ERROR {u}", ""])
        pts = r["points"]
        self.table.setRowCount(len(pts))
        for i, q in enumerate(pts):
            err = q["error"] if self.unit == "A" else q["error"] / 10
            self.table.setItem(i, 0, mono_item(str(i + 1)))
            manual_colour = theme.AMBER if q["manual"] else None  # moved by hand: needs attention
            self.table.setItem(i, 1, mono_item(q["line"] + (" ·" if q["manual"] else ""), manual_colour))
            self.table.setItem(i, 2, mono_item(f"{q['pixel']:.1f}"))
            self.table.setItem(i, 3, mono_item(self._fmt_lam(q["lam"])))
            self.table.setItem(i, 4, mono_item(f"{err:+.3f}".replace("-", "−") if abs(err) >= 5e-4 else "0.000"))
            x = mono_item("✕", theme.MUTED)
            x.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            x.setToolTip("Remove this line from the fit")
            self.table.setItem(i, 5, x)
        self.table.setFixedHeight(34 + 34 * max(1, len(pts)) + 4)
        sol = w.Solution(r["coeffs"])
        mid = sol.pixel(5500.0)
        disp = sol.dispersion_a(mid)
        tot = r.get("total") or 0.0
        rms = r.get("rms") or 0.0
        k = 1 if self.unit == "A" else 0.1
        self.footer.setText(f"Total error {tot * k:.3f} {u}    RMS {rms * k:.3f} {u}    "
                            f"Dispersion {disp * k:.3f} {u}/px")

    def _coeffs(self, r: dict[str, Any]) -> None:
        c = r["coeffs"]
        self.coeff_text.setText(w.format_coeffs(c))
        for name, v in zip(("a", "b", "c"), [*c, None, None][:3], strict=True):
            self.coeff_cells[name].setText("—" if v is None else w.bass_number(v, 10, 12).replace("-", "−"))
        crval, cdelt, n = r["grid"]
        self.fits_terms.setText(f"Linear FITS terms (Å) · CRVAL1 {crval:.2f}   CDELT1 {cdelt:.5f}   {n} px")

    def _x_range(self, mode: str) -> None:
        pi = self.plot.getPlotItem()
        if mode == "lines" and self.result and self.result["points"]:
            xs = [q["pixel"] for q in self.result["points"]]
            pi.setXRange(float(self._to_axis(min(xs) - 80)), float(self._to_axis(max(xs) + 80)), padding=0)
        else:
            pi.autoRange()
        pi.setYRange(-0.05, 1.25, padding=0)

    def _mouse_moved(self, pos: QPointF) -> None:
        r = self.result
        vb = self.plot.getPlotItem().vb
        if r is None or not vb.sceneBoundingRect().contains(pos):
            return
        x = self._to_px(vb.mapSceneToView(pos).x())
        prof = r["profiles"].get(self.view, r["profiles"]["Reference"])
        i = round(x)
        if 0 <= i < len(prof):
            lam = float(w.Solution(r["coeffs"]).angstrom(x))
            self.readout.setText(f"X {i} · λ {self._fmt_lam(lam)} {'Å' if self.unit == 'A' else 'nm'} · "
                                 f"{prof[i]:,.0f} ADU".replace(",", " "))

    def _solution(self) -> w.Solution | None:
        r = self._view()
        return w.Solution(r["coeffs"]) if r else None

    def _to_axis(self, px: np.ndarray | float) -> np.ndarray | float:
        """Detector column → the current X axis (px, or Å through the current solution)."""
        sol = self._solution()
        return sol.angstrom(px) if self.x_unit == "A" and sol is not None else px

    def _to_px(self, value: float) -> float:
        sol = self._solution()
        return sol.pixel(value) if self.x_unit == "A" and sol is not None else value

    # ---- actions -----------------------------------------------------------------------------
    def _axis_changed(self, idx: int) -> None:
        """View only: re-plot the same profile against px or Å, keeping the visible range."""
        pi = self.plot.getPlotItem()
        lo, hi = pi.vb.viewRange()[0]
        lo_px, hi_px = self._to_px(lo), self._to_px(hi)
        self.x_unit = ("px", "A")[idx]
        self._show()
        pi.setXRange(float(self._to_axis(lo_px)), float(self._to_axis(hi_px)), padding=0)

    def _tab_changed(self, idx: int) -> None:
        self.view = ("Reference", "Target")[idx]
        self._show()
        self._target_panel()

    def _unit_changed(self, idx: int) -> None:
        self.unit = "A" if idx == 0 else "nm"
        if self.result is not None:
            self._table(self.result)

    def _mode_changed(self, mode: str) -> None:
        if not self._loading:
            self._set_params(mode=mode)

    def _order_changed(self, idx: int) -> None:
        if self._loading:
            return
        order = idx + 1
        n = len(self.result["points"]) if self.result else 0
        self._set_params(order=order, allow_exact=order == 2 and n == 3)

    def _lines_changed(self) -> None:
        if not self._loading:
            lines = [n for n, a in self.line_actions.items() if a.isChecked()]
            self._set_params(lines=lines)

    def _marker_moved(self, name: str, px: float) -> None:
        manual = {**self.params()["manual"], name: round(float(px), 2)}
        self.host.session.log(f"Line {name}: marker moved to px {px:.1f}")
        self._set_params(manual=manual)

    def _cell_clicked(self, row: int, col: int) -> None:
        if col == 5 and self.result and 0 <= row < len(self.result["points"]):
            name = self.result["points"][row]["line"]
            self.line_actions[name].setChecked(False)  # triggers _lines_changed

    def _target_marker_moved(self, name: str, old_px: float, px: float) -> None:
        """Target tab: a dragged marker moves the whole target shift, or with Fit freely, that line only."""
        adj = self.adjust()
        if adj.mode == "free" and name in adj.lines:
            adj.manual[name] = round(float(px), 2)
            self._set_adjust(adj, f"target line {name}: marker moved to px {px:.1f}")
            return
        adj.shift = round(adj.shift + float(px - old_px), 3)
        adj.source = "marker"
        self._load_target(adj)
        self._set_adjust(adj, f"marker {name} dragged: target shift {adj.shift:+.2f} px")

    _apply_match = False

    def fit_again(self) -> None:
        """Acts on the profile on screen: the reference (as before) or the target."""
        if self.view != "Target":
            self._set_params(manual={})
            return
        if self.cal_params()["mode"] != "manual":  # the target calibration: measure and fit again
            self.fit_target()
            return
        adj = self.adjust()
        if adj.mode == "free":  # refit the target's own lines
            adj.manual = {}
            self._set_adjust(adj, "target lines fitted again")
        else:  # shift only: measure the shift again and apply it when reliable
            self._apply_match = True
            self.auto_match()

    def copy_coeffs(self) -> None:
        if self.result:
            QApplication.clipboard().setText(w.format_coeffs(self.result["coeffs"]))
            self.host.session.log("Coefficients copied")

    def paste_coeffs(self, text: str | None = None) -> None:
        text = QApplication.clipboard().text() if text is None else text
        try:
            coeffs = w.parse_coeffs(text)
        except ValueError as exc:
            self.paste_status.set("err", f"Could not use the clipboard text: {exc}")
            self.paste_status.show()
            return
        self.paste_status.hide()
        self.mode_buttons["fixed"].setVisible(True)
        self._loading = True
        self.mode_buttons["fixed"].setChecked(True)
        self._loading = False
        self.host.session.log(f"Coefficients pasted (order {len(coeffs) - 1}): {w.format_coeffs(coeffs)}")
        self._set_params(mode="fixed", fixed=coeffs)

