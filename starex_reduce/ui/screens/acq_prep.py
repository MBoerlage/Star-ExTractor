"""Acquisition > Pre Processing settings: the Masters location (the same setting as Settings › Data locations),
the night's Stacks folder, the master library and Create masters widgets, master tolerances, and the Stacking
settings that Stack selected uses (kept in Stacks/<night>/acquisition.json with the rest of this tab's state)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from starex_reduce.core.files_stage import plan_from, tolerances
from starex_reduce.core.frames import BIAS, DARK, LIGHT, load_frame
from starex_reduce.core.masters import CalPlan, fmt_exp
from starex_reduce.core.settings import DEFAULTS

from .. import dialogs
from ..widgets.common import Card, StatusLine, label
from ..widgets.masters_panel import CreateMastersCard, LibraryCard

if TYPE_CHECKING:
    from ..main_window import MainWindow
    from .acquisition import AcquisitionScreen

METHODS = (("Sigma-clip mean", "sigma_clip_mean"), ("Median", "median"), ("Mean", "mean"))
STACK_DEFAULTS = {"method": "sigma_clip_mean", "sigma": 3.0, "align": False}  # = Reduce › Select' defaults
CAL_DEFAULTS: dict[str, Any] = {"choice": {DARK: "auto", BIAS: "auto"}, "match_pedestal": None,
                                "gain_scale": True, "flat": False}


class PrepPage(QWidget):
    def __init__(self, host: MainWindow, screen: AcquisitionScreen) -> None:
        super().__init__()
        self.host, self.screen = host, screen
        self._loading = False
        self.plans: dict[str, CalPlan | None] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(self._build_folders())
        self.library_card = LibraryCard(host, self._used_by, "Calibration frames")
        lay.addWidget(self.library_card)
        self.create_card = CreateMastersCard(host)
        lay.addWidget(self.create_card)
        lay.addWidget(self._build_tolerances())
        lay.addWidget(self._build_stacking())
        lay.addStretch(1)
        host.library_changed.connect(self._library_changed)

    # ---- construction ----------------------------------------------------------------------------------
    def _build_folders(self) -> QFrame:
        box = QFrame()
        box.setObjectName("acqbox")
        g = QGridLayout(box)
        g.setContentsMargins(16, 14, 16, 14)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)
        self.lib_folder = QLineEdit(str(self.host.settings.masters_dir()))
        self.lib_folder.editingFinished.connect(lambda: self.host.set_masters_dir(self.lib_folder.text()))
        b = QPushButton("Browse…")
        b.clicked.connect(self.browse_library)
        g.addWidget(label("Masters folder", "muted"), 0, 0)
        g.addWidget(self.lib_folder, 0, 1)
        g.addWidget(b, 0, 2)
        g.addWidget(label("Where the calibration masters (bias, darks, flats) are kept, in <camera>\\<year> folders "
                          "with index.json. The same setting as Settings › Data locations › Masters.",
                          "muted", wrap=True, style="font-size: 12px;"), 1, 1, 1, 2)
        self.project = QLineEdit()
        self.project.setReadOnly(True)
        o = QPushButton("Open folder")
        o.clicked.connect(lambda: self.screen.open_project_folder())
        g.addWidget(label("Stacks folder", "muted"), 2, 0)
        g.addWidget(self.project, 2, 1)
        g.addWidget(o, 2, 2)
        g.addWidget(label("Stack selected writes <Name>_target_S.fit / <Name>_ref_S.fit here, with the sets file "
                          "(Stacks\\<night of the first sub>).", "muted", wrap=True, style="font-size: 12px;"),
                    3, 1, 1, 2)
        g.setRowMinimumHeight(1, 22)
        g.setColumnStretch(1, 1)
        return box

    def _build_tolerances(self) -> Card:
        card = Card("Master tolerances", "How far a master may differ from the lights. A shared setting: "
                                         "also used by Reduce › Select.")
        tol = {**DEFAULTS["tolerances"], **(self.host.settings["tolerances"] or {})}
        self.tol_enabled = QCheckBox("Warn when masters do not match the lights")
        self.tol_enabled.setChecked(bool(tol["enabled"]))
        self.tol_enabled.toggled.connect(self._tol_changed)
        card.body.addWidget(self.tol_enabled)
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        self.tol_gain, self.tol_offset, self.tol_temp = (QDoubleSpinBox() for _ in range(3))
        for col, (title, box, value, top) in enumerate((("Gain ±%", self.tol_gain, 100 * tol["gain_rel"], 1000),
                                                        ("Offset ±%", self.tol_offset, 100 * tol["offset_rel"], 1000),
                                                        ("Temperature ±°C", self.tol_temp, tol["temp_c"], 50))):
            box.setRange(0.0, top)
            box.setDecimals(1)
            box.setFixedWidth(96)
            box.setAlignment(Qt.AlignmentFlag.AlignRight)
            box.setValue(float(value))
            box.valueChanged.connect(self._tol_changed)
            g.addWidget(label(title, "muted"), 0, 2 * col)
            g.addWidget(box, 0, 2 * col + 1)
        g.setColumnStretch(6, 1)
        card.body.addLayout(g)
        return card

    def _tol_changed(self) -> None:
        self.host.settings.data["tolerances"] = {
            "gain_rel": round(self.tol_gain.value() / 100, 4), "offset_rel": round(self.tol_offset.value() / 100, 4),
            "temp_c": round(self.tol_temp.value(), 2), "enabled": self.tol_enabled.isChecked()}
        self.host.settings.save()
        self.host.library_changed.emit()

    def _build_stacking(self) -> Card:
        card = Card("Stacking", "Used by Stack selected on the Target and Reference tabs.")
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        self.method = QComboBox()
        self.method.setMaximumWidth(170)
        for text, _ in METHODS:
            self.method.addItem(text)
        self.method.currentIndexChanged.connect(self._changed)
        self.sigma = QDoubleSpinBox()
        self.sigma.setRange(1.0, 10.0)
        self.sigma.setSingleStep(0.5)
        self.sigma.setDecimals(1)
        self.sigma.setFixedWidth(80)
        self.sigma.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.sigma.valueChanged.connect(self._changed)
        g.addWidget(label("Stack method", "muted"), 0, 0)
        g.addWidget(self.method, 0, 1)
        g.addWidget(label("Rejection (σ)", "muted"), 0, 2)
        g.addWidget(self.sigma, 0, 3, Qt.AlignmentFlag.AlignLeft)
        self.dark_pick, self.bias_pick = QComboBox(), QComboBox()
        for c in (self.dark_pick, self.bias_pick):
            c.setMinimumWidth(200)
            c.currentIndexChanged.connect(self._changed)
        g.addWidget(label("Dark", "muted"), 1, 0)
        g.addWidget(self.dark_pick, 1, 1)
        g.addWidget(label("Bias", "muted"), 1, 2)
        g.addWidget(self.bias_pick, 1, 3)
        g.setColumnStretch(4, 1)
        card.body.addLayout(g)
        self.pedestal_cb = QCheckBox("Match pedestal (subtract dark − bias only)")
        self.gain_cb = QCheckBox("Scale dark signal by gain ratio (ZWO 0.1 dB steps)")
        self.flat_cb = QCheckBox("Use a flat master")
        self.align = QCheckBox("Align frames")
        for cb in (self.pedestal_cb, self.gain_cb, self.flat_cb, self.align):
            cb.toggled.connect(self._changed)
            card.body.addWidget(cb)
        card.body.addWidget(label("Off for a fixed slit. Turn on if the star drifts.", "muted",
                                  style="font-size: 12px;"))
        card.body.addWidget(label("CALIBRATION IT WILL USE", "eyebrow"))
        self.cal_lines = QVBoxLayout()
        self.cal_lines.setSpacing(6)
        card.body.addLayout(self.cal_lines)
        return card

    # ---- state -------------------------------------------------------------------------------------------
    def _acq(self) -> dict[str, Any]:
        return self.screen._state()

    def stack_params(self) -> dict[str, Any]:
        return {**STACK_DEFAULTS, **(self._acq().get("stack") or {})}

    def cal(self) -> dict[str, Any]:
        d = self._acq().get("cal") or {}
        return {**CAL_DEFAULTS, **d, "choice": {**CAL_DEFAULTS["choice"], **(d.get("choice") or {})}}

    def load_session(self) -> None:
        self._loading = True
        st, cal = self.stack_params(), self.cal()
        self.method.setCurrentIndex(next((i for i, (_, m) in enumerate(METHODS) if m == st["method"]), 0))
        self.sigma.setValue(float(st["sigma"]))
        self.sigma.setEnabled(st["method"] == "sigma_clip_mean")
        self.align.setChecked(bool(st["align"]))
        self.pedestal_cb.setChecked(cal.get("match_pedestal") is not False)
        self.gain_cb.setChecked(bool(cal["gain_scale"]))
        self.flat_cb.setChecked(bool(cal["flat"]))
        self._loading = False
        self.refresh()

    def _changed(self) -> None:
        if self._loading:
            return
        method = METHODS[self.method.currentIndex()][1]
        self.sigma.setEnabled(method == "sigma_clip_mean")
        acq = self._acq()
        acq["stack"] = {"method": method, "sigma": round(self.sigma.value(), 2), "align": self.align.isChecked()}
        acq["cal"] = {"choice": {DARK: self.dark_pick.currentData() or "auto",
                                 BIAS: self.bias_pick.currentData() or "auto"},
                      "match_pedestal": self.pedestal_cb.isChecked(), "gain_scale": self.gain_cb.isChecked(),
                      "flat": self.flat_cb.isChecked()}
        self.screen.save_state()
        self.refresh()

    # ---- library -------------------------------------------------------------------------------------------
    def browse_library(self) -> None:
        d = dialogs.existing_directory(self, "Master library folder", self.lib_folder.text())
        if d:
            self.lib_folder.setText(d)
            self.host.set_masters_dir(d)

    def _library_changed(self) -> None:
        self.lib_folder.setText(str(self.host.settings.masters_dir()))
        self.refresh()

    def _used_by(self) -> dict[str, list[str]]:
        used: dict[str, list[str]] = {}
        for role, plan in self.plans.items():
            if plan is None:
                continue
            for m in (plan.dark, plan.bias, plan.flat):
                if m is not None:
                    used.setdefault(m.id, []).append(role.title())
        return used

    def _fill_pickers(self) -> None:
        self._loading = True
        lib, choice = self.host.library, self.cal()["choice"]
        for combo, kind in ((self.dark_pick, DARK), (self.bias_pick, BIAS)):
            combo.clear()
            combo.addItem("Auto (best match)", "auto")
            combo.addItem("None", "none")
            for m in lib.by_kind(kind):
                combo.addItem(f"{m.kind.title()} {fmt_exp(m.exptime)} · gain {m.gain:g} · {m.date}"
                              if m.gain is not None else m.label(), m.id)
            idx = combo.findData(choice.get(kind, "auto"))
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._loading = False

    def refresh(self) -> None:
        """Pickers, the plan for each set's ticked subs (header reads only), the library table."""
        self.project.setText(str(self.screen.night_folder()))
        self._fill_pickers()
        tol = tolerances(self.host.settings["tolerances"])
        for role in ("target", "reference"):
            good = self.screen._good(role)
            frames = [load_frame(r.path, LIGHT) for r in good]
            self.plans[role] = plan_from([f for f in frames if not f.error], self.host.library, tol, self.cal(),
                                         role.title()) if frames else None
        while self.cal_lines.count():
            w = self.cal_lines.takeAt(0).widget()
            if w is not None:
                w.hide()  # off screen now; deleteLater alone left the old lines painted under the new ones
                w.setParent(None)
                w.deleteLater()
        role = self.screen.view_role
        plan = self.plans.get(role)
        if plan is None:
            self.cal_lines.addWidget(StatusLine("off", f"No ticked {role} subs yet: the masters are picked when "
                                                       "there are subs to stack."))
        else:
            for _kind, state, text in plan.lines:
                self.cal_lines.addWidget(StatusLine(state, f"{role.title()}: {text}"))
            for issue in plan.issues:
                if issue.code != "no_dark":
                    self.cal_lines.addWidget(StatusLine("err" if issue.severity == "error" else "warn", issue.text))
        self.library_card.refresh()

    def open_folder(self) -> None:
        self.screen.open_project_folder()
