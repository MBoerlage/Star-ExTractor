"""Line centres for broad, low-resolution absorption lines (no Qt).

At R ≈ 360 a Balmer line is 10–16 px wide (FWHM), sits on a continuum that the instrument response tilts by tens of
per cent across the line, and has Stark wings. The centre is the parameter of a profile fitted together with a local
continuum; the user's marker or the pattern search only gives the starting guess.

Methods (the names used in docs/investigations/wavelength_2025-10-02_omiand.md):
  M0  the method of v0.1: Gaussian + additive line on the envelope-normalised profile, window ±12 px (±6 px for a
      dragged marker), sigma ≤ 0.8 × half window  (core/wavelength.py fit_line)
  M1  argmin of a lightly smoothed profile                 M2  parabola through the lowest N points
  M3  centroid of the depth below a local linear continuum
  M4  Gaussian × linear continuum    M5  Lorentzian × linear continuum    M6  pseudo-Voigt × linear continuum
  M7  M6 on the profile divided by the smooth global continuum (core/wavelength.py continuum)
`fit_line_centre` is the production entry point: the benchmark's winner plus fallbacks and flags.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage, optimize

LN2_4 = 4.0 * math.log(2.0)
POOR_REL = 0.08  # residual RMS / line depth above which a fit is poor (clean lines: 0.01–0.04)
CORE = 0.35  # × FWHM: the core left out of the wings-only fit
BROAD_PX = 18.0  # dip width above which M0's ±12 px window grows to ±0.8 FWHM (a broad Hα)

# flags
LOW_SN, BLEND, EDGE, EMISSION_CORE, ASYMMETRIC, POOR_FIT, FALLBACK, WANDERED = (
    "low S/N", "blend", "edge of window", "emission core", "asymmetric", "poor fit", "fallback", "wandered")


@dataclass
class Centre:
    centre: float  # px (0-based column)
    sigma: float  # 1σ uncertainty of the centre, px (inf when unknown)
    fwhm: float = 0.0  # px
    depth: float = 0.0  # fraction of the continuum
    chi2r: float = float("nan")
    ok: bool = True
    method: str = ""
    flags: list[str] = field(default_factory=list)
    x: np.ndarray | None = None  # the fitted window and curve (for the overlay)
    curve: np.ndarray | None = None
    cont: tuple[float, float, float] | None = None  # fitted continuum c0 + c1·(x − x0): (c0, c1, x0)
    noise: float = float("nan")  # pixel noise near the line (profile units)


# ---- profiles -------------------------------------------------------------------------------------------------
def gauss(x, mu, w):
    return np.exp(-LN2_4 * ((x - mu) / w) ** 2)


def lorentz(x, mu, w):
    return 1.0 / (1.0 + 4.0 * ((x - mu) / w) ** 2)


def pvoigt(x, mu, w, eta):
    return eta * lorentz(x, mu, w) + (1.0 - eta) * gauss(x, mu, w)


SHAPES = {"gauss": (lambda x, mu, w, *_: gauss(x, mu, w), 0),
          "lorentz": (lambda x, mu, w, *_: lorentz(x, mu, w), 0),
          "voigt": (lambda x, mu, w, eta: pvoigt(x, mu, w, eta), 1)}


# ---- noise and masking ----------------------------------------------------------------------------------------
def noise_of(y: np.ndarray) -> float:
    """Pixel noise from the second differences (robust to slopes and broad lines)."""
    d = np.diff(np.asarray(y, dtype=np.float64), 2)
    if len(d) < 3:
        return float("nan")
    return float(1.4826 * np.median(np.abs(d - np.median(d))) / math.sqrt(6.0))


def correlation_factor(y: np.ndarray, b: int = 4) -> float:
    """How much correlated pixel noise (resampling in Geometry, binning) inflates a fitted position's error: the
    scatter of b-pixel means of the high-passed profile against what independent pixels would give (√b smaller).
    1 for independent pixels, which is what the fit covariance assumes. Lines and the continuum are removed first
    (a 15-pixel running median), and the scatter is a MAD, so the remaining line cores do not count."""
    y = np.asarray(y, dtype=np.float64)
    r = y - ndimage.median_filter(y, 15, mode="nearest")
    n = len(r) // b * b
    if n < 8 * b:
        return 1.0

    def mad(v: np.ndarray) -> float:
        return float(1.4826 * np.median(np.abs(v - np.median(v))))

    s1, sb = mad(r[:n]), mad(r[:n].reshape(-1, b).mean(axis=1))
    if s1 <= 0 or not np.isfinite(sb):
        return 1.0
    return float(np.clip(sb * math.sqrt(b) / s1, 1.0, 3.0))


def spikes(y: np.ndarray, sigma: float, k: float = 6.0) -> np.ndarray:
    """Hot pixels and cosmic rays: points far from their 5-point median."""
    if not np.isfinite(sigma) or sigma <= 0:
        return np.zeros(len(y), dtype=bool)
    return np.abs(y - ndimage.median_filter(y, 5, mode="nearest")) > k * sigma


def _window(n: int, guess: float, half: float) -> tuple[int, int]:
    lo = max(0, math.floor(guess - half))
    hi = min(n, math.ceil(guess + half) + 1)
    return lo, hi


# ---- the simple methods ---------------------------------------------------------------------------------------
def m1_argmin(y: np.ndarray, guess: float, half: float, smooth: float = 1.5) -> Centre:
    lo, hi = _window(len(y), guess, half)
    s = ndimage.gaussian_filter1d(np.asarray(y, dtype=np.float64), smooth)
    k = lo + int(np.argmin(s[lo:hi]))
    return Centre(float(k), 0.5, method="M1", ok=lo < k < hi - 1)


def m2_parabola(y: np.ndarray, guess: float, half: float, npts: int = 5) -> Centre:
    lo, hi = _window(len(y), guess, half)
    seg = np.asarray(y[lo:hi], dtype=np.float64)
    k = int(np.argmin(ndimage.gaussian_filter1d(seg, 1.0)))
    h = npts // 2
    a, b = max(0, k - h), min(len(seg), k + h + 1)
    if b - a < 3:
        return Centre(float(lo + k), float("inf"), method="M2", ok=False)
    xx = np.arange(a, b, dtype=np.float64)
    c2, c1, _c0 = np.polyfit(xx - k, seg[a:b], 2)
    if c2 <= 0:
        return Centre(float(lo + k), float("inf"), method="M2", ok=False)
    off = -c1 / (2 * c2)
    ok = abs(off) <= h
    return Centre(float(lo + k + np.clip(off, -h, h)), 0.5, method="M2", ok=bool(ok))


def m3_centroid(y: np.ndarray, guess: float, half: float) -> Centre:
    """Centroid of the depth below a straight line through the window's edge medians."""
    lo, hi = _window(len(y), guess, half)
    if hi - lo < 7:
        return Centre(guess, float("inf"), method="M3", ok=False)
    x = np.arange(lo, hi, dtype=np.float64)
    seg = np.asarray(y[lo:hi], dtype=np.float64)
    e = max(2, (hi - lo) // 8)
    xa, xb = float(np.mean(x[:e])), float(np.mean(x[-e:]))
    ya, yb = float(np.median(seg[:e])), float(np.median(seg[-e:]))
    cont = ya + (yb - ya) * (x - xa) / (xb - xa)
    d = np.clip(1.0 - seg / np.where(cont > 0, cont, np.nan), 0, None)
    d = np.nan_to_num(d)
    if d.sum() <= 0:
        return Centre(guess, float("inf"), method="M3", ok=False)
    c = float(np.sum(x * d) / np.sum(d))
    return Centre(c, 0.5, method="M3", ok=lo < c < hi - 1)


# ---- profile fits ---------------------------------------------------------------------------------------------
def _model(shape: str):
    f, extra = SHAPES[shape]

    def m(x, c0, c1, depth, mu, w, *rest):
        eta = rest[0] if extra else 0.0
        return (c0 + c1 * (x - m.x0)) * (1.0 - depth * f(x, mu, w, eta))

    m.x0 = 0.0
    return m, extra


def fit_profile(y: np.ndarray, guess: float, fwhm0: float, *, shape: str = "voigt", width: float = 2.5,
                adaptive: bool = False, weights: bool = True, clip: bool = True, mask_spikes: bool = True,
                max_move: float | None = None, method: str = "", core_mask: float = 0.0) -> Centre:
    """Line × straight continuum fitted at once, in a window of `width` × FWHM around `guess`.
    adaptive: refit twice with the window re-centred and re-sized from the fitted FWHM.
    core_mask > 0: the points within core_mask × FWHM of `guess` are left out (a wings-only fit)."""
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    centre, w = float(guess), float(fwhm0)
    res = Centre(guess, float("inf"), method=method or shape, ok=False)
    sig_pix = noise_of(y[max(0, int(guess - 3 * fwhm0)):min(n, int(guess + 3 * fwhm0) + 1)])
    for _it in range(3 if adaptive else 1):
        half = max(4.0, 0.5 * width * w)
        lo, hi = _window(n, centre if adaptive else guess, half)
        if hi - lo < 8:
            res.flags.append(EDGE)
            return res
        x = np.arange(lo, hi, dtype=np.float64)
        seg = y[lo:hi]
        good = np.ones(len(seg), dtype=bool)
        if mask_spikes:
            good &= ~spikes(seg, sig_pix)
        if core_mask > 0:
            good &= np.abs(x - guess) >= core_mask * w
        model, extra = _model(shape)
        model.x0 = float(np.mean(x))
        e = max(2, len(seg) // 8)
        ya, yb = float(np.median(seg[:e])), float(np.median(seg[-e:]))
        c0 = 0.5 * (ya + yb)
        c1 = (yb - ya) / max(1.0, x[-1] - x[0])
        kmin = int(np.argmin(ndimage.gaussian_filter1d(seg, 1.5)))
        mu0 = float(x[kmin]) if abs(x[kmin] - centre) < 0.5 * w else centre
        d0 = float(np.clip(1.0 - seg[kmin] / max(c0, 1e-9), 0.05, 0.95))
        p0 = [c0, c1, d0, mu0, w] + ([0.5] if extra else [])
        span = abs(c0) * 10 + 1.0
        lb = [-span, -span, 0.0, lo, 2.0] + ([0.0] if extra else [])
        ub = [span, span, 1.0, hi - 1, 3.0 * (hi - lo)] + ([1.0] if extra else [])
        sd = None
        if weights and np.isfinite(sig_pix) and sig_pix > 0:
            # photon noise: variance grows with the signal (sky-subtracted data keep a floor)
            lvl = max(abs(c0), 1e-9)
            sd = sig_pix * np.sqrt(np.clip(np.abs(seg) / lvl, 0.25, 4.0))
        p = None
        for _ in range(2 if clip else 1):
            try:
                p, cov = optimize.curve_fit(model, x[good], seg[good], p0=p0, bounds=(lb, ub),
                                            sigma=None if sd is None else sd[good], absolute_sigma=False,
                                            maxfev=4000)
            except (RuntimeError, ValueError, optimize.OptimizeWarning):
                p = None
                break
            r = (seg - model(x, *p)) / (sd if sd is not None else 1.0)
            if not clip:
                break
            s = 1.4826 * np.median(np.abs(r[good])) or 1.0
            new = good & (np.abs(r) < 4.0 * s)
            if new.sum() == good.sum() or new.sum() < 8:
                break
            good, p0 = new, list(p)
        if p is None:
            res.flags.append(POOR_FIT)
            return res
        dof = max(1, int(good.sum()) - len(p))
        r = (seg - model(x, *p)) / (sd if sd is not None else 1.0)
        chi2r = float(np.sum(r[good] ** 2) / dof)
        perr = np.sqrt(np.clip(np.diag(cov), 0, None)) if cov is not None and np.all(np.isfinite(cov)) else None
        mu, w_fit, depth = float(p[3]), float(p[4]), float(p[2])
        sig_mu = float(perr[3]) if perr is not None else float("inf")
        res = Centre(mu, sig_mu, abs(w_fit), depth, chi2r, True, method or shape, [], x, model(x, *p),
                     (float(p[0]), float(p[1]), model.x0), sig_pix)
        rel = float(np.std((seg - model(x, *p))[good])) / max(depth * abs(float(p[0])), 1e-12)
        if adaptive:
            if abs(mu - centre) < 0.05 and abs(w_fit - w) / w < 0.05:
                break
            centre, w = mu, float(np.clip(w_fit, 0.5 * fwhm0, 2.5 * fwhm0))
    # quality flags
    if res.x is not None:
        lo, hi = float(res.x[0]), float(res.x[-1])
        if not lo + 0.15 * (hi - lo) < res.centre < hi - 0.15 * (hi - lo):
            res.flags.append(EDGE)
    if res.curve is not None:
        cont = float(np.max(np.abs(res.curve))) or 1.0
        if res.depth < 0.02 or (np.isfinite(sig_pix) and sig_pix > 0 and res.depth * cont < 5.0 * sig_pix):
            res.flags.append(LOW_SN)
    if res.curve is not None and rel > POOR_REL:  # residuals large against the line (χ² alone measures the S/N)
        res.flags.append(POOR_FIT)
    if max_move is not None and abs(res.centre - guess) > max_move:
        res.flags.append(WANDERED)
        res.ok = False
    if not np.isfinite(res.sigma) or res.sigma > 0.5 * max(res.fwhm, 1.0):
        res.ok = False
    return res


# ---- M0: the v0.1 method, verbatim (kept for the benchmark) -----------------------------------------------------
def m0_v01(norm: np.ndarray, guess: float, half: int = 12) -> Centre:
    from .wavelength import fit_line

    lf = fit_line(norm, guess, False, half=half)
    return Centre(lf.centre, float("inf"), 2.3548 * lf.sigma, -lf.depth, method="M0", ok=lf.ok)


def m0_line(y: np.ndarray, guess: float, fwhm_guess: float = 12.0, *, norm: np.ndarray | None = None,
            recentre: bool = True, scale: bool = True, clean: bool = True) -> Centre:
    """M0, the method the study kept (v0.1's Gaussian + linear background on the envelope-normalised profile), with
    three corrections that are not a new method: hot pixels replaced by their 5-point median (`clean`), the window
    re-centred on the first result until it stops moving (`recentre`: a marker dropped off-centre no longer biases
    it), and, only when the ±12 px fit fails on a line broader than BROAD_PX, a ±0.8 FWHM window (`scale`: v0.1
    rejected a broad Hα whose σ exceeded 0.8 × 12 px). A wider window by default made the calibration worse."""
    from .wavelength import continuum, fit_line

    y = np.asarray(y, dtype=np.float64)
    if clean:
        sig = noise_of(y)
        bad = spikes(y, sig, 8.0)
        if bad.any():  # interpolated from the good neighbours (a median biases a curved line core)
            xs = np.arange(len(y), dtype=np.float64)
            y = y.copy()
            y[bad] = np.interp(xs[bad], xs[~bad], y[~bad])
            norm = None
    cont = continuum(y)
    nrm = norm if norm is not None else y / cont
    def run(half: int):
        c = float(guess)
        lf = fit_line(nrm, c, False, half=half)
        stable = lf.ok
        for _ in range(4 if recentre else 0):
            if not lf.ok or abs(lf.centre - c) < 0.05:
                break
            c = lf.centre
            nxt = fit_line(nrm, c, False, half=half)
            if not nxt.ok:
                stable = False
                break
            lf = nxt
        return lf, stable

    half = 12
    lf, stable = run(half)
    if not stable and scale and fwhm_guess > BROAD_PX:  # fallback only: a broad line v0.1's window cannot hold
        half = round(0.8 * fwhm_guess)
        lf2, stable2 = run(half)
        if stable2 or not lf.ok:
            lf = lf2
    corr = correlation_factor(y)
    out = Centre(lf.centre, lf.err * corr if lf.ok else float("inf"), 2.3548 * lf.sigma, -lf.depth, method="M0",
                 ok=lf.ok)
    if lf.ok:
        lo, hi = max(0, int(lf.centre) - half), min(len(y), int(lf.centre) + half + 1)
        xx = np.arange(lo, hi, dtype=np.float64)
        g = lf.base + lf.slope * (xx - lf.centre) + lf.depth * np.exp(-0.5 * ((xx - lf.centre) / lf.sigma) ** 2)
        out.x, out.curve = xx, g * cont[lo:hi]  # the fitted line in profile units (the overlay)
        out.cont = (float(np.median(cont[lo:hi])), 0.0, float(lf.centre))
        out.noise = noise_of(y[max(0, lo - 30):hi + 30])
    return out


# ---- production entry point -----------------------------------------------------------------------------------
def wings_only(y: np.ndarray, c: Centre, shape: str) -> Centre | None:
    """The same line fitted on its wings (core left out): the centre an emission core cannot pull."""
    if c.x is None or c.fwhm <= 0:
        return None
    w = fit_profile(y, c.centre, c.fwhm, shape=shape, width=2.5, adaptive=False, clip=False, core_mask=CORE,
                    method=shape)
    return w if w.ok and w.curve is not None else None


def emission_core(y: np.ndarray, c: Centre, wings: Centre | None) -> bool:
    """Data above the wings-only absorption at the line centre: a Be-star or shell emission core."""
    if wings is None or wings.x is None or wings.curve is None:
        return False
    x0 = int(wings.x[0])
    core = np.abs(wings.x - wings.centre) < 0.25 * wings.fwhm
    if core.sum() < 2:
        return False
    seg = y[x0:x0 + len(wings.x)]
    sig = wings.noise if np.isfinite(wings.noise) else noise_of(y[max(0, x0 - 40):x0 + len(wings.x) + 40])
    core &= ~spikes(seg, sig)  # a hot pixel is not an emission core
    if core.sum() < 2 or c.x is None or c.curve is None:
        return False
    excess = float(np.mean(seg[core] - wings.curve[core]))
    cont = abs(wings.cont[0]) if wings.cont else float(np.max(np.abs(wings.curve)))
    # the full fit must also leave a bump at its centre (the wings alone can over-predict a core)
    c0 = int(c.x[0])
    near = (np.abs(c.x - c.centre) < 0.25 * c.fwhm) & ~spikes(y[c0:c0 + len(c.x)], sig)
    bump = float(np.mean(y[c0:c0 + len(c.x)][near] - c.curve[near])) if near.sum() >= 2 else 0.0
    n = math.sqrt(core.sum())
    return excess > max(5.0 * sig / n, 0.12 * wings.depth * cont) and bump > 2.0 * sig / n


def bisector(y: np.ndarray, c: Centre, levels: tuple[float, ...] = (0.3, 0.5, 0.7)) -> list[float]:
    """Midpoints of the line at fractions of its depth (from the fitted continuum): equal for a symmetric line."""
    if c.x is None or c.cont is None or c.depth <= 0:
        return []
    c0, c1, xc = c.cont
    x = c.x
    seg = np.asarray(y[int(x[0]):int(x[0]) + len(x)], dtype=np.float64)
    bad = spikes(seg, c.noise)
    if bad.any():  # hot pixels: their 5-point median instead
        seg = np.where(bad, ndimage.median_filter(seg, 5, mode="nearest"), seg)
    d = 1.0 - seg / (c0 + c1 * (x - xc))
    k = int(np.argmin(np.abs(x - c.centre)))
    out = []
    for lev in levels:
        t = lev * c.depth
        i = k
        while i > 0 and d[i] > t:
            i -= 1
        j = k
        while j < len(d) - 1 and d[j] > t:
            j += 1
        if i == 0 or j == len(d) - 1 or d[i] > t or d[j] > t:
            return []
        left = x[i] + (t - d[i]) / (d[i + 1] - d[i]) * (x[i + 1] - x[i]) if d[i + 1] != d[i] else x[i]
        right = x[j - 1] + (t - d[j - 1]) / (d[j] - d[j - 1]) * (x[j] - x[j - 1]) if d[j] != d[j - 1] else x[j]
        out.append(0.5 * (left + right))
    return out


def asymmetric(c: Centre, y: np.ndarray) -> bool:
    """The bisector leans: its midpoints at 30, 50 and 70 % depth spread more than the noise allows (a blend or a
    shoulder; the profile fit would otherwise move the centre silently)."""
    mids = bisector(y, c)
    if len(mids) < 3 or c.cont is None:
        return False
    sig_d = (c.noise if np.isfinite(c.noise) else 0.0) / max(abs(c.cont[0]), 1e-12)
    slope = c.depth / max(0.5 * c.fwhm, 1.0)  # depth per px on the flanks
    sig_mid = sig_d / max(slope, 1e-12)
    return float(np.ptp(mids)) > max(0.05 * c.fwhm, 3.0 * sig_mid)


def fit_line_centre(y: np.ndarray, guess: float, fwhm: float = 12.0, *, model: str = "auto", window: str = "auto",
                    max_move_fwhm: float = 1.0, disp_a: float | None = None) -> Centre:
    """The centre of the absorption line nearest `guess` with its uncertainty and flags.
    model auto = pseudo-Voigt, then Gaussian, then a parabola (FALLBACK flagged); window auto = adaptive 2.5 × FWHM.
    The centre never moves more than `max_move_fwhm` × FWHM from the guess: then the guess is kept (WANDERED)."""
    y = np.asarray(y, dtype=np.float64)
    chain = {"auto": ["voigt", "gauss"], "voigt": ["voigt"], "gauss": ["gauss"], "lorentz": ["lorentz"],
             "minimum": []}[model]
    adaptive = window == "auto"
    width = 2.5 if window == "auto" else float(window)
    max_move = max_move_fwhm * fwhm
    best: Centre | None = None
    for i, shape in enumerate(chain):
        c = fit_profile(y, guess, fwhm, shape=shape, width=width, adaptive=adaptive, max_move=max_move,
                        method=shape)
        if c.ok:
            if i:
                c.flags.append(FALLBACK)
            best = c
            break
    if best is None:
        p = m2_parabola(y, guess, 0.5 * fwhm, 7)
        p.method = "parabola"
        p.flags.append(FALLBACK)
        if model != "minimum":
            p.flags.append(POOR_FIT)
        if not p.ok or abs(p.centre - guess) > max_move:
            return Centre(float(guess), float("inf"), fwhm, 0.0, ok=False, method="guess",
                          flags=[POOR_FIT, *([WANDERED] if p.ok else [])])
        best = p
    if best.x is not None and best.method in SHAPES:
        wings = wings_only(y, best, best.method)
        if emission_core(y, best, wings):  # the wings give the centre; the core is flagged
            wings.flags = [*best.flags, EMISSION_CORE]
            wings.sigma = max(wings.sigma, best.sigma)
            best = wings
        elif asymmetric(best, y):
            best.flags.append(BLEND)
    best.flags = list(dict.fromkeys(best.flags))
    return best
