"""Step 4 orchestration (no Qt): calibrate on the reference profile, carry the solution to the
target, resample both to a linear Å grid → Reference_W.fit, Target_W.fit."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from starextractor import APP_NAME

from . import devflags, targetcal
from .fitsio import STRUCTURAL, read_header, write_float32
from .instrument import InstrumentProfile
from .jobs import Ctx
from .pipeline import IMAGES, StageRun, input_name, output_name
from .session import Session
from .skylines import SkyResult, find_sky_lines
from .target_wave import Adjust, TargetCal, auto_match, target_calibration
from .wavelength import BALMER, Calibration, Solution, calibrate, linear_grid, resample

CENTRE_DEFAULT = "auto"  # the line-centre method (docs/investigations/wavelength_2025-10-02_omiand.md)
CENTRE_LABEL = {"auto": "M0 v0.2 (Gaussian core, re-centred)", "voigt": "pseudo-Voigt fit", "gauss": "Gaussian fit",
                "lorentz": "Lorentzian fit", "minimum": "parabola at the minimum", "v01": "v0.1 Gaussian"}


def set_centre_method(method: str | None) -> None:
    """auto | voigt | gauss | lorentz | minimum | v01 (settings.json "wavelength": {"centre_method": ...}). It is
    part of the step's parameters, so changing it re-runs the step and marks the later steps out of date."""
    devflags.centre_method = method


def centre_method() -> str:
    from .wavelength import CENTRE_METHODS

    m = devflags.centre_method
    return m if m in CENTRE_METHODS else CENTRE_DEFAULT

# Grid start 3900 Å keeps the decided publishing crop (3900–7700 Å); BASS used 4000 Å.
DEFAULTS: dict[str, Any] = {"mode": "refine", "order": None, "lines": list(BALMER), "manual": {},
                            "allow_exact": False, "grid_start": 3900.0, "grid_end": 8000.0, "fixed": None,
                            "centre": CENTRE_DEFAULT, "nofit": [], "mode_user": False}
DROP_KEYS = {"CRVAL1", "CDELT1", "CRPIX1", "CTYPE1", "CUNIT1", "CD1_1"}


def params_of(session: Session, profile: InstrumentProfile) -> dict[str, Any]:
    """Params with defaults. `saved` is a snapshot of the profile's solution taken when a run starts,
    so saving tonight's solution to the profile afterwards does not make this step stale."""
    p = {**DEFAULTS, **session.params["wavelength"]}
    p["centre"] = centre_method()
    disp = profile.dispersion
    if not p.get("saved"):
        p["saved"] = list(disp["coeffs_nm"])
    if not p.get("saved_source"):  # where the prior came from (pair, date), shown with its check
        same = [round(c, 12) for c in p["saved"]] == [round(c, 12) for c in disp.get("coeffs_nm", [])]
        src = str(disp.get("source", "")).split(",")[0].strip()
        p["saved_source"] = (f"{src}, saved {disp.get('date', '?')}" if same and src else
                             "the instrument preset" if same else "an earlier saved solution")
    return p


def sync_params(session: Session, profile: InstrumentProfile) -> None:
    """Write the defaults and the snapshot of the saved solution into the session the first time; an
    existing snapshot is kept, so saving tonight's solution to the profile leaves this step valid."""
    p = params_of(session, profile)
    if p != session.params["wavelength"]:
        session.params["wavelength"] = p
        session.changed()


def target_adjust(p: dict[str, Any]) -> Adjust:
    """The target's own adjustment (default: the reference solution unchanged). Stored per target in the
    session, never in the spectrograph's saved solution."""
    return Adjust.from_params(((p.get("per_image") or {}).get("Target") or {}).get("adjust"))


def target_cal_params(p: dict[str, Any]) -> dict[str, Any]:
    """The target calibration settings (per target): mode auto | shift | shift_scale | manual, unticked anchors."""
    d = ((p.get("per_image") or {}).get("Target") or {}).get("cal") or {}
    mode = d.get("mode", "auto")
    return {"mode": mode if mode in targetcal.MODES else "auto", "disabled": list(d.get("disabled") or [])}


_SKY_CACHE: dict[tuple[str, float, tuple], Any] = {}


def sky_result(frame: Path | None, x_file: Path | None, ref: Solution) -> SkyResult | None:
    """Sky lines of a straightened frame in its Sky A / Sky B rows (from the X file's header). None when the frame
    or the rows are not there. Cached per file, rows and solution (previews run often)."""
    if frame is None or x_file is None or not frame.exists() or not x_file.exists():
        return None
    h = read_header(x_file)
    try:
        rows = [(int(h["EXT_SAT"]), int(h["EXT_SAB"])), (int(h["EXT_SBT"]), int(h["EXT_SBB"]))]
    except (KeyError, TypeError, ValueError):
        return None
    key = (str(frame), frame.stat().st_mtime, (tuple(rows), tuple(round(c, 9) for c in ref.coeffs_nm)))
    if key not in _SKY_CACHE:
        from astropy.io import fits

        img = np.asarray(fits.getdata(frame), dtype=np.float32)
        if len(_SKY_CACHE) > 8:
            _SKY_CACHE.clear()
        _SKY_CACHE[key] = find_sky_lines(img, rows, ref)
    return _SKY_CACHE[key]


@dataclass
class TargetInputs:
    """What the target calibration reads besides the two profiles."""

    frames: dict[str, Path]  # image → straightened 2D frame (work/*_G.fit)
    x_files: dict[str, Path]  # image → X file (its header has the sky rows)
    sptype: str = ""


def target_solution(ref: Solution, ref_prof: np.ndarray, tgt_prof: np.ndarray, p: dict[str, Any],
                    ti: TargetInputs | None) -> tuple[Solution, dict[str, Any], list[str]]:
    """The target's solution and its screen data. Modes other than Manual: the reference solution plus the fitted
    correction (core/targetcal.py), then the manual shift on top; Manual, a failed fit or missing inputs: exactly the
    old behaviour (the stored adjustment)."""
    cp = target_cal_params(p)
    adj = target_adjust(p)
    reset = None
    if adj.stale_for(ref):  # never carry a manual shift over to a new reference solution
        reset = {"shift": adj.shift, "text": f"Manual target shift {adj.shift:+.2f} px reset: it was set for "
                 + ("a reference solution from before this check" if adj.ref_hb is None else
                    "a different reference solution") + "; set it again if the target still needs it"}
        adj.shift, adj.source, adj.ref_hb = 0.0, "", None
    old = target_calibration(ref, tgt_prof, adj)
    data = describe_target(old, ref, len(tgt_prof))
    data["manual_reset"] = reset
    history: list[str] = [f"target wavelength: {reset['text']}"] if reset else []
    if cp["mode"] == "manual" or ti is None:
        data["cal"] = None
        return old.solution, data, history
    tsky = sky_result(ti.frames.get("Target"), ti.x_files.get("Target"), ref)
    rsky = sky_result(ti.frames.get("Reference"), ti.x_files.get("Reference"), ref)
    lo = max(0.0, ref.pixel(float(p["grid_start"])))
    hi = min(float(len(tgt_prof) - 1), ref.pixel(float(p["grid_end"])))
    fit = targetcal.fit_target(ref, ref_prof, tgt_prof, tgt_sky=tsky, ref_sky=rsky, ref_sptype=ti.sptype or None,
                               mode=cp["mode"], disabled=set(cp["disabled"]), used_range=(lo, hi))
    cal = targetcal.to_data(fit, ref, tsky, rsky)
    if not fit.ok or fit.solution is None:
        cal["fallback"] = "The target fit failed or found no anchors: the target uses the old setting (" + (
            "the reference solution unchanged" if old.mode == "reference" else f"offset {old.shift_px:+.2f} px") + ")"
        data["cal"] = cal
        return old.solution, data, history
    sol = fit.solution
    extra = adj.shift if adj.mode == "shift" and adj.shift else 0.0
    if extra:
        sol = sol.shifted(extra)
        cal["override_px"] = extra
    disp = ref.dispersion_a(fit.x0)
    data.update({"coeffs": list(sol.coeffs_nm), "mode": "fit", "shift": fit.s + extra,
                 "shift_a": (fit.s + extra) * disp, "source": "target calibration", "points": [], "rms": fit.rms_a,
                 "total": None})
    data["cal"] = cal
    history = history + targetcal.history(fit, ref)
    if extra:
        history.append(f"target wavelength: manual override {extra:+.2f} px applied after the fit")
    return sol, data, history


def describe_target(tc: TargetCal, ref: Solution, n: int) -> dict[str, Any]:
    errs = tc.fit.errors_a if tc.fit is not None else np.zeros(len(tc.points))
    disp = ref.dispersion_a((n - 1) / 2)
    return {"coeffs": list(tc.solution.coeffs_nm), "mode": tc.mode, "shift": tc.shift_px,
            "shift_a": tc.shift_px * disp, "source": tc.source, "messages": tc.messages,
            "points": [{"line": q.line, "lam": q.lam_a, "pixel": q.pixel, "error": float(e), "manual": q.manual}
                       for q, e in zip(tc.points, errs, strict=True)],
            "total": tc.fit.total_a if tc.fit else None, "rms": tc.fit.rms_a if tc.fit else None}


def target_log(t: dict[str, Any]) -> str:
    cal = t.get("cal")
    if cal and cal.get("ok") and t["mode"] == "fit":
        extra = cal.get("override_px")
        return (f"Target: {cal['summary']}" + (f"; {cal['delta']['text']}" if cal.get("delta") else "")
                + (f"; manual shift {extra:+.2f} px on top (total {t['shift']:+.2f} px)" if extra else ""))
    if t["mode"] == "reference":
        return "Target: the reference solution, unchanged"
    how = {"manual": "set manually", "marker": "set manually (marker dragged)", "auto-match": "set by auto-match",
           "free fit": "by free fit"}.get(t["source"], "set manually")
    extra = (f" from {len(t['points'])} target lines, RMS {t['rms']:.3f} Å" if t["mode"] == "free" and t["rms"]
             is not None else "")
    return f"Target: offset {t['shift']:+.2f} px ({t['shift_a']:+.2f} Å) from the reference solution, {how}{extra}"


def match_job(ctx: Ctx, ref_src: Path, tgt_src: Path, p: dict[str, Any]) -> dict[str, Any]:
    """Auto-match estimate for the target (nothing applied)."""
    ref = _load(ref_src)
    cal = calibrate_profile(ref, p)
    m = auto_match(ref, cal.solution, _load(tgt_src), target_adjust(p))
    ctx.step(1.0)
    return {"shift": m.shift_px, "err": m.err_px, "shift_a": m.shift_a, "err_a": m.err_a, "reliable": m.reliable,
            "reason": m.reason, "text": m.text(), "features": [(f.name, f.shift_px, f.kind) for f in m.features]}


_CAL_CACHE: dict[str, Calibration] = {}


def calibrate_profile(profile_ref: np.ndarray, p: dict[str, Any]) -> Calibration:
    """The reference solution, cached on the profile and the reference's own parameters: edits on the Target tab
    (which change only `per_image`) do not repeat the pattern search and the line fits (about 0.7 s)."""
    import hashlib
    import json

    ref_params = {k: v for k, v in p.items() if k != "per_image"}
    key = hashlib.sha1(np.ascontiguousarray(profile_ref, dtype=np.float64).tobytes()
                       + json.dumps(ref_params, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    cal = _CAL_CACHE.get(key)
    if cal is None:
        cal = _calibrate_profile(profile_ref, p)
        if len(_CAL_CACHE) > 16:
            _CAL_CACHE.clear()
        _CAL_CACHE[key] = cal
    return cal


def _calibrate_profile(profile_ref: np.ndarray, p: dict[str, Any]) -> Calibration:
    fixed = Solution(list(p["fixed"])) if p.get("fixed") else None
    return calibrate(profile_ref, Solution(list(p["saved"])), p["mode"], p["lines"], p["order"],
                     {k: float(v) for k, v in p["manual"].items()}, False, bool(p["allow_exact"]), fixed,
                     centre=str(p.get("centre") or CENTRE_DEFAULT), nofit=list(p.get("nofit") or []),
                     mode_user=bool(p.get("mode_user")), prior_source=str(p.get("saved_source") or ""))


def describe(cal: Calibration) -> dict[str, Any]:
    """Everything the screen shows, as plain data."""
    sol = cal.solution
    used = [q for q in cal.points if q.used]

    def err(q) -> float:
        return float(sol.angstrom(q.pixel)) - q.lam_a

    active = "shift" if cal.mode == "refine" else "free" if cal.mode == "free" else "fixed"
    label = cal.fits.get(active, {}).get("label", "Pasted coefficients")
    return {
        "mode": cal.mode, "coeffs": list(sol.coeffs_nm), "shift": cal.shift_px,
        "points": [{"line": q.line, "lam": q.lam_a, "pixel": q.pixel, "error": err(q), "manual": q.manual,
                    "err_px": q.err_px, "err_a": q.err_px * abs(sol.dispersion_a(q.pixel)) if q.err_px == q.err_px
                    else float("nan"), "fwhm_px": q.fwhm_px, "flags": list(q.flags), "rejected": q.rejected,
                    "weight": q.weight, "fit_x": q.fit_x, "fit_y": q.fit_y}
                   for q in sorted(cal.points, key=lambda r: r.lam_a)],
        "total": float(np.sqrt(np.sum([err(q) ** 2 for q in used]))) if used else None,
        "rms": cal.fit.rms_a if cal.fit else None, "n_used": len(used), "label": label,
        "fwhm": cal.fwhm_a, "R": cal.resolving_power, "fwhm_line": cal.fwhm_line, "messages": cal.messages,
        "status": cal.status, "reason": cal.reason, "mode_reason": cal.mode_reason, "prior": cal.prior,
        "search": cal.search, "unmatched": cal.unmatched, "fits": cal.fits, "centre": cal.centre_method,
        "repaired": list(cal.repaired),
    }


def calibration_log(cal: Calibration, data: dict[str, Any]) -> list[str]:
    """Log lines whose numbers say which fit they belong to (the Solution card and the table use the same labels)."""
    out = [f"Calibration: {cal.prior.get('message', '')}"] if cal.prior.get("message") else []
    if cal.mode_reason:
        out.append(f"Calibration: {cal.mode_reason}")
    pts = " ".join(q.line for q in cal.points if q.used)
    active = "shift" if cal.mode == "refine" else "free" if cal.mode == "free" else ""
    for key in ("free", "shift"):
        f = cal.fits.get(key)
        if f is None:
            continue
        extra = f", shift {f['shift']:+.2f} px from the saved solution" if key == "shift" else ""
        out.append(f"{f['label']}{' (used)' if key == active else ''}: order {f['order']}, {f['n']} lines, "
                   f"RMS {f['rms']:.3f} Å{extra}")
    method = CENTRE_LABEL.get(cal.centre_method, cal.centre_method)
    out.append(f"Lines: {pts}, centres by {method}"
               + (f", FWHM {cal.fwhm_a:.2f} Å on {cal.fwhm_line} (R {cal.resolving_power:.0f})" if cal.fwhm_a else ""))
    out += [f"Line {q.line}: {q.rejected}" for q in cal.points if q.rejected]
    return out


@dataclass
class WavelengthSpec:
    ref_src: Path
    sources: dict[str, Path]  # image → X file
    outputs: dict[str, Path]  # image → W file
    params: dict[str, Any]
    target: TargetInputs | None = None


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
                           params_of(session, profile), target_inputs(session))], []


def target_inputs(session: Session) -> TargetInputs | None:
    """The straightened frames, X files and the reference's spectral type for the target calibration."""
    if session.folder is None:
        return None
    from .identity import sp_type_for

    try:
        sp = sp_type_for(session, "reference")[0]
    except (KeyError, TypeError, AttributeError):
        sp = ""
    return TargetInputs({i: session.folder / (input_name("extract", i) or "") for i in IMAGES},
                        {i: session.folder / (input_name("wavelength", i) or "") for i in IMAGES}, sp)


def _load(path: Path) -> np.ndarray:
    from astropy.io import fits

    return np.asarray(fits.getdata(path), dtype=np.float64)


def free_rms(cal: Calibration) -> float | None:
    """When refining: the RMS a free fit reaches on this night (to suggest it if much better)."""
    return cal.fits["free"]["rms"] if cal.mode == "refine" and "free" in cal.fits else None


def preview_job(ctx: Ctx, ref_src: Path, tgt_src: Path | None, p: dict[str, Any],
                ti: TargetInputs | None = None) -> dict[str, Any]:
    """Calibration for the screen without writing files."""
    ref = _load(ref_src)
    cal = calibrate_profile(ref, p)
    out = describe(cal)
    out["free_rms"] = free_rms(cal)
    out["profiles"] = {"Reference": ref.astype(np.float32)}
    if tgt_src is not None and tgt_src.exists():
        tgt = _load(tgt_src)
        out["profiles"]["Target"] = tgt.astype(np.float32)
        if cal.status == "ok":  # a failed reference solution is never passed on to the target
            out["target"] = target_solution(cal.solution, ref, tgt, p, ti)[1]
    crval, cdelt, n = linear_grid(cal.solution, p["grid_start"], p["grid_end"])
    out["grid"] = (crval, cdelt, n)
    ctx.step(1.0)
    return out


def wavelength_job(ctx: Ctx, spec: WavelengthSpec) -> StageRun:
    t0 = time.perf_counter()
    p = spec.params
    ref = _load(spec.ref_src)
    cal = calibrate_profile(ref, p)
    if cal.status != "ok":
        raise ValueError(f"Wavelength failed: {cal.reason}")
    crval, cdelt, n = linear_grid(cal.solution, p["grid_start"], p["grid_end"])
    data = describe(cal)
    data["grid"], data["profiles"], data["free_rms"] = (crval, cdelt, n), {}, free_rms(cal)
    outputs, summaries, log = {}, {}, []
    target_line = ""
    for img, src in spec.sources.items():
        prof = ref if img == "Reference" else _load(src)
        data["profiles"][img] = prof.astype(np.float32)
        sol = cal.solution
        target_history: list[str] = []
        if img == "Target":  # the target's own solution: the reference's plus its measured correction
            sol, data["target"], target_history = target_solution(cal.solution, ref, prof, p, spec.target)
            target_line = target_log(data["target"])
        c = sol.coeffs_nm
        flux = resample(prof, sol, crval, cdelt, n)
        header = {k: v for k, v in read_header(src).items() if k not in STRUCTURAL and k not in DROP_KEYS}
        header.update({"CRVAL1": crval, "CDELT1": cdelt, "CRPIX1": 1, "CTYPE1": "Wavelength", "CUNIT1": "Angstrom",
                       "WAV_MODE": cal.mode, "WAV_ORD": cal.solution.order, "WAV_NLIN": data["n_used"],
                       "WAV_RMS": round(cal.fit.rms_a, 4), "WAV_SHFT": round(cal.shift_px, 3),
                       "WAV_C0": c[0], "WAV_C1": c[1]})
        for k in range(2, len(c)):
            header[f"WAV_C{k}"] = c[k]
        if cal.resolving_power:
            header.update({"WAV_FWHM": round(cal.fwhm_a or 0, 3), "BSS_ESRP": round(cal.resolving_power),
                           "BSS_SRPW": round(BALMER[cal.fwhm_line], 2)})
        lines = " ".join(q.line.replace("α", "a").replace("β", "b").replace("γ", "g").replace("δ", "d")
                         .replace("ε", "e").replace("ζ", "z").replace("η", "eta") for q in cal.points if q.used)
        history = [f"{APP_NAME} wavelength: {cal.mode} from the reference star, lines {lines}, order "
                   f"{cal.solution.order}, RMS {cal.fit.rms_a:.3f} A, f(x)=a+bx+cx^2 nm (x = 0-based column)",
                   f"coefficients (nm): {', '.join(repr(v) for v in c)}",
                   f"flux-conserving resample to CRVAL1={crval:.3f} A, CDELT1={cdelt:.6f} A/px, {n} samples",
                   *target_history]
        write_float32(spec.outputs[img], flux, header, history)
        outputs[img] = spec.outputs[img].name
        summaries[img] = f"{img}: {n} samples"
    log += calibration_log(cal, data)
    log.append(f"Resampled to linear dispersion: {crval:.1f} Å + {cdelt:.5f} Å/px × {n} → "
               f"{', '.join(outputs.values())} in {time.perf_counter() - t0:.2f} s")
    if target_line:
        log.append(target_line)
    log += [f"Calibration: {m}" for m in cal.messages]
    ribbon = f"{data['n_used']} lines · RMS {cal.fit.rms_a:.2f} Å"
    return StageRun("wavelength", outputs, summaries, log, data, ribbon)
