"""Step 4 orchestration (no Qt): calibrate on the reference profile, carry the solution to the
target, resample both to a linear Å grid → Reference_W.fit, Target_W.fit."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from starex_reduce import APP_NAME

from .fitsio import STRUCTURAL, read_header, write_float32
from .instrument import InstrumentProfile
from .jobs import Ctx
from .pipeline import IMAGES, StageRun, input_name, output_name
from .session import Session
from .wavelength import BALMER, Calibration, Solution, calibrate, linear_grid, resample

# Grid start 3900 Å keeps the decided publishing crop (3900–7700 Å); BASS used 4000 Å.
DEFAULTS: dict[str, Any] = {"mode": "refine", "order": None, "lines": list(BALMER), "manual": {},
                            "allow_exact": False, "grid_start": 3900.0, "grid_end": 8000.0, "fixed": None}
DROP_KEYS = {"CRVAL1", "CDELT1", "CRPIX1", "CTYPE1", "CUNIT1", "CD1_1"}


def params_of(session: Session, profile: InstrumentProfile) -> dict[str, Any]:
    """Params with defaults. `saved` is a snapshot of the profile's solution taken when a run starts,
    so saving tonight's solution to the profile afterwards does not make this step stale."""
    p = {**DEFAULTS, **session.params["wavelength"]}
    if not p.get("saved"):
        p["saved"] = list(profile.dispersion["coeffs_nm"])
    return p


def sync_params(session: Session, profile: InstrumentProfile) -> None:
    """Write the defaults and the snapshot of the saved solution into the session the first time; an
    existing snapshot is kept, so saving tonight's solution to the profile leaves this step valid."""
    p = params_of(session, profile)
    if p != session.params["wavelength"]:
        session.params["wavelength"] = p
        session.changed()


def calibrate_profile(profile_ref: np.ndarray, p: dict[str, Any]) -> Calibration:
    fixed = Solution(list(p["fixed"])) if p.get("fixed") else None
    return calibrate(profile_ref, Solution(list(p["saved"])), p["mode"], p["lines"], p["order"],
                     {k: float(v) for k, v in p["manual"].items()}, False, bool(p["allow_exact"]), fixed)


def describe(cal: Calibration) -> dict[str, Any]:
    """Everything the screen shows, as plain data."""
    errs = cal.fit.errors_a if cal.fit is not None else np.zeros(len(cal.points))
    return {
        "mode": cal.mode, "coeffs": list(cal.solution.coeffs_nm), "shift": cal.shift_px,
        "points": [{"line": q.line, "lam": q.lam_a, "pixel": q.pixel, "error": float(e), "manual": q.manual}
                   for q, e in zip(cal.points, errs, strict=True)],
        "total": cal.fit.total_a if cal.fit else None, "rms": cal.fit.rms_a if cal.fit else None,
        "fwhm": cal.fwhm_a, "R": cal.resolving_power, "fwhm_line": cal.fwhm_line, "messages": cal.messages,
    }


@dataclass
class WavelengthSpec:
    ref_src: Path
    sources: dict[str, Path]  # image → X file
    outputs: dict[str, Path]  # image → W file
    params: dict[str, Any]


def specs_for(session: Session, images: list[str],
              profile: InstrumentProfile) -> tuple[list[WavelengthSpec], list[str]]:
    if session.folder is None:
        return [], ["Open or create a session first"]
    sync_params(session, profile)
    srcs = {img: session.folder / (input_name("wavelength", img) or "") for img in IMAGES}
    if not srcs["Reference"].exists():
        return [], ["Reference_X.fit is missing; run 1D Extract first (the solution comes from the reference star)"]
    imgs = [i for i in IMAGES if i in images or i == "Reference"]
    missing = [i for i in imgs if not srcs[i].exists()]
    if missing:
        return [], [f"{', '.join(missing)}: run 1D Extract first"]
    return [WavelengthSpec(srcs["Reference"], {i: srcs[i] for i in imgs},
                           {i: session.folder / output_name("wavelength", i) for i in imgs},
                           params_of(session, profile))], []


def _load(path: Path) -> np.ndarray:
    from astropy.io import fits

    return np.asarray(fits.getdata(path), dtype=np.float64)


def free_rms(ref: np.ndarray, p: dict[str, Any]) -> float | None:
    """When refining: the RMS a free fit would reach on this night (to suggest it if much better)."""
    if p["mode"] != "refine":
        return None
    try:
        alt = calibrate_profile(ref, {**p, "mode": "free"})
    except ValueError:
        return None
    return alt.fit.rms_a if alt.fit is not None and alt.mode == "free" else None


def preview_job(ctx: Ctx, ref_src: Path, tgt_src: Path | None, p: dict[str, Any]) -> dict[str, Any]:
    """Calibration for the screen without writing files."""
    ref = _load(ref_src)
    cal = calibrate_profile(ref, p)
    out = describe(cal)
    out["free_rms"] = free_rms(ref, p)
    out["profiles"] = {"Reference": ref.astype(np.float32)}
    if tgt_src is not None and tgt_src.exists():
        out["profiles"]["Target"] = _load(tgt_src).astype(np.float32)
    crval, cdelt, n = linear_grid(cal.solution, p["grid_start"], p["grid_end"])
    out["grid"] = (crval, cdelt, n)
    ctx.step(1.0)
    return out


def wavelength_job(ctx: Ctx, spec: WavelengthSpec) -> StageRun:
    t0 = time.perf_counter()
    p = spec.params
    ref = _load(spec.ref_src)
    cal = calibrate_profile(ref, p)
    if cal.fit is None or len(cal.points) < 2:
        raise ValueError("fewer than two Balmer lines measured in the reference profile")
    crval, cdelt, n = linear_grid(cal.solution, p["grid_start"], p["grid_end"])
    data = describe(cal)
    data["grid"], data["profiles"], data["free_rms"] = (crval, cdelt, n), {}, free_rms(ref, p)
    outputs, summaries, log = {}, {}, []
    c = cal.solution.coeffs_nm
    for img, src in spec.sources.items():
        prof = ref if img == "Reference" else _load(src)
        data["profiles"][img] = prof.astype(np.float32)
        flux = resample(prof, cal.solution, crval, cdelt, n)
        header = {k: v for k, v in read_header(src).items() if k not in STRUCTURAL and k not in DROP_KEYS}
        header.update({"CRVAL1": crval, "CDELT1": cdelt, "CRPIX1": 1, "CTYPE1": "Wavelength", "CUNIT1": "Angstrom",
                       "WAV_MODE": cal.mode, "WAV_ORD": cal.solution.order, "WAV_NLIN": len(cal.points),
                       "WAV_RMS": round(cal.fit.rms_a, 4), "WAV_SHFT": round(cal.shift_px, 3),
                       "WAV_C0": c[0], "WAV_C1": c[1]})
        if len(c) > 2:
            header["WAV_C2"] = c[2]
        if cal.resolving_power:
            header.update({"WAV_FWHM": round(cal.fwhm_a or 0, 3), "BSS_ESRP": round(cal.resolving_power),
                           "BSS_SRPW": round(BALMER[cal.fwhm_line], 2)})
        lines = " ".join(q.line.replace("α", "a").replace("β", "b").replace("γ", "g").replace("δ", "d")
                         .replace("ε", "e") for q in cal.points)
        history = [f"{APP_NAME} wavelength: {cal.mode} from the reference star, lines {lines}, order "
                   f"{cal.solution.order}, RMS {cal.fit.rms_a:.3f} A, f(x)=a+bx+cx^2 nm (x = 0-based column)",
                   f"coefficients (nm): {', '.join(repr(v) for v in c)}",
                   f"flux-conserving resample to CRVAL1={crval:.3f} A, CDELT1={cdelt:.6f} A/px, {n} samples"]
        write_float32(spec.outputs[img], flux, header, history)
        outputs[img] = spec.outputs[img].name
        summaries[img] = f"{img}: {n} samples"
    pts = " ".join(q.line for q in cal.points)
    log.append(f"Calibration: {cal.mode}, shift {cal.shift_px:+.2f} px from the saved solution" if cal.mode == "refine"
               else f"Calibration: {cal.mode}")
    log.append(f"Lines: {pts} matched, order {cal.solution.order}, RMS {cal.fit.rms_a:.3f} Å"
               + (f", FWHM {cal.fwhm_a:.2f} Å on {cal.fwhm_line} (R {cal.resolving_power:.0f})" if cal.fwhm_a else ""))
    log.append(f"Resampled to linear dispersion: {crval:.1f} Å + {cdelt:.5f} Å/px × {n} → "
               f"{', '.join(outputs.values())} in {time.perf_counter() - t0:.2f} s")
    log += [f"Calibration: {m}" for m in cal.messages]
    ribbon = f"{len(cal.points)} lines · RMS {cal.fit.rms_a:.2f} Å"
    return StageRun("wavelength", outputs, summaries, log, data, ribbon)
