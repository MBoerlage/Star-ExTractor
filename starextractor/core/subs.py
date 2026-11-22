"""Per-sub analysis for the Acquisition tab (and Reduce › Select "Scan folder"). No Qt.

Reads only a strip of rows around the spectrum through the memmapped FITS reader. The rows are found once
(`detect_rows`, the Extract stage's auto-placement on a tilt-straightened strip) and then locked; each sub
is checked cheaply for a moved spectrum but the rows never move by themselves.

Background noise uses the sky above the bias pedestal (1st percentile of the per-column sky).
Continuum columns (documented choice): lit columns (target signal above 10 % of its 99th percentile) whose
signal lies within ±5 % of a 101-column median-filtered continuum, i.e. not dominated by emission or
absorption lines. Continuum SNR = median per-column SNR over those columns.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage

from . import camera
from .extract import Band, Bands, auto_place
from .fitsio import open_image
from .geometry import detect_tilt

FULL_SCALE = 65520.0  # 12-bit ADC stored as 16-bit (value × 16)
SKY_CONTEXT = 150  # rows each side Re-detect needs to place the skies of a wide spectrum
MARGIN = 80  # rows above/below the ridge in the straightened strip (bands + skies + slack)
DECIMATE = 4  # col_max / sky are stored every 4th column for the plot
MOVE_HINT_PX = 3.0


@dataclass
class Rows:
    """Locked spectrum geometry: ridge row at column x_centre, tilt, band offsets from the ridge (rows)."""
    row: float
    tilt_deg: float
    x_centre: float
    spec: tuple[int, int]  # offsets from the ridge, inclusive
    sky_a: tuple[int, int]
    sky_b: tuple[int, int]
    locked_from: str = ""
    fwhm: float = 0.0  # of the main lobe; a focus measure only when shape is "single"
    shape: str = "single"  # "single" | "double-lobed" (defocused, ring-shaped star image)

    @property
    def n_rows(self) -> int:
        return self.spec[1] - self.spec[0] + 1

    def ridge(self, w: int) -> np.ndarray:
        x = np.arange(w)
        return self.row + math.tan(math.radians(self.tilt_deg)) * (x - self.x_centre)

    def moved(self, centre: float) -> Rows:
        return Rows(centre, self.tilt_deg, self.x_centre, self.spec, self.sky_a, self.sky_b, self.locked_from,
                    self.fwhm, self.shape)

    def ordered(self) -> Rows:
        """Sky A above the spectrum, Sky B below it, no overlap (rows saved by an older version)."""
        o = 100_000
        b = Bands(*(Band(t + o, u + o) for t, u in (self.spec, self.sky_a, self.sky_b))).ordered(2 * o)
        spec, a, bb = ((x.top - o, x.bottom - o) for x in (b.spectrum, b.sky_a, b.sky_b))
        return Rows(self.row, self.tilt_deg, self.x_centre, spec, a, bb, self.locked_from, self.fwhm, self.shape)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Rows:
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for k in ("spec", "sky_a", "sky_b"):
            d[k] = tuple(d[k])
        return cls(**d).ordered()


@dataclass
class SubRecord:
    path: str
    name: str
    time: str = ""
    exptime: float = 0.0
    gain: float | None = None
    temp: float | None = None
    object: str = ""  # header OBJECT: information only, never assigns a role
    peak_adu: float = 0.0
    peak_col: int = 0
    peak_fill: float = 0.0
    sat_cols: int = 0
    continuum_snr: float = 0.0
    n_continuum: int = 0
    col_max: list[float] = field(default_factory=list)  # every DECIMATE-th column, for the plot
    sky: list[float] = field(default_factory=list)
    rows: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    role: str | None = None  # 'target' | 'reference' | None (set by the user's "Collecting for" switch)
    included: bool = True
    user_override: bool = False
    shift_px: float = 0.0
    pedestal: float = 0.0
    g_source: str = ""
    ms: float = 0.0
    size: int = 0
    mtime: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> SubRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def limit_adu(fraction: float = 0.8) -> float:
    return fraction * FULL_SCALE


# ---- strip access ---------------------------------------------------------------------------------------
def _straight_strip(img, rows: Rows, above: int = MARGIN, below: int = MARGIN) -> tuple[np.ndarray, int]:
    """(above+below+1, w) float32: row k is ridge + (k − above) in every column (integer shift per column)."""
    h, w = img.shape
    ridge = np.rint(rows.ridge(w)).astype(int)
    y0 = max(0, int(ridge.min()) - above)
    y1 = min(h, int(ridge.max()) + below + 1)
    strip = img.strip(y0, y1)
    idx = ridge[None, :] - y0 + np.arange(-above, below + 1)[:, None]
    np.clip(idx, 0, strip.shape[0] - 1, out=idx)
    return np.take_along_axis(strip, idx, axis=0), w


def rows_margin(rows: Rows) -> tuple[int, int]:
    """Rows above / below the ridge that hold every band of `rows` (at least MARGIN each)."""
    lo = min(rows.spec[0], rows.sky_a[0], rows.sky_b[0])
    hi = max(rows.spec[1], rows.sky_a[1], rows.sky_b[1])
    return max(MARGIN, -int(lo) + 2), max(MARGIN, int(hi) + 2)


def detect_rows(path: str | Path, tilt_deg: float | None = None, mode: str = "auto") -> Rows:
    """Full-frame read once: tilt (unless a saved one is given), then the Extract stage's auto-placement on
    the straightened strip."""
    img = open_image(path)
    full = img.read()
    t = detect_tilt(full)
    tilt = t.angle_deg if tilt_deg is None else tilt_deg
    probe = Rows(t.row, tilt, t.x_centre, (0, 0), (0, 0), (0, 0))
    strip, _ = _straight_strip(img, probe)
    place = auto_place(strip, mode=mode)
    off = place.row  # ridge index inside the strip
    b = place.bands
    centre = t.row + (off - MARGIN)
    return Rows(centre, tilt, t.x_centre, (b.spectrum.top - off, b.spectrum.bottom - off),
                (b.sky_a.top - off, b.sky_a.bottom - off), (b.sky_b.top - off, b.sky_b.bottom - off),
                Path(path).name, float(place.fwhm), place.mode)


def detect_rows_in(path: str | Path, tilt_deg: float, x_centre: float, top: float, bottom: float,
                   mode: str = "auto") -> Rows:
    """Auto-placement limited to the rows top..bottom (straightened along the given tilt, rows at the centre
    column): finds a faint spectrum that a brighter one elsewhere would hide. A window under 120 rows is
    widened around its centre. The spectrum is always searched in that window only; when the window is too
    short for the skies of a wide spectrum (×16 shows 135 rows), the bands are then placed on a taller strip
    centred on the spectrum found, kept only if it finds the same spectrum."""
    img = open_image(path)
    h = img.shape[0]

    def place_in(c: int, half: float):
        above, below = int(min(c, half)), int(min(h - 1 - c, half))
        strip, _ = _straight_strip(img, Rows(c, tilt_deg, x_centre, (0, 0), (0, 0), (0, 0)), above, below)
        place = auto_place(strip, mode=mode)
        return c + (place.row - above), place

    c = min(max(round((top + bottom) / 2), 0), h - 1)
    row, place = place_in(c, max((bottom - top) / 2, 60))
    b = place.bands
    if b.sky_a.bottom >= b.spectrum.top or b.sky_b.top <= b.spectrum.bottom or (bottom - top) / 2 < SKY_CONTEXT:
        wide_row, wide = place_in(min(max(round(row), 0), h - 1), SKY_CONTEXT)
        if abs(wide_row - row) <= max(2.0, place.fwhm / 2):
            row, place = wide_row, wide
    off = place.row
    b = place.bands
    return Rows(row, tilt_deg, x_centre, (b.spectrum.top - off, b.spectrum.bottom - off),
                (b.sky_a.top - off, b.sky_a.bottom - off), (b.sky_b.top - off, b.sky_b.bottom - off),
                f"{Path(path).name} rows {round(top)}–{round(bottom)}", float(place.fwhm), place.mode)


# ---- one sub --------------------------------------------------------------------------------------------
def analyse_sub(path: str | Path, rows: Rows, linearity_frac: float = 0.8) -> SubRecord:
    t0 = time.perf_counter()
    p = Path(path)
    img = open_image(p)
    h = img.header
    # the strip reaches past the outermost band (a sky dragged far out), never less than MARGIN
    above, below = rows_margin(rows)
    strip, _w = _straight_strip(img, rows, above, below)
    m = above
    tgt = strip[m + rows.spec[0]: m + rows.spec[1] + 1]
    sky_rows = np.vstack([strip[m + rows.sky_a[0]: m + rows.sky_a[1] + 1],
                          strip[m + rows.sky_b[0]: m + rows.sky_b[1] + 1]])
    sky = np.median(sky_rows, axis=0)
    n = tgt.shape[0]
    col_max = np.partition(tgt, n - 2, axis=0)[n - 2] if n >= 2 else tgt[0]  # hot pixels need 2 to count
    limit = limit_adu(linearity_frac)
    peak_col = int(np.argmax(col_max))
    peak = float(col_max[peak_col])
    G, R, src = camera.gain_and_noise(h)
    spec = tgt.sum(axis=0, dtype=np.float64) - sky.astype(np.float64) * n
    signal_e = np.clip(spec, 0, None) * G
    # sky photons only: the bias pedestal is an offset, not noise; estimated from the darkest sky columns
    bg_e = np.clip(sky - np.percentile(sky, 1), 0, None) * G
    noise_e = np.sqrt(signal_e + n * (bg_e + R * R))
    snr = np.where(noise_e > 0, signal_e / noise_e, 0.0)
    cont = continuum_columns(spec)
    # spectrum position check on the straightened strip (median over lit columns)
    prof = np.median(strip[:, cont] if cont.size >= 10 else strip, axis=1)
    prof = prof - np.median(prof)
    k = int(np.argmax(ndimage.gaussian_filter1d(prof, 1.5)))
    lo, hi = max(0, k - 4), min(len(prof), k + 5)
    wts = np.clip(prof[lo:hi], 0, None)
    shift = float((np.arange(lo, hi) * wts).sum() / wts.sum() - m) if wts.sum() > 0 else 0.0
    st = p.stat()
    rec = SubRecord(
        path=str(p), name=p.name, time=str(h.get("DATE-OBS") or ""), exptime=float(h.get("EXPTIME") or 0),
        gain=_num(h.get("GAIN")), temp=_num(h.get("CCD-TEMP")), object=str(h.get("OBJECT") or ""),
        peak_adu=peak, peak_col=peak_col, peak_fill=peak / limit, sat_cols=int((col_max >= limit).sum()),
        continuum_snr=float(np.median(snr[cont])) if cont.size else 0.0, n_continuum=int(cont.size),
        col_max=[float(v) for v in col_max[::DECIMATE]], sky=[float(v) for v in sky[::DECIMATE]],
        rows=rows.to_json(), shift_px=shift, pedestal=float(np.median(sky)), g_source=src,
        size=st.st_size, mtime=st.st_mtime)
    rec.flags = own_flags(rec)
    rec.ms = (time.perf_counter() - t0) * 1000
    return rec


def sub_view(path: str | Path, rows: Rows) -> dict[str, Any]:
    """Display data for one sub (runs in a worker): the whole frame straightened along the tilt (row k is
    detector row k at the centre column) as a DisplayImage, the row profile (median over continuum columns)
    and the sky level that is subtracted."""
    from .display import DisplayImage, pooled, stretch, thumbnail

    img = open_image(path)
    h = img.shape[0]
    r0 = min(max(round(float(rows.row)), 0), h - 1)
    full, w = _straight_strip(img, rows, r0, h - 1 - r0)
    tgt = full[r0 + rows.spec[0]: r0 + rows.spec[1] + 1]
    sky_rows = np.vstack([full[r0 + rows.sky_a[0]: r0 + rows.sky_a[1] + 1],
                          full[r0 + rows.sky_b[0]: r0 + rows.sky_b[1] + 1]])
    sky = np.median(sky_rows, axis=0)
    cont = continuum_columns(tgt.sum(axis=0, dtype=np.float64) - sky.astype(np.float64) * tgt.shape[0])
    cols = cont if cont.size >= 10 else np.arange(w)
    near = full[max(0, r0 - MARGIN): r0 + MARGIN + 1, ::4]  # levels from the spectrum and its skies
    lo, hi = (float(v) for v in np.percentile(near, [1.0, 99.9]))
    lv = (lo, hi if hi > lo else lo + 1)
    u8 = stretch(full, lv)
    disp = DisplayImage(full, u8, thumbnail(u8), lv, float(r0), pooled(u8))
    return {"name": Path(path).name, "path": str(path), "display": disp, "width": w,
            "profile": np.median(full[:, cols[::2]], axis=1), "sky": float(np.median(sky_rows[:, cols])),
            "rows": rows}


def continuum_columns(spec: np.ndarray) -> np.ndarray:
    top = float(np.percentile(spec, 99))
    if top <= 0:
        return np.array([], dtype=int)
    lit = spec > 0.10 * top
    smooth = ndimage.median_filter(spec, 101, mode="nearest")
    near = np.abs(spec - smooth) <= 0.05 * np.abs(smooth)
    return np.flatnonzero(lit & near & (smooth > 0))


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- flags and set-level numbers ------------------------------------------------------------------------
SATURATED = "saturated"
FEW_CONTINUUM = "few continuum columns"
LOW_SNR = "SNR low"


def own_flags(rec: SubRecord) -> list[str]:
    out = []
    if rec.peak_fill >= 1.0:
        out.append(SATURATED)
    if rec.n_continuum < 10:
        out.append(FEW_CONTINUUM)
    return out


def refresh_flags(records: list[SubRecord], sigma: float = 2.0) -> None:
    """Recompute flags for one set (in place). The SNR rule needs 3+ subs. Its mean and sigma come from every
    unsaturated sub the user has not excluded, so a sub flagged and dropped earlier does not raise the floor
    for itself. A sub the user ticked or unticked by hand (user_override) keeps its `included` state."""
    base = [r for r in records if SATURATED not in own_flags(r) and not (r.user_override and not r.included)]
    snrs = np.array([r.continuum_snr for r in base])
    floor = float(snrs.mean() - sigma * snrs.std()) if len(snrs) >= 3 else None
    for r in records:
        r.flags = own_flags(r)
        if floor is not None and r.continuum_snr < floor:
            r.flags.append(LOW_SNR)
        if not r.user_override:
            r.included = not r.flags


def stack_snr(snrs: list[float]) -> float:
    """Quadrature sum of per-sub continuum SNRs (independent noise, equal weights)."""
    return float(math.sqrt(sum(s * s for s in snrs)))


def subs_needed(target_snr: float, per_sub_snr: float) -> int | None:
    if per_sub_snr <= 0:
        return None
    return math.ceil((target_snr / per_sub_snr) ** 2)


def safe_exposure(exptime: float, peak: float, pedestal: float, limit: float, safety: float = 0.95) -> float | None:
    """Longest exposure keeping the peak under safety × limit, assuming signal above the pedestal scales
    linearly with exposure."""
    signal = peak - pedestal
    if signal <= 0 or exptime <= 0:
        return None
    return exptime * (safety * limit - pedestal) / signal


def scaled_snr(per_sub_snr: float, exptime: float, new_exptime: float) -> float:
    """Per-sub SNR at another exposure, signal-limited: ∝ √t."""
    return per_sub_snr * math.sqrt(new_exptime / exptime) if exptime > 0 else 0.0


def stack_curve(snrs: list[float]) -> list[float]:
    acc, out = 0.0, []
    for s in snrs:
        acc += s * s
        out.append(math.sqrt(acc))
    return out


def transparency_hint(snrs: list[float]) -> bool:
    """Three consecutive stack points below 0.7 or above 1.5 of the √N reference from sub 1."""
    if len(snrs) < 3 or snrs[0] <= 0:
        return False
    curve = stack_curve(snrs)
    run = 0
    for n, s in enumerate(curve, start=1):
        ratio = s / (snrs[0] * math.sqrt(n))
        run = run + 1 if (ratio < 0.7 or ratio > 1.5) else 0
        if run >= 3:
            return True
    return False


def moved_hint(rec: SubRecord) -> str | None:
    """'Spectrum moved by X px. Re-detect?' when the profile centroid is more than 3 px off the locked rows."""
    if abs(rec.shift_px) > MOVE_HINT_PX:
        return f"Spectrum moved by {rec.shift_px:+.1f} px. Re-detect?"
    return None
