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

# ---- double-lobed (defocused) profiles: a ring-shaped star image gives two humps across the slit ----------
NSIGMA_LOBE = 5.0  # a lobe must stand this many noise sigmas above the sky (and be this prominent)
LOBE_MIN_FRAC = 0.15  # ... and reach this fraction of the main peak's height above the sky
VALLEY_MIN_FRAC = 0.15  # merge only when the dip between two lobes stays above sky + this × the lower lobe
MAX_LOBE_SEP_FWHM = 4.0  # merge only lobes at most this many main-lobe FWHMs apart
SOURCE_SEARCH_FWHM = 10.0  # lobes (and other sources) are looked for within this many FWHMs of the main peak
EDGE_FRAC = 0.10  # spectrum edge: where the profile falls to this fraction of the outer lobe's height
EDGE_NOISE_SIGMA = 2.0  # ... or to this many noise sigmas, whichever is higher
EDGE_PAD = 2  # rows added outside each edge
LOBE_SMOOTH = 1.5  # Gaussian sigma (rows) of the profile used for finding peaks (edges use the unsmoothed one)
LOBE_MODES = ("auto", "single", "all")  # Auto | Single peak only | Include all lobes
SINGLE, DOUBLE = "single", "double-lobed"


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

    def ordered(self, h: int) -> Bands:
        """Clipped to the frame and in order, never overlapping: Sky A above the spectrum, Sky B below it.
        A sky on the wrong side is mirrored about the spectrum; one reaching into it is cut back."""
        if h < 5:
            return self.clipped(h)
        s = self.spectrum.clipped(h)
        top = min(max(s.top, 1), h - 2)  # room for at least one sky row each side
        s = Band(top, min(max(s.bottom, top), h - 2))
        c = s.centre

        def mirror(b: Band) -> Band:
            return Band(round(2 * c - b.bottom), round(2 * c - b.top))

        a, b = self.sky_a, self.sky_b
        a = mirror(a) if a.centre > c else a
        b = mirror(b) if b.centre < c else b
        a_bot = min(a.bottom, s.top - 1)
        a = Band(max(0, min(a.top, a_bot)), max(0, a_bot))
        b_top = max(b.top, s.bottom + 1)
        b = Band(min(h - 1, b_top), min(h - 1, max(b.bottom, b_top)))
        return Bands(s, a, b)


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
    if np.isnan(p).all():  # a short window around a wide star: no rows outside it to find the slit edges
        return 0, len(prof) - 1
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
    row: int  # the main (brightest) lobe
    fwhm: float  # of the main lobe: a focus measure only for single-peak profiles
    slit: tuple[int, int]
    mode: str = SINGLE  # "single" | "double-lobed"
    lobes: tuple[int, ...] = ()  # rows of the lobes merged into the spectrum (one for a single peak)
    found: int = 1  # lobes found near the main peak (merged or not)
    valley_frac: float | None = None  # dip between the merged lobes / the lower lobe (above the sky)
    warnings: tuple[str, ...] = ()

    @property
    def span(self) -> int:
        return self.bands.spectrum.rows

    def shifted(self, dy: int, h: int) -> Placement:
        b = self.bands
        moved = Bands(*(Band(x.top + dy, x.bottom + dy) for x in (b.spectrum, b.sky_a, b.sky_b))).ordered(h)
        return Placement(moved, self.row + dy, self.fwhm, (self.slit[0] + dy, self.slit[1] + dy), self.mode,
                         tuple(r + dy for r in self.lobes), self.found, self.valley_frac, self.warnings)


@dataclass
class Lobes:
    """What the lobe search saw around the main peak (rows in the image)."""

    rows: list[int]  # every lobe found, top to bottom
    heights: list[float]  # above the sky, smoothed profile
    merged: list[int]  # indexes into rows of the lobes that form the spectrum
    sky: float
    noise: float  # of the unsmoothed lobe profile
    profile: np.ndarray  # unsmoothed, sky-subtracted
    smooth: np.ndarray
    valley_frac: float | None
    warnings: list[str]


def lobe_profile(img: np.ndarray, lit: tuple[int, int] | None = None) -> np.ndarray:
    """Interquartile mean over the lit columns per row: robust to hot pixels and bright emission-line columns
    like the median, but not quantised (camera data often comes in steps of 16 ADU)."""
    w = img.shape[1]
    a, b = lit or (w // 8, w - w // 8)
    cols = np.sort(img[:, a:b + 1:2], axis=1)
    n = cols.shape[1]
    return cols[:, n // 4: max(n // 4 + 1, 3 * n // 4)].mean(axis=1)


def find_lobes(img: np.ndarray, lit: tuple[int, int], row: int, fwhm: float, mode: str = "auto") -> Lobes:
    """Significant lobes near the main peak and which of them belong to the same star image (docs: the
    constants above). mode "all" merges every lobe found."""
    from scipy.signal import find_peaks

    prof = lobe_profile(img, lit).astype(np.float64)
    h = len(prof)
    near = int(max(SOURCE_SEARCH_FWHM * fwhm, 40))
    lo, hi = max(0, row - near), min(h, row + near + 1)
    outer = np.r_[0:lo, hi:h]
    if len(outer) < 20:  # a short strip: the outer quarter on each side
        q = max(5, h // 6)
        outer = np.r_[0:q, h - q:h]
    sky = float(np.median(prof[outer]))
    p = prof - sky
    sm = ndimage.gaussian_filter1d(p, LOBE_SMOOTH)
    noise = float(1.4826 * np.median(np.abs(p[outer] - np.median(p[outer])))) or 1e-6
    noise_s = float(1.4826 * np.median(np.abs(sm[outer] - np.median(sm[outer])))) or 1e-6
    k0, k1 = max(lo, row - 3), min(hi, row + 4)
    main = k0 + int(np.argmax(sm[k0:k1]))
    main_h = float(sm[main])
    thr = max(NSIGMA_LOBE * noise_s, LOBE_MIN_FRAC * main_h)
    idx, _props = find_peaks(sm[lo:hi], height=thr, prominence=NSIGMA_LOBE * noise_s)
    rows = sorted({int(lo + i) for i in idx} | {main})
    heights = [float(sm[r]) for r in rows]
    m = rows.index(main)
    warnings: list[str] = []
    if mode == "all":
        merged = list(range(len(rows)))
    else:
        merged = [m]

        def joins(a: int, b: int) -> bool:
            ra, rb = rows[a], rows[b]
            ha, hb = heights[a], heights[b]
            lower = min(ha, hb)
            valley = float(sm[min(ra, rb):max(ra, rb) + 1].min())
            return (valley >= VALLEY_MIN_FRAC * lower and abs(ra - rb) <= MAX_LOBE_SEP_FWHM * fwhm
                    and lower >= LOBE_MIN_FRAC * max(ha, hb))

        for step in (-1, 1):  # grow the group outward from the main lobe, neighbour by neighbour
            j = m
            while 0 <= j + step < len(rows) and joins(j, j + step):
                j += step
                merged.append(j)
        merged.sort()
        if len(merged) < len(rows):
            warnings.append("second source in the slit, not merged")
    valley = None
    if len(merged) > 1:
        a, b = rows[merged[0]], rows[merged[-1]]
        lower = min(heights[i] for i in merged)
        valley = float(sm[a:b + 1].min()) / lower if lower > 0 else None
    return Lobes(rows, heights, merged, sky, noise, p, sm, valley, warnings)


def _edge(p: np.ndarray, start: int, step: int, height: float, noise: float) -> int:
    """Walk from a lobe centre outward to where the profile falls to EDGE_FRAC of the lobe (or the noise
    floor); that row, padded by EDGE_PAD."""
    thr = max(EDGE_FRAC * height, EDGE_NOISE_SIGMA * noise)
    r = start
    while 0 <= r + step < len(p) and p[r + step] > thr:
        r += step
    return r + step * (1 + EDGE_PAD)


def _half_width(p: np.ndarray, start: int, step: int, height: float) -> float:
    """Rows from a lobe centre outward to its half height (at least 1)."""
    r = start
    while 0 <= r + step < len(p) and p[r + step] > height / 2:
        r += step
    return float(max(1, abs(r - start) + 1))


def _trim_sky(sky: Band, keep_near: int, blocked: list[tuple[int, int]]) -> tuple[Band, bool]:
    """Cut a sky band back so it stays clear of the rows of other sources; keeps the part nearest the
    spectrum (`keep_near`). Returns (band, trimmed?)."""
    top, bot = sky.top, sky.bottom
    for b0, b1 in blocked:
        if b1 < top or b0 > bot:
            continue
        if keep_near <= b0:  # Sky B: keep the rows between the spectrum and the other source
            bot = min(bot, b0 - 1)
        else:  # Sky A
            top = max(top, b1 + 1)
    if top > bot:
        top = bot = sky.bottom if keep_near > sky.bottom else sky.top
    return Band(top, bot), (top, bot) != (sky.top, sky.bottom)


def auto_place(img: np.ndarray, sky_width: int = SKY_WIDTH, mode: str = "auto") -> Placement:
    """Spectrum band ≈ 2×FWHM centred on the brightest row; skies either side, outside the wings, inside the
    lit slit. A double-lobed (defocused, ring-shaped) star image gets one band over both lobes (find_lobes);
    mode "single" keeps the main peak only (the behaviour before), "all" spans every lobe found. A profile with
    one lobe gives exactly the single-peak rows."""
    h = img.shape[0]
    row0 = spectrum_row(img)
    lit = lit_range(img, row0)
    prof = row_profile(img, lit)
    sm = ndimage.gaussian_filter1d(prof - np.median(prof), 1.5)
    lo, hi = max(0, int(row0) - 40), min(h, int(row0) + 41)
    row = lo + int(np.argmax(sm[lo:hi]))
    fwhm = fwhm_rows(prof, row)
    single = _place_single(img, prof, row, fwhm, sky_width)
    if mode == "single":
        return single
    lb = find_lobes(img, lit, row, fwhm, mode)
    blocked = [] if len(lb.merged) == len(lb.rows) else [
        (_edge(lb.profile, r, -1, hh, lb.noise), _edge(lb.profile, r, 1, hh, lb.noise))
        for i, (r, hh) in enumerate(zip(lb.rows, lb.heights, strict=True)) if i not in lb.merged]
    if len(lb.merged) == 1:
        return _with_blocked(single, blocked, lb, h)
    first, last = lb.merged[0], lb.merged[-1]
    top = _edge(lb.profile, lb.rows[first], -1, lb.heights[first], lb.noise)
    bot = _edge(lb.profile, lb.rows[last], 1, lb.heights[last], lb.noise)
    spec = Band(max(0, top), min(h - 1, bot))
    lobe_fwhm = (_half_width(lb.smooth, lb.rows[first], -1, lb.heights[first])
                 + _half_width(lb.smooth, lb.rows[last], 1, lb.heights[last]))  # one hump's width, on the outer flanks
    placed = _place_skies(spec, prof, row, lobe_fwhm, h, sky_width, max(round(2.5 * lobe_fwhm), 12))
    out = Placement(placed[0], row, fwhm, placed[1], DOUBLE, tuple(lb.rows[i] for i in lb.merged),
                    len(lb.rows), lb.valley_frac, tuple(lb.warnings))
    return _with_blocked(out, blocked, lb, h)


def _with_blocked(pl: Placement, blocked: list[tuple[int, int]], lb: Lobes, h: int) -> Placement:
    """Skies kept clear of other sources in the slit; the lobe facts recorded on the placement."""
    warnings = list(lb.warnings)
    b = pl.bands
    sa, cut_a = _trim_sky(b.sky_a, b.spectrum.top, blocked)
    sb, cut_b = _trim_sky(b.sky_b, b.spectrum.bottom, blocked)
    if cut_a:
        warnings.append("Sky A shortened: another source in the slit")
    if cut_b:
        warnings.append("Sky B shortened: another source in the slit")
    bands = Bands(b.spectrum, sa, sb).ordered(h) if cut_a or cut_b else b
    lobes = pl.lobes or (pl.row,)
    return Placement(bands, pl.row, pl.fwhm, pl.slit, pl.mode, lobes, len(lb.rows), pl.valley_frac,
                     tuple(warnings))


def _place_single(img: np.ndarray, prof: np.ndarray, row: int, fwhm: float, sky_width: int) -> Placement:
    """The single-peak placement (unchanged): band row ± FWHM, skies 2.5 FWHM (at least 12 rows) away."""
    h = img.shape[0]
    half = max(2, round(fwhm))
    spec = Band(row - half, row + half)
    bands, slit = _place_skies(spec, prof, row, fwhm, h, sky_width, max(round(2.5 * fwhm), 12))
    return Placement(bands, row, fwhm, slit)


def _place_skies(spec: Band, prof: np.ndarray, row: int, fwhm: float, h: int, sky_width: int,
                 gap: int) -> tuple[Bands, tuple[int, int]]:
    slit = slit_rows(prof, row, fwhm)
    # skies inside the lit slit, but never into the spectrum band: a slit edge found inside the star
    # (a short strip around a wide star) is not a slit edge and is ignored
    lo_edge = slit[0] if slit[0] < spec.top - 1 else 0
    hi_edge = slit[1] if slit[1] > spec.bottom + 1 else h - 1
    a_bot = min(max(lo_edge + 1, spec.top - gap), spec.top - 1)
    b_top = max(min(hi_edge - 1, spec.bottom + gap), spec.bottom + 1)
    sky_a = Band(max(lo_edge, a_bot - sky_width + 1), a_bot)
    sky_b = Band(b_top, min(hi_edge, b_top + sky_width - 1))
    return Bands(spec, sky_a, sky_b).ordered(h), slit


VIEW_MIN_ROWS = 120  # a window shorter than this is widened around its centre
VIEW_SKY_CONTEXT = 150  # rows each side used to place the skies once the spectrum is found in the window


def auto_place_in(img: np.ndarray, top: float, bottom: float, sky_width: int = SKY_WIDTH,
                  mode: str = "auto") -> Placement:
    """Auto-placement with the spectrum searched only in rows top..bottom (the rows in view), so a brighter or
    broader band elsewhere in the frame cannot win. The skies are then placed on a strip of ±VIEW_SKY_CONTEXT
    rows around the spectrum found, kept only when that strip finds the same spectrum."""
    h = img.shape[0]
    c = (top + bottom) / 2
    half = max((bottom - top) / 2, VIEW_MIN_ROWS / 2)
    y0, y1 = max(0, int(c - half)), min(h, int(c + half) + 1)
    if y1 - y0 < 8:
        return auto_place(img, sky_width, mode)
    pl = auto_place(img[y0:y1], sky_width, mode)
    row = y0 + pl.row
    w0, w1 = max(0, row - VIEW_SKY_CONTEXT), min(h, row + VIEW_SKY_CONTEXT + 1)
    wide = auto_place(img[w0:w1], sky_width, mode)
    if abs(w0 + wide.row - row) <= max(2.0, pl.fwhm / 2):
        pl, y0 = wide, w0
    return pl.shifted(y0, h)


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
