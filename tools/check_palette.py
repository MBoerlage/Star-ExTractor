"""Contrast of the important colour pairs of every palette, normally and through a red filter.

    python tools/check_palette.py          # tables for default, night and red; exit 1 if night or red fail

The red filter is simulated by keeping only the red channel of each colour (G = B = 0), then the usual WCAG
relative-luminance contrast. Thresholds: 4.5:1 for text, 3:1 for outlines and large elements (the rail's state
blocks and their labels, see docs/THEMES.md), and the rail state fills (current, done, not run) at least 1.5:1 apart.
The default palette is reported only; night and red must pass through the filter.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starextractor.ui.theme import PALETTES, SPEC_TOKENS

TEXT, LARGE, STATES = 4.5, 3.0, 1.5
# (foreground, background, threshold, what)
PAIRS = (
    ("text", "bg", TEXT, "text"), ("text", "panel", TEXT, "text"), ("text_muted", "panel", TEXT, "text"),
    ("accent_text", "accent", TEXT, "text"), ("done_text", "done", LARGE, "rail label"),
    ("not_run_text", "not_run", LARGE, "rail label"), ("missing", "panel", LARGE, "outline"),
    ("ok", "panel", TEXT, "text"), ("warn", "panel", TEXT, "text"), ("err", "panel", TEXT, "text"),
)
STATE_FILLS = ("accent", "done", "not_run")  # rail: current, done, not run


def rgb(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(c: tuple[float, float, float], red_filter: bool = False) -> float:
    r, g, b = c
    if red_filter:
        g = b = 0.0
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(a: str, b: str, red_filter: bool = False) -> float:
    la, lb = luminance(rgb(a), red_filter), luminance(rgb(b), red_filter)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def colour(palette: str, name: str) -> str:
    return PALETTES[palette][SPEC_TOKENS[name]]


def rows(palette: str) -> list[tuple[str, float, float, float, bool]]:
    """(pair, normal, through the filter, threshold, passes through the filter)"""
    out = []
    for fg, bg, need, _what in PAIRS:
        n = contrast(colour(palette, fg), colour(palette, bg))
        f = contrast(colour(palette, fg), colour(palette, bg), red_filter=True)
        out.append((f"{fg} on {bg}", n, f, need, f >= need))
    for a, b in itertools.combinations(STATE_FILLS, 2):
        n = contrast(colour(palette, a), colour(palette, b))
        f = contrast(colour(palette, a), colour(palette, b), red_filter=True)
        out.append((f"fill {a} vs {b}", n, f, STATES, f >= STATES))
    return out


def report(palette: str) -> bool:
    ok = True
    print(f"\n{palette}")
    print(f"  {'pair':32} {'normal':>8} {'filter':>8} {'need':>6}")
    for name, n, f, need, good in rows(palette):
        flag = "" if good else "  << below"
        ok &= good
        print(f"  {name:32} {n:7.2f}:1 {f:7.2f}:1 {need:5.1f}{flag}")
    return ok


def main() -> int:
    results = {p: report(p) for p in PALETTES}
    bad = [p for p in ("night", "red") if not results[p]]
    print("\nnight and red pass through the filter" if not bad else f"\nFAIL through the filter: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
