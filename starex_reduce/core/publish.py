"""Promote to Published: copy a saved AVSpec file from the pair folder into
Published\\<year>\\<night>_<Target>_v<N>\\ with submission.json, and record it in the pair logbook. No Qt.

A copy, never a move: the pair folder stays complete. A version folder is never overwritten (the next free N is
used). The logbook is not copied. The AVSpec file was written without the SX* block (export strips it).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from starex_reduce import APP_NAME, __version__

from . import logbook
from .cache import file_sha256
from .datalocs import to_stored
from .naming import published_folder
from .settings import write_json_atomic

SUBMISSION = "submission.json"


def next_version(published: Path, year: int | str, night: str, target: str, doc: dict[str, Any] | None) -> int:
    """1, or one more than both the logbook's highest version and any version folder already on disk."""
    used = [int(p.get("version") or 0) for p in (doc or {}).get("published") or []]
    n = max(used, default=0) + 1
    while (published / published_folder(year, night, target, n)).exists():
        n += 1
    return n


def promote(pair: Path, avspec: Path, published: Path, data_root: Path, avspec_reference: str = "") -> dict[str, Any]:
    """Copy `avspec` into the next version folder with submission.json; add the entry to logbook published[].
    Returns the entry (version, date_utc, file, sha256, avspec_reference)."""
    doc = logbook.load(pair) or {}
    night = str(doc.get("night") or "")
    target = str((doc.get("target") or {}).get("name") or "Target")
    year = night[:4] if night else logbook.now_utc()[:4]
    version = next_version(published, year, night, target, doc)
    dest = published / published_folder(year, night, target, version)
    dest.mkdir(parents=True, exist_ok=False)  # never into an existing version folder
    out = dest / avspec.name
    shutil.copy2(avspec, out)
    sha = file_sha256(out)
    entry = {"version": version, "date_utc": logbook.now_utc(), "file": to_stored(out, data_root), "sha256": sha,
             "avspec_reference": avspec_reference}
    write_json_atomic(dest / SUBMISSION, {
        "pair_id": doc.get("pair_id") or pair.name, "night": night, "target": target,
        "reference": (doc.get("reference") or {}).get("name") or "", "version": version,
        "date_utc": entry["date_utc"], "file": avspec.name, "sha256": sha,
        "source_sha256": file_sha256(avspec), "avspec_reference": avspec_reference,
        "app": APP_NAME, "app_version": __version__})
    if doc:
        doc.setdefault("published", []).append(entry)
        doc.setdefault("status", {})["published"] = entry["date_utc"]
        doc.setdefault("steps", {})["publish"] = {"state": logbook.DONE, "finished_utc": entry["date_utc"],
                                                 "summary": f"v{version} → {entry['file']}",
                                                 "metrics": {"version": version}}
        logbook.save(pair, doc)
    return entry


def read_submission(folder: Path) -> dict[str, Any] | None:
    try:
        return json.loads((folder / SUBMISSION).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
