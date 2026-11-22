"""User settings in %APPDATA%\\StarExtractor\\settings.json (machine-level: settings, window state, caches).

`STAREX_REDUCE_HOME` overrides the folder (tests use it so they never touch the real profile). The data itself
lives under the data root (core/datalocs.py). The first start after the rename copies the files of the old
%APPDATA%\\starex_reduce folder (never moves or deletes them); the old master library folder becomes the Masters
location once.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .datalocs import DataPaths

DEFAULTS: dict[str, Any] = {
    "recent_sessions": [],  # pair folders (and sessions from before the data root), newest first
    "data_root": "",  # core/datalocs.py; empty = not chosen yet (first-run dialog)
    "data_paths": {"stacks": "", "masters": "", "processed": "", "published": ""},  # empty = <root>\<Name>
    "cache_cap_mb": 1536,
    "use_opengl": False,
    "unit": "A",
    "tolerances": {"gain_rel": 0.20, "offset_rel": 0.50, "temp_c": 3.0, "enabled": True},
    "last_folder": "",
    "vsx_api_url": "",  # VSX URL template with {name}; empty = core.vsx.DEFAULT_URL (docs/FORMATS.md)
    "window": {},  # saved window geometry
    "library": {},  # Library tab: last selected pair (key), filter chip and search text
    "location": "",  # the last rail step (or "plan" / "library"); "files" from before is read as "select"
    "ui": {"palette": "default", "splash": True},  # palette: default | night | red (docs/THEMES.md), set by the
    # top-row switch; splash: the start-up splash (ui/splash.py reads both straight from the file, no import)
    "plan": {},  # Plan › Observing conditions: from/to (HH:mm), min_alt, location (planning only, never in data files)
    "celebrate_saves": None,  # Export → Publish confetti; None = on unless the OS asks for reduced motion
    "acq": {  # Acquisition tab (docs/ACQUISITION_TAB.md)
        "folder": "", "interval_s": 2.0, "process_existing": False, "linearity_frac": 0.8,
        "safety_frac": 0.95, "snr_target": 100.0, "snr_sigma": 2.0,
    },
}
MAX_RECENT = 10


APP_FOLDER = "StarExtractor"
LEGACY_FOLDER = "starex_reduce"  # before 2026-11-18


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))


def legacy_dir() -> Path | None:
    """The settings folder used before the rename (None under STAREX_REDUCE_HOME)."""
    return None if os.environ.get("STAREX_REDUCE_HOME") else _appdata() / LEGACY_FOLDER


def app_dir() -> Path:
    override = os.environ.get("STAREX_REDUCE_HOME")
    if override:
        base = Path(override)
    else:
        base = _appdata() / APP_FOLDER
        old = _appdata() / LEGACY_FOLDER
        if not base.exists() and old.is_dir():  # first start after the rename: copy, never move
            base.mkdir(parents=True, exist_ok=True)
            for item in old.iterdir():
                try:
                    if item.is_file():
                        shutil.copy2(item, base / item.name)
                    elif item.is_dir() and item.name == "responses":
                        shutil.copytree(item, base / item.name, dirs_exist_ok=True)
                except OSError:
                    pass  # a file that cannot be copied keeps its default
    base.mkdir(parents=True, exist_ok=True)
    return base


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class Settings:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_dir() / "settings.json"
        self.data: dict[str, Any] = dict(DEFAULTS)
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass  # a corrupt settings file must never stop the app; defaults apply
        self._migrate()

    def _migrate(self) -> None:
        """Read the old master library setting once: it becomes the Masters location when that is unset, then the
        old key is dropped. The files stay where they are."""
        old = self.data.pop("masters_dir", None)
        legacy = legacy_dir()
        if not old and legacy is not None and (legacy / "masters" / "index.json").exists() \
                and not self.data.get("data_root"):
            old = str(legacy / "masters")  # the old default library folder
        paths = dict(DEFAULTS["data_paths"]) | dict(self.data.get("data_paths") or {})
        if old and not paths.get("masters"):
            paths["masters"] = str(old)
            self.data["data_paths"] = paths
            if self.path.exists():
                self.save()
    def __getitem__(self, key: str) -> Any:
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def paths(self) -> DataPaths:
        from .datalocs import data_paths

        return data_paths(self)

    def masters_dir(self) -> Path:
        """The Masters location (the master library folder)."""
        return self.paths().masters

    def add_recent(self, folder: Path) -> None:
        items = [str(folder)] + [p for p in self["recent_sessions"] if p != str(folder)]
        self.data["recent_sessions"] = items[:MAX_RECENT]

    def save(self) -> None:
        write_json_atomic(self.path, self.data)
