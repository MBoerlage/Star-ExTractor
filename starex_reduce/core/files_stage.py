"""Step 1 orchestration (no Qt): session state → frames + calibration plan → stack job."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .fitsio import open_image, peak_adu
from .frames import LIGHT, UNKNOWN, FrameInfo, load_frame, scan
from .jobs import Ctx
from .masters import (
    CalPlan,
    LightSettings,
    MasterLibrary,
    Tolerances,
    calibration_frame,
    pick_calibration,
)
from .pipeline import StageRun, output_name
from .session import SIDES, Session
from .stack import StackParams, run_stack

SIDE_LABEL = {"target": "Target", "reference": "Reference"}


def tolerances(settings_tol: dict[str, Any] | None) -> Tolerances:
    t = Tolerances()
    for k, v in (settings_tol or {}).items():
        if hasattr(t, k):
            setattr(t, k, v)
    return t


def light_frames(session: Session, side: str, used_only: bool = True) -> list[FrameInfo]:
    items = session.inputs[side]["lights"]
    return [load_frame(f["path"], LIGHT) for f in items if f.get("used", True) or not used_only]


def plan_for(session: Session, side: str, lib: MasterLibrary, tol: Tolerances,
             frames: list[FrameInfo] | None = None) -> CalPlan | None:
    frames = frames if frames is not None else light_frames(session, side)
    ls = LightSettings.from_frames(frames)
    if ls is None:
        return None
    cal = session.calibration[side]
    return pick_calibration(
        ls, lib, tol, choice=cal.get("choice", {}), accepted=set(cal.get("accepted", [])),
        match_pedestal=cal.get("match_pedestal"), gain_scale=bool(cal.get("gain_scale", True)),
        use_flat=bool(cal.get("flat", False)), side=SIDE_LABEL[side],
    )


def scan_lights(ctx: Ctx, folder: str) -> dict[str, Any]:
    """Header-only scan of a lights folder. Non-light frames are reported, not loaded."""
    frames = scan(folder, LIGHT)
    lights = [f for f in frames if f.frame_type in (LIGHT, UNKNOWN) or f.error]
    skipped = [f.path.name for f in frames if f not in lights]
    ctx.step(1.0)
    return {"folder": folder, "frames": lights, "skipped": skipped}


def measure_peaks(ctx: Ctx, paths: list[str]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for i, p in enumerate(paths):
        ctx.step(i / max(len(paths), 1))
        try:
            out[p] = peak_adu(open_image(p))
        except (OSError, ValueError):
            out[p] = None
    ctx.step(1.0)
    return out


@dataclass
class StackSpec:
    image: str  # Target | Reference
    frames: list[FrameInfo]
    plan: CalPlan
    params: StackParams
    object_name: str


def stack_job(ctx: Ctx, specs: list[StackSpec], lib: MasterLibrary, folder: Path) -> StageRun:
    outputs, summaries, log = {}, {}, []
    data: dict[str, Any] = {}
    n = len(specs)
    for k, spec in enumerate(specs):
        sub = Ctx(cancel=ctx.cancel, progress=lambda f, k=k: ctx.progress((k + f) / n), log=ctx.log)
        t0 = time.perf_counter()
        cal = calibration_frame(spec.plan, lib)
        flat = lib.data(spec.plan.flat) if spec.plan.flat is not None else None
        out = folder / output_name("files", spec.image)
        res = run_stack(sub, spec.frames, cal, spec.params, out, spec.plan.describe(),
                        spec.object_name, flat)
        dt = time.perf_counter() - t0
        outputs[spec.image] = out.name
        summaries[spec.image] = f"{spec.image}: {res.summary}"
        cal_text = ", ".join(spec.plan.describe()) or "no calibration"
        log.append(f"Stack {spec.image}: {res.n} frames, {spec.params.method}, {cal_text} "
                   f"→ {out.name} in {dt:.1f} s (background {res.background:.0f} ADU, "
                   f"spectrum row {res.peak_row})")
        if res.background < 0:
            log.append(f"Warning: {spec.image} background is negative after calibration")
        data[spec.image] = {"background": res.background, "peak_row": res.peak_row, "n": res.n}
    return StageRun("files", outputs, summaries, log, data)


def specs_for(session: Session, images: list[str], lib: MasterLibrary,
              tol: Tolerances) -> tuple[list[StackSpec], list[str]]:
    """Build stack specs, or plain-language problems that block the run."""
    specs, problems = [], []
    for image in images:
        side = SIDES[image]
        frames = light_frames(session, side)
        bad = [f.path.name for f in frames if f.error]
        if not frames:
            problems.append(f"{SIDE_LABEL[side]} lights: no frames ticked")
            continue
        if bad:
            problems.append(f"{SIDE_LABEL[side]} lights: cannot read {', '.join(bad)}")
            continue
        sizes = {f.size for f in frames}
        if len(sizes) > 1:
            problems.append(f"{SIDE_LABEL[side]} lights have mixed image sizes")
            continue
        plan = plan_for(session, side, lib, tol, frames)
        if plan is None or not plan.ok:
            errs = [i.text for i in (plan.issues if plan else []) if i.severity == "error"]
            problems.append(f"{SIDE_LABEL[side]}: " + ("; ".join(errs) or "calibration not ready"))
            continue
        st = session.inputs[side]["stack"]
        params = StackParams(st.get("method", "sigma_clip_mean"), float(st.get("sigma", 3.0)),
                             3, bool(st.get("align", False)))
        specs.append(StackSpec(image, frames, plan, params, session.inputs[side].get("name", "")))
    return specs, problems


def background_of(path: Path) -> float:
    img = open_image(path)
    return float(np.median(img.read()[::8, ::8]))


def build_masters_job(ctx: Ctx, groups: list[tuple[str, list[FrameInfo]]], lib: MasterLibrary,
                      method: str, subtract_bias: bool, tol: Tolerances) -> list[str]:
    """Build one master per group. Bias groups go first so darks can be stored bias-subtracted."""
    from .frames import BIAS
    from .masters import build_master, fmt_exp

    order = sorted(groups, key=lambda g: g[0] != BIAS)
    log = []
    for k, (kind, frames) in enumerate(order):
        sub = Ctx(cancel=ctx.cancel, progress=lambda f, k=k: ctx.progress((k + f) / len(order)),
                  log=ctx.log)
        t0 = time.perf_counter()
        m = build_master(frames, kind, lib, sub, method=method, subtract_bias=subtract_bias, tol=tol)
        log.append(f"Master {kind} {fmt_exp(m.exptime)}: {method} of {m.nframes} frames"
                   + (" (bias subtracted)" if m.bias_subtracted else "")
                   + f" in {time.perf_counter() - t0:.1f} s")
    return log
