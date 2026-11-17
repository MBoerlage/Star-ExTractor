"""Step 3 orchestration (no Qt): bands per image → extraction → Target_X.fit (pixel axis)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from starex_reduce import APP_NAME

from .display import prepare
from .extract import Band, Bands, auto_place, centring, extract, sky_subtracted
from .fitsio import STRUCTURAL, read_header, read_image, write_float32
from .jobs import Ctx
from .pipeline import StageRun, input_name, output_name
from .session import Session

DEFAULTS: dict[str, Any] = {"method": "sum", "subtract_sky": True, "sky_mode": "mean"}


def params_of(session: Session) -> dict[str, Any]:
    return {**DEFAULTS, **session.params["extract"]}


def bands_of(session: Session, image: str) -> Bands | None:
    d = params_of(session).get("per_image", {}).get(image, {}).get("bands")
    return Bands.from_json(d) if d else None


def stored_of(session: Session, image: str) -> dict[str, Any] | None:
    """{bands, anchor}: the stored rows and the spectrum row they were set on (None for older sessions)."""
    per = params_of(session).get("per_image", {}).get(image, {})
    return {"bands": per["bands"], "anchor": per.get("anchor")} if per.get("bands") else None


Offsets = dict[str, list[int]]  # {"spec": [top, bottom], "sky_a": [...], "sky_b": [...]} from the spectrum row


def offsets_of(bands: Bands, row: float) -> Offsets:
    r = round(row)
    return {k: [b.top - r, b.bottom - r] for k, b in (("spec", bands.spectrum), ("sky_a", bands.sky_a),
                                                       ("sky_b", bands.sky_b))}


def bands_at(row: float, off: Offsets) -> Bands:
    r = round(row)
    return Bands(*(Band(r + off[k][0], r + off[k][1]) for k in ("spec", "sky_a", "sky_b")))


def default_offsets(session: Session, image: str, profile_bands: dict[str, Any] | None) -> tuple[Offsets | None, str]:
    """Rows to start from when an image has none stored: this night's Acquisition rows for the same set,
    else the spectrograph's default rows (saved from Extract)."""
    acq = session.params.get("acq")
    rows = ((acq or {}).get("rows") or {}).get("target" if image == "Target" else "reference") \
        if isinstance(acq, dict) else None
    if rows and rows.get("spec"):
        return {k: list(rows[k]) for k in ("spec", "sky_a", "sky_b")}, "Acquisition rows"
    if profile_bands and profile_bands.get("spec"):
        return {k: list(profile_bands[k]) for k in ("spec", "sky_a", "sky_b")}, "default rows"
    return None, ""


def resolve_bands(img: np.ndarray, stored: dict[str, Any] | None, offsets: Offsets | None,
                  offsets_src: str = "") -> tuple[Bands, str, Any]:
    """(bands, how, placement or None). Stored bands follow the spectrum when Geometry moved it (they are
    kept as offsets from the row they were set on); else the night's / default rows at the spectrum row;
    else auto-placement."""
    from .geometry import spectrum_row

    h = img.shape[0]
    if stored is not None:
        bands = Bands.from_json(stored["bands"])
        anchor = stored.get("anchor")
        if anchor is not None:
            shift = round(spectrum_row(img) - float(anchor))
            if shift:
                return bands_at(float(anchor) + shift, offsets_of(bands, float(anchor))).clipped(h), \
                    f"followed the spectrum ({shift:+d} rows)", None
        return bands, "stored", None
    if offsets is not None:
        return bands_at(spectrum_row(img), offsets).clipped(h), offsets_src or "default rows", None
    placed = auto_place(img)
    return placed.bands, "auto-placed", placed


@dataclass
class ExtractSpec:
    image: str
    src: Path
    out: Path
    stored: dict[str, Any] | None  # {bands, anchor}; None = start from the offsets or auto-place
    subtract_sky: bool
    sky_mode: str
    offsets: Offsets | None = None
    offsets_src: str = ""

    @property
    def bands(self) -> Bands | None:
        return Bands.from_json(self.stored["bands"]) if self.stored else None


def specs_for(session: Session, images: list[str],
              profile_bands: dict[str, Any] | None = None) -> tuple[list[ExtractSpec], list[str]]:
    if session.folder is None:
        return [], ["Open or create a session first"]
    p = params_of(session)
    specs, problems = [], []
    for image in images:
        src = session.folder / (input_name("extract", image) or "")
        if not src.exists():
            problems.append(f"{image}: {src.name} is missing; run Geometry first")
            continue
        off, off_src = default_offsets(session, image, profile_bands)
        specs.append(ExtractSpec(image, src, session.folder / output_name("extract", image), stored_of(session, image),
                                 bool(p["subtract_sky"]), str(p["sky_mode"]), off, off_src))
    return specs, problems


def extract_job(ctx: Ctx, specs: list[ExtractSpec]) -> StageRun:
    outputs, summaries, log, data = {}, {}, [], {}
    for k, sp in enumerate(specs):
        ctx.step(k / len(specs))
        t0 = time.perf_counter()
        img = read_image(sp.src)
        bands, how, placed = resolve_bands(img, sp.stored, sp.offsets, sp.offsets_src)
        ex = extract(img, bands, sp.subtract_sky, sp.sky_mode)
        header = {k2: v for k2, v in read_header(sp.src).items() if k2 not in STRUCTURAL}
        b = bands.clipped(img.shape[0])
        header.update({
            "CRPIX1": 1, "CRVAL1": 1.0, "CDELT1": 1.0, "CTYPE1": "PIXEL", "BUNIT": "ADU",
            "EXT_SPT": b.spectrum.top, "EXT_SPB": b.spectrum.bottom, "EXT_SAT": b.sky_a.top,
            "EXT_SAB": b.sky_a.bottom, "EXT_SBT": b.sky_b.top, "EXT_SBB": b.sky_b.bottom,
            "EXT_SKY": sp.sky_mode if sp.subtract_sky else "none", "EXT_PEAK": round(ex.peak_row, 2),
        })
        sky_text = (f", minus sky ({sp.sky_mode} of rows {b.sky_a.top}-{b.sky_a.bottom} and "
                    f"{b.sky_b.top}-{b.sky_b.bottom})") if sp.subtract_sky else ", no sky subtraction"
        history = [f"{APP_NAME} extraction: sum of rows {b.spectrum.top}-{b.spectrum.bottom} ({ex.rows} rows)"
                   + sky_text]
        write_float32(sp.out, ex.profile, header, history)
        state, msg = centring(b, ex.peak_row)
        outputs[sp.image] = sp.out.name
        summaries[sp.image] = f"{sp.image}: rows {b.spectrum.top}–{b.spectrum.bottom}"
        log.append(f"Extraction {sp.image}: {sp.src.name}, rows {b.spectrum.top}–{b.spectrum.bottom}"
                   + (", sky subtracted" if sp.subtract_sky else "") + (f" ({how})" if how != "stored" else "")
                   + f" → {sp.out.name} in {time.perf_counter() - t0:.2f} s")
        if state != "ok":
            log.append(f"Extraction {sp.image}: {msg}")
        data[sp.image] = {"bands": b.to_json(), "profile": ex.profile, "peak_row": ex.peak_row,
                          "centring": (state, msg), "auto": placed is not None,
                          "fwhm": placed.fwhm if placed else None}
    ctx.step(1.0)
    return StageRun("extract", outputs, summaries, log, data, f"{len(outputs)} profile"
                    f"{'s' if len(outputs) != 1 else ''} · sky subtracted" if specs and specs[0].subtract_sky
                    else f"{len(outputs)} profiles")


def auto_place_job(ctx: Ctx, img: np.ndarray) -> dict[str, Any]:
    pl = auto_place(img)
    ctx.step(1.0)
    return {"bands": pl.bands.to_json(), "row": pl.row, "fwhm": pl.fwhm, "slit": pl.slit}


def preview_job(ctx: Ctx, img: np.ndarray, bands: Bands, subtract_sky: bool, sky_mode: str) -> dict[str, Any]:
    """Live profile for the band being dragged (sums ~20 rows: well under a millisecond)."""
    ex = extract(img, bands, subtract_sky, sky_mode)
    return {"profile": ex.profile, "peak_row": ex.peak_row, "centring": centring(bands.clipped(img.shape[0]),
                                                                                 ex.peak_row)}


def load_display_job(ctx: Ctx, image: str, src: Path, stored: dict[str, Any] | None, sky_mode: str,
                     offsets: Offsets | None = None, offsets_src: str = "") -> dict[str, Any]:
    """Geometry-corrected stack for display, its sky-subtracted version and the bands (stored, following the
    spectrum, from the night's/default rows, or auto-placed)."""
    img = read_image(src)
    orig = prepare(img)
    ctx.step(0.4)
    bands, how, placed = resolve_bands(img, stored, offsets, offsets_src)
    corr = prepare(sky_subtracted(img, bands, sky_mode))
    ctx.step(1.0)
    return {"image": image, "orig": orig, "corr": corr, "mtime": src.stat().st_mtime_ns,
            "bands": bands.to_json(), "auto": placed is not None, "how": how,
            "fwhm": placed.fwhm if placed else None}
