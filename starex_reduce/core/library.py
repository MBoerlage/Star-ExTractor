"""The Library tab's data: every pair under Processed and every stack under Stacks that is not in a pair yet,
grouped by night (docs/LIBRARY_TAB.md). Reads logbooks, file stats and FITS headers only, never pixels (except
the 1D spectrum thumbnail, in a worker). No Qt.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import logbook
from .datalocs import DataPaths, resolve, to_stored

ROLE_SUFFIX = {"_target_S": "target", "_ref_S": "reference"}
FILTERS = ("all", "stacked", "reduced", "published", "attention")
THUMB_POINTS = 600


@dataclass
class Row:
    """One line of the Library table: a pair, or a stack that has no pair yet (kind "stack")."""

    kind: str  # "pair" | "stack"
    night: str
    target: str
    reference: str  # "" for a stack without a reference
    key: str  # pair folder name, or the stack path relative to the data root
    folder: Path | None = None  # the pair folder
    stack: Path | None = None  # the stack file of an unpaired target
    status: dict[str, bool] = field(default_factory=dict)
    status_word: str = ""
    notes: str = ""
    last_note: str = ""
    attention: tuple[str, ...] = ()
    steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    published: tuple[dict[str, Any], ...] = ()
    suggested_reference: str = ""  # an unpaired stack: the sets file's reference stack (relative), if known

    def matches(self, text: str) -> bool:
        t = text.strip().lower()
        return not t or t in self.target.lower() or t in self.reference.lower() or t in self.notes.lower()

    def passes(self, chip: str) -> bool:
        if chip == "stacked":
            return bool(self.status.get("stacked"))
        if chip == "reduced":
            return bool(self.status.get("reduced"))
        if chip == "published":
            return bool(self.status.get("published"))
        if chip == "attention":
            return bool(self.attention)
        return True


@dataclass
class Library:
    rows: list[Row]
    nights: int
    pairs: int
    published: int
    attention: int
    scanned_utc: str = ""
    ms: float = 0.0

    def filtered(self, chip: str = "all", text: str = "") -> list[Row]:
        return [r for r in self.rows if r.passes(chip) and r.matches(text)]


def role_of(path: Path) -> str | None:
    """target | reference from the file name suffix (<Name>_target_S.fit, <Name>_ref_S_2.fit), else None."""
    stem = path.stem
    for suffix, role in ROLE_SUFFIX.items():
        i = stem.rfind(suffix)
        if i > 0 and (i + len(suffix) == len(stem) or stem[i + len(suffix):].lstrip("_").isdigit()):
            return role
    return None


def _stack_name(path: Path) -> str:
    from .fitsio import read_header

    try:
        h = read_header(path)
    except (OSError, ValueError):
        h = {}
    name = str(h.get("SXNAME") or h.get("OBJECT") or "").strip()
    if name:
        return name
    stem = path.stem
    for suffix in ROLE_SUFFIX:
        stem = stem.split(suffix)[0]
    return stem


def _sets_reference(night_dir: Path, target: Path, root: Path) -> str:
    """The reference stack the night's sets file names next to this target stack ('' when unknown)."""
    from . import acq_sets

    f = night_dir / acq_sets.FILE_NAME
    if not f.exists():
        return ""
    try:
        doc = acq_sets.load_sets(f)
    except (OSError, ValueError):
        return ""
    t = doc["target"].get("stack_file") or ""
    if t and Path(t).name == target.name and doc["reference"].get("stack_file"):
        return str(doc["reference"]["stack_file"])
    return ""


def unpaired_stacks(paths: DataPaths, paired: set[str]) -> list[Row]:
    """Target stacks under Stacks/<night>/ that no pair logbook names (role from the file name suffix)."""
    out = []
    try:
        nights = [d for d in paths.stacks.iterdir() if d.is_dir()]
    except OSError:
        return []
    for d in nights:
        for f in sorted(d.glob("*.fit")):
            if role_of(f) != "target":
                continue
            rel = to_stored(f, paths.root)
            if rel in paired:
                continue
            out.append(Row("stack", d.name, _stack_name(f), "", rel, stack=f,
                           status={"stacked": False, "reduced": False, "published": False},
                           status_word="No reference", attention=("stack with no reference",),
                           suggested_reference=_sets_reference(d, f, paths.root)))
    return out


def reference_candidates(paths: DataPaths, night: str, exclude: Path | None = None) -> list[tuple[str, Path]]:
    """(label, path) of the other stacks of a night: reference stacks first, then other targets."""
    d = paths.stacks / night
    files = [f for f in sorted(d.glob("*.fit")) if role_of(f) and (exclude is None or f != exclude)]
    files.sort(key=lambda f: (role_of(f) != "reference", f.name.lower()))
    return [(f"{_stack_name(f)} ({'reference' if role_of(f) == 'reference' else 'target'} stack, {f.name})", f)
            for f in files]


def scan(paths: DataPaths) -> Library:
    """Everything the Library shows, newest night first (pairs, then the night's unpaired stacks)."""
    t0 = time.perf_counter()
    rows: list[Row] = []
    paired: set[str] = set()
    for p in logbook.scan_pairs(paths.root, paths.processed):
        doc_sides = _sides(p.folder)
        paired.update(v for v in doc_sides if v)
        rows.append(Row("pair", p.night, p.target, p.reference, p.folder.name, folder=p.folder, status=p.status,
                        status_word=p.status_word, notes=p.notes, last_note=p.last_note, attention=p.attention,
                        steps=p.steps or {}, published=p.published))
    rows += unpaired_stacks(paths, paired)
    rows.sort(key=lambda r: (r.night, r.kind == "pair", r.target.lower()), reverse=True)
    rows.sort(key=lambda r: r.night, reverse=True)  # stable: within a night pairs first, then stacks
    lib = Library(rows, len({r.night for r in rows}), sum(r.kind == "pair" for r in rows),
                  sum(bool(r.status.get("published")) for r in rows), sum(bool(r.attention) for r in rows),
                  logbook.now_utc(), (time.perf_counter() - t0) * 1000)
    return lib


def _sides(folder: Path) -> list[str]:
    doc = logbook.load(folder) or {}
    return [str((doc.get(r) or {}).get("stack_file") or "") for r in ("target", "reference")]


def scan_job(ctx: Any, paths: DataPaths) -> Library:
    """The scan in a worker (cancellable between pairs is not needed: logbooks only, well under a second)."""
    lib = scan(paths)
    if ctx is not None:
        ctx.step(1.0)
    return lib


# ---- details -------------------------------------------------------------------------------------------------------
def avspec_file(folder: Path, root: Path) -> Path | None:
    """The AVSpec file saved for the pair (the logbook's export record, else the newest .fit in export/)."""
    doc = logbook.load(folder) or {}
    rec = (doc.get("processing") or {}).get("export") or {}
    if rec.get("file"):
        p = resolve(rec["file"], root)
        if p.exists():
            return p
    files = sorted((folder / "export").glob("*.fit"), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None


def first_open_step(session: Any) -> int:
    """Index of the first Reduce step that is not done for both images (0 = Select when all are done)."""
    from .pipeline import IMAGES, STAGE_IDS

    keys = session.compute_keys()
    for i, sid in enumerate(STAGE_IDS):
        if not all(session.image_done(sid, img, keys) for img in IMAGES):
            return i
    return 0


def thumb_job(ctx: Any, path: str) -> dict[str, Any]:
    """The 1D spectrum downsampled to about THUMB_POINTS points with its wavelength axis (worker)."""
    from astropy.io import fits

    from .export import wavelengths

    with fits.open(path, memmap=False) as f:
        flux = np.asarray(f[0].data, dtype=np.float64).ravel()
        h = dict(f[0].header)
    lam = wavelengths(h, len(flux))
    step = max(1, -(-len(flux) // THUMB_POINTS))  # ceil: at most THUMB_POINTS points
    n = len(flux) // step * step
    return {"path": path, "mtime": os.stat(path).st_mtime_ns,
            "lam": lam[:n].reshape(-1, step).mean(axis=1), "flux": flux[:n].reshape(-1, step).mean(axis=1)}
