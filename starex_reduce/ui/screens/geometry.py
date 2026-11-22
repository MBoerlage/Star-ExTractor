"""Step 2 · Geometry: straighten the spectrum (tilt detect / draw angle), optional slant, flip, hot pixels."""
from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core.extract_stage import bands_of
from starex_reduce.core.geometry import drawn_angle, drawn_slant
from starex_reduce.core.geometry_stage import (
    RESIDUAL_WARN_DEG,
    detect_job,
    load_display_job,
    params_of,
    slant_detect_job,
    slant_for,
    tilt_for,
)
from starex_reduce.core.pipeline import IMAGES, StageDef, StageRun, input_name, output_name

from .. import theme
from ..widgets.common import Card, Segmented, StatusLine, label
from ..widgets.image_viewer import DrawnLine, ImageViewer, PointsOverlay
from ..widgets.plot_guard import pg
from .base import StepScreen

if TYPE_CHECKING:
    from ..main_window import MainWindow

STEPS = {
    "tilt": ("Press at one end of the spectrum.", "Drag along it. The line follows the cursor.",
             "Release. The angle is measured and the frame is straightened."),
    "slant": ("Press at one end of a leaning sky line or streak (zoom in first).",
              "Drag along it. The line follows the cursor.",
              "Release. The slant is measured in image pixels, whatever the zoom."),
}
ZOOMS = (("Full height", "full"), ("Fit to spectrum", "fit"), ("×2", 2), ("×4", 4), ("×8", 8), ("×16", 16))


FROM_TARGET = "target"  # tilt source of a reference that uses the target's angle

class GeometryScreen(StepScreen):
    def __init__(self, stage: StageDef, total: int, host: MainWindow) -> None:
        super().__init__(stage, total)
        self.host = host
        self.display: dict[str, dict[str, Any]] = {}  # image -> load_display_job result
        self.results: dict[str, dict[str, Any]] = {}  # image -> last geometry run data
        self._loading = False
        self._draw_target = "tilt"  # which card the drawn line is for: tilt | slant
        self.slant_results: dict[str, dict[str, Any]] = {}  # image -> last slant_detect_job result
        self.slant_notes: dict[str, str] = {}  # image -> how the slant value was obtained

        row = QHBoxLayout()
        row.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._build_viewer_card())
        left.addWidget(self._build_result_card())
        row.addLayout(left, 1)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_saved_card())
        right.addWidget(self._build_tilt_card())
        right.addWidget(self._build_slant_card())
        right.addWidget(self._build_pixels_card())
        right.addStretch(1)
        side = QWidget()
        side.setLayout(right)
        side.setFixedWidth(300)
        row.addWidget(side, 0, Qt.AlignmentFlag.AlignTop)
        self.body_layout.addLayout(row)
        self.body_layout.addStretch(1)
        self.header.image_changed.connect(self._image_changed)

    # ---- construction ------------------------------------------------------------------------
    def _build_viewer_card(self) -> Card:
        card = Card()
        top = QHBoxLayout()
        self.tabs = Segmented(["Target stack", "Reference stack"])
        self.tabs.changed.connect(self._tab_changed)
        top.addWidget(self.tabs)
        top.addStretch(1)
        for text, tip, fn in (("Fit && auto", "Full height", lambda: self._zoom("full")),
                              ("Fit", "Fit to spectrum", lambda: self._zoom("fit")),
                              ("×1", "True aspect (rows as wide as columns)", lambda: self._zoom(1)),
                              ("↔", "Show the full width", lambda: self._zoom("full"))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            top.addWidget(b)
        self.readout = label("", "mono")
        self.readout.setMinimumWidth(190)
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.readout)
        card.body.addLayout(top)

        oc = QHBoxLayout()
        self.view_toggle = Segmented(["Original", "Corrected"])
        self.view_toggle.changed.connect(lambda i: self.viewer.show_corrected(i == 1))
        oc.addWidget(self.view_toggle)
        oc.addWidget(label("After a run the view switches to Corrected. Flip back to compare.", "muted",
                           style="font-size: 12px;"))
        oc.addStretch(1)
        from PySide6.QtWidgets import QCheckBox

        self.level_cb = QCheckBox("Level line")
        self.level_cb.setToolTip("A horizontal blue line to compare the spectrum against: drag it up or down. A view "
                                 "aid only; it changes nothing.")
        self.level_cb.setChecked(True)
        self.level_cb.toggled.connect(lambda on: (self.level.setVisible(on), self.level.label.valueChanged()))
        oc.addWidget(self.level_cb)
        card.body.addLayout(oc)

        zr = QHBoxLayout()
        zr.addWidget(label("Y ZOOM", "eyebrow"))
        self.zoom_chips = Segmented([t for t, _ in ZOOMS])
        for b in self.zoom_chips.buttons:
            b.setProperty("seg", "only")
        self.zoom_chips.layout().setSpacing(6)
        self.zoom_chips.changed.connect(lambda i: self.viewer.set_zoom(ZOOMS[i][1]))
        zr.addWidget(self.zoom_chips)
        zr.addStretch(1)
        self.rows_label = label("", "mono")
        zr.addWidget(self.rows_label)
        card.body.addLayout(zr)

        self.viewer = ImageViewer()
        self.viewer.setMinimumHeight(240)
        self.viewer.readout.connect(self.readout.setText)
        self.viewer.rows_changed.connect(lambda a, b, n: self.rows_label.setText(f"rows {a}–{b} of {n}"))
        self.viewer.line_moved.connect(self._line_moving)
        self.viewer.line_drawn.connect(self._line_drawn)
        self.line = DrawnLine(self.viewer.vb)
        self.points = PointsOverlay(self.viewer.vb)
        # a floating horizontal reference: drag it onto the spectrum to see how level the correction is
        self.level = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen(theme.BLUE, width=2),
                                     hoverPen=pg.mkPen(theme.TEXT, width=2),
                                     label="row {value:.0f}", labelOpts={"position": 0.03, "color": theme.BLUE})
        self.level.setZValue(8)
        self.level.hide()
        self.viewer.vb.addItem(self.level, ignoreBounds=True)
        self._level_placed = False
        card.body.addWidget(self.viewer, 1)

        self.steps = QWidget()
        sl = QHBoxLayout(self.steps)
        sl.setContentsMargins(0, 0, 0, 0)
        self.step_labels: list[QLabel] = []
        for n, text in zip((1, 2, 3), STEPS["tilt"], strict=True):
            badge = label(str(n), style=f"background: {theme.AMBER}; color: {theme.AMBER_TEXT}; border-radius: 9px;"
                                        "min-width: 18px; max-width: 18px; min-height: 18px; max-height: 18px;"
                                        "font-size: 11px; font-weight: 600;")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sl.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            lab = label(text, wrap=True)
            self.step_labels.append(lab)
            sl.addWidget(lab, 1)
        self.steps.hide()
        card.body.addWidget(self.steps)
        card.body.addWidget(label(
            "Y zoom is a view only and never changes the file. X stays fixed. Roll the wheel over the image or the "
            "frame strip to zoom Y, or drag the amber window in the strip to move up and down. Angles are measured "
            "in real pixels, so zooming does not change them.", "muted", wrap=True, style="font-size: 12px;"))
        return card

    def _build_result_card(self) -> Card:
        card = Card("Result · corrected, same rows")
        card.title.setStyleSheet("font-size: 12px; font-weight: 400;")
        self.result_note = label("", "mono", style=f"color: {theme.GREEN};")
        card.head.addWidget(self.result_note)
        self.result = ImageViewer(strip=False, ruler=False)
        self.result.setFixedHeight(110)
        self.result.vb.setMouseEnabled(x=False, y=False)
        self.viewer.vb.sigYRangeChanged.connect(lambda *_: self._sync_result())
        card.body.addWidget(self.result)
        return card

    def _build_saved_card(self) -> Card:
        card = Card("Geometry for this spectrograph")
        self.use_saved = QCheckBox()
        self.use_saved.toggled.connect(self._use_saved_toggled)
        self.saved_sub = label("Tilt barely changes on a fixed instrument", "muted", wrap=True,
                               style="font-size: 12px;")
        card.body.addWidget(self.use_saved)
        card.body.addWidget(self.saved_sub)
        self.saved_status = StatusLine("off", "")
        card.body.addWidget(self.saved_status)
        return card

    def _build_tilt_card(self) -> Card:
        card = Card("Tilt")
        modes = QHBoxLayout()
        self.detect_btn = QPushButton("Detect")
        self.detect_btn.clicked.connect(self.detect)
        self.draw_btn = QPushButton("Draw angle")
        self.draw_btn.setCheckable(True)
        self.draw_btn.toggled.connect(self._draw_toggled)
        modes.addWidget(self.detect_btn)
        modes.addWidget(self.draw_btn)
        modes.addStretch(1)
        card.body.addLayout(modes)
        card.body.addWidget(label("Detect finds the spectrum for you. Draw angle lets you place the line yourself.",
                                  "muted", wrap=True, style="font-size: 12px;"))
        g = QGridLayout()
        g.addWidget(label("Angle (°)", "muted"), 0, 0)
        self.angle = AngleEdit()
        self.angle.returnPressed.connect(self.preview)
        g.addWidget(self.angle, 0, 1, Qt.AlignmentFlag.AlignRight)
        card.body.addLayout(g)
        self.tilt_note = label("", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.tilt_note)
        btns = QHBoxLayout()
        self.preview_btn = QPushButton("Preview")
        self.preview_btn.setToolTip("Straighten this image with the angle above")
        self.preview_btn.clicked.connect(self.preview)
        self.set_btn = QPushButton("Set values")
        self.set_btn.setProperty("role", "primary")
        self.set_btn.setToolTip("Use this angle for both images and save it as the spectrograph's geometry")
        self.set_btn.clicked.connect(self.set_values)
        self.redraw_btn = QPushButton("Redraw")
        self.redraw_btn.clicked.connect(self.redraw)
        for b in (self.preview_btn, self.set_btn, self.redraw_btn):
            b.setStyleSheet("padding: 8px 10px;")
            btns.addWidget(b)
        card.body.addLayout(btns)
        return card

    def _build_slant_card(self) -> Card:
        card = Card("Slant")
        self.slant_on = QCheckBox("Correct slant")
        self.slant_on.setToolTip("Shear the frame so lines of one wavelength stand straight (with the tilt, one "
                                 "resample about the spectrum row). Off by default.")
        self.slant_on.toggled.connect(lambda on: self._set_param("slant_on", on))
        card.body.addWidget(self.slant_on)
        self.slant_sub = label("Off: only the tilt is removed. Slant = lines of one wavelength lean after the "
                               "tilt is fixed (a shear).", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.slant_sub)
        modes = QHBoxLayout()
        self.slant_detect_btn = QPushButton("Detect")
        self.slant_detect_btn.setToolTip("Measure the slant from the sky rows above and below the spectrum")
        self.slant_detect_btn.clicked.connect(self.detect_slant)
        self.slant_draw_btn = QPushButton("Draw angle")
        self.slant_draw_btn.setCheckable(True)
        self.slant_draw_btn.setToolTip("Draw along a leaning sky line or streak")
        self.slant_draw_btn.toggled.connect(self._slant_draw_toggled)
        modes.addWidget(self.slant_detect_btn)
        modes.addWidget(self.slant_draw_btn)
        modes.addStretch(1)
        card.body.addLayout(modes)
        g = QGridLayout()
        g.addWidget(label("Angle (°)", "muted"), 0, 0)
        self.slant_angle = AngleEdit()
        self.slant_angle.returnPressed.connect(
            lambda: self._apply_slant(self.image, self.slant_angle.value(), "manual",
                                      f"Typed slant: {self.slant_angle.value():+.3f}°"))
        g.addWidget(self.slant_angle, 0, 1, Qt.AlignmentFlag.AlignRight)
        card.body.addLayout(g)
        self.slant_status = StatusLine("off", "Not measured yet.")
        card.body.addWidget(self.slant_status)
        self.slant_impact = label("", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.slant_impact)
        return card

    def _build_pixels_card(self) -> Card:
        card = Card("Pixels")
        self.flip_x = QCheckBox("Flip so blue is on the left")
        self.flip_y = QCheckBox("Flip vertically")
        self.hot = QCheckBox("Remove hot pixels and cosmic rays")
        for cb, key in ((self.flip_x, "flip_x"), (self.flip_y, "flip_y"), (self.hot, "hot")):
            cb.toggled.connect(lambda on, k=key: self._set_param(k, on))
            card.body.addWidget(cb)
        self.hot_note = label("", "muted", style="font-size: 12px; padding-left: 22px;")
        card.body.addWidget(self.hot_note)
        return card

    # ---- state -------------------------------------------------------------------------------
    @property
    def image(self) -> str:
        return IMAGES[self.header.image_idx]

    def params(self) -> dict[str, Any]:
        return params_of(self.host.session, self.host.profile)

    def load_session(self) -> None:
        self._loading = True
        p = self.params()
        prof = self.host.profile
        self.use_saved.setChecked(bool(p.get("use_saved")))
        self.use_saved.setText(f"Use saved geometry · {prof.tilt_date}" if prof.has_geometry
                               else "Use saved geometry (none saved yet)")
        self.flip_x.setChecked(bool(p.get("flip_x")))
        self.flip_y.setChecked(bool(p.get("flip_y")))
        self.hot.setChecked(bool(p.get("hot", True)))
        self.slant_on.setChecked(bool(p.get("slant_on")))
        self._loading = False
        self.display.clear()
        self.results.clear()
        self.slant_results.clear()
        self.slant_notes.clear()
        if self.isVisible():
            self._refresh_image()

    def on_shown(self) -> None:
        self._refresh_image()

    def _image_changed(self, idx: int) -> None:
        self.tabs.set_index(idx)
        self._refresh_image()

    def _tab_changed(self, idx: int) -> None:
        if idx != self.header.image_idx:
            self.header._step_image(idx - self.header.image_idx)

    def _refresh_image(self) -> None:
        """Show the current image; (re)load display arrays in a worker if the files changed."""
        s = self.host.session
        img = self.image
        p = self.params()
        tilt = p.get("per_image", {}).get(img, {}).get("tilt")
        saved = p.get("saved_tilt") if p.get("use_saved") else None
        last = (self.results.get(img) or {}).get("tilt", (self.display.get(img) or {}).get("corr_tilt"))
        value = next((v for v in (tilt, last, saved) if v is not None), 0.0)
        self.angle.setValue(float(value))
        per = p.get("per_image", {}).get(img, {})
        self.tilt_note.setText(f"From the target: {per['tilt']:.3f}° (first guess). Detect or Draw angle to refine "
                               "it for the reference." if per.get("source") == FROM_TARGET else "")
        slant = next((v for v in (p.get("slant"), p.get("saved_slant") if p.get("use_saved") else None)
                      if v is not None), None)
        self.slant_angle.setValue(float(slant or 0.0))
        self._update_notes()
        if s.folder is None:
            self.viewer.set_images(None, None)
            return
        src = s.folder / (input_name("geometry", img) or "")
        out = s.folder / output_name("geometry", img)
        corr = out if s.image_done("geometry", img) else None
        cached = self.display.get(img)
        fresh = (cached is not None and cached.get("orig_mtime") == _mtime(src)
                 and cached.get("corr_mtime") == (_mtime(corr) if corr else None)
                 and cached.get("flip") == (p.get("flip_x"), p.get("flip_y")))
        if fresh:
            self._show(cached)
        elif src.exists():
            self.host.runner.submit(f"view.geometry.{img}", load_display_job, img, src, corr,
                                    bool(p.get("flip_x")), bool(p.get("flip_y")))
        else:
            self.viewer.set_images(None, None)
            self.result.set_images(None, None)

    def on_display(self, res: dict[str, Any]) -> None:
        p = self.params()
        res["flip"] = (p.get("flip_x"), p.get("flip_y"))
        res.setdefault("corr_mtime", None)
        self.display[res["image"]] = res
        if res["image"] == self.image:
            self._show(res)

    def _show(self, res: dict[str, Any]) -> None:
        self.viewer.set_images(res["orig"], res["corr"])
        if not self._level_placed:  # first image: on the spectrum row; afterwards it stays where it was dragged
            d = res["corr"] or res["orig"]
            row = getattr(d, "spectrum_row", None)
            self.level.setValue(float(row) + 0.5 if row is not None else sum(self.viewer.y_range()) / 2)
            self._level_placed = True
        self.level.setVisible(self.level_cb.isChecked())
        self.level.label.valueChanged()  # pyqtgraph skips label updates while the line is hidden
        corrected = res["corr"] is not None
        self.view_toggle.buttons[1].setEnabled(corrected)
        self.view_toggle.set_index(1 if corrected and self.viewer.which == "corr" else 0)
        self.result.set_images(res["corr"] or res["orig"], None)
        self._sync_result()
        r = self.results.get(self.image)
        self.points.set(r["points"] if r and not corrected else [])

    def _sync_result(self) -> None:
        y0, y1 = self.viewer.y_range()
        self.result.vb.setYRange(y0, y1, padding=0)

    def _zoom(self, mode: str | int) -> None:
        self.viewer.set_zoom(mode)

    def _update_notes(self) -> None:
        r = self.results.get(self.image)
        st = self.host.session.status["geometry"]
        p = self.params()
        if r:
            self.result_note.setText(f"tilt {r['tilt']:.2f}° · slant {r['slant']:+.2f}° removed"
                                     if r.get("slant") else f"tilt {r['tilt']:.2f}° removed")
            self.hot_note.setText(f"{r['hot']:,} pixels replaced in this stack".replace(",", " ")
                                  if p.get("hot") else "")
        else:
            self.result_note.setText("" if st.status != "valid" else "corrected")
            self.hot_note.setText("")
        self._update_slant_notes()
        res = (r or {}).get("residual_deg")
        if not p.get("use_saved") or p.get("saved_tilt") is None:
            self.saved_status.set("off", "No saved values in use: the tilt is detected for each image."
                                  if p.get("use_saved") else "Saved values are off.")
        elif res is None:
            saved_slant = (f" Saved slant {p['saved_slant']:+.2f}°." if p.get("saved_slant") is not None else "")
            self.saved_status.set("off", f"Saved tilt {p['saved_tilt']:.3f}°.{saved_slant} Run to check it on "
                                         "this night.")
        elif abs(res) <= RESIDUAL_WARN_DEG:
            self.saved_status.set("ok", f"Saved values still fit this night. Residual tilt {abs(res):.2f}°.")
        else:
            self.saved_status.set("warn", f"Residual tilt {res:+.2f}° is larger than {RESIDUAL_WARN_DEG}°. "
                                          "Press Detect for this night.")

    # ---- actions -----------------------------------------------------------------------------
    def _set_param(self, key: str, value: Any) -> None:
        if self._loading:
            return
        s = self.host.session
        s.params["geometry"] = {**self.params(), key: value}
        if key in ("flip_x", "flip_y"):
            self.host.profile.flip_x = bool(s.params["geometry"].get("flip_x"))
            self.host.profile.flip_y = bool(s.params["geometry"].get("flip_y"))
            self.host.profile.save()
            self.display.clear()
            self.results.clear()
        self.host.inputs_changed()
        self._refresh_image()

    def _use_saved_toggled(self, on: bool) -> None:
        self._set_param("use_saved", on)

    def detect(self) -> None:
        s = self.host.session
        if s.folder is None:
            return
        src = s.folder / (input_name("geometry", self.image) or "")
        if not src.exists():
            s.log("Detect: load the pair in Select first")
            return
        p = self.params()
        self.detect_btn.setEnabled(False)
        self.host.runner.submit(f"detect.{self.image}", detect_job, src, bool(p.get("flip_x")), bool(p.get("flip_y")))

    def on_detected(self, image: str, res: dict[str, Any]) -> None:
        self.detect_btn.setEnabled(True)
        self.host.session.log(f"Tilt {image}: detected {res['angle']:.3f}° (±{res['residual_px']:.2f} px over "
                              f"{res['span']:.0f} px)")
        self.results.setdefault(image, {})["points"] = res["points"]
        self._apply_angle(image, res["angle"], "detect",
                          f"Detected: {res['angle']:.3f}° over {res['span']:,.0f} px".replace(",", " "))

    def _draw_toggled(self, on: bool, target: str = "tilt") -> None:
        if on:
            self._draw_target = target
            other = self.slant_draw_btn if target == "tilt" else self.draw_btn
            other.blockSignals(True)
            other.setChecked(False)
            other.blockSignals(False)
            for lab, text in zip(self.step_labels, STEPS[target], strict=True):
                lab.setText(text)
        self.viewer.set_draw_mode(on)
        self.steps.setVisible(on)
        if on:
            if target == "tilt":  # the tilt is drawn on the stack as it is
                self.view_toggle.set_index(0)
                self.viewer.show_corrected(False)
            # slant: either view; _slant_drawn adds what that view already has removed
            self.line.hide()

    def redraw(self) -> None:
        self.line.hide()
        self.draw_btn.setChecked(True)

    def _slant_draw_toggled(self, on: bool) -> None:
        self._draw_toggled(on, "slant")

    def _line_moving(self, x0: float, y0: float, x1: float, y1: float) -> None:
        # x/y arrive in image pixels (ViewBox coordinates), so the stretched Y zoom never skews the angle
        try:
            a = (drawn_slant if self._draw_target == "slant" else drawn_angle)(x0, y0, x1, y1)
        except ValueError:
            return
        self.line.set(x0, y0, x1, y1, f"{a:+.2f}°" if self._draw_target == "slant" else f"{a:.2f}°")

    def _line_drawn(self, x0: float, y0: float, x1: float, y1: float) -> None:
        if self._draw_target == "slant":
            self._slant_drawn(x0, y0, x1, y1)
            return
        try:
            a = drawn_angle(x0, y0, x1, y1)
        except ValueError:
            return
        span = math.hypot(x1 - x0, y1 - y0)
        self.draw_btn.setChecked(False)  # leaves draw mode (hides the steps) but keeps the line
        self.line.set(x0, y0, x1, y1, f"{a:.2f}° · straightening")
        self.host.session.log(f"Tilt: drawn line, {a:.2f}° over {span:,.0f} px".replace(",", " "))
        note = f"From the drawn line: {a:.2f}° over {span:,.0f} px".replace(",", " ")
        self._apply_angle(self.image, a, "drawn", note)

    def preview(self) -> None:
        self._apply_angle(self.image, self.angle.value(), "manual", f"Typed angle: {self.angle.value():.3f}°")

    def _apply_angle(self, image: str, angle: float, source: str, note: str) -> None:
        self.angle.setValue(angle)
        self.tilt_note.setText(note)
        self.host.session.set_image_params("geometry", image, {"tilt": round(angle, 5), "source": source})
        self._target_as_first_guess(image, angle)
        self.host.inputs_changed()
        self.host.run_stage("geometry", [image])

    def _target_as_first_guess(self, image: str, angle: float) -> None:
        """The target's angle is the reference's first guess (same spectrograph, same night): used for the
        reference until it gets an angle of its own (detected, drawn or typed on the Reference stack)."""
        if image != "Target":
            return
        ref = self.params().get("per_image", {}).get("Reference", {})
        if ref.get("tilt") is None or ref.get("source") == FROM_TARGET:
            self.host.session.set_image_params("geometry", "Reference", {"tilt": round(angle, 5),
                                                                        "source": FROM_TARGET})
            self.host.session.log(f"Tilt Reference: {angle:.3f}° taken from the target as a first guess")

    # ---- slant -------------------------------------------------------------------------------
    def detect_slant(self) -> None:
        s = self.host.session
        if s.folder is None:
            return
        src = s.folder / (input_name("geometry", self.image) or "")
        if not src.exists():
            s.log("Slant: load the pair in Select first")
            return
        p = self.params()
        tilt, _src = tilt_for(p, self.image)
        self.slant_detect_btn.setEnabled(False)
        self.slant_status.set("off", "Measuring the slant…")
        self.host.runner.submit(f"slant.{self.image}", slant_detect_job, src, bool(p.get("flip_x")),
                                bool(p.get("flip_y")), tilt)

    def on_slant(self, image: str, res: dict[str, Any]) -> None:
        self.slant_detect_btn.setEnabled(True)
        s = self.host.session
        if not res["ok"]:
            s.log(f"Slant {image}: could not be measured ({res['reason']}) in {res['seconds']:.1f} s")
            self.slant_results[image] = res
            if image == self.image:
                self._update_slant_notes()
            return
        s.log(f"Slant {image}: detected {res['angle']:+.3f}° ± {res['sigma']:.3f}° from {res['n']} block pairs "
              f"(quality {res['quality']:.2f}, tilt {res['tilt']:.3f}° removed first) in {res['seconds']:.1f} s")
        self.slant_results[image] = res
        self._apply_slant(image, res["angle"], "detect", res["text"])

    def on_slant_failed(self, msg: str) -> None:
        self.slant_detect_btn.setEnabled(True)
        self.slant_status.set("warn", f"Slant could not be measured: {msg}")

    def _view_offset(self) -> tuple[float, str]:
        """What the shown view has already removed, to add to a lean drawn on it: on the stack as it is the
        tilt (a line leaning by s after straightening leans by s − tilt before it), on the corrected image
        the slant already applied."""
        disp = self.display.get(self.image) or {}
        if self.viewer.which == "corr" and disp.get("corr") is not None:
            return float(disp.get("corr_slant") or 0.0), "corrected"
        tilt, _src = tilt_for(self.params(), self.image)
        if tilt is None:
            tilt = (self.results.get(self.image) or {}).get("tilt", disp.get("corr_tilt"))
        return float(tilt or 0.0), "original"

    def _slant_drawn(self, x0: float, y0: float, x1: float, y1: float) -> None:
        try:
            lean = drawn_slant(x0, y0, x1, y1)
        except ValueError:
            self.slant_status.set("warn", "Draw along a near-vertical streak (the line was closer to horizontal).")
            return
        offset, view = self._view_offset()
        a = lean + offset
        span = math.hypot(x1 - x0, y1 - y0)
        self.slant_draw_btn.setChecked(False)
        self.line.set(x0, y0, x1, y1, f"{lean:+.2f}°")
        how = (f"lean {lean:+.2f}° on the {view} image {'+ tilt' if view == 'original' else '+ slant applied'} "
               f"{offset:+.2f}°")
        self.host.session.log(f"Slant: drawn line over {span:,.0f} px, {how} = {a:+.2f}°".replace(",", " "))
        self._apply_slant(self.image, a, "drawn", f"From the drawn line: {a:+.2f}° ({how})".replace(",", " "))

    def _apply_slant(self, image: str, angle: float, source: str, note: str) -> None:
        """Store the night's slant (both images); it is applied only with "Correct slant" ticked."""
        self.slant_angle.setValue(angle)
        for img in IMAGES:
            self.slant_notes[img] = note + (f" (measured on the {image.lower()})" if img != image else "")
        s = self.host.session
        p = {**self.params(), "slant": round(angle, 5), "slant_source": source}
        for img, per in list(p.get("per_image", {}).items()):  # legacy per-image values give way
            p["per_image"][img] = {k: v for k, v in per.items() if k not in ("slant", "slant_source")}
        s.params["geometry"] = p
        self.host.inputs_changed()
        self._update_slant_notes()
        if p.get("slant_on"):
            self.host.run_stage("geometry")

    def _update_slant_notes(self) -> None:
        img = self.image
        res = self.slant_results.get(img)
        note = self.slant_notes.get(img)
        p = self.params()
        if res is not None and not res["ok"] and note is None:
            self.slant_status.set("warn", f"{res['text']}. Use Draw angle along a leaning streak.")
        elif note:
            self.slant_status.set("ok" if p.get("slant_on") else "off",
                                  note + ("" if p.get("slant_on") else " · not applied (Correct slant is off)"))
        else:
            self.slant_status.set("off", "Not measured yet. Detect or Draw angle.")
        k = abs(math.tan(math.radians(self.slant_angle.value())))
        half, sky = 10, 40
        bands = bands_of(self.host.session, img)
        if bands is not None:
            half = (bands.spectrum.bottom - bands.spectrum.top) // 2
            r0 = (bands.spectrum.top + bands.spectrum.bottom) / 2
            sky = round(max(abs(r0 - bands.sky_a.top), abs(bands.sky_b.bottom - r0)))
        self.slant_impact.setText(
            f"At {self.slant_angle.value():+.2f}° lines move {2 * half * k:.2f} px across a {2 * half + 1}-row band "
            f"and {sky * k:.2f} px at the sky rows {sky} rows away.")

    def set_values(self) -> None:
        """This angle for both images, saved as the spectrograph's geometry (with the slant when it is
        corrected)."""
        a = round(self.angle.value(), 5)
        prof = self.host.profile
        prof.tilt_deg = a
        prof.tilt_date = time.strftime("%Y-%m-%d")
        slant, slant_src = slant_for(self.params())
        if slant is not None and slant_src != "saved":
            prof.slant_deg = round(float(slant), 5)
            prof.slant_date = prof.tilt_date
        prof.save()
        s = self.host.session
        p = {**self.params(), "use_saved": True, "saved_tilt": a, "saved_slant": prof.slant_deg}
        p.pop("per_image", None)
        s.params["geometry"] = p
        s.log(f"Geometry: {a:.3f}° set for both images and saved for this spectrograph"
              + (f", slant {prof.slant_deg:+.3f}°" if p.get("slant_on") and prof.slant_deg is not None else ""))
        self.load_session()
        self.host.inputs_changed()

    def stage_done(self, run: StageRun) -> None:
        for img, d in (run.data or {}).items():
            self.results[img] = d
        self.view_toggle.set_index(1)
        self.viewer.which = "corr"
        self.line.hide()
        self._refresh_image()


class AngleEdit(QLineEdit):
    """Angle in degrees, typed or set by Detect / Draw angle (−10 … 10)."""

    def __init__(self) -> None:
        super().__init__()
        v = QDoubleValidator(-10.0, 10.0, 4, self)
        v.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.setValidator(v)
        self.setFont(theme.mono_font(12))
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setFixedWidth(110)

    def value(self) -> float:
        try:
            return float(self.text().replace("−", "-").replace(",", "."))
        except ValueError:
            return 0.0

    def setValue(self, v: float) -> None:
        self.setText(f"{v:.3f}")


def _mtime(path: Any) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except (OSError, AttributeError):
        return None
