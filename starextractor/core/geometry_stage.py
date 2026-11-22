"""Step 2 orchestration (no Qt): session params + saved geometry → specs → job → Target_G.fit."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starextractor import APP_NAME

from .display import DisplayImage, prepare
from .fitsio import STRUCTURAL, read_header, read_image, write_float32
from .frames import DARK
from .geometry import (
    GeometryParams,
    correct,
    detect_slant,
    detect_tilt,
    flip,
    outlier_mask,
    replace_masked,
    straighten,
)
from .instrument import InstrumentProfile
from .jobs import Ctx
from .masters import MasterLibrary
from .pipeline import StageRun, input_name, output_name
from .session import SIDES, Session

RESIDUAL_WARN_DEG = 0.05


def defaults(profile: InstrumentProfile) -> dict[str, Any]:
    return {"use_saved": True, "flip_x": profile.flip_x, "flip_y": profile.flip_y, "hot": True,
            "saved_tilt": profile.tilt_deg, "slant_on": False, "saved_slant": profile.slant_deg}


def params_of(session: Session, profile: InstrumentProfile) -> dict[str, Any]:
    """Geometry params with defaults filled in (written back so they are part of the stage key)."""
    p = {**defaults(profile), **session.params["geometry"]}
    if p.get("use_saved"):
        p["saved_tilt"] = profile.tilt_deg
        p["saved_slant"] = profile.slant_deg
    return p


@dataclass
class GeometrySpec:
    image: str
    src: Path
    out: Path
    params: GeometryParams
    tilt_source: str  # saved | detect | drawn | manual | auto
    saved_tilt: float | None
    dark_path: Path | None
    slant_source: str = "off"  # off | saved | detect | drawn | manual


def tilt_for(p: dict[str, Any], image: str) -> tuple[float | None, str]:
    per = p.get("per_image", {}).get(image, {})
    if per.get("tilt") is not None:
        return float(per["tilt"]), per.get("source", "manual")
    if p.get("use_saved") and p.get("saved_tilt") is not None:
        return float(p["saved_tilt"]), "saved"
    return None, "auto"


def slant_for(p: dict[str, Any], image: str = "") -> tuple[float | None, str]:
    """Slant to correct (only with "Correct slant" ticked). One value for the night, used for both images
    (the wavelength solution is carried from the reference to the target): this session's value, else the
    saved one. Sessions from before keep their per-image value as the night's value (Target first)."""
    if not p.get("slant_on"):
        return None, "off"
    if p.get("slant") is not None:
        return float(p["slant"]), p.get("slant_source", "manual")
    for img in ("Target", "Reference"):  # legacy: slant stored per image
        per = p.get("per_image", {}).get(img, {})
        if per.get("slant") is not None:
            return float(per["slant"]), per.get("slant_source", "manual")
    if p.get("use_saved") and p.get("saved_slant") is not None:
        return float(p["saved_slant"]), "saved"
    return None, "off"


def check_not_corrected(src: Path) -> None:
    """A Geometry output must never be straightened again (double correction)."""
    if read_header(src).get("GEO_TILT") is not None:
        raise ValueError(f"{src.name} is already geometry-corrected (GEO_TILT in its header)")


def dark_for(session: Session, image: str, lib: MasterLibrary) -> Path | None:
    resolved = {str(Path(r)) for r in session.calibration[SIDES[image]].get("resolved", [])}
    for m in lib.by_kind(DARK, enabled_only=False):
        if str(lib.abspath(m)) in resolved and m.bias_subtracted:
            return lib.abspath(m)
    return None


def specs_for(session: Session, images: list[str], profile: InstrumentProfile,
              lib: MasterLibrary) -> tuple[list[GeometrySpec], list[str]]:
    if session.folder is None:
        return [], ["Open or create a session first"]
    p = params_of(session, profile)
    if p != session.params["geometry"]:
        session.params["geometry"] = p
        session.changed()
    specs, problems = [], []
    for image in images:
        src = session.folder / (input_name("geometry", image) or "")
        if not src.exists():
            problems.append(f"{image}: {src.name} is missing; load the pair in Select first")
            continue
        tilt, source = tilt_for(p, image)
        slant, slant_src = slant_for(p, image)
        gp = GeometryParams(bool(p.get("flip_x")), bool(p.get("flip_y")), tilt, bool(p.get("hot", True)),
                            slant_deg=slant)
        specs.append(GeometrySpec(image, src, session.folder / output_name("geometry", image), gp, source,
                                  p.get("saved_tilt") if p.get("use_saved") else None,
                                  dark_for(session, image, lib) if gp.hot_pixels else None, slant_src))
    return specs, problems


def geometry_job(ctx: Ctx, specs: list[GeometrySpec]) -> StageRun:
    outputs, summaries, log, data = {}, {}, [], {}
    n = len(specs)
    for k, sp in enumerate(specs):
        sub = Ctx(cancel=ctx.cancel, progress=lambda f, k=k: ctx.progress((k + f) / n), log=ctx.log)
        t0 = time.perf_counter()
        check_not_corrected(sp.src)
        img = read_image(sp.src)
        dark = read_image(sp.dark_path) if sp.dark_path is not None else None
        res = correct(img, sp.params, sub, dark, sp.tilt_source)
        header = {k2: v for k2, v in read_header(sp.src).items() if k2 not in STRUCTURAL}
        header.update({"GEO_TILT": round(res.tilt_deg, 5), "GEO_TSRC": res.tilt_source,
                       "GEO_FLPX": sp.params.flip_x, "GEO_FLPY": sp.params.flip_y,
                       "GEO_HOTN": res.hot_replaced, "GEO_ROW": round(res.centre[0], 2),
                       "GEO_SLNT": round(res.slant_deg, 5), "GEO_SSRC": sp.slant_source})
        slant_txt = f"slant {res.slant_deg:.4f} deg ({sp.slant_source})" if res.slant_deg else "slant not corrected"
        history = [f"{APP_NAME} geometry: tilt {res.tilt_deg:.4f} deg ({res.tilt_source}), {slant_txt}, about "
                   f"row {res.centre[0]:.1f}, col {res.centre[1]:.0f}; one bilinear affine resample"]
        if sp.params.flip_x or sp.params.flip_y:
            history.append(f"flipped: x={sp.params.flip_x} y={sp.params.flip_y}")
        if sp.params.hot_pixels:
            history.append(f"hot pixels/cosmics: {res.hot_replaced} replaced by neighbour median"
                           + (" (dark-master map + outliers)" if sp.dark_path else " (outliers)"))
        write_float32(sp.out, res.image, header, history)
        dt = time.perf_counter() - t0
        det = res.detected
        residual = None
        if det is not None and res.tilt_source == "saved" and sp.saved_tilt is not None:
            residual = det.angle_deg - sp.saved_tilt
        outputs[sp.image] = output_name("geometry", sp.image)  # relative to the pair folder (work/...)
        slant_log = f"slant {res.slant_deg:+.3f}° ({sp.slant_source})" if res.slant_deg else "slant off"
        summaries[sp.image] = (f"{sp.image}: tilt {res.tilt_deg:.3f}° ({res.tilt_source})"
                               + (f" · slant {res.slant_deg:+.2f}°" if res.slant_deg else "")
                               + f" · {res.hot_replaced} px fixed")
        log.append(f"Geometry {sp.image}: tilt {res.tilt_deg:.3f}° ({res.tilt_source}"
                   + (f", measured {det.angle_deg:.3f}° ±{det.residual_px:.2f} px" if det else "")
                   + f"), {slant_log}, hot pixels {res.hot_replaced} replaced → {sp.out.name} in {dt:.1f} s")
        if residual is not None and abs(residual) > RESIDUAL_WARN_DEG:
            log.append(f"Geometry {sp.image}: saved tilt is off by {residual:+.3f}°; consider Detect")
        data[sp.image] = {
            "tilt": res.tilt_deg, "source": res.tilt_source, "hot": res.hot_replaced,
            "detected": det.angle_deg if det else None, "residual_deg": residual,
            "points": det.points if det else [], "centre": res.centre, "slant": res.slant_deg,
        }
    first = next(iter(data.values()), {})
    slant_rb = f" · slant {first['slant']:.2f}°" if first.get("slant") else ""
    ribbon = (f"Tilt {first.get('tilt', 0):.2f}°{slant_rb} · hot pixels fixed".replace("-", "−")) if first else "Done"
    return StageRun("geometry", outputs, summaries, log, data, ribbon)


def detect_job(ctx: Ctx, src: Path, flip_x: bool, flip_y: bool) -> dict[str, Any]:
    """Tilt of a stack as it would be straightened (after the flip settings)."""
    img = flip(read_image(src), flip_x, flip_y)
    ctx.step(0.3)
    res = detect_tilt(img)
    ctx.step(1.0)
    return {"angle": res.angle_deg, "residual_px": res.residual_px, "points": res.points,
            "row": res.row, "lit": res.lit, "span": res.span_px}


def slant_detect_job(ctx: Ctx, src: Path, flip_x: bool, flip_y: bool, tilt: float | None) -> dict[str, Any]:
    """Slant of a stack as Geometry would see it: flipped, hot pixels fixed, tilt removed (the tilt in use,
    else detected), then the sky-row block correlation."""
    t0 = time.perf_counter()
    check_not_corrected(src)
    img = flip(read_image(src), flip_x, flip_y)
    replace_masked(img, outlier_mask(img))
    ctx.step(0.3)
    det = detect_tilt(img)
    angle = det.angle_deg if tilt is None else tilt
    st = straighten(img, angle, 0.0, (det.row, det.x_centre))
    ctx.step(0.7)
    r = detect_slant(st, det.row, det.lit)
    ctx.step(1.0)
    return {"ok": r.ok, "angle": r.angle_deg, "sigma": r.sigma_deg, "n": r.n_blocks, "quality": r.quality,
            "reason": r.reason, "text": r.text(), "tilt": angle, "shifts": r.shifts,
            "seconds": time.perf_counter() - t0}


def load_display_job(ctx: Ctx, image: str, original: Path | None, corrected: Path | None,
                     flip_x: bool, flip_y: bool) -> dict[str, Any]:
    """Display arrays for one image: the stack (as flipped) and its corrected version, same levels."""
    out: dict[str, Any] = {"image": image, "orig": None, "corr": None}
    lv = None
    if original is not None and original.exists():
        d: DisplayImage = prepare(flip(read_image(original), flip_x, flip_y))
        out["orig"], lv = d, d.levels
        out["orig_mtime"] = original.stat().st_mtime_ns
    ctx.step(0.5)
    if corrected is not None and corrected.exists():
        out["corr"] = prepare(read_image(corrected), lv)
        out["corr_mtime"] = corrected.stat().st_mtime_ns
        hdr = read_header(corrected)
        out["corr_tilt"], out["corr_slant"] = hdr.get("GEO_TILT"), hdr.get("GEO_SLNT")
    ctx.step(1.0)
    return out
