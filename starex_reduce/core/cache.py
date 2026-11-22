"""Stage-result caching: content keys, a byte-capped in-memory LRU and on-disk .npy files."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

CODE_VERSION = "1"  # bump when a stage algorithm changes so old cache entries miss


def file_fingerprint(path: str | Path) -> str:
    """Cheap identity of a raw input: path, size and mtime (no pixel reads)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return f"{p}|missing"
    return f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}"


def file_sha256(path: str | Path) -> str:
    """SHA256 of a file (read in 1 MB blocks)."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stage_key(stage: str, params: dict[str, Any], upstream: list[str]) -> str:
    payload = json.dumps(
        {"stage": stage, "v": CODE_VERSION, "params": params, "up": upstream},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def nbytes_of(value: Any) -> int:
    nbytes = getattr(value, "nbytes", None)
    if isinstance(nbytes, int):
        return nbytes
    if isinstance(value, dict):
        return sum(nbytes_of(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return sum(nbytes_of(v) for v in value)
    return 64


class ArrayLRU:
    """Thread-safe LRU keyed by cache key, evicting by total array bytes."""

    def __init__(self, cap_bytes: int) -> None:
        self.cap = cap_bytes
        self._items: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._size = 0
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        return self._size

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item[0]

    def put(self, key: str, value: Any) -> None:
        n = nbytes_of(value)
        with self._lock:
            if key in self._items:
                self._size -= self._items.pop(key)[1]
            if n > self.cap:
                return  # never hold something bigger than the whole cache
            self._items[key] = (value, n)
            self._size += n
            while self._size > self.cap:
                _, (_, old) = self._items.popitem(last=False)
                self._size -= old

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._items

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._size = 0


class DiskCache:
    """Intermediate arrays as `<pair folder>/work/cache/<key>_<name>.npy` (memory-mapped on load). work/ can be
    deleted to reclaim space: everything in it is recomputed."""

    def __init__(self, folder: Path) -> None:
        self.dir = folder / "work" / "cache"

    def path(self, key: str, name: str) -> Path:
        return self.dir / f"{key}_{name}.npy"

    def save(self, key: str, name: str, arr: np.ndarray) -> Path:
        import numpy as np

        self.dir.mkdir(parents=True, exist_ok=True)
        target = self.path(key, name)
        tmp = target.with_name(target.stem + ".tmp.npy")
        np.save(tmp, np.ascontiguousarray(arr))
        os.replace(tmp, target)
        return target

    def load(self, key: str, name: str) -> np.ndarray | None:
        import numpy as np

        p = self.path(key, name)
        if not p.exists():
            return None
        return np.load(p, mmap_mode="r")
