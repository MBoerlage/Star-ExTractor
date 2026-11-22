"""Export tab: check the target spectrum against Balmer/telluric lines, fill in observer details,
write the AVSpec file and its process log. Nothing is uploaded or opened online."""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from starextractor.core import export as ex
from starextractor.core.response import telluric_bands
from starextractor.core.vsx import DEFAULT_URL, VSX_SITE, VsxObject, lookup
from starextractor.core.wavelength import BALMER

from .. import theme
from ..log_panel import LogPanel
from ..rail import StepState
from ..widgets.common import Card, InfoButton, Segmented, StatusLine, label
from ..widgets.plot_guard import pg

if TYPE_CHECKING:
    from ..main_window import MainWindow

ZOOMS = (("Full", None), ("Blue Balmer", (3900.0, 5000.0)), ("Hα + telluric", (6200.0, 7700.0)),
         ("O₂ A band", (7500.0, 7750.0)), ("Custom", "custom"))
FIRST_KEYS = ("OBJNAME", "OBJECT", "DATE-OBS", "DATE-END", "EXPTIME", "EXPTIME2", "JD", "OBSERVER", "AAV_SITE",
              "AAV_INST", "AAV_ITRP",
              "AIRMASS", "CTYPE1", "CUNIT1", "CRVAL1", "CDELT1", "CRPIX1", "VERSION")


def field(placeholder: str = "", width: int | None = None, mono: bool = False) -> QLineEdit:
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    if width:
        e.setFixedWidth(width)
    if mono:
        e.setFont(theme.mono_font(12))
    return e


def num_field(lo: float, hi: float, placeholder: str) -> QLineEdit:
    e = field(placeholder, mono=True)
    v = QDoubleValidator(lo, hi, 6, e)
    v.setNotation(QDoubleValidator.Notation.StandardNotation)
    e.setValidator(v)
    return e


class ExportScreen(QWidget):
    def __init__(self, host: MainWindow) -> None:
        super().__init__()
        self.host = host
        self.lam: np.ndarray | None = None
        self.flux: np.ndarray | None = None
        self.r_header: dict[str, Any] = {}
        self.vsx: dict[str, Any] | None = None
        self.plan: ex.ExportPlan | None = None
        self.nm = False
        self._loading = False
        self.step = 0
        self.test_sig: str | None = None  # signature of the data the last passing test saw
        self.test_failed_sig: str | None = None
        self.published_sig: str | None = None
        self.last_export: Path | None = None
        self.last_checks: list[ex.Check] = []  # the last Test run, and the data it saw
        self.last_checks_sig: str | None = None
        self.ack_sig: str | None = None  # warnings acknowledged for this data (reset by every Test run)

        # Collect / Test / Publish: shown on the workflow rail (MainWindow.refresh_export_steps)
        self.step_states: list[tuple[StepState, str]] = [(StepState.NOT_RUN, "Not run")] * 3
        self.banner = label("", "banner", wrap=True)
        self.banner.hide()

        collect = [self._build_object_card(), self._build_plot_card()]
        who = QHBoxLayout()  # who, where, with what: above the header preview
        who.setSpacing(12)
        for c in (self._build_observer_card(), self._build_location_card(), self._build_equipment_card()):
            who.addWidget(c, 1, Qt.AlignmentFlag.AlignTop)
        collect += [who, self._build_header_card()]
        self.pages = QStackedWidget()
        from .export_publish import PublishCard

        self.publish = PublishCard(self)
        for items in (collect, [self._build_test_card()], [self.publish]):
            self.pages.addWidget(self._scroll_page(items))
        self.log = LogPanel()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 16, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(label("Export", "title"))
        outer.addWidget(label("Collect the data for the FITS header, test it against the AAVSO AVSpec rules, then "
                              "save the file you upload to AVSpec.", "muted", wrap=True))
        outer.addWidget(self.banner)
        outer.addWidget(self.pages, 1)
        outer.addWidget(self.log)
        self._update_steps()

    @staticmethod
    def _scroll_page(items: list) -> QScrollArea:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(12)
        for it in items:
            if isinstance(it, QWidget):
                lay.addWidget(it)
            else:
                lay.addLayout(it)
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    # ---- steps: Collect data → Test → Publish -----------------------------------------------------
    def show_step(self, idx: int) -> None:
        self.step = idx
        self.pages.setCurrentIndex(idx)
        if idx == 1:
            self.run_test()  # cheap and offline: the Test step always shows a fresh result
        if idx == 2:
            self.publish.refresh()
        self._update_steps()

    def _signature(self) -> str | None:
        if self.plan is None or self.flux is None:
            return None
        h = hashlib.sha1(repr(sorted((k, str(v)) for k, v in self.plan.header.items())).encode())
        h.update("\n".join(self.plan.history).encode())
        h.update(np.ascontiguousarray(self.flux, dtype=np.float32).tobytes())
        return h.hexdigest()

    def _update_steps(self) -> None:
        sig = self._signature()
        if sig is None:
            states = [(StepState.MISSING, "No Target_R.fit: run Response")] + [(StepState.NOT_RUN, "Not run")] * 2
        else:
            errs = [c for c in ex.preflight_avspec(self.plan, self.flux, self.vsx) if c.state == "err"]
            collect = (StepState.DONE, "Header complete") if not errs else (
                StepState.NOT_RUN, f"{len(errs)} item{'s' if len(errs) > 1 else ''} to fill in")
            if self.test_sig == sig:
                test = (StepState.DONE, "All AVSpec checks pass")
            elif self.test_failed_sig == sig:
                test = (StepState.MISSING, "Test failed: see the list")
            else:
                test = (StepState.NOT_RUN, "Needs re-run" if self.test_sig or self.test_failed_sig else "Not run")
            saved = self.publish.tiles["avspec"].saved
            publish = ((StepState.DONE, f"Saved {Path(saved['path']).name}") if saved
                       else (StepState.NOT_RUN, "Not saved"))
            states = [collect, test, publish]
        self.step_states = list(states)
        refresh = getattr(self.host, "refresh_export_steps", None)
        if refresh is not None:
            refresh()

    @property
    def ack_cb(self) -> QCheckBox:
        return self.publish.ack_cb

    def warnings_acknowledged(self) -> bool:
        sig = self._signature()
        return sig is not None and self.ack_sig == sig

    def _ack_toggled(self, on: bool) -> None:
        sig = self._signature()
        self.ack_sig = sig if on else None
        warns = [c.text for c in self.last_checks if c.state == "warn"]
        if on:  # to the log only, never to any FITS header
            self.host.session.log(f"AVSpec test: {len(warns)} warning{'s' if len(warns) != 1 else ''} acknowledged: "
                                  + "; ".join(warns))
        self._record_qc(warns, on)
        self.publish.refresh()

    def _record_qc(self, warnings: list[str], acknowledged: bool) -> None:
        """The test's warnings and whether they were read, in the pair logbook (qc) for the Library."""
        from starextractor.core import logbook

        folder = self.host.session.folder
        if folder is None or logbook.load(folder) is None:
            return
        try:
            logbook.set_qc(folder, warnings, acknowledged)
        except OSError:
            return
        self.host.pairs_changed.emit()

    def run_test(self) -> list[ex.Check]:
        """AVSpec rules on the header, then the file written to a temporary folder and read back."""
        self._refresh_header()
        if self.plan is None or self.flux is None:
            checks = [ex.Check("err", "No Target_R.fit yet: run Response on the Reduce tab")]
        else:
            checks = ex.preflight_avspec(self.plan, self.flux, self.vsx)
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / self.default_name()
                ex.write_export(path, self.flux.astype(np.float32), self.plan)
                checks += ex.verify_file(path, int(self.flux.size))
        self._fill_checks(self.avspec_checks, checks)
        errors = [c for c in checks if c.state == "err"]
        sig = self._signature()
        self.last_checks, self.last_checks_sig = checks, sig
        warns = sum(c.state == "warn" for c in checks)
        if not any(c.state == "err" for c in checks):
            self._record_qc([c.text for c in checks if c.state == "warn"], False)
        self.ack_sig = None  # every Test run asks again
        cb = self.ack_cb  # lives in the AVSpec tile of Publish: it unlocks the AVSpec save only
        cb.blockSignals(True)
        cb.setChecked(False)
        cb.blockSignals(False)
        cb.setText(f"I have read the {warns} warning{'s' if warns != 1 else ''}")
        cb.setVisible(bool(warns) and not errors and sig is not None)
        if errors:
            self.test_sig, self.test_failed_sig = None, sig
            self.test_summary.set("err", f"{len(errors)} problem{'s' if len(errors) > 1 else ''}: fix them in "
                                  "Collect data, then test again.")
        else:
            self.test_sig, self.test_failed_sig = sig, None
            self.test_summary.set("ok", "All AVSpec requirements pass" +
                                  (f" ({warns} note{'s' if warns > 1 else ''} below)." if warns else "."))
        if sig is not None:
            self.host.session.log(f"AVSpec test: {len(checks) - len(errors)} of {len(checks)} checks pass"
                                  + (f", {len(errors)} failed" if errors else ""))
        self._update_steps()
        self.publish.refresh()
        return checks

    # ---- construction ------------------------------------------------------------------------
    def _build_plot_card(self) -> Card:
        card = Card("Spectrum to publish")
        self.source_note = label("target only", "mono")
        card.head.insertWidget(1, self.source_note)
        self.range_note = label("", "mono", style=f"color: {theme.TEXT};")
        card.head.insertWidget(2, self.range_note)
        self.unit = Segmented(["Å", "nm"])
        self.unit.changed.connect(self._unit_changed)
        card.head.addWidget(self.unit)
        zr = QHBoxLayout()
        zr.addWidget(label("X ZOOM", "eyebrow"))
        self.zoom = Segmented([t for t, _ in ZOOMS])
        for b in self.zoom.buttons:
            b.setProperty("seg", "only")
        self.zoom.layout().setSpacing(6)
        self.zoom.changed.connect(self._zoom_changed)
        zr.addWidget(self.zoom)
        zr.addWidget(label("from", "mono"))
        self.x_from = num_field(1000, 20000, "")
        self.x_from.setFixedWidth(80)
        zr.addWidget(self.x_from)
        zr.addWidget(label("to", "mono"))
        self.x_to = num_field(1000, 20000, "")
        self.x_to.setFixedWidth(80)
        zr.addWidget(self.x_to)
        self.x_unit_lbl = label("Å", "mono")
        zr.addWidget(self.x_unit_lbl)
        for e in (self.x_from, self.x_to):
            e.editingFinished.connect(self._custom_range)
        zr.addStretch(1)
        card.body.addLayout(zr)
        tg = QHBoxLayout()
        self.show_balmer = QCheckBox("Balmer lines")
        self.show_telluric = QCheckBox("Telluric bands")
        for cb in (self.show_balmer, self.show_telluric):
            cb.setChecked(True)
            cb.toggled.connect(self._markers_visible)
            tg.addWidget(cb)
        tg.addStretch(1)
        self.peak = label("", "mono")
        tg.addWidget(self.peak)
        card.body.addLayout(tg)

        self.plot = pg.PlotWidget(enableMenu=False)
        self.plot.setBackground(theme.CARD)
        self.plot.setMinimumHeight(280)
        pi = self.plot.getPlotItem()
        pi.hideButtons()
        pi.showGrid(x=True, y=True, alpha=0.12)
        pi.setLabel("left", "Relative intensity")
        pi.setLabel("bottom", "Wavelength (Å)")
        pi.setMouseEnabled(x=True, y=False)
        pi.vb.setAutoVisible(y=True)
        self.curve = pg.PlotDataItem(pen=pg.mkPen(theme.TEAL, width=1.5))
        self.curve.setClipToView(True)
        pi.addItem(self.curve)
        pi.vb.sigXRangeChanged.connect(self._range_changed)
        self.markers: dict[str, list] = {"balmer": [], "telluric": []}
        self._add_markers()
        card.body.addWidget(self.plot)

        self.overview = pg.PlotWidget(enableMenu=False)
        self.overview.setBackground(theme.CARD)
        self.overview.setFixedHeight(64)
        oi = self.overview.getPlotItem()
        oi.hideButtons()
        oi.hideAxis("left")
        oi.getAxis("bottom").setStyle(tickFont=theme.mono_font(10))
        oi.setMouseEnabled(x=False, y=False)
        self.ov_curve = pg.PlotDataItem(pen=pg.mkPen(theme.MUTED, width=1))
        oi.addItem(self.ov_curve)
        self.window = pg.LinearRegionItem(brush=pg.mkBrush(theme.with_alpha(theme.AMBER, 45)),
                                          pen=pg.mkPen(theme.AMBER, width=2))
        self.window.setZValue(10)
        self.window.sigRegionChanged.connect(self._window_moved)
        oi.addItem(self.window)
        card.body.addWidget(self.overview)
        card.body.addWidget(label(
            "Zooming is a view only: the exported file always holds the whole range shown in the strip below, "
            "set in Response → Normalise and crop. "
            "Roll the wheel over the plot to zoom X, or drag the amber window in the whole-spectrum strip. "
            "Dashed amber lines mark the Balmer lines at their rest wavelengths. Blue bands, dotted band heads "
            "and baseline ticks are the telluric lines AAVSO uses to check calibration. Quality check: the dips "
            "in your spectrum should sit on the blue ticks. Telluric features do not move with the star and stay "
            "in the published file.",
            "muted", wrap=True, style="font-size: 12px;"))
        return card

    def _add_markers(self) -> None:
        pi = self.plot.getPlotItem()
        for name, lam in BALMER.items():
            m = pg.InfiniteLine(pos=lam, angle=90, movable=False,
                                pen=pg.mkPen(theme.AMBER, width=1, style=Qt.PenStyle.DashLine),
                                label=f"{name} {lam:.1f}", labelOpts={"position": 0.96, "color": theme.AMBER})
            m.setProperty("lam", lam)
            pi.addItem(m)
            self.markers["balmer"].append(m)
        for name, a, b in telluric_bands():
            if "bandhead" in name.lower():
                lam = a + 8
                m = pg.InfiniteLine(pos=lam, angle=90, movable=False,
                                    pen=pg.mkPen(theme.SKYBLUE, width=1, style=Qt.PenStyle.DotLine),
                                    label=name.split("(")[-1].split(")")[0].replace("O2", "O₂").replace("H2O", "H₂O")
                                    + f" {lam:g}",
                                    labelOpts={"position": 0.08, "color": theme.SKYBLUE})
                m.setProperty("lam", lam)
            else:
                m = pg.LinearRegionItem((a + 3, b - 3), movable=False,
                                        brush=pg.mkBrush(theme.with_alpha(theme.SKYBLUE, 40)),
                                        pen=pg.mkPen(None))
                m.setZValue(-5)
                m.setProperty("lam", (a + 3, b - 3))
                short = name.replace(" band", "").replace("near Ha", "").replace("O2", "O₂").replace("H2O", "H₂O")
                tag = pg.InfiniteLine(pos=(a + b) / 2, angle=90, movable=False, pen=pg.mkPen(None),
                                      label=f"{short.strip()} {a + 3:.0f}–{b - 3:.0f}",
                                      labelOpts={"position": 0.04, "color": theme.SKYBLUE})
                tag.setProperty("lam", (a + b) / 2)
                pi.addItem(tag)
                self.markers["telluric"].append(tag)
            pi.addItem(m)
            self.markers["telluric"].append(m)
        self.ticks = pg.ScatterPlotItem(symbol="|", size=10, pen=pg.mkPen(theme.SKYBLUE), brush=None)
        pi.addItem(self.ticks)
        self.markers["telluric"].append(self.ticks)

    def _build_test_card(self) -> Card:
        card = Card("AAVSO AVSpec test")
        card.head.insertWidget(1, InfoButton(
            "Checks the header against the AVSpec requirements (AAVSO spectroscopy database manual, see "
            "docs/FORMATS.md), then writes the file to a temporary folder and reads it back with astropy. "
            "Nothing is saved in the export folder and nothing goes online."))
        self.test_btn = QPushButton("Run test again")
        self.test_btn.setProperty("role", "primary")
        self.test_btn.clicked.connect(lambda: self.run_test())
        card.head.addWidget(self.test_btn)
        self.test_summary = StatusLine("off", "Not run yet.")
        card.body.addWidget(self.test_summary)
        self.avspec_checks = QVBoxLayout()
        self.avspec_checks.setSpacing(6)
        card.body.addLayout(self.avspec_checks)
        return card

    def _build_header_card(self) -> Card:
        card = Card("FITS header preview")
        card.head.addWidget(label("Filled from the fields on the right", "muted", style="font-size: 12px;"))
        self.table = QTableWidget(0, 2)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 120)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(300)
        card.body.addWidget(self.table)
        return card

    def _build_object_card(self) -> Card:
        card = Card("Object name")
        card.head.insertWidget(1, InfoButton(
            "OBJNAME follows the target name from Select until you change it here. AVSpec wants a name the Variable "
            "Star Index (VSX) knows, which can differ from the SIMBAD name. Checking it with VSX is optional: one "
            "online request per click (answers are cached), and the result is only used when you press "
            "\u201cUse VSX name\u201d. Without the check, AVSpec still validates the name on upload."))
        self.obj_hint = label("OBJNAME", "muted")
        card.head.addWidget(self.obj_hint)
        row = QHBoxLayout()
        self.objname = field("Name known to VSX")
        self.objname.textEdited.connect(self._objname_edited)
        self.objname.editingFinished.connect(self._fields_changed)
        self.vsx_btn = QPushButton("Check VSX name")
        self.vsx_btn.setToolTip("Optional: one request to vsx.aavso.org")
        self.vsx_btn.clicked.connect(lambda: self.check_vsx())
        self.vsx_use = QPushButton("Use VSX name")
        self.vsx_use.setToolTip("Write VSX's name to OBJNAME (and its coordinates if the target has none)")
        self.vsx_use.clicked.connect(lambda: self.apply_vsx())
        self.vsx_use.setEnabled(False)
        row.addWidget(self.objname, 1)
        row.addWidget(self.vsx_btn)
        row.addWidget(self.vsx_use)
        card.body.addLayout(row)
        self.vsx_status = StatusLine("off", "")
        card.body.addWidget(self.vsx_status)
        self.vsx_meta = label("", "mono", wrap=True)
        card.body.addWidget(self.vsx_meta)
        return card

    def _build_observer_card(self) -> Card:
        card = Card("Observer")
        card.body.addWidget(label("Observer code (AAVSO obscode)", "muted"))
        self.obscode = field("e.g. ABCD", mono=True)
        card.body.addWidget(self.obscode)
        card.body.addWidget(label("Written to OBSERVER. This is what AVSpec reads.", "muted", style="font-size: 12px;"))
        card.body.addWidget(label("Name", "muted"))
        self.obs_name = field("Optional")
        card.body.addWidget(self.obs_name)
        for e in (self.obscode, self.obs_name):
            e.editingFinished.connect(self._fields_changed)
        return card

    def _build_location_card(self) -> Card:
        card = Card("Location")
        from_hdr = QPushButton("From frame headers")
        from_hdr.setToolTip("Use SITELAT/SITELONG/SITEELEV from your frames (the default)")
        from_hdr.clicked.connect(lambda: self.site_from_headers())
        card.head.addWidget(from_hdr)
        card.body.addWidget(label("Site name", "muted"))
        self.site_name = field("Your AAVSO site nickname")
        card.body.addWidget(self.site_name)
        card.body.addWidget(label("Must match your AAVSO site record exactly.", "muted", style="font-size: 12px;"))
        g = QGridLayout()
        self.lat = num_field(-90, 90, "+00.000")
        self.lon = num_field(-180, 180, "−000.000 (east +)")
        self.elev = num_field(-500, 9000, "m")
        for c, (t, w_) in enumerate((("Latitude", self.lat), ("Longitude", self.lon))):
            g.addWidget(label(t, "muted"), 0, c)
            g.addWidget(w_, 1, c)
        g.addWidget(label("Elevation (m)", "muted"), 2, 0)
        g.addWidget(self.elev, 3, 0)
        card.body.addLayout(g)
        self.site_source = label("", "muted", wrap=True, style="font-size: 12px;")
        card.body.addWidget(self.site_source)
        self.site_from_hdr = True
        for e in (self.lat, self.lon, self.elev):
            e.textEdited.connect(self._site_typed)
        for e in (self.site_name, self.lat, self.lon, self.elev):
            e.editingFinished.connect(self._fields_changed)
        return card

    def _build_equipment_card(self) -> Card:
        card = Card("Equipment")
        self.eq: dict[str, QLineEdit] = {}
        for key, title, ph, note in (
            ("name", "Instrument name", "Your AAVSO equipment nickname",
             "Must match your AAVSO equipment record exactly."),
            ("spectrograph", "Spectrograph", "Model, grating (lines/mm), slit width", ""),
            ("telescope", "Telescope", "Aperture and focal length", ""),
            ("camera", "Camera", "", ""),
        ):
            card.body.addWidget(label(title, "muted"))
            e = field(ph)
            e.editingFinished.connect(self._fields_changed)
            card.body.addWidget(e)
            if note:
                card.body.addWidget(label(note, "muted", style="font-size: 12px;"))
            self.eq[key] = e
        g = QGridLayout()
        g.addWidget(label("Resolving power (AAV_ITRP)", "muted"), 0, 0)
        self.rpower = num_field(10, 100000, "500")
        self.rpower.setFixedWidth(80)
        self.rpower.editingFinished.connect(self._fields_changed)
        g.addWidget(self.rpower, 0, 1, Qt.AlignmentFlag.AlignRight)
        card.body.addLayout(g)
        mr = QHBoxLayout()
        self.r_measured = label("", "muted", wrap=True, style="font-size: 12px;")
        mr.addWidget(self.r_measured, 1)
        self.r_use = QPushButton("Use")
        self.r_use.setToolTip("Use the resolving power measured by the Wavelength step")
        self.r_use.clicked.connect(self._use_measured_r)
        mr.addWidget(self.r_use)
        card.body.addLayout(mr)
        self.remember = QCheckBox("Remember for future sessions")
        self.remember.setChecked(True)
        self.remember.toggled.connect(self._fields_changed)
        card.body.addWidget(self.remember)
        return card

    # ---- state -------------------------------------------------------------------------------
    def observer(self) -> ex.ObserverInfo:
        def num(e: QLineEdit) -> float | None:
            try:
                return float(e.text().replace("−", "-").replace(",", "."))
            except ValueError:
                return None
        return ex.ObserverInfo(
            self.obscode.text().strip().upper(), self.obs_name.text().strip(), self.site_name.text().strip(),
            num(self.lat), num(self.lon), num(self.elev), self.eq["name"].text().strip(),
            self.eq["spectrograph"].text().strip(), self.eq["telescope"].text().strip(),
            self.eq["camera"].text().strip(), int(num(self.rpower) or ex.DEFAULT_RESOLVING_POWER))

    def _measured_r(self) -> tuple[int, float | None] | None:
        prof = self.host.profile
        if prof.resolving_power:
            return round(float(prof.resolving_power)), prof.fwhm_a
        return None

    def _show_measured_r(self) -> None:
        m = self._measured_r()
        self.r_measured.setText(f"Measured by Wavelength: R ≈ {m[0]}" + (f" (FWHM {m[1]:.1f} Å)" if m[1] else "")
                                if m else "Not measured yet (run Wavelength).")
        self.r_use.setVisible(bool(m) and str(m[0]) != self.rpower.text().strip() if m else False)

    def _use_measured_r(self) -> None:
        m = self._measured_r()
        if m:
            self.rpower.setText(str(m[0]))
            self._fields_changed()
            self._show_measured_r()

    def _stored(self) -> dict[str, Any]:
        sess = self.host.session.params.get("export") if hasattr(self.host.session, "params") else None
        return {**self.host.settings.data, **(sess or {})}

    def load_session(self) -> None:
        self._loading = True
        o = ex.ObserverInfo.from_settings(self._stored())
        self.obscode.setText(o.obscode)
        self.obs_name.setText(o.name)
        self.site_name.setText(o.site_name)
        for e, v in ((self.lat, o.lat), (self.lon, o.lon), (self.elev, o.elev)):
            e.setText("" if v is None else f"{v:g}")
        for k, v in (("name", o.instrument), ("spectrograph", o.spectrograph), ("telescope", o.telescope),
                     ("camera", o.camera)):
            self.eq[k].setText(v)
        self.rpower.setText(str(o.resolving_power))
        self._show_measured_r()
        self.site_from_hdr = bool((self._stored().get("site") or {}).get("from_headers", True))
        self._show_site(self.host.site())
        s = self.host.session
        exp = s.params.get("export", {}) if isinstance(s.params.get("export"), dict) else {}
        target = s.inputs["target"].get("name", "")
        self.objname.setText(exp.get("objname") if exp.get("objname_edited") else target)
        self.obj_hint.setText(f"OBJNAME (from Select: {target or '—'})")
        self.vsx = exp.get("vsx")
        self._loading = False
        self._show_vsx()
        self.reload()

    def _objname_edited(self, _text: str) -> None:
        s = self.host.session
        exp = dict(s.params.get("export", {}) or {})
        exp["objname_edited"] = True
        s.params["export"] = exp

    def _fields_changed(self) -> None:
        if self._loading:
            return
        o = self.observer()
        s = self.host.session
        exp = dict(s.params.get("export", {}) or {})
        exp["objname"] = self.objname.text().strip()
        values = o.to_settings()
        values["site"]["from_headers"] = self.site_from_hdr
        if self.remember.isChecked():
            self.host.settings.data.update(values)
            self.host.settings.save()
            exp.pop("observer", None)
        else:
            exp.update(values)
        s.params["export"] = exp
        s.save()
        self._refresh_header()

    def reload(self) -> None:
        """Load Target_R.fit (1D, small) for the plot and the header."""
        s = self.host.session
        path = s.folder / "Target_R.fit" if s.folder else None
        if path is None or not path.exists():
            self.banner.setText("Missing data: run all five steps first (Export publishes Target_R.fit).")
            self.banner.show()
            self.lam = self.flux = None
            self.curve.setData([], [])
            self.ov_curve.setData([], [])
            self._refresh_header()
            self.publish.refresh()
            return
        self.banner.hide()
        from astropy.io import fits

        self.flux = np.asarray(fits.getdata(path), dtype=np.float64)
        info = ex.read_header_info(path)
        self.r_header = info.cards
        self.lam = ex.wavelengths(self.r_header, len(self.flux))
        self.source_note.setText(f"target only · {path.name}")
        self.range_note.setText(f"exports {self.lam[0]:.0f}–{self.lam[-1]:.0f} Å (crop from Response)")
        self._plot()
        self._refresh_header()
        self.publish.refresh()  # a re-reduced spectrum resets the saved tiles
        self._update_steps()

    # ---- plot --------------------------------------------------------------------------------
    def _k(self) -> float:
        return 0.1 if self.nm else 1.0

    def _plot(self) -> None:
        if self.lam is None or self.flux is None:
            return
        x = self.lam * self._k()
        self.curve.setData(x, self.flux)
        self.ov_curve.setData(x, self.flux)
        self.overview.getPlotItem().setXRange(float(x[0]), float(x[-1]), padding=0)
        self.plot.getPlotItem().setLabel("bottom", "Wavelength (nm)" if self.nm else "Wavelength (Å)")
        self.x_unit_lbl.setText("nm" if self.nm else "Å")
        k = self._k()
        for m in self.markers["balmer"]:
            m.setValue(m.property("lam") * k)
        for m in self.markers["telluric"]:
            lam = m.property("lam") if hasattr(m, "property") else None
            if isinstance(m, pg.LinearRegionItem):
                a, b = lam
                m.setRegion((a * k, b * k))
            elif isinstance(m, pg.InfiniteLine):
                m.setValue(lam * k)
        import csv

        from starextractor.core.response import DATA_DIR
        with open(DATA_DIR / "aavso_telluric_lines.csv", encoding="utf-8") as fh:
            lines = [float(r["wavelength_A"]) for r in csv.DictReader(ln for ln in fh if not ln.startswith("#"))]
        lo = float(np.nanpercentile(self.flux, 1))
        self.ticks.setData([v * k for v in lines], [lo] * len(lines))
        self._zoom_changed(self.zoom.index())

    def _zoom_changed(self, idx: int) -> None:
        if self.lam is None:
            return
        rng = ZOOMS[idx][1]
        if rng is None:
            a, b = float(self.lam[0]), float(self.lam[-1])
        elif rng == "custom":
            self._custom_range()
            return
        else:
            a, b = rng
        self.plot.getPlotItem().setXRange(a * self._k(), b * self._k(), padding=0)

    def _custom_range(self) -> None:
        try:
            a, b = float(self.x_from.text()), float(self.x_to.text())
        except ValueError:
            return
        if a < b:
            self.zoom.set_index(len(ZOOMS) - 1)
            self.plot.getPlotItem().setXRange(a, b, padding=0)

    def _range_changed(self, *_a: Any) -> None:
        (a, b), _ = self.plot.getPlotItem().vb.viewRange()
        self.x_from.setText(f"{a:.0f}" if not self.nm else f"{a:.1f}")
        self.x_to.setText(f"{b:.0f}" if not self.nm else f"{b:.1f}")
        self.window.blockSignals(True)
        self.window.setRegion((a, b))
        self.window.blockSignals(False)
        self._peak(a / self._k(), b / self._k())

    def _window_moved(self) -> None:
        a, b = self.window.getRegion()
        self.plot.getPlotItem().setXRange(a, b, padding=0)

    def _peak(self, a: float, b: float) -> None:
        if self.lam is None or self.flux is None:
            return
        sel = (self.lam >= a) & (self.lam <= b) & np.isfinite(self.flux)
        if not sel.any():
            self.peak.setText("")
            return
        i = int(np.argmax(np.where(sel, self.flux, -np.inf)))
        lam = float(self.lam[i])
        name, rest = min(BALMER.items(), key=lambda kv: abs(kv[1] - lam))
        text = f"peak {lam * self._k():.1f} {'nm' if self.nm else 'Å'}"
        if abs(lam - rest) < 30:
            text += f" · Δ {(lam - rest) * self._k():+.1f} {'nm' if self.nm else 'Å'} from {name}"
        self.peak.setText(text)

    def _markers_visible(self) -> None:
        for m in self.markers["balmer"]:
            m.setVisible(self.show_balmer.isChecked())
        for m in self.markers["telluric"]:
            m.setVisible(self.show_telluric.isChecked())

    def _unit_changed(self, idx: int) -> None:
        (a, b), _ = self.plot.getPlotItem().vb.viewRange()
        a, b = a / self._k(), b / self._k()
        self.nm = idx == 1
        self._plot()
        self.plot.getPlotItem().setXRange(a * self._k(), b * self._k(), padding=0)

    # ---- header and checks ---------------------------------------------------------------------
    def _refresh_header(self) -> None:
        s = self.host.session
        if self.flux is None:
            self.plan = None
            self.table.setRowCount(0)
            self._update_steps()
            return
        coords = s.inputs["target"].get("coords") or {}
        self.plan = ex.build_header(self.r_header, self.observer(), self.objname.text().strip(), coords, s.folder)
        self._update_steps()
        keys = [k for k in FIRST_KEYS if k in self.plan.header] + sorted(
            k for k in self.plan.header if k not in FIRST_KEYS)
        self.table.setRowCount(len(keys) + 1)
        for r, k in enumerate(keys):
            v = self.plan.header[k]
            txt = f"'{v}'" if isinstance(v, str) else (f"{v:.10g}" if isinstance(v, float) else str(v))
            for c, t in enumerate((k, txt)):
                it = QTableWidgetItem(t)
                it.setFont(theme.mono_font(12))
                if c == 0 and k in FIRST_KEYS:
                    from PySide6.QtGui import QColor
                    it.setForeground(QColor(theme.AMBER))
                self.table.setItem(r, c, it)
        n = len(keys)
        self.table.setItem(n, 0, QTableWidgetItem("HISTORY"))
        self.table.setItem(n, 1, QTableWidgetItem(f"{len(self.plan.history)} lines describing the reduction"))

    def _fill_checks(self, lay: QVBoxLayout, checks: list[ex.Check]) -> None:
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.hide()  # deleteLater alone leaves it painted until the next event loop pass
                w.setParent(None)
                w.deleteLater()
        for c in checks:
            lay.addWidget(StatusLine(c.state, c.text))

    # ---- VSX -------------------------------------------------------------------------------------
    def check_vsx(self) -> None:
        name = self.objname.text().strip()
        if not name:
            self.vsx_status.set("warn", "Type the object name first.")
            return
        self.vsx_btn.setEnabled(False)
        self.vsx_status.set("off", f"Asking VSX for “{name}”…")
        url = str(self.host.settings["vsx_api_url"] or "") or DEFAULT_URL
        self.host.runner.submit("vsx.export", lookup, name, url)

    def on_vsx(self, res: dict[str, Any]) -> None:
        self.vsx_btn.setEnabled(True)
        self.vsx = res
        s = self.host.session
        exp = dict(s.params.get("export", {}) or {})
        exp["vsx"] = res
        s.params["export"] = exp
        s.save()
        s.log(f"VSX: {res.get('message', '')}")
        self._show_vsx()
        self._refresh_header()

    def _vsx_object(self) -> VsxObject | None:
        r = self.vsx or {}
        o = r.get("object")
        return VsxObject(**{k: v for k, v in o.items() if k in VsxObject.__dataclass_fields__}) if o else None

    def _show_vsx(self) -> None:
        r = self.vsx
        obj = self._vsx_object()
        self.vsx_meta.setText("")
        self.vsx_meta.hide()
        self.vsx_use.setEnabled(False)
        if r is None:
            self.vsx_status.set("off", "VSX check is optional.")
        elif obj is not None:
            same = obj.name.lower() == self.objname.text().strip().lower()
            cached = " (cached)" if r.get("cached") else ""
            self.vsx_status.set("ok" if same else "warn",
                                f"VSX: {obj.name}{cached}" + ("" if same else " — differs from OBJNAME"))
            coords = (f"RA {obj.ra_deg:.5f}°  Dec {obj.dec_deg:+.5f}° (J2000)"
                      if obj.ra_deg is not None and obj.dec_deg is not None else "")
            self.vsx_meta.setText("  ·  ".join(p for p in (obj.summary(), coords) if p))
            self.vsx_meta.show()
            self.vsx_use.setEnabled(not same or not self._target_coords())
        else:
            self.vsx_status.set("warn", r.get("message", "") + ("" if r.get("offline") else
                                                                  f" (check {VSX_SITE} if unsure)"))

    def _target_coords(self) -> bool:
        c = self.host.session.inputs["target"].get("coords") or {}
        return bool(c.get("ra") and c.get("dec"))

    def apply_vsx(self) -> None:
        """Use the VSX identity: OBJNAME, and the coordinates when the target has none yet."""
        obj = self._vsx_object()
        if obj is None:
            return
        from starextractor.core.coords import format_dec, format_ra

        s = self.host.session
        self.objname.setText(obj.name)
        self._objname_edited(obj.name)
        note = f"OBJNAME set to the VSX name {obj.name}"
        if not self._target_coords() and obj.ra_deg is not None and obj.dec_deg is not None:
            s.inputs["target"]["coords"] = {"source": "vsx", "ra": format_ra(obj.ra_deg),
                                            "dec": format_dec(obj.dec_deg)}
            note += (f"; target coordinates from VSX ({format_ra(obj.ra_deg)}, {format_dec(obj.dec_deg)}). "
                     "The Response step had no target position, so it now needs a re-run to apply the airmass "
                     "correction")
            self.host.inputs_changed()
        s.log(note)
        self._fields_changed()
        self._show_vsx()

    # ---- actions -----------------------------------------------------------------------------
    def _export_dir(self) -> Path:
        d = self.host.session.folder / "export"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def default_name(self) -> str:
        o = self.observer()
        return ex.export_name(self.objname.text().strip() or "target", self.plan.times if self.plan else None,
                              o.obscode or o.name)

    def open_folder(self) -> None:
        self.publish.open_folder()

    def site_from_headers(self) -> None:
        """Back to the default: the position stored in the frames (falls back to the typed one)."""
        self.site_from_hdr = True
        self._fields_changed()
        site = self.host.site()
        self._show_site(site)
        self.host.session.log(f"Location: {site.get('lat')}, {site.get('lon')} ({site.get('source')})")
        self._fields_changed()

    def _site_typed(self, _text: str) -> None:
        self.site_from_hdr = False
        self.site_source.setText("Position typed here (overrides the frame headers).")

    def _show_site(self, site: dict[str, Any]) -> None:
        self._loading, was = True, self._loading
        for e, k in ((self.lat, "lat"), (self.lon, "lon"), (self.elev, "elev")):
            v = site.get(k)
            e.setText("" if v is None else f"{v:g}")
        self._loading = was
        self.site_source.setText({"headers": "Position from the frame headers (SITELAT/SITELONG).",
                                  "typed": "Position typed here (overrides the frame headers).",
                                  "settings": "No position in the frame headers: using the saved one."
                                  }.get(site.get("source", ""), "Type the site position, or use frame headers."))

    def showEvent(self, e) -> None:
        super().showEvent(e)
        target = self.host.session.inputs["target"].get("name", "")
        self.obj_hint.setText(f"OBJNAME (from Select: {target or '—'})")
        edited = (self.host.session.params.get("export") or {}).get("objname_edited")
        if not edited and target and self.objname.text().strip() != target:
            self.objname.setText(target)
            self._fields_changed()
        self.reload()

