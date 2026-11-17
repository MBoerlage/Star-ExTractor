"""Synthetic Star'Ex LR frames that mimic the real data (docs/DATA.md, docs/EXAMPLE_NOVA.md).

Default preset `nova` follows the measured V8055 Sgr set: lights gain 200/offset 3 on a ~380 ADU
pedestal, spectrum near row 1070 falling 0.67° with X (raw tilt; BASS stacks are pre-rotated),
calibration frames at gain 251/offset 20
(bias 2064 ADU, 0.001 s; dark 300 s = bias + 32 ADU + hot pixels). `sheliak` keeps the DATA.md
numbers (row 1330, −0.2°). Frames are uint16 written with BZERO=32768 like SharpCap/NINA.

    python tools/synth.py OUT_DIR [--preset nova] [--n-target 6] [--n-ref 10] [--n-cal 11]
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from astropy.io import fits

WIDTH, HEIGHT = 3840, 2160
SATURATION = 65520
# Gam Cas BASS solution (nm, 0-based pixel), docs/BASS_CALIBRATION.md §5
BASS_COEFFS_NM = (348.381371973896, 0.120309271523351, 6.05776509593276e-08)
BALMER_A = {"Ha": 6562.852, "Hb": 4861.33, "Hg": 4340.47, "Hd": 4101.74, "He": 3970.07}


@dataclass(frozen=True)
class StarCfg:
    name: str
    row0: float  # spectrum centre row at x = WIDTH/2
    tilt_deg: float  # positive: row increases with X
    lit: tuple[int, int]  # x range where the continuum is bright
    peak: float  # peak continuum ADU above pedestal per frame
    exptime: float
    pedestal: float | None = None  # overrides Preset.light_pedestal (measured per set)
    fwhm_y: float = 6.0
    emission: bool = False  # nova: Balmer in emission; A/B star: in absorption


@dataclass(frozen=True)
class Preset:
    target: StarCfg
    reference: StarCfg
    light_pedestal: float
    light_gain: int
    light_offset: int
    cal_gain: int
    cal_offset: int
    bias_level: float
    dark_exptime: float
    dark_current_per_300s: float
    sky_per_s: float = 0.02
    read_noise: float = 3.65
    hot_fraction: float = 0.017  # ~1.2 % of pixels end up > +200 ADU per 300 s
    width: int = WIDTH
    height: int = HEIGHT


PRESETS: dict[str, Preset] = {
    "nova": Preset(
        target=StarCfg("Synth Nova", 1070.0, -0.67, (876, 3145), 9000.0, 300.0, emission=True),
        reference=StarCfg("Synth A0V", 1042.0, -0.73, (749, 2526), 30000.0, 30.0, pedestal=352.0),
        light_pedestal=380.0, light_gain=200, light_offset=3,
        cal_gain=251, cal_offset=20, bias_level=2064.0, dark_exptime=300.0,
        dark_current_per_300s=32.0,
    ),
    "sheliak": Preset(
        target=StarCfg("Synth Sheliak", 1331.5, -0.2, (1100, 2530), 30000.0, 15.0),
        reference=StarCfg("Synth HD", 1345.0, -0.2, (810, 2420), 40000.0, 15.0),
        light_pedestal=1008.0, light_gain=250, light_offset=3,
        cal_gain=251, cal_offset=20, bias_level=2064.0, dark_exptime=10.0,
        dark_current_per_300s=32.0,
    ),
}


def balmer_pixel(lambda_a: float) -> float:
    """Detector column of a wavelength from the BASS quadratic (solve a + b x + c x² = λ)."""
    a, b, c = BASS_COEFFS_NM
    lam = lambda_a / 10.0
    return (-b + math.sqrt(b * b - 4 * c * (a - lam))) / (2 * c)


def spectrum_row(star: StarCfg, x: np.ndarray | float, width: int = WIDTH) -> np.ndarray | float:
    return star.row0 + (np.asarray(x) - width / 2) * math.tan(math.radians(star.tilt_deg))


def continuum(star: StarCfg, width: int = WIDTH) -> np.ndarray:
    """1D spectrum along X in ADU above pedestal: smooth envelope × Balmer features."""
    x = np.arange(width, dtype=np.float64)
    lo, hi = star.lit
    centre, half = (lo + hi) / 2, (hi - lo) / 2
    envelope = np.exp(-0.5 * ((x - centre) / (half * 0.75)) ** 4)  # flat top, soft edges
    envelope = np.maximum(envelope, 0.02 * np.exp(-0.5 * ((x - centre) / (half * 1.6)) ** 2))
    flux = star.peak * envelope
    for name, lam in BALMER_A.items():
        px = balmer_pixel(lam)
        sigma = 5.0 / 2.355 * (3 if star.emission else 1.5)
        g = np.exp(-0.5 * ((x - px) / sigma) ** 2)
        if star.emission:
            strength = {"Ha": 8.0, "Hb": 2.5, "Hg": 1.4, "Hd": 0.8, "He": 0.5}[name]
            flux = flux + strength * star.peak * 0.3 * g
        else:
            flux = flux * (1 - 0.55 * g)
    return flux.astype(np.float32)


def slanted_frame(tilt_deg: float, slant_deg: float, seed: int = 0, lines: int = 60, row0: float = 1070.0,
                  width: int = WIDTH, height: int = HEIGHT) -> np.ndarray:
    """Float32 frame: a spectrum tilted by `tilt_deg` and, in the straightened frame, `lines` sky lines
    leaning by `slant_deg` (x = x_i + tan(slant)·(y − row0)) across a lit slit (rows 150–1950), with
    noise. `lines=0` gives a frame with no features besides the spectrum."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    a = math.radians(tilt_deg)
    cy, cx = row0, width / 2
    uy = cy + math.cos(a) * (yy - cy) - math.sin(a) * (xx - cx)  # straightened row of each pixel
    ux = cx + math.sin(a) * (yy - cy) + math.cos(a) * (xx - cx)
    img = np.full((height, width), 400.0, np.float32)
    lit = ((uy > 150) & (uy < 1950)).astype(np.float32)
    img += lit * 120
    k = math.tan(math.radians(slant_deg))
    for xi in rng.uniform(300, width - 300, lines):
        amp = rng.uniform(60, 200)
        d = ux - (xi + k * (uy - row0))
        img += lit * amp * np.exp(-0.5 * (d / 1.6) ** 2)
    img += 8000 * np.exp(-0.5 * ((uy - row0) / 2.5) ** 2) * ((ux > 400) & (ux < width - 400))
    img += rng.normal(0, 12, img.shape).astype(np.float32)
    return img.astype(np.float32)


def hot_pixel_map(p: Preset, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """Fixed sensor defects: flat indices and their dark-signal amplitude per 300 s."""
    rng = np.random.default_rng(seed)
    n = int(p.width * p.height * p.hot_fraction)
    idx = rng.choice(p.width * p.height, size=n, replace=False)
    amp = rng.lognormal(mean=math.log(330), sigma=1.0, size=n).astype(np.float32)
    amp[: max(n // 2000, 3)] = SATURATION  # a few stuck at the 16-bit max, as in DATA.md
    return idx, amp


def _noise(rng: np.random.Generator, shape: tuple[int, int], sigma: float) -> np.ndarray:
    return rng.standard_normal(shape, dtype=np.float32) * np.float32(sigma)


def _to_uint16(img: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(img), 0, SATURATION).astype(np.uint16)


def _add_hot(img: np.ndarray, p: Preset, exptime: float) -> None:
    idx, amp = hot_pixel_map(p)
    flat = img.reshape(-1)
    scaled = np.where(amp >= SATURATION, SATURATION, amp * (exptime / 300.0))
    flat[idx] += scaled.astype(np.float32)


def make_light(p: Preset, which: str = "target", seed: int = 0) -> np.ndarray:
    star = p.target if which == "target" else p.reference
    rng = np.random.default_rng(seed)
    shape = (p.height, p.width)
    pedestal = star.pedestal if star.pedestal is not None else p.light_pedestal
    img = np.full(shape, pedestal, dtype=np.float32)
    img += _noise(rng, shape, p.read_noise)
    slit = slice(int(p.height * 0.15), int(p.height * 0.85))
    img[slit] += np.float32(p.sky_per_s * star.exptime)

    flux = continuum(star, p.width) * np.float32(1 + 0.01 * rng.standard_normal())
    rows = spectrum_row(star, np.arange(p.width), p.width)
    sig = star.fwhm_y / 2.355
    y0, y1 = int(rows.min() - 8 * sig), int(rows.max() + 8 * sig) + 1
    yy = np.arange(y0, y1, dtype=np.float32)[:, None]
    band = flux[None, :] * np.exp(-0.5 * ((yy - rows[None, :].astype(np.float32)) / sig) ** 2)
    band /= np.float32(sig * math.sqrt(2 * math.pi))
    band += np.sqrt(np.maximum(band, 0)) * rng.standard_normal(band.shape, dtype=np.float32)
    img[y0:y1] += band
    _add_hot(img, p, star.exptime)
    return _to_uint16(img)


def make_bias(p: Preset, seed: int = 100) -> np.ndarray:
    rng = np.random.default_rng(seed)
    shape = (p.height, p.width)
    return _to_uint16(np.full(shape, p.bias_level, np.float32) + _noise(rng, shape, p.read_noise))


def make_dark(p: Preset, seed: int = 200, exptime: float | None = None) -> np.ndarray:
    t = p.dark_exptime if exptime is None else exptime
    rng = np.random.default_rng(seed)
    shape = (p.height, p.width)
    img = np.full(shape, p.bias_level + p.dark_current_per_300s * t / 300.0, np.float32)
    img += _noise(rng, shape, p.read_noise)
    _add_hot(img, p, t)
    return _to_uint16(img)


def light_header(p: Preset, which: str, start: datetime) -> fits.Header:
    star = p.target if which == "target" else p.reference
    h = fits.Header()
    end = start + timedelta(seconds=star.exptime)
    for k, v in (
        ("SWCREATE", "SharpCap synthetic"), ("INSTRUME", "ZWO ASI585MM Pro"),
        ("IMAGETYP", "Light"), ("FRAMETYP", "Light"), ("OBJECT", star.name.replace(" ", "")),
        ("EXPTIME", star.exptime), ("GAIN", p.light_gain), ("OFFSET", p.light_offset),
        ("CCD-TEMP", -5.5), ("SET-TEMP", -10.0), ("XPIXSZ", 2.9), ("YPIXSZ", 2.9),
        ("XBINNING", 1), ("YBINNING", 1), ("ROWORDER", "TOP-DOWN"),
        ("DATE-OBS", start.isoformat(timespec="microseconds")),
        ("DATE-END", end.isoformat(timespec="microseconds")),
    ):
        h[k] = v
    return h


def cal_header(p: Preset, kind: str, start: datetime, exptime: float) -> fits.Header:
    h = fits.Header()
    for k, v in (
        ("SWCREATE", "N.I.N.A. synthetic"), ("INSTRUME", "ZWO ASI585MM Pro"),
        ("IMAGETYP", kind.upper()), ("EXPOSURE", exptime), ("EXPTIME", exptime),
        ("GAIN", p.cal_gain), ("OFFSET", p.cal_offset), ("CCD-TEMP", -7.5), ("SET-TEMP", -10.0),
        ("XPIXSZ", 2.9), ("YPIXSZ", 2.9), ("XBINNING", 1), ("YBINNING", 1),
        ("DATE-OBS", start.isoformat(timespec="microseconds")),
    ):
        h[k] = v
    return h


def write_frame(path: Path, data: np.ndarray, header: fits.Header) -> None:
    """uint16 → int16 with BZERO=32768 on disk, as the cameras write it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)


def write_set(out: Path, preset: str = "nova", n_target: int = 6, n_ref: int = 10,
              n_cal: int = 11, scale: float = 1.0, rows: int | None = None) -> dict[str, list[Path]]:
    """Write a full night: target/ reference/ darks/ bias/. `scale` < 1 shrinks frames for tests; `rows`
    keeps the full width (so the wavelength solution applies) but only that many rows around the spectra."""
    p = PRESETS[preset]
    if scale != 1.0:
        p = scaled(p, scale)
    if rows is not None:
        p = short(p, rows)
    t0 = datetime(2026, 8, 21, 3, 0, 0)
    written: dict[str, list[Path]] = {"target": [], "reference": [], "darks": [], "bias": []}
    for which, n, folder in (("reference", n_ref, "reference"), ("target", n_target, "target")):
        star = p.target if which == "target" else p.reference
        for i in range(n):
            start = t0 + timedelta(seconds=i * (star.exptime + 2))
            path = out / folder / f"{star.name.replace(' ', '')}_{i + 1:05d}.fits"
            write_frame(path, make_light(p, which, seed=1000 * (which == "target") + i),
                        light_header(p, which, start))
            written[which].append(path)
        t0 += timedelta(seconds=n * (star.exptime + 2) + 300)
    for i in range(n_cal):
        start = datetime(2025, 6, 4, 11, 0, 0) + timedelta(seconds=i * 302)
        d = out / "darks" / f"{start:%Y-%m-%d_%H-%M-%S}__-7.50_{p.dark_exptime:.2f}s_{i:04d}.fits"
        write_frame(d, make_dark(p, seed=200 + i), cal_header(p, "dark", start, p.dark_exptime))
        written["darks"].append(d)
        b = out / "bias" / f"{start:%Y-%m-%d_%H-%M-%S}__-10.00_0.00s_{i:04d}.fits"
        write_frame(b, make_bias(p, seed=100 + i), cal_header(p, "bias", start, 0.001))
        written["bias"].append(b)
    return written


def scaled(p: Preset, s: float) -> Preset:
    """Shrink a preset geometrically (rows, columns, lit ranges) for fast tests."""
    def star(c: StarCfg) -> StarCfg:
        return replace(c, row0=c.row0 * s, lit=(int(c.lit[0] * s), int(c.lit[1] * s)))
    return replace(p, target=star(p.target), reference=star(p.reference),
                   width=int(p.width * s), height=int(p.height * s))


def short(p: Preset, rows: int) -> Preset:
    """Full width, `rows` rows: the spectra keep their tilt and sit in the middle of the shorter frame."""
    return replace(p, target=replace(p.target, row0=rows * 0.52), reference=replace(p.reference, row0=rows * 0.48),
                   height=rows)


EXAMPLE_ROWS = 320


def write_example(out: Path) -> dict[str, list[Path]]:
    """The small example night for the README walkthrough and --selftest: a synthetic nova-like target and an
    A0V-like reference star, 3 lights each, 3 darks and 3 bias, full width × 320 rows (about 30 MB)."""
    return write_set(out, "nova", n_target=3, n_ref=3, n_cal=3, rows=EXAMPLE_ROWS)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--preset", choices=sorted(PRESETS), default="nova")
    ap.add_argument("--n-target", type=int, default=6)
    ap.add_argument("--n-ref", type=int, default=10)
    ap.add_argument("--n-cal", type=int, default=11)
    a = ap.parse_args()
    files = write_set(a.out, a.preset, a.n_target, a.n_ref, a.n_cal)
    print({k: len(v) for k, v in files.items()}, "->", a.out)


if __name__ == "__main__":
    main()
