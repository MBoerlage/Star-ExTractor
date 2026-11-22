"""What each finished reduce step writes into the pair folder: its entry in logbook.json `processing` and, for
Wavelength and Response, wavelength.json / response.json next to the 1D spectra. No Qt, no pixels."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from . import logbook
from .pipeline import IMAGES, StageRun
from .session import Session
from .settings import write_json_atomic

MAX_LIST = 64  # longer arrays (profiles, curves) stay out of the JSON files
STEP_FILES = {"wavelength": "wavelength.json", "response": "response.json"}


def json_safe(v: Any, depth: int = 0) -> Any:
    """Numbers, strings, short lists and dicts only: numpy scalars become floats, long arrays are dropped."""
    if depth > 6:
        return None
    if isinstance(v, (str, bool)) or v is None:
        return v
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        f = float(v)
        return round(f, 8) if math.isfinite(f) else None
    if isinstance(v, np.ndarray):
        return [json_safe(x, depth + 1) for x in v.tolist()] if v.size <= MAX_LIST else None
    if isinstance(v, dict):
        out = {str(k): json_safe(x, depth + 1) for k, x in v.items()}
        return {k: x for k, x in out.items() if x is not None}
    if isinstance(v, (list, tuple)):
        if len(v) > MAX_LIST:
            return None
        return [json_safe(x, depth + 1) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        return json_safe({k: getattr(v, k) for k in v.__dataclass_fields__}, depth + 1)
    return str(v)


def step_params(session: Session, sid: str) -> dict[str, Any]:
    if sid == "response":
        return json_safe({k: v for k, v in session.params[sid].items() if not str(k).startswith("_")})
    return json_safe({img: session.image_params(sid, img) for img in IMAGES})


def step_metrics(sid: str, run: StageRun) -> dict[str, Any]:
    """A few numbers per step for the Library's progress report (logbook steps[sid].metrics)."""
    d = run.data or {}
    t = d.get("Target") if isinstance(d.get("Target"), dict) else {}
    if sid == "geometry":
        return json_safe({"tilt_deg": t.get("tilt"), "slant_deg": t.get("slant"), "hot_pixels": t.get("hot"),
                          "tilt_source": t.get("source")})
    if sid == "extract":
        spec = ((t.get("bands") or {}).get("spectrum")) or None
        return json_safe({"rows": spec, "rows_source": t.get("how"), "mode": t.get("mode")})
    if sid == "wavelength":
        m = {"fit": d.get("mode"), "rms_A": d.get("rms"), "nlines": len(d.get("points") or []), "R": d.get("R")}
        cal = ((d.get("target") or {}).get("cal")) or {}
        if cal.get("metrics"):  # the target calibration (docs/WAVELENGTH.md); rms_A above stays the reference's
            m.update({k: v for k, v in cal["metrics"].items() if k != "rms_A"})
            m["target_rms_A"] = cal["metrics"].get("rms_A")
        return json_safe(m)
    if sid == "response":
        return json_safe({"library": d.get("label") or run.ribbon.split(" · ")[0]})
    return {}


def record_step(session: Session, sid: str, run: StageRun) -> None:
    """Merge {params, results, summary, utc} for `sid` into the pair logbook (when the folder has one), record
    steps[sid] (state done, summary, metrics) and write the step's JSON file for Wavelength and Response."""
    folder = Path(session.folder)  # type: ignore[arg-type]
    entry = {"utc": logbook.now_utc(), "summary": run.ribbon or run.summary, "params": step_params(session, sid),
             "results": json_safe(run.data or {}), "outputs": dict(run.outputs)}
    if sid in STEP_FILES:
        write_json_atomic(folder / STEP_FILES[sid], {"pair_id": folder.name, **entry})
    step = {"state": logbook.DONE, "finished_utc": entry["utc"], "summary": entry["summary"],
            "metrics": step_metrics(sid, run)}
    patch: dict[str, Any] = {"processing": {sid: entry}, "steps": {sid: step}}
    if sid == "response":
        patch["status"] = {"reduced": entry["utc"]}
    logbook.update(folder, patch)


def sync_stale(session: Session) -> list[str]:
    """Steps the logbook has as done whose results the session now has as out of date (an upstream change):
    marked stale. Returns them."""
    from .session import Status

    if session.folder is None:
        return []
    doc = logbook.load(session.folder)
    if doc is None:
        return []
    names = [sid for sid in ("geometry", "extract", "wavelength", "response")
             if ((doc.get("steps") or {}).get(sid) or {}).get("state") == logbook.DONE
             and session.status[sid].status != Status.VALID]
    if names:
        logbook.mark_stale(session.folder, names)
    return names
