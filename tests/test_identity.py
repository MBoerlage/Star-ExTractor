"""One view of who is being observed: Plan vs Files vs FITS OBJECT, spectral type for Response, checks."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from pytestqt.qtbot import QtBot

from starex_reduce.core import identity as idn
from starex_reduce.core.fitsio import write_float32
from starex_reduce.core.session import Session


def _session(tmp_path: Path) -> Session:
    s = Session.create(tmp_path / "s")
    s.params["plan"] = {"n": 10,
                        "target": {"name": "Gam Cas", "ra_deg": 14.177, "dec_deg": 60.717, "sp_type": "B0.5IVpe"},
                        "reference": {"name": "HD 4222", "ra": 11.3216, "dec": 55.2214, "sp_type": "A2Vs",
                                      "chosen": True}}
    return s


def test_names_compare_loosely() -> None:
    assert idn.same_name("HD 193369", "hd193369") and idn.same_name("* gam Cas", "Gam_Cas")
    assert not idn.same_name("", "") and not idn.same_name("P Cyg", "Gam Cas")


def test_spectral_type_from_simbad_then_plan(tmp_path: Path) -> None:
    s = _session(tmp_path)
    assert idn.sp_type_for(s, "reference") == ("A2Vs", "Plan")  # Files has no name yet: the Plan's star
    s.inputs["reference"]["name"] = "HD 193369"
    assert idn.sp_type_for(s, "reference") == ("", "")  # another star: the Plan's type does not apply
    s.inputs["reference"]["name"] = "hd4222"
    assert idn.sp_type_for(s, "reference") == ("A2Vs", "Plan")
    s.inputs["reference"]["simbad"] = {"found": True, "sp_type": "A2V"}
    assert idn.sp_type_for(s, "reference") == ("A2V", "SIMBAD")


def test_checks_list_the_inconsistencies(tmp_path: Path) -> None:
    s = _session(tmp_path)
    light = tmp_path / "l.fits"
    write_float32(light, np.zeros((4, 4), np.float32), {"OBJECT": "Sgrnov32026"})
    s.inputs["target"].update(name="P cyg", lights=[{"path": str(light), "used": True}])
    s.inputs["reference"]["name"] = "HD 193369"
    text = " | ".join(t for _l, t in idn.checks(s, site_known=False))
    assert "the Plan says Gam Cas, Files says P cyg" in text
    assert "OBJECT = Sgrnov32026" in text
    assert "Reference: no spectral type" in text
    assert "target coordinates" in text and "site position" in text
    s.inputs["reference"]["simbad"] = {"found": True, "sp_type": "B2IIIe"}
    assert any("poor flux reference" in t for _l, t in idn.checks(s, site_known=True))


def test_response_uses_the_plan_type(tmp_path: Path) -> None:
    from starex_reduce.core.instrument import InstrumentProfile
    from starex_reduce.core.response_stage import params_of, template_for

    s = _session(tmp_path)
    s.inputs["reference"]["name"] = "HD 4222"
    p = params_of(s, InstrumentProfile.load(tmp_path / "inst.json"))
    t, note = template_for(p)
    assert p["_ctx"]["sptype"] == "A2Vs" and t.label.startswith("A2") and "from the Plan" in note


def test_files_offers_the_plan_star(qtbot: QtBot, tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from starex_reduce.ui import theme
    from starex_reduce.ui.main_window import MainWindow

    theme.apply(QApplication.instance())
    s = _session(tmp_path)
    s.inputs["target"]["name"] = "P cyg"
    w = MainWindow(session=s)
    qtbot.addWidget(w)
    w.show()
    w.open_step(0)
    card = w.files_screen.cards["target"]
    assert card.plan_row.isVisibleTo(card) and "Gam Cas" in card.plan_hint.text()
    card.use_plan()
    assert s.inputs["target"]["name"] == "Gam Cas" and s.inputs["target"]["coords"]["ra"].startswith("00 56")
    assert not card.plan_row.isVisibleTo(card)
    qtbot.waitUntil(lambda: w.screens[0].checks.isVisibleTo(w.screens[0]), timeout=3000)  # still: no lights etc.
