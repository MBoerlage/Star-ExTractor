"""Step 2 · geometry: flip, hot pixels, tilt detection and rotation (smile model kept for later).

Angles are degrees in array coordinates: positive = the spectrum row increases with X. The frame
is straightened by rotating about the spectrum centre by the opposite angle.
"""
from __future__ import annotations

import itertools
import math
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from .combine import THREADS
from .jobs import Ctx

SATURATED = 65520.0


def theil_sen(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Robust line: median of pairwise slopes, intercept = median(y − slope·x). Small n only."""
    i, j = np.triu_indices(len(x), k=1)
    dx = x[j] - x[i]
    ok = dx != 0
    slope = float(np.median((y[j] - y[i])[ok] / dx[ok]))
    return slope, float(np.median(y - slope * x))


# ---- spectrum location ------------------------------------------------------------------------
def spectrum_row(img: np.ndarray) -> float:
    """Row of the brightest ridge from a robust row profile (90th percentile over columns)."""
    _h, w = img.shape
    prof = np.percentile(img[:, w // 8: w - w // 8: 8], 90, axis=1)
    prof = ndimage.gaussian_filter1d(prof - np.median(prof), 2.0)
    return float(np.argmax(prof))


def lit_range(img: np.ndarray, row: float, half: int = 12, frac: float = 0.10) -> tuple[int, int]:
    """X range where the spectrum is bright (band sum above `frac` of its maximum)."""
    h, w = img.shape
    y0, y1 = max(0, int(row) - half), min(h, int(row) + half + 1)
    band = img[y0:y1].sum(axis=0)
    bg_rows = np.r_[max(0, y0 - 40):max(0, y0 - 20), min(h, y1 + 20):min(h, y1 + 40)]
    bg = np.median(img[bg_rows], axis=0) * (y1 - y0) if len(bg_rows) else 0
    sig = ndimage.median_filter(band - bg, 31)
    peak = float(sig.max())
    if peak <= 0:
        return 0, w - 1
    idx = np.flatnonzero(sig > frac * peak)
    return int(idx[0]), int(idx[-1])


@dataclass
class TiltResult:
    angle_deg: float
    residual_px: float
    row: float  # spectrum row at the centre of the lit range
    x_centre: float
    lit: tuple[int, int]
    points: list[tuple[float, float]] = field(default_factory=list)  # (x, y) block centres used
    rejected: int = 0

    @property
    def span_px(self) -> float:
        return float(self.lit[1] - self.lit[0])


def ridge_centres(img: np.ndarray, row: float, lit: tuple[int, int], blocks: int = 16,
                  window: int = 40) -> list[tuple[float, float, float]]:
    """(x, y, amplitude) of the spectrum centre in `blocks` column blocks across the lit range."""
    h, _ = img.shape
    y0, y1 = max(0, int(row) - window), min(h, int(row) + window + 1)
    edges = np.linspace(lit[0], lit[1], blocks + 1).astype(int)
    out = []
    for a, b in itertools.pairwise(edges):
        if b - a < 4:
            continue
        prof = np.median(img[y0:y1, a:b], axis=1)
        prof = ndimage.gaussian_filter1d(prof - np.median(prof), 1.5)
        k = int(np.argmax(prof))
        amp = float(prof[k])
        if amp <= 0 or k < 3 or k > len(prof) - 4:
            continue
        half = max(2, int(np.sum(prof > amp / 2) / 2) + 1)  # ~ half the FWHM
        lo, hi = max(0, k - half), min(len(prof), k + half + 1)
        wts = np.clip(prof[lo:hi], 0, None)
        if wts.sum() <= 0:
            continue
        yc = y0 + float((np.arange(lo, hi) * wts).sum() / wts.sum())
        out.append(((a + b - 1) / 2, yc, amp))
    return out


def detect_tilt(img: np.ndarray, blocks: int = 16, ctx: Ctx | None = None) -> TiltResult:
    """Spectrum centre per column block, robust Theil–Sen line through them → tilt angle."""
    ctx = ctx or Ctx()
    row = spectrum_row(img)
    ctx.step(0.2)
    lit = lit_range(img, row)
    pts = ridge_centres(img, row, lit, blocks)
    ctx.step(0.8)
    if len(pts) < 3:
        raise ValueError("could not find the spectrum in enough places to measure the tilt")
    amps = np.array([p[2] for p in pts])
    keep = amps > 0.05 * amps.max()  # faint ends give unreliable centres
    x = np.array([p[0] for p in pts])[keep]
    y = np.array([p[1] for p in pts])[keep]
    slope, icpt = theil_sen(x, y)
    resid = y - (icpt + slope * x)
    mad = float(np.median(np.abs(resid - np.median(resid))))
    good = np.abs(resid) <= max(1.0, 4 * 1.4826 * mad)
    if good.sum() >= 3 and not good.all():
        slope, icpt = theil_sen(x[good], y[good])
        resid = y[good] - (icpt + slope * x[good])
    xc = (lit[0] + lit[1]) / 2
    ctx.step(1.0)
    return TiltResult(
        angle_deg=math.degrees(math.atan(slope)),
        residual_px=float(np.sqrt(np.mean(resid ** 2))),
        row=float(icpt + slope * xc), x_centre=xc, lit=lit,
        points=[(float(a), float(b)) for a, b in zip(x[good], y[good], strict=True)],
        rejected=int((~good).sum() + (~keep).sum()),
    )


def drawn_angle(x0: float, y0: float, x1: float, y1: float) -> float:
    """Angle of a drawn line in real pixel units (independent of any zoom)."""
    if x1 == x0 and y1 == y0:
        raise ValueError("the drawn line has no length")
    if x1 < x0:
        x0, y0, x1, y1 = x1, y1, x0, y0
    return math.degrees(math.atan2(y1 - y0, x1 - x0))


def drawn_slant(x0: float, y0: float, x1: float, y1: float) -> float:
    """Slant from a line drawn along a leaning streak, in image pixels (degrees; positive = X grows with
    the row). Raises for a line closer to horizontal than vertical."""
    if abs(y1 - y0) <= abs(x1 - x0):
        raise ValueError("draw along a near-vertical streak")
    return math.degrees(math.atan((x1 - x0) / (y1 - y0)))


# ---- slant ----------------------------------------------------------------------------------
SLANT_BLOCK = 100  # rows per block
SLANT_EXCLUDE = 40  # rows kept clear around the spectrum (band and wings)
SLANT_MAX_BLOCKS = 8  # per side
SLANT_SEARCH = 25  # px
SLANT_MIN_CORR = 0.3
SLANT_MIN_BLOCKS = 4
SLANT_FLOOR_DEG = 0.05  # a slant measured this well counts even when it is ~0


@dataclass
class SlantResult:
    ok: bool
    angle_deg: float | None
    sigma_deg: float | None
    n_blocks: int
    quality: float
    reason: str = ""
    shifts: list[tuple[float, float]] = field(default_factory=list)  # (dy, dx) of the good blocks

    @property
    def k(self) -> float:
        return math.tan(math.radians(self.angle_deg or 0.0))

    def text(self) -> str:
        if not self.ok:
            return f"Slant could not be measured: {self.reason}"
        return (f"{self.angle_deg:+.2f}° ± {self.sigma_deg:.2f}° from {self.n_blocks} block pairs "
                f"(quality {self.quality:.2f})")


def _hp(prof: np.ndarray) -> np.ndarray | None:
    p = prof - ndimage.gaussian_filter1d(prof, 12)
    s = float(np.std(p))
    return (p - p.mean()) / s if s > 0 else None


def _xcorr_shift(a: np.ndarray, ref: np.ndarray, search: int) -> tuple[float, float, bool]:
    """Shift of `a` against `ref` (px, a(x) ≈ ref(x − shift)), peak normalised correlation, edge flag."""
    n = len(a)
    lags = np.arange(-search, search + 1)
    cc = np.empty(len(lags))
    for i, s in enumerate(lags):
        if s >= 0:
            x, y = a[s:], ref[: n - s]
        else:
            x, y = a[: n + s], ref[-s:]
        cc[i] = float(np.dot(x, y)) / len(x)
    j = int(np.argmax(cc))
    edge = j in (0, len(lags) - 1)
    sub = 0.0
    if not edge:
        c0, c1, c2 = cc[j - 1], cc[j], cc[j + 1]
        den = c0 - 2 * c1 + c2
        sub = 0.5 * (c0 - c2) / den if den != 0 else 0.0
    return float(lags[j] + sub), float(cc[j]), edge


def detect_slant(img: np.ndarray, row: float, lit: tuple[int, int], block: int = SLANT_BLOCK,
                 exclude: int = SLANT_EXCLUDE) -> SlantResult:
    """Slant of a tilt-straightened frame from the sky rows: per block of rows above and below the
    spectrum, the high-passed column profile is cross-correlated with the median profile of all blocks;
    a straight line of shift vs. row distance gives k = tan(slant) and its uncertainty."""
    h, w = img.shape
    x0, x1 = max(0, lit[0] - 64), min(w, lit[1] + 65)
    r = round(row)
    starts = []
    for side in (-1, 1):
        for i in range(SLANT_MAX_BLOCKS):
            if side < 0:
                b1 = r - exclude - i * block
                b0 = b1 - block
            else:
                b0 = r + exclude + 1 + i * block
                b1 = b0 + block
            if b0 < 0 or b1 > h:
                break
            starts.append((b0, b1))
    profs, dys = [], []
    for b0, b1 in starts:
        p = _hp(np.median(img[b0:b1, x0:x1], axis=0).astype(np.float64))
        if p is not None:
            profs.append(p)
            dys.append((b0 + b1 - 1) / 2 - row)
    if len(profs) < SLANT_MIN_BLOCKS:
        return SlantResult(False, None, None, len(profs), 0.0, "too few sky rows above and below the spectrum")
    # neighbouring blocks (in row order) against each other: features shift little between them even at a
    # large slant, so the correlation stays sharp; each pair gives a slope shift / row distance
    order = np.argsort(dys)
    profs = [profs[i] for i in order]
    dys = [dys[i] for i in order]
    good, corrs = [], []
    for i in range(len(profs) - 1):
        s, c, edge = _xcorr_shift(profs[i + 1], profs[i], SLANT_SEARCH)
        corrs.append(c)
        if c >= SLANT_MIN_CORR and not edge:
            good.append((dys[i], dys[i + 1], s, c))
    quality = float(np.median([g[3] for g in good])) if good else float(max(corrs, default=0.0))
    if len(good) < SLANT_MIN_BLOCKS:
        return SlantResult(False, None, None, len(good), quality,
                           f"only {len(good)} of {len(corrs)} pairs of sky blocks show matching features "
                           f"(need {SLANT_MIN_BLOCKS})")
    span = np.array([g[1] - g[0] for g in good])
    slopes = np.array([g[2] for g in good]) / span
    wts = np.array([g[3] for g in good]) * span ** 2  # a pair further apart measures the slope better
    k = float(np.sum(wts * slopes) / np.sum(wts))
    n = len(slopes)
    var = float(np.sum(wts * (slopes - k) ** 2) / np.sum(wts)) * n / max(n - 1, 1)
    sk = math.sqrt(var / n)
    angle = math.degrees(math.atan(k))
    sigma = math.degrees(math.atan(sk))
    shifts = [((a + b) / 2, s) for a, b, s, _c in good]  # (mid row distance, shift between the pair)
    if sigma > max(abs(angle), SLANT_FLOOR_DEG):
        return SlantResult(False, None, sigma, len(good), quality,
                           f"the fit is too uncertain (±{sigma:.2f}° for {angle:+.2f}°)", shifts)
    return SlantResult(True, angle, sigma, len(good), quality, "", shifts)


# ---- corrections ----------------------------------------------------------------------------
def flip(img: np.ndarray, flip_x: bool, flip_y: bool) -> np.ndarray:
    if flip_x:
        img = img[:, ::-1]
    if flip_y:
        img = img[::-1, :]
    return np.ascontiguousarray(img, dtype=np.float32)


def rotate(img: np.ndarray, angle_deg: float, centre: tuple[float, float], threads: int = THREADS) -> np.ndarray:
    """Straighten a spectrum tilted by `angle_deg`: rotate about `centre` (row, col) by −angle."""
    return straighten(img, angle_deg, 0.0, centre, threads)


def straighten(img: np.ndarray, tilt_deg: float, slant_deg: float, centre: tuple[float, float],
               threads: int = THREADS) -> np.ndarray:
    """Tilt and slant removed in ONE resample: output→input matrix R·S about `centre` (row, col), where
    R undoes the tilt and S = [[1, 0], [tan(slant), 1]] undoes the shear (x_in = x + k·(y − row)), so the
    spectrum row stays put. Bilinear (order 1), float32, row strips in parallel (identical to one call).
    With slant 0 the matrix is exactly R (byte-identical to tilt only)."""
    if tilt_deg == 0 and slant_deg == 0:
        return img.copy()
    a = math.radians(tilt_deg)
    m = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])  # output → input
    if slant_deg != 0:
        m = m @ np.array([[1.0, 0.0], [math.tan(math.radians(slant_deg)), 1.0]])
    c = np.asarray(centre, dtype=float)
    offset = c - m @ c
    h, w = img.shape
    out = np.empty_like(img, dtype=np.float32)
    step = max(32, h // (threads * 2))

    def strip(y0: int) -> None:
        y1 = min(h, y0 + step)
        ndimage.affine_transform(img, m, offset=offset + m @ np.array([y0, 0.0]), output_shape=(y1 - y0, w),
                                 output=out[y0:y1], order=1, mode="nearest")

    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(strip, range(0, h, step)))
    return out


def apply_smile(img: np.ndarray, radius: float | None, y0: float | None) -> np.ndarray:
    """x' = x + (y − y0)² / (2R). No-op without a radius (lamp-based smile is not in v1)."""
    if not radius or y0 is None:
        return img
    h, w = img.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    shift = (yy - np.float32(y0)) ** 2 / np.float32(2 * radius)
    return ndimage.map_coordinates(img, [yy, xx + shift], order=1, mode="nearest").astype(np.float32)


def dark_hot_mask(dark_signal: np.ndarray, k: float = 8.0) -> np.ndarray:
    """Hot pixels of the sensor from the (bias-subtracted) dark master: > median + k·MAD."""
    sub = dark_signal[::4, ::4]
    med = float(np.median(sub))
    mad = float(np.median(np.abs(sub - med))) * 1.4826 or 1.0
    return (dark_signal > med + k * mad) | (dark_signal >= SATURATED - 1)


def outlier_mask(img: np.ndarray, k: float = 8.0) -> np.ndarray:
    """Isolated pixels far above (hot, cosmic) or below (over-subtracted) all four neighbours.
    A smooth spectrum never trips this: along X its neighbours have the same brightness."""
    c = img[1:-1, 1:-1]
    up, dn, lf, rt = img[:-2, 1:-1], img[2:, 1:-1], img[1:-1, :-2], img[1:-1, 2:]
    hi = np.maximum(np.maximum(up, dn), np.maximum(lf, rt))
    lo = np.minimum(np.minimum(up, dn), np.minimum(lf, rt))
    mean4 = (up + dn + lf + rt) * np.float32(0.25)
    diff = (c - mean4)[::4, ::4]
    sigma = float(np.median(np.abs(diff - np.median(diff)))) * 1.4826 or 1.0
    thr = np.float32(k) * np.sqrt(np.float32(sigma * sigma) + np.abs(mean4))
    mask = np.zeros(img.shape, bool)
    mask[1:-1, 1:-1] = ((c - hi) > thr) | ((lo - c) > thr)
    return mask


def replace_masked(img: np.ndarray, mask: np.ndarray) -> int:
    """Replace masked pixels in place by the median of their unmasked 3×3 neighbours."""
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return 0
    h, w = img.shape
    offs = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    vals = np.empty((len(ys), 8), np.float32)
    for i, (dy, dx) in enumerate(offs):
        yy = np.clip(ys + dy, 0, h - 1)
        xx = np.clip(xs + dx, 0, w - 1)
        v = img[yy, xx].astype(np.float32)
        v[mask[yy, xx]] = np.nan
        vals[:, i] = v
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN rows are handled below
        med = np.nanmedian(vals, axis=1)
    fallback = np.isnan(med)
    if fallback.any():  # a clump with no clean neighbour: use the local background instead
        med[fallback] = float(np.median(img[::8, ::8]))
    img[ys, xs] = med
    return len(ys)


@dataclass
class GeometryParams:
    flip_x: bool = False
    flip_y: bool = False
    tilt_deg: float | None = None  # None = detect in the job
    hot_pixels: bool = True
    hot_k: float = 8.0
    smile_radius: float | None = None
    smile_y0: float | None = None
    slant_deg: float | None = None  # None = not corrected (optional, off by default)


@dataclass
class GeometryResult:
    image: np.ndarray
    tilt_deg: float
    tilt_source: str
    detected: TiltResult | None
    hot_replaced: int
    centre: tuple[float, float]
    slant_deg: float = 0.0


def correct(img: np.ndarray, p: GeometryParams, ctx: Ctx, dark_signal: np.ndarray | None = None,
            source: str = "set") -> GeometryResult:
    """flip → hot pixels (before rotation, so they are not smeared) → tilt + slant (one resample) → smile."""
    out = flip(img, p.flip_x, p.flip_y)
    ctx.step(0.05)
    replaced = 0
    if p.hot_pixels:
        mask = outlier_mask(out, p.hot_k)
        if dark_signal is not None and dark_signal.shape == out.shape:
            mask |= dark_hot_mask(flip(dark_signal, p.flip_x, p.flip_y), p.hot_k)
        replaced = replace_masked(out, mask)
    ctx.step(0.35)
    detected = None
    try:
        detected = detect_tilt(out)
    except ValueError:
        if p.tilt_deg is None:
            raise
    ctx.step(0.55)
    tilt = p.tilt_deg if p.tilt_deg is not None else detected.angle_deg  # type: ignore[union-attr]
    src = source if p.tilt_deg is not None else "detected"
    centre = (detected.row, detected.x_centre) if detected else (out.shape[0] / 2, out.shape[1] / 2)
    slant = float(p.slant_deg or 0.0)
    out = straighten(out, tilt, slant, centre)  # one resample for both
    ctx.step(0.9)
    out = apply_smile(out, p.smile_radius, p.smile_y0)
    ctx.step(1.0)
    return GeometryResult(out, tilt, src, detected, replaced, centre, slant)


def flatness(img: np.ndarray, blocks: int = 16, min_amp: float = 0.35) -> float:
    """Largest deviation (px) of the spectrum centre from its median, over blocks with at least
    `min_amp` of the peak signal (the faint blue end of the reference curls by up to ~3 px)."""
    row = spectrum_row(img)
    pts = ridge_centres(img, row, lit_range(img, row), blocks)
    amps = np.array([p[2] for p in pts])
    ys = np.array([p[1] for p in pts])[amps >= min_amp * amps.max()]
    return float(np.max(np.abs(ys - np.median(ys))))
