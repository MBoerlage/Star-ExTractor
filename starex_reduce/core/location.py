"""The observing location for planning (Plan › Observing conditions). No Qt.

PRIVACY: this location is used for planning only. It lives in the settings under plan.location (with its source
and whether the user set it) and is never written to a logbook, FITS header, export file, sets file or the
Observing plan text (docs/PLAN_TAB.md, docs/DATA.md).

Default order when the Plan tab opens (`default_location`): a location the user set in Plan (typed, or "Use my
location") > the app's saved site (settings["site"], Export › Collect with "remember") > the loaded pair's stack
headers (SITELAT/SITELONG, OBSGEO-B/L, LAT-OBS/LONG-OBS) > the pair's own Export Collect fields > none.

The one network call: `lookup_ip_location`, an IP-geolocation request made only when the user clicks "Use my
location" (in a worker, 5 s timeout). Nothing else here touches the network.
"""
from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GEO_URL = "https://ipwho.is/"  # HTTPS, no key; returns {"success": true, "latitude": .., "longitude": ..}
GEO_TIMEOUT_S = 5.0
GEO_TOOLTIP = "Approximate location from your internet address. Check it or type your coordinates."
SOURCES = {"settings": "from settings", "header": "from stack header", "export": "from Export", "internet":
           "from internet", "manual": "manual", "": ""}
HEADER_KEYS = (("SITELAT", "SITELONG"), ("OBSGEO-B", "OBSGEO-L"), ("LAT-OBS", "LONG-OBS"))


class LocationError(ValueError):
    pass


# ---- parsing ------------------------------------------------------------------------------------------------------
_HEMI = {"N": 1, "S": -1, "E": 1, "W": -1}


def parse_angle(text: str, kind: str) -> float:
    """Latitude (kind "lat", -90..90) or longitude ("lon", -180..180) in degrees, north and east positive.

    Accepts "30.67", "-104.02", "30.67 N", "104.02W", "104 01 21 W", "104°01'21\\"W", "-104:01:21". A hemisphere letter
    must match the kind (N/S for latitude, E/W for longitude) and cannot be combined with a minus sign."""
    s = (text or "").strip().upper()
    if not s:
        raise LocationError("empty")
    hemi = None
    m = re.fullmatch(r"(.*?)\s*([NSEW])", s)
    if m:
        s, hemi = m.group(1), m.group(2)
        if (kind == "lat") != (hemi in "NS"):
            raise LocationError(f"{hemi} is not a {'latitude' if kind == 'lat' else 'longitude'} direction")
    s = re.sub(r"[°º'′\"″:dhm]", " ", s.lower()).replace(",", ".").strip()
    parts = s.split()
    if not 1 <= len(parts) <= 3:
        raise LocationError("expected degrees, or degrees minutes seconds")
    try:
        nums = [float(p) for p in parts]
    except ValueError as e:
        raise LocationError(f"not a number: {text!r}") from e
    neg = parts[0].startswith("-")
    if hemi and neg:
        raise LocationError("use either a minus sign or a direction letter")
    if any(not math.isfinite(n) for n in nums) or any(n < 0 or n >= 60 for n in nums[1:]):
        raise LocationError("minutes and seconds must be 0-59")
    if len(nums) > 1 and nums[0] != int(nums[0]):
        raise LocationError("whole degrees before minutes")
    value = abs(nums[0]) + sum(n / 60 ** (i + 1) for i, n in enumerate(nums[1:]))
    value = -value if neg else value * _HEMI.get(hemi or "N", 1)
    limit = 90.0 if kind == "lat" else 180.0
    if not -limit <= value <= limit:
        raise LocationError(f"{'latitude' if kind == 'lat' else 'longitude'} must be within ±{limit:.0f}°")
    return value


def format_angle(value: float, kind: str) -> str:
    hemi = ("N" if value >= 0 else "S") if kind == "lat" else ("E" if value >= 0 else "W")
    return f"{abs(value):.4f}° {hemi}"


# ---- the location and its default ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    source: str  # settings | header | export | internet | manual

    def same_place(self, other: Location | None, tol: float = 1e-4) -> bool:
        return other is not None and abs(self.lat - other.lat) < tol and abs(self.lon - other.lon) < tol

    def to_settings(self) -> dict[str, Any]:
        return {"lat": self.lat, "lon": self.lon, "source": self.source, "user_set": True}


def _valid(lat: Any, lon: Any) -> bool:
    return (isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not isinstance(lat, bool)
            and -90 <= lat <= 90 and -180 <= lon <= 180)


def from_header(header: Any) -> tuple[float, float] | None:
    for klat, klon in HEADER_KEYS:
        lat, lon = header.get(klat), header.get(klon)
        if isinstance(lat, str) or isinstance(lon, str):
            try:
                lat, lon = parse_angle(str(lat), "lat"), parse_angle(str(lon), "lon")
            except LocationError:
                continue
        if isinstance(lon, (int, float)) and not isinstance(lon, bool) and 180.0 < lon <= 360.0:
            lon = float(lon) - 360.0  # 0..360 east → ±180
        if _valid(lat, lon):
            return float(lat), float(lon)
    return None


def site_default(settings: dict[str, Any], stack_paths: list[Path], export_site: dict[str, Any] | None
                 ) -> Location | None:
    """The site location that already exists elsewhere in the app (reads at most a few FITS headers)."""
    site = settings.get("site") or {}
    if _valid(site.get("lat"), site.get("lon")):
        return Location(float(site["lat"]), float(site["lon"]), "settings")
    from .fitsio import read_header

    for p in stack_paths:
        try:
            hit = from_header(read_header(Path(p)))
        except (OSError, ValueError):
            continue
        if hit is not None:
            return Location(hit[0], hit[1], "header")
    ex = export_site or {}
    if _valid(ex.get("lat"), ex.get("lon")):
        return Location(float(ex["lat"]), float(ex["lon"]), "export")
    return None


def user_location(plan_settings: dict[str, Any] | None) -> Location | None:
    """plan.location, when the user set it."""
    loc = (plan_settings or {}).get("location") or {}
    if loc.get("user_set") and _valid(loc.get("lat"), loc.get("lon")):
        return Location(float(loc["lat"]), float(loc["lon"]), str(loc.get("source") or "manual"))
    return None


# ---- the one network call -----------------------------------------------------------------------------------------
def lookup_ip_location(timeout: float = GEO_TIMEOUT_S, opener=urllib.request.urlopen) -> Location:
    """Approximate location of this internet connection. Raises LocationError with a short message."""
    req = urllib.request.Request(GEO_URL, headers={"User-Agent": "StarExtractor", "Accept": "application/json"})
    try:
        with opener(req, timeout=timeout) as r:
            data = json.loads(r.read(65536).decode("utf-8"))
    except TimeoutError as e:
        raise LocationError("The location service did not answer in time.") from e
    except urllib.error.HTTPError as e:
        raise LocationError("The location service is busy (too many requests)." if e.code == 429
                            else f"The location service answered with an error ({e.code}).") from e
    except (urllib.error.URLError, OSError) as e:
        raise LocationError("No internet connection, or the location service cannot be reached.") from e
    except (ValueError, UnicodeDecodeError) as e:
        raise LocationError("The location service sent an answer that could not be read.") from e
    if not isinstance(data, dict) or data.get("success") is False:
        raise LocationError("The location service could not place this connection.")
    lat, lon = data.get("latitude"), data.get("longitude")
    if not _valid(lat, lon):
        raise LocationError("The location service sent no usable position.")
    return Location(float(lat), float(lon), "internet")
