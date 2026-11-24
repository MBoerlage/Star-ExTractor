"""Plan tab state (session.params["plan"]: target, chosen reference, N) and the observing plan it
produces as text or JSON. The plan stays on its own: it does not fill in Select or Response. No Qt."""
from __future__ import annotations

import json
from typing import Any

from .coords import format_dec, format_ra
from .reference_stars import ReferenceStar, ReferenceStarMatch, is_emission, star_by_name
from .session import Session

DEFAULT_N = 10


def plan_state(session: Session) -> dict[str, Any]:
    p = session.params.get("plan")
    if not isinstance(p, dict):
        p = session.params["plan"] = {}
    p.setdefault("n", DEFAULT_N)
    p.setdefault("target", {})
    p.setdefault("reference", {})
    if is_emission(str(p["reference"].get("sp_type", ""))):  # saved before emission stars were excluded
        session.log(f"Plan: reference star {p['reference'].get('name')} dropped (emission-line star)")
        p["reference"] = {}
    if p["reference"].get("chosen") and "grade" not in p["reference"]:  # saved before the list had grades
        p["reference"].update(grade_fields(p["reference"].get("name", "")))
    return p


# ---- grades (core/reference_stars.py) ----------------------------------------------------------------
GRADE_CHIP = {"good": "✓ good", "ok": "ok", "poor": "! poor"}  # text and shape carry the grade, not colour
GRADE_TIP = {"good": "Good reference: a likely match to the library spectrum of its type",
             "ok": "OK reference: usable, with the reasons below",
             "poor": "Poor reference: use only when nothing better is up"}


def grade_fields(name: str, star: ReferenceStar | None = None) -> dict[str, Any]:
    """grade, reasons, reason_text, v_class, verified of a reference star: from the star, else looked up by name
    in the bundled list; blank when it is not there (an old pair, a star from another list)."""
    s = star or star_by_name(name)
    if s is None:
        return {"grade": "", "reasons": [], "reason_text": "", "v_class": "", "verified": False}
    return {"grade": s.grade, "reasons": list(s.reasons), "reason_text": s.reason_text, "v_class": s.v_class,
            "verified": s.verified}


def grade_tip(grade: str, reason_text: str, verified: bool = False) -> str:
    """Tooltip of a grade chip: what the grade means, the reasons in words, and the verified mark."""
    lines = [GRADE_TIP.get(grade, "No grade: this star is not in the bundled list")]
    if reason_text:
        lines.append("Why: " + reason_text.replace("; ", ", "))
    if verified:
        lines.append("Verified by hand (own observation)")
    return "\n".join(lines)


FLUX_WARN_TIP = "Emission/early type: poor flux reference"
FLUX_GOOD_TIP = "A0–A3 V without emission: good flux reference"


def flux_flag(sp_type: str) -> str | None:
    """Only for a reference without a grade (an old list or saved plan).
    'warn' for emission ('e') or earlier than A0 (O, B); 'good' for A0–A3 V without 'e'; else None."""
    from .response import parse_sptype

    if is_emission(sp_type):
        return "warn"
    parsed = parse_sptype(sp_type)
    if parsed is None:
        return None
    letter, sub, lum = parsed
    if letter in ("O", "B"):
        return "warn"
    if letter == "A" and sub <= 3 and lum == "V":
        return "good"
    return None


def _same_target(a: dict[str, Any], b: dict[str, Any]) -> bool:
    def pos(d: dict[str, Any]) -> tuple:
        return tuple(None if d.get(k) is None else round(float(d[k]), 6) for k in ("ra_deg", "dec_deg"))
    return a.get("name", "") == b.get("name", "") and pos(a) == pos(b)


def set_target(session: Session, name: str, ra_deg: float | None, dec_deg: float | None, source: str,
               simbad_result: dict[str, Any] | None = None) -> bool:
    """Store the target; a different target clears the reference choice. Returns True when it changed."""
    p = plan_state(session)
    res = simbad_result or {}
    old = p["target"]
    same_name = old.get("name", "") == name.strip()
    new = {"name": name.strip(), "ra_deg": ra_deg, "dec_deg": dec_deg, "source": source,
           # a typed position keeps the magnitude SIMBAD gave for the same name
           "vmag": res.get("vmag") if res else (old.get("vmag") if same_name else None),
           "otype": res.get("otype", "") if res else (old.get("otype", "") if same_name else ""),
           "simbad_id": res.get("main_id", "") if res else (old.get("simbad_id", "") if same_name else ""),
           "sp_type": res.get("sp_type", "") if res else (old.get("sp_type", "") if same_name else "")}
    changed = not _same_target(old, new)
    p["target"] = new
    if changed and p["reference"]:
        p["reference"] = {}
    return changed


def set_reference(session: Session, m: ReferenceStarMatch) -> None:
    s = m.star
    plan_state(session)["reference"] = {"name": s.name, "ra": s.ra, "dec": s.dec, "v": s.v_mag, "bv": s.b_v,
                                        "sp_type": s.sp_type, "distance_deg": m.distance_deg, "chosen": True,
                                        **grade_fields(s.name, s)}


# ---- the observing plan --------------------------------------------------------------------------------
def plan_rows(session: Session) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """(target, reference or None) as {name, vmag, ra, dec} with RA 'hh mm ss.s' and Dec '±dd mm ss'."""
    p = plan_state(session)
    t, r = p["target"], p["reference"]

    def pos(ra: float | None, dec: float | None) -> dict[str, str | None]:
        return {"ra": None if ra is None else format_ra(ra), "dec": None if dec is None else format_dec(dec)}

    target = {"name": t.get("name", ""), "vmag": t.get("vmag"), **pos(t.get("ra_deg"), t.get("dec_deg")),
              "source": t.get("source") if t.get("ra_deg") is not None else None}
    ref = ({"name": r["name"], "vmag": r.get("v"), **pos(r.get("ra"), r.get("dec")), "grade": r.get("grade", ""),
            "reason_text": r.get("reason_text", "")} if r.get("chosen") else None)
    return target, ref


def plan_json(session: Session, conditions: dict[str, Any] | None = None) -> str:
    """conditions (Plan › Observing conditions): {"date", "window", "visible"}; never the observing location."""
    target, ref = plan_rows(session)
    out: dict[str, Any] = {"target": target, "reference": ref}
    if conditions:
        out |= {k: conditions.get(k) for k in ("date", "window", "visible")}
    return json.dumps(out, indent=2, ensure_ascii=False)


def coords_text(session: Session, which: str, part: str = "both") -> str | None:
    """Bare J2000 values for pasting: 'ra', 'dec' or both as '20 17 47.2  +38 01 59'. None when unknown."""
    target, ref = plan_rows(session)
    row = target if which == "target" else ref
    if not row or row.get("ra") is None:
        return None
    return {"ra": row["ra"], "dec": row["dec"]}.get(part, f"{row['ra']}  {row['dec']}")


def plan_text(session: Session, conditions: dict[str, Any] | None = None) -> str:
    """Two columns, target left and reference right, for pasting into notes or an e-mail; then Date, Window and
    Visible from the Observing conditions card (never the observing location)."""
    target, ref = plan_rows(session)

    def mag(v: Any) -> str:
        return f"{v:.2f}" if isinstance(v, (int, float)) else "-"

    left = [("Target", target["name"] or "-"), ("V", mag(target["vmag"])), ("RA (J2000)", target["ra"] or "-"),
            ("Dec (J2000)", target["dec"] or "-")]
    right = ([("Reference", ref["name"]), ("V", mag(ref["vmag"])), ("RA (J2000)", ref["ra"]),
              ("Dec (J2000)", ref["dec"])] if ref else [("Reference", "-"), ("", ""), ("", ""), ("", "")])
    if ref and ref.get("grade"):
        left.append(("", ""))
        right.append(("Grade", ref["grade"] + (f" ({ref['reason_text']})" if ref.get("reason_text") else "")))
    lw = max(len(f"{k}: {v}") for k, v in left) + 4
    lines = []
    for (lk, lv), (rk, rv) in zip(left, right, strict=True):
        a = f"{lk + ':':<12} {lv}" if lk else ""
        b = f"{rk + ':':<12} {rv}" if rk else ""
        lines.append(f"{a:<{lw + 6}}{b}".rstrip())
    if conditions:
        w = conditions.get("window") or {}
        window = f"{w.get('from', '-')}–{w.get('to', '-')} local, min altitude {w.get('min_alt', '-')}°" if w else "-"
        lines += ["", f"{'Date:':<12} {conditions.get('date') or '-'}", f"{'Window:':<12} {window}",
                  f"{'Visible:':<12} {conditions.get('visible') or '-'}"]
    return "\n".join(lines)
