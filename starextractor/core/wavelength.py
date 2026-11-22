"""Step 4 · wavelength calibration from Balmer lines, with BASS-compatible coefficients.

Solutions are polynomials in nm of the 0-based detector column (PIXEL_ORIGIN = 0, as in BASS):
f(x) = a + b·x (+ c·x²). Internally wavelengths are Å; the BASS box and export are nm.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage, optimize

PIXEL_ORIGIN = 0
# Air wavelengths in Å. Hα/Hβ/Hγ are BASS's own values (docs/BASS_CALIBRATION.md §2);
# Hδ/Hε are the standard air values (BASS's unverified).
BALMER: dict[str, float] = {"Hα": 6562.852, "Hβ": 4861.33, "Hγ": 4340.47, "Hδ": 4101.74, "Hε": 3970.07}
OPTIONAL_LINES = {"Hε"}  # faint blue end of hot stars
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
        if self.order == 1:
            a, b = self.coeffs_nm
            return (target - a) / b + PIXEL_ORIGIN
        a, b, c = self.coeffs_nm
        if c == 0:
            return (target - a) / b + PIXEL_ORIGIN
        disc = b * b - 4 * c * (a - target)
        return (-b + math.sqrt(disc)) / (2 * c) + PIXEL_ORIGIN

    def dispersion_a(self, x: float) -> float:
        """Å per pixel at column x."""
        b = self.coeffs_nm[1]
        c = self.coeffs_nm[2] if self.order >= 2 else 0.0
        return 10.0 * (b + 2 * c * (x - PIXEL_ORIGIN))

    def shifted(self, s: float) -> Solution:
        """Same curve moved by s pixels: g(x) = f(x − s)."""
        if self.order == 1:
            a, b = self.coeffs_nm
            return Solution([a - b * s, b])
        a, b, c = self.coeffs_nm
        return Solution([a - b * s + c * s * s, b - 2 * c * s, c])


def format_coeffs(coeffs: list[float]) -> str:
    """BASS box layout: full double precision, ', ' separated, offset first, 'E-08' style."""
    return ", ".join(repr(float(c)).replace("e", "E") for c in coeffs)


def parse_coeffs(text: str) -> list[float]:
    """2 or 3 numbers separated by commas, spaces or semicolons; any exponent notation."""
    parts = [p for p in re.split(r"[,;\s]+", text.strip().replace("−", "-")) if p]
    if len(parts) not in (2, 3):
        raise ValueError(f"expected 2 or 3 coefficients (offset first, nm), got {len(parts)}")
    try:
        vals = [float(p.replace("D", "E").replace("d", "e")) for p in parts]
    except ValueError as exc:
        raise ValueError(f"not a number: {exc}") from exc
    if not (100 < vals[0] < 2000 and 0 < vals[1] < 5):
        raise ValueError("these do not look like nm coefficients (offset 100–2000 nm, dispersion 0–5 nm/px)")
    return vals


def max_order(n_lines: int, allow_exact: bool = False) -> int:
    """Never above (lines − 2); 2 lines → linear. With 3 lines a quadratic only fits exactly, so it
    is offered only on explicit request (`allow_exact`)."""
    if n_lines < 2:
        return 0
    if n_lines == 3 and allow_exact:
        return 2
    return max(1, min(2, n_lines - 2))


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
        p, _ = optimize.curve_fit(_gauss, x, y, p0=[amp0, x[k], 2.5, float(np.median(y)), 0.0],
                                  bounds=([-10, lo, 0.5, -10, -1], [10, hi - 1, half, 10, 1]), maxfev=4000)
    except (RuntimeError, ValueError):
        return LineFit(guess, 0.0, 0.0, False)
    amp, mu, sig = float(p[0]), float(p[1]), float(p[2])
    noise = float(np.std(np.diff(norm[max(0, lo - 30):hi + 30]))) / math.sqrt(2) or 1e-3
    ok = (amp > 0) == emission and abs(amp) > 3 * noise and lo + 1 < mu < hi - 2 and sig < half * 0.8
    return LineFit(mu, sig, amp, ok)


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
    sigma_px: float = 0.0
    manual: bool = False


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


def measure(norm: np.ndarray, guesses: dict[str, float], emission: bool = False,
            manual: dict[str, float] | None = None) -> list[Point]:
    pts = []
    manual = manual or {}
    for name, guess in guesses.items():
        if name in manual:
            lf = fit_line(norm, manual[name], emission, half=6)
            pts.append(Point(name, BALMER[name], lf.centre if lf.ok else manual[name], lf.sigma, True))
            continue
        if not 0 <= guess < len(norm):
            continue
        lf = fit_line(norm, guess, emission)
        if lf.ok:
            pts.append(Point(name, BALMER[name], lf.centre, lf.sigma))
    return pts


def calibrate(profile: np.ndarray, saved: Solution, mode: str = "refine", lines: list[str] | None = None,
              order: int | None = None, manual: dict[str, float] | None = None, emission: bool = False,
              allow_exact: bool = False, fixed: Solution | None = None) -> Calibration:
    """refine: saved solution moved by the median line shift. free: fit from the lines found.
    fixed: a pasted solution used as is (lines measured to report errors)."""
    names = [n for n in (lines or list(BALMER)) if n in BALMER]
    norm = normalised(profile)
    msgs: list[str] = []
    base = fixed if mode == "fixed" and fixed is not None else saved
    predicted = {n: base.pixel(BALMER[n]) for n in names}
    s0 = 0.0 if mode == "fixed" else coarse_shift(norm, predicted, emission)
    guesses = {n: p + s0 for n, p in predicted.items()}
    pts = measure(norm, guesses, emission, manual)
    found = {p.line for p in pts}
    missing = [n for n in names if n not in found and n not in OPTIONAL_LINES]
    if missing:
        msgs.append("Not found: " + ", ".join(missing))

    if mode == "fixed":
        sol = base
        res = _errors(sol, pts)
        shift = 0.0
    elif mode == "refine" or len(pts) < 2:
        if not pts:
            raise ValueError("no Balmer lines found near the saved solution; try 'Fit freely' or move a marker")
        shifts = [p.pixel - base.pixel(p.lam_a) for p in pts]
        shift = float(np.median(shifts))
        sol = base.shifted(shift)
        res = _errors(sol, pts)
        if mode != "refine":
            msgs.append("Too few lines for a free fit: refined the saved solution instead")
            mode = "refine"
    else:
        cap = max_order(len(pts), allow_exact)
        use = min(order if order is not None else cap, cap)
        if order is not None and order > cap:
            msgs.append(f"Order {order} needs more lines; using order {cap}")
        res = fit(np.array([p.pixel for p in pts]), np.array([p.lam_a for p in pts]), use)
        sol = res.solution
        shift = float(np.median([p.pixel - base.pixel(p.lam_a) for p in pts]))
    cal = Calibration(mode, sol, pts, res, shift, messages=msgs)
    _resolution(cal, norm)
    return cal


def _errors(sol: Solution, pts: list[Point]) -> FitResult | None:
    if not pts:
        return None
    err = np.array([float(sol.angstrom(p.pixel)) - p.lam_a for p in pts])
    return FitResult(sol, err, float(np.sqrt(np.sum(err ** 2))), float(np.sqrt(np.mean(err ** 2))))


def _resolution(cal: Calibration, norm: np.ndarray) -> None:
    """FWHM of Hβ (Gaussian σ × 2.3548 × local dispersion) and R = λ / FWHM."""
    for name in ("Hβ", "Hγ", "Hα"):
        p = next((q for q in cal.points if q.line == name and q.sigma_px > 0), None)
        if p is not None:
            fwhm = 2.3548 * p.sigma_px * abs(cal.solution.dispersion_a(p.pixel))
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
