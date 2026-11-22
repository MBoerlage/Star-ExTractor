"""Camera conversion gain and read noise for SNR estimates (ZWO ASI585MM Pro). No Qt.

The frame header wins (SharpCap writes EGAINSAV = e-/ADU at the saved 16-bit depth, and RDNOISE in e-).
Otherwise the bundled table (data/asi585mm_pro.csv) is used by GAIN, interpolating only between points on the
same side of the HCG boundary at gain 200 (read noise drops from ~4 e- to ~1 e- there).
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

HCG_GAIN = 200.0
_TABLE = Path(__file__).resolve().parent.parent / "data" / "asi585mm_pro.csv"


@lru_cache(maxsize=1)
def table() -> list[tuple[float, float, float]]:
    with open(_TABLE, encoding="utf-8") as fh:
        rows = csv.DictReader(line for line in fh if not line.startswith("#"))
        return sorted((float(r["gain"]), float(r["e_per_adu_16bit"]), float(r["read_noise_e"])) for r in rows)


def from_table(gain: float) -> tuple[float, float]:
    """(e-/16-bit ADU, read noise e-) at `gain`, never interpolated across the HCG boundary."""
    side = [p for p in table() if (p[0] >= HCG_GAIN) == (gain >= HCG_GAIN)]
    below = [p for p in side if p[0] <= gain]
    above = [p for p in side if p[0] >= gain]
    if not below or not above:  # outside this side's range: nearest point on the same side
        p = min(side, key=lambda q: abs(q[0] - gain))
        return p[1], p[2]
    a, b = below[-1], above[0]
    if a[0] == b[0]:
        return a[1], a[2]
    t = (gain - a[0]) / (b[0] - a[0])
    return a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])


def gain_and_noise(header: dict[str, Any]) -> tuple[float, float, str]:
    """(G e-/16-bit ADU, R e-, source 'header' | 'table')."""
    g, r = header.get("EGAINSAV"), header.get("RDNOISE")
    try:
        if g is not None and r is not None and float(g) > 0:
            return float(g), float(r), "header"
    except (TypeError, ValueError):
        pass
    gain = float(header.get("GAIN") or 0)
    G, R = from_table(gain)
    if g is not None:
        try:
            G = float(g) if float(g) > 0 else G
        except (TypeError, ValueError):
            pass
    return G, R, "table"
