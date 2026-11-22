"""Sky positions for planning (Plan › Observing conditions). numpy only, vectorised, offline; no Qt, no astropy.

Accuracy (tests/test_sky.py checks it against astropy): target alt/az < 0.1° (J2000 precessed to the date, no
nutation, aberration or refraction), Sun < 0.1° (Meeus low-precision theory), Moon < 0.5° (Meeus ch. 47, the
main terms, with topocentric parallax), rise/set times < 2 min. Time is unix seconds (UTC, taken as UT1).

Conventions: latitude north +, longitude east +; azimuth from north through east; altitudes in degrees without
refraction. "Rises"/"sets" for the observing markers mean crossing the minimum altitude; the horizon events are
reported separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

import numpy as np

STEP_S = 300  # 5 minutes
MARGIN_H = 2.0  # the plotted range: the window ± 2 h
ASTRO_SUN_ALT = -18.0
TWILIGHT = (("day", 0.0), ("civil", -6.0), ("nautical", -12.0), ("astronomical", -18.0))  # sun above this

_D2R = np.pi / 180.0


# ---- time ---------------------------------------------------------------------------------------------------------
def jd(unix: np.ndarray) -> np.ndarray:
    return np.asarray(unix, float) / 86400.0 + 2440587.5


def centuries(unix: np.ndarray) -> np.ndarray:
    return (jd(unix) - 2451545.0) / 36525.0


def lst_deg(unix: np.ndarray, lon_deg: float) -> np.ndarray:
    """Local mean sidereal time, IAU 1982 GMST (as core/airmass.py)."""
    d = jd(unix) - 2451545.0
    return np.mod((18.697374558 + 24.06570982441908 * d) * 15.0 + lon_deg, 360.0)


# ---- coordinates --------------------------------------------------------------------------------------------------
def precess(ra_deg: float, dec_deg: float, unix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """J2000 mean place to the mean equinox of the date (IAU 1976 angles, Meeus 21.2)."""
    t = centuries(unix)
    zeta = (2306.2181 * t + 0.30188 * t**2 + 0.017998 * t**3) / 3600.0
    z = (2306.2181 * t + 1.09468 * t**2 + 0.018203 * t**3) / 3600.0
    theta = (2004.3109 * t - 0.42665 * t**2 - 0.041833 * t**3) / 3600.0
    a, d = ra_deg * _D2R, dec_deg * _D2R
    zt, th = zeta * _D2R, theta * _D2R
    A = np.cos(d) * np.sin(a + zt)
    B = np.cos(th) * np.cos(d) * np.cos(a + zt) - np.sin(th) * np.sin(d)
    C = np.sin(th) * np.cos(d) * np.cos(a + zt) + np.cos(th) * np.sin(d)
    ra = np.mod(np.arctan2(A, B) / _D2R + z, 360.0)
    dec = np.arcsin(np.clip(C, -1.0, 1.0)) / _D2R
    return ra, dec


def altaz(ra_deg: np.ndarray, dec_deg: np.ndarray, lat_deg: float, lon_deg: float,
          unix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(alt, az) in degrees for equatorial coordinates of the date."""
    h = (lst_deg(unix, lon_deg) - ra_deg) * _D2R
    d, p = np.asarray(dec_deg) * _D2R, lat_deg * _D2R
    sin_alt = np.sin(p) * np.sin(d) + np.cos(p) * np.cos(d) * np.cos(h)
    alt = np.arcsin(np.clip(sin_alt, -1.0, 1.0))
    az = np.arctan2(-np.sin(h) * np.cos(d), np.cos(p) * np.sin(d) - np.sin(p) * np.cos(d) * np.cos(h))
    return alt / _D2R, np.mod(az / _D2R, 360.0)


def separation(ra1, dec1, ra2, dec2) -> np.ndarray:
    a1, d1, a2, d2 = (np.asarray(x) * _D2R for x in (ra1, dec1, ra2, dec2))
    c = np.sin(d1) * np.sin(d2) + np.cos(d1) * np.cos(d2) * np.cos(a1 - a2)
    return np.arccos(np.clip(c, -1.0, 1.0)) / _D2R


def _obliquity(t: np.ndarray) -> np.ndarray:
    return 23.439291 - 0.0130042 * t


def _ecl_to_eq(lam: np.ndarray, beta: np.ndarray, eps: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lam_r, b, e = lam * _D2R, beta * _D2R, eps * _D2R
    ra = np.arctan2(np.sin(lam_r) * np.cos(e) - np.tan(b) * np.sin(e), np.cos(lam_r))
    dec = np.arcsin(np.sin(b) * np.cos(e) + np.cos(b) * np.sin(e) * np.sin(lam_r))
    return np.mod(ra / _D2R, 360.0), dec / _D2R


# ---- Sun ----------------------------------------------------------------------------------------------------------
def sun_ecliptic(unix: np.ndarray) -> np.ndarray:
    """Apparent ecliptic longitude of the Sun (Meeus ch. 25, low precision, ~0.01°)."""
    t = centuries(unix)
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t**2
    m = (357.52911 + 35999.05029 * t - 0.0001537 * t**2) * _D2R
    c = ((1.914602 - 0.004817 * t - 0.000014 * t**2) * np.sin(m) + (0.019993 - 0.000101 * t) * np.sin(2 * m)
         + 0.000289 * np.sin(3 * m))
    omega = (125.04 - 1934.136 * t) * _D2R
    return np.mod(l0 + c - 0.00569 - 0.00478 * np.sin(omega), 360.0)


def sun_radec(unix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = centuries(unix)
    omega = (125.04 - 1934.136 * t) * _D2R
    lam = sun_ecliptic(unix)
    return _ecl_to_eq(lam, np.zeros_like(lam), _obliquity(t) + 0.00256 * np.cos(omega))


# ---- Moon (Meeus ch. 47, main terms) ------------------------------------------------------------------------------
# D, M, M', F, Σl (1e-6 deg), Σr (1e-3 km)
_MOON_LR = np.array([
    (0, 0, 1, 0, 6288774, -20905355), (2, 0, -1, 0, 1274027, -3699111), (2, 0, 0, 0, 658314, -2955968),
    (0, 0, 2, 0, 213618, -569925), (0, 1, 0, 0, -185116, 48888), (0, 0, 0, 2, -114332, -3149),
    (2, 0, -2, 0, 58793, 246158), (2, -1, -1, 0, 57066, -152138), (2, 0, 1, 0, 53322, -170733),
    (2, -1, 0, 0, 45758, -204586), (0, 1, -1, 0, -40923, -129620), (1, 0, 0, 0, -34720, 108743),
    (0, 1, 1, 0, -30383, 104755), (2, 0, 0, -2, 15327, 10321), (0, 0, 1, 2, -12528, 0),
    (0, 0, 1, -2, 10980, 79661), (4, 0, -1, 0, 10675, -34782), (0, 0, 3, 0, 10034, -23210),
    (4, 0, -2, 0, 8548, -21636), (2, 1, -1, 0, -7888, 24208), (2, 1, 0, 0, -6766, 30824),
    (1, 0, -1, 0, -5163, -8379), (1, 1, 0, 0, 4987, -16675), (2, -1, 1, 0, 4036, -12831),
    (2, 0, 2, 0, 3994, -10445), (4, 0, 0, 0, 3861, -11650), (2, 0, -3, 0, 3665, 14403),
    (0, 1, -2, 0, -2689, -7003), (2, 0, -1, 2, -2602, 0), (2, -1, -2, 0, 2390, 10056),
    (1, 0, 1, 0, -2348, 6322), (2, -2, 0, 0, 2236, -9884),
], float)
# D, M, M', F, Σb (1e-6 deg)
_MOON_B = np.array([
    (0, 0, 0, 1, 5128122), (0, 0, 1, 1, 280602), (0, 0, 1, -1, 277693), (2, 0, 0, -1, 173237),
    (2, 0, -1, 1, 55413), (2, 0, -1, -1, 46271), (2, 0, 0, 1, 32573), (0, 0, 2, 1, 17198),
    (2, 0, 1, -1, 9266), (0, 0, 2, -1, 8822), (2, -1, 0, -1, 8216), (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200), (2, 1, 0, -1, -3359), (2, -1, -1, 1, 2463), (2, -1, 0, 1, 2211),
    (2, -1, -1, -1, 2065), (0, 1, -1, -1, -1870), (4, 0, -1, -1, 1828), (0, 1, 0, 1, -1794),
], float)


def moon_ecliptic(unix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Geocentric ecliptic longitude, latitude (deg, of date) and distance (km)."""
    t = np.atleast_1d(centuries(unix))
    lp = 218.3164477 + 481267.88123421 * t
    args = np.stack([297.8501921 + 445267.1114034 * t, 357.5291092 + 35999.0502909 * t,
                     134.9633964 + 477198.8675055 * t, 93.2720950 + 483202.0175233 * t])  # D, M, M', F
    e = 1.0 - 0.002516 * t
    a1, a2, a3 = 119.75 + 131.849 * t, 53.09 + 479264.290 * t, 313.45 + 481266.484 * t

    def series(table: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        phase = (table[:, :4] @ args) * _D2R  # (terms, n)
        ecc = np.where(np.abs(table[:, 1:2]) == 1, e, np.where(np.abs(table[:, 1:2]) == 2, e * e, 1.0))
        return phase, ecc

    ph, ecc = series(_MOON_LR)
    sl = (_MOON_LR[:, 4:5] * ecc * np.sin(ph)).sum(0)
    sr = (_MOON_LR[:, 5:6] * ecc * np.cos(ph)).sum(0)
    phb, eccb = series(_MOON_B)
    sb = (_MOON_B[:, 4:5] * eccb * np.sin(phb)).sum(0)
    f, mp = args[3], args[2]
    sl += 3958 * np.sin(a1 * _D2R) + 1962 * np.sin((lp - f) * _D2R) + 318 * np.sin(a2 * _D2R)
    sb += (-2235 * np.sin(lp * _D2R) + 382 * np.sin(a3 * _D2R) + 175 * np.sin((a1 - f) * _D2R)
           + 175 * np.sin((a1 + f) * _D2R) + 127 * np.sin((lp - mp) * _D2R) - 115 * np.sin((lp + mp) * _D2R))
    lam = np.mod(lp + sl / 1e6, 360.0)
    return lam, sb / 1e6, 385000.56 + sr / 1000.0


def moon_radec(unix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(ra, dec, distance km), geocentric, of date."""
    lam, beta, dist = moon_ecliptic(unix)
    ra, dec = _ecl_to_eq(lam, beta, _obliquity(centuries(unix)))
    return ra, dec, dist


def moon_altaz(lat_deg: float, lon_deg: float, unix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Topocentric alt/az: the geocentric position lowered by the parallax (up to ~1°)."""
    ra, dec, dist = moon_radec(unix)
    alt, az = altaz(ra, dec, lat_deg, lon_deg, unix)
    parallax = np.arcsin(6378.14 / dist) / _D2R
    return alt - parallax * np.cos(alt * _D2R), az


def moon_illumination(unix: np.ndarray) -> np.ndarray:
    """Illuminated fraction 0..1 from the Sun-Moon elongation (geocentric)."""
    lam, beta, _ = moon_ecliptic(unix)
    psi = np.arccos(np.cos(beta * _D2R) * np.cos((lam - sun_ecliptic(unix)) * _D2R))
    return (1.0 - np.cos(psi)) / 2.0


# ---- events -------------------------------------------------------------------------------------------------------
def crossings(unix: np.ndarray, alt: np.ndarray, level: float) -> list[tuple[float, str]]:
    """Times where alt crosses level (linear interpolation between samples): (unix, "rise" | "set")."""
    a = np.asarray(alt) - level
    out = []
    for i in np.nonzero(np.signbit(a[:-1]) != np.signbit(a[1:]))[0]:
        f = a[i] / (a[i] - a[i + 1])
        out.append((float(unix[i] + f * (unix[i + 1] - unix[i])), "rise" if a[i + 1] > a[i] else "set"))
    return out


def transit(ra_deg: float, dec_deg: float, lat_deg: float, lon_deg: float, near_unix: float) -> tuple[float, float]:
    """The upper culmination nearest near_unix: (unix, altitude)."""
    ra, dec = precess(ra_deg, dec_deg, np.array([near_unix]))
    ha = (lst_deg(np.array([near_unix]), lon_deg)[0] - ra[0] + 180.0) % 360.0 - 180.0  # -180..180
    t = near_unix - ha / 360.0 * 86164.0905  # one sidereal day per 360°
    return t, 90.0 - abs(lat_deg - float(dec[0]))


# ---- the plan -----------------------------------------------------------------------------------------------------
def _at(clock: datetime, tz=None) -> datetime:
    """A naive clock time read in `tz` (not converted from the computer's zone); the computer's zone when None."""
    return clock.astimezone() if tz is None else clock.replace(tzinfo=tz)


@dataclass
class Window:
    start: datetime  # aware, local
    end: datetime

    @classmethod
    def from_local(cls, evening: datetime | None, start: time, end: time, tz=None) -> Window:
        """A window on the evening `evening` (a date) from `start` to `end`, crossing midnight when end <= start;
        as clock times in `tz` (the computer's time zone when None)."""
        day = (evening or datetime.now()).date()
        s = _at(datetime.combine(day, start), tz)
        e = _at(datetime.combine(day, end), tz)
        if e <= s:
            e = _at(datetime.combine(day + timedelta(days=1), end), tz)
        return cls(s, e)


@dataclass
class SkyPlan:
    unix: np.ndarray  # the plotted range, every 5 min
    window: tuple[float, float]  # unix
    min_alt: float
    sun_alt: np.ndarray
    moon_alt: np.ndarray
    moon_az: np.ndarray
    target_alt: np.ndarray | None = None
    target_az: np.ndarray | None = None
    events: list[tuple[float, str]] = field(default_factory=list)  # min-altitude crossings in the window
    horizon_events: list[tuple[float, str]] = field(default_factory=list)  # rise/set over the plotted range
    transit: tuple[float, float] | None = None
    moon_sep: float | None = None  # at the window middle
    moon_sep_transit: float | None = None
    moon_lit: float = 0.0  # at the window middle
    state: str = "no target"  # ok | circumpolar | never rises | never above min | no target

    def in_window(self) -> np.ndarray:
        return (self.unix >= self.window[0]) & (self.unix <= self.window[1])


def compute(lat: float, lon: float, window: Window, min_alt: float = 25.0, ra: float | None = None,
            dec: float | None = None, step_s: int = STEP_S) -> SkyPlan:
    """Everything the Plan card draws: one vectorised pass (a few ms)."""
    w0, w1 = window.start.timestamp(), window.end.timestamp()
    pad = MARGIN_H * 3600.0
    unix = np.arange(w0 - pad, w1 + pad + 1.0, step_s, dtype=float)
    sun_ra, sun_dec = sun_radec(unix)
    sun_alt, _ = altaz(sun_ra, sun_dec, lat, lon, unix)
    moon_alt, moon_az = moon_altaz(lat, lon, unix)
    mid = np.array([(w0 + w1) / 2.0])
    plan = SkyPlan(unix, (w0, w1), min_alt, sun_alt, moon_alt, moon_az,
                   moon_lit=float(moon_illumination(mid)[0]))
    if ra is None or dec is None:
        return plan
    tra, tdec = precess(ra, dec, unix)
    plan.target_alt, plan.target_az = altaz(tra, tdec, lat, lon, unix)
    mra, mdec, _ = moon_radec(mid)
    pra, pdec = precess(ra, dec, mid)
    plan.moon_sep = float(separation(pra, pdec, mra, mdec)[0])
    plan.transit = transit(ra, dec, lat, lon, float(mid[0]))
    tra_t, tdec_t = precess(ra, dec, np.array([plan.transit[0]]))
    mra_t, mdec_t, _ = moon_radec(np.array([plan.transit[0]]))
    plan.moon_sep_transit = float(separation(tra_t, tdec_t, mra_t, mdec_t)[0])
    plan.horizon_events = crossings(unix, plan.target_alt, 0.0)
    inside = plan.in_window()
    plan.events = [(t, k) for t, k in crossings(unix, plan.target_alt, min_alt) if w0 <= t <= w1]
    max_alt = 90.0 - abs(lat - dec)  # upper culmination
    min_ever = -90.0 + abs(lat + dec)  # lower culmination
    if min_ever > 0.0:
        plan.state = "circumpolar"
    elif max_alt < 0.0:
        plan.state = "never rises"
    elif not np.any(plan.target_alt[inside] >= min_alt):
        plan.state = "never above min"
    else:
        plan.state = "ok"
    return plan


def astronomical_night(lat: float, lon: float, evening: datetime, tz=None) -> tuple[datetime, datetime] | None:
    """Sun at -18° on the evening `evening` (local): (end of evening twilight, start of morning twilight), or None
    when the Sun does not go that low (or does not come back up) between local noon and the next noon."""
    noon = _at(datetime.combine(evening.date(), time(12, 0)), tz)
    unix = np.arange(noon.timestamp(), noon.timestamp() + 86400.0 + 1.0, 60.0)
    ra, dec = sun_radec(unix)
    alt, _ = altaz(ra, dec, lat, lon, unix)
    ev = crossings(unix, alt, ASTRO_SUN_ALT)
    down = next((t for t, k in ev if k == "set"), None)
    up = next((t for t, k in ev if k == "rise" and down is not None and t > down), None)
    if down is None or up is None:
        return None
    return datetime.fromtimestamp(down).astimezone(tz), datetime.fromtimestamp(up).astimezone(tz)


def twilight_bands(unix: np.ndarray, sun_alt: np.ndarray) -> list[tuple[float, float, str]]:
    """Contiguous (start, end, kind) spans: day | civil | nautical | astronomical | night."""
    kinds = np.full(sun_alt.shape, "night", dtype=object)
    for name, level in reversed(TWILIGHT):  # deepest first, so the brighter kinds overwrite
        kinds[sun_alt > level] = name
    out: list[tuple[float, float, str]] = []
    start = 0
    for i in range(1, len(unix) + 1):
        if i == len(unix) or kinds[i] != kinds[start]:
            out.append((float(unix[start]), float(unix[min(i, len(unix) - 1)]), str(kinds[start])))
            start = i
    return out


def hhmm(unix: float, tz=None) -> str:
    return datetime.fromtimestamp(unix).astimezone(tz).strftime("%H:%M")


def visible_span(plan: SkyPlan) -> tuple[float, float] | None:
    """(from, to) in unix of the longest stretch above the minimum altitude inside the window (min-altitude
    crossings where it rises or sets there, else the window ends); None without one."""
    if plan.target_alt is None or plan.state in ("no target", "never rises", "never above min"):
        return None
    inside = plan.in_window()
    idx = np.nonzero(inside & (plan.target_alt >= plan.min_alt))[0]
    if not len(idx):
        return None
    run = max(np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1), key=len)
    first, last = np.nonzero(inside)[0][[0, -1]]
    lo, hi = float(plan.unix[run[0]]), float(plan.unix[run[-1]])
    t0 = plan.window[0] if run[0] == first else next(
        (t for t, k in plan.events if k == "rise" and lo - STEP_S <= t <= hi), max(lo, plan.window[0]))
    t1 = plan.window[1] if run[-1] == last else next(
        (t for t, k in plan.events if k == "set" and lo <= t <= hi + STEP_S), min(hi, plan.window[1]))
    return t0, t1


def visible_text(plan: SkyPlan, tz=None) -> str:
    """When the target is up in the window: "Above 25° from 18:00 to 22:46", "Always above the horizon · Above …",
    "Never rises at this latitude", "Not above 25° in the window (highest 12°)"; "" without a target."""
    if plan.state == "no target" or plan.target_alt is None:
        return ""
    if plan.state == "never rises":
        return "Never rises at this latitude"
    parts = ["Always above the horizon"] if plan.state == "circumpolar" else []
    if plan.state == "never above min":
        peak = float(plan.target_alt[plan.in_window()].max())
        parts.append(f"Not above {plan.min_alt:.0f}° in the window (highest {peak:.0f}°)")
        return " · ".join(parts)
    span = visible_span(plan)
    if span is not None:
        parts.append(f"Above {plan.min_alt:.0f}° from {hhmm(span[0], tz)} to {hhmm(span[1], tz)}")
    return " · ".join(parts)


def visible_short(plan: SkyPlan | None, tz=None) -> str:
    """For a table cell: "18:00–22:46", "not above 25°", "never rises" or "—" (no target or no location)."""
    if plan is None or plan.target_alt is None or plan.state == "no target":
        return "—"
    if plan.state == "never rises":
        return "never rises"
    span = visible_span(plan)
    if span is None:
        return f"not above {plan.min_alt:.0f}°"
    return f"{hhmm(span[0], tz)}–{hhmm(span[1], tz)}"


def summary(plan: SkyPlan, tz=None) -> str:
    """One line under the plots, e.g. "Above 25° from 18:00 to 22:46 · transit 18:12 at 72° · Moon 63° away,
    71 % lit"."""
    moon = f"Moon {plan.moon_sep:.0f}° away, {plan.moon_lit * 100:.0f} % lit" if plan.moon_sep is not None \
        else f"Moon {plan.moon_lit * 100:.0f} % lit"
    parts = [visible_text(plan, tz)]
    if plan.transit is not None and plan.state != "never rises":
        parts.append(f"transit {hhmm(plan.transit[0], tz)} at {plan.transit[1]:.0f}°")
    parts.append(moon)
    return " · ".join(p for p in parts if p)
