"""Background job runner: one active job per slot, cancellable, progress 0–1 at ≤ 30 Hz.

Jobs run on a QThreadPool. Results come back to the UI thread through queued signals; results of
a job that was superseded or cancelled are dropped, so the UI only ever sees the newest answer.
"""
from __future__ import annotations

import itertools
import time
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from starex_reduce.core.jobs import Cancelled, CancelToken, Ctx

PROGRESS_INTERVAL = 1 / 30


class _Bridge(QObject):
    progress = Signal(str, int, float)
    done = Signal(str, int, object)
    failed = Signal(str, int, str)
    cancelled = Signal(str, int)


class _Job(QRunnable):
    def __init__(self, slot: str, job_id: int, fn: Callable[..., Any], ctx: Ctx,
                 bridge: _Bridge, args: tuple, kwargs: dict) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.slot, self.job_id, self.fn, self.ctx = slot, job_id, fn, ctx
        self.bridge, self.args, self.kwargs = bridge, args, kwargs
        self._last = 0.0

    def _emit(self, signal: str, *args: object) -> None:
        try:
            getattr(self.bridge, signal).emit(self.slot, self.job_id, *args)
        except RuntimeError:  # the app is shutting down and the bridge is gone: drop the result
            self.ctx.cancel.cancel()

    def _progress(self, f: float) -> None:
        now = time.perf_counter()
        if f >= 1.0 or now - self._last >= PROGRESS_INTERVAL:
            self._last = now
            self._emit("progress", float(f))

    def run(self) -> None:
        self.ctx.progress = self._progress
        try:
            self.ctx.cancel.check()
            result = self.fn(self.ctx, *self.args, **self.kwargs)
            self.ctx.cancel.check()
        except Cancelled:
            self._emit("cancelled")
        except Exception as exc:  # noqa: BLE001 - every failure must reach the UI as a message
            msg = f"{type(exc).__name__}: {exc}"
            self._emit("failed", msg + "\n" + traceback.format_exc())
        else:
            self._emit("done", result)


class JobRunner(QObject):
    """`submit(slot, fn, *args)` runs `fn(ctx, *args)` in the pool, replacing any job in `slot`."""

    progress = Signal(str, float)
    finished = Signal(str, object)
    failed = Signal(str, str)
    cancelled = Signal(str)
    busy_changed = Signal(str, bool)

    def __init__(self, parent: QObject | None = None, max_threads: int | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        if max_threads:
            self.pool.setMaxThreadCount(max_threads)
        self._ids = itertools.count(1)
        self._active: dict[str, tuple[int, CancelToken]] = {}
        self._bridge = _Bridge()
        self._bridge.progress.connect(self._on_progress)
        self._bridge.done.connect(self._on_done)
        self._bridge.failed.connect(self._on_failed)
        self._bridge.cancelled.connect(self._on_cancelled)

    def submit(self, slot: str, fn: Callable[..., Any], *args: Any,
               log: Callable[[str], None] | None = None, **kwargs: Any) -> int:
        self.cancel(slot, emit=False)
        job_id = next(self._ids)
        ctx = Ctx(log=log or (lambda _m: None))
        self._active[slot] = (job_id, ctx.cancel)
        self.busy_changed.emit(slot, True)
        self.pool.start(_Job(slot, job_id, fn, ctx, self._bridge, args, kwargs))
        return job_id

    def cancel(self, slot: str, emit: bool = True) -> None:
        entry = self._active.pop(slot, None)
        if entry is not None:
            entry[1].cancel()
            if emit:
                self.busy_changed.emit(slot, False)
                self.cancelled.emit(slot)

    def cancel_all(self) -> None:
        for slot in list(self._active):
            self.cancel(slot)

    def is_busy(self, slot: str) -> bool:
        return slot in self._active

    def wait(self, msecs: int = -1) -> bool:
        return self.pool.waitForDone(msecs)

    def _current(self, slot: str, job_id: int) -> bool:
        entry = self._active.get(slot)
        return entry is not None and entry[0] == job_id

    @Slot(str, int, float)
    def _on_progress(self, slot: str, job_id: int, f: float) -> None:
        if self._current(slot, job_id):
            self.progress.emit(slot, f)

    @Slot(str, int, object)
    def _on_done(self, slot: str, job_id: int, result: object) -> None:
        if self._current(slot, job_id):
            del self._active[slot]
            self.busy_changed.emit(slot, False)
            self.finished.emit(slot, result)

    @Slot(str, int, str)
    def _on_failed(self, slot: str, job_id: int, msg: str) -> None:
        if self._current(slot, job_id):
            del self._active[slot]
            self.busy_changed.emit(slot, False)
            self.failed.emit(slot, msg)

    @Slot(str, int)
    def _on_cancelled(self, slot: str, job_id: int) -> None:
        if self._current(slot, job_id):  # cancelled from inside the job itself
            del self._active[slot]
            self.busy_changed.emit(slot, False)
            self.cancelled.emit(slot)
