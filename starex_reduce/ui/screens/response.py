"""Step 5 · Response: reference ÷ library → smoothed response → corrected, normalised, cropped."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core.pipeline import StageDef, StageRun
from starex_reduce.core.response import list_templates, pick_template
from starex_reduce.core.response_stage import (
    RESPONSE_FILE,
    params_of,
    preview_job,
    save_response,
    saved_responses,
    specs_for,
)

from .. import theme
from ..retheme import dark_style
from ..widgets.common import Card, LegendToggle, Segmented, StatusLine, label
from ..widgets.plot_guard import pg
from .base import StepScreen

if TYPE_CHECKING:
    from ..main_window import MainWindow

HALPHA = 6562.852


def num_edit() -> QLineEdit:
    e = QLineEdit()
    e.setValidator(QDoubleValidator(1000.0, 20000.0, 1, e))
    e.setFont(theme.mono_font(12))
    e.setAlignment(Qt.AlignmentFlag.AlignRight)
    e.setFixedWidth(90)
    return e


def plot_widget(ylabel: str, height: int) -> pg.PlotWidget:
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


def curve(colour: str, width: float = 1.5) -> pg.PlotDataItem:
    c = pg.PlotDataItem(pen=pg.mkPen(colour, width=width))
    c.setClipToView(True)
    c.setDownsampling(auto=True, method="peak")
    return c


class ResponseScreen(StepScreen):
    def __init__(self, stage: StageDef, total: int, host: MainWindow) -> None:
        super().__init__(stage, total, two_images=False)
        self.host = host
        self.result: dict[str, Any] | None = None
        self.nm = False
        self._loading = False
        self.mask_items: list[pg.LinearRegionItem] = []
        self.header.set_files("Target_W.fit", "Target_R.fit")
        row = QHBoxLayout()
        row.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._build_response_plot())
        left.addWidget(self._build_corrected_plot())
        row.addLayout(left, 1)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_response_card())
        right.addWidget(self._build_norm_card())
        right.addStretch(1)
        side = QWidget()
        side.setLayout(right)
        side.setFixedWidth(300)
        row.addWidget(side, 0, Qt.AlignmentFlag.AlignTop)
        self.body_layout.addLayout(row)
        self.body_layout.addStretch(1)

    def _update_chips(self, idx: int) -> None:
        self.header.set_files("Target_W.fit", "Target_R.fit")

    # ---- construction ------------------------------------------------------------------------
    def _build_response_plot(self) -> Card:
        card = Card("Build the response")
        self.lib_note = label("", "mono")
        card.head.insertWidget(1, self.lib_note)
        shows = QHBoxLayout()
        shows.setSpacing(4)  # keeps the legend inside the column on a 1100 px window
        shows.addWidget(label("SHOW", "eyebrow"))
        self.plot1 = plot_widget("Relative", 300)
        widths = {"obs": 1.5, "lib": 1.5, "raw": 1.0, "smooth": 2.5}
        self.c_obs, self.c_lib = curve(theme.SPRING, widths["obs"]), curve(theme.VIOLET, widths["lib"])
        self.c_raw, self.c_smooth = curve(theme.SKYBLUE, widths["raw"]), curve(theme.PINK, widths["smooth"])
        # night / red: the curves also differ by line style (observed solid, library dashed, raw dotted,
        # smoothed solid and thick), not by colour alone
        dark_style(self.c_lib, Qt.PenStyle.DashLine)
        dark_style(self.c_raw, Qt.PenStyle.DotLine)
        self.show1: dict[str, LegendToggle] = {}
        for key, text, colour, item in (("obs", "Observed reference", theme.SPRING, self.c_obs),
                                        ("lib", "Library", theme.VIOLET, self.c_lib),
                                        ("raw", "Raw response", theme.SKYBLUE, self.c_raw),
                                        ("smooth", "Smoothed response", theme.PINK, self.c_smooth)):
            cb = LegendToggle(text, colour, widths[key])
            cb.swatch.dark_style = getattr(item, "_sx_dark", (None,))[0]
            cb.toggled.connect(item.setVisible)  # view only: never recomputes
            shows.addWidget(cb)
            self.show1[key] = cb
            self.plot1.getPlotItem().addItem(item)
        shows.addStretch(1)
        card.body.addLayout(shows)
        card.body.addWidget(self.plot1)
        card.body.addWidget(label("Shaded: masked Balmer lines and telluric bands (left out of the fit). The pink "
                                  "curve is what gets applied; with the upper envelope it rides on the top of "
                                  "the raw response, so absorption dips do not pull it down.", "muted", wrap=True,
                                  style="font-size: 12px;"))
        return card

    def _build_corrected_plot(self) -> Card:
        card = Card("Corrected spectra")
        card.head.addWidget(label("SHOW", "eyebrow"))
        self.plot2 = plot_widget("Relative intensity", 280)
        self.c_tgt, self.c_ref = curve(theme.TEAL), curve(theme.VIOLET)
        dark_style(self.c_ref, Qt.PenStyle.DashLine)  # night / red: target solid, reference dashed
        self.show2: dict[str, LegendToggle] = {}
        for key, text, colour, item in (("target", "Target", theme.TEAL, self.c_tgt),
                                        ("reference", "Reference", theme.VIOLET, self.c_ref)):
            cb = LegendToggle(text, colour, 1.5)
            cb.swatch.dark_style = getattr(item, "_sx_dark", (None,))[0]
            cb.toggled.connect(item.setVisible)
            card.head.addWidget(cb)
            self.show2[key] = cb
            self.plot2.getPlotItem().addItem(item)
        self.unit = Segmented(["Å", "nm"])
        self.unit.changed.connect(self._unit_changed)
        card.head.addWidget(self.unit)
        self.ha = pg.InfiniteLine(pos=HALPHA, angle=90, movable=False,
                                  pen=pg.mkPen(theme.AMBER, width=1, style=Qt.PenStyle.DashLine),
                                  label="Hα", labelOpts={"position": 0.95, "color": theme.AMBER})
        self.plot2.getPlotItem().addItem(self.ha)
        card.body.addWidget(self.plot2)
        return card

    def _build_response_card(self) -> Card:
        card = Card("Response")
        g = QGridLayout()
        g.addWidget(label("Library", "muted"), 0, 0)
        self.library = QComboBox()
        self.library.setMinimumWidth(170)
        self.library.currentIndexChanged.connect(self._library_changed)
        g.addWidget(self.library, 0, 1, Qt.AlignmentFlag.AlignRight)
        card.body.addLayout(g)
        self.mask_balmer = QCheckBox("Mask Balmer lines")
        self.mask_telluric = QCheckBox("Mask telluric bands")
        self.airmass = QCheckBox("Airmass correction")
        for cb, key in ((self.mask_balmer, "mask_balmer"), (self.mask_telluric, "mask_telluric"),
                        (self.airmass, "airmass")):
            cb.toggled.connect(lambda on, k=key: self._set(**{k: on}))
            card.body.addWidget(cb)
        self.airmass_note = label("", "muted", wrap=True, style="font-size: 12px; padding-left: 22px;")
        card.body.addWidget(self.airmass_note)
        sg = QGridLayout()
        sg.addWidget(label("Smoothing", "muted"), 0, 0)
        self.smoothing = QComboBox()
        self.smoothing.addItems(["low", "medium", "high", "very high"])
        self.smoothing.setToolTip("Higher smoothing follows fewer bumps: use it when telluric absorption "
                                  "still leaks into the pink curve")
        self.smoothing.currentTextChanged.connect(lambda t: self._set(smoothing=t))
        sg.addWidget(self.smoothing, 0, 1, Qt.AlignmentFlag.AlignRight)
        card.body.addLayout(sg)
        self.envelope = QCheckBox("Follow the upper envelope")
        self.envelope.setToolTip("Absorption only lowers the flux, so the response is the top of the raw "
                                 "response: dips (telluric wings, lines) are ignored and the masked gaps are "
                                 "bridged smoothly. Off: the median of each bin, as before.")
        self.envelope.toggled.connect(lambda on: self._set(envelope=on))
        card.body.addWidget(self.envelope)
        card.body.addWidget(label("(ignore absorption dips)", "muted",
                                  style="font-size: 12px; padding-left: 22px; margin-top: -4px;"))
        card.body.addWidget(label("The pink curve is what gets applied to the target.", "muted", wrap=True,
                                  style="font-size: 12px;"))
        self.notes = StatusLine("warn", "")
        self.notes.hide()
        card.body.addWidget(self.notes)
        btns = QHBoxLayout()
        self.save_btn = QPushButton("Save response")
        self.save_btn.setToolTip("Keep this response for later nights (Library → Saved …)")
        self.save_btn.clicked.connect(lambda: self.save())
        self.none_btn = QPushButton("No response")
        self.none_btn.setCheckable(True)
        self.none_btn.setToolTip("Skip the response: normalise and crop the counts only (diagnostics)")
        self.none_btn.toggled.connect(lambda on: self._set(no_response=on))
        btns.addWidget(self.save_btn)
        btns.addWidget(self.none_btn)
        card.body.addLayout(btns)
        return card

    def _build_norm_card(self) -> Card:
        card = Card("Normalise and crop")
        g = QGridLayout()
        self.norm_from, self.norm_to, self.crop_lo, self.crop_hi = (num_edit() for _ in range(4))
        for r, (text, w_) in enumerate((("Norm. from (Å)", self.norm_from), ("Norm. to (Å)", self.norm_to),
                                        ("Crop low (Å)", self.crop_lo), ("Crop high (Å)", self.crop_hi))):
            g.addWidget(label(text, "muted"), r, 0)
            g.addWidget(w_, r, 1, Qt.AlignmentFlag.AlignRight)
            w_.editingFinished.connect(self._norm_edited)
        card.body.addLayout(g)
        self.norm_error = StatusLine("err", "")
        self.norm_error.hide()
        card.body.addWidget(self.norm_error)
        return card

    # ---- state -------------------------------------------------------------------------------
    def params(self) -> dict[str, Any]:
        return params_of(self.host.session, self.host.profile, {**self.host.settings.data, "site": self.host.site()})

    def _set(self, **values: Any) -> None:
        if self._loading:
            return
        s = self.host.session
        p = {**self.params(), **values}
        s.params["response"] = p
        self.host.inputs_changed()
        self.preview()

    def load_session(self) -> None:
        self._loading = True
        p = self.params()
        self._fill_library(p)
        self.mask_balmer.setChecked(bool(p["mask_balmer"]))
        self.mask_telluric.setChecked(bool(p["mask_telluric"]))
        self.airmass.setChecked(bool(p["airmass"]))
        self.smoothing.setCurrentText(p["smoothing"])
        self.envelope.setChecked(bool(p.get("envelope", True)))
        self.none_btn.setChecked(bool(p["no_response"]))
        self.norm_from.setText(f"{p['norm'][0]:g}")
        self.norm_to.setText(f"{p['norm'][1]:g}")
        self.crop_lo.setText(f"{p['crop'][0]:g}")
        self.crop_hi.setText(f"{p['crop'][1]:g}")
        self._loading = False
        self.result = None
        if self.isVisible():
            self.preview()

    def _fill_library(self, p: dict[str, Any]) -> None:
        self.library.clear()
        auto, note = pick_template(p["_ctx"]["sptype"])
        sp = p["_ctx"]["sptype"] or "no spectral type"
        self.library.addItem(f"Auto · {auto.label}", "auto")
        self.library.setItemData(0, f"Pickles {auto.label}, picked from the reference star's SIMBAD type ({sp}). "
                                    + note, Qt.ItemDataRole.ToolTipRole)
        letter = None
        for t in list_templates():  # O → M, then subtype, then luminosity class I → V
            if t.letter != letter:
                self.library.insertSeparator(self.library.count())
                letter = t.letter
            self.library.addItem(f"Pickles · {t.label}", t.stem)
        for path in saved_responses():
            self.library.addItem(f"Saved · {path.stem.removeprefix('response_')}", f"saved:{path.name}")
        key = p["source"] if str(p["source"]).startswith("saved:") else p["template"]
        i = self.library.findData(key)
        self.library.setCurrentIndex(max(i, 0))

    def on_shown(self) -> None:
        if self.result is None:
            self.preview()

    def preview(self) -> None:
        specs, problems = self._specs()
        if specs:
            self.host.runner.submit("preview.response.Target", preview_job, specs[0])
        elif problems:
            self.notes.set("warn", problems[0])
            self.notes.show()

    def _specs(self) -> tuple[list, list[str]]:
        s = self.host.session
        if s.folder is None:
            return [], []
        if not all((s.folder / f"{i}_W.fit").exists() for i in ("Target", "Reference")):
            return [], ["Run Wavelength first: the response needs Target_W.fit and Reference_W.fit."]
        return specs_for(s, self.host.profile, {**self.host.settings.data, "site": self.host.site()})

    def on_preview(self, _image: str, res: dict[str, Any]) -> None:
        self.result = res
        self._show()

    def stage_done(self, run: StageRun) -> None:
        self.result = run.data
        self._show()

    # ---- display -----------------------------------------------------------------------------
    def _x(self, lam: np.ndarray) -> np.ndarray:
        return lam / 10.0 if self.nm else lam

    def _show(self) -> None:
        r = self.result
        if r is None:
            return
        p1 = self.plot1.getPlotItem()
        for it in self.mask_items:
            p1.removeItem(it)
        self.mask_items = []
        lam = r["lam"]
        if "smooth" in r:
            def rel(y: np.ndarray) -> np.ndarray:
                top = float(np.nanpercentile(y, 99.5)) or 1.0
                return y / top
            self.c_obs.setData(lam, rel(r["observed"]))
            self.c_lib.setData(lam, rel(r["library"]))
            self.c_raw.setData(lam, r["raw"])
            self.c_smooth.setData(lam, r["smooth"])
            for _name, a, b in r["regions"]:
                reg = pg.LinearRegionItem((a, b), movable=False, brush=pg.mkBrush(theme.with_alpha(theme.AMBER, 28)),
                                          pen=pg.mkPen(None))
                reg.setZValue(-5)
                p1.addItem(reg)
                self.mask_items.append(reg)
            p1.setYRange(0, 1.1, padding=0)
            self.lib_note.setText(f"{r['label']} · {len(r['regions'])} regions masked")
        else:
            for c in (self.c_obs, self.c_lib, self.c_raw, self.c_smooth):
                c.setData([], [])
            self.lib_note.setText(r["label"])
        x = self._x(r["lam_out"])
        self.c_tgt.setData(x, r["target"])
        self.c_ref.setData(x, r["reference"])
        self.ha.setValue(HALPHA / 10 if self.nm else HALPHA)
        self.plot2.getPlotItem().setLabel("bottom", "Wavelength (nm)" if self.nm else "Wavelength (Å)")
        xt, xr = r.get("x_target"), r.get("x_reference")
        if xt is not None and xr is not None:
            self.airmass_note.setText(f"Reference {xr:.2f} · target {xt:.2f}" + ("" if r["ext"] else " (not applied)"))
        else:
            self.airmass_note.setText(" ".join(r.get("airmass_notes") or []) or "")
        notes = [n for n in r["notes"] if n not in (r.get("airmass_notes") or [])]
        self.notes.set("warn", " ".join(notes))
        self.notes.setVisible(bool(notes))
        self.save_btn.setEnabled("smooth" in r)

    def _unit_changed(self, idx: int) -> None:
        pi = self.plot2.getPlotItem()
        lo, hi = pi.vb.viewRange()[0]
        k = 10.0 if self.nm else 1.0
        self.nm = idx == 1
        self._show()
        k2 = 0.1 if self.nm else 1.0
        pi.setXRange(lo * k * k2, hi * k * k2, padding=0)

    # ---- actions -----------------------------------------------------------------------------
    def _library_changed(self, _i: int) -> None:
        if self._loading:
            return
        key = self.library.currentData()
        if str(key).startswith("saved:"):
            self._set(source=key)
        else:
            self._set(source="library", template=key)

    def _norm_edited(self) -> None:
        if self._loading:
            return
        try:
            vals = [float(e.text()) for e in (self.norm_from, self.norm_to, self.crop_lo, self.crop_hi)]
        except ValueError:
            return
        n0, n1, c0, c1 = vals
        if not (n0 < n1 and c0 < c1 and c0 <= n0 and n1 <= c1):
            self.norm_error.set("err", "Use from < to, and keep the normalisation window inside the crop.")
            self.norm_error.show()
            return
        self.norm_error.hide()
        prof = self.host.profile  # the spectrograph's defaults for new sessions
        if (tuple(prof.norm_a), tuple(prof.crop_a)) != ((n0, n1), (c0, c1)):
            prof.norm_a, prof.crop_a = (n0, n1), (c0, c1)
            prof.save()
        self._set(norm=[n0, n1], crop=[c0, c1])

    def save(self) -> None:
        s = self.host.session
        src = s.folder / RESPONSE_FILE if s.folder else None
        if src is None or not src.exists():
            s.log("Save response: press Run first (response.fit is written by the run)")
            return
        name = (s.inputs["reference"].get("name") or "reference").replace(" ", "")
        dst = save_response(src, name)
        s.log(f"Response saved as {dst.name} (choose it under Library for later nights)")
        self._loading = True
        self._fill_library(self.params())
        self._loading = False
