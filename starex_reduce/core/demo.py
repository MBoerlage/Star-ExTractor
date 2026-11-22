"""A cancellable busy job with real numpy work (used by the runner tests and benchmarks)."""
from __future__ import annotations

import time

import numpy as np

from .jobs import Ctx


def fake_stage(ctx: Ctx, stage_id: str, seconds: float = 2.0) -> dict:
    """Busy numpy loop of roughly `seconds`, in 40 chunks with progress and cancel checks."""
    rng = np.random.default_rng(0)
    block = rng.random((512, 3840), dtype=np.float32)
    chunks = 40
    t_end = time.perf_counter() + seconds
    acc = 0.0
    for i in range(chunks):
        ctx.step(i / chunks)
        target = time.perf_counter() + (t_end - time.perf_counter()) / (chunks - i)
        while time.perf_counter() < target:
            acc += float(np.median(block[:64]))  # keeps a core busy, releases the GIL in numpy
    ctx.step(1.0)
    return {"stage": stage_id, "summary": "Demo run", "value": acc}
