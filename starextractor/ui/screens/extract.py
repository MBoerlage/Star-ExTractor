"""Step 3 · 1D Extract: place spectrum and sky bands, live 1D profile, sum rows minus sky."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
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

from starextractor.core.extract import Band, Bands
from starextractor.core.extract_stage import (
    anchor_row,
    auto_place_job,
    corrected_path,
    default_offsets,
    load_display_job,
    offsets_of,
    params_of,
    preview_job,
    stored_of,
)
from starextractor.core.pipeline import IMAGES, StageDef, StageRun

from .. import theme
from ..widgets.bands import BandOverlay
from ..widgets.common import Card, Segmented, StatusLine, label
from ..widgets.image_viewer import ImageViewer
from ..widgets.plot_guard import pg
from .base import StepScreen

if TYPE_CHECKING:
    from ..main_window import MainWindow

ZOOMS = (("Full height", "full"), ("Fit to bands", "bands"), ("×2", 2), ("×4", 4), ("×8", 8), ("×16", 16))
SKY_MODES = (("Mean of Sky A and Sky B", "mean"), ("Linear fit across Sky A and B", "linear"))
FIELDS = (("spectrum", "Spectrum", "SPECTRUM_TEXT"), ("sky_a", "Sky A", "SKY_TEXT"), ("sky_b", "Sky B", "SKY_TEXT"))


VIEW_ALL = 0.99  # a view showing at least this share of the rows counts as the whole frame

class RowEdit(QLineEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setValidator(QIntValidator(0, 100000, self))
        self.setFont(theme.mono_font(12))
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setFixedWidth(90)


class ExtractScreen(StepScreen):
    def __init__(self, stage: StageDef, total: int, host: MainWindow) -> None:
        super().__init__(stage, total)
        self.host = host
        self.display: dict[str, dict[str, Any]] = {}
        self.bands: dict[str, Bands] = {}  # bands shown per image (stored or auto)
        self.shown: str | None = None  # image whose corrected stack is in the viewer (None while loading)
        self._loading = False
        row = QHBoxLayout()
        row.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._build_viewer_card())
        left.addWidget(self._build_profile_card())
        row.addLayout(left, 1)
        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_bands_card())
        right.addWidget(self._build_extraction_card())
        right.addWidget(self._build_reuse_card())
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
        for text, tip, mode in (("Fit && auto", "Full height", "full"), ("Fit", "Fit to bands", "bands"),
                                ("×1", "True aspect", 1), ("↔", "Show the full width", "full")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, m=mode: self._zoom(m))
            top.addWidget(b)
        self.readout = label("", "mono")
        self.readout.setMinimumWidth(190)
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.readout)
        card.body.addLayout(top)
        tg = QHBoxLayout()
        self.view_toggle = Segmented(["Pre-extraction", "Sky-subtracted"])
        self.view_toggle.changed.connect(lambda i: self.viewer.show_corrected(i == 1))
        tg.addWidget(self.view_toggle)
        tg.addStretch(1)
        card.body.addLayout(tg)
        zr = QHBoxLayout()
        zr.addWidget(label("Y ZOOM", "eyebrow"))
        self.zoom_chips = Segmented([t for t, _ in ZOOMS])
        for b in self.zoom_chips.buttons:
            b.setProperty("seg", "only")
        self.zoom_chips.layout().setSpacing(6)
        self.zoom_chips.changed.connect(lambda i: self._zoom(ZOOMS[i][1]))
        zr.addWidget(self.zoom_chips)
        zr.addStretch(1)
        self.rows_label = label("", "mono")
        zr.addWidget(self.rows_label)
        card.body.addLayout(zr)
        self.viewer = ImageViewer()
        self.viewer.setMinimumHeight(240)
        self.viewer.readout.connect(self.readout.setText)
        self.viewer.rows_changed.connect(lambda a, b, n: self.rows_label.setText(f"rows {a}–{b} of {n}"))
        self.overlay = BandOverlay(self.viewer.vb)
        self.overlay.moving.connect(self._bands_moving)
        self.overlay.moved.connect(self._bands_moved)
        card.body.addWidget(self.viewer, 1)
        card.body.addWidget(label(
            "Drag a band edge to move it. The row number follows the cursor. Y zoom is a view only and never "
            "changes the file. X stays fixed. Roll the wheel over the image to zoom Y, or drag the amber window in "
            "the frame strip to move up and down. Blue and brown marks in the strip show where the bands sit.",
            "muted", wrap=True, style="font-size: 12px;"))
        return card

    def _build_profile_card(self) -> Card:
        card = Card("Profile from the blue band")
        self.profile_note = label("", "mono")
        card.head.addWidget(self.profile_note)
        self.plot = pg.PlotWidget(enableMenu=False)
        self.plot.setBackground(theme.CARD)
        self.plot.setFixedHeight(200)
        pi = self.plot.getPlotItem()
        pi.hideButtons()
        pi.showGrid(x=True, y=True, alpha=0.15)
        pi.setLabel("left", "Counts (norm.)")
        pi.setLabel("bottom", "Detector column (px)")
        pi.setMouseEnabled(x=True, y=False)
        self.curve = pg.PlotDataItem(pen=pg.mkPen(theme.TEAL, width=1.5))
        self.curve.setClipToView(True)
        self.curve.setDownsampling(auto=True, method="peak")
        pi.addItem(self.curve)
        card.body.addWidget(self.plot)
        return card

    def _build_bands_card(self) -> Card:
        card = Card("Bands")
        self.auto_btn = QPushButton("Auto-place from the brightest row")
        self.auto_btn.setToolTip("Zoomed in, only the rows in view are searched for the spectrum (zoom or move the "
                                 "frame-strip window onto the star when a broader band elsewhere is brighter)")
        self.auto_btn.clicked.connect(self.auto_place)
        card.body.addWidget(self.auto_btn)
        self.lobe_chip = label("", "chip", wrap=True)  # neutral: a double-lobed (defocused) profile
        self.lobe_chip.hide()
        card.body.addWidget(self.lobe_chip)
        self.fields: dict[str, tuple[RowEdit, RowEdit]] = {}
        g = QGridLayout()
        g.setVerticalSpacing(6)
        r = 0
        for key, title, colour in FIELDS:
            g.addWidget(label(title, style=f"color: {getattr(theme, colour)}; font-size: 12px;"), r, 0, 1, 2)
            r += 1
            pair = (RowEdit(), RowEdit())
            for k, (name, edit) in enumerate(zip(("Top", "Bottom"), pair, strict=True)):
                g.addWidget(label(name, "muted"), r, 0)
                g.addWidget(edit, r, 1, Qt.AlignmentFlag.AlignRight)
                edit.editingFinished.connect(self._fields_edited)
                r += 1
                del k
            self.fields[key] = pair
        card.body.addLayout(g)
        card.body.addWidget(label("Drag the bands in the image, or type rows. Sky bands should sit inside the lit "
                                  "slit area, away from the star.", "muted", wrap=True, style="font-size: 12px;"))
        return card

    def _build_extraction_card(self) -> Card:
        card = Card("Extraction")
        g = QGridLayout()
        g.addWidget(label("Method", "muted"), 0, 0)
        self.method = QComboBox()
        self.method.addItem("Sum of rows", "sum")
        g.addWidget(self.method, 0, 1, Qt.AlignmentFlag.AlignRight)
        card.body.addLayout(g)
        self.subtract = QCheckBox("Subtract sky background")
        self.subtract.toggled.connect(lambda on: self._set_param("subtract_sky", on))
        card.body.addWidget(self.subtract)
        self.sky_mode = QComboBox()
        for text, val in SKY_MODES:
            self.sky_mode.addItem(text, val)
        self.sky_mode.currentIndexChanged.connect(lambda _i: self._set_param("sky_mode", self.sky_mode.currentData()))
        card.body.addWidget(self.sky_mode)
        self.centring = StatusLine("off", "")
        card.body.addWidget(self.centring)
        return card

    def _show_lobes(self, res: dict[str, Any]) -> None:
        """The double-lobed note for an auto-placement (rows of the band shown)."""
        if res.get("mode") == "double-lobed":
            b = Bands.from_json(res["bands"]).spectrum
            self.lobe_chip.setText(f"Double-lobed profile (defocus?). Spectrum spans both lobes: rows "
                                   f"{b.top}–{b.bottom}. A sharper focus gives more signal per row.")
            self.lobe_chip.show()
        else:
            self.lobe_chip.hide()

    def _build_reuse_card(self) -> Card:
        card = Card("Reuse")
        self.copy_btn = QPushButton("Copy bands to the reference stack")
        self.copy_btn.clicked.connect(self.copy_bands)
        card.body.addWidget(self.copy_btn)
        card.body.addWidget(label("Same slit position, same rows", "muted", style="font-size: 12px;"))
        self.default_btn = QPushButton("Save as default rows")
        self.default_btn.setToolTip("Keep these band heights and sky distances (from the spectrum row) for this "
                                    "spectrograph: a new image starts from them instead of auto-placement")
        self.default_btn.clicked.connect(self.save_default_rows)
        card.body.addWidget(self.default_btn)
        self.bands_note = label("", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.bands_note)
        return card

    # ---- state -------------------------------------------------------------------------------
    @property
    def image(self) -> str:
        return IMAGES[self.header.image_idx]

    def load_session(self) -> None:
        self._loading = True
        p = params_of(self.host.session)
        self.subtract.setChecked(bool(p["subtract_sky"]))
        idx = self.sky_mode.findData(p["sky_mode"])
        self.sky_mode.setCurrentIndex(max(idx, 0))
        self._loading = False
        self.display.clear()
        self.bands.clear()
        self.shown = None  # another session: nothing of the old one may stay editable
        if self.isVisible():
            self._refresh_image()

    def on_shown(self) -> None:
        self._refresh_image()

    def _image_changed(self, idx: int) -> None:
        self.tabs.set_index(idx)
        self.copy_btn.setEnabled(idx == 0)
        self._refresh_image()

    def _tab_changed(self, idx: int) -> None:
        if idx != self.header.image_idx:
            self.header._step_image(idx - self.header.image_idx)

    def _refresh_image(self) -> None:
        s = self.host.session
        img = self.image
        src = corrected_path(s, img)
        if src is None:
            self._clear_view()
            return
        cached = self.display.get(img)
        stored = stored_of(s, img)
        mtime = src.stat().st_mtime_ns if src.exists() else None
        if cached is not None and cached["mtime"] == mtime and (
                stored is None or Bands.from_json(stored["bands"]) == self.bands.get(img)):
            self._show(cached)
        elif src.exists():
            if self.shown != img:  # never leave the other image (and its bands) on screen while this one loads
                self._clear_view()
            off, off_src = default_offsets(s, img, self.host.profile.bands)
            self.host.runner.submit(f"view.extract.{img}", load_display_job, img, src, stored,
                                    params_of(s)["sky_mode"], off, off_src)
        else:
            self._clear_view()

    def _clear_view(self) -> None:
        self.shown = None
        self.viewer.set_images(None, None)
        self.overlay.set_bands(None, 1)
        self.curve.setData([], [])
        self.profile_note.setText("")
        self._loading = True
        for top, bot in self.fields.values():
            top.clear()
            bot.clear()
        self._loading = False

    def _editable(self) -> bool:
        """Band edits apply only to the image on screen."""
        return self.shown == self.image

    def on_display(self, res: dict[str, Any]) -> None:
        self.display[res["image"]] = res
        self.bands[res["image"]] = Bands.from_json(res["bands"])
        how = res.get("how", "")
        if how.startswith("followed"):  # Geometry moved the spectrum: keep the moved rows
            self.host.session.log(f"Bands {res['image']}: {how} after a Geometry change")
            self._store(res["image"], Bands.from_json(res["bands"]), how)
            return
        self.bands_note.setText({"Acquisition rows": "Rows from tonight's Acquisition set (not stored yet).",
                                 "default rows": "Rows from the saved default rows (not stored yet).",
                                 "auto-placed": "Auto-placed on the brightest row (not stored yet)."}.get(how, ""))
        if res["image"] == self.image:
            self._show(res)
            self._show_lobes(res)

    def _show(self, res: dict[str, Any]) -> None:
        self.shown = res["image"]
        self.viewer.set_images(res["orig"], res["corr"])
        bands = self.bands.get(self.image) or Bands.from_json(res["bands"])
        self.overlay.set_bands(bands, res["orig"].shape[1])
        self._fill_fields(bands)
        self._update_strip()
        self._preview(bands)

    def _fill_fields(self, bands: Bands) -> None:
        self._loading = True
        for key, band in (("spectrum", bands.spectrum), ("sky_a", bands.sky_a), ("sky_b", bands.sky_b)):
            top, bot = self.fields[key]
            top.setText(str(band.top))
            bot.setText(str(band.bottom))
        self._loading = False

    def _update_strip(self) -> None:
        if self.viewer.strip is not None:
            self.viewer.strip.marks = self.overlay.strip_marks()
            self.viewer.strip.update()

    def _zoom(self, mode: str | int) -> None:
        if mode == "bands" and self.image in self.bands:
            b = self.bands[self.image]
            self.viewer.set_rows(min(b.sky_a.top, b.spectrum.top) - 20, max(b.sky_b.bottom, b.spectrum.bottom) + 20)
        else:
            self.viewer.set_zoom(mode)

    # ---- band edits --------------------------------------------------------------------------
    def _raw(self) -> np.ndarray | None:
        d = self.display.get(self.image)
        return d["orig"].raw if d else None

    def _preview(self, bands: Bands) -> None:
        raw = self._raw()
        if raw is None:
            return
        p = params_of(self.host.session)
        self.host.runner.submit(f"preview.extract.{self.image}", preview_job, raw, bands, bool(p["subtract_sky"]),
                                p["sky_mode"])

    def on_preview(self, image: str, res: dict[str, Any]) -> None:
        if image != self.image:
            return
        prof = res["profile"]
        peak = float(np.percentile(prof, 99.5)) or 1.0
        self.curve.setData(np.arange(len(prof)), prof / peak)
        done = self.host.session.image_done("extract", image)
        self.profile_note.setText(f"{image.lower()} stack" + ("" if done else " · preview · Needs re-run"))
        self.centring.set(*res["centring"])

    def _bands_moving(self, bands: Bands) -> None:
        if not self._editable():
            return
        self._fill_fields(bands)
        self.bands[self.image] = bands
        self._update_strip()
        self._preview(bands)

    def _bands_moved(self, bands: Bands) -> None:
        if self._editable():
            self._store(self.image, bands, "moved")

    def _fields_edited(self) -> None:
        if self._loading or not self._editable():
            return
        try:
            vals = {k: (int(t.text()), int(b.text())) for k, (t, b) in self.fields.items()}
        except ValueError:
            return
        bands = Bands(Band(*vals["spectrum"]), Band(*vals["sky_a"]), Band(*vals["sky_b"]))
        d = self.display.get(self.image)
        if d is not None:
            bands = bands.clipped(d["orig"].shape[0])
        if bands != self.bands.get(self.image):
            self.overlay.set_bands(bands, self.overlay.width)
            self._store(self.image, bands, "typed")

    def _store(self, image: str, bands: Bands, how: str) -> None:
        """Keep the bands with the ridge row of the spectrum they sit on, so they follow a Geometry change."""
        self.bands[image] = bands
        d = self.display.get(image)
        anchor = anchor_row(d["orig"].raw, bands.spectrum) if d else None
        values: dict[str, Any] = {"bands": bands.to_json()}
        if anchor is not None:
            values["anchor"] = round(float(anchor), 1)
        self.host.session.set_image_params("extract", image, values)
        self.bands_note.setText("")
        s = bands.spectrum
        self.host.session.log(f"Bands {image} ({how}): spectrum {s.top}–{s.bottom}, sky A {bands.sky_a.top}–"
                              f"{bands.sky_a.bottom}, sky B {bands.sky_b.top}–{bands.sky_b.bottom}")
        self.host.inputs_changed()
        self._fill_fields(bands)
        self._update_strip()
        self._preview(bands)
        self.display.pop(image, None)  # the sky-subtracted view depends on the sky bands
        if image == self.image:
            self._refresh_image()

    def auto_place(self) -> None:
        raw = self._raw()
        if raw is not None and self._editable():
            self.auto_btn.setEnabled(False)
            y0, y1 = self.viewer.y_range()
            h = raw.shape[0]
            window = None if y1 - y0 >= VIEW_ALL * h else (max(0.0, y0), min(float(h), y1))
            self.host.runner.submit(f"auto.extract.{self.image}", auto_place_job, raw, window)

    def on_auto(self, image: str, res: dict[str, Any]) -> None:
        self.auto_btn.setEnabled(True)
        bands = Bands.from_json(res["bands"])
        if image == self.image:
            self.overlay.set_bands(bands, self.overlay.width)
            self._show_lobes(res)
        how = (f"auto-placed, double-lobed, span {res['span']} rows (FWHM n/a)" if res.get("mode") == "double-lobed"
               else f"auto-placed, FWHM {res['fwhm']:.1f} rows")
        for w in res.get("warnings") or []:
            self.host.session.log(f"Bands {image}: {w}")
        self._store(image, bands, how)

    def save_default_rows(self) -> None:
        d = self.display.get(self.image)
        b = self.bands.get(self.image)
        if d is None or b is None:
            return
        off = offsets_of(b, d["orig"].spectrum_row)
        prof = self.host.profile
        prof.bands = off
        prof.save()
        self.host.session.log(f"Default rows saved for this spectrograph: spectrum {off['spec']}, "
                              f"sky A {off['sky_a']}, sky B {off['sky_b']} (rows from the spectrum row)")
        self.bands_note.setText("Saved as the default rows for new images.")

    def copy_bands(self) -> None:
        if "Target" in self.bands:
            self._store("Reference", self.bands["Target"], "copied from the target")

    def _set_param(self, key: str, value: Any) -> None:
        if self._loading:
            return
        s = self.host.session
        s.params["extract"] = {**params_of(s), key: value}
        self.host.inputs_changed()
        self.display.clear()
        self._refresh_image()

    def stage_done(self, run: StageRun) -> None:
        for img, d in (run.data or {}).items():
            self.bands[img] = Bands.from_json(d["bands"])
        if self.image in (run.data or {}):
            d = run.data[self.image]  # type: ignore[index]
            self.on_preview(self.image, {"profile": d["profile"], "centring": d["centring"]})
        self.view_toggle.set_index(1)
