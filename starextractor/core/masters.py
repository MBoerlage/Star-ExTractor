"""Master library (bias/dark/flat), master building, and the calibration rules of SPEC §4.

Calibration of a light is always `cal = L − C` with one precomputed frame C:
- exact dark, no bias:            C = D                      (only valid at t_D == t_L)
- with bias (normal):             C = B + (t_L/t_D)·S        S = D − B (pedestal-free signal)
- Match pedestal (gain/offset of the masters differ from the lights, default ON then):
                                  C = (t_L/t_D)·g·S          light pedestal stays; g = 1 unless
                                                             "scale by gain ratio" is on.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from starextractor import APP_NAME

from .combine import combine_strips
from .fitsio import STRUCTURAL, open_image, read_image, write_float32
from .frames import BIAS, DARK, FLAT, FrameInfo
from .jobs import Ctx
from .settings import write_json_atomic

KINDS = (DARK, BIAS, FLAT)
EXP_EQUAL_REL = 1e-3


# ---- library ---------------------------------------------------------------------------------
@dataclass
class Master:
    id: str
    kind: str
    exptime: float
    gain: float | None
    offset: float | None
    temp: float | None
    size: tuple[int, int]
    binning: tuple[int, int]
    nframes: int
    date: str  # date of the frames (YYYY-MM-DD)
    created: str
    path: str  # path inside the Masters folder (<camera>/<year>/<file>), or absolute for imported masters
    bias_subtracted: bool = False
    enabled: bool = True
    method: str = "median"
    sha256: str = ""  # of the master file; filled when it is written, or the first time it is used

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Master:
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        d["size"] = tuple(d["size"])
        d["binning"] = tuple(d.get("binning", (1, 1)))
        return cls(**d)

    def label(self) -> str:
        return f"{self.kind.title()} {fmt_exp(self.exptime)}"


def fmt_exp(t: float) -> str:
    if t < 0.1:
        return f"{t * 1000:g} ms"
    return f"{t:g} s"


class MasterLibrary:
    """The masters in the Masters location, listed in its index.json. A missing index is rebuilt from the FITS
    headers of the files found there. Reading never creates the folder (an unplugged drive stays visible as
    missing); writing a master does."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.index_path = self.folder / "index.json"
        self.masters: list[Master] = []
        self._data: dict[str, tuple[float, np.ndarray]] = {}
        if self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text(encoding="utf-8"))
                self.masters = [Master.from_json(m) for m in raw.get("masters", [])]
            except (OSError, ValueError, KeyError, TypeError):
                self.masters = []
        elif self.folder.is_dir():
            self.rebuild_index()

    def save(self) -> None:
        write_json_atomic(self.index_path, {"schema": 1, "masters": [asdict(m) for m in self.masters]})

    def rebuild_index(self) -> int:
        """Scan the Masters folder for master FITS files (float32 with IMAGETYP dark/bias/flat) and list them.
        Returns how many were found; writes index.json when there are any."""
        from .frames import iter_fits, load_frame

        self.masters = []
        for path in iter_fits(self.folder, recursive=True):
            f = load_frame(path)
            if f.error or f.size is None or f.h("BITPIX") != -32 or f.frame_type not in KINDS:
                continue
            self.masters.append(_master_from(f, f.frame_type, rel_path(path, self.folder)))
        if self.masters:
            self.save()
        return len(self.masters)

    def sha256(self, m: Master) -> str:
        """The master's SHA256 (computed once and kept in the index)."""
        if not m.sha256:
            from .cache import file_sha256

            m.sha256 = file_sha256(self.abspath(m))
            self.save()
        return m.sha256

    def get(self, mid: str | None) -> Master | None:
        return next((m for m in self.masters if m.id == mid), None)

    def by_kind(self, kind: str, enabled_only: bool = True) -> list[Master]:
        return [m for m in self.masters if m.kind == kind and (m.enabled or not enabled_only)]

    def abspath(self, m: Master) -> Path:
        p = Path(m.path)
        return p if p.is_absolute() else self.folder / p

    def add(self, m: Master) -> None:
        self.masters.append(m)
        self.save()

    def remove(self, mid: str, delete_file: bool = True) -> None:
        m = self.get(mid)
        if m is None:
            return
        self.masters.remove(m)
        p = self.abspath(m)
        if delete_file and not Path(m.path).is_absolute() and p.exists():
            p.unlink()
        self._data.pop(mid, None)
        self.save()

    def set_enabled(self, mid: str, enabled: bool) -> None:
        m = self.get(mid)
        if m is not None:
            m.enabled = enabled
            self.save()

    def data(self, m: Master) -> np.ndarray:
        """Master pixels (float32), cached while the file is unchanged."""
        p = self.abspath(m)
        mtime = p.stat().st_mtime_ns
        hit = self._data.get(m.id)
        if hit is not None and hit[0] == mtime:
            return hit[1]
        arr = read_image(p)
        self._data[m.id] = (mtime, arr)
        return arr

    def import_file(self, path: Path, kind: str | None = None) -> Master:
        """Register an existing master FITS (kept where it is)."""
        from .frames import load_frame

        f = load_frame(path)
        if f.error or f.size is None:
            raise ValueError(f"{path.name}: {f.error or 'not a 2-D image'}")
        k = kind or f.frame_type
        if k not in KINDS:
            raise ValueError(f"{path.name}: cannot tell whether this is a dark, bias or flat")
        m = _master_from(f, k, rel_path(path, self.folder))
        self.add(m)
        return m


def rel_path(path: Path, folder: Path) -> str:
    """Inside the Masters folder: relative with forward slashes; elsewhere absolute."""
    try:
        return Path(path).resolve().relative_to(Path(folder).resolve()).as_posix()
    except ValueError:
        return str(Path(path).resolve())


def _master_from(f: FrameInfo, kind: str, path: str) -> Master:
    hist = " ".join(str(v) for v in f.header.values() if isinstance(v, str))
    return Master(
        id=uuid.uuid4().hex[:10], kind=kind, exptime=f.exptime or 0.0, gain=f.gain, offset=f.offset,
        temp=f.temp, size=f.size or (0, 0), binning=f.binning, nframes=int(f.h("NCOMBINE", 1) or 1),
        date=(f.start.date().isoformat() if f.start else ""), created=time.strftime("%Y-%m-%d"),
        path=path, bias_subtracted=bool(f.h("BIASSUB", "bias subtracted" in hist)),
    )


def master_subdir(f0: FrameInfo) -> Path:
    """<camera>/<year>: the camera from INSTRUME (sanitised), the year of the frames."""
    from .naming import sanitize_name

    camera = sanitize_name(str(f0.h("INSTRUME") or "")) if f0.h("INSTRUME") else "Camera"
    year = f0.start.year if f0.start else time.localtime().tm_year
    return Path(camera) / str(year)


# ---- building --------------------------------------------------------------------------------
def group_for_masters(frames: list[FrameInfo]) -> list[list[FrameInfo]]:
    """One group per (exposure, gain, offset, size, binning): one master each."""
    groups: dict[tuple, list[FrameInfo]] = {}
    for f in frames:
        if f.error or f.size is None:
            continue
        key = (round(f.exptime or 0.0, 4), f.gain, f.offset, f.size, f.binning)
        groups.setdefault(key, []).append(f)
    return [groups[k] for k in sorted(groups, key=lambda k: (k[0], str(k[1:])))]


def camera_matches(m: Master, gain: float | None, offset: float | None, tol: Tolerances) -> bool:
    return _within(m.gain, gain, tol.gain_rel) and _within(m.offset, offset, tol.offset_rel)


def _within(a: float | None, b: float | None, rel: float) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= rel * max(abs(b), 1.0)


def find_bias_for(lib: MasterLibrary, size: tuple[int, int], binning: tuple[int, int],
                  gain: float | None, offset: float | None, temp: float | None,
                  tol: Tolerances) -> Master | None:
    """Bias with exactly the same gain/offset (and temperature within tolerance)."""
    cands = [b for b in lib.by_kind(BIAS) if b.size == size and b.binning == binning
             and b.gain == gain and b.offset == offset
             and (b.temp is None or temp is None or abs(b.temp - temp) <= tol.temp_c)]
    return max(cands, key=lambda b: (b.nframes, b.created), default=None)


def build_master(frames: list[FrameInfo], kind: str, lib: MasterLibrary, ctx: Ctx,
                 method: str = "median", sigma: float = 3.0, subtract_bias: bool = True,
                 tol: Tolerances | None = None) -> Master:
    """Combine one group of raw calibration frames into a float32 master in the library."""
    tol = tol or Tolerances()
    if not frames:
        raise ValueError("no frames to combine")
    imgs = [open_image(f.path) for f in frames]
    h, w = imgs[0].shape
    for img in imgs:
        if img.shape != (h, w):
            raise ValueError(f"{img.path.name}: image size {img.shape} differs from {(h, w)}")
    f0 = frames[0]
    temps = [f.temp for f in frames if f.temp is not None]
    temp = round(sum(temps) / len(temps), 2) if temps else None

    bias = None
    if kind in (DARK, FLAT) and subtract_bias:
        bias = find_bias_for(lib, (w, h), f0.binning, f0.gain, f0.offset, temp, tol)
    bias_arr = lib.data(bias) if bias is not None else None
    n = len(imgs)

    def load(y0: int, y1: int) -> np.ndarray:
        block = np.empty((n, y1 - y0, w), np.float32)
        for i, img in enumerate(imgs):
            img.strip(y0, y1, out=block[i])
        if bias_arr is not None:
            block -= bias_arr[y0:y1]
        return block

    data = combine_strips(h, w, load, method, sigma, 3, ctx)
    if kind == FLAT:
        med = float(np.median(data[::8, ::8]))
        if med > 0:
            data /= np.float32(med)

    mid = uuid.uuid4().hex[:10]
    exptime = f0.exptime or 0.0
    name = f"{kind}_{fmt_exp(exptime).replace(' ', '')}_g{f0.gain:g}_o{f0.offset:g}_{mid}.fits" \
        if f0.gain is not None and f0.offset is not None else f"{kind}_{mid}.fits"
    header = {k: v for k, v in f0.header.items() if k not in STRUCTURAL}
    header.update({"IMAGETYP": kind.upper(), "NCOMBINE": n, "COMBMETH": method,
                   "BIASSUB": bias is not None})
    if temp is not None:
        header["CCD-TEMP"] = temp
    history = [f"{APP_NAME} master {kind}: {method} of {n} frames"]
    if bias is not None:
        history.append(f"bias master subtracted: {Path(bias.path).name}")
    history += [f"  {f.path.name}" for f in frames]
    from .cache import file_sha256

    rel = master_subdir(f0) / name
    out = write_float32(lib.folder / rel, data, header, history)  # makes <Masters>/<camera>/<year>/
    m = Master(
        id=mid, kind=kind, exptime=exptime, gain=f0.gain, offset=f0.offset, temp=temp,
        size=(w, h), binning=f0.binning, nframes=n,
        date=(f0.start.date().isoformat() if f0.start else ""), created=time.strftime("%Y-%m-%d"),
        path=rel.as_posix(), bias_subtracted=bias is not None, method=method, sha256=file_sha256(out),
    )
    lib.add(m)
    return m


# ---- calibration plan ------------------------------------------------------------------------
@dataclass
class Tolerances:
    gain_rel: float = 0.20
    offset_rel: float = 0.50
    temp_c: float = 3.0
    enabled: bool = True


@dataclass
class LightSettings:
    exptime: float
    gain: float | None
    offset: float | None
    temp: float | None
    size: tuple[int, int]
    binning: tuple[int, int] = (1, 1)
    date: str = ""  # YYYY-MM-DD of the first light: among equal matches the master nearest in time wins

    @classmethod
    def from_frames(cls, frames: list[FrameInfo]) -> LightSettings | None:
        ok = [f for f in frames if not f.error and f.size is not None]
        if not ok:
            return None
        temps = [f.temp for f in ok if f.temp is not None]
        start = min((f.start for f in ok if f.start), default=None)
        return cls(ok[0].exptime or 0.0, ok[0].gain, ok[0].offset,
                   sum(temps) / len(temps) if temps else None, ok[0].size, ok[0].binning,
                   start.date().isoformat() if start else "")


def days_apart(a: str, b: str) -> float:
    """Days between two YYYY-MM-DD dates; unknown dates sort last."""
    from datetime import date

    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except ValueError:
        return 1e9


@dataclass
class CalIssue:
    code: str
    severity: str  # error | warning | info
    text: str
    action: str | None = None  # use_anyway | create_dark | pick_dark | create_bias


@dataclass
class CalPlan:
    dark: Master | None = None
    bias: Master | None = None
    flat: Master | None = None
    mode: str = "none"  # none | exact | scaled | pedestal
    dark_scale: float = 1.0
    gain_scale: float = 1.0
    subtract_bias_level: bool = False
    issues: list[CalIssue] = field(default_factory=list)
    lines: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, state ok|warn|err|off, text)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def paths(self, lib: MasterLibrary) -> list[str]:
        return [str(lib.abspath(m)) for m in (self.dark, self.bias, self.flat) if m is not None]

    def describe(self) -> list[str]:
        out = []
        if self.dark is not None:
            out.append(f"dark {fmt_exp(self.dark.exptime)} ({self.mode}, ×{self.dark_scale:.4g}"
                       + (f", gain ×{self.gain_scale:.3g}" if self.gain_scale != 1 else "") + ")")
        if self.bias is not None and (self.subtract_bias_level or self.mode != "exact"):
            out.append("bias" + (" level subtracted" if self.subtract_bias_level else " (for dark signal)"))
        if self.flat is not None:
            out.append("flat")
        return out


def zwo_gain_ratio(light_gain: float, master_gain: float) -> float:
    """Amplitude ratio light/master for ZWO gain settings in 0.1 dB steps (20·log10 amplitude)."""
    return 10 ** ((light_gain - master_gain) / 200.0)


def _choose(cands: list[Master], choice: str | None, lib: MasterLibrary) -> Master | None:
    if choice and choice not in ("auto", "none"):
        return lib.get(choice)
    return None


def pick_calibration(light: LightSettings, lib: MasterLibrary, tol: Tolerances | None = None,
                     choice: dict[str, str] | None = None, accepted: set[str] | None = None,
                     match_pedestal: bool | None = None, gain_scale: bool = True,
                     use_flat: bool = False, side: str = "Lights") -> CalPlan:
    """Auto-pick dark/bias/flat for a light set and decide how to apply them (SPEC §4)."""
    tol = tol or Tolerances()
    choice = choice or {}
    accepted = accepted or set()
    plan = CalPlan()

    def fits_geom(m: Master) -> bool:
        return m.size == light.size and m.binning == light.binning

    def cam_ok(m: Master) -> bool:
        return not tol.enabled or camera_matches(m, light.gain, light.offset, tol)

    # ---- dark
    darks = [d for d in lib.by_kind(DARK) if fits_geom(d)]
    dark = _choose(darks, choice.get(DARK), lib)
    if dark is None and choice.get(DARK) != "none" and darks:
        pool = [d for d in darks if cam_ok(d)] or darks
        # exposure, then temperature, then the master nearest in time (geometry and camera were filtered above)
        dark = min(pool, key=lambda d: (round(abs(math.log(max(d.exptime, 1e-6) / max(light.exptime, 1e-6))), 6),
                                        round(abs((d.temp or 0) - (light.temp or 0)), 1),
                                        days_apart(d.date, light.date)))
    plan.dark = dark

    # ---- bias: same camera settings as the dark (for D − B), else as the lights
    biases = [b for b in lib.by_kind(BIAS) if fits_geom(b)]
    bias = _choose(biases, choice.get(BIAS), lib)
    if bias is None and choice.get(BIAS) != "none" and biases:
        ref = dark if dark is not None else None
        same = [b for b in biases if ref is not None and b.gain == ref.gain and b.offset == ref.offset]
        pool = same or [b for b in biases if cam_ok(b)] or biases
        bias = min(pool, key=lambda b: (round(abs((b.temp or 0) - (light.temp or 0)), 1),
                                        days_apart(b.date, light.date), -b.nframes))
    plan.bias = bias

    # ---- flat (optional, off by default)
    if use_flat:
        flats = [f for f in lib.by_kind(FLAT) if fits_geom(f)]
        plan.flat = _choose(flats, choice.get(FLAT), lib) or (max(flats, key=lambda f: f.created)
                                                                if flats else None)

    _decide(plan, light, tol, accepted, match_pedestal, gain_scale, side, choice, darks)
    return plan


def _issue(plan: CalPlan, code: str, severity: str, text: str, accepted: set[str],
           action: str | None = None) -> None:
    if severity == "error" and action == "use_anyway" and code in accepted:
        plan.issues.append(CalIssue(code + "_accepted", "warning", text + " (used anyway)"))
    else:
        plan.issues.append(CalIssue(code, severity, text, action))


def _decide(plan: CalPlan, light: LightSettings, tol: Tolerances, accepted: set[str],
            match_pedestal: bool | None, gain_scale: bool, side: str, choice: dict[str, str],
            darks: list[Master]) -> None:
    dark, bias = plan.dark, plan.bias
    if dark is None:
        if choice.get(DARK) == "none":
            plan.lines.append((DARK, "warn", "Dark: none (not recommended; AAVSO wants dark-subtracted)"))
            plan.issues.append(CalIssue("no_dark_chosen", "warning", "No dark subtraction"))
        else:
            near = ", ".join(sorted({fmt_exp(d.exptime) for d in darks})) or "none in the library"
            plan.lines.append((DARK, "err", f"No {fmt_exp(light.exptime)} dark master. Available: {near}."))
            plan.issues.append(CalIssue("no_dark", "error", f"{side} needs a dark", "create_dark"))
        _bias_line(plan, needed=False)
        _flat_line(plan)
        return

    exact = abs(dark.exptime - light.exptime) <= EXP_EQUAL_REL * max(light.exptime, 1e-6)
    has_signal = dark.bias_subtracted or bias is not None
    ratio = light.exptime / dark.exptime if dark.exptime > 0 else 1.0
    cam_mismatch = tol.enabled and not camera_matches(dark, light.gain, light.offset, tol)
    pedestal = cam_mismatch and (match_pedestal is None or match_pedestal)

    if tol.enabled and dark.temp is not None and light.temp is not None \
            and abs(dark.temp - light.temp) > tol.temp_c:
        _issue(plan, "temp_mismatch", "error",
               f"Dark at {dark.temp:.1f} °C, lights at {light.temp:.1f} °C (tolerance ±{tol.temp_c:g} °C)",
               accepted, "use_anyway")

    if pedestal:
        if not has_signal:
            plan.lines.append((DARK, "err", f"Dark {fmt_exp(dark.exptime)} · needs a bias for pedestal match"))
            plan.issues.append(CalIssue(
                "pedestal_needs_bias", "error",
                f"Calibration frames at gain {dark.gain:g}/offset {dark.offset:g}, lights at "
                f"{light.gain:g}/{light.offset:g}: matching the pedestal needs a bias master",
                "create_bias"))
            _bias_line(plan, needed=True)
            _flat_line(plan)
            return
        plan.mode, plan.dark_scale = "pedestal", ratio
        if gain_scale and dark.gain is not None and light.gain is not None:
            plan.gain_scale = zwo_gain_ratio(light.gain, dark.gain)
        plan.issues.append(CalIssue(
            "pedestal_match", "warning",
            f"Calibration frames taken at gain {dark.gain:g}/offset {dark.offset:g}, lights at "
            f"{light.gain:g}/{light.offset:g}: using dark-bias only"))
        scale = "" if exact else f" · scaled ×{ratio:.3g}"
        plan.lines.append((DARK, "warn", f"Dark {fmt_exp(dark.exptime)} · signal only (pedestal match){scale}"))
        _bias_line(plan, needed=not dark.bias_subtracted, note="for dark − bias")
        _flat_line(plan)
        return

    if cam_mismatch:
        _issue(plan, "calib_mismatch", "error",
               f"Dark at gain {dark.gain:g}/offset {dark.offset:g}, lights at "
               f"{light.gain:g}/{light.offset:g}", accepted, "use_anyway")

    if exact:
        plan.mode = "exact"
        plan.subtract_bias_level = dark.bias_subtracted and bias is not None
        if dark.bias_subtracted and bias is None:
            plan.issues.append(CalIssue("no_bias_level", "warning",
                                        "Bias level not removed (no bias master); sky subtraction removes it"))
        plan.lines.append((DARK, "ok", f"Dark {fmt_exp(dark.exptime)} · exact match"))
        _bias_line(plan, needed=dark.bias_subtracted)
    else:
        if not has_signal:
            plan.lines.append((DARK, "err", f"No {fmt_exp(light.exptime)} dark master. Closest is "
                                            f"{fmt_exp(dark.exptime)}; scaling it needs a bias."))
            plan.issues.append(CalIssue(
                "dark_needs_bias", "error",
                f"A {fmt_exp(dark.exptime)} dark can only be used for {fmt_exp(light.exptime)} "
                "lights after subtracting a bias", "create_bias"))
            _bias_line(plan, needed=True)
            _flat_line(plan)
            return
        plan.mode, plan.dark_scale = "scaled", ratio
        plan.subtract_bias_level = bias is not None
        if bias is None:
            plan.issues.append(CalIssue("no_bias_level", "warning",
                                        "Bias level not removed (no bias master); sky subtraction removes it"))
        plan.lines.append((DARK, "ok", f"Dark {fmt_exp(dark.exptime)} · scaled ×{ratio:.3g}"))
        _bias_line(plan, needed=True)
    _flat_line(plan)


def _bias_line(plan: CalPlan, needed: bool, note: str = "") -> None:
    if plan.bias is not None:
        plan.lines.append((BIAS, "ok", "Bias" + (f" · {note}" if note else "")))
    elif needed:
        plan.lines.append((BIAS, "err", "Bias: none in the library"))
    else:
        plan.lines.append((BIAS, "off", "Bias: not needed"))


def _flat_line(plan: CalPlan) -> None:
    plan.lines.append((FLAT, "ok" if plan.flat else "off",
                       "Flat" if plan.flat else "Flat: none (optional)"))


def calibration_frame(plan: CalPlan, lib: MasterLibrary) -> np.ndarray | None:
    """The single frame C with cal = L − C (see module docstring). None = nothing to subtract."""
    dark, bias = plan.dark, plan.bias
    if dark is None or plan.mode == "none":
        return None
    d = lib.data(dark)
    b = lib.data(bias) if bias is not None else None
    if plan.mode == "exact" and not dark.bias_subtracted:
        return d.copy()
    signal = d if dark.bias_subtracted else d - b  # type: ignore[operator]
    c = signal * np.float32(plan.dark_scale * plan.gain_scale)
    if plan.mode != "pedestal" and plan.subtract_bias_level and b is not None:
        c += b
    return c.astype(np.float32, copy=False)
