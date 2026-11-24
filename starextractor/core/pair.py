"""Reduce › Select: the pair of stacks (target + reference star) that goes through the reduce flow. No Qt.

A stack is read from its own header first (the SX* block, core/stack_keys.py), then from the Acquisition sets
file entry that points at it, then from the standard keywords of older stacks. `checks` lists what the Select
screen shows; only a ✗ blocks loading. `pair_folder_for` picks Processed/<night>_<Target>__<Ref>; `load_pair`
copies the stacks to work/Target_S.fit and work/Reference_S.fit there (where Geometry reads them), stores the pair
in the session and writes the pair logbook.
"""
from __future__ import annotations

import math
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from . import acq_sets, coords
from .frames import FrameInfo, load_frame
from .pair_state import active_pair as active_pair
from .pair_state import pair_text as pair_text
from .pair_state import pairs_of as pairs_of
from .pipeline import output_name
from .session import Session
from .stack_keys import Rows1, rows_from_header

TIME_GAP_WARN_H = 3.0  # ! when the reference was taken more than this many hours before/after the target
SEP_WARN_DEG = 20.0  # ! when the reference is further than this from the target on the sky
PREVIEW_HALF_ROWS = 200  # rows shown above and below the spectrum band in the preview
PREVIEW_COLS = 960  # preview width (columns are binned down to about this)
ROLES = ("target", "reference")
IMAGE_OF = {"target": "Target", "reference": "Reference"}
TITLE = {"target": "Target", "reference": "Reference"}
FILE_FILTER = "FITS stacks (*.fit *.fits *.fts)"

OK, WARN, ERR, NOTE = "ok", "warn", "err", "note"


@dataclass
class StackInfo:
    """What Select shows about one stack file. `error` set when it is not a readable 2D image."""

    path: Path
    error: str = ""
    size: tuple[int, int] | None = None  # (width, height)
    name: str = ""
    name_src: str = ""  # "file header" | "sets file" | ""
    nsub: int | None = None
    subexp: float | None = None
    start: datetime | None = None
    gain: float | None = None
    offset: float | None = None
    temp: float | None = None
    binning: tuple[int, int] | None = None
    ra: float | None = None  # deg
    dec: float | None = None
    pos_src: str = ""  # "file header" | "SIMBAD"
    rows: Rows1 = field(default_factory=dict)
    rows_src: str = ""  # "file header" | "sets file"
    sp_type: str = ""
    notes: str = ""  # the set's observing notes from the sets file (imported into the pair logbook)
    masters: dict[str, tuple[str, str]] = field(default_factory=dict)  # kind → (path in Masters, sha256)
    raw_dir: str = ""
    read_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.error

    def camera_known(self) -> bool:
        return self.gain is not None and self.offset is not None and self.binning is not None


def sets_entry(path: Path, folders: list[Path]) -> dict[str, Any] | None:
    """The sets-file side (normalised, with 'role') whose stack_file is this file, looked up in `folders`."""
    want = os.path.normcase(str(Path(path).resolve()))
    seen: set[str] = set()
    for folder in folders:
        f = Path(folder) / acq_sets.FILE_NAME
        if str(f) in seen or not f.exists():
            continue
        seen.add(str(f))
        try:
            doc = acq_sets.load_sets(f)
        except (OSError, ValueError):
            continue
        for role in ROLES:
            sf = doc[role].get("stack_file")
            if not sf:
                continue
            # stored relative to the data root (the sets file sits in Stacks/<night>/, next to its stacks),
            # absolute, or (older files) relative to the subs' folder
            root = Path(folder).parents[1] if len(Path(folder).parents) > 1 else Path(folder)
            cands = [Path(sf)]
            if not Path(sf).is_absolute():
                cands = [root / sf, Path(folder) / Path(sf).name, acq_sets.absolute(doc, sf)]
            for p in cands:
                try:
                    if os.path.normcase(str(p.resolve())) == want:
                        return {**doc[role], "role": role}
                except OSError:
                    continue
    return None


def read_stack(path: str | os.PathLike, project: Path | None = None) -> StackInfo:
    """Header reads only (no pixels): the file's own SX* block first, then the sets file, then standard keys."""
    from .files_stage import header_coords

    t0 = time.perf_counter()
    p = Path(path)
    info = StackInfo(p)
    f: FrameInfo = load_frame(p)
    if f.error:
        info.error = f"cannot read {p.name}: {f.error}"
        return info
    h = f.header
    if h.get("NAXIS") != 2 or f.size is None:
        info.error = f"{p.name} is not a 2D image"
        return info
    info.size = f.size
    entry = sets_entry(p, [p.parent] + ([project] if project is not None else []))
    if str(h.get("SXNAME") or "").strip():
        info.name, info.name_src = str(h["SXNAME"]).strip(), "file header"
    elif entry and entry.get("name"):
        info.name, info.name_src = entry["name"], "sets file"
    elif f.object:
        info.name, info.name_src = f.object, "file header"
    info.nsub = _int(h.get("SXNSUB")) or _int(h.get("NCOMBINE")) or _int(h.get("STACKCNT"))
    info.subexp = _flt(h.get("SXSUBEXP"))
    if info.subexp is None and info.nsub and f.exptime is not None:
        info.subexp = f.exptime  # older stacks of this program: EXPTIME is the exposure of one sub
    info.start = f.start
    info.gain, info.offset, info.temp = f.gain, f.offset, f.temp
    if "XBINNING" in h or "YBINNING" in h:
        info.binning = f.binning
    ra, dec = header_coords([f])
    if ra is not None and dec is not None:
        info.ra, info.dec, info.pos_src = ra, dec, "file header"
    elif entry and entry.get("ra") and entry.get("dec"):
        try:
            info.ra, info.dec = coords.parse_coords(str(entry["ra"]), str(entry["dec"]))
            info.pos_src = "SIMBAD"
        except ValueError:
            pass
    info.rows = rows_from_header(h)
    if info.rows:
        info.rows_src = "file header"
    elif entry and entry.get("rows"):
        from .stack_keys import rows1_of

        info.rows = rows1_of(entry["rows"])
        info.rows_src = "sets file" if info.rows else ""
    info.sp_type = str((entry or {}).get("sp_type") or "")
    info.notes = str((entry or {}).get("notes") or "")
    from .stack_keys import masters_from_header

    info.masters = masters_from_header(h)
    info.raw_dir = str(h.get("SXRAWDIR") or "")
    info.read_ms = (time.perf_counter() - t0) * 1000
    return info


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def _flt(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ---- checks -----------------------------------------------------------------------------------------------------
def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    c = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(r1 - r2)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _gap_text(minutes: float) -> str:
    m = abs(minutes)
    if m < 90:
        return f"{m:.0f} min"
    return f"{m / 60:.1f} h"


def checks(t: StackInfo | None, r: StackInfo | None, names: dict[str, str]) -> list[tuple[str, str]]:
    """The Select checks, in order: (state ok|warn|err|note, text). Only err blocks loading."""
    out: list[tuple[str, str]] = []
    # 1. both readable 2D images of the same size
    bad = [f"{TITLE[role]}: {i.error}" if i is not None else f"Pick the {role} stack."
           for role, i in (("target", t), ("reference", r)) if i is None or not i.ok]
    if bad:
        out.append((ERR, " ".join(bad)))
    elif t.size != r.size:  # type: ignore[union-attr]
        out.append((ERR, f"The images differ in size: target {t.size[0]} × {t.size[1]}, "  # type: ignore[union-attr,index]
                         f"reference {r.size[0]} × {r.size[1]}."))  # type: ignore[union-attr,index]
    else:
        out.append((OK, f"Both files are 2D images of {t.size[0]} × {t.size[1]}."))  # type: ignore[union-attr,index]
    both = t is not None and r is not None and t.ok and r.ok
    # 2. names
    missing = [role for role in ROLES if not names.get(role, "").strip()]
    if missing:
        texts = {"target": "Type a name for the target. It is used in the output file names and the AVSpec header.",
                 "reference": "Type a name for the reference star. It picks its library spectrum and goes in the "
                              "file headers."}
        out.extend((ERR, texts[role]) for role in missing)
    else:
        out.append((OK, f"Names set: {names['target'].strip()} and {names['reference'].strip()}."))
    # 3. rows passed on from Acquisition
    if both:
        if t.rows.get("spec") and r.rows.get("spec"):  # type: ignore[union-attr]
            out.append((OK, "Spectrum and sky rows passed on from Acquisition for both."))
        else:
            out.append((WARN, "Rows are not in the files. They will be placed automatically in 1D Extract; "
                              "check them there."))
    # 4. same camera setup
    if both:
        if not (t.camera_known() and r.camera_known()):  # type: ignore[union-attr]
            out.append((WARN, "Camera settings are not in the headers, so the setup cannot be compared."))
        else:
            diff = [what for what, a, b in (("gain", t.gain, r.gain), ("offset", t.offset, r.offset),  # type: ignore[union-attr]
                                            ("binning", t.binning, r.binning), ("size", t.size, r.size))  # type: ignore[union-attr]
                    if a != b]
            out.append((WARN, f"Different camera setup: {', '.join(diff)} differ.") if diff
                       else (OK, f"Same camera setup (gain {t.gain:g}, offset {t.offset:g}, "  # type: ignore[union-attr]
                                 f"binning {t.binning[0]}×{t.binning[1]})."))  # type: ignore[union-attr,index]
    # 5. time gap
    if both and t.start is not None and r.start is not None:  # type: ignore[union-attr]
        minutes = (r.start - t.start).total_seconds() / 60  # type: ignore[union-attr,operator]
        text = f"Reference taken {_gap_text(minutes)} {'after' if minutes >= 0 else 'before'} the target."
        out.append((WARN if abs(minutes) > TIME_GAP_WARN_H * 60 else OK, text))
    # 6. distance on the sky
    if both:
        if None in (t.ra, t.dec, r.ra, r.dec):  # type: ignore[union-attr]
            out.append((NOTE, "No coordinates, so the distance and airmass are not checked."))
        else:
            sep = separation_deg(t.ra, t.dec, r.ra, r.dec)  # type: ignore[union-attr,arg-type]
            out.append((WARN if sep > SEP_WARN_DEG else OK, f"Reference is {sep:.2f}° from the target on the sky."))
    # 7. not the same file
    if t is not None and r is not None and _same_file(t.path, r.path):
        out.append((ERR, "Target and Reference point at the same file."))
    return out


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def summary(items: list[tuple[str, str]]) -> tuple[str, str]:
    """(state, heading text) for the Checks card."""
    errs = sum(1 for s, _ in items if s == ERR)
    warns = sum(1 for s, _ in items if s == WARN)
    if errs:
        return ERR, f"{errs} to fix"
    if warns:
        return WARN, f"{warns} warning{'s' if warns > 1 else ''}, nothing blocking"
    return OK, "all clear"


# ---- preview (worker) -------------------------------------------------------------------------------------------
def preview_job(ctx: Any, path: str, rows: Rows1) -> dict[str, Any]:
    """A small 8-bit view of the stack around the spectrum rows (±PREVIEW_HALF_ROWS, clamped), columns binned to
    about PREVIEW_COLS, percentile-stretched. Reads only those rows through the memmap."""
    from .display import stretch
    from .fitsio import open_image

    t0 = time.perf_counter()
    img = open_image(path)
    h, w = img.shape
    a, b = rows["spec"]
    y0 = max(0, a - 1 - PREVIEW_HALF_ROWS)
    y1 = min(h, b + PREVIEW_HALF_ROWS)
    strip = img.strip(y0, y1)
    step = max(1, w // PREVIEW_COLS)
    ww = w // step * step
    small = strip[:, :ww].reshape(y1 - y0, ww // step, step).mean(axis=2, dtype=np.float32)
    lo, hi = np.percentile(small, [1.0, 99.9])
    u8 = stretch(small, (float(lo), float(hi) if hi > lo else float(lo) + 1.0))
    return {"path": path, "u8": np.ascontiguousarray(u8), "y0": y0, "y1": y1, "width": w, "step": step,
            "rows": rows, "ms": (time.perf_counter() - t0) * 1000}


# ---- the pair in the session --------------------------------------------------------------------------------------
def side_entry(info: StackInfo, name: str, folder: Path, role: str) -> dict[str, Any]:
    return {"source": str(info.path), "file": output_name("files", IMAGE_OF[role]), "name": name.strip(),
            "name_src": info.name_src if name.strip() == info.name else "typed",
            "ra": info.ra, "dec": info.dec, "pos_src": info.pos_src,
            "rows": {k: list(v) for k, v in info.rows.items()}, "rows_src": info.rows_src,
            "sp_type": info.sp_type}


def _copy_into(src: Path, dst: Path) -> bool:
    """Copy src to dst (headers included) unless dst already is that file or an identical copy. True if copied."""
    if dst.exists():
        if _same_file(src, dst):
            return False
        a, b = src.stat(), dst.stat()
        if a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns:
            return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    return True


def night_of_pair(infos: dict[str, StackInfo]) -> str:
    """The observing night of the target stack's first sub (DATE-OBS), else of the reference, else tonight."""
    from .naming import observing_night

    for role in ROLES:
        if infos[role].start is not None:
            return observing_night(infos[role].start)
    return observing_night()


def pair_folder_for(processed: Path, infos: dict[str, StackInfo], names: dict[str, str],
                    data_root: Path) -> tuple[Path, bool]:
    """(Processed/<night>_<Target>__<Ref>, reused). An existing folder is reused when its logbook names the same
    two stack files; a different pair with the same name gets _2, _3, … Nothing is created here."""
    from . import logbook
    from .datalocs import to_stored
    from .naming import pair_folder, with_suffix_number

    base = Path(processed) / pair_folder(night_of_pair(infos), names["target"], names["reference"])
    want = {r: to_stored(infos[r].path, data_root) for r in ROLES}
    n = 1
    while True:
        cand = with_suffix_number(base, n)
        if not cand.exists():
            return cand, False
        doc = logbook.load(cand)
        if doc is not None and all((doc.get(r) or {}).get("stack_file") == want[r] for r in ROLES):
            return cand, True
        n += 1


def _logbook_side(info: StackInfo, name: str, data_root: Path) -> dict[str, Any]:
    from .cache import file_sha256
    from .datalocs import to_stored

    r = info.rows
    return {"name": name.strip(), "stack_file": to_stored(info.path, data_root), "sha256": file_sha256(info.path),
            "nsub": info.nsub, "exp_s": info.subexp,
            "date_obs_utc": info.start.isoformat().replace("+00:00", "Z") if info.start else None,
            "ra": info.ra, "dec": info.dec, "position_source": info.pos_src or None,
            "rows": {"spectrum": list(r["spec"]) if "spec" in r else None,
                     "sky_a": list(r["sky_a"]) if "sky_a" in r else None,
                     "sky_b": list(r["sky_b"]) if "sky_b" in r else None},
            "raw_dir": info.raw_dir or None}


def write_logbook(folder: Path, infos: dict[str, StackInfo], names: dict[str, str], data_root: Path,
                  masters_root: Path) -> dict[str, Any]:
    """Create or update the pair logbook: stacks, names, rows, camera, masters used; the sets' notes are imported
    once (appended when not already in the notes). Unknown fields are kept."""
    from . import logbook
    from .datalocs import to_stored

    doc = logbook.load(folder) or logbook.empty(folder.name, night_of_pair(infos))
    t = infos["target"]
    used, seen = [], set()
    for role in ROLES:
        for kind, (path, sha) in infos[role].masters.items():
            if path not in seen:
                seen.add(path)
                used.append({"file": to_stored(masters_root / path, data_root), "sha256": sha, "kind": kind})
    notes = str(doc.get("notes") or "")
    for role in ROLES:
        n = infos[role].notes.strip()
        if n and n not in notes:
            notes = (notes.rstrip() + "\n\n" if notes.strip() else "") + f"{TITLE[role]}: {n}"
    logbook.merge(doc, {"target": _logbook_side(t, names["target"], data_root),
                        "reference": _logbook_side(infos["reference"], names["reference"], data_root),
                        "camera": {"gain": t.gain, "offset": t.offset, "temp_c": t.temp,
                                   "binning": list(t.binning) if t.binning else None},
                        "masters_used": used, "notes": notes})
    doc["status"] = {**(doc.get("status") or {}), "stacked": logbook.now_utc()}
    logbook.save(folder, doc)
    return doc


def hot_pixel_darks(infos: dict[str, StackInfo], masters_root: Path) -> dict[str, list[str]]:
    """The dark master each stack was calibrated with (SXDARK), for Geometry's hot-pixel map."""
    out = {}
    for role in ROLES:
        d = infos[role].masters.get("dark")
        p = masters_root / d[0] if d else None
        out[role] = [str(p)] if p is not None and p.exists() else []
    return out


def load_pair(session: Session, infos: dict[str, StackInfo], names: dict[str, str],
              acq_cal_files: dict[str, list[str]] | None = None, data_root: Path | None = None,
              masters_root: Path | None = None) -> list[str]:
    """In the pair (session) folder: copy the chosen stacks to work/Target_S.fit and work/Reference_S.fit, store
    the pair (a one-item list: the night queue later holds several), the names and positions, write the pair
    logbook (when `data_root` is given) and mark Select done. Returns log lines. The caller checks for ✗ first."""
    assert session.folder is not None
    log = []
    pair = {}
    if masters_root is not None and acq_cal_files is None:
        acq_cal_files = hot_pixel_darks(infos, masters_root)
    for role in ROLES:
        info = infos[role]
        dst = session.folder / output_name("files", IMAGE_OF[role])
        if _copy_into(info.path, dst):
            log.append(f"Select: copied {info.path} → {dst.relative_to(session.folder).as_posix()}")
        entry = side_entry(info, names[role], session.folder, role)
        pair[role] = entry
        side = session.inputs[role]
        if side.get("name") != entry["name"]:
            side["simbad"] = {}
        side["name"] = entry["name"]
        if entry["ra"] is not None and entry["dec"] is not None:
            side["coords"] = {"source": "header" if entry["pos_src"] == "file header" else "simbad",
                              "ra": coords.format_ra(entry["ra"]), "dec": coords.format_dec(entry["dec"])}
        if entry["sp_type"] and not (side.get("simbad") or {}).get("sp_type"):
            side["simbad"] = {"found": True, "query": entry["name"], "main_id": entry["name"],
                              "sp_type": entry["sp_type"], "ra": entry["ra"], "dec": entry["dec"]}
        session.calibration[role]["resolved"] = list((acq_cal_files or {}).get(role) or [])
    files = dict(session.params.get("files") or {})
    files["pairs"] = [pair]
    session.params["files"] = files
    session.mark_done("files", {IMAGE_OF[r]: pair[r]["file"] for r in ROLES}, pair_text(pair))
    if data_root is not None:
        write_logbook(session.folder, infos, names, data_root, masters_root or data_root / "Masters")
        log.append(f"Select: logbook {session.folder.name}/logbook.json updated")
    log.append(f"Select: pair loaded: {pair_text(pair)} ({infos['target'].path.name} + "
               f"{infos['reference'].path.name})")
    return log

