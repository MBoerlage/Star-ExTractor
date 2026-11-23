"""Line-centre study for broad, low-resolution Balmer lines (docs/investigations/wavelength_2025-10-02_omiand.md).

    python tools/centre_study.py [--reps 12] [--data <data root>\\Processed] [--json out.json]

1. Synthetic benchmark (known centres): FWHM 8–16 px, depth 0.1–0.6, Gaussian / Lorentzian / Voigt shapes, continuum
   tilt ±40 % across 4 FWHM and curvature, S/N 15–200, correlated pixels; special cases (blend, Hε + Ca II H, emission
   core, hot pixel, line near the window edge). Bias, scatter, failure rate and uncertainty calibration per method.
2. Real data: every pair with Balmer lines. Leave-one-out residuals of the free dispersion fit, reference/target
   consistency, window sensitivity and bootstrap repeatability.
3. The decision rule (adopt a new default or keep M0) with the numbers.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from starextractor.core import balmer_search as bs  # noqa: E402
from starextractor.core import centroid as cz  # noqa: E402
from starextractor.core import wavelength as wl  # noqa: E402

FWHM0 = 12.0  # the starting FWHM guess every method gets (the true one is unknown to it)
DISP = 1.205  # Å/px, for reporting
N = 600
X = np.arange(N, dtype=np.float64)


# ---- methods ---------------------------------------------------------------------------------------------------
def methods() -> dict[str, callable]:
    """name → f(y, guess) -> Centre. Windows are total widths in units of the FWHM guess."""
    m = {
        "M0 auto (±12 px)": lambda y, g: _m0(y, g, 12, keep_guess=False),
        "M0 marker (±6 px)": lambda y, g: _m0(y, g, 6, keep_guess=True),
        "M1 argmin": lambda y, g: cz.m1_argmin(y, g, 1.25 * FWHM0),
        "M2 parabola 5": lambda y, g: cz.m2_parabola(y, g, 1.25 * FWHM0, 5),
        "M2 parabola 7": lambda y, g: cz.m2_parabola(y, g, 1.25 * FWHM0, 7),
        "M3 centroid": lambda y, g: cz.m3_centroid(y, g, 1.25 * FWHM0),
    }
    for shape, tag in (("gauss", "M4 Gauss"), ("lorentz", "M5 Lorentz"), ("voigt", "M6 pVoigt")):
        for wdt in (1.5, 2.5, 4.0):
            m[f"{tag} {wdt}×"] = (lambda s, w: lambda y, g: cz.fit_profile(y, g, FWHM0, shape=s, width=w))(shape, wdt)
        m[f"{tag} adaptive"] = (lambda s: lambda y, g: cz.fit_profile(y, g, FWHM0, shape=s, width=2.5,
                                                                      adaptive=True))(shape)
    m["M7 pVoigt/global cont"] = lambda y, g: cz.fit_profile(wl.normalised(y), g, FWHM0, shape="voigt", width=2.5,
                                                             adaptive=True, weights=False)
    m[M8] = lambda y, g: cz.fit_line_centre(y, g, FWHM0)
    m[M0V2] = _m0v2
    return m


M8 = "M8 profile-fit chain"  # pseudo-Voigt → Gauss → parabola, with flags (core/centroid.fit_line_centre)
M0V2 = "M0 v0.2 (default)"  # M0 + hot-pixel masking + re-centred window (+ broad-line fallback)


def _m0v2(y: np.ndarray, g: float, width: float = FWHM0) -> cz.Centre:
    return cz.m0_line(y, g, width)


_m0v2.wants_width = True


def _m0(y: np.ndarray, g: float, half: int, keep_guess: bool) -> cz.Centre:
    c = cz.m0_v01(wl.normalised(y), g, half)
    if not c.ok and keep_guess:  # the app keeps the dragged marker when the fit fails
        return cz.Centre(g, float("inf"), method="M0", ok=True, flags=[cz.FALLBACK])
    return c


# ---- synthetic lines -------------------------------------------------------------------------------------------
@dataclass
class Case:
    fwhm: float
    depth: float
    shape: str  # gauss | lorentz | voigt
    tilt: float  # fractional continuum change across 4 FWHM
    curv: float
    sn: float
    corr: bool
    special: str = ""


def make(case: Case, rng: np.random.Generator) -> tuple[np.ndarray, float, float]:
    """Profile, true centre, starting guess."""
    c = 300.0 + rng.uniform(-0.5, 0.5)
    u = (X - c) / (4.0 * case.fwhm)
    cont = 1000.0 * (1.0 + case.tilt * u + case.curv * u * u)
    eta = {"gauss": 0.0, "lorentz": 1.0, "voigt": 0.5}[case.shape]
    line = case.depth * cz.pvoigt(X, c, case.fwhm, eta)
    guess = c + rng.normal(0.0, 2.0)
    if case.special == "blend":  # a weaker line 0.8 FWHM to the red
        line = line + 0.35 * case.depth * cz.pvoigt(X, c + 0.8 * case.fwhm, 0.7 * case.fwhm, 0.3)
    elif case.special == "CaH":  # Hε 3970.07 with Ca II H 3968.47 (1.3 px to the blue), a narrower line
        line = line + 0.5 * case.depth * cz.pvoigt(X, c - 1.6 / DISP, 0.6 * case.fwhm, 0.2)
    flux = cont * (1.0 - np.clip(line, 0, 0.98))
    if case.special == "emission":  # a Be-star core: narrow emission inside the absorption, slightly off centre
        flux = flux + cont * 0.6 * case.depth * cz.gauss(X, c + 0.8, 0.35 * case.fwhm)
    sig = np.sqrt(np.clip(flux, 1.0, None) * 1000.0) / case.sn
    noise = rng.normal(0.0, 1.0, N)
    if case.corr:
        noise = np.convolve(noise, [0.25, 0.5, 0.25], mode="same") / math.sqrt(0.375)
    y = flux + sig * noise
    if case.special == "hot":
        y[round(c) + int(rng.integers(-4, 5))] += 8.0 * 1000.0
    if case.special == "edge":
        guess = c + rng.choice([-1, 1]) * 0.6 * case.fwhm
    return y, c, guess


def grid() -> list[Case]:
    out = []
    for fwhm in (8.0, 12.0, 16.0):
        for depth in (0.1, 0.3, 0.6):
            for shape in ("gauss", "lorentz", "voigt"):
                for tilt in (-0.4, 0.0, 0.4):
                    for sn in (15.0, 50.0, 200.0):
                        for corr in (False, True):
                            out.append(Case(fwhm, depth, shape, tilt, 0.15 if tilt else 0.0, sn, corr))
    return out


SPECIALS = [Case(12.0, 0.4, "voigt", 0.2, 0.0, 50.0, False, s) for s in ("blend", "CaH", "emission", "hot", "edge")]


def run_cases(cases: list[Case], reps: int, seed: int, ms: dict) -> dict[str, dict[str, list]]:
    rng = np.random.default_rng(seed)
    res = {k: {"err": [], "sig": [], "fail": [], "flags": []} for k in ms}
    for case in cases:
        for _ in range(reps):
            y, c, g = make(case, rng)
            for name, f in ms.items():
                try:
                    r = f(y, g)
                except Exception:  # noqa: BLE001 - a crash is a failure of that method
                    r = cz.Centre(g, float("inf"), ok=False)
                e = r.centre - c
                bad = (not r.ok) or not np.isfinite(e) or abs(e) > 0.5 * case.fwhm
                res[name]["fail"].append(bad)
                res[name]["err"].append(np.nan if bad else e)
                res[name]["sig"].append(r.sigma if (not bad and np.isfinite(r.sigma)) else np.nan)
                res[name]["flags"].append(r.flags)
    return res


def stats(r: dict[str, list]) -> dict[str, float]:
    e = np.array(r["err"], dtype=np.float64)
    s = np.array(r["sig"], dtype=np.float64)
    good = np.isfinite(e)
    eg = e[good]
    out = {"bias": float(np.mean(eg)) if len(eg) else np.nan,
           "scatter": float(np.std(eg)) if len(eg) else np.nan,
           "rms": float(np.sqrt(np.mean(eg ** 2))) if len(eg) else np.nan,
           "mad": float(1.4826 * np.median(np.abs(eg - np.median(eg)))) if len(eg) else np.nan,
           "fail": float(np.mean(r["fail"]))}
    ok = good & np.isfinite(s) & (s > 0)
    out["unc_ratio"] = float(np.sqrt(np.mean((e[ok] / s[ok]) ** 2))) if ok.sum() > 10 else np.nan
    return out


# ---- real data -------------------------------------------------------------------------------------------------
REAL_LINES = ("Hα", "Hβ", "Hγ", "Hδ", "Hε", "Hζ", "Hη")
APP_LINES = ("Hα", "Hβ", "Hγ", "Hδ", "Hε")
LAM = {**wl.BALMER, **bs.EXTRA_BALMER}


def loo_rms(cx: dict[str, float], order: int = 3) -> float:
    """Leave each INTERIOR line out (interpolation, never a 1400 px extrapolation) and predict it from the others.
    Order 3: the Star'Ex dispersion is not quadratic to better than ±1 Å (see the report), so a quadratic would measure
    the polynomial instead of the centres."""
    names = [k for k in REAL_LINES if k in cx and np.isfinite(cx[k])]
    if len(names) < order + 3:
        return np.nan
    res = []
    for k in names[1:-1]:
        rest = [n for n in names if n != k]
        c = np.polynomial.polynomial.polyfit([cx[n] for n in rest], [LAM[n] for n in rest], order)
        res.append(np.polynomial.polynomial.polyval(cx[k], c) - LAM[k])
    return float(np.sqrt(np.mean(np.square(res))))


def fit_rms(cx: dict[str, float], order: int = 2, lines: tuple[str, ...] = APP_LINES) -> float:
    """In-sample RMS of the fit the app makes (quadratic on Hα–Hε)."""
    names = [k for k in lines if k in cx and np.isfinite(cx[k])]
    if len(names) < order + 2:
        return np.nan
    x = [cx[n] for n in names]
    lam = [LAM[n] for n in names]
    c = np.polynomial.polynomial.polyfit(x, lam, order)
    return float(np.sqrt(np.mean(np.square(np.polynomial.polynomial.polyval(x, c) - lam))))


def centres(prof: np.ndarray, guesses: dict[str, float], f, widths: dict[float, float] | None = None
            ) -> dict[str, float]:
    out = {}
    for k, g in guesses.items():
        try:
            r = f(prof, g, (widths or {}).get(g, FWHM0)) if getattr(f, "wants_width", False) else f(prof, g)
        except Exception:  # noqa: BLE001
            continue
        if r.ok and abs(r.centre - g) < 15:
            out[k] = r.centre
    return out


def real_data(root: str, ms: dict, boot: int, seed: int) -> dict:
    from astropy.io import fits

    rng = np.random.default_rng(seed)
    pairs = {}
    for d in sorted(glob.glob(str(Path(root) / "*"))):
        name = Path(d).name
        profs, found, widths = {}, {}, {}
        ref_disp = None
        for img in ("Reference", "Target"):
            f = Path(d) / f"{img}_X.fit"
            if not f.exists():
                continue
            p = np.asarray(fits.getdata(f), dtype=np.float64)
            s = bs.search(p)
            ok = s.best is not None and not s.ambiguous and s.best.rms_a < 3.0
            if ok and img == "Target" and ref_disp:  # a target without Balmer absorption has no true pattern
                ok = abs(s.best.dispersion / ref_disp - 1.0) < 0.03
            if ok:
                profs[img], found[img] = p, {k: v for k, v in s.best.matched.items() if k in REAL_LINES}
                widths[img] = {d.x: d.width for d in s.dips}
                if img == "Reference":
                    ref_disp = s.best.dispersion
        if "Reference" in profs:
            pairs[name] = (profs, found, widths)
    out = {"pairs": {}, "methods": list(ms)}
    for name, (profs, found, widths) in pairs.items():
        row = {}
        for mname, f in ms.items():
            r = {}
            cref = centres(profs["Reference"], found["Reference"], f, widths["Reference"])
            r["n"] = len(cref)
            r["loo"] = loo_rms(cref)
            r["fit"] = fit_rms(cref)
            r["centres"] = cref
            # starting-point sensitivity: a marker dropped 3 px (rms) off the line
            moves = []
            for _ in range(8):
                jit = {k: g + rng.normal(0.0, 3.0) for k, g in found["Reference"].items()}
                cj = centres(profs["Reference"], jit, f, {jit[k]: widths["Reference"].get(g, FWHM0)
                                                          for k, g in found["Reference"].items()})
                moves += [abs(cj[k] - cref[k]) for k in cj if k in cref]
            r["guess_move"] = float(np.median(moves)) if moves else np.nan
            r["guess_fail"] = 1.0 - len(moves) / max(1, 8 * len(cref))
            if "Target" in profs:
                ctgt = centres(profs["Target"], found["Target"], f, widths["Target"])
                common = [k for k in cref if k in ctgt and not (k == "Hα" and "omiand" in name.lower())]
                diffs = [ctgt[k] - cref[k] for k in common]
                r["tgt_scatter"] = float(np.std(diffs)) if len(diffs) >= 3 else np.nan
                r["tgt_diffs"] = {k: ctgt[k] - cref[k] for k in common}
            row[mname] = r
        out["pairs"][name] = row
    # window sensitivity and bootstrap on the reference profiles: the chosen candidates only
    sens = {}
    for name, (profs, found, _widths) in pairs.items():
        p = profs["Reference"]
        sig = cz.noise_of(p)
        per = {}
        for tag, fs in (("M0 auto", [lambda y, g, h=h: _m0(y, g, h, False) for h in (8, 12, 18)]),
                        ("M6 pVoigt", [lambda y, g, w=w: cz.fit_profile(y, g, FWHM0, shape="voigt", width=w)
                                       for w in (1.5, 2.5, 4.0)]),
                        ("M4 Gauss", [lambda y, g, w=w: cz.fit_profile(y, g, FWHM0, shape="gauss", width=w)
                                      for w in (1.5, 2.5, 4.0)]),
                        ("M8 chain", [lambda y, g: cz.fit_line_centre(y, g, FWHM0)]),
                        ("M0 v0.2", [lambda y, g: cz.m0_line(y, g, FWHM0)])):
            spread = []
            for k, g in found["Reference"].items():
                cs = [c for f in fs if (c := centres(p, {k: g}, f).get(k)) is not None]
                if len(cs) == len(fs) and len(fs) > 1:
                    spread.append(float(np.ptp(cs)))
            boots = []
            sigs = []
            f0 = fs[len(fs) // 2]
            for k, g in found["Reference"].items():
                base = f0(p, g)
                vals = []
                for _ in range(boot):
                    q = p + rng.normal(0.0, sig, len(p)) * np.sqrt(np.clip(np.abs(p) / max(np.median(np.abs(p)),
                                                                                            1e-9), 0.25, 4.0))
                    c = centres(q, {k: g}, f0).get(k)
                    if c is not None:
                        vals.append(c)
                if len(vals) >= max(5, boot // 2):
                    boots.append(float(np.std(vals)) * math.sqrt(2.0))  # noise added on top of noise: ×√2 ≈ real
                    sigs.append(base.sigma if np.isfinite(base.sigma) else np.nan)
            per[tag] = {"window_spread_med": float(np.median(spread)) if spread else np.nan,
                        "boot_med": float(np.median(boots)) if boots else np.nan,
                        "reported_sigma_med": float(np.nanmedian(sigs)) if sigs and np.isfinite(sigs).any()
                        else np.nan}
        sens[name] = per
    out["sensitivity"] = sens
    return out


# ---- report ----------------------------------------------------------------------------------------------------
def decide(syn: dict[str, dict], real: dict, candidate: str, m0: str = "M0 auto (±12 px)") -> tuple[bool, list[str]]:
    raw_a, raw_b = syn["_raw"][candidate], syn["_raw"][m0]
    ea, eb = np.array(raw_a["err"], dtype=float), np.array(raw_b["err"], dtype=float)
    both = np.isfinite(ea) & np.isfinite(eb)
    a = {"bias": float(np.mean(ea[both])), "scatter": float(np.std(ea[both])), "fail": float(np.mean(raw_a["fail"]))}
    b = {"bias": float(np.mean(eb[both])), "scatter": float(np.std(eb[both])), "fail": float(np.mean(raw_b["fail"]))}
    notes = [f"(1) judged on the {both.sum()} synthetic lines both methods measured (M0 fails on the hard ones)"]
    c1 = abs(a["bias"]) <= abs(b["bias"]) + 0.02 and a["scatter"] < b["scatter"] and a["fail"] <= b["fail"] + 0.005
    notes.append(f"(1) synthetic: |bias| {abs(a['bias']):.3f} vs {abs(b['bias']):.3f} px, scatter {a['scatter']:.3f} "
                 f"vs {b['scatter']:.3f} px, failures {a['fail']:.1%} vs {b['fail']:.1%} → {'pass' if c1 else 'FAIL'}")
    ratios, meds_new, meds_old = [], [], []
    for _name, row in real["pairs"].items():
        n, o = row[candidate]["loo"], row[m0]["loo"]
        if np.isfinite(n) and np.isfinite(o) and o > 0:
            ratios.append(n / o)
            meds_new.append(n)
            meds_old.append(o)
    if ratios:
        gain = 1.0 - float(np.median(meds_new)) / float(np.median(meds_old))
        worst = max(ratios)
        c2 = gain >= 0.15 and worst <= 1.5
        notes.append(f"(2) real pairs: median LOO RMS {np.median(meds_new):.2f} vs {np.median(meds_old):.2f} Å "
                     f"({gain:+.0%}), worst pair ratio {worst:.2f} → {'pass' if c2 else 'FAIL'}")
    else:
        c2 = False
        notes.append("(2) real pairs: none usable → FAIL")
    return c1 and c2, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--special-reps", type=int, default=150)
    ap.add_argument("--boot", type=int, default=30)
    ap.add_argument("--data", default=None, help="Processed folder (default: the data root in the settings)")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--seed", type=int, default=20261123)
    a = ap.parse_args(argv)
    if a.data is None:
        from starextractor.core.settings import Settings
        a.data = str(Path(Settings()["data_root"] or ".") / "Processed")
    sys.stdout.reconfigure(encoding="utf-8")
    ms = methods()
    t0 = time.perf_counter()
    syn_raw = run_cases(grid(), a.reps, a.seed, ms)
    syn = {k: stats(v) for k, v in syn_raw.items()}
    syn_full = {**syn, "_raw": syn_raw}
    print(f"## Synthetic grid: {len(grid())} cases × {a.reps} ({time.perf_counter() - t0:.0f} s)")
    print(f"{'method':26} {'bias px':>8} {'scatter':>8} {'RMS px':>7} {'RMS Å':>6} {'fail':>6} {'σ ratio':>8}")
    for k, s in syn.items():
        print(f"{k:26} {s['bias']:+8.3f} {s['scatter']:8.3f} {s['rms']:7.3f} {s['rms'] * DISP:6.2f} {s['fail']:6.1%} "
              f"{s['unc_ratio']:8.2f}")
    # by S/N for the key methods
    key = ["M0 auto (±12 px)", "M0 marker (±6 px)", M0V2, "M6 pVoigt adaptive", M8]
    print("\n## RMS error (px) by S/N")
    for sn in (15.0, 50.0, 200.0):
        cases = [c for c in grid() if c.sn == sn]
        r = run_cases(cases, max(2, a.reps // 2), a.seed + int(sn), {k: ms[k] for k in key})
        print(f"S/N {sn:4.0f}: " + "  ".join(f"{k.split(' (')[0]} {stats(v)['rms']:.3f} ({stats(v)['fail']:.0%})"
                                            for k, v in r.items()))
    print("\n## Tilt ±40 % (S/N 50, FWHM 12): bias px")
    for tilt in (-0.4, 0.0, 0.4):
        cases = [Case(12.0, 0.4, "voigt", tilt, 0.0, 50.0, False)]
        r = run_cases(cases, 200, a.seed + 7, {k: ms[k] for k in key})
        print(f"tilt {tilt:+.1f}: " + "  ".join(f"{k.split(' (')[0]} {stats(v)['bias']:+.3f}" for k, v in r.items()))
    print("\n## Special cases (FWHM 12, depth 0.4, S/N 50): bias / scatter px, failures, flagged")
    spec = {}
    for case in SPECIALS:
        r = run_cases([case], a.special_reps, a.seed + 11, {k: ms[k] for k in key})
        spec[case.special] = {}
        cells = []
        for k, v in r.items():
            s = stats(v)
            flagged = float(np.mean([bool(f) for f in v["flags"]]))
            spec[case.special][k] = {**s, "flagged": flagged}
            cells.append(f"{k.split(' (')[0]} {s['bias']:+.2f}/{s['scatter']:.2f} {s['fail']:.0%} fl {flagged:.0%}")
        print(f"{case.special:9}: " + " | ".join(cells))
    real = {}
    if Path(a.data).exists():
        t1 = time.perf_counter()
        sel = {k: ms[k] for k in ["M0 auto (±12 px)", "M0 marker (±6 px)", "M1 argmin", "M2 parabola 7",
                                  "M3 centroid", "M4 Gauss 2.5×", "M4 Gauss adaptive", "M5 Lorentz adaptive",
                                  "M4 Gauss 1.5×", "M6 pVoigt 1.5×", "M6 pVoigt 2.5×", "M6 pVoigt adaptive",
                                  "M7 pVoigt/global cont", M8, M0V2]}
        real = real_data(a.data, sel, a.boot, a.seed)
        print(f"\n## Real data: leave-one-out RMS (interior lines, cubic model), Å ({time.perf_counter() - t1:.0f} s)")
        names = list(real["pairs"])
        print(f"{'method':26} " + " ".join(f"{n[:10]:>10}" for n in names) + f" {'median':>7}")
        for m in sel:
            vals = [real["pairs"][n][m]["loo"] for n in names]
            print(f"{m:26} " + " ".join(f"{v:10.2f}" for v in vals) + f" {np.nanmedian(vals):7.2f}")
        print("\n## The app's fit (quadratic, Hα–Hε): in-sample RMS, Å — limited by the polynomial, not the centres")
        for m in sel:
            vals = [real["pairs"][n][m]["fit"] for n in names]
            print(f"{m:26} " + " ".join(f"{v:10.2f}" for v in vals) + f" {np.nanmedian(vals):7.2f}")
        print("\n## Starting guess 3 px (rms) off: median centre change, px (failures)")
        for m in sel:
            vals = [real["pairs"][n][m]["guess_move"] for n in names]
            fails = [real["pairs"][n][m]["guess_fail"] for n in names]
            print(f"{m:26} " + " ".join(f"{v:5.2f} ({f:3.0%})" for v, f in zip(vals, fails, strict=True)))
        print("\n## Reference vs target: scatter of per-line centre differences, px (Be target: Hα left out)")
        for m in sel:
            vals = [real["pairs"][n][m].get("tgt_scatter", np.nan) for n in names]
            print(f"{m:26} " + " ".join(f"{v:10.2f}" for v in vals))
        print("\n## Window sensitivity (spread of the centre across windows) and bootstrap noise, median px")
        for n, per in real["sensitivity"].items():
            print(f"{n[:30]:30} " + "  ".join(f"{k}: win {v['window_spread_med']:.2f} boot {v['boot_med']:.2f} "
                                             f"σ {v['reported_sigma_med']:.2f}" for k, v in per.items()))
    cands = [M8, "M6 pVoigt adaptive", "M4 Gauss adaptive", "M6 pVoigt 1.5×"]
    verdicts = {}
    if real:
        print("\n## Decision rule")
        for c in cands:
            ok, notes = decide(syn_full, real, c)
            verdicts[c] = ok
            print(f"{c}: {'ADOPT' if ok else 'keep M0'}")
            for s in notes:
                print("   ", s)
    if real:
        print("\n## M0 v0.2 (the default) against M0 v0.1: the same fit with hot-pixel masking and a re-centred window")
        for k in ("bias", "scatter", "rms", "fail", "unc_ratio"):
            print(f"  {k:9} v0.1 {syn['M0 auto (±12 px)'][k]:+.3f}  v0.2 {syn[M0V2][k]:+.3f}")
        names = list(real["pairs"])
        v1 = [real["pairs"][n]["M0 auto (±12 px)"]["loo"] for n in names]
        v2 = [real["pairs"][n][M0V2]["loo"] for n in names]
        print(f"  real LOO median: v0.1 {np.nanmedian(v1):.2f} Å, v0.2 {np.nanmedian(v2):.2f} Å")
    if a.json:
        a.json.write_text(json.dumps({"synthetic": syn, "special": spec, "real": real, "verdicts": verdicts},
                                     default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else
                                     (o.tolist() if isinstance(o, np.ndarray) else str(o)), indent=1),
                          encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
