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
import shutil
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


# ---- defocused (donut) star: two humps across the slit ------------------------------------------------------------
@dataclass(frozen=True)
class Donut:
    """A defocused Newtonian: the star image is a ring, so the cross-dispersion profile has two humps with a dip
    between them. Rows are at the centre column; `tilt_deg` tilts the whole spectrum."""

    main_row: float = 1378.0  # the stronger lobe
    second_row: float = 1356.0  # the weaker lobe
    ratio: float = 0.35  # weaker / stronger peak height
    dip: float = 0.5  # valley between them / the weaker lobe's height (both above the sky)
    fwhm: float = 7.0  # of one lobe (rows)
    peak: float = 300.0  # ADU above the sky of the stronger lobe (a 15 s sub)
    sky: float = 400.0
    noise: float = 6.0
    tilt_deg: float = 0.0
    width: int = WIDTH
    height: int = HEIGHT


def donut_profile(d: Donut, y: np.ndarray) -> np.ndarray:
    """Cross-dispersion profile (above the sky) of the donut, rows y relative to the centre column."""
    sig = d.fwhm / 2.3548
    g = lambda c: np.exp(-0.5 * ((y - c) / sig) ** 2)  # noqa: E731
    lo, hi = sorted((d.main_row, d.second_row))
    edge = lambda z: 0.5 * (1 + np.tanh(z / sig))  # noqa: E731
    plateau = edge(y - lo) * edge(hi - y)  # ~1 between the lobes, falls off like a lobe flank outside
    base = d.peak * g(d.main_row) + d.ratio * d.peak * g(d.second_row)
    mid = np.array([(lo + hi) / 2])
    v0 = float(d.peak * np.exp(-0.5 * ((mid - d.main_row) / sig) ** 2)[0]
               + d.ratio * d.peak * np.exp(-0.5 * ((mid - d.second_row) / sig) ** 2)[0])
    fill = max(0.0, d.dip * d.ratio * d.peak - v0)  # lift the valley to `dip` of the weaker lobe
    return base + fill * plateau


def make_donut(d: Donut = Donut(), seed: int = 0, hot: int = 0, emission_col: int | None = None) -> np.ndarray:
    """A float32 frame: sky + noise + the donut spectrum (a smooth continuum along X), optionally hot pixels and
    one very bright emission-line column."""
    rng = np.random.default_rng(seed)
    x = np.arange(d.width)
    cont = 0.6 + 0.4 * np.sin(np.pi * (x + 200) / (d.width + 400))  # bright in the middle, fainter at the ends
    shift = np.tan(np.radians(d.tilt_deg)) * (x - d.width / 2)
    yy = np.arange(d.height)[:, None] - shift[None, :]
    img = d.sky + donut_profile(d, yy) * cont[None, :] + rng.normal(0, d.noise, (d.height, d.width))
    if emission_col is not None:
        img[:, emission_col] += 40 * donut_profile(d, yy[:, emission_col])
    if hot:
        img[rng.integers(0, d.height, hot), rng.integers(0, d.width, hot)] = 60000.0
    return img.astype(np.float32)


def donut_total(d: Donut) -> np.ndarray:
    """The injected flux per column, summed over all rows (for checking an extraction)."""
    x = np.arange(d.width)
    cont = 0.6 + 0.4 * np.sin(np.pi * (x + 200) / (d.width + 400))
    y = np.arange(-400, 400, 0.25) + d.main_row
    return float(donut_profile(d, y).sum() * 0.25) * cont


def write_donut(path: Path, d: Donut = Donut(), seed: int = 0, exptime: float = 15.0) -> Path:
    """A uint16 light with a SharpCap-like header (the V799 Sgr night 4 case by default)."""
    p = PRESETS["nova"]
    h = light_header(p, "target", datetime(2025, 10, 3, 2, 0, 0))
    h["OBJECT"], h["EXPTIME"] = "Donut", exptime
    write_frame(path, _to_uint16(make_donut(d, seed)), h)
    return path


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


# ---- a multi-night data root for the Library tab --------------------------------------------------------------------
LIBRARY_NIGHTS = ("2025-09-25", "2025-09-26", "2025-09-27", "2025-09-28", "2025-09-29", "2025-09-30")


def _tiny_stack(path: Path, name: str, night: str, nsub: int, exp: float, hour: int = 22) -> Path:
    """A small float32 'stack' with the SX* block and the facts the Library reads (header only matters)."""
    from .core.fitsio import write_float32
    from .core.stack_keys import COMMENTS, sx_cards

    rng = np.random.default_rng(abs(hash((name, night))) % 2**32)
    img = rng.normal(100, 3, (64, 96)).astype(np.float32)
    img[30:34] += 500
    d = datetime.fromisoformat(night) + timedelta(hours=hour + 5)  # local evening (UTC−5) as UTC
    h = {**sx_cards(name, nsub, exp, {"spec": (30, 34), "sky_a": (10, 20), "sky_b": (44, 54)}),
         "OBJECT": name, "DATE-OBS": d.isoformat(), "EXPTIME": exp, "NCOMBINE": nsub, "GAIN": 250, "OFFSET": 3,
         "CCD-TEMP": -8.0, "XBINNING": 1, "YBINNING": 1}
    return write_float32(path, img, h, comments=COMMENTS, keep_sx=True)


def _spectrum_1d(path: Path, name: str, lam0: float = 3900.0, n: int = 1500) -> Path:
    from .core.fitsio import write_float32

    x = np.arange(n)
    lam = lam0 + 2.6 * x
    flux = 1.0 + 0.3 * np.sin(x / 140.0) - 0.4 * np.exp(-0.5 * ((lam - 6563) / 12) ** 2)
    if "nova" in name.lower() or "dra" in name.lower():
        flux += 2.5 * np.exp(-0.5 * ((lam - 6563) / 10) ** 2)
    return write_float32(path, flux.astype(np.float32), {"CRVAL1": lam0, "CDELT1": 2.6, "CRPIX1": 1,
                                                          "CTYPE1": "Wavelength", "CUNIT1": "Angstrom",
                                                          "OBJECT": name})


def write_library(root: Path) -> dict[str, Path]:
    """A data root with 6 nights and 11 pairs in every state the Library shows: stacked only, reduced, reduced
    with an acknowledged / an unacknowledged warning, a stale step, a failed step, published v1, published v2, a
    published file gone, an old (schema 1) logbook without steps, a target stack with no reference, and a night
    of stacks only. Returns {label: pair folder or stack}."""
    from .core import logbook, naming
    from .core.cache import file_sha256
    from .core.datalocs import data_paths, to_stored
    from .core.settings import write_json_atomic

    paths = data_paths({"data_root": str(root)})
    for p in (paths.stacks, paths.masters, paths.processed, paths.published):
        p.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    done = logbook.DONE

    def step(summary: str, state: str = done, **metrics: object) -> dict:
        return {"state": state, "finished_utc": "2025-10-01T03:10:00Z", "summary": summary, "metrics": metrics}

    reduced_steps = {
        "stacked": step("stacked"), "geometry": step("Tilt 0.82° · hot pixels fixed", tilt_deg=0.818, slant_deg=0.0),
        "extract": step("2 profiles · sky subtracted", rows=[1347, 1387], rows_source="rows from the stack"),
        "wavelength": step("6 lines · RMS 0.12 Å", fit="linear", rms_A=0.12, nlines=6),
        "response": step("Pickles A0V · normalised", library="Pickles A0V")}

    def pair(label: str, night: str, target: str, ref: str, *, steps: dict | None = None, reduced: bool = False,
             qc: list | None = None, published: int = 0, gone: bool = False, notes: str = "",
             schema: int = 2) -> Path:
        nd = paths.stacks / night
        nd.mkdir(parents=True, exist_ok=True)
        t = _tiny_stack(nd / naming.stack_filename(target, "target"), target, night, 9, 240.0)
        r = _tiny_stack(nd / naming.stack_filename(ref, "reference"), ref, night, 6, 60.0, hour=23)
        folder = paths.processed / naming.pair_folder(night, target, ref)
        folder.mkdir(parents=True, exist_ok=True)
        doc = logbook.empty(folder.name, night)
        for role, f, name, n, e in (("target", t, target, 9, 240.0), ("reference", r, ref, 6, 60.0)):
            doc[role] = {"name": name, "stack_file": to_stored(f, root), "sha256": file_sha256(f), "nsub": n,
                         "exp_s": e, "rows": {"spectrum": [31, 35], "sky_a": [11, 21], "sky_b": [45, 55]}}
        doc["camera"] = {"gain": 250, "offset": 3, "temp_c": -8.0, "binning": [1, 1]}
        doc["notes"] = notes
        doc["steps"] = steps if steps is not None else {"stacked": step("stacked")}
        if reduced:
            for name in ("Target_X.fit", "Target_W.fit", "Target_R.fit"):
                _spectrum_1d(folder / name, target)
        if qc is not None:
            doc["qc"] = {"utc": "2025-10-01T03:20:00Z", "warnings": qc}
        for v in range(1, published + 1):
            stem = f"_{naming.sanitize_name(target).lower()}_{naming.compact_date(night)}_110_ABCD"
            avs = folder / "export" / f"{stem}.fit"
            if not avs.exists():
                _spectrum_1d(avs, target)
            dest = paths.published / naming.published_folder(night[:4], night, target, v)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(avs, dest / avs.name)
            write_json_atomic(dest / "submission.json", {"version": v, "file": avs.name})
            doc["published"].append({"version": v, "date_utc": f"2025-10-0{v}T12:00:00Z",
                                     "file": to_stored(dest / avs.name, root), "sha256": file_sha256(avs),
                                     "avspec_reference": ""})
            doc["processing"]["export"] = {"file": to_stored(avs, root)}
            if gone and v == published:
                (dest / avs.name).unlink()
        if published:
            doc["steps"]["publish"] = step(f"v{published}", version=published)
            doc["status"]["published"] = doc["published"][-1]["date_utc"]
        if schema == 1:  # an old logbook: no steps, no qc
            doc.pop("steps", None)
            doc.pop("qc", None)
            doc["schema_version"] = 1
        write_json_atomic(folder / logbook.FILE_NAME, doc)
        out[label] = folder
        return folder

    n1, n2, n3, n4, n5, n6 = LIBRARY_NIGHTS
    pair("stacked", n1, "P Cyg", "HD 193369", notes="Clouds after 23:00.\nOnly the stacks so far.")
    pair("old", n1, "gam Cas", "HD 4222", reduced=True, schema=1, notes="Reduced with an older version.")
    pair("reduced", n2, "AG Dra", "HD 145454", steps=dict(reduced_steps), reduced=True, qc=[],
         notes="Good seeing.\nHa strong.")
    pair("reduced_ack", n2, "CH Cyg", "HD 184006", steps=dict(reduced_steps), reduced=True,
         qc=[{"text": "First submission? AAVSO first wants one spectrum of a standard star", "acknowledged": True}])
    pair("reduced_warn", n3, "T CrB", "HD 143894", steps=dict(reduced_steps), reduced=True,
         qc=[{"text": "Airmass not computed: needs the site latitude/longitude", "acknowledged": False}],
         notes="Check the airmass before publishing.")
    pair("stale", n3, "RS Oph", "HD 161868", reduced=True,
         steps={**reduced_steps, "extract": step("2 profiles", state=logbook.STALE, rows=[1347, 1387]),
                "wavelength": step("6 lines · RMS 0.12 Å", state=logbook.STALE, fit="linear", rms_A=0.12, nlines=6),
                "response": step("Pickles A0V", state=logbook.STALE, library="Pickles A0V")},
         qc=[{"text": "Airmass not computed", "acknowledged": False}], notes="Moved the sky bands; re-run.")
    pair("failed", n4, "V Sge", "HD 192640", reduced=True,
         steps={**reduced_steps, "wavelength": step("too few lines identified", state=logbook.FAILED),
                "response": {"state": logbook.SKIPPED, "finished_utc": None, "summary": "", "metrics": {}}})
    pair("published_v1", n4, "Sheliak", "HD 174602", steps=dict(reduced_steps), reduced=True, qc=[], published=1,
         notes="Submitted to AVSpec.")
    pair("published_v2", n5, "V8055 Sgr", "HD 163955", steps=dict(reduced_steps), reduced=True, qc=[],
         published=2, notes="v2: better response.")
    pair("published_gone", n5, "V1405 Cas", "HD 2905", steps=dict(reduced_steps), reduced=True, qc=[], published=1,
         gone=True)
    pair("reduced_2", n5, "BF Cyg", "HD 188665", steps=dict(reduced_steps), reduced=True, qc=[])
    # a target stack with no reference yet (night 5 has another reference stack it could use)
    nd5 = paths.stacks / n5
    out["no_reference"] = _tiny_stack(nd5 / naming.stack_filename("Z And", "target"), "Z And", n5, 12, 180.0)
    out["spare_reference"] = _tiny_stack(nd5 / naming.stack_filename("HD 222603", "reference"), "HD 222603", n5, 6,
                                         60.0, hour=23)
    # a night of stacks only (no pair yet), with the sets file naming the reference
    nd6 = paths.stacks / n6
    nd6.mkdir(parents=True, exist_ok=True)
    t6 = _tiny_stack(nd6 / naming.stack_filename("R Aqr", "target"), "R Aqr", n6, 10, 120.0)
    r6 = _tiny_stack(nd6 / naming.stack_filename("HD 222661", "reference"), "HD 222661", n6, 6, 30.0, hour=23)
    write_json_atomic(nd6 / "acquisition_sets.json", {
        "version": 1, "night": n6, "folder": str(nd6),
        "target": {"name": "R Aqr", "stack_file": to_stored(t6, root), "good": [], "excluded": []},
        "reference": {"name": "HD 222661", "stack_file": to_stored(r6, root), "good": [], "excluded": []}})
    out["stack_only_target"], out["stack_only_reference"] = t6, r6
    return out


# ---- target calibration cases (core/targetcal.py, tests/test_targetcal.py) --------------------------------------
WAVE_REF = (346.25, 0.1203, 6.0e-8)  # the reference solution (nm, 0-based column)
WAVE_N = 3840
SKY_CASE_LINES = ((5460.735, 1.0, 7.0), (5577.339, 1.3, 7.0), (5685.4, 0.8, 13.0), (6300.304, 0.7, 8.0))
FALSE_PEAKS_A = (4620.0, 5105.0, 7020.0)  # sky peaks that are not in the catalogue


@dataclass
class WaveCase:
    ref: object  # Solution
    ref_prof: np.ndarray
    tgt_prof: np.ndarray
    tgt_sky: np.ndarray  # 2D: sky rows only
    ref_sky: np.ndarray
    sky_rows: list
    truth: dict


def _tellurics(lam: np.ndarray) -> np.ndarray:
    t = np.ones_like(lam)
    for lo, hi, step, depth, w in ((6867.0, 6905.0, 3.2, 0.45, 1.2), (7594.0, 7690.0, 3.6, 0.6, 1.4),
                                    (7165.0, 7330.0, 4.7, 0.12, 2.0)):
        for c in np.arange(lo, hi, step):
            t *= 1.0 - depth * np.exp(-0.5 * ((lam - c) / w) ** 2)
    return t


def _star(lam: np.ndarray, v_kms: float, balmer_depth: float = 0.45, he: bool = True) -> np.ndarray:
    from .core.linelists import STELLAR_BALMER, STELLAR_HE, of_kind

    f = 1.0 + 0.6 * np.exp(-0.5 * ((lam - 5600.0) / 1500.0) ** 2)
    z = 1.0 + v_kms / 299792.458
    for ln in of_kind(STELLAR_BALMER):
        f *= 1.0 - balmer_depth * np.exp(-0.5 * ((lam - ln.lam_a * z) / 9.0) ** 2)
    if he:
        for ln in of_kind(STELLAR_HE):
            if ln.group == "He I":
                f *= 1.0 - 0.12 * np.exp(-0.5 * ((lam - ln.lam_a * z) / 4.0) ** 2)
    return f


def _sky_rows_img(lam: np.ndarray, rows: int, rng: np.random.Generator, lines: bool, noise: float,
                  width_scale: float = 1.0) -> np.ndarray:
    disp = 1.2
    sky = 40.0 + 15.0 * np.exp(-0.5 * ((lam - 6000.0) / 1800.0) ** 2)
    if lines:
        for lam0, amp, fwhm_px in SKY_CASE_LINES:
            sky = sky + 60.0 * amp * np.exp(-0.5 * ((lam - lam0) / (fwhm_px * width_scale * disp / 2.355)) ** 2)
        for lam0 in FALSE_PEAKS_A:
            sky = sky + 25.0 * np.exp(-0.5 * ((lam - lam0) / (6.0 * disp / 2.355)) ** 2)
    img = np.tile(sky, (rows, 1)) + rng.normal(0.0, noise, (rows, len(lam)))
    img[:, 900] += 400.0  # a hot column
    return img.astype(np.float32)


def wave_case(shift: float = 13.5, scale: float = 0.0, quad: float = 0.0, v_kms: float = 0.0,
              sky_lines: bool = True, noise: float = 0.004, sky_noise: float = 4.0, seed: int = 0,
              stellar: bool = True, tellurics: bool = True, ref_offset: float = 0.0,
              width_scale: float = 1.0) -> WaveCase:
    """A reference and a target profile plus their sky rows. The target's truth: D(x) = shift + scale (x − x0)
    + quad (x − x0)², i.e. λ_target(x) = λ_ref(x − D(x)); the star moves by v_kms. `ref_offset`: the reference's
    own solution is off by that many px (the initial solution for sky identification)."""
    from .core.wavelength import Solution

    rng = np.random.default_rng(seed)
    ref = Solution(list(WAVE_REF))
    x = np.arange(WAVE_N, dtype=np.float64)
    x0 = (WAVE_N - 1) / 2
    lam_r = np.asarray(ref.angstrom(x))
    u = x - x0
    lam_t = np.asarray(ref.angstrom(x - (shift + scale * u + quad * u * u)))
    tell_r = _tellurics(lam_r) if tellurics else 1.0
    tell_t = _tellurics(lam_t) if tellurics else 1.0
    ref_prof = _star(lam_r, 0.0, he=False) * tell_r
    tgt_prof = (_star(lam_t, v_kms) if stellar else 1.0 + 0.6 * np.exp(-0.5 * ((lam_t - 5600) / 1500) ** 2)) * tell_t
    ref_prof = ref_prof * (1 + rng.normal(0, noise, WAVE_N))
    tgt_prof = tgt_prof * (1 + rng.normal(0, noise, WAVE_N))
    tgt_sky = _sky_rows_img(lam_t, 24, rng, sky_lines, sky_noise, width_scale)
    ref_sky = _sky_rows_img(np.asarray(ref.angstrom(x - ref_offset)), 24, rng, sky_lines, sky_noise)
    return WaveCase(ref, ref_prof * 1000.0, tgt_prof * 1000.0, tgt_sky, ref_sky, [(0, 11), (12, 23)],
                    {"shift": shift, "scale": scale, "quad": quad, "v": v_kms, "x0": x0})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--preset", choices=sorted(PRESETS), default="nova")
    ap.add_argument("--n-target", type=int, default=6)
    ap.add_argument("--n-ref", type=int, default=10)
    ap.add_argument("--n-cal", type=int, default=11)
    ap.add_argument("--library", action="store_true", help="a multi-night data root for the Library tab instead")
    a = ap.parse_args()
    if a.library:
        made = write_library(a.out)
        print(f"{len(made)} pairs and stacks ->", a.out)
        return
    files = write_set(a.out, a.preset, a.n_target, a.n_ref, a.n_cal)
    print({k: len(v) for k, v in files.items()}, "->", a.out)


if __name__ == "__main__":
    main()
