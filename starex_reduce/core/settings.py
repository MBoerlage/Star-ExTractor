"""User settings in %APPDATA%\\starex_reduce\\settings.json.

`STAREX_REDUCE_HOME` overrides the folder (tests use it so they never touch the real profile).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "recent_sessions": [],
    "masters_dir": "",
    "cache_cap_mb": 1536,
    "use_opengl": False,
    "unit": "A",
    "tolerances": {"gain_rel": 0.20, "offset_rel": 0.50, "temp_c": 3.0, "enabled": True},
    "last_folder": "",
    "vsx_api_url": "",  # VSX URL template with {name}; empty = core.vsx.DEFAULT_URL (docs/FORMATS.md)
    "window": {},  # saved window geometry
    "celebrate_saves": None,  # Export → Publish confetti; None = on unless the OS asks for reduced motion
    "acq": {  # Acquisition tab (docs/ACQUISITION_TAB.md)
        "folder": "", "filter": "", "interval_s": 2.0, "process_existing": False, "linearity_frac": 0.8,
        "safety_frac": 0.95, "snr_target": 100.0, "snr_sigma": 2.0, "save_next_to_images": False,
    },
}
MAX_RECENT = 10


def app_dir() -> Path:
    override = os.environ.get("STAREX_REDUCE_HOME")
    if override:
        base = Path(override)
    else:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "starex_reduce"
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

    def __getitem__(self, key: str) -> Any:
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def masters_dir(self) -> Path:
        return Path(self["masters_dir"]) if self["masters_dir"] else app_dir() / "masters"

    def add_recent(self, folder: Path) -> None:
        items = [str(folder)] + [p for p in self["recent_sessions"] if p != str(folder)]
        self.data["recent_sessions"] = items[:MAX_RECENT]

    def save(self) -> None:
        write_json_atomic(self.path, self.data)
