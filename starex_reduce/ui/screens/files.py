"""Step 1 · Files: lights (target + reference), calibration plan, stack, master library."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core import coords, simbad
from starex_reduce.core.files_stage import (
    SIDE_LABEL,
    build_masters_job,
    light_frames,
    measure_peaks,
    plan_for,
    scan_lights,
    tolerances,
)
from starex_reduce.core.frames import BIAS, DARK, FLAT, SATURATION_WARN, scan, summary_line
from starex_reduce.core.masters import CalPlan, fmt_exp, group_for_masters
from starex_reduce.core.pipeline import StageDef

from .. import theme
from ..widgets.common import Card, Segmented, StatusLine, label, section_title
from .base import StepScreen

if TYPE_CHECKING:
    from ..main_window import MainWindow

COORD_SOURCES = ("simbad", "header", "manual")
METHODS = (("Sigma-clip mean", "sigma_clip_mean"), ("Median", "median"))
HEADER_RA = ("RA", "OBJCTRA", "CRVAL1")
HEADER_DEC = ("DEC", "OBJCTDEC", "CRVAL2")


def short_path(path: str, parts: int = 2) -> str:
    """`…\2026-08-20\target` style: the last folders only (the full path goes in the tooltip)."""
    p = Path(path)
    tail = p.parts[-parts:]
    return path if len(p.parts) <= parts + 1 else "…\\" + "\\".join(tail)


def _fmt_int(v: float | None) -> str:
    return "—" if v is None else f"{v:,.0f}".replace(",", " ")


class LightsCard(Card):
    """One light set (target or reference star)."""

    changed = Signal()  # lights, ticks, names or calibration choices changed

    def __init__(self, side: str, host: MainWindow) -> None:
        title = "Target lights" if side == "target" else "Reference lights"
        super().__init__(title)
        self.side, self.host = side, host
        self.plan: CalPlan | None = None
        self._loading = False
        self.counter = label("", "mono")
        self.head.addWidget(self.counter)

        row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        self.folder.setPlaceholderText("Choose the folder with the lights…")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        self.body.addLayout(row)
        self.summary = label("", "mono")
        self.body.addWidget(self.summary)

        self.body.addWidget(self._build_name_box())
        self.body.addWidget(self._build_frames())
        self.body.addWidget(self._build_calibration())
        self.body.addLayout(self._build_stack_settings())

    # ---- construction ------------------------------------------------------------------------
    def _build_name_box(self) -> Card:
        box = Card("TARGET NAME", inner=True)
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Type the correct name")
        self.name.editingFinished.connect(self._name_edited)
        self.verify_btn = QPushButton("Verify with SIMBAD")
        self.verify_btn.clicked.connect(self.verify)
        row.addWidget(self.name, 1)
        row.addWidget(self.verify_btn)
        box.body.addLayout(row)
        self.header_name = label("", "muted")
        self.header_name.setTextFormat(Qt.TextFormat.RichText)
        box.body.addWidget(self.header_name)
        self.name_status = StatusLine("warn", "Not verified.")
        box.body.addWidget(self.name_status)
        prow = QHBoxLayout()  # the Plan's star for this side, when it differs (never copied by itself)
        self.plan_hint = label("", "muted", wrap=True, style="font-size: 12px;")
        prow.addWidget(self.plan_hint, 1)
        self.plan_btn = QPushButton("Use Plan")
        self.plan_btn.setToolTip("Take the name, coordinates and spectral type the Plan has for this star")
        self.plan_btn.clicked.connect(self.use_plan)
        prow.addWidget(self.plan_btn)
        self.plan_row = QWidget()
        self.plan_row.setLayout(prow)
        prow.setContentsMargins(0, 0, 0, 0)
        box.body.addWidget(self.plan_row)
        crow = QHBoxLayout()
        crow.addWidget(label("Coordinates from", "muted"))
        self.coord_src = Segmented(["SIMBAD", "From header", "Enter by hand"])
        self.coord_src.changed.connect(self._coord_source_changed)
        crow.addWidget(self.coord_src)
        crow.addStretch(1)
        box.body.addLayout(crow)
        grid = QGridLayout()
        self.ra = QLineEdit()
        self.dec = QLineEdit()
        self.ra.setPlaceholderText("hh mm ss.s")
        self.dec.setPlaceholderText("+dd mm ss")
        for w in (self.ra, self.dec):
            w.editingFinished.connect(self._coords_edited)
            w.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addWidget(label("RA", "muted"), 0, 0)
        grid.addWidget(self.ra, 0, 1)
        grid.addWidget(label("Dec", "muted"), 0, 2)
        grid.addWidget(self.dec, 0, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        box.body.addLayout(grid)
        self.coord_error = StatusLine("err", "")
        self.coord_error.hide()
        box.body.addWidget(self.coord_error)
        return box

    def _build_frames(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.frames_title = label("LOADED FRAMES", "eyebrow")
        head.addWidget(self.frames_title)
        head.addStretch(1)
        add = QPushButton("Add files…")
        add.clicked.connect(self.add_files)
        rm = QPushButton("Remove unticked")
        rm.clicked.connect(self.remove_unticked)
        head.addWidget(add)
        head.addWidget(rm)
        lay.addLayout(head)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["USE", "FILE", "TIME (UT)", "EXP", "PEAK ADU", ""])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setWordWrap(False)
        for c, width in ((0, 48), (2, 84), (3, 68), (4, 150), (5, 36)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(c, width)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setFixedHeight(38 * 5 + 34)
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellClicked.connect(self._cell_clicked)
        lay.addWidget(self.table)
        return w

    def _build_calibration(self) -> Card:
        box = Card("CALIBRATION IT WILL USE", inner=True)
        self.cal_lines = QVBoxLayout()
        self.cal_lines.setSpacing(6)
        box.body.addLayout(self.cal_lines)
        self.cal_actions = QHBoxLayout()
        box.body.addLayout(self.cal_actions)
        opts = QGridLayout()
        opts.setHorizontalSpacing(12)
        self.dark_pick = QComboBox()
        self.bias_pick = QComboBox()
        for c in (self.dark_pick, self.bias_pick):
            c.setMinimumWidth(170)
            c.currentIndexChanged.connect(self._choice_changed)
        opts.addWidget(label("Dark", "muted"), 0, 0)
        opts.addWidget(self.dark_pick, 0, 1)
        opts.addWidget(label("Bias", "muted"), 0, 2)
        opts.addWidget(self.bias_pick, 0, 3)
        opts.setColumnStretch(4, 1)
        box.body.addLayout(opts)
        self.pedestal_cb = QCheckBox("Match pedestal (subtract dark − bias only)")
        self.gain_cb = QCheckBox("Scale dark signal by gain ratio (ZWO 0.1 dB steps)")
        self.flat_cb = QCheckBox("Use a flat master")
        self.pedestal_cb.toggled.connect(lambda on: self._cal_set("match_pedestal", on))
        self.gain_cb.toggled.connect(lambda on: self._cal_set("gain_scale", on))
        self.flat_cb.toggled.connect(lambda on: self._cal_set("flat", on))
        for cb in (self.pedestal_cb, self.gain_cb, self.flat_cb):
            box.body.addWidget(cb)
        return box

    def _build_stack_settings(self) -> QGridLayout:
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        self.method = QComboBox()
        self.method.setMaximumWidth(170)
        for text, _ in METHODS:
            self.method.addItem(text)
        self.method.currentIndexChanged.connect(self._stack_changed)
        self.sigma = QDoubleSpinBox()
        self.sigma.setRange(1.0, 10.0)
        self.sigma.setSingleStep(0.5)
        self.sigma.setDecimals(1)
        self.sigma.setFixedWidth(80)
        self.sigma.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.sigma.valueChanged.connect(self._stack_changed)
        self.align = QCheckBox("Align frames")
        self.align.toggled.connect(self._stack_changed)
        g.addWidget(label("Stack method", "muted"), 0, 0)
        g.addWidget(self.method, 0, 1)
        g.addWidget(label("Rejection (σ)", "muted"), 0, 2)
        g.addWidget(self.sigma, 0, 3, Qt.AlignmentFlag.AlignRight)
        g.addWidget(self.align, 1, 0, 1, 2)
        g.addWidget(label("Off for a fixed slit. Turn on if the star drifts.", "muted",
                          style="font-size: 12px;"), 2, 0, 1, 4)
        g.setColumnStretch(2, 1)
        return g

    # ---- state ↔ widgets ---------------------------------------------------------------------
    @property
    def data(self) -> dict[str, Any]:
        return self.host.session.inputs[self.side]

    @property
    def cal(self) -> dict[str, Any]:
        return self.host.session.calibration[self.side]

    def load_from_session(self) -> None:
        self._loading = True
        d = self.data
        self.folder.setText(short_path(d["folder"]) if d.get("folder") else "")
        self.folder.setToolTip(d.get("folder", ""))
        self.name.setText(d.get("name", ""))
        src = d.get("coords", {}).get("source", "simbad")
        self.coord_src.set_index(COORD_SOURCES.index(src) if src in COORD_SOURCES else 0)
        self.ra.setText(d.get("coords", {}).get("ra", ""))
        self.dec.setText(d.get("coords", {}).get("dec", ""))
        st = d.get("stack", {})
        self.method.setCurrentIndex(1 if st.get("method") == "median" else 0)
        self.sigma.setValue(float(st.get("sigma", 3.0)))
        self.align.setChecked(bool(st.get("align", False)))
        self.gain_cb.setChecked(bool(self.cal.get("gain_scale", True)))
        self.flat_cb.setChecked(bool(self.cal.get("flat", False)))
        self._loading = False
        self._show_simbad()
        self._show_plan_hint()
        self.refresh_table()
        self.refresh_plan()

    def refresh_table(self) -> None:
        self._loading = True
        lights = self.data["lights"]
        frames = {str(f.path): f for f in light_frames(self.host.session, self.side, used_only=False)}
        self.table.setRowCount(len(lights))
        headers = []
        for r, item in enumerate(lights):
            f = frames.get(item["path"])
            headers.append(f)
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            use.setCheckState(Qt.CheckState.Checked if item.get("used", True) else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, use)
            name = QTableWidgetItem(Path(item["path"]).name)
            name.setToolTip(item["path"] + (f"\n{f.error}" if f and f.error else ""))
            if f and f.error:
                name.setForeground(theme_colour(theme.RED))
            self.table.setItem(r, 1, name)
            t = f.start.strftime("%H:%M:%S") if f and f.start else "—"
            self.table.setItem(r, 2, mono_item(t))
            self.table.setItem(r, 3, mono_item(f"{f.exptime:g} s" if f and f.exptime is not None else "—"))
            peak = item.get("peak")
            text = _fmt_int(peak) if peak is not None else ("…" if f and not f.error else "—")
            pk = mono_item(text + ("  near saturation" if peak and peak >= SATURATION_WARN else ""),
                           right=True)
            if peak and peak >= SATURATION_WARN:
                pk.setForeground(theme_colour(theme.AMBER))
            self.table.setItem(r, 4, pk)
            x = QTableWidgetItem("✕")
            x.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            x.setForeground(theme_colour(theme.MUTED))
            x.setToolTip("Remove this frame from the list")
            self.table.setItem(r, 5, x)
        used = sum(1 for i in lights if i.get("used", True))
        txt = f"{used} of {len(lights)} used" if lights else ""
        self.counter.setText(txt)
        self.frames_title.setText(f"LOADED FRAMES   {txt}" if lights else "LOADED FRAMES")
        ok = [f for f in headers if f]
        self.summary.setText(summary_line(ok) if ok else "")
        objs = sorted({f.object for f in ok if f.object})
        self.header_name.setText(
            f"Name in the file header: <span style='font-family:{theme.mono_font_family()}; "
            f"color:{theme.TEXT}'>{', '.join(objs)}</span>" if objs else "")
        self._loading = False

    def refresh_plan(self) -> None:
        s = self.host.session
        tol = tolerances(self.host.settings["tolerances"])
        self.plan = plan_for(s, self.side, self.host.library, tol)
        self._fill_pickers()
        _clear(self.cal_lines)
        _clear(self.cal_actions)
        plan = self.plan
        self.cal["resolved"] = plan.paths(self.host.library) if plan else []
        if plan is None:
            self.cal_lines.addWidget(StatusLine("off", "Load lights to see which masters fit."))
            self.pedestal_cb.hide()
            self.set_error(False)
            return
        for _kind, state, text in plan.lines:
            self.cal_lines.addWidget(StatusLine(state, text))
        for issue in plan.issues:
            if issue.code in ("no_dark",):
                continue  # already explained by the dark line
            self.cal_lines.addWidget(StatusLine("err" if issue.severity == "error" else "warn", issue.text))
            if issue.action == "use_anyway":
                b = QPushButton("Use anyway")
                b.clicked.connect(lambda _=False, c=issue.code: self._accept(c))
                self.cal_actions.addWidget(b)
        if any(i.action in ("create_dark", "create_bias") for i in plan.issues):
            b = QPushButton("Create a master ↓")
            b.clicked.connect(self.host_scroll_to_masters)
            self.cal_actions.addWidget(b)
        self.cal_actions.addStretch(1)
        mismatch = any(i.code in ("pedestal_match", "pedestal_needs_bias", "calib_mismatch",
                                  "calib_mismatch_accepted") for i in plan.issues)
        self.pedestal_cb.setVisible(mismatch)
        self.gain_cb.setVisible(mismatch)
        self._loading = True
        self.pedestal_cb.setChecked(self.cal.get("match_pedestal") is not False)
        self._loading = False
        self.set_error(bool(self.data["lights"]) and not plan.ok)

    def _fill_pickers(self) -> None:
        self._loading = True
        lib = self.host.library
        for combo, kind in ((self.dark_pick, DARK), (self.bias_pick, BIAS)):
            combo.clear()
            combo.addItem("Auto (best match)", "auto")
            combo.addItem("None", "none")
            for m in lib.by_kind(kind):
                combo.addItem(f"{m.kind.title()} {fmt_exp(m.exptime)} · gain {m.gain:g} · {m.date}"
                              if m.gain is not None else m.label(), m.id)
            choice = self.cal.get("choice", {}).get(kind, "auto")
            idx = combo.findData(choice)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._loading = False

    # ---- user actions ------------------------------------------------------------------------
    def browse(self) -> None:
        start = self.data.get("folder") or self.host.settings["last_folder"] or ""
        folder = QFileDialog.getExistingDirectory(self, f"{SIDE_LABEL[self.side]} lights folder", start)
        if folder:
            self.host.settings["last_folder"] = str(Path(folder).parent)
            self.host.runner.submit(f"scan.{self.side}", scan_lights, folder)

    def on_scanned(self, result: dict[str, Any]) -> None:
        d = self.data
        d["folder"] = result["folder"]
        d["lights"] = [{"path": str(f.path), "used": not f.error} for f in result["frames"]]
        objs = [f.object for f in result["frames"] if f.object]
        if objs and not d.get("name"):
            d["header_name"] = objs[0]
        n = len(result["frames"])
        self.host.session.log(f"{SIDE_LABEL[self.side]} lights: {n} frames in {result['folder']}"
                              + (f" ({len(result['skipped'])} non-light files skipped)" if result["skipped"] else ""))
        self.load_from_session()
        self._measure_peaks()
        self.changed.emit()

    def _measure_peaks(self) -> None:
        todo = [i["path"] for i in self.data["lights"] if i.get("peak") is None]
        if todo:
            self.host.runner.submit(f"peaks.{self.side}", measure_peaks, todo)

    def on_peaks(self, peaks: dict[str, float | None]) -> None:
        for item in self.data["lights"]:
            if item["path"] in peaks:
                item["peak"] = peaks[item["path"]]
        sat = [Path(p).name for p, v in peaks.items() if v is not None and v >= SATURATION_WARN]
        if sat:
            self.host.session.log(f"{SIDE_LABEL[self.side]}: near saturation in {', '.join(sat)}")
        self.refresh_table()
        self.host.session.save()

    def add_files(self) -> None:
        start = self.data.get("folder") or self.host.settings["last_folder"] or ""
        files, _ = QFileDialog.getOpenFileNames(self, "Add light frames", start, "FITS (*.fits *.fit *.fts)")
        known = {i["path"] for i in self.data["lights"]}
        new = [str(Path(f)) for f in files if str(Path(f)) not in known]
        if new:
            self.data["lights"] += [{"path": p, "used": True} for p in new]
            self.host.session.log(f"{SIDE_LABEL[self.side]}: added {len(new)} files")
            self.refresh_table()
            self._measure_peaks()
            self._emit()

    def remove_unticked(self) -> None:
        before = len(self.data["lights"])
        self.data["lights"] = [i for i in self.data["lights"] if i.get("used", True)]
        if len(self.data["lights"]) != before:
            removed = before - len(self.data["lights"])
            self.host.session.log(f"{SIDE_LABEL[self.side]}: removed {removed} unticked frames")
            self.refresh_table()
            self._emit()

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        self.data["lights"][item.row()]["used"] = item.checkState() == Qt.CheckState.Checked
        self.refresh_table()
        self._emit()

    def _cell_clicked(self, row: int, col: int) -> None:
        if col == 5 and 0 <= row < len(self.data["lights"]):
            name = Path(self.data["lights"][row]["path"]).name
            del self.data["lights"][row]
            self.host.session.log(f"{SIDE_LABEL[self.side]}: removed {name}")
            self.refresh_table()
            self._emit()

    def _name_edited(self) -> None:
        name = self.name.text().strip()
        if name != self.data.get("name", ""):
            self.data["name"] = name
            self.data["simbad"] = {}
            self._show_simbad()
            self._show_plan_hint()
            self._emit(plan=False)

    def set_name(self, name: str) -> None:
        self.name.setText(name)
        self._name_edited()

    def _show_plan_hint(self) -> None:
        from starex_reduce.core.identity import plan_entry, same_name

        pe = plan_entry(self.host.session, self.side)
        name = self.data.get("name", "")
        show = pe is not None and not same_name(name, pe["name"])
        if show:
            pos = f" · {pe['ra']}  {pe['dec']}" if pe["ra"] else ""
            sp = f" · {pe['sp_type']}" if pe["sp_type"] else ""
            self.plan_hint.setText(f"The Plan has {pe['name']}{pos}{sp}"
                                   + (f" (Files has {name})." if name else "."))
        self.plan_row.setVisible(show)

    def use_plan(self) -> None:
        """Name, coordinates (as typed values, source 'manual') and, through the name, the spectral type."""
        from starex_reduce.core.identity import plan_entry

        pe = plan_entry(self.host.session, self.side)
        if pe is None:
            return
        d = self.data
        d["name"], d["simbad"] = pe["name"], {}
        if pe["ra"]:
            d["coords"] = {"source": "manual", "ra": pe["ra"], "dec": pe["dec"]}
        self.host.session.log(f"{SIDE_LABEL[self.side]}: {pe['name']} from the Plan"
                              + (f" ({pe['ra']} {pe['dec']}" + (f", {pe['sp_type']}" if pe["sp_type"] else "") + ")"
                                 if pe["ra"] else ""))
        self.load_from_session()
        self._emit(plan=False)

    def verify(self) -> None:
        name = self.name.text().strip() or self.data.get("header_name", "")
        if not name:
            self.name_status.set("warn", "Type a name first.")
            return
        self.name.setText(name)
        self._name_edited()
        self.name_status.set("off", f"Asking SIMBAD for “{name}”…")
        self.verify_btn.setEnabled(False)
        self.host.runner.submit(f"simbad.{self.side}", simbad_job, name)

    def on_simbad(self, res: dict[str, Any]) -> None:
        self.verify_btn.setEnabled(True)
        self.data["simbad"] = res
        if res.get("found"):
            self.data["coords"].update(source="simbad", ra=coords.format_ra(res["ra"]),
                                       dec=coords.format_dec(res["dec"]))
            self.host.session.log(f"SIMBAD: {res['query']} = {res['main_id']} ({res.get('sp_type') or 'no type'})")
        elif res.get("offline"):
            self.host.session.log(f"SIMBAD: offline ({res.get('error', '')[:80]})")
        self.load_from_session()
        self._emit(plan=False)

    def _show_simbad(self) -> None:
        res = self.data.get("simbad") or {}
        if res.get("found"):
            sp = f" · {res['sp_type']}" if res.get("sp_type") else ""
            name = res.get("main_id") or res.get("query", "")
            self.name_status.set("ok", f"Resolved by SIMBAD as {name}{sp}. Offline? Pick "
                                       "“From header” or enter the coordinates by hand.")
        elif res.get("offline"):
            self.name_status.set("warn", "SIMBAD could not be reached (offline?). Pick “From header” "
                                         "or enter the coordinates by hand.")
        elif res.get("query"):
            self.name_status.set("warn", f"“{res['query']}” is not known to SIMBAD. Type the catalogue "
                                         "name and verify, or enter the coordinates by hand.")
        else:
            self.name_status.set("warn", "Not verified. Verify the name with SIMBAD, or enter the "
                                         "coordinates by hand if you are offline.")

    def _coord_source_changed(self, idx: int) -> None:
        if self._loading:
            return
        src = COORD_SOURCES[idx]
        c = self.data["coords"]
        c["source"] = src
        self.coord_error.hide()
        if src == "header":
            ra, dec = self._header_coords()
            if ra is None:
                self.coord_error.set("err", "The light frames have no RA/Dec in their header.")
                self.coord_error.show()
            else:
                c.update(ra=coords.format_ra(ra), dec=coords.format_dec(dec))
        elif src == "simbad" and (self.data.get("simbad") or {}).get("found"):
            res = self.data["simbad"]
            c.update(ra=coords.format_ra(res["ra"]), dec=coords.format_dec(res["dec"]))
        self.ra.setText(c.get("ra", ""))
        self.dec.setText(c.get("dec", ""))
        self.ra.setReadOnly(src != "manual")
        self.dec.setReadOnly(src != "manual")
        self._emit(plan=False)

    def _header_coords(self) -> tuple[float | None, float | None]:
        frames = light_frames(self.host.session, self.side)
        for f in frames:
            for rk, dk in zip(HEADER_RA, HEADER_DEC, strict=True):
                ra, dec = f.h(rk), f.h(dk)
                try:
                    if isinstance(ra, (int, float)) and isinstance(dec, (int, float)):
                        return float(ra), float(dec)
                    if isinstance(ra, str) and isinstance(dec, str):
                        return coords.parse_ra(ra), coords.parse_dec(dec)
                except ValueError:
                    continue
        return None, None

    def _coords_edited(self) -> None:
        if self._loading:
            return
        c = self.data["coords"]
        self.coord_error.hide()
        try:
            if self.ra.text().strip():
                coords.parse_ra(self.ra.text())
            if self.dec.text().strip():
                coords.parse_dec(self.dec.text())
        except ValueError as exc:
            self.coord_error.set("err", f"{exc}. Use e.g. RA 18 36 56.3 and Dec +38 47 01.")
            self.coord_error.show()
            return
        c.update(ra=self.ra.text().strip(), dec=self.dec.text().strip())
        self._emit(plan=False)

    def _stack_changed(self) -> None:
        if self._loading:
            return
        self.data["stack"] = {"method": METHODS[self.method.currentIndex()][1],
                              "sigma": round(self.sigma.value(), 2), "align": self.align.isChecked()}
        self.sigma.setEnabled(self.data["stack"]["method"] == "sigma_clip_mean")
        self._emit(plan=False)

    def _choice_changed(self) -> None:
        if self._loading:
            return
        self.cal["choice"] = {DARK: self.dark_pick.currentData(), BIAS: self.bias_pick.currentData()}
        self.cal["accepted"] = []
        self._emit()

    def _cal_set(self, key: str, value: Any) -> None:
        if self._loading:
            return
        self.cal[key] = value
        self._emit()

    def _accept(self, code: str) -> None:
        acc = set(self.cal.get("accepted", []))
        acc.add(code)
        self.cal["accepted"] = sorted(acc)
        self.host.session.log(f"{SIDE_LABEL[self.side]}: calibration mismatch used anyway ({code})")
        self._emit()

    def _emit(self, plan: bool = True) -> None:
        if plan:
            self.refresh_plan()
        self.changed.emit()

    def host_scroll_to_masters(self) -> None:
        self.window().findChild(FilesScreen).scroll_to_masters()  # type: ignore[union-attr]


def simbad_job(ctx: Any, name: str) -> dict[str, Any]:
    """Runs in a worker: one Sesame request, offline-safe."""
    try:
        r = simbad.lookup(name, timeout=4.0)  # per mirror; Sesame answers in well under a second
    except simbad.LookupError_ as exc:
        return {"query": name, "found": False, "offline": True, "error": str(exc)}
    if not r.found:
        return {"query": name, "found": False}
    return {"query": name, "found": True, "main_id": r.main_id, "name": r.usable_name,
            "ra": r.ra_deg, "dec": r.dec_deg, "sp_type": r.sp_type, "otype": r.otype, "vmag": r.vmag}


def theme_colour(hex_colour: str):
    from PySide6.QtGui import QColor
    return QColor(hex_colour)


def mono_item(text: str, right: bool = False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFont(theme.mono_font(12))
    if right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return it


def _clear(layout: QHBoxLayout | QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()


class MasterBin(Card):
    """One drop/choose bin in Create masters (Darks, Bias, Flats)."""

    def __init__(self, kind: str, title: str, note: str, host: MainWindow) -> None:
        super().__init__(title, note, inner=True)
        self.kind, self.host = kind, host
        self.title.setProperty("role", None)
        self.title.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.groups: list = []
        row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setReadOnly(True)
        self.folder.setPlaceholderText(f"Folder with raw {title.lower()}…")
        b = QPushButton("Browse…")
        b.clicked.connect(self.browse)
        row.addWidget(self.folder, 1)
        row.addWidget(b)
        self.body.addLayout(row)
        self.found = label("", "mono", wrap=True)
        self.body.addWidget(self.found)

    def browse(self) -> None:
        start = self.host.settings["last_folder"] or ""
        folder = QFileDialog.getExistingDirectory(self, f"Raw {self.kind} frames", start)
        if folder:
            self.set_folder(folder)

    def set_folder(self, folder: str) -> None:
        self.folder.setText(short_path(folder))
        self.folder.setToolTip(folder)
        frames = [f for f in scan(folder, self.kind) if f.frame_type == self.kind or f.error]
        self.groups = group_for_masters([f for f in frames if not f.error])
        parts = [f"{fmt_exp(g[0].exptime or 0)} × {len(g)}" for g in self.groups]
        bad = sum(1 for f in frames if f.error)
        self.found.setText(
            (f"Found: {' · '.join(parts)}\nWill create {len(self.groups)} master"
             f"{'s' if len(self.groups) != 1 else ''}" if parts else f"No {self.kind} frames found")
            + (f"\n{bad} unreadable file(s) skipped" if bad else ""))


class FilesScreen(StepScreen):
    def __init__(self, stage: StageDef, total: int, host: MainWindow) -> None:
        super().__init__(stage, total)
        self.host = host
        self.header.btn_this.setText("Run this image")
        lay = self.body_layout
        lay.addWidget(section_title("A", "Lights", "Target and reference star"))
        from .files_suggest import SuggestionCard

        self.suggestion = SuggestionCard(host, self)  # optional: Files works the same without a sets file
        cards = QHBoxLayout()
        cards.setSpacing(16)
        self.cards = {"target": LightsCard("target", host), "reference": LightsCard("reference", host)}
        for c in self.cards.values():
            c.changed.connect(self._inputs_changed)
            cards.addWidget(c, 1, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self.suggestion)
        lay.addLayout(cards)
        lay.addWidget(self._build_stack_bar())
        self.masters_title = section_title("B", "Calibration frames", "Darks, bias and optional flats")
        lay.addWidget(self.masters_title)
        lay.addWidget(self._build_library())
        lay.addWidget(self._build_create())
        lay.addStretch(1)
        self.load_session()

    # ---- construction ------------------------------------------------------------------------
    def _build_stack_bar(self) -> Card:
        bar = Card()
        row = QHBoxLayout()
        col = QVBoxLayout()
        col.addWidget(label("Only the ticked frames are stacked. Each set is calibrated with the "
                            "masters from the library below.", wrap=True))
        self.stack_problem = label("", wrap=True, style=f"color: {theme.RED};")
        col.addWidget(self.stack_problem)
        row.addLayout(col, 1)
        self.btn_target = QPushButton("Stack target only")
        self.btn_both = QPushButton("Stack target + reference →")
        self.btn_both.setProperty("role", "primary")
        self.btn_target.clicked.connect(lambda: self.host.run_stage("files", ["Target"]))
        self.btn_both.clicked.connect(lambda: self.host.run_stage("files", ["Target", "Reference"]))
        row.addWidget(self.btn_target)
        row.addWidget(self.btn_both)
        bar.body.addLayout(row)
        return bar

    def _build_library(self) -> Card:
        card = Card("Master library", "Tick the masters the auto-pick may use. The best match by "
                                      "exposure, gain, temperature and size is picked automatically.")
        imp = QPushButton("Import master…")
        imp.clicked.connect(self.import_master)
        rm = QPushButton("Remove")
        rm.clicked.connect(self.remove_master)
        card.head.addWidget(imp)
        card.head.addWidget(rm)
        self.lib_table = QTableWidget(0, 10)
        self.lib_table.setHorizontalHeaderLabels(
            ["USE", "TYPE", "EXPOSURE", "GAIN", "OFFSET", "TEMP", "SIZE", "FRAMES", "CREATED", "USED BY"])
        self.lib_table.verticalHeader().hide()
        self.lib_table.setShowGrid(False)
        self.lib_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lib_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.lib_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lib_table.verticalHeader().setDefaultSectionSize(38)
        self.lib_table.setWordWrap(False)
        hh = self.lib_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.lib_table.setColumnWidth(0, 48)
        self.lib_table.itemChanged.connect(self._lib_item_changed)
        card.body.addWidget(self.lib_table)
        self.lib_empty = label("The library is empty. Create masters below from raw darks and bias.",
                               "muted")
        card.body.addWidget(self.lib_empty)
        return card

    def _build_create(self) -> Card:
        card = Card("Create masters", "Point at raw calibration frames. One master is made per "
                                      "exposure time found, then added to the library.")
        bins = QHBoxLayout()
        bins.setSpacing(12)
        self.bins = {
            DARK: MasterBin(DARK, "Darks", "required", self.host),
            BIAS: MasterBin(BIAS, "Bias", "optional", self.host),
            FLAT: MasterBin(FLAT, "Flats", "optional", self.host),
        }
        for b in self.bins.values():
            bins.addWidget(b, 1)
        card.body.addLayout(bins)
        row = QHBoxLayout()
        row.addWidget(label("Method", "muted"))
        self.master_method = QComboBox()
        self.master_method.addItem("Median", "median")
        self.master_method.addItem("Sigma-clip mean", "sigma_clip_mean")
        row.addWidget(self.master_method)
        self.sub_bias = QCheckBox("Subtract bias from darks and flats")
        self.sub_bias.setChecked(True)
        row.addSpacing(12)
        row.addWidget(self.sub_bias)
        row.addStretch(1)
        self.master_progress = QProgressBar()
        self.master_progress.setRange(0, 1000)
        self.master_progress.setTextVisible(False)
        self.master_progress.setFixedSize(160, 8)
        self.master_progress.hide()
        row.addWidget(self.master_progress)
        self.create_btn = QPushButton("Create masters")
        self.create_btn.setProperty("role", "primary")
        self.create_btn.clicked.connect(self.create_masters)
        row.addWidget(self.create_btn)
        card.body.addLayout(row)
        return card

    # ---- session -----------------------------------------------------------------------------
    def load_session(self) -> None:
        for c in self.cards.values():
            c.load_from_session()
        self.suggestion.load_session()
        self.refresh_library()
        self._update_problems()

    def _inputs_changed(self) -> None:
        self.refresh_library()
        self._update_problems()
        self.host.inputs_changed()

    def _update_problems(self) -> None:
        msgs = []
        for side, c in self.cards.items():
            if not self.host.session.inputs[side]["lights"]:
                continue
            if c.plan is not None and not c.plan.ok:
                first = next(i for i in c.plan.issues if i.severity == "error")
                prefix = "" if first.text.startswith(SIDE_LABEL[side]) else f"{SIDE_LABEL[side]}: "
                msgs.append(prefix + first.text)
        self.stack_problem.setText(" · ".join(msgs))
        self.stack_problem.setVisible(bool(msgs))

    def ribbon_problem(self) -> str | None:
        """Short red ribbon text when a loaded light set cannot be calibrated."""
        for side, c in self.cards.items():
            if self.host.session.inputs[side]["lights"] and c.plan is not None and not c.plan.ok:
                if any(i.code == "no_dark" for i in c.plan.issues):
                    return f"{SIDE_LABEL[side]} needs a dark"
                return f"{SIDE_LABEL[side]}: calibration"
        return None

    def set_name(self, side: str, name: str) -> None:
        self.cards[side].set_name(name)

    def scroll_to_masters(self) -> None:
        sa = self.body.parentWidget().parentWidget()
        sa.ensureWidgetVisible(self.masters_title, 0, 0)  # type: ignore[attr-defined]

    # ---- library -----------------------------------------------------------------------------
    def refresh_library(self) -> None:
        lib = self.host.library
        used: dict[str, list[str]] = {}
        for side, c in self.cards.items():
            if c.plan is None:
                continue
            for m in (c.plan.dark, c.plan.bias, c.plan.flat):
                if m is not None:
                    used.setdefault(m.id, []).append(SIDE_LABEL[side])
        t = self.lib_table
        t.blockSignals(True)
        t.setRowCount(len(lib.masters))
        for r, m in enumerate(sorted(lib.masters, key=lambda m: (m.kind, m.exptime))):
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            use.setCheckState(Qt.CheckState.Checked if m.enabled else Qt.CheckState.Unchecked)
            use.setData(Qt.ItemDataRole.UserRole, m.id)
            t.setItem(r, 0, use)
            kind = m.kind.title() + (" (−bias)" if m.bias_subtracted else "")
            vals = [kind, fmt_exp(m.exptime), "—" if m.gain is None else f"{m.gain:g}",
                    "—" if m.offset is None else f"{m.offset:g}",
                    "—" if m.temp is None else f"{m.temp:.1f} °C".replace("-", "−"),
                    f"{m.size[0]}×{m.size[1]}", str(m.nframes), m.created]
            for c, v in enumerate(vals, start=1):
                it = mono_item(v) if c > 1 else QTableWidgetItem(v)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                t.setItem(r, c, it)
            u = " and ".join(used.get(m.id, [])) or "—"
            ui = QTableWidgetItem(u)
            ui.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if m.id in used:
                ui.setForeground(theme_colour(theme.GREEN))
            t.setItem(r, 9, ui)
        t.setFixedHeight(42 + 38 * max(1, min(len(lib.masters), 8)))
        t.blockSignals(False)
        t.setVisible(bool(lib.masters))
        self.lib_empty.setVisible(not lib.masters)

    def _lib_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        mid = item.data(Qt.ItemDataRole.UserRole)
        self.host.library.set_enabled(mid, item.checkState() == Qt.CheckState.Checked)
        self._replan()

    def _replan(self) -> None:
        for c in self.cards.values():
            c.refresh_plan()
        self._inputs_changed()

    def import_master(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Import master frames", "", "FITS (*.fits *.fit *.fts)")
        for f in files:
            try:
                m = self.host.library.import_file(Path(f))
                self.host.session.log(f"Imported master {m.label()} from {Path(f).name}")
            except ValueError as exc:
                self.host.session.log(f"Import failed: {exc}")
        if files:
            self._replan()

    def remove_master(self) -> None:
        rows = {i.row() for i in self.lib_table.selectedItems()}
        for r in rows:
            mid = self.lib_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            m = self.host.library.get(mid)
            if m is not None:
                self.host.library.remove(mid)
                self.host.session.log(f"Removed master {m.label()} from the library")
        if rows:
            self._replan()

    def create_masters(self) -> None:
        groups = [(kind, g) for kind, b in self.bins.items() for g in b.groups]
        if not groups:
            self.host.session.log("Create masters: choose a folder with raw darks or bias first")
            return
        tol = tolerances(self.host.settings["tolerances"])
        self.create_btn.setEnabled(False)
        self.master_progress.setValue(0)
        self.master_progress.show()
        self.host.runner.submit("masters", build_masters_job, groups, self.host.library,
                                self.master_method.currentData(), self.sub_bias.isChecked(), tol,
                                log=self.host.session.log)

    def masters_progress(self, f: float) -> None:
        self.master_progress.setValue(int(f * 1000))

    def masters_done(self, lines: list[str] | None, error: str | None = None) -> None:
        self.create_btn.setEnabled(True)
        self.master_progress.hide()
        for line in lines or []:
            self.host.session.log(line)
        if error:
            self.host.session.log(f"Create masters failed: {error}")
        self._replan()  # the worker added the new masters to the shared library object
