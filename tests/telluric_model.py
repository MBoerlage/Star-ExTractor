"""Synthetic atmosphere for the response tests: O2 B/A and H2O bands with wide shallow wings."""
from __future__ import annotations

import numpy as np


def transmission(lam: np.ndarray, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.ones_like(lam, dtype=float)

    def band(lo: float, hi: float, depth: float, wing_a: float, wing_depth: float) -> None:
        core = (lam >= lo) & (lam <= hi)
        t[core] *= 1 - depth * (0.6 + 0.4 * rng.random(core.sum()))
        for edge, sign in ((lo, -1), (hi, 1)):  # wings fall off away from the band
            d = (lam - edge) * sign
            wing = (d > 0) & (d < 2.5 * wing_a)
            t[wing] *= 1 - wing_depth * np.exp(-d[wing] / wing_a) * (0.5 + 0.5 * rng.random(wing.sum()))

    band(6867, 6930, 0.55, 30, 0.10)  # O2 B
    band(7594, 7700, 0.65, 35, 0.12)  # O2 A
    band(7160, 7340, 0.18, 40, 0.08)  # H2O, ragged
    band(6532, 6587, 0.08, 15, 0.03)  # H2O near Hα
    return t
