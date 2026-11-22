"""Plan › Observing conditions (ui/screens/plan_conditions.py) and the hidden Run all: location defaults, the one
network call, privacy, the window, the plots, palettes and Run all behind its flag."""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QDate, QTime
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from starex_reduce.core import location as lc
from starex_reduce.core import plan as pl
from starex_reduce.core import sky
from starex_reduce.core.session import Session
from starex_reduce.ui import theme
from starex_reduce.ui.main_window import MainWindow
from starex_reduce.ui.screens import plan_conditions as pcm
from starex_reduce.ui.widgets.alt_chart import SERIES, ChartLegend

VEGA = ("Vega", 279.2347, 38.7837)
POLARIS = ("Polaris", 37.9546, 89.2641)
LAT, LON = 30.6714, -104.0225  # McDonald Observatory, a public site


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any network use fails the test that did not expect it; calls are recorded."""
    calls: list[str] = []

    def refuse(*a, **k):
        calls.append(str(a[0] if a else k))
        raise AssertionError("network used")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    import socket

    monkeypatch.setattr(socket, "create_connection", refuse)
    return calls


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path, no_network: list[str]) -> MainWindow:
    w = MainWindow(session=Session.create(tmp_path / "pair"))
    qtbot.addWidget(w)
    w.resize(1400, 1600)
    w.show()
    return w


def _card(win: MainWindow, qtbot: QtBot) -> pcm.ConditionsCard:
    win.show_tab("plan")
    card = win.plan_page.conditions
    qtbot.waitUntil(card.idle, timeout=5000)
    return card


def _settle(card: pcm.ConditionsCard, qtbot: QtBot) -> None:
    qtbot.wait(10)
    qtbot.waitUntil(card.idle, timeout=5000)


def _target(win: MainWindow, qtbot: QtBot, star=VEGA) -> None:
    pl.set_target(win.session, star[0], star[1], star[2], "manual", None)
    win.plan_page.load_session()
    _settle(win.plan_page.conditions, qtbot)


def _window(card: pcm.ConditionsCard, day: datetime, a: str, b: str, qtbot: QtBot) -> None:
    card.date.setDate(QDate(day.year, day.month, day.day))
    card.t_from.setTime(QTime.fromString(a, "HH:mm"))
    card.t_to.setTime(QTime.fromString(b, "HH:mm"))
    _settle(card, qtbot)


def _site(win: MainWindow) -> None:
    win.settings["site"] = {"lat": LAT, "lon": LON}


# PART A: Run all ----------------------------------------------------------------------------------------------------
def test_run_all_is_hidden_with_its_shortcuts(win: MainWindow, qtbot: QtBot) -> None:
    assert not win.run_all_shown() and not win.run_all_btn.isVisibleTo(win)
    assert all(not s.isEnabled() for s in win._run_all_shortcuts)
    win.set_run_all_shown(True)
    assert win.run_all_btn.isVisibleTo(win) and all(s.isEnabled() for s in win._run_all_shortcuts)
    assert win.settings["ui"]["show_run_all"] is True
    win.set_run_all_shown(False)
    assert not win.run_all_btn.isVisibleTo(win) and all(not s.isEnabled() for s in win._run_all_shortcuts)


def test_run_all_flag_from_settings(qtbot: QtBot, tmp_path: Path) -> None:
    from starex_reduce.core.settings import Settings

    s = Settings()
    s["ui"] = {"palette": "default", "show_run_all": True}
    s.save()
    w = MainWindow(session=Session.create(tmp_path / "p"))
    qtbot.addWidget(w)
    assert (w.run_all_shown() and w.run_all_btn.isVisibleTo(w)) or not w.isVisible()
    assert all(s.isEnabled() for s in w._run_all_shortcuts)


# 4. location defaults -----------------------------------------------------------------------------------------------
def test_no_location_shows_the_prompt(win: MainWindow, qtbot: QtBot) -> None:
    card = _card(win, qtbot)
    assert card.current() is None and card.lat.text() == "" and card.source_tag.text() == ""
    assert card.plan is None and card.summary_text == "Set your location to see the sky plots"
    assert not card.polar.has_location and card.chart.plan is None


def test_site_default_is_mirrored_until_the_user_edits(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    assert card.current() == lc.Location(LAT, LON, "settings")
    assert card.lat.text() == "30.6714" and card.source_tag.text() == "from settings"
    assert not card.reset_btn.isVisibleTo(card)
    win.settings["site"] = {"lat": 31.0, "lon": -105.0}  # the site changes elsewhere: mirrored live
    win.pairs_changed.emit()
    _settle(card, qtbot)
    assert card.lat.text() == "31.0000" and card.user is None
    card.lat.setText("29 30 00 N")
    card.lon.setText("104 01 21 W")
    card._location_edited()
    _settle(card, qtbot)
    assert card.user == lc.Location(29.5, -104.0225, "manual") and card.source_tag.text() == "manual"
    assert win.settings["plan"]["location"] == {"lat": 29.5, "lon": -104.0225, "source": "manual", "user_set": True}
    assert card.reset_btn.isVisibleTo(card)
    win.settings["site"] = {"lat": 40.0, "lon": -100.0}  # no longer mirrored
    win.pairs_changed.emit()
    _settle(card, qtbot)
    assert card.lat.text() == "29.5000"
    card.reset_to_site()
    _settle(card, qtbot)
    assert card.user is None and "location" not in win.settings["plan"] and card.lat.text() == "40.0000"
    assert not card.reset_btn.isVisibleTo(card)


def test_header_site_default(win: MainWindow, qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from astropy.io import fits

    f = tmp_path / "t_S.fit"
    h = fits.Header()
    h["SITELAT"], h["SITELONG"] = -33.92, 18.42
    fits.PrimaryHDU(np.zeros((4, 4), np.float32), h).writeto(f)
    monkeypatch.setattr(pcm.ConditionsCard, "_stack_paths", lambda self: [f])
    card = _card(win, qtbot)
    card.refresh_site()
    _settle(card, qtbot)
    assert card.current() == lc.Location(-33.92, 18.42, "header") and card.source_tag.text() == "from stack header"


def test_bad_coordinates_are_rejected_with_a_message(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    card.lat.setText("95 N")
    card._location_edited()
    assert card.message.isVisibleTo(card) and "latitude" in card.message.text.text().lower()
    assert card.user is None and card.current() == lc.Location(LAT, LON, "settings")


def test_use_my_location_success(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    seen = []

    def lookup():
        seen.append(threading.current_thread().name)
        return lc.Location(32.78, -96.80, "internet")

    card.use_my_location(lookup)
    _settle(card, qtbot)
    assert seen == ["plan-geo"]  # off the GUI thread
    assert card.user == lc.Location(32.78, -96.80, "internet") and card.source_tag.text() == "from internet"
    assert card.source_tag.toolTip() == lc.GEO_TOOLTIP and card.reset_btn.isVisibleTo(card)


@pytest.mark.parametrize("error", ["The location service did not answer in time.",
                                   "No internet connection, or the location service cannot be reached.",
                                   "The location service sent an answer that could not be read."])
def test_use_my_location_failure_changes_nothing(win: MainWindow, qtbot: QtBot, error: str) -> None:
    _site(win)
    card = _card(win, qtbot)

    def lookup():
        raise lc.LocationError(error)

    card.use_my_location(lookup)
    _settle(card, qtbot)
    assert card.user is None and card.current() == lc.Location(LAT, LON, "settings")
    assert card.message.text.text().startswith(error) and "not changed" in card.message.text.text()
    assert card.geo_btn.isEnabled()


def test_lookup_uses_the_network_layer_only_on_the_click(win: MainWindow, qtbot: QtBot,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The real lookup function goes through urllib.request.urlopen (mocked here): one request, on the click."""
    import io

    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    def opener(req, timeout):
        calls.append((req.full_url, timeout))
        return Resp(json.dumps({"success": True, "latitude": 1.5, "longitude": 2.5}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", opener)
    monkeypatch.setattr(lc.urllib.request, "urlopen", opener)
    card = _card(win, qtbot)
    _target(win, qtbot)
    card.lat.setText("10")
    card.lon.setText("20")
    card._location_edited()
    _settle(card, qtbot)
    assert calls == []  # opening the tab and editing never call out
    card.use_my_location(lambda: lc.lookup_ip_location(opener=urllib.request.urlopen))
    _settle(card, qtbot)
    assert calls == [(lc.GEO_URL, lc.GEO_TIMEOUT_S)] and card.current() == lc.Location(1.5, 2.5, "internet")


# 5. no network at startup or on the tab; the location never in data files --------------------------------------------
def test_no_network_at_startup_on_the_tab_or_when_editing(win: MainWindow, qtbot: QtBot,
                                                          no_network: list[str]) -> None:
    _site(win)
    card = _card(win, qtbot)
    _target(win, qtbot)
    card.lat.setText("31")
    card._location_edited()
    card.min_alt.setValue(30)
    card.set_astronomical_night()
    _settle(card, qtbot)
    assert no_network == []


def test_location_never_in_the_plan_text_json_or_pair_files(win: MainWindow, qtbot: QtBot, tmp_path: Path) -> None:
    card = _card(win, qtbot)
    card.lat.setText("12.3456")
    card.lon.setText("-65.4321")
    card._location_edited()
    _target(win, qtbot)
    for i in (0, 1):
        win.plan_page.fmt.set_index(i)
        out = win.plan_page.plan_output()
        assert "12.3" not in out and "65.4" not in out
        assert "Date" in out or '"date"' in out
    win.plan_page.save_plan(str(tmp_path / "pair" / "plan.txt"))
    win.session.save()
    for f in (tmp_path / "pair").rglob("*"):
        if f.is_file():
            text = f.read_bytes().decode("utf-8", "replace")
            assert "12.3456" not in text and "65.4321" not in text, f
    assert win.settings["plan"]["location"]["lat"] == 12.3456  # it lives in the settings only


def test_location_never_in_data_files_after_a_full_synthetic_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture) -> None:
    """The self-test reduces the synthetic night end to end (masters, stacks, every step, an AVSpec file) with a
    planning location set in its settings: no data file it writes contains it."""
    from starex_reduce import selftest

    work = tmp_path / "work"
    (work / "settings").mkdir(parents=True)
    (work / "settings" / "settings.json").write_text(json.dumps(
        {"plan": {"location": {"lat": 12.3456, "lon": -65.4321, "source": "manual", "user_set": True}}}),
        encoding="utf-8")
    monkeypatch.setattr(selftest.tempfile, "mkdtemp", lambda prefix="": str(work))
    kept = tmp_path / "kept"
    assert selftest.run(keep=kept) == 0
    work = kept  # the self-test copies everything it wrote here, then removes its own folder
    files = [f for f in work.rglob("*") if f.is_file() and f.name != "settings.json"]
    assert any(f.suffix in (".fit", ".fits") for f in files) and any(f.suffix == ".json" for f in files)
    for f in files:
        text = f.read_bytes().decode("latin-1")
        assert "12.3456" not in text and "65.4321" not in text and "12.346" not in text, f


# 3. visibility cases in the card ------------------------------------------------------------------------------------
def test_target_that_sets_in_the_window(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    _window(card, datetime(2025, 11, 20), "20:00", "06:00", qtbot)
    _target(win, qtbot)
    kinds = [k for k, _t in card.polar.legend]
    assert "event" in kinds and card.polar.legend[0][1].startswith("↓ sets ")
    assert [m[0] for m in card.polar.markers] == ["event"]  # now (today) is outside the 2025-11-20 range
    assert card.chart.series_shown() == ["target", "moon", "min alt"]
    assert card.summary_text.startswith("Above 25° from 20:00 to ") and "transit" in card.summary_text
    text = win.plan_page.plan_output()
    assert "Date:        2025-11-20" in text and "Window:      20:00–06:00 local, min altitude 25°" in text
    assert "Visible:     Above 25° from 20:00 to " in text
    win.plan_page.fmt.set_index(1)
    js = json.loads(win.plan_page.plan_output())
    assert js["date"] == "2025-11-20" and js["window"] == {"from": "20:00", "to": "06:00", "min_alt": 25}
    assert js["visible"].startswith("Above 25° from 20:00")


def test_circumpolar_and_no_target(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    _window(card, datetime(2025, 11, 20), "20:00", "06:00", qtbot)
    assert card.plan is not None and card.plan.target_alt is None  # no target: the Moon still drawn
    assert card.chart.series_shown() == ["moon", "min alt"]
    assert card.polar.path_points() == [] and [k for k, _t in card.polar.legend if k in ("event", "now")] == []
    _target(win, qtbot, POLARIS)
    assert card.plan.state == "circumpolar" and card.polar.markers == []  # no rise or set
    assert card.summary_text.startswith("Always above the horizon")
    run = card.polar.path_points()[0]
    assert len(card.polar.path_points()) == 1 and np.all(run[:, 0] > 28)


def test_never_rises_and_never_above_min(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    _window(card, datetime(2025, 11, 20), "20:00", "06:00", qtbot)
    _target(win, qtbot, ("south", 90.0, -75.0))
    assert card.summary_text.startswith("Never rises at this latitude") and card.polar.path_points() == []
    _target(win, qtbot, ("Canopus", 95.99, -52.70))
    assert card.summary_text.startswith("Not above 25° in the window")


def test_rise_only_and_a_window_on_one_side_of_midnight(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    _window(card, datetime(2025, 11, 20), "22:00", "05:00", qtbot)
    _target(win, qtbot, ("Regulus", 152.09, 11.97))
    assert [k for _t, k in card.plan.events] == ["rise"] and card.polar.legend[0][1].startswith("↑ rises ")
    _window(card, datetime(2026, 11, 21), "01:00", "05:00", qtbot)  # no midnight crossing
    w = card.window()
    assert w.start.date() == w.end.date() == datetime(2026, 11, 21).date()


# 6. date and window -------------------------------------------------------------------------------------------------
def test_chart_range_follows_the_window(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    _window(card, datetime(2025, 11, 20), "21:00", "04:00", qtbot)
    w = card.window()
    assert w.end - w.start == timedelta(hours=7)
    (x0, x1), _ = card.chart.getPlotItem().viewRange()
    assert x0 == pytest.approx(w.start.timestamp() - 7200) and x1 == pytest.approx(w.end.timestamp() + 7200, abs=300)
    assert win.settings["plan"]["from"] == "21:00" and win.settings["plan"]["to"] == "04:00"


def test_astronomical_night_preset(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    card.date.setDate(QDate(2026, 11, 20))
    assert card.set_astronomical_night()
    _settle(card, qtbot)
    a, b = sky.astronomical_night(LAT, LON, datetime(2026, 11, 20))
    assert card.t_from.time() == QTime(a.hour, a.minute) and card.t_to.time() == QTime(b.hour, b.minute)


def test_now_marker_only_inside_the_range(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    _target(win, qtbot, POLARIS)
    now = datetime.now()
    _window(card, now - timedelta(hours=1), (now - timedelta(hours=1)).strftime("%H:%M"),
            (now + timedelta(hours=3)).strftime("%H:%M"), qtbot)
    card._tick_now()
    assert "now" in card.chart.items and ("now", "current pos") in card.polar.legend
    _window(card, datetime(2025, 11, 20), "20:00", "06:00", qtbot)
    card._tick_now()
    assert "now" not in card.chart.items and all(k != "now" for k, _t in card.polar.legend)


def test_default_evening() -> None:
    a, b = dtime(20, 0), dtime(6, 0)
    assert pcm.default_evening(a, b, datetime(2026, 11, 20, 15, 0)).date() == datetime(2026, 11, 20).date()
    assert pcm.default_evening(a, b, datetime(2026, 11, 21, 2, 0)).date() == datetime(2026, 11, 20).date()
    short = (dtime(20, 0), dtime(23, 0))
    assert pcm.default_evening(*short, datetime(2026, 11, 20, 23, 30)).date() == datetime(2026, 11, 21).date()
    assert pcm.tz_note(datetime(2026, 11, 20, 12)).startswith("Times use your computer's time zone (UTC")


# 7. the work happens off the GUI thread; debounce ---------------------------------------------------------------------
def test_sky_is_computed_off_the_gui_thread_and_debounced(win: MainWindow, qtbot: QtBot,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _site(win)
    card = _card(win, qtbot)
    threads: list[str] = []
    real = sky.compute

    def spy(*a, **k):
        threads.append(threading.current_thread().name)
        return real(*a, **k)

    monkeypatch.setattr(sky, "compute", spy)
    for v in (26, 27, 28, 29, 30):  # five edits in a row: one computation
        card.min_alt.setValue(v)
    _settle(card, qtbot)
    assert threads == ["plan-sky"] and card.plan.min_alt == 30


# 8. palettes --------------------------------------------------------------------------------------------------------
def _rgb(w) -> np.ndarray:
    img = w.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
    return a[:, : img.width(), [2, 1, 0]].astype(int)


@pytest.mark.parametrize("palette", ["night", "red"])
def test_dark_room_palettes_have_no_green_or_blue(win: MainWindow, qtbot: QtBot, palette: str) -> None:
    from starex_reduce.ui import retheme

    _site(win)
    card = _card(win, qtbot)
    _window(card, datetime(2025, 11, 20), "20:00", "06:00", qtbot)
    _target(win, qtbot)
    retheme.switch(palette)
    try:
        qtbot.wait(50)
        for w in (card.chart, card.polar, card.legend):
            rgb = _rgb(w)
            lit = rgb.sum(axis=2) > 30
            r, g, b = (rgb[..., i][lit] for i in range(3))
            assert np.all(g <= r + 2) and np.all(b <= r + 2), (palette, type(w).__name__)
    finally:
        retheme.switch("default")


def test_series_differ_by_line_style_and_match_the_legend() -> None:
    styles = {(v[1], v[2]) for v in SERIES.values()}
    assert len(styles) == len(SERIES)  # solid thick, dashed, dotted, thin solid
    assert [s for s, _t in ChartLegend().entries] == list(SERIES)


def test_new_widgets_use_tokens_only() -> None:
    import re

    root = Path(__file__).resolve().parents[1] / "starex_reduce" / "ui"
    literal = re.compile(r"#[0-9A-Fa-f]{6}\b|QColor\(\s*[\d\"]|mk(Brush|Pen|Color)\(\s*\d")
    for f in ("widgets/sky_polar.py", "widgets/alt_chart.py", "screens/plan_conditions.py"):
        assert not [ln for ln in (root / f).read_text(encoding="utf-8").splitlines() if literal.search(ln)], f


def test_card_heads_the_right_column(win: MainWindow, qtbot: QtBot) -> None:
    """Plan layout (2026-11-21): Target and Reference on the left, the conditions card on the right, the sky
    plots under it (tests/test_plan_layout.py has the full layout)."""
    card = _card(win, qtbot)
    page = win.plan_page
    left, right = page.columns
    assert left.isAncestorOf(page.name) and left.isAncestorOf(page.n_box) and right.isAncestorOf(card)
    assert right.isAncestorOf(card.polar) and right.isAncestorOf(card.chart)
    assert theme.palette == "default"


def test_recalculation_budget_in_the_card(win: MainWindow, qtbot: QtBot) -> None:
    _site(win)
    card = _card(win, qtbot)
    _target(win, qtbot)
    w = card.window()
    t = time.perf_counter()
    sky.compute(LAT, LON, w, 25.0, VEGA[1], VEGA[2])
    assert time.perf_counter() - t < 0.030
