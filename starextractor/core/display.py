"""Display preparation (runs in workers): 8-bit stretched image, frame-strip thumbnail, levels.

Views never recompute pixels: zoom, pan and Original/Corrected only swap these cached arrays.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

STRIP_COLS = 64
LOW_ROWS, LOW_COLS = 4, 2


@dataclass
class DisplayImage:
    raw: np.ndarray  # float32 (rows, cols) for the cursor readout
    u8: np.ndarray  # uint8 stretched, same shape
    strip: np.ndarray  # uint8 (rows/4, STRIP_COLS) thumbnail for the frame strip
    levels: tuple[float, float]
    spectrum_row: float
    low: np.ndarray | None = None  # uint8 max-pooled (rows/LOW_ROWS, cols/LOW_COLS) for zoomed-out views

    @property
    def shape(self) -> tuple[int, int]:
        return self.raw.shape  # type: ignore[return-value]


def levels_of(img: np.ndarray) -> tuple[float, float]:
    sub = img[::4, ::4]
    lo, hi = np.percentile(sub, [1.0, 99.95])
    if hi <= lo:
        hi = lo + 1.0
    return float(lo), float(hi)


def stretch(img: np.ndarray, levels: tuple[float, float]) -> np.ndarray:
    """Square-root stretch between the levels: shows the faint sky next to a bright spectrum."""
    lo, hi = levels
    v = (img - np.float32(lo)) * np.float32(1.0 / (hi - lo))
    np.clip(v, 0, 1, out=v)
    np.sqrt(v, out=v)
    v *= np.float32(255)
    return v.astype(np.uint8)


def thumbnail(u8: np.ndarray, cols: int = STRIP_COLS, row_bin: int = 4) -> np.ndarray:
    h, w = u8.shape
    hh, ww = h // row_bin * row_bin, w // cols * cols
    t = u8[:hh, :ww].reshape(hh // row_bin, row_bin, cols, ww // cols)
    return t.max(axis=(1, 3)).astype(np.uint8)  # max keeps a thin spectrum visible


def prepare(img: np.ndarray, levels: tuple[float, float] | None = None) -> DisplayImage:
    from .geometry import spectrum_row

    img = np.ascontiguousarray(img, dtype=np.float32)
    lv = levels or levels_of(img)
    u8 = stretch(img, lv)
    return DisplayImage(img, u8, thumbnail(u8), lv, spectrum_row(img), pooled(u8))


def pooled(u8: np.ndarray, rows: int = LOW_ROWS, cols: int = LOW_COLS) -> np.ndarray:
    """Max-pool so a thin spectrum or line never disappears when the view is zoomed out."""
    h, w = u8.shape
    hh, ww = h // rows * rows, w // cols * cols
    return np.ascontiguousarray(u8[:hh, :ww].reshape(hh // rows, rows, ww // cols, cols).max(axis=(1, 3)))
