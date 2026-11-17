"""Who is being observed, as the session knows it in several places: the Plan (target + chosen reference),
Files (names, SIMBAD result, coordinates) and the FITS OBJECT of the lights. One place to compare them,
pick the spectral type the Response step uses, and list the inconsistencies. No Qt, no network, no pixels
(only the header of the first used light)."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .coords import format_dec, format_ra
from .session import Session

SIDES = ("target", "reference")
SIDE_TITLE = {"target": "Target", "reference": "Reference"}


def norm(name: str | None) -> str:
    """Case, spaces, '*', '_', '-' and '.' ignored: 'HD 193369' = 'hd193369', '* gam Cas' = 'Gam_Cas'."""
    return re.sub(r"[\s*_.\-]+", "", (name or "").lower())


def same_name(a: str | None, b: str | None) -> bool:
    return bool(norm(a)) and norm(a) == norm(b)


def plan_entry(session: Session, side: str) -> dict[str, Any] | None:
    """The Plan's target or chosen reference as {name, ra, dec, sp_type}, or None (read only)."""
    p = session.params.get("plan")
    if not isinstance(p, dict):
        return None
    if side == "target":
        t = p.get("target") or {}
        if not t.get("name"):
            return None
        ra, dec = t.get("ra_deg"), t.get("dec_deg")
    else:
        t = p.get("reference") or {}
        if not t.get("chosen") or not t.get("name"):
            return None
        ra, dec = t.get("ra"), t.get("dec")
    return {"name": t["name"], "ra": None if ra is None else format_ra(float(ra)),
            "dec": None if dec is None else format_dec(float(dec)), "sp_type": t.get("sp_type", "") or ""}


def sp_type_for(session: Session, side: str) -> tuple[str, str]:
    """(spectral type, where it came from): the Files SIMBAD result, else the Plan's entry for the same
    star (or for this side when Files has no name yet). ('', '') when neither knows it."""
    d = session.inputs[side]
    res = d.get("simbad") or {}
    if res.get("found") and res.get("sp_type"):
        return str(res["sp_type"]), "SIMBAD"
    pe = plan_entry(session, side)
    if pe and pe["sp_type"] and (not d.get("name") or same_name(d.get("name"), pe["name"])):
        return pe["sp_type"], "Plan"
    return "", ""


@lru_cache(maxsize=64)
def _object_of(path: str, mtime: float) -> str:
    from .fitsio import read_header

    try:
        return str(read_header(Path(path)).get("OBJECT") or "").strip()
    except (OSError, ValueError):
        return ""


def header_object(session: Session, side: str) -> str:
    """OBJECT of the first used light (info only: OBJECT never assigns a role)."""
    for p in session.used_lights(side):
        try:
            return _object_of(p, Path(p).stat().st_mtime)
        except OSError:
            continue
    return ""


def checks(session: Session, site_known: bool, airmass_on: bool = True) -> list[tuple[str, str]]:
    """Inconsistencies between the tabs, as (level, text), most important first. level: warn | info."""
    from .plan import flux_flag

    out: list[tuple[str, str]] = []
    for side in SIDES:
        d = session.inputs[side]
        name = d.get("name", "")
        title = SIDE_TITLE[side]
        pe = plan_entry(session, side)
        if pe and name and not same_name(name, pe["name"]):
            out.append(("warn", f"{title}: the Plan says {pe['name']}, Files says {name}."))
        obj = header_object(session, side) if d.get("lights") else ""
        if obj and name and not same_name(obj, name):
            out.append(("info", f"{title}: Files says {name}, the light frames say OBJECT = {obj}."))
    ref = session.inputs["reference"]
    if ref.get("lights") or ref.get("name"):
        sp, src = sp_type_for(session, "reference")
        if not sp:
            out.append(("warn", "Reference: no spectral type (verify it with SIMBAD in Files); the Response "
                                "library falls back to A0V."))
        elif flux_flag(sp) == "warn":
            out.append(("warn", f"Reference: {sp} ({src}) is an emission-line or early-type star, a poor flux "
                                "reference."))
    if airmass_on:
        missing = [SIDE_TITLE[s].lower() for s in SIDES
                   if session.inputs[s].get("lights") and not ((session.inputs[s].get("coords") or {}).get("ra"))]
        if missing:
            out.append(("info", f"Airmass correction needs the {' and '.join(missing)} coordinates (Files)."))
        if not site_known:
            out.append(("info", "Airmass correction needs the site position (Export → Location, or the frame "
                                "headers)."))
    return out
