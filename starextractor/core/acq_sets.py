"""acquisition_sets.json: what the Acquisition tab collected per set (docs/ACQUISITION_TAB.md §7). Reduce › Select
reads the entry whose stack_file is the stack it is given (names, position, rows) when the stack header lacks them.

Plain, human-editable JSON, version 1. Paths are relative to `folder`; `qc` records size and mtime so a changed
or missing file can be detected. Reading tolerates missing optional keys and unknown extra keys. No Qt.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from starextractor import APP_NAME

from .settings import write_json_atomic
from .subs import SubRecord

FILE_NAME = "acquisition_sets.json"
VERSION = 1
ROLES = ("target", "reference")


def _rel(path: str, folder: Path) -> str:
    try:
        return Path(os.path.relpath(path, folder)).as_posix()
    except ValueError:  # another drive
        return Path(path).as_posix()


def make_sets(records: list[SubRecord], names: dict[str, str], name_source: dict[str, str],
              night: str = "", stacked: dict[str, dict[str, Any]] | None = None,
              idents: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The sets document from the Acquisition state. Roles come only from each record's `role`."""
    paths = [r.path for r in records]
    try:
        folder = Path(os.path.commonpath([str(Path(p).parent) for p in paths])) if paths else Path()
    except ValueError:  # the two sets on different drives
        folder = Path(paths[0]).parent
    if not night and records and records[0].time:
        from .session import night_of

        try:
            night = night_of(records[0].time)  # local noon to noon (core.naming.observing_night)
        except ValueError:
            night = records[0].time[:10]
    doc: dict[str, Any] = {"version": VERSION, "app": APP_NAME, "night": night, "folder": str(folder)}
    for role in ROLES:
        mine = [r for r in records if r.role == role]
        good = [r for r in mine if r.included]
        exp = Counter(r.exptime for r in good).most_common(1)
        gain = Counter(r.gain for r in good).most_common(1)
        doc[role] = {
            "name": names.get(role, ""), "name_source": name_source.get(role, "typed"),
            "good": [_rel(r.path, folder) for r in good],
            "excluded": [{"file": _rel(r.path, folder),
                          "reason": ", ".join(r.flags) if r.flags else "excluded by hand"}
                         for r in mine if not r.included],
            "exposure_s": exp[0][0] if exp else None, "gain": gain[0][0] if gain else None,
        }
        ident = (idents or {}).get(role) or {}
        for key in ("ra", "dec", "sp_type", "notes", "lobe_mode"):  # optional: position, spectral type, notes,
            # how the rows were found (auto | single | all)
            if ident.get(key):
                doc[role][key] = str(ident[key])
        st = (stacked or {}).get(role) or {}
        if st.get("file"):  # optional: the stack Stack selected wrote, and the rows it was taken with
            doc[role]["stack_file"] = str(st["file"])
            if st.get("rows"):
                doc[role]["rows"] = st["rows"]
    doc["qc"] = {_rel(r.path, folder): {"peak_fill": round(r.peak_fill, 3), "snr": round(r.continuum_snr, 1),
                                        "size": r.size, "mtime": round(r.mtime)} for r in records}
    return doc


def save_sets(path: Path, doc: dict[str, Any]) -> Path:
    write_json_atomic(path, doc)
    return path


def _side(d: Any) -> dict[str, Any]:
    d = d if isinstance(d, dict) else {}
    excluded = []
    for e in d.get("excluded") or []:
        if isinstance(e, dict) and e.get("file"):
            excluded.append({"file": str(e["file"]), "reason": str(e.get("reason", ""))})
        elif isinstance(e, str):
            excluded.append({"file": e, "reason": ""})
    return {"name": str(d.get("name") or ""), "name_source": str(d.get("name_source") or "typed"),
            "good": [str(g) for g in d.get("good") or [] if g], "excluded": excluded,
            "exposure_s": d.get("exposure_s"), "gain": d.get("gain"),
            "stack_file": str(d.get("stack_file") or ""),
            "ra": str(d.get("ra") or ""), "dec": str(d.get("dec") or ""), "sp_type": str(d.get("sp_type") or ""),
            "notes": str(d.get("notes") or ""), "lobe_mode": str(d.get("lobe_mode") or "auto"),
            "rows": d.get("rows") if isinstance(d.get("rows"), dict) else None}


def normalise(raw: Any, default_folder: str = "", source: str = "") -> dict[str, Any]:
    """Normalised document; unknown keys are ignored, missing optional keys get defaults."""
    if not isinstance(raw, dict):
        raise ValueError("not an acquisition sets file")
    folder = raw.get("folder") or default_folder
    return {"version": raw.get("version", VERSION), "night": str(raw.get("night") or ""), "folder": str(folder),
            "target": _side(raw.get("target")), "reference": _side(raw.get("reference")),
            "qc": raw.get("qc") if isinstance(raw.get("qc"), dict) else {}, "source": source}


def load_sets(path: Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalise(raw, str(Path(path).parent), str(path))


def absolute(doc: dict[str, Any], rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else Path(doc["folder"]) / p


def check_files(doc: dict[str, Any]) -> dict[str, list[str]]:
    """Files listed in the sets that are missing, or whose size/mtime differ from the recorded qc."""
    missing, changed = [], []
    for role in ROLES:
        for rel in doc[role]["good"] + [e["file"] for e in doc[role]["excluded"]]:
            p = absolute(doc, rel)
            if not p.exists():
                missing.append(rel)
                continue
            q = doc["qc"].get(rel) or {}
            st = p.stat()
            if (q.get("size") is not None and q["size"] != st.st_size) or (
                    q.get("mtime") is not None and abs(float(q["mtime"]) - st.st_mtime) > 2):
                changed.append(rel)
    return {"missing": missing, "changed": changed}


def summary(doc: dict[str, Any]) -> str:
    t, r = doc["target"], doc["reference"]
    return (f"Target {t['name'] or '(unnamed)'}, {len(t['good'])} good subs. "
            f"Reference {r['name'] or '(unnamed)'}, {len(r['good'])} good subs.")
