"""Step 3 · 1D extraction: spectrum band minus sky (mean of Sky A and Sky B), summed over rows.

Bands are inclusive row ranges in the geometry-corrected image. The profile runs along X in
detector columns (array index 0 = pixel 0, the same origin as the BASS coefficients).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy import ndimage

from .geometry import lit_range, spectrum_row

SKY_WIDTH = 30


@dataclass
class Band:
    top: int
    bottom: int  # inclusive; top <= bottom

    @property
    def rows(self) -> int:
        return self.bottom - self.top + 1

    @property
    def centre(self) -> float:
        return (self.top + self.bottom) / 2

    def clipped(self, h: int) -> Band:
        t, b = sorted((int(self.top), int(self.bottom)))
        t, b = max(0, min(t, h - 1)), max(0, min(b, h - 1))
        return Band(t, b)


@dataclass
class Bands:
    spectrum: Band
    sky_a: Band
    sky_b: Band

    def to_json(self) -> dict[str, list[int]]:
        return {k: [v["top"], v["bottom"]] for k, v in asdict(self).items()}

    @classmethod
    def from_json(cls, d: dict[str, list[int]]) -> Bands:
        return cls(Band(*d["spectrum"]), Band(*d["sky_a"]), Band(*d["sky_b"]))

    def clipped(self, h: int) -> Bands:
        return Bands(self.spectrum.clipped(h), self.sky_a.clipped(h), self.sky_b.clipped(h))


def row_profile(img: np.ndarray, lit: tuple[int, int] | None = None) -> np.ndarray:
    """Median over the lit columns per row (robust to residual hot pixels)."""
    w = img.shape[1]
    a, b = lit or (w // 8, w - w // 8)
    return np.median(img[:, a:b + 1:4], axis=1)


def fwhm_rows(prof: np.ndarray, row: int, window: int = 40) -> float:
    """FWHM (rows) of the star across the slit, by linear interpolation of the half-maximum."""
    lo, hi = max(0, row - window), min(len(prof), row + window + 1)
    seg = prof[lo:hi] - np.median(np.r_[prof[max(0, lo - 30):lo], prof[hi:hi + 30]] if lo or hi < len(prof)
                                  else prof)
    k = row - lo
    half = seg[k] / 2
    if half <= 0:
        return 6.0
    left = k
    while left > 0 and seg[left] > half:
        left -= 1
    right = k
    while right < len(seg) - 1 and seg[right] > half:
        right += 1
    lx = left + (half - seg[left]) / (seg[left + 1] - seg[left]) if seg[left + 1] != seg[left] else left
    rx = right - 1 + (seg[right - 1] - half) / (seg[right - 1] - seg[right]) if seg[right - 1] != seg[right] else right
    return float(max(2.0, rx - lx))


def slit_rows(prof: np.ndarray, star: int, fwhm: float) -> tuple[int, int]:
    """Rows of the illuminated slit (sky level clearly above the unexposed sensor)."""
    p = prof.astype(np.float64).copy()
    guard = int(4 * fwhm) + 10
    p[max(0, star - guard):star + guard] = np.nan
    smooth = ndimage.median_filter(np.nan_to_num(p, nan=float(np.nanmedian(p))), 31)
    outside = float(np.percentile(smooth, 5))
    inside = float(np.median(smooth[max(0, star - 300):star + 300]))
    if inside - outside < 5:  # no visible slit edge: whole frame
        return 0, len(prof) - 1
    lit = smooth > outside + 0.5 * (inside - outside)
    top, bot = star, star
    while top > 0 and lit[top - 1]:
        top -= 1
    while bot < len(prof) - 1 and lit[bot + 1]:
        bot += 1
    return top, bot


@dataclass
class Placement:
    bands: Bands
    row: int
    fwhm: float
    slit: tuple[int, int]


def auto_place(img: np.ndarray, sky_width: int = SKY_WIDTH) -> Placement:
    """Spectrum band ≈ 2×FWHM centred on the brightest row; skies either side, outside the wings,
    inside the lit slit."""
    h = img.shape[0]
    row0 = spectrum_row(img)
    lit = lit_range(img, row0)
    prof = row_profile(img, lit)
    sm = ndimage.gaussian_filter1d(prof - np.median(prof), 1.5)
    lo, hi = max(0, int(row0) - 40), min(h, int(row0) + 41)
    row = lo + int(np.argmax(sm[lo:hi]))
    fwhm = fwhm_rows(prof, row)
    half = max(2, round(fwhm))
    spec = Band(row - half, row + half)
    gap = max(round(2.5 * fwhm), 12)
    slit = slit_rows(prof, row, fwhm)
    a_bot = max(slit[0] + 1, spec.top - gap)
    b_top = min(slit[1] - 1, spec.bottom + gap)
    sky_a = Band(max(slit[0], a_bot - sky_width + 1), a_bot)
    sky_b = Band(b_top, min(slit[1], b_top + sky_width - 1))
    return Placement(Bands(spec, sky_a, sky_b).clipped(h), row, fwhm, slit)


@dataclass
class Extraction:
    profile: np.ndarray  # sky-subtracted sum over the spectrum band (W,)
    raw_sum: np.ndarray  # same without sky subtraction
    sky: np.ndarray  # per-column sky level per row at the spectrum centre (W,)
    rows: int
    peak_row: float  # brightest row inside the band (centroid)


def sky_per_column(img: np.ndarray, bands: Bands, mode: str = "mean") -> np.ndarray:
    """Sky per row and column: mean of the two sky bands, or a line through them evaluated at the
    spectrum centre (`mode="linear"`). One sky band is enough when the other is empty."""
    a = img[bands.sky_a.top:bands.sky_a.bottom + 1].mean(axis=0, dtype=np.float64)
    b = img[bands.sky_b.top:bands.sky_b.bottom + 1].mean(axis=0, dtype=np.float64)
    if mode == "linear" and bands.sky_a.centre != bands.sky_b.centre:
        t = (bands.spectrum.centre - bands.sky_a.centre) / (bands.sky_b.centre - bands.sky_a.centre)
        return (a + t * (b - a)).astype(np.float32)
    return ((a + b) / 2).astype(np.float32)


def extract(img: np.ndarray, bands: Bands, subtract_sky: bool = True, sky_mode: str = "mean") -> Extraction:
    bands = bands.clipped(img.shape[0])
    s = bands.spectrum
    block = img[s.top:s.bottom + 1]
    raw_sum = block.sum(axis=0, dtype=np.float64).astype(np.float32)
    sky = sky_per_column(img, bands, sky_mode) if subtract_sky else np.zeros(img.shape[1], np.float32)
    profile = raw_sum - np.float32(s.rows) * sky
    rp = np.median(block, axis=1) - (float(np.median(sky)) if subtract_sky else 0.0)
    w = np.clip(rp, 0, None)
    peak = float(s.top + (np.arange(len(w)) * w).sum() / w.sum()) if w.sum() > 0 else s.centre
    return Extraction(profile.astype(np.float32), raw_sum, sky, s.rows, peak)


def sky_subtracted(img: np.ndarray, bands: Bands, sky_mode: str = "mean") -> np.ndarray:
    """2D view with the per-column sky removed (what the extraction sums over)."""
    return (img - sky_per_column(img, bands.clipped(img.shape[0]), sky_mode)[None, :]).astype(np.float32)


def centring(bands: Bands, peak_row: float) -> tuple[str, str]:
    """('ok'|'warn', message) about where the star sits inside the spectrum band."""
    s = bands.spectrum
    off = peak_row - s.centre
    tol = max(2.0, 0.25 * s.rows)
    if s.top <= peak_row <= s.bottom and abs(off) <= tol:
        return "ok", f"Star centred in the band. Peak row {peak_row:.0f}."
    return "warn", (f"Star is off-centre: peak row {peak_row:.0f}, band centre {s.centre:.0f}. "
                    "Use Auto-place or move the band.")
