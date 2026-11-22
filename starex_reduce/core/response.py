"""Step 5 · instrument response from the reference star and a Pickles (1998) library template.

R(λ) = observed reference (smoothed to the library's ~10 Å resolution) / library template,
Balmer lines and telluric bands masked, then smoothed. Corrected = observed / R, optionally times
10^(0.4·k(λ)·(X_target − X_reference)) with the Kitt Peak extinction k(λ) (data/kpnoextinct.dat).
"""
from __future__ import annotations

import csv
import itertools
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.interpolate import make_interp_spline

PACKAGE = Path(__file__).resolve().parents[1]
LIBRARY_DIR = PACKAGE / "reference_spectra"
DATA_DIR = PACKAGE / "data"
LIBRARY_FWHM_A = 10.0  # Pickles HILIB: "approximately 10A resolution" (README.HILIB)
LETTERS = "OBAFGKM"
CLASSES = ("I", "II", "III", "IV", "V")
# Half-widths (Å) of the Balmer masks: A-type wings are broad.
BALMER_MASK = {"Hα": (6562.852, 60.0), "Hβ": (4861.33, 50.0), "Hγ": (4340.47, 40.0), "Hδ": (4101.74, 35.0),
               "Hε": (3970.07, 30.0)}
SMOOTHING_BIN_A = {"low": 25.0, "medium": 50.0, "high": 100.0, "very high": 200.0}


# ---- library -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Template:
    stem: str  # file name without .dat, e.g. "a47iv"
    letter: str
    sub_lo: float
    sub_hi: float
    lum: str

    @property
    def label(self) -> str:
        sub = f"{self.sub_lo:g}" if self.sub_lo == self.sub_hi else f"{self.sub_lo:g}-{self.sub_hi:g}"
        return f"{self.letter}{sub}{self.lum}"

    @property
    def index(self) -> float:
        return LETTERS.index(self.letter) * 10 + (self.sub_lo + self.sub_hi) / 2

    @property
    def path(self) -> Path:
        return LIBRARY_DIR / f"{self.stem}.dat"


_NAME = re.compile(r"^([obafgkm])(\d+(?:\.\d+)?)(i{1,3}|iv|v)$")


def parse_template_name(stem: str) -> Template | None:
    """'a0v' → A0V, 'a47iv' → A4-7IV, 'b12iii' → B1-2III, 'm2.5v' → M2.5V, 'm10iii' → M10III."""
    m = _NAME.match(stem.lower())
    if not m:
        return None
    letter, digits, lum = m.group(1).upper(), m.group(2), m.group(3).upper()
    if "." not in digits and len(digits) == 2 and int(digits[1]) > int(digits[0]):
        lo, hi = float(digits[0]), float(digits[1])  # a range such as 4-7
    else:
        lo = hi = float(digits)
    return Template(stem.lower(), letter, lo, hi, lum)


@lru_cache(maxsize=1)
def list_templates() -> tuple[Template, ...]:
    """Every parsable *.dat in the library folder (never assume a particular file exists)."""
    out = [t for p in sorted(LIBRARY_DIR.glob("*.dat")) if (t := parse_template_name(p.stem)) is not None]
    return tuple(sorted(out, key=lambda t: (LETTERS.index(t.letter), t.sub_lo, t.sub_hi,
                                            CLASSES.index(t.lum) if t.lum in CLASSES else 9)))


_SPTYPE = re.compile(r"^\s*([OBAFGKM])\s*(\d+(?:\.\d+)?)?\s*(Iab|Ia|Ib|III|II|IV|I|V)?", re.IGNORECASE)  # longest first


def parse_sptype(sp: str) -> tuple[str, float, str | None] | None:
    """SIMBAD spectral type → (letter, subtype, luminosity class or None): 'A0Va' → A, 0, V;
    'B8.5Ib-II' → B, 8.5, I; 'A0' → A, 0, None. Decimal commas (reference catalog: 'B2,5V') read as points."""
    m = _SPTYPE.match(re.sub(r"(?<=\d),(?=\d)", ".", sp or ""))
    if not m:
        return None
    lum = (m.group(3) or "").upper() or None
    if lum in ("IA", "IAB", "IB"):
        lum = "I"
    return m.group(1).upper(), float(m.group(2) or 0), lum


def pick_template(sptype: str | None) -> tuple[Template, str]:
    """Nearest template, preferring the same luminosity class. Returns (template, note)."""
    temps = list_templates()
    if not temps:
        raise FileNotFoundError(f"no library spectra in {LIBRARY_DIR}")
    parsed = parse_sptype(sptype or "")
    if parsed is None:
        t = next((t for t in temps if t.stem == "a0v"), temps[0])
        return t, f"No spectral type for the reference star (resolve it with SIMBAD in Acquisition); using {t.label}."
    letter, sub, lum = parsed
    want = LETTERS.index(letter) * 10 + sub
    cls = lum or "V"
    same = [t for t in temps if t.lum == cls] or list(temps)
    best = min(same, key=lambda t: (0 if t.sub_lo <= sub <= t.sub_hi and t.letter == letter else 1,
                                    abs(t.index - want)))
    exact = best.letter == letter and best.sub_lo <= sub <= best.sub_hi and best.lum == cls
    note = "" if exact and lum else (
        f"Spectral type {sptype}: " + ("no luminosity class, assumed V; " if not lum else "")
        + ("" if exact else f"nearest template is {best.label}; ") + f"using {best.label}.")
    return best, note.replace("; using", ", using") if note else ""


@lru_cache(maxsize=16)
def load_template(stem: str) -> tuple[np.ndarray, np.ndarray]:
    """(λ Å, flux) with the library's zeroed regions interpolated over (README.HILIB)."""
    data = np.loadtxt(LIBRARY_DIR / f"{stem}.dat")
    lam, flux = data[:, 0].astype(np.float64), data[:, 1].astype(np.float64)
    good = flux > 0
    if not good.all() and good.sum() > 2:
        flux = np.interp(lam, lam[good], flux[good])
    return lam, flux


@lru_cache(maxsize=1)
def extinction() -> tuple[np.ndarray, np.ndarray]:
    """Kitt Peak extinction (IRAF onedstds kpnoextinct.dat): λ Å, k mag/airmass."""
    data = np.loadtxt(DATA_DIR / "kpnoextinct.dat")
    return data[:, 0], data[:, 1]


@lru_cache(maxsize=1)
def telluric_bands() -> tuple[tuple[str, float, float], ...]:
    """Mask ranges from AAVSO's telluric list: line groups span their listed lines; the O₂ A/B bands
    start at their band heads (7594, 6867 Å); single band heads get a short (O₂ a) or the full H₂O
    7160–7340 Å band."""
    groups: dict[str, list[float]] = {}
    with open(DATA_DIR / "aavso_telluric_lines.csv", encoding="utf-8") as fh:
        for row in csv.DictReader(line for line in fh if not line.startswith("#")):
            groups.setdefault(row["group"], []).append(float(row["wavelength_A"]))
    out = []
    for name, lams in groups.items():
        lo, hi = min(lams), max(lams)
        if name.startswith("O2 A"):
            lo = min(lo, 7594.0)
        elif name.startswith("O2 B"):
            lo = min(lo, 6867.0)
        elif name.startswith("Other bandhead"):
            for lam in lams:
                out.append((f"{name} {lam:g}", lam - 8, lam + (172 if "H2O" in name else 30)))
            continue
        out.append((name, lo - 3, hi + 3))
    return tuple(sorted(out, key=lambda b: b[1]))


# ---- building --------------------------------------------------------------------------------------
TELLURIC_PAD_A = 1.5 * LIBRARY_FWHM_A  # smoothing to ~10 Å spreads each band by about this much


def mask_regions(balmer: bool, telluric: bool, lo: float, hi: float) -> list[tuple[str, float, float]]:
    regions = []
    if balmer:
        regions += [(n, c - h, c + h) for n, (c, h) in BALMER_MASK.items()]
    if telluric:
        regions += [(n, a - TELLURIC_PAD_A, b + TELLURIC_PAD_A) for n, a, b in telluric_bands()]
    return sorted([r for r in regions if r[2] > lo and r[1] < hi], key=lambda r: r[1])


def masked(lam: np.ndarray, regions: list[tuple[str, float, float]]) -> np.ndarray:
    m = np.zeros(lam.shape, bool)
    for _, a, b in regions:
        m |= (lam >= a) & (lam <= b)
    return m


def fill_nan(y: np.ndarray) -> np.ndarray:
    good = np.isfinite(y)
    if good.all() or good.sum() < 2:
        return np.nan_to_num(y)
    x = np.arange(len(y))
    return np.interp(x, x[good], y[good])


def smooth_to_library(flux: np.ndarray, cdelt: float, fwhm_obs_a: float | None) -> np.ndarray:
    """Degrade the observed spectrum to the library's ~10 Å FWHM (never sharpen the library)."""
    obs = fwhm_obs_a if fwhm_obs_a and 0 < fwhm_obs_a < LIBRARY_FWHM_A else 6.0
    sigma_a = np.sqrt(LIBRARY_FWHM_A ** 2 - obs ** 2) / 2.3548
    return ndimage.gaussian_filter1d(fill_nan(flux), sigma_a / cdelt, mode="nearest")


def smooth_curve(lam: np.ndarray, y: np.ndarray, keep: np.ndarray, level: str = "medium") -> np.ndarray:
    """Bin medians of the unmasked points, a cubic spline through them, flat beyond the ends."""
    width = SMOOTHING_BIN_A.get(level, 50.0)
    ok = keep & np.isfinite(y) & (y > 0)
    if ok.sum() < 8:
        raise ValueError("too little unmasked spectrum to build a response")
    edges = np.arange(lam[ok].min(), lam[ok].max() + width, width)
    xs, ys = [], []
    for a, b in itertools.pairwise(edges):
        sel = ok & (lam >= a) & (lam < b)
        if sel.sum() >= max(3, int(0.3 * width / max(lam[1] - lam[0], 1e-9))):
            xs.append(float(np.median(lam[sel])))
            ys.append(float(np.median(y[sel])))
    if len(xs) < 4:
        raise ValueError("too few unmasked regions to smooth the response")
    spl = make_interp_spline(np.array(xs), np.array(ys), k=3)
    out = spl(np.clip(lam, xs[0], xs[-1]))
    return np.where(lam < xs[0], ys[0], np.where(lam > xs[-1], ys[-1], out))


# ---- upper envelope ----------------------------------------------------------------------------------
TELLURIC_WIDE_PAD_A = 30.0  # envelope mode: band extents + this margin (the wings absorb too)
TELLURIC_PRONE_A = ((6850.0, 7400.0), (7550.0, 7750.0))  # O2 B + H2O, O2 A: dips ignored here
DIP_FRAC = 0.08  # ignore points this far below the running upper envelope inside the prone ranges
DIP_WINDOW_A = 120.0
# 2nd-difference penalty; stiffness comes mainly from the knot spacing (half a smoothing bin)
ENV_LAMBDA = {"low": 2.0, "medium": 4.0, "high": 4.0, "very high": 0.6}
ENV_K_SIGMA = 2.0
ENV_MIN_DIP = 0.02  # a bin must be at least this far (relative) below the curve to count as a dip
ENV_W_DEEP, ENV_W_NOISE = 0.02, 0.03  # weight of points below the curve: dips / within the noise


def wide_regions(balmer: bool, telluric: bool, lo: float, hi: float) -> list[tuple[str, float, float]]:
    """Like mask_regions but the telluric bands get TELLURIC_WIDE_PAD_A on both sides."""
    regions = []
    if balmer:
        regions += [(n, c - h, c + h) for n, (c, h) in BALMER_MASK.items()]
    if telluric:
        regions += [(n, a - TELLURIC_WIDE_PAD_A, b + TELLURIC_WIDE_PAD_A) for n, a, b in telluric_bands()]
    return sorted([r for r in regions if r[2] > lo and r[1] < hi], key=lambda r: r[1])


def telluric_dips(lam: np.ndarray, y: np.ndarray, usable: np.ndarray) -> np.ndarray:
    """Points inside the telluric-prone ranges more than DIP_FRAC below a running upper envelope: the 90th
    percentile of the usable points within ±DIP_WINDOW_A/2 (masked gaps are left out, never filled, so a
    high value across a gap cannot leak in)."""
    out = np.zeros(lam.shape, bool)
    good = usable & np.isfinite(y)
    half = DIP_WINDOW_A / 2
    for a, b in TELLURIC_PRONE_A:
        for i in np.flatnonzero(good & (lam >= a) & (lam <= b)):
            near = good & (np.abs(lam - lam[i]) <= half)
            if near.sum() >= 5 and y[i] < (1 - DIP_FRAC) * np.percentile(y[near], 90):
                out[i] = True
    return out


def _bspline_basis(x: np.ndarray, knots: np.ndarray) -> np.ndarray:
    """Cubic B-spline design matrix (len(x), len(knots)+2) on equally spaced knots (Eilers & Marx)."""
    from scipy.interpolate import BSpline

    dx = knots[1] - knots[0]
    t = np.r_[knots[0] - 3 * dx, knots[0] - 2 * dx, knots[0] - dx, knots, knots[-1] + dx, knots[-1] + 2 * dx,
              knots[-1] + 3 * dx]
    n = len(t) - 4
    return BSpline.design_matrix(np.clip(x, knots[0], knots[-1]), t, 3).toarray()[:, :n]


def envelope_curve(lam: np.ndarray, y: np.ndarray, keep: np.ndarray, level: str = "medium",
                   ignore: np.ndarray | None = None) -> np.ndarray:
    """Upper envelope: penalised cubic B-spline (knots every SMOOTHING_BIN_A[level] / 2 Å, 2nd-difference
    penalty) fitted to every usable point. Masked/ignored points have weight 0, so the penalty bridges the
    gap smoothly (no free knots to overshoot). Asymmetric iteration from a plain fit: points above keep
    weight 1, points below by more than max(k·σ, ENV_MIN_DIP) are dips (ENV_W_DEEP), points below within
    that get ENV_W_NOISE, so the curve rides on the top of the noise. The relative floor stops a stiff
    curve from ratcheting above data that is low for a long stretch."""
    ok = keep & np.isfinite(y) & (y > 0)
    if ignore is not None:
        ok &= ~ignore
    if ok.sum() < 8:
        raise ValueError("too little unmasked spectrum to build a response")
    x0, x1 = float(lam[ok].min()), float(lam[ok].max())
    spacing = SMOOTHING_BIN_A.get(level, 50.0) / 2
    knots = np.linspace(x0, x1, max(4, int(np.ceil((x1 - x0) / spacing)) + 1))
    inside = (lam >= x0) & (lam <= x1)
    B = _bspline_basis(lam[inside], knots)
    yy = np.where(ok, y, 0.0)[inside]
    base = ok[inside].astype(float)
    m = B.shape[1]
    D = np.diff(np.eye(m), 2, axis=0)
    scale = float(np.median(yy[base > 0]))
    pen = ENV_LAMBDA.get(level, 1.0) * float(base.sum()) / m * (D.T @ D)  # stiffness independent of sampling
    w = base.copy()
    f = np.zeros_like(yy)
    for _ in range(30):
        BW = B * w[:, None]
        c = np.linalg.solve(BW.T @ B + pen, BW.T @ (yy / scale))
        f = (B @ c) * scale
        r = yy - f
        up = (base > 0) & (r >= 0)
        sigma = 1.4826 * float(np.median(r[up])) if up.sum() > 10 else float(np.std(r[base > 0]))
        thr = np.maximum(ENV_K_SIGMA * sigma, ENV_MIN_DIP * np.abs(f))
        neww = np.where(r >= 0, 1.0, np.where(r < -thr, ENV_W_DEEP, ENV_W_NOISE)) * base
        if np.array_equal(neww, w):
            break
        w = neww
    out = np.empty_like(lam, dtype=float)
    out[inside] = f
    out[lam < x0] = f[0]
    out[lam > x1] = f[-1]
    return out


@dataclass
class ResponseResult:
    lam: np.ndarray
    observed: np.ndarray  # reference counts
    observed_smoothed: np.ndarray  # degraded to ~10 Å
    library: np.ndarray  # template on our grid, scaled to the observed level
    raw: np.ndarray  # observed_smoothed / library
    smooth: np.ndarray  # what gets applied, peak 1
    regions: list[tuple[str, float, float]]
    template: Template
    note: str = ""
    ignored: np.ndarray | None = None  # envelope mode: dips left out inside the telluric-prone ranges
    envelope: bool = False


def build_response(lam: np.ndarray, ref_flux: np.ndarray, template: Template, fwhm_obs_a: float | None,
                   balmer: bool = True, telluric: bool = True, smoothing: str = "medium",
                   note: str = "", envelope: bool = False) -> ResponseResult:
    """envelope=True: upper-envelope fit with widened telluric masks and dip rejection (absorption only ever
    lowers the flux); False: bin medians + spline (the original behaviour)."""
    cdelt = float(lam[1] - lam[0])
    lib_lam, lib_flux = load_template(template.stem)
    lib = np.interp(lam, lib_lam, lib_flux, left=np.nan, right=np.nan)
    obs_s = smooth_to_library(ref_flux, cdelt, fwhm_obs_a)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = obs_s / lib
    ignored = None
    if envelope:
        regions = wide_regions(balmer, telluric, float(lam[0]), float(lam[-1]))
        keep = ~masked(lam, regions) & np.isfinite(ref_flux) & np.isfinite(lib)
        ignored = telluric_dips(lam, raw, keep) if telluric else np.zeros(lam.shape, bool)
        sm = envelope_curve(lam, raw, keep, smoothing, ignored)
    else:
        regions = mask_regions(balmer, telluric, float(lam[0]), float(lam[-1]))
        keep = ~masked(lam, regions) & np.isfinite(ref_flux) & np.isfinite(lib)
        sm = smooth_curve(lam, raw, keep, smoothing)
    peak = float(np.nanmax(sm)) or 1.0
    scale = float(np.nanmedian((obs_s / lib)[keep])) or 1.0
    return ResponseResult(lam, ref_flux, obs_s, lib * scale, raw / peak, sm / peak, regions, template, note,
                          ignored, envelope)


def extinction_factor(lam: np.ndarray, x_target: float, x_reference: float) -> np.ndarray:
    """Multiply the target by this to bring it to the reference's airmass."""
    el, ek = extinction()
    k = np.interp(lam, el, ek)
    return 10 ** (0.4 * k * (x_target - x_reference))


def normalise_crop(lam: np.ndarray, flux: np.ndarray, norm: tuple[float, float],
                   crop: tuple[float, float]) -> tuple[np.ndarray, np.ndarray, float]:
    """Divide by the mean in the window, keep [crop]. Returns (λ, flux, divisor)."""
    win = (lam >= norm[0]) & (lam <= norm[1]) & np.isfinite(flux)
    if not win.any():
        raise ValueError(f"the normalisation window {norm[0]:g}–{norm[1]:g} Å is outside the spectrum")
    div = float(np.mean(flux[win]))
    if div == 0:
        raise ValueError("the spectrum is zero in the normalisation window")
    keep = (lam >= crop[0]) & (lam <= crop[1])
    return lam[keep], (flux[keep] / div).astype(np.float32), div


@dataclass
class Airmasses:
    target: float | None = None
    reference: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.target is not None and self.reference is not None
