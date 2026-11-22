"""AAVSO Variable Star Index (VSX) object lookup: one request per user click, cached locally.

Endpoint (verified live 2026-11-16, unofficial interface, so configurable in settings["vsx_api_url"]):
    https://vsx.aavso.org/index.php?view=api.object&ident=<name>&format=json
Found:      {"VSXObject": {"Name", "AUID", "RA2000", "Declination2000", "VariabilityType", "Period",
                           "MaxMag", "MinMag", "SpectralType", "Category", "OID", "Constellation", ...}}
Not found:  {"VSXObject": []}
Querying VSX is kept separate from FITS keywords: the lookup only returns data; the Export tab applies
the canonical name (and coordinates) to OBJNAME only when the user asks for it.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .settings import app_dir, write_json_atomic

DEFAULT_URL = "https://vsx.aavso.org/index.php?view=api.object&ident={name}&format=json"
VSX_SITE = "https://vsx.aavso.org/"
USER_AGENT = "star-ex-tractor/0.1 (spectroscopy reduction)"
CACHE_FILE = "vsx_cache.json"


@dataclass
class VsxObject:
    name: str
    auid: str = ""
    ra_deg: float | None = None
    dec_deg: float | None = None
    var_type: str = ""
    period: str = ""
    max_mag: str = ""
    min_mag: str = ""
    spectral_type: str = ""
    oid: str = ""

    def summary(self) -> str:
        parts = [f"AUID {self.auid}" if self.auid else "", f"type {self.var_type}" if self.var_type else "",
                 f"period {self.period} d" if self.period else "",
                 f"range {self.max_mag} – {self.min_mag}" if self.max_mag or self.min_mag else "",
                 f"Sp {self.spectral_type}" if self.spectral_type else ""]
        return " · ".join(p for p in parts if p)


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse(body: str) -> tuple[list[VsxObject], str | None]:
    """VSX JSON → objects (0, 1 or several) or an error message for an invalid answer."""
    try:
        data = json.loads(body)
    except ValueError:
        return [], "VSX answered in an unexpected format"
    obj = data.get("VSXObject") if isinstance(data, dict) else None
    items = obj if isinstance(obj, list) else [obj] if isinstance(obj, dict) else None
    if items is None:
        return [], "VSX answered in an unexpected format"
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("Name"):
            out.append(VsxObject(str(it["Name"]), str(it.get("AUID", "")), _float(it.get("RA2000")),
                                 _float(it.get("Declination2000")), str(it.get("VariabilityType", "")),
                                 str(it.get("Period", "")), str(it.get("MaxMag", "")), str(it.get("MinMag", "")),
                                 str(it.get("SpectralType", "")), str(it.get("OID", ""))))
    return out, None


def _cache_path() -> Path:
    return app_dir() / CACHE_FILE


def _cache_get(name: str) -> dict[str, Any] | None:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8")).get(name.strip().lower())
    except (OSError, ValueError):
        return None


def _cache_put(name: str, obj: VsxObject) -> None:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[name.strip().lower()] = {**asdict(obj), "cached": time.strftime("%Y-%m-%d")}
    write_json_atomic(_cache_path(), data)


def lookup(ctx: Any, name: str, url_template: str = "", timeout: float = 10.0,
           use_cache: bool = True) -> dict[str, Any]:
    """Runs in a worker. Returns {query, found, object, matches, message, offline, cached}."""
    query = name.strip()
    if not query:
        return {"query": query, "found": False, "message": "Type the object name first"}
    if use_cache and (hit := _cache_get(query)) is not None:
        hit = {k: v for k, v in hit.items() if k in VsxObject.__dataclass_fields__}
        obj = VsxObject(**hit)
        return {"query": query, "found": True, "object": asdict(obj), "cached": True,
                "message": f"VSX: {obj.name} (cached)"}
    url = (url_template or DEFAULT_URL).format(name=urllib.parse.quote(query))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
    except OSError as exc:  # includes timeouts, DNS failures, HTTP errors
        return {"query": query, "found": False, "offline": True, "message": f"Could not reach VSX ({exc})"}
    objs, error = parse(body)
    if error:
        return {"query": query, "found": False, "message": error}
    if not objs:
        return {"query": query, "found": False, "message": f"“{query}” is not in VSX: try the VSX designation"}
    if len(objs) > 1:
        return {"query": query, "found": False, "matches": [asdict(o) for o in objs],
                "message": f"{len(objs)} VSX objects match “{query}”: pick one and type its exact name"}
    _cache_put(query, objs[0])
    return {"query": query, "found": True, "object": asdict(objs[0]), "cached": False,
            "message": f"VSX: {objs[0].name}"}
