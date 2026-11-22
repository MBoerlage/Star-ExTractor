"""Folder polling for the Acquisition tab. No Qt: `poll()` is a plain method (tests call it directly) and
`run()` is a loop for a background thread.

A file is reported once, only when it is stable: same size and mtime as at the previous poll, mtime at least
one poll interval old, and it opens as FITS. Capture software writes a frame over a second or more; a half
written file is never read, it is simply retried at the next poll.
"""
from __future__ import annotations

import fnmatch
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .fitsio import FitsError, read_header_info

EXTENSIONS = (".fit", ".fits")


class FolderWatcher:
    def __init__(self, folder: str | Path, pattern: str = "", process_existing: bool = False,
                 interval_s: float = 2.0) -> None:
        self.folder = Path(folder)
        self.pattern = pattern.strip()
        self.interval_s = interval_s
        self.done: set[str] = set()
        self._seen: dict[str, tuple[int, float]] = {}  # name -> (size, mtime) at the previous poll
        if not process_existing:
            self.done = {p.name for p in self._candidates()}

    def _candidates(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        out = []
        for p in self.folder.iterdir():
            if not p.is_file() or p.suffix.lower() not in EXTENSIONS:
                continue
            if self.pattern and not fnmatch.fnmatch(p.name.lower(), self.pattern.lower()):
                continue
            out.append(p)
        return out

    def poll(self, now: float | None = None) -> list[Path]:
        """New stable files since the last poll, oldest first."""
        now = time.time() if now is None else now
        ready = []
        for p in self._candidates():
            if p.name in self.done:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime)
            prev = self._seen.get(p.name)
            self._seen[p.name] = sig
            if prev != sig or now - st.st_mtime < self.interval_s:
                continue  # still being written (or first sight): look again next poll
            try:
                read_header_info(p)
            except (FitsError, OSError, ValueError):
                continue  # opens later, once complete
            ready.append(p)
        ready.sort(key=lambda q: (q.stat().st_mtime, q.name))
        for p in ready:
            self.done.add(p.name)
            self._seen.pop(p.name, None)
        return ready

    def run(self, stop: threading.Event, on_files: Callable[[list[Path]], None],
            paused: threading.Event | None = None) -> None:
        while not stop.is_set():
            if paused is None or not paused.is_set():
                files = self.poll()
                if files:
                    on_files(files)
            stop.wait(self.interval_s)
