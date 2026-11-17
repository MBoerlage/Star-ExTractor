"""RA/Dec parsing to decimal degrees (for CRVAL1/CRVAL2)."""
from __future__ import annotations

import re


def _parts(text: str) -> list[str]:
    return [p for p in re.split(r"[\s:hHdDmMsS°'\"]+", text.strip()) if p]


def parse_ra(text: str) -> float:
    """'18 05 12.3' / '18:05:12.3' / '18h05m12.3s' -> degrees; a single number is degrees."""
    t = text.strip()
    if not t:
        raise ValueError("empty RA")
    parts = _parts(t)
    if len(parts) == 1 and not re.search(r"[hH:]", t):
        deg = float(parts[0])
    else:
        h = float(parts[0])
        m = float(parts[1]) if len(parts) > 1 else 0.0
        s = float(parts[2]) if len(parts) > 2 else 0.0
        if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
            raise ValueError(f"RA out of range: {text}")
        deg = 15.0 * (h + m / 60 + s / 3600)
    if not 0 <= deg < 360:
        raise ValueError(f"RA out of range: {text}")
    return deg


def parse_dec(text: str) -> float:
    """'-25 30 12' / '-25:30:12' / '-25d30m12s' / '-25.503' -> degrees."""
    t = text.strip()
    if not t:
        raise ValueError("empty Dec")
    sign = -1.0 if t.startswith("-") else 1.0
    parts = _parts(t.lstrip("+-"))
    d = float(parts[0])
    m = float(parts[1]) if len(parts) > 1 else 0.0
    s = float(parts[2]) if len(parts) > 2 else 0.0
    if not (0 <= m < 60 and 0 <= s < 60):
        raise ValueError(f"Dec out of range: {text}")
    deg = sign * (d + m / 60 + s / 3600)
    if not -90 <= deg <= 90:
        raise ValueError(f"Dec out of range: {text}")
    return deg


def parse_coords(ra_text: str, dec_text: str) -> tuple[float, float]:
    """RA and Dec text -> (ra_deg, dec_deg). RA 'hh mm ss.s' or decimal degrees, Dec 'dd mm ss' or degrees."""
    return parse_ra(ra_text), parse_dec(dec_text)


def format_ra(deg: float) -> str:
    """Degrees → 'hh mm ss.s'."""
    total = (deg % 360.0) / 15.0 * 3600.0
    total = round(total, 1)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h) % 24:02d} {int(m):02d} {s:04.1f}"


def format_dec(deg: float) -> str:
    """Degrees → '+dd mm ss'."""
    sign = "-" if deg < 0 else "+"
    total = round(abs(deg) * 3600.0)
    d, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{int(d):02d} {int(m):02d} {int(s):02d}"
