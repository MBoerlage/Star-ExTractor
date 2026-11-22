"""core/watcher.py: stable-file detection (doc test 1). Time is passed in, so no sleeping."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np
from astropy.io import fits

from starextractor.core.watcher import FolderWatcher


def _fits(path: Path, mtime: float) -> Path:
    fits.PrimaryHDU(np.zeros((4, 4), np.int16)).writeto(path, overwrite=True)
    os.utime(path, (mtime, mtime))
    return path


def test_new_file_reported_once_when_stable(tmp_path: Path) -> None:
    w = FolderWatcher(tmp_path, interval_s=2)
    t = time.time()
    _fits(tmp_path / "a.fits", t)
    assert w.poll(now=t) == []  # first sight
    assert w.poll(now=t + 1) == []  # unchanged but younger than one interval
    assert [p.name for p in w.poll(now=t + 3)] == ["a.fits"]
    assert w.poll(now=t + 5) == []  # once only


def test_growing_file_is_not_read_until_stable(tmp_path: Path) -> None:
    w = FolderWatcher(tmp_path, interval_s=2)
    t = time.time() - 100
    p = tmp_path / "b.fits"
    full = tmp_path / "full.tmp"
    fits.PrimaryHDU(np.zeros((64, 64), np.int16)).writeto(full)
    data = full.read_bytes()
    p.write_bytes(data[:2880])  # header only so far
    os.utime(p, (t, t))
    assert w.poll(now=t + 10) == []
    with open(p, "ab") as fh:  # the capture program is still writing
        fh.write(data[2880:])
    os.utime(p, (t + 1, t + 1))
    assert w.poll(now=t + 10) == []  # size changed since the last poll
    assert [q.name for q in w.poll(now=t + 20)] == ["b.fits"]


def test_extensions_case_insensitive_and_filter(tmp_path: Path) -> None:
    t = time.time() - 100
    for name in ("one.FIT", "two.Fits", "three.fits", "notes.txt", "hd1.fits", "hd2.FIT"):
        if name.endswith(".txt"):
            (tmp_path / name).write_text("x")
        else:
            _fits(tmp_path / name, t)
    w = FolderWatcher(tmp_path, process_existing=True)
    w.poll(now=t + 50)
    got = sorted(p.name for p in w.poll(now=t + 60))
    assert got == ["hd1.fits", "hd2.FIT", "one.FIT", "three.fits", "two.Fits"]
    f = FolderWatcher(tmp_path, pattern="HD*", process_existing=True)
    f.poll(now=t + 50)
    assert sorted(p.name for p in f.poll(now=t + 60)) == ["hd1.fits", "hd2.FIT"]


def test_existing_files_skipped_unless_asked(tmp_path: Path) -> None:
    t = time.time() - 100
    _fits(tmp_path / "old.fits", t)
    w = FolderWatcher(tmp_path)
    w.poll(now=t + 50)
    assert w.poll(now=t + 60) == []
    _fits(tmp_path / "new.fits", t + 70)
    w.poll(now=t + 80)
    assert [p.name for p in w.poll(now=t + 90)] == ["new.fits"]


def test_not_a_fits_yet_is_retried(tmp_path: Path) -> None:
    t = time.time() - 100
    p = tmp_path / "c.fits"
    p.write_bytes(b"\0" * 100)
    os.utime(p, (t, t))
    w = FolderWatcher(tmp_path, process_existing=True)
    w.poll(now=t + 50)
    assert w.poll(now=t + 60) == []  # does not open: retried, not dropped
    _fits(p, t + 61)
    w.poll(now=t + 70)
    assert [q.name for q in w.poll(now=t + 80)] == ["c.fits"]


def test_run_loop_in_a_thread(tmp_path: Path) -> None:
    w = FolderWatcher(tmp_path, interval_s=0.05)
    got: list[str] = []
    stop = threading.Event()
    th = threading.Thread(target=w.run, args=(stop, lambda fs: got.extend(p.name for p in fs)), daemon=True)
    th.start()
    _fits(tmp_path / "d.fits", time.time() - 10)
    deadline = time.time() + 3
    while not got and time.time() < deadline:
        time.sleep(0.02)
    stop.set()
    th.join(1)
    assert got == ["d.fits"]
