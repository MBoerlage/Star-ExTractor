"""The data root and its four locations: Stacks, Masters, Processed, Published (docs/DATA_ARCHITECTURE.md). No Qt.

Settings keys: "data_root" (absolute) and "data_paths" {stacks, masters, processed, published}: an empty entry
means <root>\\<Name>; an entry can point anywhere (Published on another drive). Stored paths inside the data
root are written relative to it (`to_stored` / `resolve`). Nothing here creates a folder unless asked
(`create_missing`), so an unplugged drive is reported, never replaced by a folder somewhere else.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KEYS = ("stacks", "masters", "processed", "published")
NAMES = {"stacks": "Stacks", "masters": "Masters", "processed": "Processed", "published": "Published"}
OK, MISSING, READONLY = "ok", "missing", "not writable"


@dataclass(frozen=True)
class DataPaths:
    root: Path
    stacks: Path
    masters: Path
    processed: Path
    published: Path

    def get(self, key: str) -> Path:
        return getattr(self, key)

    def items(self) -> list[tuple[str, Path]]:
        return [(k, self.get(k)) for k in KEYS]


def default_root() -> Path:
    """A suggestion for the first-run dialog: D:\\StarExtractorData when a D: drive exists, else in the home."""
    if os.name == "nt" and Path("D:/").exists():
        return Path("D:/StarExtractorData")
    return Path.home() / "StarExtractorData"


def data_paths(settings: Any) -> DataPaths:
    """The five folders from the settings (`settings` is a Settings or a plain dict). Without a data root (only
    before the first-run dialog, and in tests) the root is <app folder>\\Data."""
    from .settings import app_dir

    raw = settings.data if hasattr(settings, "data") else settings
    root = Path(raw.get("data_root") or (app_dir() / "Data"))
    over = raw.get("data_paths") or {}
    paths = {}
    for k in KEYS:
        v = str(over.get(k) or "").strip()
        p = Path(v) if v else root / NAMES[k]
        paths[k] = p if p.is_absolute() else root / p
    out = DataPaths(root, **paths)
    if not raw.get("data_root"):  # the app's own fallback folder (tests, before the first-run dialog): made here
        create_missing(out)
    return out


def status(path: Path) -> str:
    """ok | missing | not writable. Never creates anything (a temp file is made and removed to test writing)."""
    p = Path(path)
    if not p.is_dir():
        return MISSING
    try:
        fd, name = tempfile.mkstemp(dir=p, prefix=".starex_write_test_")
        os.close(fd)
        os.unlink(name)
    except OSError:
        return READONLY
    return OK


def problems(paths: DataPaths) -> list[tuple[str, Path, str]]:
    """(name, path, state) for every location that is not usable."""
    out = []
    for k, p in paths.items():
        st = status(p)
        if st != OK:
            out.append((NAMES[k], p, st))
    return out


def create_missing(paths: DataPaths) -> list[Path]:
    """Create the locations that do not exist (the root first). Returns what was made."""
    made = []
    for p in (paths.root, *(p for _k, p in paths.items())):
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            made.append(p)
    return made


def to_stored(path: str | os.PathLike, root: Path) -> str:
    """A path for logbooks and the sets file: relative (forward slashes) when inside the data root, else
    absolute (raw capture folders)."""
    p = Path(path)
    try:
        return p.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(p)


def resolve(stored: str, root: Path) -> Path:
    p = Path(stored)
    return p if p.is_absolute() else Path(root) / p
