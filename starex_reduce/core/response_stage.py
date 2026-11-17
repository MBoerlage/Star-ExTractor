"""Step 5 orchestration (no Qt): reference ÷ library → response → corrected, normalised, cropped
Target_R.fit / Reference_R.fit, plus response.fit (reusable)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from starex_reduce import APP_NAME

from .airmass import mean_airmass
from .coords import parse_dec, parse_ra
from .fitsio import STRUCTURAL, read_header, write_float32
from .frames import parse_time
from .instrument import InstrumentProfile
from .jobs import Ctx
from .pipeline import StageRun, input_name, output_name
from .response import (
    Airmasses,
    ResponseResult,
    Template,
    build_response,
    extinction_factor,
    list_templates,
    normalise_crop,
    pick_template,
)
from .session import Session
from .settings import app_dir

RESPONSE_FILE = "response.fit"
DEFAULTS: dict[str, Any] = {"template": "auto", "mask_balmer": True, "mask_telluric": True, "airmass": True,
                            "smoothing": "medium", "no_response": False, "source": "library", "envelope": True}
DROP = {"BUNIT"}


def responses_dir() -> Path:
    d = app_dir() / "responses"
    d.mkdir(parents=True, exist_ok=True)
    return d


def saved_responses() -> list[Path]:
    return sorted(responses_dir().glob("*.fit"), reverse=True)


def params_of(session: Session, profile: InstrumentProfile, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    p = {**DEFAULTS, "norm": list(profile.norm_a), "crop": list(profile.crop_a), **session.params["response"]}
    from .identity import sp_type_for

    sp, sp_src = sp_type_for(session, "reference")  # Files SIMBAD, else the Plan's entry for this star
    p["_ctx"] = {  # inputs from other screens: part of the key, so a change there re-runs this step
        "sptype": sp, "sptype_source": sp_src,
        "coords": {s: session.inputs[s].get("coords", {}) for s in ("target", "reference")},
        "site": {k: v for k, v in ((settings or {}).get("site") or {}).items() if k in ("lat", "lon", "elev")},
    }
    return p


def sync_params(session: Session, p: dict[str, Any]) -> None:
    if p != session.params["response"]:
        session.params["response"] = p
        session.changed()


def template_for(p: dict[str, Any]) -> tuple[Template, str]:
    if p["template"] != "auto":
        t = next((t for t in list_templates() if t.stem == p["template"]), None)
        if t is not None:
            return t, ""
    t, note = pick_template(p["_ctx"]["sptype"])
    if p["_ctx"].get("sptype_source") == "Plan":
        note = (f"Spectral type {p['_ctx']['sptype']} from the Plan (not verified with SIMBAD in Files). "
                + note).strip()
    return t, note


def _radec(c: dict[str, Any]) -> tuple[float, float] | None:
    try:
        return parse_ra(c.get("ra", "")), parse_dec(c.get("dec", ""))
    except (ValueError, IndexError, TypeError):
        return None


def airmasses(p: dict[str, Any], headers: dict[str, dict[str, Any]]) -> Airmasses:
    """Header AIRMASS if present, else computed from site + coordinates + exposure times."""
    out = Airmasses()
    site = p["_ctx"]["site"]
    wants: list[str] = []
    for img, side in (("Target", "target"), ("Reference", "reference")):
        h = headers[img]
        if isinstance(h.get("AIRMASS"), (int, float)):
            setattr(out, side, float(h["AIRMASS"]))
            continue
        rd = _radec(p["_ctx"]["coords"].get(side, {}))
        t0, t1 = parse_time(h.get("DATE-OBS")), parse_time(h.get("DATE-END"))
        lat, lon = site.get("lat"), site.get("lon")
        missing = [n for n, v in (("the site position (Export → Location)", lat is not None and lon is not None),
                                  (f"{img.lower()} coordinates (Files)", rd is not None),
                                  (f"the {img.lower()} observation time", t0 is not None)) if not v]
        if missing:
            wants += [m for m in missing if m not in wants]
            continue
        x = mean_airmass(rd[0], rd[1], float(lat), float(lon), t0, t1 or t0)  # type: ignore[index,arg-type]
        if not 1.0 <= x < 10:
            out.notes.append(f"{img}: object below or near the horizon at that time (airmass {x:.1f})")
            continue
        setattr(out, side, x)
    if wants:
        out.notes.insert(0, "Airmass needs " + ", ".join(wants) + ".")
    return out


@dataclass
class ResponseSpec:
    sources: dict[str, Path]  # Target/Reference → W file
    outputs: dict[str, Path]
    response_out: Path
    params: dict[str, Any]


def specs_for(session: Session, profile: InstrumentProfile,
              settings: dict[str, Any]) -> tuple[list[ResponseSpec], list[str]]:
    if session.folder is None:
        return [], ["Open or create a session first"]
    p = params_of(session, profile, settings)
    sync_params(session, p)
    srcs = {img: session.folder / (input_name("response", img) or "") for img in ("Target", "Reference")}
    missing = [i for i, s in srcs.items() if not s.exists()]
    if missing:
        return [], [f"{', '.join(missing)}: run Wavelength first"]
    outs = {img: session.folder / output_name("response", img) for img in srcs}
    return [ResponseSpec(srcs, outs, session.folder / RESPONSE_FILE, p)], []


def _load(path: Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    from astropy.io import fits

    flux = np.asarray(fits.getdata(path), dtype=np.float64)
    h = read_header(path)
    lam = float(h["CRVAL1"]) + float(h["CDELT1"]) * (np.arange(len(flux)) + 1 - float(h.get("CRPIX1", 1)))
    return lam, flux, h


def compute(spec: ResponseSpec) -> dict[str, Any]:
    """Everything the screen shows and the job writes."""
    p = spec.params
    lam_t, tgt, ht = _load(spec.sources["Target"])
    lam_r, ref, hr = _load(spec.sources["Reference"])
    if len(lam_r) != len(lam_t) or not np.allclose(lam_r, lam_t):
        ref = np.interp(lam_t, lam_r, ref, left=np.nan, right=np.nan)
    lam = lam_t
    notes: list[str] = []
    res: ResponseResult | None = None
    resp = np.ones_like(lam)
    label = "none"
    if not p["no_response"]:
        if str(p["source"]).startswith("saved:"):
            path = responses_dir() / str(p["source"])[6:]
            rl, rv, rh = _load(path)
            resp = np.interp(lam, rl, rv)
            label = f"saved {path.name}"
            made = str(rh.get("DATE-OBS", "?"))[:10]
            notes.append(f"Using the saved response {path.name} ({rh.get('RSP_LIB', '?')}, {made})")
        else:
            template, note = template_for(p)
            if note:
                notes.append(note)
            res = build_response(lam, ref, template, hr.get("WAV_FWHM"), p["mask_balmer"], p["mask_telluric"],
                                 p["smoothing"], note, bool(p.get("envelope", True)))
            resp = res.smooth
            label = f"Pickles {template.label}"
    am = airmasses(p, {"Target": ht, "Reference": hr})
    with np.errstate(divide="ignore", invalid="ignore"):
        tgt_c = tgt / resp
        ref_c = ref / resp
    ext_used = False
    if p["airmass"] and not p["no_response"]:
        if am.usable:
            tgt_c = tgt_c * extinction_factor(lam, am.target, am.reference)  # type: ignore[arg-type]
            ext_used = True
        else:
            notes += am.notes
    norm, crop = tuple(p["norm"]), tuple(p["crop"])
    lam_out, tgt_n, _ = normalise_crop(lam, tgt_c, norm, crop)
    _, ref_n, _ = normalise_crop(lam, ref_c, norm, crop)
    return {"lam": lam, "response": res, "applied": resp, "label": label, "airmass": am, "ext": ext_used,
            "lam_out": lam_out, "target": tgt_n, "reference": ref_n, "notes": notes, "headers": (ht, hr),
            "raw_target": tgt, "raw_reference": ref}


def screen_data(c: dict[str, Any]) -> dict[str, Any]:
    r: ResponseResult | None = c["response"]
    am: Airmasses = c["airmass"]
    d = {"lam": c["lam"], "lam_out": c["lam_out"], "target": c["target"], "reference": c["reference"],
         "label": c["label"], "notes": c["notes"], "ext": c["ext"], "x_target": am.target, "x_reference": am.reference,
         "airmass_notes": am.notes}
    if r is not None:
        d.update(observed=r.observed_smoothed, library=r.library, raw=r.raw, smooth=r.smooth, regions=r.regions,
                 template=r.template.stem)
    return d


def preview_job(ctx: Ctx, spec: ResponseSpec) -> dict[str, Any]:
    out = screen_data(compute(spec))
    ctx.step(1.0)
    return out


def response_job(ctx: Ctx, spec: ResponseSpec) -> StageRun:
    t0 = time.perf_counter()
    c = compute(spec)
    p = spec.params
    am: Airmasses = c["airmass"]
    lam_out = c["lam_out"]
    crval, cdelt = float(lam_out[0]), float(lam_out[1] - lam_out[0])
    outputs, summaries, log = {}, {}, []
    for img, flux, h in (("Target", c["target"], c["headers"][0]), ("Reference", c["reference"], c["headers"][1])):
        header = {k: v for k, v in h.items() if k not in STRUCTURAL and k not in DROP}
        header.update({"CRVAL1": crval, "CDELT1": cdelt, "CRPIX1": 1, "CTYPE1": "Wavelength", "CUNIT1": "Angstrom",
                       "RSP_MODE": "none" if p["no_response"] else ("saved" if c["response"] is None else "library"),
                       "RSP_LIB": c["label"], "RSP_NRM1": float(p["norm"][0]), "RSP_NRM2": float(p["norm"][1]),
                       "RSP_ENV": bool(p.get("envelope", True))})
        x = am.target if img == "Target" else am.reference
        if x is not None:
            header["AIRMASS"] = round(x, 4)
        history = []
        if p["no_response"]:
            history.append(f"{APP_NAME} response: none applied (counts), normalised to the mean of "
                           f"{p['norm'][0]:g}-{p['norm'][1]:g} A, cropped {p['crop'][0]:g}-{p['crop'][1]:g} A")
        else:
            masks = (("Balmer" if p["mask_balmer"] else "") + (" telluric" if p["mask_telluric"] else "")).strip()
            history.append(f"{APP_NAME} response: divided by reference / {c['label']} (masks: {masks or 'none'}, "
                           f"smoothing {p['smoothing']}, "
                           + ("upper-envelope fit, absorption dips ignored" if p.get("envelope", True)
                              else "median smoothing") + "), reference smoothed to the library's 10 A resolution")
            history.append("airmass correction: Kitt Peak extinction (IRAF kpnoextinct), "
                           f"X target {am.target:.3f}, X reference {am.reference:.3f}" if c["ext"]
                           else "airmass correction: not applied")
            history.append(f"normalised to the mean of {p['norm'][0]:g}-{p['norm'][1]:g} A, "
                           f"cropped {p['crop'][0]:g}-{p['crop'][1]:g} A")
        write_float32(spec.outputs[img], flux, header, history)
        outputs[img] = spec.outputs[img].name
        summaries[img] = f"{img}: {len(flux)} samples"
    if not p["no_response"]:
        lam = c["lam"]
        rh = {"CRVAL1": float(lam[0]), "CDELT1": float(lam[1] - lam[0]), "CRPIX1": 1, "CTYPE1": "Wavelength",
              "CUNIT1": "Angstrom", "RSP_LIB": c["label"], "DATE-OBS": c["headers"][1].get("DATE-OBS", ""),
              "OBJECT": c["headers"][1].get("OBJECT", "")}
        write_float32(spec.response_out, c["applied"], rh, [f"{APP_NAME} instrument response, peak 1"])
    regions = len(c["response"].regions) if c["response"] is not None else 0
    fit = ", upper envelope" if c["response"] is not None and c["response"].envelope else ""
    log.append(f"Response: Reference ÷ {c['label']}, {regions} regions masked{fit}" if not p["no_response"]
               else "Response: none (No response)")
    airmass_text = f" · airmass {am.target:.2f} vs {am.reference:.2f}" if c["ext"] else ""
    log.append(f"Response applied · normalised at {p['norm'][0]:g}–{p['norm'][1]:g} Å · cropped "
               f"{p['crop'][0]:g}–{p['crop'][1]:g} Å{airmass_text}")
    log += [f"Response: {n}" for n in c["notes"]]
    log.append(f"Target_R.fit ready for export ({time.perf_counter() - t0:.2f} s)")
    data = screen_data(c)
    ribbon = "No response · normalised" if p["no_response"] else f"{c['label']} · normalised"
    return StageRun("response", outputs, summaries, log, data, ribbon)


def save_response(src: Path, name_hint: str) -> Path:
    """Copy the session's response.fit into the user library for later nights."""
    import shutil

    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name_hint)[:40]
    dst = responses_dir() / f"response_{time.strftime('%Y-%m-%d')}_{safe}.fit"
    shutil.copyfile(src, dst)
    return dst
