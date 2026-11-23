"""Diagnose the Wavelength step of one pair: dips, the best Balmer pattern, and what the v0.1 pipeline did with the
saved solution as its prior.

    python tools/diagnose_wavelength.py <data root>\\Processed\\2025-10-02_omiand__hd217782_2
        [--saved 370.734,0.120155,1.765e-07] [--manual "Hα=2519,Hβ=1113.9,Hδ=679.5,Hγ=240"]

Prints plain tables (UTF-8). Reads Reference_X.fit / Target_X.fit and session.json; writes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from starextractor.core import balmer_search as bs  # noqa: E402
from starextractor.core import wavelength as wl  # noqa: E402

CANDIDATES = {**{k: v for k, v in wl.BALMER.items()}, **bs.EXTRA_BALMER, **bs.OTHER_FEATURES}


def load(folder: Path, img: str) -> np.ndarray:
    return np.asarray(fits.getdata(folder / f"{img}_X.fit"), dtype=np.float64)


def dip_table(prof: np.ndarray, sol: wl.Solution | None, min_prom: float = 0.05) -> list[str]:
    out = [f"{'x':>7} {'depth':>6} {'prom':>6} {'width':>6}  {'λ (Å)':>8}  nearest feature (Δ px)"]
    for d in bs.find_dips(prof, min_prominence=min_prom):
        if sol is None:
            out.append(f"{d.x:7.0f} {d.depth:6.3f} {d.prominence:6.3f} {d.width:6.1f}")
            continue
        lam = float(sol.angstrom(d.x))
        name, l0 = min(CANDIDATES.items(), key=lambda kv: abs(float(sol.pixel(kv[1])) - d.x))
        dx = d.x - float(sol.pixel(l0))
        tag = f"{name} ({dx:+.1f})" if abs(dx) < 15 else "—"
        out.append(f"{d.x:7.0f} {d.depth:6.3f} {d.prominence:6.3f} {d.width:6.1f}  {lam:8.1f}  {tag}")
    return out


def pipeline_table(prof: np.ndarray, saved: wl.Solution, manual: dict[str, float],
                   truth: dict[str, float]) -> list[str]:
    """What v0.1 did (predicted positions, the ±80 px coarse shift, the dip each line got), then v0.2 on the same."""
    norm = wl.normalised(prof)
    names = list(wl.BALMER)
    predicted = {n: float(saved.pixel(wl.BALMER[n])) for n in names}
    s0 = wl.coarse_shift(norm, predicted)
    out = [f"prior: coeffs (nm) {', '.join(f'{c:.9g}' for c in saved.coeffs_nm)}",
           f"coarse_shift (search ±80 px): {s0:+.1f} px",
           f"{'line':5} {'pred x':>8} {'+shift':>8} {'centre':>8} {'ok':>4} {'true dip':>9} {'off':>8}"]
    for n in names:
        g = predicted[n] + s0
        lf = wl.fit_line(norm, manual.get(n, g), half=6 if n in manual else wl.FIT_HALF_WINDOW)
        t = truth.get(n, float("nan"))
        out.append(f"{n:5} {predicted[n]:8.1f} {g:8.1f} {lf.centre:8.1f} {lf.ok!s:>4} {t:9.0f} {lf.centre - t:+8.1f}"
                   + ("  (marker)" if n in manual else ""))
    for mode in ("refine", "free"):
        try:
            cal = wl.calibrate(prof, saved, mode, manual=manual)
        except ValueError as exc:
            out.append(f"v0.2 calibrate({mode}): {exc}")
            continue
        pts = ", ".join(f"{q.line}@{q.pixel:.1f}" for q in cal.points)
        out.append(f"v0.2 calibrate({mode}): mode {cal.mode}, status {cal.status}, order {cal.solution.order}, "
                   f"{len(cal.points)} lines [{pts}], shift {cal.shift_px:+.2f} px, "
                   f"RMS {cal.fit.rms_a if cal.fit else float('nan'):.3f} Å; messages: {cal.messages}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--saved", help="prior coefficients, nm (default: the snapshot in session.json)")
    ap.add_argument("--manual", default="", help='dragged markers, e.g. "Hα=2519,Hβ=1113.9"')
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sess = json.loads((a.folder / "session.json").read_text(encoding="utf-8"))
    p = sess["params"]["wavelength"]
    saved = wl.Solution([float(v) for v in (a.saved.split(",") if a.saved else p["saved"])])
    manual = {k.strip(): float(v) for k, v in (kv.split("=") for kv in a.manual.split(",") if kv.strip())}
    print(f"# {a.folder.name}")
    print(f"session wavelength params: mode {p.get('mode')}, manual {p.get('manual')}, target adjust "
          f"{((p.get('per_image') or {}).get('Target') or {}).get('adjust')}")
    res = {}
    for img in ("Reference", "Target"):
        prof = load(a.folder, img)
        r = bs.search(prof)
        res[img] = r
        print(f"\n## {img}: blind Balmer search")
        if r.best is None:
            print("  none:", r.messages)
            continue
        print("  best:  ", r.best.describe())
        print("  second:", r.second.describe() if r.second else "—", "(ambiguous)" if r.ambiguous else "")
        print("  coefficients (nm):", ", ".join(f"{c:.9g}" for c in r.best.solution.coeffs_nm))
        print("\n".join("  " + s for s in dip_table(prof, r.best.solution)))
    ref = res["Reference"].best
    truth = dict(ref.matched) if ref else {}
    print("\n## v0.1 line search on the reference (prior = saved solution), then v0.2 on the same profile")
    chk = bs.validate_prior(saved, load(a.folder, "Reference"), found_by=ref)
    print(f"  prior check: {chk.message} (found {chk.found}/{chk.total}, median offset {chk.median_offset:+.1f} px)")
    print("\n".join("  " + s for s in pipeline_table(load(a.folder, "Reference"), saved, {}, truth)))
    if manual:
        print(f"\n## same, with the dragged markers {manual}")
        print("\n".join("  " + s for s in pipeline_table(load(a.folder, "Reference"), saved, manual, truth)))
    if ref and res["Target"].best:
        print("\n## target − reference, per line (px)")
        t = res["Target"].best.matched
        d = {k: t[k] - ref.matched[k] for k in ref.matched if k in t}
        print("  " + "  ".join(f"{k} {v:+.1f}" for k, v in d.items()))
        print(f"  median {np.median(list(d.values())):+.2f} px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
