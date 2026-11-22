"""The pair logbook: Processed\\<pair folder>\\logbook.json, the single source of truth for one target + reference
pair (docs/DATA.md "Pair logbook"). No Qt.

Written atomically (temp file, then replace). Fields this program does not know are kept on every rewrite, so a
newer version's logbook survives an older program. Paths inside the data root are stored relative to it.
`derive_status` looks only at which files exist; `scan_pairs` reads only logbooks (never pixels) and is what the
Library tab lists.

Schema 2 (2026-11-19) adds `steps` (one record per reduce step: state done | stale | failed | skipped,
finished_utc, summary, metrics) and `qc` (the AVSpec test warnings with an acknowledged flag). A schema-1 logbook
is migrated on load (empty steps; their state is then derived from the files that exist).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .settings import write_json_atomic

FILE_NAME = "logbook.json"
SCHEMA_VERSION = 2
ONE_D = ("Target_X.fit", "Target_W.fit", "Target_R.fit")  # any of these = reduced
DONE, STALE, FAILED, SKIPPED, NONE = "done", "stale", "failed", "skipped", "none"
STEP_NAMES = ("stacked", "geometry", "extract", "wavelength", "response", "qc", "export", "publish")
STEP_TITLES = {"stacked": "Stacked", "geometry": "Geometry", "extract": "1D extract", "wavelength": "Wavelength",
               "response": "Response", "qc": "QC", "export": "Export", "publish": "Published"}
STEP_FILES = {"geometry": "work/Target_G.fit", "extract": "Target_X.fit", "wavelength": "Target_W.fit",
              "response": "Target_R.fit"}  # what shows a step ran, for logbooks without step records


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def empty(pair_id: str, night: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "pair_id": pair_id, "night": night, "created_utc": now_utc(),
            "updated_utc": now_utc(), "target": {}, "reference": {}, "camera": {}, "masters_used": [],
            "processing": {}, "steps": {}, "qc": {"warnings": []},
            "status": {"stacked": False, "reduced": False, "published": False}, "published": [], "notes": ""}


def migrate(doc: dict[str, Any]) -> dict[str, Any]:
    """Bring an older logbook to the current schema in memory (unknown fields kept)."""
    if int(doc.get("schema_version") or 1) < 2:
        doc.setdefault("steps", {})
        doc.setdefault("qc", {"warnings": []})
        doc["schema_version"] = 2
    return doc


def load(folder: Path | None) -> dict[str, Any] | None:
    """The logbook of a pair folder, or None (no file, or not readable)."""
    if folder is None:
        return None
    try:
        d = json.loads((Path(folder) / FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return migrate(d) if isinstance(d, dict) else None


def save(folder: Path, doc: dict[str, Any]) -> Path:
    """Write the whole document (unknown fields included), stamping updated_utc."""
    doc["schema_version"] = doc.get("schema_version") or SCHEMA_VERSION
    doc["updated_utc"] = now_utc()
    path = Path(folder) / FILE_NAME
    write_json_atomic(path, doc)
    return path


def merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Deep merge: dicts merge key by key, anything else replaces. Keys not in `patch` stay untouched."""
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
        else:
            base[k] = v
    return base


def update(folder: Path, patch: dict[str, Any]) -> dict[str, Any] | None:
    """Merge `patch` into the pair's logbook and write it. None when the folder has no logbook."""
    doc = load(folder)
    if doc is None:
        return None
    merge(doc, patch)
    save(folder, doc)
    return doc


def set_notes(folder: Path, notes: str) -> None:
    """Replace the free-text notes (Select › Pair notes, Library). The file is read again first and only `notes`
    changes, so a step that wrote in the meantime is never overwritten."""
    doc = load(folder)
    if doc is None or doc.get("notes", "") == notes:
        return
    doc["notes"] = notes
    save(folder, doc)


def set_step(folder: Path, name: str, state: str, summary: str = "", metrics: dict[str, Any] | None = None) -> None:
    """Record one reduce step (re-reads the file; only steps[name] changes)."""
    doc = load(folder)
    if doc is None:
        return
    doc.setdefault("steps", {})[name] = {"state": state, "finished_utc": now_utc(), "summary": summary,
                                         "metrics": metrics or {}}
    save(folder, doc)


def mark_stale(folder: Path, names: list[str]) -> bool:
    """Steps recorded as done whose results are out of date now (an upstream change). True when written."""
    doc = load(folder)
    if doc is None:
        return False
    steps = doc.setdefault("steps", {})
    changed = [n for n in names if (steps.get(n) or {}).get("state") == DONE]
    for n in changed:
        steps[n]["state"] = STALE
    if changed:
        save(folder, doc)
    return bool(changed)


def set_qc(folder: Path, warnings: list[str], acknowledged: bool = False) -> None:
    """The AVSpec test's warnings (replacing the last run's) with their acknowledged flag."""
    doc = load(folder)
    if doc is None:
        return
    doc["qc"] = {"utc": now_utc(), "warnings": [{"text": w, "acknowledged": bool(acknowledged)} for w in warnings]}
    steps = doc.setdefault("steps", {})
    steps["qc"] = {"state": DONE, "finished_utc": doc["qc"]["utc"],
                   "summary": (f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
                               + (", acknowledged" if warnings and acknowledged else "") if warnings
                               else "no warnings"),
                   "metrics": {"warnings": len(warnings), "acknowledged": bool(acknowledged)}}
    save(folder, doc)


def step_states(folder: Path, doc: dict[str, Any], data_root: Path) -> dict[str, dict[str, Any]]:
    """Every step's record: the logbook's when there is one, else derived from the files (recorded False,
    no timestamp or result)."""
    from .datalocs import resolve

    steps = doc.get("steps") or {}
    out: dict[str, dict[str, Any]] = {}
    for name in STEP_NAMES:
        rec = steps.get(name)
        if isinstance(rec, dict) and rec.get("state"):
            out[name] = {**rec, "recorded": True}
            continue
        if name == "stacked":
            files = [(doc.get(r) or {}).get("stack_file") for r in ("target", "reference")]
            done = all(f and resolve(f, data_root).exists() for f in files)
        elif name == "publish":
            done = bool(doc.get("published"))
        elif name == "export":
            done = bool((doc.get("processing") or {}).get("export")) or any((Path(folder) / "export").glob("*.fit"))
        elif name == "qc":
            done = bool((doc.get("qc") or {}).get("warnings"))
        else:
            done = (Path(folder) / STEP_FILES[name]).exists()
        out[name] = {"state": DONE if done else NONE, "finished_utc": None, "summary": "", "metrics": {},
                     "recorded": False}
    return out


def attention(doc: dict[str, Any], status: dict[str, bool], steps: dict[str, dict[str, Any]],
              data_root: Path) -> list[str]:
    """Why a pair needs a look (empty = fine): unacknowledged QC warnings, a failed or stale step in a reduced
    pair, a published record whose file is gone."""
    from .datalocs import resolve

    out = []
    warns = [w for w in (doc.get("qc") or {}).get("warnings") or [] if not w.get("acknowledged")]
    if warns:
        out.append(f"{len(warns)} unacknowledged QC warning{'s' if len(warns) != 1 else ''}")
    if status.get("reduced"):
        bad = [STEP_TITLES[n] for n, r in steps.items() if r.get("state") in (FAILED, STALE)]
        if bad:
            out.append("failed or stale: " + ", ".join(bad))
    gone = [p for p in doc.get("published") or [] if p.get("file") and not resolve(p["file"], data_root).exists()]
    if gone:
        out.append(f"published file missing (v{gone[-1].get('version')})")
    return out


def last_note(notes: str) -> str:
    lines = [ln.strip() for ln in str(notes or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def status_word(status: dict[str, bool], doc: dict[str, Any]) -> str:
    pub = doc.get("published") or []
    if status.get("published") and pub:
        return f"Published v{pub[-1].get('version')}"
    if status.get("reduced"):
        return "Reduced"
    return "Stacked" if status.get("stacked") else "Not stacked"


def derive_status(folder: Path, doc: dict[str, Any], data_root: Path) -> dict[str, bool]:
    """Pure: stacked = both stack files exist, reduced = a 1D result exists in the pair folder, published = a
    Published record exists."""
    from .datalocs import resolve

    def stack_ok(side: str) -> bool:
        f = (doc.get(side) or {}).get("stack_file")
        return bool(f) and resolve(f, data_root).exists()

    return {"stacked": stack_ok("target") and stack_ok("reference"),
            "reduced": any((Path(folder) / n).exists() for n in ONE_D),
            "published": bool(doc.get("published"))}


@dataclass
class PairSummary:
    pair_id: str
    folder: Path
    night: str
    target: str
    reference: str
    status: dict[str, bool]
    notes_preview: str
    notes: str = ""
    last_note: str = ""
    attention: tuple[str, ...] = ()
    steps: dict[str, dict[str, Any]] | None = None
    status_word: str = ""
    published: tuple[dict[str, Any], ...] = ()


def scan_pairs(data_root: Path, processed: Path | None = None) -> list[PairSummary]:
    """Every pair under Processed (default <root>/Processed), newest night first. Reads logbook.json files and
    file stats only."""
    base = Path(processed) if processed is not None else Path(data_root) / "Processed"
    out = []
    try:
        folders = [p for p in base.iterdir() if p.is_dir()]
    except OSError:
        return []
    for folder in folders:
        doc = load(folder)
        if doc is None:
            continue
        notes = str(doc.get("notes") or "")
        status = derive_status(folder, doc, data_root)
        steps = step_states(folder, doc, data_root)
        out.append(PairSummary(str(doc.get("pair_id") or folder.name), folder, str(doc.get("night") or ""),
                               str((doc.get("target") or {}).get("name") or ""),
                               str((doc.get("reference") or {}).get("name") or ""),
                               status, notes.strip().replace("\n", " ")[:80], notes, last_note(notes),
                               tuple(attention(doc, status, steps, data_root)), steps, status_word(status, doc),
                               tuple(doc.get("published") or ())))
    out.sort(key=lambda p: (p.night, p.pair_id), reverse=True)
    return out
