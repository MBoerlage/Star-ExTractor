"""SIMBAD (Sesame) parsing and fallbacks, ported from specinti_prep. No network unless asked."""
from __future__ import annotations

import os

import pytest

from starextractor.core import simbad
from starextractor.core.simbad import simbad_job

SHELIAK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame><Target option="S"><name>Sheliak</name>
<Resolver name="S=Simbad (via url)"><oid>1</oid><oname>* bet Lyr</oname><otype>EB*</otype>
<jpos>18:50:04.79 +33:21:45.6</jpos><jradeg>282.51997976764824</jradeg><jdedeg>33.36266676977778</jdedeg>
<spType>B8.5Ib-II</spType></Resolver></Target></Sesame>"""
NOTFOUND_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame><Target option="S"><name>Sgrnov32026</name><INFO> *** Nothing found *** </INFO></Target></Sesame>"""


def test_parse_found() -> None:
    r = simbad.parse_sesame(SHELIAK_XML, "Sheliak")
    assert r.found and r.main_id == "* bet Lyr" and r.usable_name == "bet Lyr"
    assert r.ra_deg == pytest.approx(282.51998) and r.dec_deg == pytest.approx(33.36267)
    assert r.sp_type == "B8.5Ib-II"


def test_parse_not_found() -> None:
    assert not simbad.parse_sesame(NOTFOUND_XML, "Sgrnov32026").found


@pytest.mark.parametrize("mid,name", [("* bet Lyr", "bet Lyr"), ("V* V1500 Cyg", "V1500 Cyg"),
                                      ("NAME Sheliak", "Sheliak"), ("HD 174602", "HD 174602")])
def test_usable_name(mid: str, name: str) -> None:
    assert simbad.usable_name(mid) == name


def test_lookup_falls_back_to_mirror() -> None:
    calls: list[str] = []

    def fetch(url: str, timeout: float) -> str:
        calls.append(url)
        if len(calls) == 1:
            raise OSError("down")
        return SHELIAK_XML

    simbad._last_good = None
    assert simbad.lookup("Sheliak", fetch=fetch).found and len(calls) == 2
    assert simbad.lookup("Sheliak", fetch=fetch).found and len(calls) == 3  # the mirror that answered goes first
    assert calls[2].startswith(simbad.MIRRORS[1])
    simbad._last_good = None


def test_lookup_all_mirrors_fail() -> None:
    with pytest.raises(simbad.LookupError_):
        simbad.lookup("Sheliak", fetch=lambda url, timeout: "<html>Page unavailable</html>")


def test_job_is_offline_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(name: str, timeout: float = 0) -> None:
        raise simbad.LookupError_("no network")

    monkeypatch.setattr(simbad, "lookup", fail)
    res = simbad_job(None, "HD 163955")
    assert res["offline"] and not res["found"]


@pytest.mark.skipif(not os.environ.get("STAREX_NETWORK"), reason="set STAREX_NETWORK=1 for the live test")
def test_live_lookup() -> None:
    r = simbad.lookup("HD 163955")
    assert r.found and r.sp_type


def test_parse_v_magnitude_when_sent() -> None:
    xml = SHELIAK_XML.replace("<spType>", '<mag band="B"><v>3.4</v></mag><mag band="V"><v>3.52</v></mag><spType>')
    assert simbad.parse_sesame(xml, "Sheliak").vmag == pytest.approx(3.52)
    assert simbad.parse_sesame(SHELIAK_XML, "Sheliak").vmag is None
    assert all("-oxpF/" in m for m in simbad.MIRRORS)  # F = magnitudes in the same single answer
