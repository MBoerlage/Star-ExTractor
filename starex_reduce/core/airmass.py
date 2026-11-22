"""Airmass from site, time and coordinates (offline: no IERS download, ~0.01 airmass accuracy)."""
from __future__ import annotations

import math
from datetime import UTC, datetime


def julian_date(t: datetime) -> float:
    t = t.astimezone(UTC) if t.tzinfo else t.replace(tzinfo=UTC)
    return t.timestamp() / 86400.0 + 2440587.5


def lst_deg(jd: float, lon_deg: float) -> float:
    """Local sidereal time (degrees) from the IAU 1982 GMST expression (east longitude positive)."""
    d = jd - 2451545.0
    gmst_h = 18.697374558 + 24.06570982441908 * d
    return (gmst_h * 15.0 + lon_deg) % 360.0


def altitude_deg(ra_deg: float, dec_deg: float, lat_deg: float, lon_deg: float, t: datetime) -> float:
    ha = math.radians(lst_deg(julian_date(t), lon_deg) - ra_deg)
    lat, dec = math.radians(lat_deg), math.radians(dec_deg)
    s = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def airmass(alt_deg: float) -> float:
    """Kasten & Young (1989) relative air mass; finite down to the horizon."""
    if alt_deg <= 0:
        return math.inf
    z = 90.0 - alt_deg
    return 1.0 / (math.cos(math.radians(z)) + 0.50572 * (96.07995 - z) ** -1.6364)


def airmass_at(ra_deg: float, dec_deg: float, lat_deg: float, lon_deg: float, t: datetime) -> float:
    return airmass(altitude_deg(ra_deg, dec_deg, lat_deg, lon_deg, t))


def mean_airmass(ra_deg: float, dec_deg: float, lat_deg: float, lon_deg: float,
                 start: datetime, end: datetime, steps: int = 9) -> float:
    """Airmass averaged over the exposure (what the summed light actually went through)."""
    if end <= start:
        return airmass_at(ra_deg, dec_deg, lat_deg, lon_deg, start)
    dt = (end - start) / (steps - 1)
    return sum(airmass_at(ra_deg, dec_deg, lat_deg, lon_deg, start + i * dt) for i in range(steps)) / steps
