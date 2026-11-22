from __future__ import annotations

import itertools
import time

import pytest
from pytestqt.qtbot import QtBot

from starextractor.core.demo import fake_stage
from starextractor.core.jobs import Cancelled, CancelToken, Ctx
from starextractor.workers.runner import JobRunner


def test_cancel_token_raises() -> None:
    tok = CancelToken()
    tok.check()
    tok.cancel()
    with pytest.raises(Cancelled):
        Ctx(cancel=tok).step(0.5)


def test_job_reports_progress_and_result(qtbot: QtBot) -> None:
    runner = JobRunner()
    seen: list[float] = []
    runner.progress.connect(lambda _s, f: seen.append(f))
    with qtbot.waitSignal(runner.finished, timeout=5000) as sig:
        runner.submit("files", fake_stage, "files", seconds=0.3)
    assert sig.args[0] == "files" and sig.args[1]["stage"] == "files"
    assert seen and seen[-1] == 1.0 and all(0 <= f <= 1 for f in seen)
    assert not runner.is_busy("files")


def test_cancel_stops_job_quickly(qtbot: QtBot) -> None:
    runner = JobRunner()
    finished: list[str] = []
    runner.finished.connect(lambda s, _r: finished.append(s))
    runner.submit("geometry", fake_stage, "geometry", seconds=5.0)
    qtbot.wait(100)
    t = time.perf_counter()
    with qtbot.waitSignal(runner.cancelled, timeout=1000):
        runner.cancel("geometry")
    runner.wait(2000)
    assert time.perf_counter() - t < 1.0
    qtbot.wait(50)
    assert finished == []


def test_new_submit_supersedes_old(qtbot: QtBot) -> None:
    runner = JobRunner()
    results: list[float] = []
    runner.finished.connect(lambda _s, r: results.append(r))
    runner.submit("extract", lambda ctx, v: (time.sleep(0.2), v)[1], 1.0)
    with qtbot.waitSignal(runner.finished, timeout=3000):
        runner.submit("extract", lambda ctx, v: v, 2.0)
    runner.wait(2000)
    qtbot.wait(50)
    assert results == [2.0]


def test_failure_is_reported_not_raised(qtbot: QtBot) -> None:
    runner = JobRunner()

    def boom(ctx: Ctx) -> None:
        raise ValueError("bad frame")

    with qtbot.waitSignal(runner.failed, timeout=3000) as sig:
        runner.submit("files", boom)
    assert "ValueError: bad frame" in sig.args[1]


def test_ui_thread_stays_responsive(qtbot: QtBot) -> None:
    """While a job runs, the event loop keeps ticking at UI rate (no blocking)."""
    from PySide6.QtCore import QTimer

    runner = JobRunner()
    ticks: list[float] = []
    timer = QTimer()
    timer.setInterval(16)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start()
    with qtbot.waitSignal(runner.finished, timeout=5000):
        runner.submit("files", fake_stage, "files", seconds=1.0)
    timer.stop()
    gaps = [b - a for a, b in itertools.pairwise(ticks)]
    assert len(ticks) > 30
    assert max(gaps) < 0.1
