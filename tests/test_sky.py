"""core/sky.py and core/location.py: parsing, accuracy against astropy, visibility cases, the astronomical-night
preset, speed, the default-location order and the one network call (mocked)."""
from __future__ import annotations

import io
import json
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pytest

from starex_reduce.core import location as loc
from starex_reduce.core import sky

MCDONALD = (30.6714, -104.0225)  # McDonald Observatory, a public site
TZ = timezone(timedelta(hours=-6))  # fixed, so the expected local times do not depend on the machine
CAPE_TOWN = (-33.92, 18.42)


# 1. parsing ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("text", "kind", "value"), [
    ("30.67", "lat", 30.67), ("-33.92", "lat", -33.92), ("30.67 N", "lat", 30.67), ("33.92S", "lat", -33.92),
    ("104 01 21 W", "lon", -104.0225), ("104°01'21\"W", "lon", -104.0225), ("-104:01:21", "lon", -104.0225),
    ("18 25 E", "lon", 18 + 25 / 60), ("0", "lon", 0.0), ("+12,5", "lon", 12.5), ("90 N", "lat", 90.0),
])
def test_parse_accepts(text: str, kind: str, value: float) -> None:
    assert loc.parse_angle(text, kind) == pytest.approx(value, abs=1e-9)


@pytest.mark.parametrize(("text", "kind"), [
    ("", "lat"), ("abc", "lat"), ("91", "lat"), ("-90.5", "lat"), ("181 E", "lon"), ("30 E", "lat"),
    ("95 W", "lat"), ("10 N", "lon"), ("-30 S", "lat"), ("30 61", "lat"), ("30 10 75", "lat"), ("30.5 10", "lat"),
    ("1 2 3 4", "lat"), ("nan", "lat"), ("inf", "lon"),
])
def test_parse_rejects(text: str, kind: str) -> None:
    with pytest.raises(loc.LocationError):
        loc.parse_angle(text, kind)


# 2. accuracy against astropy (test-only use) ------------------------------------------------------------------------
astropy = pytest.importorskip("astropy")


@pytest.fixture(scope="module")
def ap():
    from astropy.utils import iers

    iers.conf.auto_download = False  # offline: astropy's bundled tables
    import warnings
    from types import SimpleNamespace

    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body, get_sun
    from astropy.time import Time

    warnings.simplefilter("ignore")
    return SimpleNamespace(u=u, AltAz=AltAz, EarthLocation=EarthLocation, SkyCoord=SkyCoord, get_body=get_body,
                           get_sun=get_sun, Time=Time)


SITES = [MCDONALD, CAPE_TOWN, (51.48, -0.0), (64.1, -21.9)]
DATES = ["2026-01-15T03:00", "2026-06-20T22:00", "2026-11-21T05:30", "2027-03-03T12:00"]
TARGETS = [(279.23, 38.78), (83.82, -5.39), (201.30, -11.16), (37.95, 89.26), (95.99, -52.70)]


def test_positions_match_astropy(ap) -> None:
    u, AltAz, EarthLocation, SkyCoord, get_body, get_sun, Time = (ap.u, ap.AltAz, ap.EarthLocation, ap.SkyCoord,
                                                             ap.get_body, ap.get_sun, ap.Time)
    worst = {"target": 0.0, "sun": 0.0, "moon": 0.0}
    for lat, lon in SITES:
        site = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
        for d in DATES:
            t = Time(d) + np.arange(0, 24, 2) * u.hour
            frame = AltAz(obstime=t, location=site, pressure=0)
            for ra, dec in TARGETS:
                c = SkyCoord(ra * u.deg, dec * u.deg).transform_to(frame)
                r, de = sky.precess(ra, dec, t.unix)
                alt, az = sky.altaz(r, de, lat, lon, t.unix)
                daz = np.abs((az - c.az.deg + 180) % 360 - 180) * np.cos(np.radians(c.alt.deg))  # on the sky
                worst["target"] = max(worst["target"], np.abs(alt - c.alt.deg).max(), daz.max())
            s = get_sun(t).transform_to(frame)
            sr, sd = sky.sun_radec(t.unix)
            worst["sun"] = max(worst["sun"], np.abs(sky.altaz(sr, sd, lat, lon, t.unix)[0] - s.alt.deg).max())
            m = get_body("moon", t, site).transform_to(frame)
            worst["moon"] = max(worst["moon"], np.abs(sky.moon_altaz(lat, lon, t.unix)[0] - m.alt.deg).max())
    assert worst["target"] < 0.1 and worst["sun"] < 0.1 and worst["moon"] < 0.5, worst


def test_rise_and_set_times_match_astropy(ap) -> None:
    u, AltAz, EarthLocation, SkyCoord, Time = ap.u, ap.AltAz, ap.EarthLocation, ap.SkyCoord, ap.Time
    checked = 0
    for lat, lon in (MCDONALD, CAPE_TOWN):
        site = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
        for ra, dec in ((83.82, -5.39), (201.30, -11.16), (279.23, 38.78)):
            w = sky.Window.from_local(datetime(2026, 11, 20), dtime(18, 0), dtime(8, 0), TZ)
            plan = sky.compute(lat, lon, w, 25.0, ra, dec)
            for t_ev, _kind in plan.horizon_events:
                fine = Time(t_ev + np.arange(-900, 901, 5.0), format="unix")
                alt = SkyCoord(ra * u.deg, dec * u.deg).transform_to(
                    AltAz(obstime=fine, location=site, pressure=0)).alt.deg
                i = int(np.nonzero(np.signbit(alt[:-1]) != np.signbit(alt[1:]))[0][0])
                assert abs(fine.unix[i] - t_ev) < 120, (lat, ra, t_ev - fine.unix[i])
                checked += 1
    assert checked >= 6


def test_moon_illumination_matches_astropy(ap) -> None:
    u, get_body, get_sun, Time = ap.u, ap.get_body, ap.get_sun, ap.Time
    t = Time("2026-11-01T00:00") + np.arange(0, 30) * u.day
    sun, moon = get_sun(t), get_body("moon", t)
    elong = sun.separation(moon).rad
    phase = np.arctan2(sun.distance * np.sin(elong), moon.distance - sun.distance * np.cos(elong))
    k = (1 + np.cos(phase)) / 2
    assert np.abs(sky.moon_illumination(t.unix) - k.value).max() < 0.02


# 3. visibility cases ------------------------------------------------------------------------------------------------
def _w(start="20:00", end="06:00", day=datetime(2026, 11, 20)) -> sky.Window:
    a, b = (dtime(*map(int, s.split(":"))) for s in (start, end))
    return sky.Window.from_local(day, a, b, TZ)


def test_window_crosses_midnight_and_the_range_is_window_plus_two_hours() -> None:
    w = _w("20:00", "06:00")
    assert w.end - w.start == timedelta(hours=10) and w.end.date() == w.start.date() + timedelta(days=1)
    same_day = _w("01:00", "05:00")
    assert same_day.end - same_day.start == timedelta(hours=4)
    p = sky.compute(*MCDONALD, w)
    assert p.unix[0] == pytest.approx(w.start.timestamp() - 7200)
    assert p.unix[-1] == pytest.approx(w.end.timestamp() + 7200, abs=sky.STEP_S)
    assert np.allclose(np.diff(p.unix), sky.STEP_S)


def test_circumpolar_target() -> None:
    p = sky.compute(*MCDONALD, _w(), 25.0, 37.95, 89.26)  # Polaris
    assert p.state == "circumpolar" and not p.events and not p.horizon_events
    assert sky.summary(p, TZ).startswith("Always above the horizon · Above 25° from 20:00 to 06:00")


def test_never_rises() -> None:
    p = sky.compute(*MCDONALD, _w(), 25.0, 95.99, -75.0)
    assert p.state == "never rises"
    assert sky.summary(p, TZ).startswith("Never rises at this latitude")


def test_never_above_the_minimum_in_the_window() -> None:
    p = sky.compute(*MCDONALD, _w(), 25.0, 95.99, -52.70)  # Canopus: up to ~7° from here
    assert p.state == "never above min" and not p.events
    assert sky.summary(p, TZ).startswith("Not above 25° in the window (highest")


def test_sets_during_the_window() -> None:
    p = sky.compute(*MCDONALD, _w(), 25.0, 279.23, 38.78)  # Vega, evening: sets below 25° early in the window
    assert p.state == "ok" and [k for _t, k in p.events] == ["set"]
    t_set = p.events[0][0]
    assert sky.summary(p, TZ).startswith(f"Above 25° from 20:00 to {sky.hhmm(t_set, TZ)}")
    assert " · transit " in sky.summary(p, TZ) and "% lit" in sky.summary(p, TZ)


def test_rises_during_the_window() -> None:
    p = sky.compute(*MCDONALD, _w(), 25.0, 152.09, 11.97)  # Regulus: rises after midnight in November
    assert [k for _t, k in p.events] == ["rise"]
    assert sky.summary(p, TZ).startswith(f"Above 25° from {sky.hhmm(p.events[0][0], TZ)} to 06:00")


def test_no_target_still_has_sun_and_moon() -> None:
    p = sky.compute(*MCDONALD, _w())
    assert p.state == "no target" and p.target_alt is None and len(p.moon_alt) == len(p.unix)
    assert sky.summary(p, TZ).startswith("Moon ") and "% lit" in sky.summary(p, TZ)


def test_southern_site() -> None:
    p = sky.compute(*CAPE_TOWN, _w(), 25.0, 24.43, -57.24)  # Achernar from Cape Town: circumpolar
    assert p.state == "circumpolar"


# 6. the astronomical night preset -----------------------------------------------------------------------------------
def test_astronomical_night_is_the_sun_at_minus_18() -> None:
    a, b = sky.astronomical_night(*MCDONALD, datetime(2026, 11, 20), TZ)
    assert a.date() == datetime(2026, 11, 20).date() and b.date() == datetime(2026, 11, 21).date()
    for t in (a, b):
        ra, dec = sky.sun_radec(np.array([t.timestamp()]))
        alt = sky.altaz(ra, dec, *MCDONALD, np.array([t.timestamp()]))[0][0]
        assert alt == pytest.approx(-18.0, abs=0.05)
    assert sky.astronomical_night(64.1, -21.9, datetime(2026, 6, 21), TZ) is None  # Reykjavik in June


def test_twilight_bands_cover_the_range_in_order() -> None:
    p = sky.compute(*MCDONALD, _w("18:00", "07:00"))
    bands = sky.twilight_bands(p.unix, p.sun_alt)
    assert bands[0][0] == p.unix[0] and bands[-1][1] == p.unix[-1]
    kinds = [k for _a, _b, k in bands]
    assert kinds[0] in ("day", "civil") and "night" in kinds
    i = kinds.index("night")
    assert kinds[:i][-3:] == ["civil", "nautical", "astronomical"]


# 7. speed -----------------------------------------------------------------------------------------------------------
def test_recalculation_under_30_ms() -> None:
    w = _w()
    sky.compute(*MCDONALD, w, 25.0, 279.23, 38.78)  # warm
    t = time.perf_counter()
    for _ in range(10):
        p = sky.compute(*MCDONALD, w, 25.0, 279.23, 38.78)
        sky.summary(p, TZ)
        sky.twilight_bands(p.unix, p.sun_alt)
    assert (time.perf_counter() - t) / 10 < 0.030


# 4. default location order and the one network call -----------------------------------------------------------------
def _stack(tmp_path: Path, name: str, **keys) -> Path:
    from astropy.io import fits

    h = fits.Header()
    for k, v in keys.items():
        h[k.replace("_", "-")] = v
    p = tmp_path / name
    fits.PrimaryHDU(np.zeros((4, 4), np.float32), h).writeto(p)
    return p


def test_site_default_order(tmp_path: Path) -> None:
    stack = _stack(tmp_path, "t.fit", SITELAT=30.25, SITELONG=-95.5)
    geo = _stack(tmp_path, "g.fit", OBSGEO_B=-33.9, OBSGEO_L=18.4)
    east360 = _stack(tmp_path, "e.fit", LAT_OBS=10.0, LONG_OBS=350.0)
    settings_site = {"site": {"lat": 29.0, "lon": -95.0}}
    assert loc.site_default(settings_site, [stack], {"lat": 1.0, "lon": 2.0}) == loc.Location(29.0, -95.0, "settings")
    assert loc.site_default({}, [stack], {"lat": 1.0, "lon": 2.0}) == loc.Location(30.25, -95.5, "header")
    assert loc.site_default({}, [tmp_path / "missing.fit", geo], None) == loc.Location(-33.9, 18.4, "header")
    assert loc.site_default({}, [east360], None) == loc.Location(10.0, -10.0, "header")
    assert loc.site_default({}, [], {"lat": 1.0, "lon": 2.0}) == loc.Location(1.0, 2.0, "export")
    assert loc.site_default({"site": {"lat": None}}, [], None) is None


def test_user_location_only_when_the_user_set_it() -> None:
    assert loc.user_location({"location": {"lat": 1.0, "lon": 2.0, "source": "manual", "user_set": True}}) == \
        loc.Location(1.0, 2.0, "manual")
    assert loc.user_location({"location": {"lat": 1.0, "lon": 2.0, "user_set": False}}) is None
    assert loc.user_location({}) is None and loc.user_location(None) is None


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        self.close()


def test_ip_lookup_parses_the_answer() -> None:
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["timeout"], seen["data"] = req.full_url, timeout, req.data
        return _Resp(json.dumps({"success": True, "latitude": 30.08, "longitude": -95.42}).encode())

    got = loc.lookup_ip_location(opener=opener)
    assert got == loc.Location(30.08, -95.42, "internet")
    assert seen == {"url": loc.GEO_URL, "timeout": 5.0, "data": None}  # nothing sent but the request
    assert loc.GEO_URL.startswith("https://")


@pytest.mark.parametrize(("raise_or_body", "words"), [
    (TimeoutError(), "in time"), (urllib.error.URLError("offline"), "No internet"),
    (urllib.error.HTTPError(loc.GEO_URL, 429, "Too Many", {}, None), "busy"),
    (b"<html>not json", "could not be read"), (json.dumps({"success": False}).encode(), "could not place"),
    (json.dumps({"success": True, "latitude": "x"}).encode(), "no usable"),
    (json.dumps({"success": True, "latitude": 95.0, "longitude": 0}).encode(), "no usable"),
])
def test_ip_lookup_failures_are_one_short_message(raise_or_body, words: str) -> None:
    def opener(req, timeout):
        if isinstance(raise_or_body, BaseException):
            raise raise_or_body
        return _Resp(raise_or_body)

    with pytest.raises(loc.LocationError, match=words):
        loc.lookup_ip_location(opener=opener)
