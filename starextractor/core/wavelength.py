"""Step 4 · wavelength calibration from Balmer lines, with BASS-compatible coefficients.

Solutions are polynomials in nm of the 0-based detector column (PIXEL_ORIGIN = 0, as in BASS):
f(x) = a + b·x (+ c·x²). Internally wavelengths are Å; the BASS box and export are nm.
"""
from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import ndimage, optimize

PIXEL_ORIGIN = 0
# Air wavelengths in Å. Hα/Hβ/Hγ are BASS's own values (docs/BASS_CALIBRATION.md §2);
# Hδ/Hε are the standard air values (BASS's unverified).
BALMER: dict[str, float] = {"Hα": 6562.852, "Hβ": 4861.33, "Hγ": 4340.47, "Hδ": 4101.74, "Hε": 3970.07}
OPTIONAL_LINES = {"Hε"}  # faint blue end of hot stars
MAX_ORDER = 3  # Linear, Quadratic or Cubic (cubic only on request, 5+ lines)
FIT_HALF_WINDOW = 12  # px around a predicted line for the centre fit


# ---- solutions --------------------------------------------------------------------------------
@dataclass
class Solution:
    coeffs_nm: list[float]  # offset first

    @property
    def order(self) -> int:
        return len(self.coeffs_nm) - 1

    def nm(self, x: np.ndarray | float) -> np.ndarray | float:
        xx = np.asarray(x, dtype=np.float64) - PIXEL_ORIGIN
        return sum(c * xx ** k for k, c in enumerate(self.coeffs_nm))

    def angstrom(self, x: np.ndarray | float) -> np.ndarray | float:
        return np.asarray(self.nm(x)) * 10.0

    def pixel(self, lam_a: float) -> float:
        """Inverse: detector column of a wavelength (Å)."""
        target = lam_a / 10.0
        if self.order == 1 or all(c == 0 for c in self.coeffs_nm[2:]):
            a, b = self.coeffs_nm[:2]
            return (target - a) / b + PIXEL_ORIGIN
        if self.order == 2:
            a, b, c = self.coeffs_nm
            disc = b * b - 4 * c * (a - target)
            return (-b + math.sqrt(disc)) / (2 * c) + PIXEL_ORIGIN
        return self._pixel_newton(target)

    def _pixel_newton(self, target_nm: float) -> float:
        """Cubic: Newton from the linear guess; ValueError when the curve never reaches the wavelength."""
        cc = np.asarray(self.coeffs_nm, dtype=np.float64)
        d = np.polynomial.polynomial.polyder(cc)
        u = (target_nm - cc[0]) / cc[1]
        for _ in range(50):
            f = np.polynomial.polynomial.polyval(u, cc) - target_nm
            g = np.polynomial.polynomial.polyval(u, d)
            if g == 0 or not math.isfinite(g):
                raise ValueError("solution not invertible here")
            step = f / g
            u -= step
            if abs(step) < 1e-9:
                if abs(u) > 1e6:
                    raise ValueError("wavelength out of reach")
                return float(u) + PIXEL_ORIGIN
        raise ValueError("inverse did not converge")

    def dispersion_a(self, x: float) -> float:
        """Å per pixel at column x."""
        d = np.polynomial.polynomial.polyder(np.asarray(self.coeffs_nm, dtype=np.float64))
        return 10.0 * float(np.polynomial.polynomial.polyval(x - PIXEL_ORIGIN, d))

    def shifted(self, s: float) -> Solution:
        """Same curve moved by s pixels: g(x) = f(x − s)."""
        if self.order == 1:
            a, b = self.coeffs_nm
            return Solution([a - b * s, b])
        if self.order == 2:
            a, b, c = self.coeffs_nm
            return Solution([a - b * s + c * s * s, b - 2 * c * s, c])
        out = np.zeros(len(self.coeffs_nm))
        for k, c in enumerate(self.coeffs_nm):  # c (u − s)^k expanded
            out[: k + 1] += c * np.array([math.comb(k, j) * (-s) ** (k - j) for j in range(k + 1)])
        return Solution([float(v) for v in out])


def format_coeffs(coeffs: list[float]) -> str:
    """BASS box layout: full double precision, ', ' separated, offset first, 'E-08' style."""
    return ", ".join(repr(float(c)).replace("e", "E") for c in coeffs)


def parse_coeffs(text: str) -> list[float]:
    """2, 3 or 4 numbers separated by commas, spaces or semicolons; any exponent notation."""
    parts = [p for p in re.split(r"[,;\s]+", text.strip().replace("−", "-")) if p]
    if len(parts) not in (2, 3, 4):
        raise ValueError(f"expected 2, 3 or 4 coefficients (offset first, nm), got {len(parts)}")
    try:
        vals = [float(p.replace("D", "E").replace("d", "e")) for p in parts]
    except ValueError as exc:
        raise ValueError(f"not a number: {exc}") from exc
    if not (100 < vals[0] < 2000 and 0 < vals[1] < 5):
        raise ValueError("these do not look like nm coefficients (offset 100–2000 nm, dispersion 0–5 nm/px)")
    return vals


def max_order(n_lines: int, allow_exact: bool = False) -> int:
    """The highest order offered: never above (lines − 2); 2 lines → linear; cubic from 5 lines. With 3 lines a
    quadratic only fits exactly, so it is offered only on explicit request (`allow_exact`)."""
    if n_lines < 2:
        return 0
    if n_lines == 3 and allow_exact:
        return 2
    return max(1, min(MAX_ORDER, n_lines - 2))


def default_order(n_lines: int, allow_exact: bool = False) -> int:
    """The order used when none is chosen: at most quadratic (cubic only on request)."""
    return min(2, max_order(n_lines, allow_exact))


@dataclass
class FitResult:
    solution: Solution
    errors_a: np.ndarray  # fit − entered, Å
    total_a: float
    rms_a: float


def fit(px: np.ndarray, lam_a: np.ndarray, order: int) -> FitResult:
    px = np.asarray(px, dtype=np.float64)
    lam_a = np.asarray(lam_a, dtype=np.float64)
    if len(px) < order + 1:
        raise ValueError(f"order {order} needs at least {order + 1} lines")
    coeffs = np.polynomial.polynomial.polyfit(px - PIXEL_ORIGIN, lam_a / 10.0, order)
    sol = Solution([float(c) for c in coeffs])
    err = np.asarray(sol.angstrom(px)) - lam_a
    return FitResult(sol, err, float(np.sqrt(np.sum(err ** 2))), float(np.sqrt(np.mean(err ** 2))))


# ---- line measurement -------------------------------------------------------------------------
def continuum(profile: np.ndarray, width: int = 151) -> np.ndarray:
    """Smooth upper envelope (absorption lines removed) for normalising."""
    p = np.asarray(profile, dtype=np.float64)
    env = ndimage.maximum_filter1d(ndimage.median_filter(p, 9), width // 3)
    env = ndimage.uniform_filter1d(ndimage.median_filter(env, width), width // 2)
    floor = max(float(np.percentile(np.abs(p), 99)) * 1e-3, 1e-9)
    return np.maximum(env, floor)


def normalised(profile: np.ndarray) -> np.ndarray:
    return np.asarray(profile, dtype=np.float64) / continuum(profile)


def _gauss(x: np.ndarray, amp: float, mu: float, sigma: float, base: float, slope: float) -> np.ndarray:
    return base + slope * (x - mu) + amp * np.exp(-0.5 * ((x - mu) / sigma) ** 2)


@dataclass
class LineFit:
    centre: float
    sigma: float
    depth: float  # amplitude in normalised units (negative = absorption)
    ok: bool
    err: float = float("nan")  # 1σ of the centre from the fit covariance (scaled by the residuals), px
    base: float = 1.0
    slope: float = 0.0


def fit_line(norm: np.ndarray, guess: float, emission: bool = False, half: int = FIT_HALF_WINDOW) -> LineFit:
    """Gaussian + linear background in a window around `guess` on the normalised profile."""
    lo, hi = max(0, int(guess) - half), min(len(norm), int(guess) + half + 1)
    if hi - lo < 7:
        return LineFit(guess, 0.0, 0.0, False)
    x = np.arange(lo, hi, dtype=np.float64)
    y = norm[lo:hi]
    k = int(np.argmax(y) if emission else np.argmin(y))
    amp0 = float(y[k] - np.median(y))
    try:
        p, cov = optimize.curve_fit(_gauss, x, y, p0=[amp0, x[k], 2.5, float(np.median(y)), 0.0],
                                    bounds=([-10, lo, 0.5, -10, -1], [10, hi - 1, half, 10, 1]), maxfev=4000)
    except (RuntimeError, ValueError):
        return LineFit(guess, 0.0, 0.0, False)
    amp, mu, sig = float(p[0]), float(p[1]), float(p[2])
    noise = float(np.std(np.diff(norm[max(0, lo - 30):hi + 30]))) / math.sqrt(2) or 1e-3
    ok = (amp > 0) == emission and abs(amp) > 3 * noise and lo + 1 < mu < hi - 2 and sig < half * 0.8
    err = float(np.sqrt(cov[1, 1])) if cov is not None and np.isfinite(cov[1, 1]) and cov[1, 1] >= 0 else float("nan")
    return LineFit(mu, sig, amp, ok, err, float(p[3]), float(p[4]))


def coarse_shift(norm: np.ndarray, predicted: dict[str, float], emission: bool = False,
                 search: int = 80, sigma: float = 3.0) -> float:
    """Shift (px) that best lines a comb of predicted lines up with the profile."""
    x = np.arange(len(norm), dtype=np.float64)
    signal = (norm - 1.0) * (1 if emission else -1)
    signal = np.clip(signal, 0, None)
    best, best_s = -np.inf, 0.0
    for s in np.arange(-search, search + 0.5, 0.5):
        model = np.zeros_like(x)
        for px in predicted.values():
            c = px + s
            if 0 <= c < len(x):
                lo, hi = int(max(0, c - 4 * sigma)), int(min(len(x), c + 4 * sigma + 1))
                model[lo:hi] += np.exp(-0.5 * ((x[lo:hi] - c) / sigma) ** 2)
        score = float(np.dot(model, signal))
        if score > best:
            best, best_s = score, float(s)
    return best_s


@dataclass
class Point:
    line: str
    lam_a: float
    pixel: float
    sigma_px: float = 0.0  # Gaussian-equivalent width (FWHM / 2.3548), px: for the resolution
    manual: bool = False
    err_px: float = float("nan")  # 1σ uncertainty of the centre, px (NaN: not measured, e.g. "my position")
    fwhm_px: float = 0.0
    flags: list[str] = field(default_factory=list)
    weight: float = 1.0  # relative weight in the fit (blends get less, emission cores none)
    rejected: str = ""  # e.g. "rejected: 4.1 σ" (left out of the fit, still shown)
    guess: float = float("nan")
    fit_x: list[float] | None = None  # the fitted profile (overlay): window columns and curve (profile units)
    fit_y: list[float] | None = None

    @property
    def used(self) -> bool:
        return not self.rejected and self.weight > 0


@dataclass
class Calibration:
    mode: str  # refine | free | fixed
    solution: Solution
    points: list[Point]
    fit: FitResult | None
    shift_px: float = 0.0
    fwhm_a: float | None = None
    resolving_power: float | None = None
    fwhm_line: str = "Hβ"
    messages: list[str] = field(default_factory=list)
    status: str = "ok"  # ok | failed
    reason: str = ""  # why it failed
    mode_reason: str = ""  # one line: why this mode was chosen automatically
    prior: dict[str, Any] = field(default_factory=dict)  # the saved solution's check
    search: dict[str, Any] = field(default_factory=dict)  # the pattern search: best, second, ambiguous
    unmatched: dict[str, str] = field(default_factory=dict)  # line → why it has no point
    fits: dict[str, dict[str, Any]] = field(default_factory=dict)  # "shift" / "free" → rms, n, order (labelled)
    centre_method: str = "auto"
    repaired: list[str] = field(default_factory=list)  # dragged markers ignored for breaking the wavelength order


# ---- the calibration ----------------------------------------------------------------------------
ALL_LINES: dict[str, float] = {**BALMER, "Hζ": 3889.049, "Hη": 3835.384}  # Hζ/Hη: optional anchors (A stars)
EXTRA_LINES = ("Hζ", "Hη")
CENTRE_METHODS = ("auto", "voigt", "gauss", "lorentz", "minimum", "v01")
MIN_GAP_PX = 15.0  # markers never come closer than this to a neighbour (Part B)
MATCH_TOL_PX = 25.0  # a line needs a dip this close to where it is expected
DISP_TOL = 0.03  # the pattern search's dispersion may differ this much from the saved solution's
QUALITY_RMS_A = 3.0  # above this (or 0.25 × FWHM) the solution is not a calibration: the step fails
CENTRE_FLOOR_PX = 0.15  # systematic floor added to every centre uncertainty (blends, RV, pixel phase)
REJECT_SIGMA = 3.0
MODEL_FLOOR_A = 1.0  # a quadratic matches the Star'Ex dispersion to about ±1 Å only (investigation 2025-10-02):
# a line is an outlier only beyond 3σ of that, never because the polynomial cannot follow it
LOW_WEIGHT = 0.3  # blends


def _ordered(names: list[str]) -> list[str]:
    return sorted(names, key=lambda n: ALL_LINES[n])


def check_order(pixels: dict[str, float], min_gap: float = MIN_GAP_PX) -> list[str]:
    """Lines whose pixel breaks the wavelength order (bluer line at a larger column, or closer than min_gap)."""
    names = _ordered([n for n in pixels if np.isfinite(pixels[n])])
    bad = []
    for a, b in itertools.pairwise(names):
        if pixels[b] - pixels[a] < min_gap:
            bad.append(b)
    return bad


def repair_manual(manual: dict[str, float], guesses: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    """Dragged markers that are out of wavelength order (a saved state from before the order rule) are dropped,
    the automatic position takes their place, and the message says so."""
    manual = {k: v for k, v in manual.items() if k in ALL_LINES}
    msgs = []
    while True:
        merged = {**guesses, **manual}
        bad = check_order(merged)
        culprit = next((n for n in bad if n in manual), None)
        if culprit is None:
            names = _ordered(list(merged))
            # the bluer neighbour may be the dragged one
            for n in bad:
                i = names.index(n)
                if i and names[i - 1] in manual:
                    culprit = names[i - 1]
                    break
        if culprit is None:
            return manual, msgs
        msgs.append(f"{culprit} marker (px {manual[culprit]:.1f}) was out of wavelength order: ignored")
        manual = {k: v for k, v in manual.items() if k != culprit}


def _measure_one(profile: np.ndarray, norm: np.ndarray, name: str, guess: float, fwhm_px: float, method: str,
                 manual: bool, nofit: bool) -> Point | None:
    """One line centre. auto (the default, chosen by the study in docs/investigations): M0, the v0.1 Gaussian on the
    normalised profile, with hot pixels masked and its window re-centred, so a marker's exact drop point no longer
    matters. The profile fit (pseudo-Voigt × continuum) only diagnoses: an emission core leaves the line out, a blend
    lowers its weight. voigt / gauss / lorentz / minimum: the other methods of the study (developer setting); v01:
    exactly v0.1 (±6 px around a dragged marker), for comparison. The centre never moves more than one FWHM from
    the guess: then a marker stays where it was put (with a warning) and an automatic line is not found."""
    from . import centroid as cz

    lam = ALL_LINES[name]
    if nofit:
        return Point(name, lam, guess, fwhm_px / 2.3548, True, float("nan"), fwhm_px, ["my position"], guess=guess)
    if method == "v01":
        lf = fit_line(norm, guess, False, half=6 if manual else FIT_HALF_WINDOW)
        if not lf.ok and not manual:
            return None
        return Point(name, lam, lf.centre if lf.ok else guess, lf.sigma, manual, lf.err if lf.ok else float("nan"),
                     2.3548 * lf.sigma, [] if lf.ok else ["fit failed: marker kept"], guess=guess)
    if method == "auto":
        c = cz.m0_line(profile, guess, fwhm_px, norm=norm)
    elif method == "minimum":
        c = cz.m2_parabola(profile, guess, 0.5 * fwhm_px, 7)
        c.fwhm = fwhm_px
    else:
        c = cz.fit_line_centre(profile, guess, fwhm_px, model=method)
    width = c.fwhm if c.ok and c.fwhm > 0 else fwhm_px
    if c.ok and abs(c.centre - guess) > max(width, 6.0):
        c.ok = False
        c.flags.append(cz.WANDERED)
    if not c.ok:
        if manual:  # the marker stays where it was put; the line says why
            return Point(name, lam, guess, fwhm_px / 2.3548, True, float("nan"), fwhm_px,
                         ["fit failed: marker kept", *c.flags], guess=guess)
        return None
    pt = Point(name, lam, c.centre, c.fwhm / 2.3548, manual, c.sigma, c.fwhm, list(c.flags), guess=guess)
    if c.x is not None and c.curve is not None:
        pt.fit_x = [round(float(v), 2) for v in c.x]
        pt.fit_y = [float(v) for v in c.curve]
    diag = c if method != "auto" else cz.fit_line_centre(profile, c.centre, max(c.fwhm, 6.0))
    flags = [f for f in diag.flags if f in (cz.EMISSION_CORE, cz.BLEND, cz.POOR_FIT, cz.LOW_SN)]
    pt.flags = list(dict.fromkeys([*pt.flags, *flags]))
    if cz.EMISSION_CORE in pt.flags:
        pt.weight = 0.0
        pt.rejected = "emission core: left out"
    elif cz.BLEND in pt.flags:
        pt.weight = LOW_WEIGHT
    if name == "Hε":  # Ca II H 3968.47 sits 1.6 Å from Hε: a blend at R ≈ 360 whatever the fit says
        pt.flags = list(dict.fromkeys([*pt.flags, "blend: Ca II H 3968"]))
        pt.weight = min(pt.weight, LOW_WEIGHT) if pt.weight else 0.0
    return pt


def _sigma_a(p: Point, disp: float) -> float:
    e = p.err_px if np.isfinite(p.err_px) else 1.0  # "my position": a pixel
    return math.hypot(e, CENTRE_FLOOR_PX) * abs(disp) / math.sqrt(max(p.weight, 1e-3))


def _weighted_fit(pts: list[Point], order: int, disp: float) -> FitResult:
    px = np.array([p.pixel for p in pts])
    lam = np.array([p.lam_a for p in pts])
    w = 1.0 / np.array([_sigma_a(p, disp) for p in pts])
    coeffs = np.polynomial.polynomial.polyfit(px - PIXEL_ORIGIN, lam / 10.0, order, w=w)
    sol = Solution([float(c) for c in coeffs])
    err = np.asarray(sol.angstrom(px)) - lam
    return FitResult(sol, err, float(np.sqrt(np.sum(err ** 2))), float(np.sqrt(np.mean(err ** 2))))


def _reject_outlier(pts: list[Point], order: int, disp: float) -> None:
    """At most one INTERIOR line. The candidate is the line whose removal leaves the most consistent fit (a wrong line
    spoils the others' predictions too, so the largest single residual can point at a good line); it is rejected when
    the others predict it beyond 3σ of its own uncertainty and their scatter (at least MODEL_FLOOR_A: the dispersion is
    not exactly a quadratic). The end lines are never judged this way: leaving Hα out means extrapolating 1400 px."""
    use = sorted((p for p in pts if p.used), key=lambda q: q.lam_a)
    if len(use) < order + 3:
        return
    best = None
    for p in use[1:-1]:
        rest = [q for q in use if q is not p]
        fr = _weighted_fit(rest, order, disp)
        scatter = float(np.sqrt(np.mean(fr.errors_a ** 2)))
        if best is None or scatter < best[1]:
            best = (p, scatter, fr)
    p, scatter, fr = best
    z = abs(float(fr.solution.angstrom(p.pixel)) - p.lam_a) / math.hypot(_sigma_a(p, disp),
                                                                        max(MODEL_FLOOR_A, scatter))
    if z > REJECT_SIGMA:
        p.rejected = f"rejected: {z:.1f} σ"


def monotonic(sol: Solution, n: int) -> bool:
    return bool(np.all(np.diff(np.asarray(sol.angstrom(np.linspace(0.0, n - 1.0, 256)))) > 0))


def calibrate(profile: np.ndarray, saved: Solution, mode: str = "refine", lines: list[str] | None = None,
              order: int | None = None, manual: dict[str, float] | None = None, emission: bool = False,
              allow_exact: bool = False, fixed: Solution | None = None, *, centre: str = "auto",
              nofit: list[str] | tuple[str, ...] = (), mode_user: bool = True, prior_source: str = "") -> Calibration:
    """The reference star's dispersion solution.

    Line positions come from a blind Balmer pattern search (core/balmer_search.py); the saved solution is only a
    prior, used when `validate_prior` says it fits this night. refine: the saved shape moved by the median line
    shift; free: fitted from the line centres; fixed: pasted coefficients used as they are. With mode_user False
    (nobody chose), refine turns into free when the prior does not fit or the free fit is much better.
    A solution whose RMS is above QUALITY_RMS_A (or 0.25 × FWHM), or that is not monotonic, has status "failed"."""
    from . import balmer_search as bs

    if emission:
        return _calibrate_emission(profile, saved, mode, lines, order, manual, allow_exact, fixed)
    n = len(profile)
    names = _ordered([k for k in (lines or list(BALMER)) if k in ALL_LINES])
    norm = normalised(profile)
    msgs: list[str] = []
    manual = {k: float(v) for k, v in (manual or {}).items() if k in names}
    search = bs.search(profile)
    best = search.best if search.best is not None and not search.ambiguous else None
    base = fixed if mode == "fixed" and fixed is not None else saved
    if best is not None:  # the offset moves between nights, the dispersion (grating, camera) does not
        d_best, d_base = best.dispersion, float(base.dispersion_a(n / 2))
        if abs(d_best / d_base - 1.0) > DISP_TOL:
            search.messages.append(f"the Balmer pattern found ({best.describe()}) has a dispersion {d_best:.3f} Å/px, "
                                   f"{abs(d_best / d_base - 1):.0%} off the saved solution's {d_base:.3f} Å/px")
            best = None
    prior = bs.validate_prior(base, profile, search.dips, source=prior_source, found_by=best)
    info = {"best": search.best.describe() if search.best else "", "ambiguous": search.ambiguous,
            "second": search.second.describe() if search.second else "", "messages": list(search.messages)}
    cal_prior = {"ok": prior.ok, "message": prior.message, "offset_px": prior.median_offset, "source": prior_source,
                 "found": prior.found, "total": prior.total}

    # 1. where to look for each line
    guess_sol: Solution | None = None
    if mode == "fixed":
        guess_sol = base
    elif best is not None:
        guess_sol = best.solution
    elif prior.ok:
        pred = {k: float(base.pixel(ALL_LINES[k])) for k in names}
        guess_sol = base.shifted(coarse_shift(norm, pred))
    widths = {d.x: d.width for d in search.dips}
    guesses: dict[str, float] = {}
    fwhm0: dict[str, float] = {}
    unmatched: dict[str, str] = {}
    for k in names:
        if guess_sol is None:
            continue
        pred = bs.pixel_of(guess_sol, ALL_LINES[k])
        if not np.isfinite(pred) or not 0 <= pred < n:
            unmatched[k] = f"outside the detector (expected at x={pred:.0f})" if np.isfinite(pred) else \
                "outside the detector"
            continue
        if best is not None and k in best.matched and mode != "fixed":
            guesses[k] = best.matched[k]
        else:
            near = [d for d in search.dips if abs(d.x - pred) <= MATCH_TOL_PX and d.width >= bs.LINE_WIDTH]
            if not near and k not in manual:
                unmatched[k] = f"no dip within {MATCH_TOL_PX:.0f} px of predicted x={pred:.0f}"
                continue
            guesses[k] = min(near, key=lambda d: abs(d.x - pred)).x if near else pred
        fwhm0[k] = float(np.clip(widths.get(guesses[k], 12.0), 5.0, 30.0))
    kept, fixmsgs = repair_manual(manual, guesses)
    repaired = [k for k in manual if k not in kept]
    manual = kept
    msgs += fixmsgs
    for k, v in manual.items():
        unmatched.pop(k, None)
        guesses[k] = v
        fwhm0.setdefault(k, 12.0)

    def failed(reason: str, sol: Solution | None = None, pts: list[Point] | None = None) -> Calibration:
        cal = Calibration(mode, sol or base, pts or [], None, 0.0, messages=msgs, status="failed", reason=reason,
                          prior=cal_prior, search=info, unmatched=unmatched, centre_method=centre)
        return cal

    if guess_sol is None and len(manual) < 2:
        if search.ambiguous:
            return failed("two Balmer patterns fit almost equally well; drag the Hβ and Hα markers onto their lines "
                          f"({info['best']} | {info['second']})")
        why = search.messages[0] if search.messages else "no Balmer pattern found"
        return failed(f"{why}, and {prior.message[0].lower() + prior.message[1:]}; drag the Hβ and Hα markers "
                      "onto their lines")

    # 2. the centres
    pts: list[Point] = []
    for k in _ordered(list(guesses)):
        p = _measure_one(profile, norm, k, guesses[k], fwhm0[k], centre, k in manual, k in nofit and k in manual)
        if p is None:
            unmatched[k] = f"no line could be fitted near x={guesses[k]:.0f}"
            continue
        pts.append(p)
    # the centres must keep the wavelength order (a fit may slide onto the neighbour's wing)
    for k in check_order({p.line: p.pixel for p in pts}, 1.0):
        p = next(q for q in pts if q.line == k)
        p.rejected = "out of wavelength order: left out"
    missing = [k for k in names if k in unmatched and k not in OPTIONAL_LINES and k not in EXTRA_LINES]
    if missing:
        msgs.append("Not found: " + ", ".join(f"{k} ({unmatched[k]})" for k in missing))

    use = [p for p in pts if p.used]
    disp = guess_sol.dispersion_a(n / 2) if guess_sol is not None else 1.2
    fits: dict[str, dict[str, Any]] = {}
    shift_sol = None
    if mode != "fixed" and use:
        shift = float(np.median([p.pixel - base.pixel(p.lam_a) for p in use]))
        shift_sol = base.shifted(shift)
        e = _errors(shift_sol, use)
        fits["shift"] = {"label": "Shift only", "rms": e.rms_a, "n": len(use), "order": base.order, "shift": shift}
    free_res = None
    cap = max_order(len(use), allow_exact)
    if mode != "fixed" and len(use) >= 2:
        want = min(order if order is not None else default_order(len(use), allow_exact), cap)
        _reject_outlier(pts, want, disp)
        use = [p for p in pts if p.used]
        cap = max_order(len(use), allow_exact)
        want = min(order if order is not None else default_order(len(use), allow_exact), cap)
        free_res = _weighted_fit(use, want, disp)
        fits["free"] = {"label": "Free fit", "rms": free_res.rms_a, "n": len(use), "order": want}
        if order is not None and order > cap:
            msgs.append(f"Order {order} needs more lines; using order {cap}")
        if shift_sol is not None:  # the shift-only fit, on the same lines after the rejection
            shift = float(np.median([p.pixel - base.pixel(p.lam_a) for p in use]))
            shift_sol = base.shifted(shift)
            e = _errors(shift_sol, use)
            fits["shift"] = {"label": "Shift only", "rms": e.rms_a, "n": len(use), "order": base.order,
                             "shift": shift}

    # 3. which solution
    mode_reason = ""
    eff = mode
    if mode == "refine" and not mode_user and "free" in fits:
        if not prior.ok:
            eff, mode_reason = "free", "Fit freely: the saved solution does not fit this night"
        elif fits["free"]["rms"] < 0.5 * fits["shift"]["rms"] and fits["shift"]["rms"] > 0.5:
            fr, sr = fits['free']['rms'], fits['shift']['rms']
            eff, mode_reason = "free", (f"Fit freely: RMS {fr:.2f} Å against {sr:.2f} Å "
                                        "with the saved shape")
    if eff == "free" and free_res is None:
        msgs.append("Too few lines for a free fit: refined the saved solution instead")
        eff = "refine"
    if eff == "fixed":
        sol, shift = base, 0.0
        res = _errors(sol, use)
    elif eff == "refine":
        if shift_sol is None:
            return failed("no Balmer line measured; drag the Hβ and Hα markers onto their lines", base, pts)
        sol, shift = shift_sol, fits["shift"]["shift"]
        res = _errors(sol, use)
    else:
        sol, res = free_res.solution, free_res
        shift = float(np.median([p.pixel - base.pixel(p.lam_a) for p in use]))
    for k, f in fits.items():
        f["active"] = (k == "shift") == (eff == "refine")
    cal = Calibration(eff, sol, pts, res, shift, messages=msgs, prior=cal_prior, search=info, unmatched=unmatched,
                      fits=fits, mode_reason=mode_reason, centre_method=centre, repaired=repaired)
    _resolution(cal, norm)

    # 4. the quality gate
    if res is None or len(use) < 2:
        cal.status, cal.reason = "failed", "fewer than two Balmer lines measured"
    elif not monotonic(sol, n):
        cal.status, cal.reason = "failed", "the solution is not monotonic (the wavelength must grow with the column)"
    else:
        limit = QUALITY_RMS_A if cal.fwhm_a is None else min(QUALITY_RMS_A, max(1.0, 0.25 * cal.fwhm_a))
        if res.rms_a > limit:
            label = fits.get("shift" if eff == "refine" else "free", {}).get("label", eff)
            cal.status = "failed"
            cal.reason = (f"{label}: RMS {res.rms_a:.2f} Å is above {limit:.1f} Å — the lines are not where this "
                          "solution puts them" + ("" if prior.ok else f"; {prior.message}"))
    return cal


def _calibrate_emission(profile: np.ndarray, saved: Solution, mode: str, lines: list[str] | None, order: int | None,
                        manual: dict[str, float] | None, allow_exact: bool, fixed: Solution | None) -> Calibration:
    """Emission-line profiles (tests and tools only): the v0.1 path, prior-based."""
    names = [k for k in (lines or list(BALMER)) if k in BALMER]
    norm = normalised(profile)
    base = fixed if mode == "fixed" and fixed is not None else saved
    predicted = {k: base.pixel(BALMER[k]) for k in names}
    s0 = 0.0 if mode == "fixed" else coarse_shift(norm, predicted, True)
    pts = []
    for k, p in predicted.items():
        g = (manual or {}).get(k, p + s0)
        lf = fit_line(norm, g, True)
        if lf.ok:
            pts.append(Point(k, BALMER[k], lf.centre, lf.sigma))
    if mode == "fixed" or len(pts) < 3:
        shift = float(np.median([p.pixel - base.pixel(p.lam_a) for p in pts])) if pts and mode != "fixed" else 0.0
        sol = base.shifted(shift) if shift else base
        return Calibration("fixed" if mode == "fixed" else "refine", sol, pts, _errors(sol, pts), shift)
    res = fit(np.array([p.pixel for p in pts]), np.array([p.lam_a for p in pts]),
              min(order or default_order(len(pts), allow_exact), max_order(len(pts), allow_exact)))
    return Calibration("free", res.solution, pts, res, 0.0)


def _errors(sol: Solution, pts: list[Point]) -> FitResult | None:
    if not pts:
        return None
    err = np.array([float(sol.angstrom(p.pixel)) - p.lam_a for p in pts])
    return FitResult(sol, err, float(np.sqrt(np.sum(err ** 2))), float(np.sqrt(np.mean(err ** 2))))


def _resolution(cal: Calibration, norm: np.ndarray) -> None:
    """FWHM of Hβ (Gaussian σ × 2.3548 × local dispersion) and R = λ / FWHM. Always the v0.1 Gaussian (±12 px) at
    the measured centre, whatever the centre method: the BSS_ESRP written to the header stays comparable."""
    for name in ("Hβ", "Hγ", "Hα"):
        p = next((q for q in cal.points if q.line == name and q.sigma_px > 0), None)
        if p is not None:
            lf = fit_line(norm, p.pixel)
            sigma = lf.sigma if lf.ok and lf.sigma > 0 else p.sigma_px
            fwhm = 2.3548 * sigma * abs(cal.solution.dispersion_a(p.pixel))
            cal.fwhm_a, cal.resolving_power, cal.fwhm_line = fwhm, p.lam_a / fwhm, name
            return


# ---- resampling ---------------------------------------------------------------------------------
def linear_grid(sol: Solution, start_a: float, end_a: float) -> tuple[float, float, int]:
    """(CRVAL1, CDELT1, n): CDELT1 = mean dispersion across [start, end]."""
    p0, p1 = sol.pixel(start_a), sol.pixel(end_a)
    cdelt = (end_a - start_a) / (p1 - p0)
    n = math.floor((end_a - start_a) / cdelt + 1e-9) + 1
    return start_a, cdelt, n


def resample(profile: np.ndarray, sol: Solution, crval: float, cdelt: float, n: int) -> np.ndarray:
    """Flux-conserving rebin: cumulative counts at output bin edges (each input pixel spans
    [x−½, x+½] in wavelength). Output = counts per output bin; NaN outside the detector."""
    x_edges = np.arange(len(profile) + 1, dtype=np.float64) - 0.5
    lam_edges = np.asarray(sol.angstrom(x_edges), dtype=np.float64)
    cum = np.concatenate([[0.0], np.cumsum(np.asarray(profile, dtype=np.float64))])
    out_edges = crval + cdelt * (np.arange(n + 1, dtype=np.float64) - 0.5)
    c = np.interp(out_edges, lam_edges, cum, left=np.nan, right=np.nan)
    return np.diff(c).astype(np.float32)


# ---- number formatting --------------------------------------------------------------------------
def bass_number(v: float, sig: int = 10, decimals: int = 9) -> str:
    """Numbers as BASS writes them: rounded to `decimals` places, at most `sig` significant digits,
    trailing zeros dropped, 'E-08' exponents (0.126004411, 348.2492888, 6.06E-08)."""
    if v == 0:
        return "0"
    r = float(f"{round(v, decimals):.{sig}g}")
    if r == 0:
        return "0"
    s = f"{r:.{sig}g}"
    if "e" in s:
        mant, exp = s.split("e")
        mant = mant.rstrip("0").rstrip(".") if "." in mant else mant
        return f"{mant}E{int(exp):+03d}"
    return s
