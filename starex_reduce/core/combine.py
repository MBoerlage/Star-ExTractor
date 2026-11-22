"""Frame combination kernels on (n, rows, cols) float32 blocks, and a threaded strip driver."""
from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .jobs import Ctx

STRIP_ROWS = 128
THREADS = max(1, min(8, (os.cpu_count() or 2) - 1))


def sigma_clip_mean(d: np.ndarray, sigma: float = 3.0, iters: int = 3) -> np.ndarray:
    """Mean of the values within ±sigma·std of the centre (median to start), per pixel.

    Uses float32 weights and squared deviations (no sqrt, no boolean temporaries) for speed.
    """
    n = d.shape[0]
    if n < 3:
        return d.mean(axis=0, dtype=np.float32)
    center = np.partition(d, n // 2, axis=0)[n // 2]
    w = np.ones_like(d)
    dev = np.empty_like(d)
    lim_k = np.float32(sigma * sigma)
    for _ in range(iters):
        np.subtract(d, center, out=dev)
        dev *= dev
        cnt = w.sum(axis=0)
        var = (dev * w).sum(axis=0) / np.maximum(cnt - 1, 1)
        new_w = (dev <= lim_k * var).astype(np.float32)
        if np.array_equal(new_w, w):
            break
        w = new_w
        cnt = w.sum(axis=0)
        center = np.where(cnt > 0, (d * w).sum(axis=0) / np.maximum(cnt, 1), center)
    return center.astype(np.float32, copy=False)


def median(d: np.ndarray, sigma: float = 3.0, iters: int = 3) -> np.ndarray:
    return np.median(d, axis=0).astype(np.float32, copy=False)


def mean(d: np.ndarray, sigma: float = 3.0, iters: int = 3) -> np.ndarray:
    return np.mean(d, axis=0, dtype=np.float64).astype(np.float32, copy=False)


KERNELS: dict[str, Callable[..., np.ndarray]] = {"sigma_clip_mean": sigma_clip_mean, "median": median,
                                                 "mean": mean}


def combine_strips(height: int, width: int, load: Callable[[int, int], np.ndarray],
                   method: str, sigma: float, iters: int, ctx: Ctx,
                   strip_rows: int = STRIP_ROWS, threads: int = THREADS) -> np.ndarray:
    """Combine row strips in parallel. `load(y0, y1)` returns the (n, y1-y0, width) block,
    already calibrated. numpy releases the GIL, so strips really run concurrently."""
    kernel = KERNELS[method]
    out = np.empty((height, width), np.float32)
    starts = list(range(0, height, strip_rows))
    done = 0

    def work(y0: int) -> int:
        ctx.cancel.check()
        y1 = min(height, y0 + strip_rows)
        out[y0:y1] = kernel(load(y0, y1), sigma, iters)
        return y1 - y0

    with ThreadPoolExecutor(max_workers=threads) as pool:
        for rows in pool.map(work, starts):
            done += rows
            ctx.step(done / height)
    return out
