"""Per-target wavelength adjustment (no Qt).

The dispersion solution comes from the reference star's Balmer lines and is carried to the target. A target
taken at another slit position, or after flexure, sits a few pixels off that solution. The adjustment is
stored per target in the session and defaults to "same as the reference":

* shift: the reference solution moved by `shift` px (set by hand, by dragging a marker or by Auto-match);
* free: fitted from the target's own lines (1-2 lines: shift only, 3-4: linear, 5+: quadratic).

Auto-match cross-correlates the target's telluric bands with the same bands in the reference profile (both
seen through the same atmosphere, and the reference solution places them), plus the target lines picked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import ndimage

from .wavelength import FitResult, Point, Solution, fit, fit_line

# A short list of typical emission/absorption lines of the targets (rest wavelengths, Å, air)
TARGET_LINES: dict[str, float] = {
    "He II 4686": 4685.68, "Hβ": 4861.33, "[O III] 4959": 4958.91, "[O III] 5007": 5006.84,
    "He I 5876": 5875.62, "Hα": 6562.85, "He I 6678": 6678.15, "Raman O VI 6825": 6825.44, "He I 7065": 7065.19,
}
# Telluric windows (Å): the band and some continuum each side
TELLURIC_WINDOWS: tuple[tuple[str, float, float], ...] = (
    ("O2 B", 6850.0, 6950.0), ("H2O 7168", 7150.0, 7330.0), ("O2 A", 7580.0, 7715.0))
SEARCH_PX = 40.0
MIN_BAND_DEPTH = 0.04  # a band shallower than this (normalised) is not visible
MIN_CORR = 0.6
MAX_SPREAD_PX = 1.5  # features disagreeing by more than this: not reliable


@dataclass
class Adjust:
    mode: str = "shift"  # shift | free
    shift: float = 0.0
    source: str = ""  # manual | marker | auto-match | free fit ("" = same as the reference)
    lines: list[str] = field(default_factory=list)
    custom: dict[str, float] = field(default_factory=dict)  # free entry: name → Å
    manual: dict[str, float] = field(default_factory=dict)  # free fit: dragged marker px
    ref_hb: float | None = None  # Hβ column of the reference solution the shift was set against

    @classmethod
    def from_params(cls, d: dict[str, Any] | None) -> Adjust:
        d = d or {}
        return cls(str(d.get("mode", "shift")), float(d.get("shift", 0.0)), str(d.get("source", "")),
                   list(d.get("lines", [])), {k: float(v) for k, v in (d.get("custom") or {}).items()},
                   {k: float(v) for k, v in (d.get("manual") or {}).items()},
                   None if d.get("ref_hb") is None else float(d["ref_hb"]))

    def to_params(self) -> dict[str, Any]:
        out = {"mode": self.mode, "shift": round(self.shift, 3), "source": self.source, "lines": list(self.lines),
               "custom": dict(self.custom), "manual": dict(self.manual)}
        if self.ref_hb is not None:
            out["ref_hb"] = round(self.ref_hb, 2)
        return out

    def stale_for(self, ref: Solution, tol_px: float = 2.0) -> bool:
        """A manual shift set against another reference solution (or before that was recorded) must not be carried
        over silently: the shift is relative to the reference solution."""
        if self.mode != "shift" or not self.shift:
            return False
        return self.ref_hb is None or abs(float(ref.pixel(4861.33)) - self.ref_hb) > tol_px

    @property
    def is_reference(self) -> bool:
        return self.mode == "shift" and self.shift == 0.0

    def wavelengths(self) -> dict[str, float]:
        """Picked lines with their rest wavelengths (known list plus free entries)."""
        table = {**TARGET_LINES, **self.custom}
        return {n: table[n] for n in self.lines if n in table}


def flattened(profile: np.ndarray, width: int = 151) -> np.ndarray:
    """Profile / running median − 1: emission positive, absorption negative, continuum 0."""
    p = np.asarray(profile, dtype=np.float64)
    med = ndimage.median_filter(p, width, mode="nearest")
    floor = max(float(np.percentile(np.abs(p), 99)) * 1e-3, 1e-9)
    return p / np.maximum(med, floor) - 1.0


def measure_line(flat: np.ndarray, guess: float, half: int = 12) -> tuple[float, bool]:
    """Centre (px) of the line nearest `guess`, emission or absorption, whichever is stronger there. The
    segment is scaled to its own peak first: emission lines of a symbiotic star reach 50× the continuum."""
    lo, hi = max(0, int(guess) - half), min(len(flat), int(guess) + half + 1)
    if hi - lo < 7:
        return guess, False
    seg = flat[lo:hi]
    emission = float(seg.max() - np.median(seg)) >= float(np.median(seg) - seg.min())
    amp = float(seg.max() - np.median(seg)) if emission else float(np.median(seg) - seg.min())
    if amp <= 0:
        return guess, False
    scaled = (flat - float(np.median(seg))) / amp * 0.5 + 1.0  # peak at ±0.5 around 1, inside fit_line's bounds
    lf = fit_line(scaled, guess, emission, half=half)
    noise = float(np.std(np.diff(flat[max(0, lo - 40):lo]))) / np.sqrt(2) if lo > 10 else 0.0
    ok = lf.ok and (noise == 0.0 or amp > 5 * noise)
    return lf.centre, ok


def target_points(prof: np.ndarray, base: Solution, adj: Adjust) -> list[Point]:
    flat = flattened(prof)
    pts = []
    for name, lam in adj.wavelengths().items():
        if name in adj.manual:
            c, ok = measure_line(flat, adj.manual[name], half=6)
            pts.append(Point(name, lam, c if ok else adj.manual[name], 0.0, True))
            continue
        guess = base.pixel(lam)
        if not 0 <= guess < len(prof):
            continue
        c, ok = measure_line(flat, guess)
        if ok:
            pts.append(Point(name, lam, c))
    return pts


@dataclass
class TargetCal:
    solution: Solution
    mode: str  # reference | shift | free
    shift_px: float  # offset from the reference solution at the middle of the profile
    source: str
    points: list[Point]
    fit: FitResult | None
    messages: list[str] = field(default_factory=list)


def _errors(sol: Solution, pts: list[Point]) -> FitResult | None:
    if not pts:
        return None
    lam = np.array([p.lam_a for p in pts])
    err = np.asarray(sol.angstrom(np.array([p.pixel for p in pts]))) - lam
    return FitResult(sol, err, float(np.sqrt(np.sum(err ** 2))), float(np.sqrt(np.mean(err ** 2))))


def free_order(n: int) -> int:
    """Shift only with 1-2 lines (order 0), linear with 3-4, quadratic with 5 or more."""
    return 0 if n < 3 else 1 if n < 5 else 2


def offset_px(ref: Solution, sol: Solution, n: int) -> float:
    """How far `sol` sits from `ref` (px) in the middle of the profile."""
    x = (n - 1) / 2
    return float(sol.pixel(float(ref.angstrom(x))) - x)


def target_calibration(ref: Solution, prof: np.ndarray, adj: Adjust) -> TargetCal:
    base = ref.shifted(adj.shift) if adj.shift else ref
    msgs: list[str] = []
    if adj.mode != "free":
        pts = target_points(prof, base, adj)
        mode = "reference" if adj.is_reference else "shift"
        return TargetCal(base, mode, adj.shift, adj.source if adj.shift else "", pts, _errors(base, pts), msgs)
    pts = target_points(prof, base, adj)
    if not pts:
        msgs.append("No target line measured: the shift alone is used")
        return TargetCal(base, "shift", adj.shift, adj.source, [], None, msgs)
    order = free_order(len(pts))
    if order == 0:
        s = float(np.median([p.pixel - ref.pixel(p.lam_a) for p in pts]))
        sol = ref.shifted(s)
    else:
        sol = fit(np.array([p.pixel for p in pts]), np.array([p.lam_a for p in pts]), order).solution
        if order == 1 and ref.order == 2:
            sol = Solution([*sol.coeffs_nm, 0.0])
    return TargetCal(sol, "free", offset_px(ref, sol, len(prof)), "free fit", pts, _errors(sol, pts), msgs)


# ---- Auto-match ---------------------------------------------------------------------------------------------
@dataclass
class Feature:
    name: str
    shift_px: float
    quality: float  # correlation (bands) or 1 (lines)
    kind: str  # telluric | line


@dataclass
class Match:
    shift_px: float | None
    err_px: float | None
    shift_a: float | None
    err_a: float | None
    features: list[Feature]
    reliable: bool
    reason: str

    def text(self) -> str:
        if self.shift_px is None:
            return self.reason
        names = ", ".join(f"{f.name} {f.shift_px:+.1f}" for f in self.features)
        return (f"{self.shift_px:+.2f} ± {self.err_px:.2f} px ({self.shift_a:+.2f} ± {self.err_a:.2f} Å) from "
                f"{names}" + ("" if self.reliable else f". Not reliable: {self.reason}"))


def _band_shift(ref_flat: np.ndarray, tgt_flat: np.ndarray, lo: int, hi: int) -> tuple[float, float]:
    """(shift, correlation): the target's band sits `shift` px from the reference's."""
    x = np.arange(lo, hi, dtype=np.float64)
    r = ref_flat[lo:hi] - ref_flat[lo:hi].mean()
    xt = np.arange(len(tgt_flat), dtype=np.float64)
    shifts = np.arange(-SEARCH_PX, SEARCH_PX + 1e-9, 0.1)
    cc = np.empty(len(shifts))
    for k, s in enumerate(shifts):
        t = np.interp(x + s, xt, tgt_flat)
        t = t - t.mean()
        den = float(np.sqrt(np.dot(t, t) * np.dot(r, r)))
        cc[k] = float(np.dot(t, r)) / den if den > 0 else 0.0
    k = int(np.argmax(cc))
    s = float(shifts[k])
    if 0 < k < len(cc) - 1:  # parabola through the peak
        a, b, c = cc[k - 1], cc[k], cc[k + 1]
        d = a - 2 * b + c
        if d < 0:
            s += 0.1 * 0.5 * (a - c) / d
    return s, float(cc[k])


def auto_match(ref_prof: np.ndarray, ref: Solution, tgt_prof: np.ndarray, adj: Adjust) -> Match:
    """Estimate the target's shift from the reference solution (px; Å at the middle of the profile)."""
    n = len(tgt_prof)
    rf, tf = flattened(ref_prof), flattened(tgt_prof)
    feats: list[Feature] = []
    skipped: list[str] = []
    for name, a0, a1 in TELLURIC_WINDOWS:
        lo, hi = round(ref.pixel(a0)), round(ref.pixel(a1))
        lo, hi = max(lo, int(SEARCH_PX) + 1), min(hi, n - int(SEARCH_PX) - 1)
        if hi - lo < 20:
            continue
        if -float(rf[lo:hi].min()) < MIN_BAND_DEPTH or -float(tf[max(0, lo - 40):hi + 40].min()) < MIN_BAND_DEPTH:
            skipped.append(f"{name} not visible")
            continue
        s, c = _band_shift(rf, tf, lo, hi)
        if c < MIN_CORR or abs(s) >= SEARCH_PX - 0.5:
            skipped.append(f"{name} does not match (correlation {c:.2f})")
            continue
        feats.append(Feature(name, s, c, "telluric"))
    flat_pts = target_points(tgt_prof, ref, Adjust(lines=adj.lines, custom=adj.custom))
    for p in flat_pts:  # picked target lines: where they are against the reference solution
        feats.append(Feature(p.line, p.pixel - ref.pixel(p.lam_a), 1.0, "line"))
    disp = ref.dispersion_a((n - 1) / 2)
    if not feats:
        reason = "no usable feature: " + ("; ".join(skipped) if skipped else "no telluric band in range and no "
                                                                             "target lines picked")
        return Match(None, None, None, None, [], False, reason)
    v = np.array([f.shift_px for f in feats])
    est = float(np.median(v))
    spread = float(np.std(v)) if len(v) > 1 else float("nan")
    err = max(spread / np.sqrt(len(v)), 0.1) if len(v) > 1 else 1.0
    reliable, reason = True, ""
    if len(v) < 2:
        reliable, reason = False, f"only one feature ({feats[0].name})"
    elif spread > MAX_SPREAD_PX:
        reliable, reason = False, f"the features disagree (spread {spread:.1f} px)"
    return Match(est, err, est * disp, err * abs(disp), feats, reliable, reason)
