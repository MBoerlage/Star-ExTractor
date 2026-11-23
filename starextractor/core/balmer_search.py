"""Find the Balmer series in a reference profile without any saved solution (no Qt).

1. Dips: absorption features of the envelope-normalised profile (position, depth, prominence, width).
2. Pattern search: every pair of strong dips is tried as every pair of Balmer lines (pixel order = wavelength order,
   dispersion inside the instrument's range); each hypothesis λ(x) = λ0 + d·x is scored by the Balmer lines that land
   on a dip, weighted by the dip's prominence and by how close it lands. A dip that sits closer to a known non-Balmer
   feature (Na D, He I 5876, telluric O2 / H2O bands) under the same hypothesis is never taken as a Balmer line.
3. The best hypotheses are refined by least squares (quadratic when 4+ lines) and re-matched with a tight tolerance;
   the best and the best *different* one are returned, so an ambiguous profile is reported, not guessed.

A saved solution is only a prior: `validate_prior` checks that its predicted lines have dips nearby before use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
from scipy import signal

from . import linelists
from .wavelength import BALMER, Solution, continuum

# The higher series members: optional anchors for A-type stars (air, Å; NIST)
EXTRA_BALMER: dict[str, float] = {"Hζ": 3889.049, "Hη": 3835.384}
REQUIRED = ("Hα", "Hβ", "Hγ", "Hδ", "Hε")
MIN_LINES = 3
DISP_RANGE = (1.0, 1.4)  # Å/px, Star'Ex LR on an ASI585MM (1.2 Å/px) with margin
TOL_COARSE = 22.0  # px: a linear hypothesis vs. the true (slightly curved) solution
TOL_FINE = 6.0  # px after the least-squares refinement
PRIOR_TOL = 25.0  # px: a saved solution must put 2/3 of the lines this close to a dip
CURV_MAX = 1.0e-6  # nm/px²: the Star'Ex LR quadratic term is 1.8e-7 to 4.7e-7 on real nights; a larger one
# means a wrong line is being absorbed (Hα is the only red anchor, a quadratic can always bend onto it)
PRUNE_RMS = 2.0  # Å: a matched set fitting worse than this still holds a wrong line
AMBIGUOUS = 0.85  # second-best score / best score above this → ask the user
LINE_WIDTH = 6.0  # px: Balmer dips are 11–27 px wide at R ≈ 360; narrower dips are noise or metal lines
# features that look like Balmer lines in a low-resolution profile but are not
OTHER_FEATURES: dict[str, float] = {
    "Na D": 5892.94, "He I 5876": 5875.62, "He I 4471": 4471.48, "He I 4026": 4026.19, "Ca II K": 3933.66,
    **{ln.label: ln.lam_a for ln in linelists.of_kind(linelists.TELLURIC)},
}


@dataclass
class Dip:
    x: float
    depth: float  # 1 − normalised flux at the minimum
    prominence: float
    width: float  # px at half prominence


@dataclass
class Hypothesis:
    solution: Solution
    matched: dict[str, float]  # line → dip x
    score: float
    rms_a: float = float("nan")
    n_required: int = 0

    @property
    def dispersion(self) -> float:
        return float(self.solution.dispersion_a(1500.0))

    def describe(self) -> str:
        names = " ".join(n for n in (*REQUIRED, *EXTRA_BALMER) if n in self.matched)
        return (f"{names} at {self.dispersion:.3f} Å/px, Hβ at x={self.solution.pixel(BALMER['Hβ']):.0f}, "
                f"RMS {self.rms_a:.2f} Å, score {self.score:.2f}")


@dataclass
class SearchResult:
    best: Hypothesis | None
    second: Hypothesis | None
    dips: list[Dip]
    ambiguous: bool = False
    messages: list[str] = field(default_factory=list)


def find_dips(profile: np.ndarray, min_prominence: float = 0.03, min_width: float = 3.0) -> list[Dip]:
    p = np.asarray(profile, dtype=np.float64)
    norm = p / continuum(p)
    k, props = signal.find_peaks(-norm, prominence=min_prominence, width=min_width)
    edge = 8
    return [Dip(float(i), float(1.0 - norm[i]), float(pr), float(w))
            for i, pr, w in zip(k, props["prominences"], props["widths"], strict=True) if edge <= i < len(p) - edge]


def pixel_of(sol: Solution, lam_a: float) -> float:
    """Detector column of a wavelength; NaN where the solution never reaches it (a curved trial solution)."""
    try:
        return float(sol.pixel(lam_a))
    except (ValueError, ZeroDivisionError):
        return float("nan")


def _lines(extra: bool) -> dict[str, float]:
    return {**BALMER, **(EXTRA_BALMER if extra else {})}


def _assign(sol: Solution, dips: list[Dip], lines: dict[str, float], n: int, tol: float) -> dict[str, int]:
    """Line → dip index: each dip to the nearest predicted feature (Balmer or not); a Balmer line keeps its dip only
    when no other feature is nearer and no other Balmer line claimed the same dip more closely."""
    feats = {**{f"B:{k}": v for k, v in lines.items()}, **{f"O:{k}": v for k, v in OTHER_FEATURES.items()}}
    pred = {k: pixel_of(sol, v) for k, v in feats.items()}
    xs = np.array([d.x for d in dips])
    best: dict[int, tuple[float, str]] = {}
    for k, px in pred.items():
        if not -tol <= px < n + tol or not len(xs):
            continue
        i = int(np.argmin(np.abs(xs - px)))
        dist = abs(xs[i] - px)
        if dist <= tol and (i not in best or dist < best[i][0]):
            best[i] = (dist, k)
    return {k[2:]: i for i, (_d, k) in best.items() if k.startswith("B:")}


def _score(sol: Solution, dips: list[Dip], lines: dict[str, float], n: int, tol: float) -> Hypothesis:
    m = _assign(sol, dips, lines, n, tol)
    score = 0.0
    for name, i in m.items():
        d = abs(dips[i].x - pixel_of(sol, lines[name]))
        score += min(dips[i].prominence, 0.6) * (1.0 - (d / tol) ** 2)
    return Hypothesis(sol, {k: dips[i].x for k, i in m.items()}, score,
                      n_required=sum(1 for k in m if k in REQUIRED))


def _refine(h: Hypothesis, dips: list[Dip], lines: dict[str, float], n: int) -> Hypothesis:
    cur = h
    for tol in (TOL_COARSE, 12.0, TOL_FINE):
        names = sorted(cur.matched, key=lambda k: lines[k])
        if len(names) < 2:
            return cur
        x = np.array([cur.matched[k] for k in names])
        lam = np.array([lines[k] for k in names])
        order = 2 if len(names) >= 4 else 1
        c = np.polynomial.polynomial.polyfit(x, lam / 10.0, order)
        if order == 2 and abs(c[2]) > CURV_MAX:
            c = np.polynomial.polynomial.polyfit(x, lam / 10.0, 1)
        sol = Solution([float(v) for v in c])
        if np.any(np.diff(sol.angstrom(np.linspace(0, n - 1, 64))) <= 0):
            return cur  # not monotonic: keep the linear hypothesis
        nxt = _score(sol, dips, lines, n, tol)
        if nxt.n_required < cur.n_required:
            break
        cur = nxt
    return _prune(cur, lines)


def _fit(matched: dict[str, float], lines: dict[str, float]) -> tuple[Solution, np.ndarray, list[str]]:
    names = sorted(matched, key=lambda k: lines[k])
    x = np.array([matched[k] for k in names])
    lam = np.array([lines[k] for k in names])
    c = np.polynomial.polynomial.polyfit(x, lam / 10.0, 2 if len(names) >= 4 else 1)
    if len(c) == 3 and abs(c[2]) > CURV_MAX:
        c = np.polynomial.polynomial.polyfit(x, lam / 10.0, 1)
    sol = Solution([float(v) for v in c])
    return sol, np.asarray(sol.angstrom(x)) - lam, names


def _prune(h: Hypothesis, lines: dict[str, float]) -> Hypothesis:
    """Refit on the matched dips; while the fit is not a calibration (RMS above PRUNE_RMS), the line that fits
    worst is not that line after all: drop it (never below MIN_LINES lines)."""
    matched = dict(h.matched)
    if len(matched) < 2:
        return h
    sol, res, names = _fit(matched, lines)
    while float(np.sqrt(np.mean(res ** 2))) > PRUNE_RMS and len(matched) > MIN_LINES:
        # the worst line predicted from the others (Hα, the only red anchor, pulls any fit onto itself)
        loo = []
        for k in names:
            rest = {q: v for q, v in matched.items() if q != k}
            s_k = _fit(rest, lines)[0]
            loo.append(abs(float(s_k.angstrom(matched[k])) - lines[k]))
        del matched[names[int(np.argmax(loo))]]
        sol, res, names = _fit(matched, lines)
    score = h.score * len(matched) / max(1, len(h.matched))
    return Hypothesis(sol, matched, score, float(np.sqrt(np.mean(res ** 2))),
                      sum(1 for k in matched if k in REQUIRED))


def search(profile: np.ndarray, disp_range: tuple[float, float] = DISP_RANGE, extra: bool = True,
           n_seed: int = 30) -> SearchResult:
    """The Balmer pattern in `profile` from scratch (no saved solution)."""
    n = len(profile)
    dips = [d for d in find_dips(profile) if d.width >= LINE_WIDTH]
    lines = _lines(extra)
    if len(dips) < MIN_LINES:
        return SearchResult(None, None, dips, messages=["fewer than 3 absorption dips in the reference profile"])
    seeds = sorted(range(len(dips)), key=lambda i: -dips[i].prominence)[:n_seed]
    seeds.sort(key=lambda i: dips[i].x)
    names = sorted(lines, key=lines.get)
    hyps: list[Hypothesis] = []
    seen: set[tuple[int, int]] = set()
    for i, j in combinations(seeds, 2):  # dips[i].x < dips[j].x
        for a, b in combinations(names, 2):  # lines[a] < lines[b]
            d = (lines[b] - lines[a]) / (dips[j].x - dips[i].x)
            if not disp_range[0] <= d <= disp_range[1]:
                continue
            lam0 = lines[a] - d * dips[i].x
            key = (round(d * 200), round(lam0 / 4))
            if key in seen:
                continue
            seen.add(key)
            h = _score(Solution([lam0 / 10.0, d / 10.0]), dips, lines, n, TOL_COARSE)
            if h.n_required >= 2:
                hyps.append(h)
    # refine the best by line count AND the best by score: many loose 5-line matches at the coarse tolerance can
    # crowd out a clean 4-line pattern (a missing Hγ), and they lose lines once refined
    by_count = sorted(hyps, key=lambda h: (h.n_required, h.score), reverse=True)[:40]
    by_score = sorted(hyps, key=lambda h: h.score, reverse=True)[:40]
    pool = list({id(h): h for h in (*by_count, *by_score)}.values())
    refined: list[Hypothesis] = []
    for h in pool:
        r = _refine(h, dips, lines, n)
        if r.n_required >= 2:
            refined.append(r)
    # the score, not the line count: lines weighted by their dip's prominence and closeness. Ranked by count first, a
    # pattern on five weak bumps (v0.1's O2 γ / Hη match) beat the true series with one line missing
    refined = [h for h in refined if h.n_required >= MIN_LINES] or refined
    refined.sort(key=lambda h: h.score, reverse=True)
    if not refined or refined[0].n_required < MIN_LINES:
        return SearchResult(None, refined[0] if refined else None, dips,
                            messages=["no Balmer pattern with at least 3 lines (Hα–Hε) in the reference profile"])
    best = refined[0]
    hb = pixel_of(best.solution, BALMER["Hβ"])
    second = next((h for h in refined[1:] if abs(pixel_of(h.solution, BALMER["Hβ"]) - hb) > 25.0), None)
    res = SearchResult(best, second, dips)
    if second is not None and second.score >= AMBIGUOUS * best.score:
        res.ambiguous = True
        res.messages.append("two Balmer patterns fit almost equally well: " + best.describe() + " | "
                            + second.describe())
    return res


@dataclass
class PriorCheck:
    ok: bool
    found: int
    total: int
    median_offset: float  # px: dips minus predicted lines (the nearest dip of each line)
    message: str


def validate_prior(sol: Solution, profile: np.ndarray, dips: list[Dip] | None = None,
                   tol: float = PRIOR_TOL, source: str = "", found_by: Hypothesis | None = None) -> PriorCheck:
    """Does a saved solution fit tonight? At least 2/3 of the required lines (on the detector) need a dip
    (prominence ≥ 0.1) within `tol` px of where the solution puts them."""
    n = len(profile)
    dips = find_dips(profile) if dips is None else dips
    strong = np.array([d.x for d in dips if d.prominence >= 0.1 and d.width >= LINE_WIDTH])
    offs = []
    found = total = 0
    for name in REQUIRED:
        px = float(sol.pixel(BALMER[name]))
        if not 0 <= px < n:
            continue
        total += 1
        if len(strong):
            off = float(strong[np.argmin(np.abs(strong - px))] - px)
            offs.append(off)
            found += abs(off) <= tol
    med = float(np.median(offs)) if offs else float("nan")
    if found_by is not None:  # the true offset: where the pattern search found the lines
        d = [found_by.matched[k] - float(sol.pixel(BALMER[k])) for k in REQUIRED if k in found_by.matched]
        med = float(np.median(d)) if d else med
    ok = total >= MIN_LINES and found >= int(np.ceil(2 * total / 3))
    where = f" ({source})" if source else ""
    msg = (f"Saved solution{where} fits this night: {found} of {total} lines within {tol:.0f} px of a dip" if ok else
           f"Saved solution{where} does not fit this night (markers {abs(med):.0f} px from the dips)")
    return PriorCheck(ok, found, total, med, msg)
