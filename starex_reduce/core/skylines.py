"""Night-sky emission lines in the sky rows of a straightened 2D image (no Qt).

`detect` collapses the Sky A / Sky B rows (never the spectrum rows) by a median, removes the smooth sky glow with a
running median, and finds narrow emission peaks with a Gaussian fit each. `identify` matches the peak pattern to the
catalogue sky lines WITHOUT trusting the initial solution: any offset up to MAX_SHIFT_PX and a scale error up to
MAX_SCALE_ERR are tried, the mapping with the most consistent lines wins, and an ambiguous or too small pattern is
reported as not identified. A clean sky (no lines) is a normal result, never an error. Pixel x = 0-based column.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage, optimize, signal

from . import linelists
from .linelists import Line
from .wavelength import Solution

NSIGMA_SKY = 5.0  # peak height and prominence over the robust noise
FWHM_MIN, FWHM_MAX = 2.5, 25.0  # px; narrower = hot column / cosmic, wider = not a sky line
GLOW_WINDOW = 101  # px, running median for the smooth sky glow
SMOOTH_SIGMA = 1.0  # px, light smoothing for finding only
MAX_SHIFT_PX = 80.0  # how far the true lines may sit from the initial solution
MAX_SCALE_ERR = 0.02  # and how much its scale may be off
MIN_SKY_LINES = 3  # consistent lines needed to call the pattern identified
MATCH_TOL_PX = 3.0  # a peak within this (or half its FWHM, if wider) of a predicted line matches it


@dataclass
class Peak:
    x: float  # centroid (px)
    err: float  # 1σ (px)
    fwhm: float  # px
    amp: float  # above the glow (ADU)
    snr: float


@dataclass
class Match:
    line: Line
    peak: Peak
    x_pred: float  # where the initial solution puts the line
    resid_px: float  # after the identified mapping


@dataclass
class SkyResult:
    peaks: list[Peak]
    matches: list[Match] = field(default_factory=list)
    identified: bool = False
    offset_px: float = 0.0  # x_measured − x_initial at x0
    scale: float = 0.0  # fractional scale error of the initial solution
    x0: float = 0.0
    confidence: float = 0.0  # 0..1
    reason: str = ""
    sky: np.ndarray | None = None  # the glow-removed collapsed sky (for the plot)
    noise: float = 0.0

    def text(self) -> str:
        if not self.peaks:
            return "No sky lines found, clean sky"
        if not self.identified:
            return f"{len(self.peaks)} sky peaks, not identified: {self.reason}"
        return (f"{len(self.matches)} sky lines identified ({', '.join(m.line.label for m in self.matches)}), "
                f"offset {self.offset_px:+.1f} px")


def collapse(img: np.ndarray, rows: list[tuple[int, int]]) -> np.ndarray:
    """Median across the given row ranges (inclusive, clipped to the image)."""
    h = img.shape[0]
    idx = sorted({r for a, b in rows for r in range(max(0, min(a, b)), min(h - 1, max(a, b)) + 1)})
    if not idx:
        return np.zeros(img.shape[1])
    return np.median(np.asarray(img[idx], dtype=np.float64), axis=0)


def _gauss(x: np.ndarray, a: float, c: float, s: float, b0: float, b1: float) -> np.ndarray:
    return a * np.exp(-0.5 * ((x - c) / s) ** 2) + b0 + b1 * (x - c)


def _fit_peak(flat: np.ndarray, x: int, width: float, noise: float) -> Peak | None:
    half = int(max(8, 2.0 * width))
    lo, hi = max(0, x - half), min(len(flat), x + half + 1)
    xx = np.arange(lo, hi, dtype=np.float64)
    yy = flat[lo:hi]
    a0 = float(flat[x])
    try:
        p, cov = optimize.curve_fit(_gauss, xx, yy, p0=(a0, float(x), max(width / 2.355, 1.0), 0.0, 0.0),
                                    sigma=np.full(len(xx), max(noise, 1e-9)), absolute_sigma=True, maxfev=2000)
    except (RuntimeError, ValueError, optimize.OptimizeWarning):
        return None
    a, c, s = float(p[0]), float(p[1]), abs(float(p[2]))
    if a <= 0 or not lo <= c <= hi or not np.all(np.isfinite(cov)):
        return None
    fwhm = 2.3548 * s
    if not FWHM_MIN <= fwhm <= FWHM_MAX * 1.5:
        return None
    err = float(math.sqrt(max(cov[1, 1], 0.0)))
    return Peak(c, max(err, 0.02), fwhm, a, a / max(noise, 1e-9))


def detect(sky: np.ndarray, nsigma: float = NSIGMA_SKY) -> tuple[list[Peak], np.ndarray, float]:
    """(peaks, glow-removed sky, robust noise). Single-column spikes (hot columns) are rejected by the width
    window and by a check against their neighbours."""
    s = np.asarray(sky, dtype=np.float64)
    flat = s - ndimage.median_filter(s, GLOW_WINDOW, mode="nearest")
    noise = float(1.4826 * np.median(np.abs(flat - np.median(flat))))
    noise = max(noise, 1e-9)
    sm = ndimage.gaussian_filter1d(flat, SMOOTH_SIGMA)
    idx, props = signal.find_peaks(sm, height=nsigma * noise, prominence=nsigma * noise,
                                   width=(FWHM_MIN * 0.7, FWHM_MAX))
    peaks = []
    for i, x in enumerate(idx):
        if 0 < x < len(flat) - 1 and flat[x] > 4 * max(flat[x - 1], flat[x + 1], noise):
            continue  # one hot column
        p = _fit_peak(flat, int(x), float(props["widths"][i]), noise)
        if p is not None and p.snr >= nsigma * 0.8:
            peaks.append(p)
    return peaks, flat, noise


def _matches_for(peaks: list[Peak], pred: np.ndarray, lines: list[Line], s: float, k: float,
                 x0: float) -> list[tuple[int, int, float]]:
    """(peak i, line j, residual px) for the mapping x = pred + s + k (pred − x0); one peak per line."""
    out = []
    used: set[int] = set()
    for j in np.argsort([-ln.weight for ln in lines]):
        xm = pred[j] + s + k * (pred[j] - x0)
        tol = max(MATCH_TOL_PX, 0.5 * lines[j].fwhm_a / 1.2)
        best, bd = -1, tol
        for i, p in enumerate(peaks):
            d = abs(p.x - xm)
            if i not in used and d <= max(bd, 0.5 * p.fwhm if lines[j].blend else bd):
                best, bd = i, d
        if best >= 0:
            used.add(best)
            out.append((best, int(j), peaks[best].x - xm))
    return out


def identify(peaks: list[Peak], initial: Solution, n_px: int, lines: list[Line] | None = None,
             max_shift: float = MAX_SHIFT_PX, max_scale: float = MAX_SCALE_ERR,
             min_lines: int = MIN_SKY_LINES) -> tuple[list[Match], dict]:
    """Match peaks to catalogue lines. Returns (matches, info{identified, offset, scale, x0, confidence, reason})."""
    lines = [ln for ln in (lines or linelists.sky_lines()) if 0 <= initial.pixel(ln.lam_a) < n_px]
    x0 = (n_px - 1) / 2
    info = {"identified": False, "offset": 0.0, "scale": 0.0, "x0": x0, "confidence": 0.0, "reason": ""}
    if len(peaks) < min_lines or len(lines) < min_lines:
        info["reason"] = f"{len(peaks)} peaks, {min_lines} needed" if peaks else "no peaks"
        return [], info
    pred = np.array([initial.pixel(ln.lam_a) for ln in lines])
    cands: list[tuple[float, int, float, float, float, list]] = []  # (score, n, rms, s, k, matches)
    # hypotheses: two peak/line pairs fix offset and scale; one pair with zero scale as well
    pairs = [(i, j) for i in range(len(peaks)) for j in range(len(lines)) if abs(peaks[i].x - pred[j]) <= max_shift]
    hyps: set[tuple[float, float]] = {(round(peaks[i].x - pred[j], 1), 0.0) for i, j in pairs}
    for (i1, j1), (i2, j2) in itertools.combinations(pairs, 2):
        if i1 == i2 or j1 == j2 or abs(pred[j2] - pred[j1]) < 50:
            continue
        k = (peaks[i2].x - peaks[i1].x) / (pred[j2] - pred[j1]) - 1.0
        if abs(k) > max_scale:
            continue
        s = peaks[i1].x - pred[j1] - k * (pred[j1] - x0)
        if abs(s) <= max_shift:
            hyps.add((round(s, 1), round(k, 5)))
    for s, k in hyps:
        m = _matches_for(peaks, pred, lines, s, k, x0)
        if len(m) < 2:
            continue
        # refine with the matched lines (weighted LS for s, k), then re-match once
        for _ in range(2):
            pp = np.array([pred[j] - x0 for _i, j, _r in m])
            xx = np.array([peaks[i].x - pred[j] for i, j, _r in m])
            w = np.array([lines[j].weight for _i, j, _r in m])
            if len(m) >= 3 and np.ptp(pp) > 200:
                a = np.vstack([np.ones_like(pp), pp]).T * np.sqrt(w)[:, None]
                s, k = np.linalg.lstsq(a, xx * np.sqrt(w), rcond=None)[0]
            else:
                s, k = float(np.average(xx, weights=w)), k
            if abs(k) > max_scale or abs(s) > max_shift:
                break
            m = _matches_for(peaks, pred, lines, float(s), float(k), x0)
        if abs(k) > max_scale or abs(s) > max_shift or len(m) < 2:
            continue
        r = np.array([res for _i, _j, res in m])
        rms = float(np.sqrt(np.mean(r ** 2)))
        score = sum(lines[j].weight for _i, j, _r in m) - 0.05 * rms
        cands.append((score, len(m), rms, float(s), float(k), m))
    if not cands:
        info["reason"] = "no consistent pattern"
        return [], info
    cands.sort(key=lambda c: (-c[1], -c[0]))
    best = cands[0]
    rivals = [c for c in cands[1:] if abs(c[3] - best[3]) > 2 * MATCH_TOL_PX]
    info.update(offset=best[3], scale=best[4])
    if best[1] < min_lines:
        info["reason"] = f"only {best[1]} consistent lines ({min_lines} needed)"
        return [], info
    if rivals and rivals[0][1] >= best[1] and rivals[0][0] >= best[0] - 0.2:
        info["reason"] = (f"ambiguous: offsets {best[3]:+.1f} and {rivals[0][3]:+.1f} px fit "
                          f"{best[1]} lines equally well")
        return [], info
    margin = best[1] - (rivals[0][1] if rivals else 0)
    info["identified"] = True
    info["confidence"] = float(min(1.0, 0.4 + 0.15 * best[1] + 0.1 * margin - 0.05 * best[2]))
    matches = [Match(lines[j], peaks[i], float(pred[j]), float(res)) for i, j, res in best[5]]
    matches.sort(key=lambda m: m.line.lam_a)
    return matches, info


def find_sky_lines(img: np.ndarray, sky_rows: list[tuple[int, int]], initial: Solution,
                   nsigma: float = NSIGMA_SKY) -> SkyResult:
    """The whole stack-level run: collapse, detect, identify (well under 300 ms on 3840×2160)."""
    sky = collapse(img, sky_rows)
    peaks, flat, noise = detect(sky, nsigma)
    res = SkyResult(peaks, sky=flat.astype(np.float32), noise=noise)
    if not peaks:
        res.reason = "no lines"
        return res
    matches, info = identify(peaks, initial, len(sky))
    res.matches, res.identified = matches, info["identified"]
    res.offset_px, res.scale, res.x0 = info["offset"], info["scale"], info["x0"]
    res.confidence, res.reason = info["confidence"], info["reason"]
    return res
