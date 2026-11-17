"""Cancellation and progress plumbing shared by core stages and the Qt worker layer."""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field


class Cancelled(Exception):
    """Raised inside a stage when its cancel token has been triggered."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()


def _no_progress(_f: float) -> None:
    pass


@dataclass
class Ctx:
    """Passed to every stage: cancel token, progress callback (0–1) and a log sink."""

    cancel: CancelToken = field(default_factory=CancelToken)
    progress: Callable[[float], None] = _no_progress
    log: Callable[[str], None] = _no_progress  # type: ignore[assignment]

    def step(self, fraction: float) -> None:
        """Report progress and honour cancellation in one call."""
        self.cancel.check()
        self.progress(min(max(fraction, 0.0), 1.0))
