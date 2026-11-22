"""Step 1 · stack calibrated lights strip by strip (sigma-clip mean or median), optional Y align."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from starex_reduce import APP_NAME

from .combine import combine_strips
from .fitsio import STRUCTURAL, Image, open_image, write_float32
from .frames import FrameInfo
from .jobs import Ctx


@dataclass
class StackParams:
    method: str = "sigma_clip_mean"
    sigma: float = 3.0
    iters: int = 3
    align: bool = False


@dataclass
class StackResult:
    path: Path
    n: int
    background: float
    peak_row: int
    summary: str
    shifts: list[float] = field(default_factory=list)


def row_profile(img: Image, cal: np.ndarray | None) -> np.ndarray:
    """Median over columns per row (robust to hot pixels), for spectrum row and alignment."""
    h, w = img.shape
    cols = slice(w // 8, w - w // 8, 4)
    prof = np.empty(h, np.float32)
    for y0 in range(0, h, 256):
        y1 = min(h, y0 + 256)
        block = img.strip(y0, y1)
        if cal is not None:
            block -= cal[y0:y1]
        prof[y0:y1] = np.percentile(block[:, cols], 90, axis=1)
    return prof


def subpixel_shift(ref: np.ndarray, prof: np.ndarray, max_shift: int = 40) -> float:
    """Y offset of `prof` relative to `ref` from the cross-correlation peak (parabolic refine)."""
    a = ref - np.median(ref)
    b = prof - np.median(prof)
    lags = np.arange(-max_shift, max_shift + 1)
    cc = np.array([np.dot(a[max(0, -k):len(a) - max(0, k)], b[max(0, k):len(b) - max(0, -k)])
                   for k in lags])
    i = int(np.argmax(cc))
    if 0 < i < len(cc) - 1:
        den = cc[i - 1] - 2 * cc[i] + cc[i + 1]
        frac = 0.5 * (cc[i - 1] - cc[i + 1]) / den if den != 0 else 0.0
    else:
        frac = 0.0
    return float(lags[i] + frac)


def stack(frames: list[FrameInfo], cal: np.ndarray | None, params: StackParams, ctx: Ctx,
          flat: np.ndarray | None = None) -> tuple[np.ndarray, list[float]]:
    if not frames:
        raise ValueError("no frames ticked")
    imgs = [open_image(f.path) for f in frames]
    h, w = imgs[0].shape
    for img in imgs:
        if img.shape != (h, w):
            raise ValueError(f"{img.path.name}: size {img.shape[1]}×{img.shape[0]} differs from {w}×{h}")
    if cal is not None and cal.shape != (h, w):
        raise ValueError("calibration master size differs from the lights")
    n = len(imgs)

    shifts = [0.0] * n
    if params.align and n > 1:
        profiles = [row_profile(img, cal) for img in imgs]
        shifts = [subpixel_shift(profiles[0], p) for p in profiles]
        ctx.log(f"Align: Y shifts {', '.join(f'{s:+.2f}' for s in shifts)} px")
        return _stack_aligned(imgs, cal, flat, shifts, params, ctx), shifts

    def load(y0: int, y1: int) -> np.ndarray:
        block = np.empty((n, y1 - y0, w), np.float32)
        for i, img in enumerate(imgs):
            img.strip(y0, y1, out=block[i])
        if cal is not None:
            block -= cal[y0:y1]
        if flat is not None:
            block /= np.maximum(flat[y0:y1], np.float32(1e-3))
        return block

    return combine_strips(h, w, load, params.method, params.sigma, params.iters, ctx), shifts


def _stack_aligned(imgs: list[Image], cal: np.ndarray | None, flat: np.ndarray | None,
                   shifts: list[float], params: StackParams, ctx: Ctx) -> np.ndarray:
    from scipy.ndimage import shift as nd_shift

    h, w = imgs[0].shape
    full = np.empty((len(imgs), h, w), np.float32)
    for i, img in enumerate(imgs):
        ctx.step(0.5 * i / len(imgs))
        frame = img.read()
        if cal is not None:
            frame -= cal
        if flat is not None:
            frame /= np.maximum(flat, np.float32(1e-3))
        full[i] = nd_shift(frame, (-shifts[i], 0), order=1, mode="nearest") if shifts[i] else frame
    sub = Ctx(cancel=ctx.cancel, progress=lambda f: ctx.progress(0.5 + 0.5 * f), log=ctx.log)
    return combine_strips(h, w, lambda y0, y1: full[:, y0:y1], params.method, params.sigma,
                          params.iters, sub)


def stack_header(frames: list[FrameInfo], params: StackParams, cal_lines: list[str],
                 object_name: str) -> tuple[dict[str, Any], list[str]]:
    """Header of the first used frame + NCOMBINE/STACKMTH, first start / last end, provenance."""
    f0 = frames[0]
    h = {k: v for k, v in f0.header.items() if k not in STRUCTURAL}
    starts = [f.start for f in frames if f.start]
    ends = [f.end for f in frames if f.end]
    exps = sorted({f.exptime for f in frames if f.exptime is not None})
    if starts:
        h["DATE-OBS"] = min(starts).isoformat(timespec="microseconds").replace("+00:00", "")
    if ends:
        h["DATE-END"] = max(ends).isoformat(timespec="microseconds").replace("+00:00", "")
    h["NCOMBINE"] = len(frames)
    h["STACKMTH"] = params.method + (f" {params.sigma:g}sigma" if params.method == "sigma_clip_mean" else "")
    if len(exps) == 1:
        h["EXPTIME"] = exps[0]
        h["EXPTIME2"] = f"{len(frames)} x {exps[0]:g} s"
    if object_name:
        h["OBJNAME"] = object_name
    h.pop("DATE-AVG", None)  # per-frame value; wrong for the stack
    h.pop("JD_UTC", None)
    history = [f"{APP_NAME} stack: {params.method} of {len(frames)} frames"
               + (" (aligned)" if params.align else "")]
    history += [f"calibration: {line}" for line in cal_lines] or ["calibration: none"]
    history += [f"  {f.path.name}" for f in frames]
    return h, history


def stack_keywords(frames: list[FrameInfo], object_name: str, rows: dict[str, Any] | None,
                   coords: tuple[float | None, float | None] = (None, None)) -> dict[str, Any]:
    """The SX* block (core/stack_keys.py) plus OBJECT and RA/DEC when known. Every stack writer uses it."""
    from .stack_keys import rows1_of, sx_cards

    exps = sorted({f.exptime for f in frames if f.exptime is not None})
    h = sx_cards(object_name, len(frames), exps[0] if len(exps) == 1 else None, rows1_of(rows))
    if object_name:
        h["OBJECT"] = object_name
    if coords[0] is not None and coords[1] is not None:
        h["RA"], h["DEC"] = round(float(coords[0]), 6), round(float(coords[1]), 6)
    return h


def run_stack(ctx: Ctx, frames: list[FrameInfo], cal: np.ndarray | None, params: StackParams,
              out_path: Path, cal_lines: list[str], object_name: str = "",
              flat: np.ndarray | None = None, rows: dict[str, Any] | None = None,
              coords: tuple[float | None, float | None] = (None, None),
              extra: dict[str, Any] | None = None) -> StackResult:
    """Stack, then write the stack with the first frame's header, the stack facts and the SX* block (the rows
    chosen in Acquisition, Rows.to_json(), or None)."""
    from .stack_keys import COMMENTS

    data, shifts = stack(frames, cal, params, ctx, flat)
    ctx.cancel.check()
    header, history = stack_header(frames, params, cal_lines, object_name)
    header.update(stack_keywords(frames, object_name, rows, coords))
    header.update(extra or {})  # SXRAWDIR and the masters used (stack_job)
    write_float32(out_path, data, header, history, COMMENTS, keep_sx=True)
    background = float(np.median(data[::4, ::4]))
    w = data.shape[1]
    prof = np.median(data[:, w // 4: 3 * w // 4: 4], axis=1)
    peak_row = int(np.argmax(prof))
    summary = f"{len(frames)} frames · bg {background:.0f} ADU · spectrum row {peak_row}"
    return StackResult(out_path, len(frames), background, peak_row, summary, shifts)
