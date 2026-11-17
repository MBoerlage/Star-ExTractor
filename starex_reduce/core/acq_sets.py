"""acquisition_sets.json: the hand-off from the Acquisition tab to Reduce > Files (docs/ACQUISITION_TAB.md §7).

Plain, human-editable JSON, version 1. Paths are relative to `folder`; `qc` records size and mtime so a changed
or missing file can be detected. Reading tolerates missing optional keys and unknown extra keys. No Qt.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from starex_reduce import APP_NAME

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
              night: str = "") -> dict[str, Any]:
    """The sets document from the Acquisition state. Roles come only from each record's `role`."""
    paths = [r.path for r in records]
    folder = Path(os.path.commonpath([str(Path(p).parent) for p in paths])) if paths else Path()
    if not night and records and records[0].time:
        from .session import night_of

        try:
            night = night_of(records[0].time)  # local noon-to-noon, like the session folders
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
    doc["qc"] = {_rel(r.path, folder): {"peak_fill": round(r.peak_fill, 3), "snr": round(r.continuum_snr, 1),
                                        "size": r.size, "mtime": round(r.mtime)} for r in records}
    return doc


def save_sets(path: Path, doc: dict[str, Any]) -> Path:
    write_json_atomic(path, doc)
    return path


def save_next_to_images(doc: dict[str, Any]) -> Path | None:
    """Optional copy in the image folder. Never overwrites a file this app did not write."""
    target = Path(doc["folder"]) / FILE_NAME
    if target.exists():
        try:
            if json.loads(target.read_text(encoding="utf-8")).get("app") != APP_NAME:
                return None
        except (OSError, ValueError):
            return None
    return save_sets(target, doc)


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
            "exposure_s": d.get("exposure_s"), "gain": d.get("gain")}


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


def find_sets(session_folder: Path, light_folders: list[str], max_dirs: int = 200) -> Path | None:
    """A sets file for this session: in its folder, else in a sibling session folder (the night's session
    when you reduce on another day) whose sets name the same light folder, or the same night as this
    folder's name. Read only."""
    own = session_folder / FILE_NAME
    if own.exists():
        return own
    parent = session_folder.parent
    want = {os.path.normcase(str(Path(f))) for f in light_folders if f}
    by_night = None
    try:
        dirs = sorted((d for d in parent.iterdir() if d.is_dir() and d != session_folder), reverse=True)[:max_dirs]
    except OSError:
        return None
    for d in dirs:
        p = d / FILE_NAME
        if not p.exists():
            continue
        try:
            doc = load_sets(p)
        except (OSError, ValueError):
            continue
        folders = {os.path.normcase(str(absolute(doc, g).parent)) for r in ROLES for g in doc[r]["good"]}
        if want and folders & want:
            return p
        if by_night is None and doc["night"] and doc["night"] == session_folder.name:
            by_night = p
    return by_night


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


# ---- old data: scan a folder of subs (Reduce > Files "Scan folder…") --------------------------------------
def scan_folder(ctx: Any, folder: str) -> dict[str, Any]:
    """Runs in a worker: analyse every light sub under `folder` with core/subs.py, grouped by sub-folder,
    OBJECT and exposure. The groups are hints for the user, who assigns them to Target / Reference / Ignore.
    Calibration frames (bias, dark, flat) are skipped."""
    from .frames import LIGHT, UNKNOWN, iter_fits, load_frame
    from .subs import analyse_sub, detect_rows

    paths = sorted(iter_fits(folder, recursive=True))
    frames = [f for f in (load_frame(p) for p in paths)
              if not f.error and f.frame_type in (LIGHT, UNKNOWN) and _raw_sub(f.header)]
    groups: dict[tuple, list] = {}
    for f in frames:
        groups.setdefault((str(f.path.parent), str(f.h("OBJECT") or ""), f.exptime), []).append(f)
    out, done = [], 0
    for (parent, obj, exp), members in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0)):
        ctx.step(done / max(len(frames), 1))
        rows = detect_rows(members[0].path)
        recs = []
        for f in members:
            ctx.step(done / max(len(frames), 1))
            recs.append(analyse_sub(f.path, rows))
            done += 1
        out.append({"folder": parent, "object": obj, "exptime": exp, "records": recs})
    ctx.step(1.0)
    return {"folder": folder, "groups": out}


def _raw_sub(h: dict[str, Any]) -> bool:
    """A camera sub: 2-D 16-bit integer frame. Stacks, 1D spectra and processed float files are skipped."""
    return h.get("NAXIS") == 2 and h.get("BITPIX") == 16


def sets_from_groups(groups: list[dict[str, Any]], assignment: list[str], names: dict[str, str] | None = None,
                     sigma: float = 2.0) -> dict[str, Any]:
    """The sets document for the user's assignment ('target' | 'reference' | 'ignore' per group)."""
    from .subs import refresh_flags

    recs: list[SubRecord] = []
    for g, role in zip(groups, assignment, strict=True):
        if role not in ROLES:
            continue
        for r in g["records"]:
            r.role, r.included, r.user_override = role, True, False
            recs.append(r)
    for role in ROLES:
        refresh_flags([r for r in recs if r.role == role], sigma)
    names = names or {}
    return make_sets(recs, names, {r: "typed" for r in ROLES})
