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


Offsets = dict[str, list[Any]]  # {"spec": [top, bottom], "sky_a": [...], "sky_b": [...]} from the spectrum row


def offsets_of(bands: Bands, row: float) -> Offsets:
    r = round(row)
    return {k: [b.top - r, b.bottom - r] for k, b in (("spec", bands.spectrum), ("sky_a", bands.sky_a),
                                                       ("sky_b", bands.sky_b))}


def bands_at(row: float, off: Offsets) -> Bands:
    r = round(row)
    return Bands(*(Band(r + off[k][0], r + off[k][1]) for k in ("spec", "sky_a", "sky_b")))


def default_offsets(session: Session, image: str, profile_bands: dict[str, Any] | None) -> tuple[Offsets | None, str]:
    """Rows to start from when an image has none stored: the rows of the pair loaded in Select (read from the
    stack's header or the sets file), else this night's Acquisition rows for the same set, else the
    spectrograph's default rows (saved from Extract)."""
    role = "target" if image == "Target" else "reference"
    pairs = (session.params.get("files") or {}).get("pairs")
    entry = (pairs[0].get(role) or {}) if isinstance(pairs, list) and pairs and isinstance(pairs[0], dict) else {}
    if entry.get("rows"):
        from .stack_keys import offsets_of_rows1

        rows1 = {k: (int(v[0]), int(v[1])) for k, v in entry["rows"].items()}
        flip_y = bool((session.params.get("geometry") or {}).get("flip_y"))
        off1 = offsets_of_rows1(rows1, flip_y)
        if off1 is not None:
            return off1, f"rows from the stack ({entry.get('rows_src') or 'file header'})"
    acq = session.params.get("acq")
    acq = acq if isinstance(acq, dict) else {}
    stacked = (acq.get("stacked") or {}).get(role) or {}
    rows = stacked.get("rows") or (acq.get("rows") or {}).get(role)
    if rows and rows.get("spec"):
        off: Offsets = {k: list(rows[k]) for k in ("spec", "sky_a", "sky_b")}
        if stacked.get("rows") and rows.get("row") is not None:
            geo = session.params.get("geometry") or {}
            off["near"] = [float(rows["row"]), 1.0 if geo.get("flip_y") else 0.0]
            return off, "rows saved with the Acquisition stack"
        return off, "Acquisition rows"
    if profile_bands and profile_bands.get("spec"):
        return {k: list(profile_bands[k]) for k in ("spec", "sky_a", "sky_b")}, "default rows"
    return None, ""


FOLLOW_SEARCH = 30  # rows beyond the spectrum band searched for the ridge it was set on


def anchor_row(img: np.ndarray, spectrum: Band) -> float:
    """Ridge row of the spectrum the bands sit on: the brightest row within the spectrum band (± a few rows),
    not the brightest row of the frame, which can be another star or a diffuse glow."""
    h, w = img.shape
    lo, hi = max(0, spectrum.top - FOLLOW_SEARCH), min(h, spectrum.bottom + FOLLOW_SEARCH + 1)
    if hi - lo < 3:
        return float(spectrum.centre)
    prof = np.percentile(img[lo:hi, w // 8: w - w // 8: 8], 90, axis=1)
    from scipy import ndimage

    prof = ndimage.gaussian_filter1d(prof - np.median(prof), 2.0)
    return float(lo + int(np.argmax(prof)))


def resolve_bands(img: np.ndarray, stored: dict[str, Any] | None, offsets: Offsets | None,
                  offsets_src: str = "") -> tuple[Bands, str, Any]:
    """(bands, how, placement or None). Stored bands follow their spectrum when Geometry moved it: the ridge
    inside the band is measured again and compared with the row it was on when the bands were set (`anchor`).
    An anchor away from the spectrum band (older sessions stored the frame's brightest row, which can be another
    spectrum) cannot tell, so those bands stay. Else the night's / default rows at the spectrum row; else
    auto-placement."""
    from .geometry import spectrum_row

    h = img.shape[0]
    if stored is not None:
        bands = Bands.from_json(stored["bands"])
        anchor = stored.get("anchor")
        sp = bands.spectrum
        if anchor is not None and sp.top - FOLLOW_SEARCH <= float(anchor) <= sp.bottom + FOLLOW_SEARCH:
            shift = round(anchor_row(img, bands.spectrum) - float(anchor))
            if shift:
                return bands_at(float(anchor) + shift, offsets_of(bands, float(anchor))).ordered(h), \
                    f"followed the spectrum ({shift:+d} rows)", None
        return bands.ordered(h), "stored", None
    if offsets is not None:
        row = spectrum_row(img)
        near = offsets.get("near")
        if near:  # the row the stack's spectrum was on (detector rows; flipped like the image with flip Y)
            hint = (h - 1 - near[0]) if near[1] else near[0]
            if 0 <= hint < h:
                row = anchor_row(img, Band(round(hint) + offsets["spec"][0], round(hint) + offsets["spec"][1]))
        return bands_at(row, offsets).ordered(h), offsets_src or "default rows", None
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


def corrected_path(session: Session, image: str) -> Path | None:
    """The current geometry-corrected stack of one image (Target_G.fit / Reference_G.fit): the only file the
    Extract view and the Extract run read."""
    if session.folder is None:
        return None
    return session.folder / (input_name("extract", image) or "")


def specs_for(session: Session, images: list[str],
              profile_bands: dict[str, Any] | None = None) -> tuple[list[ExtractSpec], list[str]]:
    if session.folder is None:
        return [], ["Open or create a session first"]
    p = params_of(session)
    specs, problems = [], []
    for image in images:
        src = corrected_path(session, image)
        assert src is not None
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
        data[sp.image] = {"bands": b.to_json(), "profile": ex.profile, "peak_row": ex.peak_row, "how": how,
                          "centring": (state, msg), "auto": placed is not None, **placement_facts(placed)}
    ctx.step(1.0)
    return StageRun("extract", outputs, summaries, log, data, f"{len(outputs)} profile"
                    f"{'s' if len(outputs) != 1 else ''} · sky subtracted" if specs and specs[0].subtract_sky
                    else f"{len(outputs)} profiles")


def auto_place_job(ctx: Ctx, img: np.ndarray, window: tuple[float, float] | None = None,
                   mode: str = "auto") -> dict[str, Any]:
    """Bands from the brightest row: of the whole image, or only of the rows in view (`window` = top, bottom)."""
    from .extract import auto_place_in

    pl = auto_place(img, mode=mode) if window is None else auto_place_in(img, *window, mode=mode)
    ctx.step(1.0)
    return {"bands": pl.bands.to_json(), "row": pl.row, **placement_facts(pl), "slit": pl.slit}


def placement_facts(pl: Any) -> dict[str, Any]:
    """What the screen says about an auto-placement: the FWHM only for a single peak (a Gaussian width is no focus
    measure for a defocused, double-lobed star), else the span."""
    double = pl is not None and pl.mode != "single"
    return {"fwhm": pl.fwhm if pl is not None and not double else None, "mode": pl.mode if pl else None,
            "span": pl.span if pl is not None else None, "lobes": list(pl.lobes) if pl else [],
            "warnings": list(pl.warnings) if pl else []}


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
            "bands": bands.to_json(), "auto": placed is not None, "how": how, **placement_facts(placed)}
