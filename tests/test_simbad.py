"""SIMBAD lookups with mirrors (core/simbad.py): parsing, the fallback order, failures of every kind, not found, the
cache, the order memory, query formats, statuses in Plan and Acquisition. Every request goes to a fake transport and a
fake clock: nothing here goes online (conftest also makes the real transport fail)."""
from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import threading
import time
import urllib.parse
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from starextractor.core import simbad
from starextractor.core.jobs import Cancelled, Ctx
from starextractor.core.session import Session
from starextractor.core.simbad import simbad_job

SHELIAK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame><Target option="S"><name>Sheliak</name>
<Resolver name="S=Simbad (via url)"><oid>1</oid><oname>* bet Lyr</oname><otype>EB*</otype>
<jpos>18:50:04.79 +33:21:45.6</jpos><jradeg>282.51997976764824</jradeg><jdedeg>33.36266676977778</jdedeg>
<spType>B8.5Ib-II</spType></Resolver></Target></Sesame>"""
NOTFOUND_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame><Target option="S"><name>Sgrnov32026</name><INFO> *** Nothing found *** </INFO></Target></Sesame>"""
# the CfA SIMBAD answer to the sim-script query (seen 2026-11-24 for HD 163955) and for an unknown name
SCRIPT_4SGR = "*   4 Sgr|269.9481518982599 -23.8161147127400|HighPM*|A0|4.724\n\n"
SCRIPT_UNKNOWN = ("::error:::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::\n\n"
                  "[3] 'Zzqx nostar 12345' this identifier has an incorrect format for catalog:\n\tZ : catalogue Z\n\n")
SCRIPT_NOT_IN_DB = ("::error:::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::::\n\n"
                    "[3] Identifier not found in the database : HD 999999999\n")
CDS_404 = ("<!doctype html>\n<html lang=\"en\">\n<head>\n  <title>Page unavailable — CDS</title>\n</head>"
           "<body>404 This page is currently unavailable.</body></html>")


@pytest.fixture(autouse=True)
def _fresh() -> None:
    simbad.reset_memory()
    yield
    simbad.reset_memory()


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Fake:
    """Answers per endpoint key: (status, body) | an exception | a callable; each answer may cost fake seconds."""

    def __init__(self, clock: Clock | None = None, **answers) -> None:
        self.clock = clock or Clock()
        self.answers = answers
        self.calls: list[tuple[str, str, str, bytes | None, float, float]] = []

    def __call__(self, method, url, body, headers, connect_timeout, read_timeout):
        key = next(e.key for e in simbad.ENDPOINTS if url.startswith(e.url))
        self.calls.append((key, method, url, body, connect_timeout, read_timeout))
        a = self.answers.get(key, (200, SCRIPT_4SGR.encode() if "script" in url else SHELIAK_XML.encode()))
        cost = 0.3
        if isinstance(a, tuple) and isinstance(a[-1], BaseException):  # (seconds it costs, the error)
            cost, a = a
        elif isinstance(a, tuple) and len(a) == 3:  # (seconds, status, body)
            cost, a = a[0], a[1:]
        self.clock.t += cost
        if isinstance(a, BaseException):
            raise a
        return a

    @property
    def keys(self) -> list[str]:
        return [c[0] for c in self.calls]


# ---- parsing -------------------------------------------------------------------------------------------------------
def test_parse_script_found_and_unknown() -> None:
    r = simbad.parse_script(SCRIPT_4SGR, "HD 163955")
    assert r.found and r.main_id == "*   4 Sgr" and r.usable_name == "4 Sgr"
    assert r.ra_deg == pytest.approx(269.94815) and r.dec_deg == pytest.approx(-23.81611)
    assert (r.otype, r.sp_type, r.vmag) == ("HighPM*", "A0", pytest.approx(4.724))
    assert not simbad.parse_script(SCRIPT_UNKNOWN, "x").found
    assert not simbad.parse_script(SCRIPT_NOT_IN_DB, "x").found
    for bad in ("", "  \n", CDS_404, "*   4 Sgr|269.9", "::error::::\n\n[1] database unavailable\n",
                "*   4 Sgr|bad coords|HighPM*|A0|4.7\n"):
        with pytest.raises(simbad.BadAnswer):
            simbad.parse_script(bad, "x")


def test_parse_sesame_found_not_found_and_broken() -> None:
    r = simbad.parse_sesame(SHELIAK_XML, "Sheliak")
    assert r.found and r.main_id == "* bet Lyr" and r.usable_name == "bet Lyr" and r.sp_type == "B8.5Ib-II"
    assert r.ra_deg == pytest.approx(282.51998) and r.dec_deg == pytest.approx(33.36267)
    assert not simbad.parse_sesame(NOTFOUND_XML, "Sgrnov32026").found
    for bad in ("", "<html><body>down</body></html>", SHELIAK_XML[:200],
                "<Sesame><Target><name>x</name><INFO>Simbad: server not responding</INFO></Target></Sesame>",
                "<Sesame><Target><ERROR>Nothing found? timeout</ERROR></Target></Sesame>"):
        with pytest.raises(simbad.BadAnswer):
            simbad.parse_sesame(bad, "x")


def test_parse_v_magnitude_when_sent() -> None:
    xml = SHELIAK_XML.replace("<spType>", '<mag band="B"><v>3.4</v></mag><mag band="V"><v>3.52</v></mag><spType>')
    assert simbad.parse_sesame(xml, "Sheliak").vmag == pytest.approx(3.52)
    assert simbad.parse_sesame(SHELIAK_XML, "Sheliak").vmag is None
    assert "/-oxpF/" in simbad.SESAME_CDS and "/-oxpF/" in simbad.SESAME_CFA  # F = magnitudes in one answer


@pytest.mark.parametrize("mid,name", [("* bet Lyr", "bet Lyr"), ("V* V1500 Cyg", "V1500 Cyg"),
                                      ("NAME Sheliak", "Sheliak"), ("HD 174602", "HD 174602")])
def test_usable_name(mid: str, name: str) -> None:
    assert simbad.usable_name(mid) == name


# ---- 1. every failure of the first server: the mirror answers, within the budget -----------------------------------
FAILURES = {
    "connection refused": ConnectionRefusedError("refused"),
    "DNS failure": socket.gaierror(11001, "getaddrinfo failed"),
    "connect timeout": (simbad.CONNECT_TIMEOUT_S, TimeoutError("timed out")),
    "read timeout": (simbad.CONNECT_TIMEOUT_S + simbad.READ_TIMEOUT_S, TimeoutError("read timed out")),
    "TLS error": ssl.SSLError("certificate verify failed"),
    "HTTP 503": (503, b"Service Unavailable"),
    "HTTP 500": (500, b""),
    "redirect": (302, b"<html><title>302 Found</title></html>"),
    "HTTP 404 HTML page": (404, CDS_404.encode()),
    "HTTP 200 HTML page": (200, CDS_404.encode()),
    "HTTP 200 empty body": (200, b""),
    "truncated answer": (200, SCRIPT_4SGR[:20].encode()),
    "malformed answer": (200, b"\x00\xff garbage | | \n"),
    "server error in the answer": (200, b"::error::::::::\n\n[1] Database unavailable, try later\n"),
    "broken HTTP (IncompleteRead)": http.client.IncompleteRead(b"*  4"),
    "broken HTTP (bad status line)": ValueError("bad status line"),
}


@pytest.mark.parametrize("what", list(FAILURES))
def test_first_server_fails_the_mirror_answers(what: str) -> None:
    clock = Clock()
    fake = Fake(clock, cds=FAILURES[what])
    r = simbad.lookup_detailed("HD 163955", fake, clock=clock)
    assert r.result is not None and r.server == "cfa" and fake.keys == ["cds", "cfa"]
    assert r.result == simbad.parse_script(SCRIPT_4SGR, "HD 163955")  # the same parsed answer as from CDS
    assert r.attempts[0].outcome != "answered" and r.attempts[1].outcome == "answered"
    assert clock.t - 1000.0 <= simbad.BUDGET_S
    assert all(c[4] <= simbad.CONNECT_TIMEOUT_S and c[5] <= simbad.READ_TIMEOUT_S for c in fake.calls)


def test_both_simbad_servers_fail_sesame_answers() -> None:
    fake = Fake(cds=(404, CDS_404.encode()), cfa=TimeoutError("x"))
    r = simbad.lookup_detailed("Sheliak", fake, clock=fake.clock)
    assert r.server == "sesame-cds" and r.result.main_id == "* bet Lyr" and fake.keys == ["cds", "cfa", "sesame-cds"]


def test_the_budget_stops_the_lookup_and_no_server_is_asked_twice() -> None:
    slow = (simbad.CONNECT_TIMEOUT_S + simbad.READ_TIMEOUT_S, TimeoutError("hang"))
    clock = Clock()
    fake = Fake(clock, cds=slow, cfa=slow, **{"sesame-cds": slow, "sesame-cfa": slow})
    r = simbad.lookup_detailed("Sheliak", fake, clock=clock)
    assert r.result is None and fake.keys == ["cds", "cfa", "sesame-cds"]  # 3 × 14 s: the 4th would pass 30 s
    assert fake.calls[2][4] <= simbad.BUDGET_S - 28 + 0.01  # the last one only gets what is left
    with pytest.raises(simbad.LookupError_):
        simbad.lookup("Sheliak", fetch=Fake(cds=slow, cfa=slow, **{"sesame-cds": slow, "sesame-cfa": slow}),
                      clock=Clock())


# ---- 2. every server fails: the saved result, or a clean "unreachable" ----------------------------------------------
def _all_down() -> Fake:
    return Fake(cds=ConnectionRefusedError(), cfa=TimeoutError(), **{"sesame-cds": (302, b""),
                                                                      "sesame-cfa": TimeoutError()})


def test_all_fail_uses_the_saved_result_then_says_unreachable(tmp_path: Path) -> None:
    cache = tmp_path / "simbad_cache.json"
    first = simbad_job(Ctx(), "HD 163955", {}, Fake(), cache)
    assert first["found"] and first["server"] == "cds" and not first.get("cached")
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert list(saved) == ["hd 163955"] and saved["hd 163955"]["main_id"] == "*   4 Sgr"
    again = simbad_job(Ctx(), " hd  163955 ", {}, _all_down(), cache)
    assert again["found"] and again["cached"] and again["saved"] and again["ra"] == pytest.approx(269.94815)
    assert len(again["attempts"]) == 4 and not again.get("server")
    none = simbad_job(Ctx(), "Sheliak", {}, _all_down(), cache)
    assert none == {**none, "found": False, "offline": True} and "Traceback" not in none["error"]
    assert simbad.UNREACHABLE == "SIMBAD unreachable. You can enter the coordinates by hand."
    assert simbad.cached_text(again).startswith("SIMBAD: all servers unreachable; using the saved result from 20")


def test_saved_results_last_30_days(tmp_path: Path) -> None:
    cache = tmp_path / "c.json"
    r = simbad.parse_script(SCRIPT_4SGR, "HD 163955")
    simbad.cache_put("HD 163955", r, "cfa", cache, now=0.0)
    assert simbad.cache_get("hd 163955", cache, now=29 * 86400)["server"] == "cfa"
    assert simbad.cache_get("hd 163955", cache, now=31 * 86400) is None
    simbad.cache_put("Sheliak", r, "cds", cache, now=31 * 86400)  # old entries are dropped on the next write
    assert list(json.loads(cache.read_text(encoding="utf-8"))) == ["sheliak"]
    cache.write_text("not json", encoding="utf-8")
    assert simbad.cache_get("sheliak", cache) is None  # a broken cache is ignored, not an error


# ---- 3. "not found" from a healthy server is final -----------------------------------------------------------------
def test_not_found_is_final() -> None:
    fake = Fake(cds=(200, SCRIPT_UNKNOWN.encode()))
    r = simbad.lookup_detailed("Zzqx nostar 12345", fake, clock=fake.clock)
    assert r.result is not None and not r.result.found and fake.keys == ["cds"] and r.server == "cds"
    res = simbad_job(Ctx(), "Zzqx", {}, Fake(cds=(200, SCRIPT_NOT_IN_DB.encode())))
    assert res["found"] is False and not res.get("offline") and res["server"] == "cds"


def test_not_found_from_sesame_is_final_too() -> None:
    fake = Fake(cds=TimeoutError(), cfa=(503, b""), **{"sesame-cds": (200, NOTFOUND_XML.encode())})
    r = simbad.lookup_detailed("Sgrnov32026", fake, clock=fake.clock)
    assert not r.result.found and r.server == "sesame-cds" and fake.keys == ["cds", "cfa", "sesame-cds"]


# ---- 4. the last working server first; the first server again after 10 minutes ---------------------------------------
def test_last_working_server_first_and_recheck_after_ten_minutes() -> None:
    clock = Clock()
    fake = Fake(clock, cds=(404, CDS_404.encode()))
    simbad.lookup_detailed("a", fake, clock=clock)
    assert fake.keys == ["cds", "cfa"]
    clock.t += 60
    simbad.lookup_detailed("b", fake, clock=clock)
    assert fake.keys[2:] == ["cfa"]  # CDS is skipped while it was down less than 10 minutes ago
    clock.t += simbad.RECHECK_S
    simbad.lookup_detailed("c", fake, clock=clock)
    assert fake.keys[3:] == ["cds", "cfa"]  # re-checked, still down: CfA again, the 10 minutes start over
    clock.t += 60
    simbad.lookup_detailed("d", fake, clock=clock)
    assert fake.keys[5:] == ["cfa"]
    clock.t += simbad.RECHECK_S
    fake.answers.pop("cds")  # CDS is back
    simbad.lookup_detailed("e", fake, clock=clock)
    simbad.lookup_detailed("f", fake, clock=clock)
    assert fake.keys[6:] == ["cds", "cds"]


def test_a_chosen_server_goes_first_and_the_restart_forgets() -> None:
    assert [e.key for e in simbad.order("cfa")] == ["cfa", "cds", "sesame-cfa", "sesame-cds"]
    assert [e.key for e in simbad.order("cds")] == ["cds", "cfa", "sesame-cds", "sesame-cfa"]
    fake = Fake(cds=TimeoutError())
    simbad.lookup_detailed("a", fake, clock=fake.clock)
    assert simbad.order("auto", fake.clock())[0].key == "cfa"
    simbad.reset_memory()  # what a new start does (the memory is per session only)
    assert simbad.order("auto")[0].key == "cds"


def test_debug_setting_makes_servers_unreachable() -> None:
    fake = Fake()
    res = simbad_job(Ctx(), "HD 163955", {"server": "auto", "unreachable": ("cds",)}, fake)
    assert res["server"] == "cfa" and fake.keys == ["cfa"]  # CDS failed without a request
    assert res["attempts"][0] == {"server": "cds", "outcome": "connection refused", "seconds": 0.0}
    s = {"simbad": {"server": "cfa", "debug_unreachable": ["cds", "sesame-cfa"]}}
    assert simbad.prefs(s) == {"server": "cfa", "unreachable": ("cds", "sesame-cfa")}
    assert simbad.prefs({"simbad": {"server": "nope"}}) == {"server": "auto", "unreachable": ()}


# ---- 5. the same query on every mirror -----------------------------------------------------------------------------
def test_query_formats_are_identical_across_mirrors() -> None:
    reqs = {e.key: simbad.request_for(e, "HD 163955") for e in simbad.ENDPOINTS}
    (m1, u1, b1, h1), (m2, u2, b2, h2) = reqs["cds"], reqs["cfa"]
    assert m1 == m2 == "POST" and b1 == b2 and h1 == h2
    p1, p2 = urllib.parse.urlsplit(u1), urllib.parse.urlsplit(u2)
    assert (p1.netloc, p2.netloc) == ("simbad.cds.unistra.fr", "simbad.cfa.harvard.edu")
    assert p1.path == p2.path == "/simbad/sim-script" and not p1.query and not p2.query
    script = urllib.parse.parse_qs(b1.decode())["script"][0]
    assert script.splitlines() == ["output console=off script=off",
                                   'format object "%IDLIST(1)|%COO(d;A D)|%OTYPE(S)|%SP(S)|%FLUXLIST(V;F)"',
                                   "query id HD 163955"]
    (m3, u3, _b3, _), (m4, u4, _b4, _) = reqs["sesame-cds"], reqs["sesame-cfa"]
    assert m3 == m4 == "GET" and u3.split("/nph-sesame/")[1] == u4.split("/nph-sesame/")[1] == "-oxpF/S?HD%20163955"


def test_a_name_cannot_add_lines_to_the_script() -> None:
    _m, _u, body, _h = simbad.request_for(simbad.BY_KEY["cds"], "Vega\nquery coo 0 0 radius=10d\r")
    script = urllib.parse.parse_qs(body.decode())["script"][0]
    assert script.splitlines()[-1] == "query id Vega query coo 0 0 radius=10d" and len(script.splitlines()) == 3


# ---- 6. no network at import or start-up ---------------------------------------------------------------------------
def test_no_network_at_import_startup_or_opening_plan(qtbot: QtBot, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    code = ("import socket\n"
            "def no(*a, **k):\n    raise SystemExit('network access at import')\n"
            "socket.create_connection = socket.getaddrinfo = no\nsocket.socket.connect = no\n"
            "import starextractor.core.simbad, starextractor.ui.screens.plan, starextractor.ui.screens.acquisition\n")
    run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert run.returncode == 0, run.stderr[-500:]
    opened: list[object] = []

    def no_net(*a, **_k):
        opened.append(a)
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "getaddrinfo", no_net)
    from starextractor.ui.main_window import MainWindow

    w = MainWindow(session=Session.create(tmp_path / "s"))
    qtbot.addWidget(w)
    w.show()
    w.show_tab("plan")
    w.ensure_acquisition()
    qtbot.wait(100)
    assert opened == []


# ---- the job: log line, cancellation, a hung server ----------------------------------------------------------------
def test_log_line_names_servers_and_times_never_the_name() -> None:
    res = simbad_job(Ctx(), "My secret nova", {}, Fake(cds=(404, CDS_404.encode())))
    line = simbad.log_line(res)
    assert line.startswith("SIMBAD: answered by CfA mirror") and "CDS Strasbourg: HTTP 404" in line
    assert " s)" in line and "secret" not in line.lower()
    assert simbad.log_line(simbad_job(Ctx(), "x", {}, _all_down())).startswith("SIMBAD: no server answered")


def test_a_hung_server_is_cut_off_and_cancel_returns_at_once() -> None:
    gate = threading.Event()

    def hang(*_a):
        gate.wait(30)
        raise TimeoutError("never answers")

    ctx = Ctx()
    threading.Timer(0.2, ctx.cancel.cancel).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        simbad_job(ctx, "HD 163955", {}, hang)
    assert time.monotonic() - t0 < 2.0  # not the 14 s of the timeouts
    gate.set()


# ---- 7. what the user sees -----------------------------------------------------------------------------------------
@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from starextractor.ui import theme
    from starextractor.ui.main_window import MainWindow

    theme.apply(QApplication.instance())
    w = MainWindow(session=Session.create(tmp_path / "sess"))
    qtbot.addWidget(w)
    w.show()
    w.show_tab("plan")
    return w


def _plan_with(win, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, fake: Fake, name: str = "HD 163955"):
    monkeypatch.setattr(simbad, "http_transport", fake)
    plan = win.ensure_plan()
    plan.name.setText(name)
    plan.check_simbad()
    qtbot.waitUntil(lambda: plan.simbad_btn.isEnabled(), timeout=5000)
    return plan


@pytest.mark.parametrize(("fake", "level", "start"), [
    (Fake(), "ok", "SIMBAD (CDS Strasbourg): HD 163955 = *   4 Sgr"),
    (Fake(cds=(404, CDS_404.encode())), "ok", "SIMBAD (CfA mirror): HD 163955 = *   4 Sgr"),
    (Fake(cds=TimeoutError(), cfa=(503, b"")), "ok", "SIMBAD via Sesame at CDS Strasbourg: HD 163955 = * bet Lyr"),
    (_all_down(), "warn", "SIMBAD unreachable. You can enter the coordinates by hand. Type RA/Dec below."),
    (Fake(cds=(200, SCRIPT_UNKNOWN.encode())), "warn", "SIMBAD (CDS Strasbourg): “HD 163955” is not known"),
])
def test_plan_status_for_each_state(win, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, fake: Fake, level: str,
                                    start: str) -> None:
    plan = _plan_with(win, qtbot, monkeypatch, fake)
    assert plan.simbad_status.state == level and plan.simbad_status.text.text().startswith(start)
    log = "\n".join(win.session.log_lines) if hasattr(win.session, "log_lines") else ""
    assert "Traceback" not in plan.simbad_status.text.text() and "Traceback" not in log
    if level == "warn":
        assert plan.ra.isEnabled() and not plan.ra.isReadOnly()  # the coordinates can be typed by hand


def test_plan_uses_the_saved_result_when_all_fail(win, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan_with(win, qtbot, monkeypatch, Fake())
    plan.ra.clear()
    plan = _plan_with(win, qtbot, monkeypatch, _all_down())
    text = plan.simbad_status.text.text()
    assert plan.simbad_status.state == "warn" and text.startswith("SIMBAD: all servers unreachable; using the saved "
                                                                   "result from ")
    assert "4 Sgr" in text and plan.ra.text().startswith("17 59")


def test_acquisition_status_for_each_state(win, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    a = win.ensure_acquisition()
    unknown = (200, SCRIPT_UNKNOWN.encode())
    for name, fake, level, start in (
            ("HD 163955", Fake(cds=ConnectionRefusedError()), "ok", "SIMBAD (CfA mirror): *   4 Sgr"),
            ("HD 163955", _all_down(), "warn", "SIMBAD: all servers unreachable; using the saved result"),
            ("Zzqx", Fake(cds=unknown, cfa=unknown), "warn", "SIMBAD (CfA mirror): “Zzqx” is not known")):
        monkeypatch.setattr(simbad, "http_transport", fake)  # CfA answered last: it is asked first from then on
        a.names["target"].setText(name)
        a.resolve_simbad("target")
        qtbot.waitUntil(lambda: not win.runner.is_busy("acq.simbad.target"), timeout=5000)
        qtbot.wait(20)
        note = a.ident_notes["target"]
        assert note.state == level and note.text.text().startswith(start), note.text.text()
        assert "attempts" not in a.sets["target"]["simbad"]  # the sets file keeps the answer, not the log
    monkeypatch.setattr(simbad, "http_transport", _all_down())
    a.names["target"].setText("Never looked up")
    a.resolve_simbad("target")
    qtbot.waitUntil(lambda: not win.runner.is_busy("acq.simbad.target"), timeout=5000)
    qtbot.wait(20)
    assert a.ident_notes["target"].text.text().startswith(simbad.UNREACHABLE)


def test_a_hung_lookup_does_not_freeze_the_window(win, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import QTimer

    gate = threading.Event()

    def hang(*_a):
        gate.wait(30)
        raise TimeoutError("never")

    monkeypatch.setattr(simbad, "http_transport", hang)
    plan = win.ensure_plan()
    plan.name.setText("HD 163955")
    ticks: list[int] = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(20)
    plan.check_simbad()
    qtbot.wait(400)
    assert len(ticks) >= 8 and plan.simbad_status.text.text().startswith("Asking SIMBAD")  # the UI kept running
    t0 = time.monotonic()
    win.runner.cancel("plan.simbad")
    qtbot.waitUntil(lambda: plan.simbad_btn.isEnabled(), timeout=2000)
    assert time.monotonic() - t0 < 2.0 and plan.simbad_status.text.text() == "SIMBAD lookup stopped."
    timer.stop()
    gate.set()


@pytest.mark.parametrize("palette", ["night", "red"])
def test_no_green_or_blue_in_the_night_palettes(win, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch,
                                                palette: str) -> None:
    from starextractor.ui import retheme

    plan = _plan_with(win, qtbot, monkeypatch, Fake(cds=(404, CDS_404.encode())))
    retheme.switch(palette)
    try:
        for state, text in (("ok", plan.simbad_status.text.text()), ("warn", simbad.UNREACHABLE)):
            plan.simbad_status.set(state, text)
            qtbot.wait(30)
            img = plan.simbad_status.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
            a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
            px = a[:, : img.width(), [2, 1, 0]].astype(int)
            lit = px.sum(axis=2) > 24
            r, g, b = (px[..., i][lit] for i in range(3))
            if palette == "red":
                assert np.all(g <= 2) and np.all(b <= 2), state
            else:
                assert np.all(g <= r + 2) and np.all(b <= r + 2), state
    finally:
        retheme.switch("default")


def test_settings_offer_the_server_and_clear_the_cache(qtbot: QtBot, tmp_path: Path) -> None:
    from starextractor.core.settings import Settings, app_dir
    from starextractor.ui.settings_dialog import SettingsDialog

    (app_dir() / simbad.CACHE_FILE).write_text("{}", encoding="utf-8")
    s = Settings.load() if hasattr(Settings, "load") else Settings()
    d = SettingsDialog(s)
    qtbot.addWidget(d)
    assert [d.simbad_server.itemText(i) for i in range(d.simbad_server.count())] == \
        ["Automatic", "CDS Strasbourg", "CfA Harvard"]
    assert d.simbad_server.currentData() == "auto" and d.clear_simbad.isEnabled()
    d.simbad_server.setCurrentIndex(2)
    d._clear_simbad()
    assert not (app_dir() / simbad.CACHE_FILE).exists()
    v = d.values()
    assert isinstance(v, dict) and v["simbad"]["server"] == "cfa"


@pytest.mark.skipif(not os.environ.get("STAREX_NETWORK"), reason="set STAREX_NETWORK=1 for the live test")
def test_live_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the real transport
    r = simbad.lookup("HD 163955")
    assert r.found and r.sp_type
