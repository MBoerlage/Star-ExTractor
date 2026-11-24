"""What Acquisition stacked last, per role: the source of Reduce › Select's "Use Acquisition stacks" and of the
Acquisition hand-off strip. It is never guessed from folders or file names.

The record lives in the machine settings (settings["acq"]["last_stacks"]), not in a night's acquisition.json nor in
a Reduce session, so it survives Clear all, a new night and a restart; a role's entry is replaced only by a newer stack
of the same role. Paths are stored relative to the data root when inside it (core/datalocs.to_stored).

    {"target": {"path", "name", "subs", "sub_exposure_s", "written_utc", "observing_night"}, "reference": {...}}

The fallback for an empty record (`night_stacks`) reads what Acquisition itself recorded in the most recent night
folder of Stacks (acquisition.json "stacked", else acquisition_sets.json "stack_file"): the roles still come from
Acquisition's sets, never from file names."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .datalocs import resolve, to_stored

ROLES = ("target", "reference")
KEY = "last_stacks"
STATE_FILE = "acquisition.json"  # Acquisition's per-night state (ui/screens/acquisition.py)
SETS_FILE = "acquisition_sets.json"  # core/acq_sets.FILE_NAME
NIGHT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _acq(settings: Any) -> dict[str, Any]:
    d = settings["acq"]
    return dict(d) if isinstance(d, dict) else {}


def raw(settings: Any) -> dict[str, dict[str, Any]]:
    """The stored record ({role: entry}, paths as stored)."""
    rec = _acq(settings).get(KEY)
    return {r: dict(e) for r, e in rec.items() if r in ROLES and isinstance(e, dict)} if isinstance(rec, dict) else {}


def record(settings: Any, role: str, path: str | os.PathLike, *, name: str, subs: int,
           sub_exposure_s: float | None, written_utc: str, observing_night: str, save: bool = True) -> dict[str, Any]:
    """Acquisition wrote a stack of `role`: replace that role's entry (the other role is kept)."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    entry = {"path": to_stored(path, settings.paths().root), "name": name, "subs": int(subs),
             "sub_exposure_s": None if sub_exposure_s is None else float(sub_exposure_s),
             "written_utc": written_utc, "observing_night": observing_night}
    acq = _acq(settings)
    acq[KEY] = {**raw(settings), role: entry}
    settings["acq"] = acq
    if save:
        settings.save()
    return entry


def entries(settings: Any) -> dict[str, dict[str, Any]]:
    """The record with each path made absolute ("file": Path)."""
    root = settings.paths().root
    return {r: {**e, "file": resolve(str(e.get("path") or ""), root)} for r, e in raw(settings).items()
            if e.get("path")}


def problem(path: Path) -> str:
    """Why this file cannot be used as a stack ("" when it can): missing, unreadable, or no SX* stack block."""
    from .fitsio import read_header

    if not Path(path).is_file():
        return "the file no longer exists"
    try:
        h = read_header(path)
    except Exception as exc:  # noqa: BLE001 - any read error is reported, the box stays empty
        return f"cannot be read ({exc})"
    if "SXVER" not in h:
        return "not a stack from Acquisition (no SX* header)"
    return ""


def same_file(a: str | os.PathLike | None, b: str | os.PathLike | None) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return False


def nights(stacks: Path | None) -> list[Path]:
    """Night folders (YYYY-MM-DD) in Stacks, most recent first."""
    if stacks is None or not Path(stacks).is_dir():
        return []
    return sorted((d for d in Path(stacks).iterdir() if d.is_dir() and NIGHT_RE.match(d.name)),
                  key=lambda d: d.name, reverse=True)


def start_dir(stacks: Path | None) -> str:
    """Where a Browse dialog starts when its box is empty: the most recent night in Stacks, else Stacks."""
    ns = nights(stacks)
    if ns:
        return str(ns[0])
    return str(stacks) if stacks is not None and Path(stacks).is_dir() else ""


def _recorded_in(night: Path, root: Path) -> dict[str, Path]:
    """The stacks Acquisition recorded in one night folder, by role (acquisition.json, else the sets file)."""
    found: dict[str, Path] = {}
    try:
        st = json.loads((night / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    for role, e in ((st.get("stacked") or {}) if isinstance(st, dict) else {}).items():
        if role in ROLES and isinstance(e, dict) and e.get("file"):
            found[role] = resolve(str(e["file"]), root)
    try:
        doc = json.loads((night / SETS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    for role in ROLES:
        sf = ((doc.get(role) if isinstance(doc, dict) else None) or {}).get("stack_file")
        if role not in found and sf:
            cand = resolve(str(sf), root)
            found[role] = cand if cand.exists() else night / Path(str(sf)).name
    return {r: p for r, p in found.items() if not problem(p)}


def night_stacks(stacks: Path | None, root: Path) -> tuple[dict[str, Path], str]:
    """Fallback when nothing is recorded: the most recent night in Stacks with a stack Acquisition recorded there
    ({role: path}, night). ({}, "") when there is none. Never scans by file name."""
    for night in nights(stacks):
        found = _recorded_in(night, root)
        if found:
            return found, night.name
    return {}, ""
