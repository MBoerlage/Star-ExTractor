"""Wavelength step: the Target calibration card (target calibration v2, docs/WAVELENGTH.md).

Shows what core/targetcal.py found: the summary and validation chips, the anchors with a Use tick each, the
residuals, the collapsed sky spectrum with its identified lines, and the difference between the target and reference
solutions with its verdict. Drawing only: every number comes from the worker's result. Palette tokens only; line
types differ by marker shape as well as colour (works through a red filter).
"""
from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from starex_reduce.core.linelists import ISM, SKY_KINDS, TELLURIC

from .. import theme
from ..widgets.common import Card, StatusLine, label
from ..widgets.plot_guard import pg

MODE_LABELS = (("auto", "Auto (recommended)"), ("shift", "Shift only"), ("shift_scale", "Shift and scale"),
               ("manual", "Manual (as before)"))
GROUPS = (("sky", "Sky"), ("telluric", "Telluric"), ("ism", "ISM"), ("stellar", "Stellar"))
SYMBOL = {"sky": "o", "telluric": "t", "ism": "s", "stellar": "d"}  # shape per type: circle, triangle, square, diamond
SYMBOL_NAME = {"o": "●", "t": "▼", "s": "■", "d": "◆"}
COLS = ("Use", "Type", "Name", "Rest (Å)", "Pixel", "Residual (Å)", "Weight", "")


def group_of(a: dict[str, Any]) -> str:
    return "sky" if a["kind"] in SKY_KINDS else "telluric" if a["kind"] == TELLURIC else \
        "ism" if a["kind"] == ISM else "stellar"


def colour_of(group: str) -> str:
    return {"sky": theme.TEAL, "telluric": theme.SKYBLUE, "ism": theme.VIOLET, "stellar": theme.AMBER}[group]


def _plot(ylabel: str, height: int) -> pg.PlotWidget:
    pw = pg.PlotWidget(enableMenu=False)
    pw.setBackground(theme.CARD)
    pw.setMinimumHeight(height)
    pi = pw.getPlotItem()
    pi.hideButtons()
    pi.showGrid(x=True, y=True, alpha=0.12)
    pi.setLabel("left", ylabel)
    pi.setLabel("bottom", "Wavelength (Å)")
    pi.setMouseEnabled(x=True, y=False)
    return pw


class TargetCalCard(Card):
    mode_changed = Signal(str)
    fit_requested = Signal()
    anchor_toggled = Signal(str, bool)  # anchor key, use

    def __init__(self) -> None:
        super().__init__("Target calibration")
        self.data: dict[str, Any] | None = None
        self._manual = False
        self._filling = False
        head = QHBoxLayout()
        head.addWidget(label("Mode", "muted"))
        self.mode = QComboBox()
        for key, text in MODE_LABELS:
            self.mode.addItem(text, key)
        self.mode.setToolTip("Auto: the reference solution plus a shift, with a scale only when clearly better and "
                             "within limits. Manual: the old target adjustment (shift, Auto-match, free fit).")
        self.mode.currentIndexChanged.connect(self._mode_picked)
        head.addWidget(self.mode)
        self.fit_btn = QPushButton("Fit target")
        self.fit_btn.setToolTip("Measure the target's sky lines, telluric bands and stellar lines again and fit")
        self.fit_btn.clicked.connect(self.fit_requested)
        head.addWidget(self.fit_btn)
        head.addStretch(1)
        self.body.addLayout(head)
        self.summary = label("", "mono", wrap=True)
        self.body.addWidget(self.summary)
        self.chip_box = QVBoxLayout()
        self.chip_box.setSpacing(2)
        self.body.addLayout(self.chip_box)
        self.chips: list[StatusLine] = []
        self.fallback = StatusLine("warn", "")
        self.fallback.hide()
        self.body.addWidget(self.fallback)
        # anchors
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(180)
        self.table.setMaximumHeight(260)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemChanged.connect(self._item_changed)
        self.body.addWidget(self.table)
        # residuals and the sky
        plots = QHBoxLayout()
        self.res_plot = _plot("Residual (Å)", 190)
        self.res_band = pg.LinearRegionItem(orientation="horizontal", movable=False,
                                            brush=pg.mkBrush(theme.with_alpha(theme.AMBER, 30)), pen=pg.mkPen(None))
        self.res_plot.addItem(self.res_band)
        self.res_items: list[Any] = []
        plots.addWidget(self.res_plot, 1)
        self.sky_plot = _plot("Sky (ADU)", 190)
        self.sky_plot.getPlotItem().setLabel("bottom", "Detector column (px)")
        self.sky_curve = pg.PlotDataItem(pen=pg.mkPen(theme.MUTED, width=1))
        self.sky_plot.addItem(self.sky_curve)
        self.sky_items: list[Any] = []
        plots.addWidget(self.sky_plot, 1)
        self.body.addLayout(plots)
        self.shape_key = label("", "muted", style="font-size: 12px;")
        self.body.addWidget(self.shape_key)
        # the difference between the two solutions
        self.body.addWidget(label("Target − reference solution", "eyebrow"))
        self.delta_nums = label("", "mono", wrap=True)
        self.body.addWidget(self.delta_nums)
        self.verdict = StatusLine("ok", "")
        self.body.addWidget(self.verdict)
        self.ref_check = StatusLine("off", "")
        self.body.addWidget(self.ref_check)
        self.delta_plot = _plot("Δ (Å)", 170)
        self.delta_curve = pg.PlotDataItem(pen=pg.mkPen(theme.TEXT, width=1.5))
        self.delta_mean = pg.InfiniteLine(angle=0, movable=False,
                                          pen=pg.mkPen(theme.MUTED, width=1, style=Qt.PenStyle.DashLine))
        self.delta_plot.addItem(self.delta_curve)
        self.delta_plot.addItem(self.delta_mean)
        self.delta_items: list[Any] = []
        self.body.addWidget(self.delta_plot)
        self.empty = label("Run 1D Extract for both images; the target fit runs with the preview.", "muted",
                           wrap=True)
        self.body.addWidget(self.empty)
        self.set_mode("auto")
        self.show_result(None, None)

    # ---- input ------------------------------------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self._filling = True
        self.mode.setCurrentIndex(max(0, [k for k, _t in MODE_LABELS].index(mode) if mode in dict(MODE_LABELS)
                                      else 0))
        self._filling = False

    def _mode_picked(self, _i: int) -> None:
        if not self._filling:
            self.mode_changed.emit(str(self.mode.currentData()))

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key:
            self.anchor_toggled.emit(str(key), item.checkState() == Qt.CheckState.Checked)

    # ---- output -----------------------------------------------------------------------------------------------
    def _set_chips(self, chips: list[tuple[str, str]]) -> None:
        while len(self.chips) < len(chips):
            s = StatusLine("off", "")
            self.chips.append(s)
            self.chip_box.addWidget(s)
        for i, s in enumerate(self.chips):
            if i < len(chips):
                lvl, text = chips[i]
                s.set({"ok": "ok", "warn": "warn", "err": "err"}.get(lvl, "info"), text)
                s.show()
            else:
                s.hide()

    def retheme(self) -> None:
        """The palette changed: draw the same result again (table colours and markers come from the theme)."""
        self.show_result(self.data, None, self._manual)

    def show_result(self, cal: dict[str, Any] | None, ref_disp: float | None, manual: bool = False) -> None:
        self.data, self._manual = cal, manual
        has = cal is not None
        for wdg in (self.table, self.res_plot, self.sky_plot, self.delta_plot, self.delta_nums, self.verdict,
                    self.shape_key):
            wdg.setVisible(has and not manual)
        self.empty.setVisible(not has and not manual)
        self.ref_check.setVisible(False)
        if manual:
            self.summary.setText("Manual: the target adjustment below (shift, Auto-match, free fit) is used as before.")
            self._set_chips([])
            self.fallback.hide()
            return
        if cal is None:
            self.summary.setText("")
            self._set_chips([])
            self.fallback.hide()
            return
        self.summary.setText(cal["summary"] + (f" · manual override {cal['override_px']:+.2f} px"
                                               if cal.get("override_px") else ""))
        notes = [(lvl if lvl != "info" else "info", t) for lvl, t in cal.get("notes", [])]
        self._set_chips([*cal.get("chips", []), *[n for n in notes if n[1] not in {c[1] for c in cal["chips"]}]])
        fb = cal.get("fallback")
        self.fallback.set("warn", fb or "")
        self.fallback.setVisible(bool(fb))
        self._fill_table(cal["anchors"])
        self._fill_residuals(cal)
        self._fill_sky(cal.get("sky"))
        self._fill_delta(cal)

    def _fill_table(self, anchors: list[dict[str, Any]]) -> None:
        self._filling = True
        order = {g: i for i, (g, _t) in enumerate(GROUPS)}
        rows = sorted(anchors, key=lambda a: (order[group_of(a)], a["lam"]))
        self.table.setRowCount(len(rows))
        mono = theme.mono_font(12)
        for r, a in enumerate(rows):
            g = group_of(a)
            use = QTableWidgetItem("")
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            use.setCheckState(Qt.CheckState.Checked if a["use"] else Qt.CheckState.Unchecked)
            use.setData(Qt.ItemDataRole.UserRole, a["key"])
            self.table.setItem(r, 0, use)
            note = a["rejected"] or ("emission, low weight" if a.get("emission") else "")
            if a.get("loo_px") is not None and not a["rejected"]:
                note = (note + "; " if note else "") + f"left out: {a['loo_px']:+.2f} px"
            vals = (f"{SYMBOL_NAME[SYMBOL[g]]} {dict(GROUPS)[g]}", a["name"], f"{a['lam']:.2f}", f"{a['x']:.2f}",
                    f"{a['resid_a']:+.2f}", f"{a['weight']:.2f}", note)
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(v)
                if c >= 3:
                    it.setFont(mono)
                if a["rejected"] or not a["use"]:
                    it.setForeground(pg.mkColor(theme.DIM))
                elif c == 1:
                    it.setForeground(pg.mkColor(colour_of(g)))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()
        self._filling = False

    def _clear(self, plot: pg.PlotWidget, items: list[Any]) -> None:
        for it in items:
            plot.removeItem(it)
        items.clear()

    def _fill_residuals(self, cal: dict[str, Any]) -> None:
        self._clear(self.res_plot, self.res_items)
        rms = float(cal.get("rms_a") or 0.0)
        self.res_band.setRegion((-rms, rms))
        for g, _t in GROUPS:
            pts = [a for a in cal["anchors"] if group_of(a) == g]
            if not pts:
                continue
            used = [a for a in pts if a["use"] and not a["rejected"]]
            off = [a for a in pts if not (a["use"] and not a["rejected"])]
            for sel, filled in ((used, True), (off, False)):
                if not sel:
                    continue
                c = colour_of(g)
                it = pg.ScatterPlotItem([a["lam"] for a in sel], [a["resid_a"] for a in sel], symbol=SYMBOL[g],
                                        size=10, pen=pg.mkPen(c, width=1.2),
                                        brush=pg.mkBrush(c) if filled else pg.mkBrush(None))
                self.res_plot.addItem(it)
                self.res_items.append(it)
        used = [abs(a["resid_a"]) for a in cal["anchors"] if a["use"] and not a["rejected"]]
        lim = max(3.0, 1.5 * max(used, default=0.0), 4 * rms)  # far outliers stay off the scale (see the table)
        self.res_plot.getPlotItem().setYRange(-lim, lim, padding=0.05)
        self.shape_key.setText("  ".join(f"{SYMBOL_NAME[SYMBOL[g]]} {t}" for g, t in GROUPS)
                               + "   open marker: not used · band: ± RMS")

    def _fill_sky(self, sky: dict[str, Any] | None) -> None:
        self._clear(self.sky_plot, self.sky_items)
        if not sky or sky.get("flux") is None:
            self.sky_curve.setData([], [])
            return
        flux = np.asarray(sky["flux"], dtype=np.float64)
        self.sky_curve.setData(np.arange(len(flux)), flux)
        if sky["peaks"]:
            xs = [p[0] for p in sky["peaks"]]
            ys = [float(flux[int(np.clip(round(x), 0, len(flux) - 1))]) for x in xs]
            it = pg.ScatterPlotItem(xs, ys, symbol="x", size=9, pen=pg.mkPen(theme.MUTED, width=1.2),
                                    brush=pg.mkBrush(theme.MUTED))
            self.sky_plot.addItem(it)
            self.sky_items.append(it)
        for name, _lam, x in sky["matches"]:
            y = float(flux[int(np.clip(round(x), 0, len(flux) - 1))])
            mk = pg.ScatterPlotItem([x], [y], symbol="o", size=10, pen=pg.mkPen(theme.TEAL, width=1.5),
                                    brush=pg.mkBrush(None))
            tx = pg.TextItem(name, color=theme.TEAL, anchor=(0.5, 1.2))
            tx.setFont(theme.mono_font(10))
            tx.setPos(x, y)
            for it in (mk, tx):
                self.sky_plot.addItem(it)
                self.sky_items.append(it)
        if sky["matches"]:
            xs = [m[2] for m in sky["matches"]]
            self.sky_plot.getPlotItem().setXRange(min(xs) - 250, max(xs) + 250, padding=0)

    def _fill_delta(self, cal: dict[str, Any]) -> None:
        self._clear(self.delta_plot, self.delta_items)
        d = cal.get("delta") or {}
        if not d:
            self.delta_curve.setData([], [])
            self.delta_nums.setText("")
            self.verdict.hide()
            return
        lam, delta = np.asarray(d["lam"], float), np.asarray(d["delta"], float)
        self.delta_curve.setData(lam, delta)
        self.delta_mean.setValue(float(np.mean(delta)))
        self.delta_nums.setText(
            f"Shift at the centre {d['center_px']:+.2f} px ({d['shift_a']:+.2f} Å) · Δ change across the range "
            f"{d['span_a']:+.2f} Å · max deviation from a constant {d['max_dev_a']:.2f} Å · implied scale "
            f"{d['scale_pct']:+.3f} %")
        lvl = {"pure_shift": "ok", "shift_scale": "info", "shape": "warn", "not_enough": "warn"}[d["verdict"]]
        self.verdict.set(lvl, d["text"])
        self.verdict.show()
        # each used anchor at its wavelength: the Δ it measures there (−D × dispersion)
        for g, _t in GROUPS:
            sel = [a for a in cal["anchors"] if group_of(a) == g and a["use"] and not a["rejected"]]
            if not sel:
                continue
            ys = [float(np.interp(a["lam"], lam, delta)) - a["resid_a"] for a in sel]
            c = colour_of(g)
            it = pg.ScatterPlotItem([a["lam"] for a in sel], ys, symbol=SYMBOL[g], size=10,
                                    pen=pg.mkPen(c, width=1.2), brush=pg.mkBrush(c))
            self.delta_plot.addItem(it)
            self.delta_items.append(it)
        ref = cal.get("sky_ref_residual_a")
        if ref is not None:
            self.ref_check.set("ok" if ref < 0.5 else "warn",
                               f"Reference solution vs its own sky lines: RMS {ref:.2f} Å ({cal['sky_ref_n']} lines)")
            self.ref_check.setVisible(True)

