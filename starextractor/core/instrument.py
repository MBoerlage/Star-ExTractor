"""The one instrument (Star'Ex LR + ASI585MM): saved geometry and wavelength solution.

Stored in %APPDATA%\\StarExtractor\\instrument_starex_lr.json. The dispersion is seeded with the
verified BASS solution of Gam Cas_00004.fits (docs/BASS_CALIBRATION.md §5): nm, 0-based pixel.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .settings import app_dir, write_json_atomic

PROFILE_FILE = "instrument_starex_lr.json"
SEED_DISPERSION = {
    "coeffs_nm": [348.381371973896, 0.120309271523351, 6.05776509593276e-08],
    "order": 2,
    "date": "2026-11-16",
    "source": "BASS Project 0.1.9.9, Gam Cas_00004.fits",
}


@dataclass
class InstrumentProfile:
    name: str = "Star'Ex LR + ASI585MM"
    flip_x: bool = False
    flip_y: bool = False
    tilt_deg: float | None = None
    tilt_date: str = ""
    slant_deg: float | None = None  # optional slant correction (docs/GEOMETRY_SLANT.md)
    slant_date: str = ""
    dispersion: dict[str, Any] = field(default_factory=lambda: dict(SEED_DISPERSION))
    fwhm_a: float | None = None  # measured by Wavelength; Export offers R as AAV_ITRP
    resolving_power: float | None = None
    # default rows from the spectrum row, saved from Extract: {"spec": [a, b], "sky_a": [..], "sky_b": [..]}
    bands: dict[str, Any] = field(default_factory=dict)
    crop_a: tuple[float, float] = (3900.0, 7700.0)  # Response: saved when edited, the start of new sessions
    norm_a: tuple[float, float] = (5500.0, 5600.0)
    # older files may also hold spectrum_row / smile_*: ignored on load (never read; smile is not in v1)
    path: Path | None = field(default=None, repr=False, compare=False)

    @classmethod
    def load(cls, path: Path | None = None) -> InstrumentProfile:
        p = path or app_dir() / PROFILE_FILE
        prof = cls()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    if k in cls.__dataclass_fields__ and k != "path":
                        setattr(prof, k, tuple(v) if k in ("crop_a", "norm_a") else v)
            except (OSError, ValueError):
                pass  # unreadable profile: fall back to the seed values
        prof.path = p
        return prof

    def save(self) -> None:
        data = asdict(self)
        data.pop("path", None)
        write_json_atomic(self.path or app_dir() / PROFILE_FILE, data)

    @property
    def has_geometry(self) -> bool:
        return self.tilt_deg is not None
