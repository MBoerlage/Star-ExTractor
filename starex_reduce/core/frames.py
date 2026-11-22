"""Frame discovery and header summary. Frame type comes from the header first, file name second,
the folder role last (NINA writes 'DARK'/'BIAS', SharpCap 'Light'; see docs/EXAMPLE_NOVA.md)."""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .fitsio import FitsError, read_header

FITS_SUFFIXES = (".fits", ".fit", ".fts")
LIGHT, DARK, BIAS, FLAT, DARKFLAT, UNKNOWN = "light", "dark", "bias", "flat", "darkflat", "unknown"
SATURATION_WARN = 60000.0


def normalize_type(value: Any) -> str | None:
    """IMAGETYP/FRAMETYP value (SharpCap, NINA, MaxIm, …) → frame type."""
    if value is None:
        return None
    v = str(value).strip().lower().replace("_", " ").replace("-", " ")
    if not v:
        return None
    compact = v.replace(" ", "")
    if compact in ("darkflat", "flatdark"):
        return DARKFLAT
    if "light" in v or v in ("object", "science"):
        return LIGHT
    if "bias" in v or "offset" in v or v == "zero":
        return BIAS
    if "flat" in v:
        return FLAT
    if "dark" in v:
        return DARK
    return None


_NAME_TYPES = ((re.compile(r"dark[ _-]?flat|flat[ _-]?dark", re.I), DARKFLAT),
               (re.compile(r"bias|offset", re.I), BIAS), (re.compile(r"flat", re.I), FLAT),
               (re.compile(r"dark", re.I), DARK), (re.compile(r"light", re.I), LIGHT))


def type_from_name(name: str) -> str | None:
    for rx, t in _NAME_TYPES:
        if rx.search(name):
            return t
    return None


def parse_time(value: Any) -> datetime | None:
    """ISO time as UTC datetime; tolerates 7 fractional digits (NINA/SharpCap) and a trailing Z."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().rstrip("Z")
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?$", s)
    if not m:
        return None
    frac = (m.group(3) or "0")[:6].ljust(6, "0")
    try:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}.{frac}").replace(tzinfo=UTC)
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


@dataclass
class FrameInfo:
    path: Path
    header: dict[str, Any] = field(default_factory=dict)
    role: str | None = None
    error: str | None = None

    def h(self, key: str, default: Any = None) -> Any:
        return self.header.get(key, default)

    @property
    def frame_type(self) -> str:
        return (normalize_type(self.h("IMAGETYP")) or normalize_type(self.h("FRAMETYP"))
                or type_from_name(self.path.name) or self.role or UNKNOWN)

    @property
    def exptime(self) -> float | None:
        return _num(self.h("EXPTIME", self.h("EXPOSURE")))

    @property
    def gain(self) -> float | None:
        return _num(self.h("GAIN"))

    @property
    def offset(self) -> float | None:
        return _num(self.h("OFFSET"))

    @property
    def temp(self) -> float | None:
        return _num(self.h("CCD-TEMP"))

    @property
    def binning(self) -> tuple[int, int]:
        return int(self.h("XBINNING", 1) or 1), int(self.h("YBINNING", 1) or 1)

    @property
    def size(self) -> tuple[int, int] | None:
        w, h = self.h("NAXIS1"), self.h("NAXIS2")
        return (int(w), int(h)) if isinstance(w, int) and isinstance(h, int) else None

    @property
    def start(self) -> datetime | None:
        return parse_time(self.h("DATE-OBS"))

    @property
    def end(self) -> datetime | None:
        end = parse_time(self.h("DATE-END"))
        if end is None and self.start is not None and self.exptime is not None:
            end = self.start + timedelta(seconds=self.exptime)
        return end

    @property
    def object(self) -> str:
        return str(self.h("OBJECT", "") or "").strip()

    def sort_key(self) -> tuple:
        return (self.start or datetime.max.replace(tzinfo=UTC), self.path.name.lower())


def is_fits(path: Path) -> bool:
    return path.suffix.lower() in FITS_SUFFIXES and ".part" not in path.name


def iter_fits(folder: str | os.PathLike, recursive: bool = False) -> Iterable[Path]:
    root = Path(folder)
    it = root.rglob("*") if recursive else root.glob("*")
    for p in sorted(it):
        if p.is_file() and is_fits(p):
            yield p


def load_frame(path: str | os.PathLike, role: str | None = None) -> FrameInfo:
    p = Path(path)
    try:
        return FrameInfo(p, read_header(p), role)
    except (OSError, FitsError) as exc:
        return FrameInfo(p, role=role, error=str(exc))


def scan(folder: str | os.PathLike, role: str | None = None, recursive: bool = False) -> list[FrameInfo]:
    frames = [load_frame(p, role) for p in iter_fits(folder, recursive)]
    return sorted(frames, key=FrameInfo.sort_key)


def summary_line(frames: list[FrameInfo]) -> str:
    """`300 s · gain 200 · −5.5 °C · 3840×2160` from the readable frames (mixed values joined)."""
    ok = [f for f in frames if not f.error]
    if not ok:
        return "No readable frames"

    def uniq(vals: list) -> list:
        return sorted({v for v in vals if v is not None})

    parts = []
    exps = uniq([f.exptime for f in ok])
    if exps:
        parts.append(" / ".join(f"{e:g} s" for e in exps))
    gains = uniq([f.gain for f in ok])
    if gains:
        parts.append("gain " + " / ".join(f"{g:g}" for g in gains))
    temps = [f.temp for f in ok if f.temp is not None]
    if temps:
        parts.append(f"{sum(temps) / len(temps):.1f} °C".replace("-", "−"))
    sizes = uniq([f.size for f in ok])
    if sizes:
        parts.append(" / ".join(f"{w}×{h}" for w, h in sizes))
    return " · ".join(parts)
