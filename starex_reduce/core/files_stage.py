"""Stacking orchestration (no Qt), used by Acquisition > Stack selected: ticked lights + calibration plan →
stack job → Target_S.fit / Reference_S.fit. Reduce › Select only picks the stacks (core/pair.py)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .frames import FrameInfo
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
from .session import SIDES
from .stack import StackParams, run_stack

SIDE_LABEL = {"target": "Target", "reference": "Reference"}


def tolerances(settings_tol: dict[str, Any] | None) -> Tolerances:
    t = Tolerances()
    for k, v in (settings_tol or {}).items():
        if hasattr(t, k):
            setattr(t, k, v)
    return t


def plan_from(frames: list[FrameInfo], lib: MasterLibrary, tol: Tolerances, cal: dict[str, Any],
              side_label: str) -> CalPlan | None:
    """The calibration plan for these lights with the calibration choices `cal` (Files' per-side dict, or
    the Acquisition tab's Stacking settings: same keys)."""
    ls = LightSettings.from_frames(frames)
    if ls is None:
        return None
    return pick_calibration(
        ls, lib, tol, choice=cal.get("choice", {}), accepted=set(cal.get("accepted", [])),
        match_pedestal=cal.get("match_pedestal"), gain_scale=bool(cal.get("gain_scale", True)),
        use_flat=bool(cal.get("flat", False)), side=side_label,
    )


HEADER_RA = ("RA", "OBJCTRA", "CRVAL1")
HEADER_DEC = ("DEC", "OBJCTDEC", "CRVAL2")


def header_coords(frames: list[FrameInfo]) -> tuple[float | None, float | None]:
    """RA/Dec (deg) from the first light whose header has them."""
    from . import coords

    for f in frames:
        for rk, dk in zip(HEADER_RA, HEADER_DEC, strict=True):
            ra, dec = f.h(rk), f.h(dk)
            try:
                if isinstance(ra, (int, float)) and isinstance(dec, (int, float)):
                    return float(ra), float(dec)
                if isinstance(ra, str) and isinstance(dec, str):
                    return coords.parse_ra(ra), coords.parse_dec(dec)
            except ValueError:
                continue
    return None, None


@dataclass
class StackSpec:
    image: str  # Target | Reference
    frames: list[FrameInfo]
    plan: CalPlan
    params: StackParams
    object_name: str
    rows: dict[str, Any] | None = None  # Acquisition's locked rows (Rows.to_json()), written as SX* keywords
    coords: tuple[float | None, float | None] = (None, None)  # RA, Dec (deg) when known
    out_name: str = ""  # file name in the output folder; "" = Target_S.fit / Reference_S.fit


def stack_job(ctx: Ctx, specs: list[StackSpec], lib: MasterLibrary, folder: Path) -> StageRun:
    outputs, summaries, log = {}, {}, []
    data: dict[str, Any] = {}
    n = len(specs)
    for k, spec in enumerate(specs):
        sub = Ctx(cancel=ctx.cancel, progress=lambda f, k=k: ctx.progress((k + f) / n), log=ctx.log)
        t0 = time.perf_counter()
        cal = calibration_frame(spec.plan, lib)
        flat = lib.data(spec.plan.flat) if spec.plan.flat is not None else None
        out = folder / (spec.out_name or output_name("files", spec.image))
        res = run_stack(sub, spec.frames, cal, spec.params, out, spec.plan.describe(),
                        spec.object_name, flat, spec.rows, spec.coords, provenance(spec, lib))
        dt = time.perf_counter() - t0
        outputs[spec.image] = out.relative_to(folder).as_posix()
        summaries[spec.image] = f"{spec.image}: {res.summary}"
        cal_text = ", ".join(spec.plan.describe()) or "no calibration"
        log.append(f"Stack {spec.image}: {res.n} frames, {spec.params.method}, {cal_text} "
                   f"→ {out.name} in {dt:.1f} s (background {res.background:.0f} ADU, "
                   f"spectrum row {res.peak_row})")
        if res.background < 0:
            log.append(f"Warning: {spec.image} background is negative after calibration")
        data[spec.image] = {"background": res.background, "peak_row": res.peak_row, "n": res.n}
    return StageRun("files", outputs, summaries, log, data)


def provenance(spec: StackSpec, lib: MasterLibrary) -> dict[str, str]:
    """SXRAWDIR (the raw subs' folder, absolute) and the masters used with their SHA256 (core/stack_keys.py)."""
    import os

    from .stack_keys import masters_cards

    dirs = sorted({str(f.path.resolve().parent) for f in spec.frames})
    h = {"SXRAWDIR": os.path.commonpath(dirs) if len(dirs) > 1 else dirs[0]} if dirs else {}
    used = {kind: (Path(m.path).as_posix(), lib.sha256(m)) for kind, m in
            (("dark", spec.plan.dark), ("bias", spec.plan.bias), ("flat", spec.plan.flat)) if m is not None}
    h.update(masters_cards(used))
    return h


def spec_from(image: str, frames: list[FrameInfo], cal: dict[str, Any], st: dict[str, Any], name: str,
              lib: MasterLibrary, tol: Tolerances) -> tuple[StackSpec | None, str]:
    """One stack job from ticked lights, calibration choices and stack settings, or the plain-language
    problem that blocks it. Used by the Acquisition tab's Stack selected (and the self-test)."""
    label = SIDE_LABEL[SIDES[image]]
    bad = [f.path.name for f in frames if f.error]
    if not frames:
        return None, f"{label} lights: no frames ticked"
    if bad:
        return None, f"{label} lights: cannot read {', '.join(bad)}"
    if len({f.size for f in frames}) > 1:
        return None, f"{label} lights have mixed image sizes"
    plan = plan_from(frames, lib, tol, cal, label)
    if plan is None or not plan.ok:
        errs = [i.text for i in (plan.issues if plan else []) if i.severity == "error"]
        return None, f"{label}: " + ("; ".join(errs) or "calibration not ready")
    params = StackParams(st.get("method", "sigma_clip_mean"), float(st.get("sigma", 3.0)),
                         3, bool(st.get("align", False)))
    return StackSpec(image, frames, plan, params, name), ""


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
