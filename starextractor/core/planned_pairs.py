"""Planned pairs (Plan tab): target + reference pairs the user plans to observe. Not the "pair" of the reduce flow
(the top-row Pair selector): a planned pair is only a plan until it is loaded into Acquisition.

Stored in the settings, `plan.pairs` = {"version": 1, "items": [pair, …]}. A pair holds only what the user chose:
id, target (name, RA/Dec in degrees and their source simbad | manual, V, spectral type), reference (name, RA/Dec,
V, spectral type, distance on the sky, and its grade from the bundled list: grade, reasons, reason_text, v_class,
verified; or None), a free-text note and the created time. Visibility, Moon
separation and status are computed when shown, never stored. The observing location is never stored with a pair
or written to a plan file (docs/PLAN_TAB.md). No Qt.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from .coords import format_dec, format_ra, parse_coords

SCHEMA = 1
PLAN_FILE_KIND = "starex-observing-plan"
DUP_ARCMIN = 1.0  # two targets this close (or with the same name) are the same target


# ---- one pair ------------------------------------------------------------------------------------------------------
def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN → None


def _target(d: Any) -> dict[str, Any] | None:
    if not isinstance(d, dict) or not str(d.get("name") or "").strip():
        return None
    ra, dec = _num(d.get("ra_deg")), _num(d.get("dec_deg"))
    if (ra is None or dec is None) and d.get("ra") and d.get("dec"):  # a plan file written by hand: sexagesimal
        try:
            ra, dec = parse_coords(str(d["ra"]), str(d["dec"]))
        except ValueError:
            ra = dec = None
    return {"name": str(d["name"]).strip(), "ra_deg": ra, "dec_deg": dec,
            "source": "simbad" if d.get("source") == "simbad" else "manual",
            "vmag": _num(d.get("vmag")), "sp_type": str(d.get("sp_type") or "")}


def _reference(d: Any) -> dict[str, Any] | None:
    t = _target(d)
    if t is None or t["ra_deg"] is None:
        return None
    out = {"name": t["name"], "ra_deg": t["ra_deg"], "dec_deg": t["dec_deg"], "vmag": t["vmag"],
           "sp_type": t["sp_type"], "distance_deg": _num((d or {}).get("distance_deg"))}
    return out | _grade(d)


def _grade(d: dict[str, Any]) -> dict[str, Any]:
    """The reference's grade as stored; a pair saved before grades resolves it by name from the bundled list
    (blank when the star is not there)."""
    from .plan import grade_fields

    if "grade" not in d:
        return grade_fields(str(d.get("name") or ""))
    reasons = d.get("reasons") or []
    return {"grade": str(d.get("grade") or ""),
            "reasons": [str(r) for r in (reasons if isinstance(reasons, list) else str(reasons).split("+")) if r],
            "reason_text": str(d.get("reason_text") or ""), "v_class": str(d.get("v_class") or ""),
            "verified": bool(d.get("verified"))}


def normalise(d: Any) -> dict[str, Any] | None:
    """A pair from the settings or a plan file (unknown keys dropped, missing ones defaulted); None if unusable."""
    if not isinstance(d, dict):
        return None
    t = _target(d.get("target"))
    if t is None:
        return None
    return {"id": str(d.get("id") or new_id()), "target": t, "reference": _reference(d.get("reference")),
            "note": str(d.get("note") or ""), "created": str(d.get("created") or now_iso())}


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def from_editor(plan_state: dict[str, Any], note: str = "") -> dict[str, Any] | None:
    """A new pair from the Plan editor (session plan state: target, chosen reference). None without a target."""
    t = plan_state.get("target") or {}
    r = plan_state.get("reference") or {}
    ref = None
    if r.get("chosen"):
        ref = {"name": r["name"], "ra_deg": r.get("ra"), "dec_deg": r.get("dec"), "vmag": r.get("v"),
               "sp_type": r.get("sp_type", ""), "distance_deg": r.get("distance_deg"),
               **{k: r[k] for k in ("grade", "reasons", "reason_text", "v_class", "verified") if k in r}}
    return normalise({"id": new_id(), "created": now_iso(), "note": note, "reference": ref,
                      "target": {"name": t.get("name"), "ra_deg": t.get("ra_deg"), "dec_deg": t.get("dec_deg"),
                                 "source": t.get("source"), "vmag": t.get("vmag"), "sp_type": t.get("sp_type")}})


def to_editor(pair: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(target, reference) in the shape of the Plan editor's session state (core/plan.py)."""
    t, r = pair["target"], pair.get("reference")
    target = {"name": t["name"], "ra_deg": t["ra_deg"], "dec_deg": t["dec_deg"], "source": t["source"],
              "vmag": t["vmag"], "otype": "", "simbad_id": t["name"] if t["source"] == "simbad" else "",
              "sp_type": t["sp_type"]}
    ref = ({"name": r["name"], "ra": r["ra_deg"], "dec": r["dec_deg"], "v": r["vmag"], "bv": None,
            "sp_type": r["sp_type"], "distance_deg": r["distance_deg"], "chosen": True,
            **{k: r.get(k) for k in ("grade", "reasons", "reason_text", "v_class", "verified")}} if r else {})
    return target, ref


def _close(a: float | None, b: float | None, tol: float = 1e-6) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) < tol)


def same_content(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Target (name, position, source) and reference (name, position) equal: Update pair has nothing to write."""
    ta, tb = a["target"], b["target"]
    if ta["name"] != tb["name"] or not _close(ta["ra_deg"], tb["ra_deg"]) or not _close(ta["dec_deg"], tb["dec_deg"]):
        return False
    ra, rb = a.get("reference"), b.get("reference")
    if (ra is None) != (rb is None):
        return False
    return ra is None or (ra["name"] == rb["name"] and _close(ra["ra_deg"], rb["ra_deg"]))


def same_target(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Same name (case and spaces ignored) or within DUP_ARCMIN on the sky."""
    ta, tb = a["target"], b["target"]
    if "".join(ta["name"].split()).casefold() == "".join(tb["name"].split()).casefold():
        return True
    if None in (ta["ra_deg"], ta["dec_deg"], tb["ra_deg"], tb["dec_deg"]):
        return False
    from .sky import separation

    return float(separation(ta["ra_deg"], ta["dec_deg"], tb["ra_deg"], tb["dec_deg"])) * 60.0 < DUP_ARCMIN


def duplicate_of(pairs: list[dict[str, Any]], pair: dict[str, Any]) -> int | None:
    """Index of another pair with the same target, or None."""
    return next((i for i, p in enumerate(pairs) if p["id"] != pair["id"] and same_target(p, pair)), None)


# ---- the list ------------------------------------------------------------------------------------------------------
def load(plan_settings: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The list from settings `plan.pairs` (any schema version: items are normalised one by one)."""
    raw = (plan_settings or {}).get("pairs")
    items = raw.get("items") if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    out = [p for p in (normalise(d) for d in items or []) if p is not None]
    seen: set[str] = set()
    for p in out:  # ids must be unique (a hand-edited file)
        if p["id"] in seen:
            p["id"] = new_id()
        seen.add(p["id"])
    return out


def dump(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": SCHEMA, "items": [dict(p) for p in pairs]}


def move(pairs: list[dict[str, Any]], i: int, delta: int) -> int:
    """Move row i by delta (±1); returns its new index."""
    j = max(0, min(len(pairs) - 1, i + delta))
    if j != i:
        pairs.insert(j, pairs.pop(i))
    return j


def sort_by_transit(pairs: list[dict[str, Any]], transit: dict[str, float | None]) -> None:
    """In place, by transit time (pair id → unix); pairs without one keep their order at the end."""
    order = {p["id"]: k for k, p in enumerate(pairs)}
    pairs.sort(key=lambda p: (transit.get(p["id"]) is None, transit.get(p["id"]) or 0.0, order[p["id"]]))


# ---- what is shown (computed, never stored) -------------------------------------------------------------------------
def sky_of(pair: dict[str, Any], lat: float, lon: float, window, min_alt: float):
    """The SkyPlan of the pair's target (core/sky.py, a few ms); None without a position."""
    from . import sky

    t = pair["target"]
    if t["ra_deg"] is None or t["dec_deg"] is None:
        return None
    return sky.compute(lat, lon, window, min_alt, t["ra_deg"], t["dec_deg"])


def row_info(plan, tz=None) -> dict[str, Any]:
    """Table cells and plan lines from a SkyPlan (or None): visible, moon separation, transit."""
    from . import sky

    if plan is None:
        return {"visible": "—", "visible_text": "", "moon_sep": None, "transit": None}
    return {"visible": sky.visible_short(plan, tz), "visible_text": sky.visible_text(plan, tz),
            "moon_sep": plan.moon_sep, "transit": plan.transit[0] if plan.transit else None}


# ---- the observing plan with all pairs ------------------------------------------------------------------------------
def _pos(ra: float | None, dec: float | None) -> dict[str, str | None]:
    return {"ra": None if ra is None else format_ra(ra), "dec": None if dec is None else format_dec(dec)}


def plan_doc(pairs: list[dict[str, Any]], conditions: dict[str, Any] | None,
             infos: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The JSON document: date, window and one entry per pair; round-trips through `pairs_from_doc`. Never the
    coordinates of the observing site."""
    infos = infos or {}
    out: dict[str, Any] = {"kind": PLAN_FILE_KIND, "version": SCHEMA}
    if conditions:
        out |= {k: conditions.get(k) for k in ("date", "window")}
    rows = []
    for p in pairs:
        t, r = p["target"], p.get("reference")
        info = infos.get(p["id"]) or {}
        rows.append({
            "id": p["id"], "created": p["created"], "note": p["note"],
            "target": {**t, **_pos(t["ra_deg"], t["dec_deg"])},
            "reference": {**r, **_pos(r["ra_deg"], r["dec_deg"])} if r else None,
            "visible": info.get("visible") if info.get("visible") not in (None, "—") else None,
            "moon_sep_deg": None if info.get("moon_sep") is None else round(float(info["moon_sep"]), 1)})
    out["pairs"] = rows
    return out


def plan_json(pairs, conditions, infos=None) -> str:
    return json.dumps(plan_doc(pairs, conditions, infos), indent=2, ensure_ascii=False)


def plan_text(pairs: list[dict[str, Any]], conditions: dict[str, Any] | None,
              infos: dict[str, dict[str, Any]] | None = None) -> str:
    """Date and window, then one block per pair (target, reference, RA/Dec, V, visible, Moon separation, note)."""
    infos = infos or {}

    def mag(v: Any) -> str:
        return f"{v:.2f}" if isinstance(v, (int, float)) else "-"

    def star(kind: str, s: dict[str, Any] | None) -> list[str]:
        if not s:
            return [f"  {kind + ':':<11} -"]
        pos = _pos(s["ra_deg"], s["dec_deg"])
        extra = f"  ({s['sp_type']})" if s.get("sp_type") else ""
        out = [f"  {kind + ':':<11} {s['name']}{extra}",
               f"  {'':<11} V {mag(s['vmag'])}   RA {pos['ra'] or '-'}   Dec {pos['dec'] or '-'}"]
        if s.get("grade"):
            why = f": {s['reason_text']}" if s.get("reason_text") else ""
            out.append(f"  {'':<11} Grade {s['grade']}{' (verified)' if s.get('verified') else ''}{why}")
        return out

    lines: list[str] = []
    if conditions:
        w = conditions.get("window") or {}
        window = f"{w.get('from', '-')}–{w.get('to', '-')} local, min altitude {w.get('min_alt', '-')}°" if w else "-"
        lines += [f"{'Date:':<12} {conditions.get('date') or '-'}", f"{'Window:':<12} {window}"]
    lines.append(f"{'Pairs:':<12} {len(pairs)}")
    for k, p in enumerate(pairs, 1):
        info = infos.get(p["id"]) or {}
        sep = info.get("moon_sep")
        lines += ["", f"{k}. {p['target']['name']}" + (f" + {p['reference']['name']}" if p.get("reference") else "")]
        lines += star("Target", p["target"]) + star("Reference", p.get("reference"))
        lines.append(f"  {'Visible:':<11} {info.get('visible_text') or info.get('visible') or '-'}")
        lines.append(f"  {'Moon:':<11} {'-' if sep is None else f'{sep:.0f}° away'}")
        if p["note"]:
            lines.append(f"  {'Note:':<11} {p['note']}")
    return "\n".join(lines)


def pairs_from_doc(doc: Any) -> list[dict[str, Any]]:
    """The pairs of a saved observing plan (JSON). Raises ValueError for a file that is not one."""
    if isinstance(doc, (str, bytes)):
        try:
            doc = json.loads(doc)
        except json.JSONDecodeError as e:
            raise ValueError(f"not JSON ({e.msg}, line {e.lineno})") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("pairs"), list):
        if isinstance(doc, dict) and isinstance(doc.get("target"), dict):  # a one-pair plan from before pairs
            return [p for p in [normalise({"target": doc["target"], "reference": doc.get("reference")})] if p]
        raise ValueError("no planned pairs in it")
    return [p for p in (normalise(d) for d in doc["pairs"]) if p is not None]


def merge(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """existing + the new pairs that are not already there (same id, or same target and reference).
    Returns (list, number added)."""
    out = list(existing)
    added = 0
    for p in new:
        if any(q["id"] == p["id"] or (same_target(q, p) and (q.get("reference") or {}).get("name")
                                      == (p.get("reference") or {}).get("name")) for q in out):
            continue
        out.append(p)
        added += 1
    return out, added
