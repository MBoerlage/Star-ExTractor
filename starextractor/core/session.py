"""Session model: inputs, per-stage params and status, the invalidation chain, the log.

A session lives in a folder with `session.json` and `log.txt`. Opening a session never reads
pixels: stage status is restored from JSON and re-checked against cheap input fingerprints.

Keys are per image (Target, Reference): a stage is valid when every image's stored key equals
the key computed from the current inputs and params. Each key includes the key of the same
image in the previous stage (Response uses both), so any change upstream invalidates every later
stage automatically and never an earlier one.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .cache import file_fingerprint, stage_key
from .pipeline import IMAGES, STAGE_IDS
from .settings import write_json_atomic

SESSION_FILE = "session.json"
LOG_FILE = "log.txt"
SCHEMA = 2
SIDES = {"Target": "target", "Reference": "reference"}


def night_of(t: Any = None) -> str:
    """'YYYY-MM-DD' of the observing night: core.naming.observing_night (local noon to noon)."""
    from .naming import observing_night

    return observing_night(t)


class Status(StrEnum):
    VALID = "valid"
    STALE = "stale"  # not run, or invalidated by an earlier change
    MISSING = "missing"  # inputs not available
    ERROR = "error"


@dataclass
class StageStatus:
    status: Status = Status.STALE
    key: str = ""  # combined key of the last complete run ("" = never run)
    image_keys: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)  # image -> file in the session folder
    summary: str = ""
    message: str = ""
    time: str = ""

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> StageStatus:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        known["status"] = Status(known.get("status", "stale"))
        return cls(**known)


def _default_side() -> dict[str, Any]:
    """Name, position and SIMBAD result of one side, set by Reduce › Select' Load pair. Sessions from before
    the step became a pair selector also carry "folder", "lights" and "stack"; they are read, never needed."""
    return {"name": "", "coords": {"source": "simbad", "ra": "", "dec": ""}, "simbad": {}}


def _default_cal() -> dict[str, Any]:
    return {"choice": {}, "accepted": [], "match_pedestal": None, "gain_scale": True,
            "flat": False, "resolved": []}


class Session:
    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder
        self.inputs: dict[str, Any] = {"target": _default_side(), "reference": _default_side()}
        self.calibration: dict[str, Any] = {"target": _default_cal(), "reference": _default_cal()}
        self.params: dict[str, dict[str, Any]] = {s: {} for s in STAGE_IDS}
        self.status: dict[str, StageStatus] = {s: StageStatus() for s in STAGE_IDS}
        self.log_lines: list[str] = []
        self._log_listeners: list[Callable[[str], None]] = []
        self._status_listeners: list[Callable[[], None]] = []

    # ---- listeners -------------------------------------------------------------------------
    def on_log(self, fn: Callable[[str], None]) -> None:
        self._log_listeners.append(fn)

    def on_status(self, fn: Callable[[], None]) -> None:
        self._status_listeners.append(fn)

    def _status_changed(self) -> None:
        for fn in self._status_listeners:
            fn()

    # ---- log -------------------------------------------------------------------------------
    def log(self, message: str) -> str:
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        self.log_lines.append(line)
        if self.folder is not None:
            try:
                with open(self.folder / LOG_FILE, "a", encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%Y-%m-%d')} {line}\n")
            except OSError:
                pass  # logging must never break processing
        for fn in self._log_listeners:
            fn(line)
        return line

    # ---- inputs ----------------------------------------------------------------------------
    def used_lights(self, side: str) -> list[str]:
        return [f["path"] for f in self.inputs[side].get("lights") or [] if f.get("used", True)]

    def side_fingerprints(self, side: str) -> list[str]:
        files = sorted(self.used_lights(side)) + sorted(self.calibration[side].get("resolved", []))
        return [file_fingerprint(p) for p in files]

    # ---- keys and status -------------------------------------------------------------------
    def compute_keys(self) -> dict[str, dict[str, str]]:
        """Expected key of every (stage, image) given current inputs and params."""
        keys: dict[str, dict[str, str]] = {}
        pairs = self.params["files"].get("pairs")
        pair = pairs[0] if isinstance(pairs, list) and pairs and isinstance(pairs[0], dict) else None
        for sid in STAGE_IDS:
            keys[sid] = {}
            for img in IMAGES:
                side = SIDES[img]
                if sid == "files" and pair is not None:
                    # the loaded stack file, its hot-pixel dark and the rows handed to 1D Extract
                    entry = pair.get(side) or {}
                    params = {"rows": entry.get("rows") or {}}
                    up = ([file_fingerprint(self.folder / entry["file"])] if self.folder and entry.get("file")
                          else []) + [file_fingerprint(p) for p in sorted(self.calibration[side].get("resolved", []))]
                elif sid == "files":  # sessions from before the step became a pair selector (stacked from lights)
                    params = {**self.params["files"], "stack": self.inputs[side].get("stack", {}),
                              "cal": {k: v for k, v in self.calibration[side].items() if k != "resolved"}}
                    up = self.side_fingerprints(side)
                elif sid == "response":
                    params = self.params[sid]
                    prev = keys[STAGE_IDS[STAGE_IDS.index(sid) - 1]]
                    up = [prev[i] for i in IMAGES]
                else:
                    params = self.image_params(sid, img)
                    prev = keys[STAGE_IDS[STAGE_IDS.index(sid) - 1]]
                    # the wavelength solution comes from the reference star and is carried to the target
                    up = [prev[img], prev["Reference"]] if sid == "wavelength" else [prev[img]]
                keys[sid][img] = stage_key(f"{sid}/{img}", params, up)
        return keys

    def image_params(self, stage_id: str, image: str) -> dict[str, Any]:
        """Stage params seen by one image: shared params plus `per_image[image]`."""
        p = self.params[stage_id]
        shared = {k: v for k, v in p.items() if k != "per_image"}
        return {**shared, "image": p.get("per_image", {}).get(image, {})}

    def set_image_params(self, stage_id: str, image: str, values: dict[str, Any]) -> list[str]:
        per = dict(self.params[stage_id].get("per_image", {}))
        merged = {**per.get(image, {}), **values}
        if per.get(image) == merged:
            return []
        per[image] = merged
        self.params[stage_id] = {**self.params[stage_id], "per_image": per}
        return self.changed()

    @staticmethod
    def combined(image_keys: dict[str, str]) -> str:
        return stage_key("combined", {}, [image_keys.get(i, "") for i in IMAGES])

    def image_done(self, stage_id: str, image: str, keys: dict[str, dict[str, str]] | None = None) -> bool:
        keys = keys or self.compute_keys()
        st = self.status[stage_id]
        if st.image_keys.get(image) != keys[stage_id][image]:
            return False
        out = st.outputs.get(image)
        return self.folder is None or not out or (self.folder / out).exists()

    def refresh_status(self) -> list[str]:
        """Re-derive valid/stale from keys. Returns stages that went from valid to stale."""
        keys = self.compute_keys()
        dropped = []
        for sid in STAGE_IDS:
            st = self.status[sid]
            if st.status in (Status.MISSING, Status.ERROR):
                continue
            valid = all(self.image_done(sid, img, keys) for img in IMAGES)
            new = Status.VALID if valid else Status.STALE
            if st.status == Status.VALID and new == Status.STALE:
                dropped.append(sid)
            st.status = new
        return dropped

    def invalidate_from(self, stage_id: str) -> list[str]:
        """Forget results of `stage_id` and every later stage. Earlier stages are never touched."""
        changed = []
        for sid in STAGE_IDS[STAGE_IDS.index(stage_id):]:
            st = self.status[sid]
            if st.status != Status.STALE:
                changed.append(sid)
            st.status = Status.STALE
            st.image_keys = {}
        if changed:
            self._status_changed()
        return changed

    def set_params(self, stage_id: str, params: dict[str, Any]) -> list[str]:
        merged = {**self.params[stage_id], **params}
        if merged == self.params[stage_id]:
            return []
        self.params[stage_id] = merged
        return self.changed()

    def changed(self) -> list[str]:
        """Call after editing inputs/params in place: re-derive status and notify."""
        dropped = self.refresh_status()
        self._status_changed()
        return dropped

    def upstream_valid(self, stage_id: str, image: str | None = None) -> bool:
        keys = self.compute_keys()
        idx = STAGE_IDS.index(stage_id)
        images = IMAGES if image is None or stage_id == "response" else (image,)
        return all(self.image_done(s, i, keys) for s in STAGE_IDS[:idx] for i in images)

    def mark_image_done(self, stage_id: str, image: str, output: str, summary: str) -> None:
        keys = self.compute_keys()
        st = self.status[stage_id]
        st.image_keys[image] = keys[stage_id][image]
        if output:
            st.outputs[image] = output
        st.summary, st.message = summary, ""
        st.time = time.strftime("%Y-%m-%dT%H:%M:%S")
        if st.status in (Status.MISSING, Status.ERROR):
            st.status = Status.STALE
        if all(self.image_done(stage_id, i, keys) for i in IMAGES):
            st.key = self.combined(st.image_keys)
        self.refresh_status()
        self._status_changed()

    def mark_done(self, stage_id: str, outputs: dict[str, str], summary: str) -> None:
        for img in IMAGES:
            self.mark_image_done(stage_id, img, outputs.get(img, ""), summary)

    def mark_error(self, stage_id: str, message: str) -> None:
        st = self.status[stage_id]
        st.status, st.message = Status.ERROR, message
        self._status_changed()

    def mark_missing(self, stage_id: str, message: str) -> None:
        st = self.status[stage_id]
        st.status, st.message = Status.MISSING, message
        self._status_changed()

    def clear_problem(self, stage_id: str) -> None:
        st = self.status[stage_id]
        if st.status in (Status.MISSING, Status.ERROR):
            st.status, st.message = Status.STALE, ""
            self.refresh_status()
            self._status_changed()

    # ---- persistence -----------------------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "inputs": self.inputs,
            "calibration": self.calibration,
            "params": self.params,
            "status": {k: v.to_json() for k, v in self.status.items()},
        }

    def save(self) -> None:
        if self.folder is not None:
            write_json_atomic(self.folder / SESSION_FILE, self.to_json())

    @classmethod
    def create(cls, folder: Path) -> Session:
        folder.mkdir(parents=True, exist_ok=True)
        s = cls(folder)
        s.save()
        return s

    @classmethod
    def load(cls, folder: Path) -> Session:
        data = json.loads((folder / SESSION_FILE).read_text(encoding="utf-8"))
        s = cls(folder)
        for side in ("target", "reference"):
            s.inputs[side].update(data.get("inputs", {}).get(side, {}))
            cal = data.get("calibration", {}).get(side, {})
            if isinstance(cal, dict):
                s.calibration[side].update(cal)
        for key, val in data.get("params", {}).items():  # stage params plus extras such as "export"
            if isinstance(val, dict):
                s.params[key] = dict(val)
        for sid in STAGE_IDS:
            if sid in data.get("status", {}):
                s.status[sid] = StageStatus.from_json(data["status"][sid])
        log = folder / LOG_FILE
        if log.exists():
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
            s.log_lines = [ln.split(" ", 1)[1] if " " in ln else ln for ln in lines]
        s.refresh_status()
        return s

    @staticmethod
    def is_session(folder: Path) -> bool:
        return (folder / SESSION_FILE).exists()
