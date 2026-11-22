"""The target's own wavelength solution: the reference solution plus a small, measured correction (no Qt).

Sign convention (docs/WAVELENGTH.md): pixel x is the 0-based column of the straightened frame. A correction
    D(x) = s + k (x − x0) + q (x − x0)²          (x0 = middle of the used pixel range)
means the target's wavelength at pixel x is  λ_target(x) = λ_ref(x − D(x)).  A line of rest wavelength λ therefore
appears in the target at  x = x_ref(λ) + D,  so every anchor gives  D = x_measured − x_ref(λ).  Positive s: the
target's features sit at larger x than the reference solution predicts.

Anchors: sky emission lines (zero velocity), telluric bands matched differentially against the reference star,
interstellar lines (low weight), and stellar lines sharing ONE velocity v:  D = x − x_ref(λ (1 + v/c)), linearised
as  D = s + … + λ v / (c · dispersion).  The target solution never becomes a free calibration: Auto starts from a
shift only (M0) and accepts a scale (M1) or a quadratic (M2) only when clearly better and within limits.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import linelists
from .linelists import FIXED_KINDS, ISM, SKY_KINDS, STELLAR_KINDS, TELLURIC, Line
from .skylines import SkyResult
from .target_wave import _band_shift, flattened, measure_line
from .wavelength import Solution

C_KMS = 299_792.458
# ---- priors and limits (named here, documented in docs/WAVELENGTH.md) ----------------------------------------
S_SIGMA_PX = 200.0  # wide prior on the shift
K_SIGMA = 0.002  # scale prior (0.2 %)
K_SIGMA_FORCED = 0.02  # the explicit "Shift and scale" mode: much looser
Q_EDGE_SIGMA_PX = 0.5  # quadratic prior: its effect at the range edges (px)
V_SIGMA_KMS = 150.0  # stellar velocity prior
SYS_PX = 0.15  # added in quadrature to every anchor's centroid error
HUBER_C = 1.5  # robust loss (in σ)
REJECT_SIGMA = 3.0  # outlier rejection after the robust fit
GROSS_PX = 4.0  # anything this far from the fit is a misidentification
EMISSION_WEIGHT = 0.3  # stellar emission / asymmetric lines (Be, P Cyg, symbiotics) count less
MIN_FIXED_ANCHORS = 2  # fixed anchors (sky, telluric, ISM) needed to separate shift from velocity
MIN_SPAN_PX = 800.0  # … and their spread
DBIC_MIN = 10.0  # Auto: a richer model must lower BIC by this much
M1_MIN_ANCHORS, M2_MIN_ANCHORS = 4, 6
SPREAD_FRAC = 0.6  # anchors must span this fraction of the used range for a scale and for judging the shape
K_MAX = 0.004  # Auto never accepts |k| above 0.4 % (0.3 % would reject a true 0.3 % scale at the edge)
Q_MAX_EDGE_PX = 1.5  # Auto never accepts a quadratic moving the edges by more than this
FLAT_DEV_A = 0.5  # Δ(x) within this of a constant: "pure shift"
EXTRAP_FRAC = 0.1  # a stretch of the used range wider than this without anchors is called extrapolated
TELLURIC_TYPES = ("O", "B", "A")  # reference types whose red end is featureless enough to serve as the template

MODES = ("auto", "shift", "shift_scale", "manual")
MODEL_NAMES = {0: "shift only", 1: "shift and scale", 2: "shift, scale and curvature"}


@dataclass
class Anchor:
    kind: str
    name: str
    lam: float  # rest, Å
    x: float  # measured (px)
    err_px: float
    weight: float
    use: bool = True
    d_px: float = 0.0  # x − x_ref(λ)
    resid_px: float = 0.0
    resid_a: float = 0.0
    loo_px: float | None = None
    rejected: str = ""  # why the fit left it out ("" = used)
    stellar: bool = False
    emission: bool = False

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.name}"

    @property
    def fixed(self) -> bool:
        return self.kind in FIXED_KINDS


@dataclass
class TargetFit:
    ok: bool
    model: int = 0
    s: float = 0.0
    k: float = 0.0
    q: float = 0.0
    v: float | None = None
    errs: dict[str, float] = field(default_factory=dict)
    x0: float = 0.0
    used_range: tuple[float, float] = (0.0, 0.0)
    anchors: list[Anchor] = field(default_factory=list)
    rms_px: float = 0.0
    rms_a: float = 0.0
    v_determined: bool = False
    notes: list[tuple[str, str]] = field(default_factory=list)  # (level ok|warn|info, text)
    star_slit_offset_px: float | None = None
    sky_ref_residual_a: float | None = None
    sky_ref_n: int = 0
    solution: Solution | None = None
    delta: dict[str, Any] = field(default_factory=dict)
    mode: str = "auto"

    def d(self, x: np.ndarray | float) -> np.ndarray | float:
        u = np.asarray(x, dtype=np.float64) - self.x0
        return self.s + self.k * u + self.q * u * u

    def counts(self) -> dict[str, int]:
        out = {"sky": 0, "telluric": 0, "ism": 0, "stellar": 0}
        for a in self.anchors:
            if a.use and not a.rejected:
                out["sky" if a.kind in SKY_KINDS else "stellar" if a.stellar else a.kind] += 1
        return out


# ---- anchors -------------------------------------------------------------------------------------------------
def sky_anchors(sky: SkyResult | None, ref: Solution) -> list[Anchor]:
    if sky is None or not sky.identified:
        return []
    out = []
    for m in sky.matches:
        err = math.hypot(m.peak.err, m.peak.fwhm / (2.355 * max(m.peak.snr, 1.0)))
        out.append(Anchor(m.line.kind, m.line.label, m.line.lam_a, m.peak.x, err, m.line.weight,
                          d_px=m.peak.x - ref.pixel(m.line.lam_a)))
    return out


def telluric_anchors(ref_prof: np.ndarray, tgt_prof: np.ndarray, ref: Solution,
                     ref_sptype: str | None) -> tuple[list[Anchor], list[str]]:
    """Each band of the target cross-correlated with the same band in the reference star (its telluric bands are
    the template, placed by the reference solution)."""
    if ref_sptype and not ref_sptype.strip().upper().startswith(TELLURIC_TYPES):
        return [], [f"Telluric bands skipped: the reference star is {ref_sptype}, not an A or B star"]
    n = len(tgt_prof)
    rf, tf = flattened(ref_prof), flattened(tgt_prof)
    out, notes = [], []
    for ln in linelists.of_kind(TELLURIC):
        if ln.span is None:
            continue
        lo, hi = round(ref.pixel(ln.span[0])), round(ref.pixel(ln.span[1]))
        lo, hi = max(lo, 42), min(hi, n - 42)
        if hi - lo < 20:
            continue
        if -float(rf[lo:hi].min()) < 0.04 or -float(tf[max(0, lo - 40):hi + 40].min()) < 0.04:
            continue
        s, c = _band_shift(rf, tf, lo, hi)
        if c < 0.6 or abs(s) >= 39.5:
            notes.append(f"{ln.label}: no clean match (correlation {c:.2f})")
            continue
        out.append(Anchor(TELLURIC, ln.label, ln.lam_a, ref.pixel(ln.lam_a) + s, 0.25 / max(c, 0.3), ln.weight,
                          d_px=s))
    return out, notes


def _measured(flat: np.ndarray, ln: Line, ref: Solution, d_guess, n: int) -> tuple[float, bool, bool] | None:
    x_ref = ref.pixel(ln.lam_a)
    guess = x_ref + float(d_guess(x_ref))
    half = 10 if ln.kind == ISM else 24 if ln.kind == linelists.STELLAR_BALMER else 14
    if not half + 2 <= guess < n - half - 2:
        return None
    c, ok = measure_line(flat, guess, half=half)
    if not ok or abs(c - guess) > 10:
        return None
    return c, bool(flat[round(c)] > 0), ok


def line_anchors(tgt_prof: np.ndarray, ref: Solution, d_guess) -> list[Anchor]:
    """ISM and stellar lines measured on the target profile near where the current estimate puts them."""
    flat = flattened(tgt_prof)
    n = len(tgt_prof)
    out = []
    for ln in linelists.of_kind(ISM, *STELLAR_KINDS):
        if ln.label in ("Na D2 ISM",):  # the doublet is one feature at this resolution: Na D1 stands for both
            continue
        m = _measured(flat, ln, ref, d_guess, n)
        if m is None:
            continue
        c, emission, _ok = m
        if ln.kind == ISM and emission:
            continue  # interstellar lines are absorption
        lam = 5892.94 if ln.label == "Na D1 ISM" else ln.lam_a
        stellar = ln.kind in STELLAR_KINDS
        w = ln.weight * (EMISSION_WEIGHT if stellar and emission else 1.0)
        width_err = 0.5 if stellar else 0.4
        out.append(Anchor(ln.kind, ln.label, lam, c, width_err, w, d_px=c - ref.pixel(lam), stellar=stellar,
                          emission=emission))
    return out


# ---- fit -----------------------------------------------------------------------------------------------------
def _design(anchors: list[Anchor], ref: Solution, x0: float, model: int, v_free: bool) -> np.ndarray:
    cols = []
    for a in anchors:
        u = a.x - x0
        row = [1.0, u, u * u][: model + 1]
        if v_free:
            row.append(a.lam / (C_KMS * ref.dispersion_a(ref.pixel(a.lam))) if a.stellar else 0.0)
        cols.append(row)
    return np.asarray(cols, dtype=np.float64).reshape(len(anchors), -1)


def _solve(anchors: list[Anchor], ref: Solution, x0: float, half: float, model: int, v_free: bool,
           k_sigma: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """IRLS with a Huber loss and Gaussian priors. Returns (params, covariance, residuals px, chi²)."""
    a = _design(anchors, ref, x0, model, v_free)
    y = np.array([x.d_px for x in anchors])
    sig = np.array([math.hypot(x.err_px, SYS_PX) for x in anchors])
    base_w = np.array([x.weight for x in anchors]) / sig ** 2
    prior = [S_SIGMA_PX, k_sigma, Q_EDGE_SIGMA_PX / max(half, 1.0) ** 2][: model + 1]
    if v_free:
        prior.append(V_SIGMA_KMS)
    p_rows = np.diag(1.0 / np.asarray(prior))
    hub = np.ones(len(y))
    params = np.zeros(a.shape[1])
    for _ in range(10):
        w = base_w * hub
        aw = np.vstack([a * np.sqrt(w)[:, None], p_rows])
        yw = np.concatenate([y * np.sqrt(w), np.zeros(len(prior))])
        new, *_ = np.linalg.lstsq(aw, yw, rcond=None)
        r = (y - a @ new) / sig
        hub = np.where(np.abs(r) <= HUBER_C, 1.0, HUBER_C / np.maximum(np.abs(r), 1e-12))
        if np.allclose(new, params, atol=1e-6):
            params = new
            break
        params = new
    w = base_w * hub
    aw = np.vstack([a * np.sqrt(w)[:, None], p_rows])
    cov = np.linalg.pinv(aw.T @ aw)
    resid = y - a @ params
    chi2 = float(np.sum(base_w * resid ** 2))
    return params, cov, resid, chi2


@dataclass
class _Trial:
    model: int
    params: np.ndarray
    cov: np.ndarray
    resid: np.ndarray
    chi2: float
    bic: float
    used: list[Anchor]


def _robust_fit(anchors: list[Anchor], ref: Solution, x0: float, half: float, model: int, v_free: bool,
                k_sigma: float) -> _Trial | None:
    used = [a for a in anchors if a.use]
    for a in used:
        a.rejected = ""
    for _ in range(max(1, len(used) - model - 1)):
        live = [a for a in used if not a.rejected]
        if len(live) < model + 1:
            return None
        params, cov, resid, chi2 = _solve(live, ref, x0, half, model, v_free, k_sigma)
        sig = np.array([math.hypot(a.err_px, SYS_PX) for a in live])
        scatter = float(np.sqrt(np.mean(resid ** 2))) if len(live) > 2 else 0.0
        worst = int(np.argmax(np.abs(resid) / np.hypot(sig, scatter * 0.5)))
        bad = abs(resid[worst]) > GROSS_PX or (
            len(live) > model + 2 and abs(resid[worst]) / math.hypot(sig[worst], 0.5 * scatter) > REJECT_SIGMA
            and abs(resid[worst]) > 0.5)
        if not bad:
            n = len(live)
            return _Trial(model, params, cov, resid, chi2, chi2 + len(params) * math.log(max(n, 2)), live)
        live[worst].rejected = (f"misidentified ({resid[worst]:+.1f} px off)" if abs(resid[worst]) > GROSS_PX
                                else f"outlier ({resid[worst]:+.2f} px)")
    return None


def _spread(anchors: list[Anchor], lo: float, hi: float) -> float:
    xs = [a.x for a in anchors]
    return (max(xs) - min(xs)) / max(hi - lo, 1.0) if len(xs) > 1 else 0.0


def _uncovered(anchors: list[Anchor], lo: float, hi: float, ref: Solution) -> list[tuple[float, float]]:
    xs = sorted(a.x for a in anchors)
    gaps = []
    if not xs:
        return [(float(ref.angstrom(lo)), float(ref.angstrom(hi)))]
    if xs[0] - lo > EXTRAP_FRAC * (hi - lo):
        gaps.append((float(ref.angstrom(lo)), float(ref.angstrom(xs[0]))))
    if hi - xs[-1] > EXTRAP_FRAC * (hi - lo):
        gaps.append((float(ref.angstrom(xs[-1])), float(ref.angstrom(hi))))
    return gaps


def solution_of(ref: Solution, fit: TargetFit, n: int) -> Solution:
    """The target's polynomial: exact for a shift (and a scale); a quadratic fitted to λ_ref(x − D(x)) otherwise."""
    if fit.model == 0 and fit.k == 0 and fit.q == 0:
        return ref.shifted(fit.s)
    x = np.linspace(0, n - 1, 200)
    lam_nm = np.asarray(ref.nm(x - fit.d(x)))
    c = np.polynomial.polynomial.polyfit(x, lam_nm, 2)
    return Solution([float(v) for v in c])


def delta_stats(ref: Solution, sol: Solution, lo: float, hi: float, fit: TargetFit) -> dict[str, Any]:
    """Δ(x) = λ_target(x) − λ_ref(x) (Å) across the used range, and the verdict on its shape."""
    x = np.linspace(lo, hi, 400)
    delta = np.asarray(sol.angstrom(x)) - np.asarray(ref.angstrom(x))
    disp = ref.dispersion_a(fit.x0)
    centre = float(np.interp(fit.x0, x, delta))
    max_dev = float((delta.max() - delta.min()) / 2)
    line = np.polyval(np.polyfit(x, delta, 1), x)
    curvature = float(np.max(np.abs(delta - line)))
    shift_a = fit.s * disp
    disp_t = sol.dispersion_a(fit.x0)
    scale_pct = (disp_t / disp - 1.0) * 100.0
    lam_lo, lam_hi = float(ref.angstrom(lo)), float(ref.angstrom(hi))
    live = [a for a in fit.anchors if a.use and not a.rejected]
    gaps = _uncovered(live, lo, hi, ref)
    if _spread(live, lo, hi) < SPREAD_FRAC:
        verdict = "not_enough"
        where = "; ".join(f"{a:.0f}–{b:.0f} Å" for a, b in gaps)
        text = "Not enough anchors to judge the shape" + (f" (no anchors in {where})" if where else "")
    elif fit.model == 2:
        verdict, text = "shape", f"Shape differs: curvature of {curvature:.1f} Å"
    elif max_dev < FLAT_DEV_A:
        verdict = "pure_shift"
        text = (f"Pure shift: {shift_a:+.1f} Å, flat within {max(max_dev, 0.05):.1f} Å across "
                f"{lam_lo:.0f} to {lam_hi:.0f} Å")
    else:
        verdict = "shift_scale"
        text = f"Shift plus scale: {shift_a:+.1f} Å at the centre, changing by {abs(delta[-1] - delta[0]):.1f} Å " \
               "across the range"
    return {"x": x.astype(np.float32), "lam": np.asarray(ref.angstrom(x), dtype=np.float32),
            "delta": delta.astype(np.float32), "center_a": centre, "center_px": fit.s, "shift_a": shift_a,
            "span_a": float(delta[-1] - delta[0]), "max_dev_a": max_dev, "curvature_a": curvature,
            "scale_pct": scale_pct, "verdict": verdict, "text": text, "gaps": gaps,
            "range_a": (lam_lo, lam_hi)}


def _first_guess(fixed: list[Anchor]):
    """D(x) to look for the ISM and stellar lines: a line through the fixed anchors when they are spread, else
    their median, else zero."""
    if not fixed:
        return lambda x: 0.0
    xs, ds = np.array([a.x for a in fixed]), np.array([a.d_px for a in fixed])
    med = float(np.median(ds))
    if len(fixed) >= 3 and np.ptp(xs) > 500:
        k, c = np.polyfit(xs, ds, 1)
        if abs(k) <= 0.03:
            return lambda x: float(c + k * x)
    return lambda x: med


def fit_target(ref: Solution, ref_prof: np.ndarray, tgt_prof: np.ndarray, *, tgt_sky: SkyResult | None = None,
               ref_sky: SkyResult | None = None, ref_sptype: str | None = None, mode: str = "auto",
               disabled: set[str] | frozenset[str] = frozenset(), used_range: tuple[float, float] | None = None,
               ) -> TargetFit:
    """Measure the anchors and fit the correction. `disabled`: anchor keys the user unticked."""
    n = len(tgt_prof)
    lo, hi = used_range or (0.0, float(n - 1))
    lo, hi = max(0.0, lo), min(float(n - 1), hi)
    x0, half = (lo + hi) / 2, (hi - lo) / 2
    notes: list[tuple[str, str]] = []
    anchors = sky_anchors(tgt_sky, ref)
    tell, tnotes = telluric_anchors(ref_prof, tgt_prof, ref, ref_sptype)
    anchors += tell
    notes += [("info", t) for t in tnotes]
    anchors += line_anchors(tgt_prof, ref, _first_guess(anchors))
    for a in anchors:
        a.use = a.key not in disabled
    fit = TargetFit(False, x0=x0, used_range=(lo, hi), anchors=anchors, notes=notes, mode=mode)
    if tgt_sky is not None and not tgt_sky.peaks:
        notes.append(("info", "No sky lines found, clean sky"))
    elif tgt_sky is not None and not tgt_sky.identified:
        notes.append(("warn", f"Sky lines not identified: {tgt_sky.reason}"))
    # the reference checked by its own sky lines (independent of its Balmer fit)
    if ref_sky is not None and ref_sky.identified:
        r = [float(ref.angstrom(m.peak.x)) - m.line.lam_a for m in ref_sky.matches if not m.line.blend]
        if r:
            fit.sky_ref_residual_a = float(np.sqrt(np.mean(np.square(r))))
            fit.sky_ref_n = len(r)
    live = [a for a in anchors if a.use]
    fixed = [a for a in live if a.fixed]
    v_free = len(fixed) >= MIN_FIXED_ANCHORS and _spread(fixed, lo, hi) * (hi - lo) >= MIN_SPAN_PX \
        and any(a.stellar for a in live)
    if not live:
        notes.append(("warn", "No anchors measured: the reference solution is used unchanged"))
        return fit
    if not fixed:
        notes.append(("warn", "Velocity not determined: only stellar lines, so the shift includes the star's "
                              "velocity (no sky line, telluric band or interstellar line found)"))
    elif not v_free and any(a.stellar for a in live):
        notes.append(("warn", f"Velocity not determined: fewer than {MIN_FIXED_ANCHORS} fixed anchors spanning "
                              f"{MIN_SPAN_PX:.0f} px"))
    # with stellar lines only (or no velocity), no scale: stellar lines cannot separate the terms
    max_model = 0 if not fixed else 2
    if mode == "shift" or (not v_free and not fixed):
        models = [0]
    elif mode == "shift_scale":
        models = [1]
    else:
        models = [m for m in (0, 1, 2) if m <= max_model]
    trials: dict[int, _Trial] = {}
    for m in models:
        t = _robust_fit(anchors, ref, x0, half, m, v_free, K_SIGMA_FORCED if mode == "shift_scale" else K_SIGMA)
        if t is not None:
            trials[m] = t
    if not trials:
        notes.append(("warn", "The fit failed: the reference solution is used unchanged"))
        return fit
    chosen = min(trials)
    if mode == "auto" and chosen != 0:  # Auto never starts above a shift
        notes.append(("warn", "The shift-only fit failed: the reference solution is used unchanged"))
        return fit
    if mode == "auto":
        for m, need in ((1, M1_MIN_ANCHORS), (2, M2_MIN_ANCHORS)):
            t = trials.get(m)
            if t is None or m != chosen + 1:
                continue
            k = float(t.params[1])
            q_edge = abs(float(t.params[2])) * half ** 2 if m == 2 else 0.0
            reason = ""
            if len(t.used) < need:
                reason = f"{len(t.used)} anchors ({need} needed)"
            elif _spread(t.used, lo, hi) < SPREAD_FRAC:
                reason = f"anchors span {_spread(t.used, lo, hi):.0%} of the range ({SPREAD_FRAC:.0%} needed)"
            elif trials[chosen].bic - t.bic < DBIC_MIN:
                if m == 1 and abs(k) > 0.0005:
                    reason = f"not clearly better (ΔBIC {trials[chosen].bic - t.bic:.1f} < {DBIC_MIN:.0f})"
                else:
                    break
            elif abs(k) > K_MAX:
                reason = f"|scale| {abs(k) * 100:.2f} % above the {K_MAX * 100:.1f} % limit"
            elif q_edge > Q_MAX_EDGE_PX:
                reason = f"curvature {q_edge:.1f} px at the edges above the {Q_MAX_EDGE_PX:.1f} px limit"
            if reason:
                notes.append(("warn", f"{'Scale' if m == 1 else 'Curvature'} term not accepted: {reason}"))
                break
            chosen = m
    t = _robust_fit(anchors, ref, x0, half, chosen, v_free, K_SIGMA_FORCED if mode == "shift_scale" else K_SIGMA)
    assert t is not None
    p = list(t.params)
    fit.model = chosen
    fit.s = float(p[0])
    fit.k = float(p[1]) if chosen >= 1 else 0.0
    fit.q = float(p[2]) if chosen >= 2 else 0.0
    errs = np.sqrt(np.clip(np.diag(t.cov), 0, None))
    fit.errs = {"s": float(errs[0])}
    if chosen >= 1:
        fit.errs["k"] = float(errs[1])
    if chosen >= 2:
        fit.errs["q"] = float(errs[2])
    if v_free:
        fit.v, fit.errs["v"], fit.v_determined = float(p[-1]), float(errs[-1]), True
    # residuals for every anchor (also the unticked and rejected ones), leave-one-out for the used ones
    for a in anchors:
        model_d = float(fit.d(a.x))
        if a.stellar and fit.v is not None:
            model_d += a.lam * fit.v / (C_KMS * ref.dispersion_a(ref.pixel(a.lam)))
        a.resid_px = a.d_px - model_d
        a.resid_a = a.resid_px * ref.dispersion_a(a.x)
    for a in t.used:
        others = [b for b in t.used if b is not a]
        if len(others) < chosen + 2:
            continue
        sub = _solve(others, ref, x0, half, chosen, v_free and any(b.stellar for b in others),
                     K_SIGMA_FORCED if mode == "shift_scale" else K_SIGMA)[0]
        u = a.x - x0
        pred = sub[0] + (sub[1] * u if chosen >= 1 else 0) + (sub[2] * u * u if chosen >= 2 else 0)
        if a.stellar and v_free and any(b.stellar for b in others):
            pred += a.lam * sub[-1] / (C_KMS * ref.dispersion_a(ref.pixel(a.lam)))
        a.loo_px = float(a.d_px - pred)
    used = t.used
    fit.rms_px = float(np.sqrt(np.mean([a.resid_px ** 2 for a in used])))
    fit.rms_a = float(np.sqrt(np.mean([a.resid_a ** 2 for a in used])))
    sky_d = [a.d_px for a in used if a.kind in SKY_KINDS]
    tel_d = [a.d_px for a in used if a.kind == TELLURIC]
    if sky_d and tel_d:
        fit.star_slit_offset_px = float(np.mean(tel_d) - np.mean(sky_d))
    gaps = _uncovered(used, lo, hi, ref)
    if gaps:
        notes.append(("warn", "Extrapolated beyond the anchors: " + "; ".join(f"{a:.0f}–{b:.0f} Å" for a, b in gaps)))
    fit.ok = True
    fit.solution = solution_of(ref, fit, n)
    fit.delta = delta_stats(ref, fit.solution, lo, hi, fit)
    return fit


def summary(fit: TargetFit, ref: Solution) -> str:
    if not fit.ok:
        return "Not fitted: the reference solution is used"
    disp = ref.dispersion_a(fit.x0)
    parts = [f"Shift {fit.s:+.1f} px ({fit.s * disp:+.1f} Å)"]
    if fit.model >= 1:
        parts.append(f"scale {fit.delta.get('scale_pct', 0):+.2f} % ± {fit.errs.get('k', 0) * 100:.2f}")
    parts += [f"RMS {fit.rms_a:.2f} Å", f"model: {MODEL_NAMES[fit.model]}"]
    if fit.v_determined and fit.v is not None:
        parts.append(f"v {fit.v:+.0f} ± {fit.errs.get('v', 0):.0f} km/s")
    else:
        parts.append("v not determined")
    return " · ".join(parts)


def chips(fit: TargetFit, sky: SkyResult | None) -> list[tuple[str, str]]:
    """(level, text) validation chips under the summary."""
    out: list[tuple[str, str]] = []
    if sky is not None and not sky.peaks:
        out.append(("info", "No sky lines found, clean sky"))
    sky_used = [a for a in fit.anchors if a.kind in SKY_KINDS and a.use and not a.rejected
                and not linelists.BY_LABEL[a.name].blend]
    if sky_used:
        worst = max(abs(a.resid_a) for a in sky_used)
        out.append(("ok" if worst < 0.5 else "warn", f"Sky lines agree within {worst:.1f} Å ({len(sky_used)} lines)"))
    tel = [a for a in fit.anchors if a.kind == TELLURIC and a.use and not a.rejected]
    if tel:
        worst = max(abs(a.resid_a) for a in tel)
        out.append(("ok" if worst < 1.0 else "warn", "Telluric bands agree" if worst < 1.0
                    else f"Telluric bands off by up to {worst:.1f} Å"))
    if fit.star_slit_offset_px is not None:
        out.append(("info", f"Star-in-slit offset about {fit.star_slit_offset_px:+.1f} px"))
    if fit.sky_ref_residual_a is not None:
        out.append(("ok" if fit.sky_ref_residual_a < 0.5 else "warn",
                    f"Reference solution vs its own sky lines: RMS {fit.sky_ref_residual_a:.2f} Å "
                    f"({fit.sky_ref_n} lines)"))
    return out


def metrics(fit: TargetFit, sky: SkyResult | None) -> dict[str, Any]:
    """The numbers for logbook steps.wavelength.metrics."""
    d = fit.delta
    return {"shift_px": fit.s, "scale": fit.k, "quad": fit.q, "model": MODEL_NAMES[fit.model] if fit.ok else "none",
            "rms_A": fit.rms_a, "n_anchors": fit.counts(), "v_kms": fit.v if fit.v_determined else None,
            "sky_lines_found": len(sky.peaks) if sky is not None else 0,
            "sky_ref_residual_A": fit.sky_ref_residual_a, "star_slit_offset_px": fit.star_slit_offset_px,
            "delta_center_A": d.get("center_a"), "delta_span_A": d.get("span_a"),
            "delta_max_dev_A": d.get("max_dev_a"), "scale_pct": d.get("scale_pct"), "verdict": d.get("verdict")}


def history(fit: TargetFit, ref: Solution) -> list[str]:
    """One-line HISTORY entries for the exported header (no SX* keywords)."""
    if not fit.ok:
        return []
    c = fit.counts()
    d = fit.delta
    disp = ref.dispersion_a(fit.x0)
    out = [f"target wavelength: reference solution + correction, model {MODEL_NAMES[fit.model]}, shift "
           f"{fit.s:+.2f} px ({fit.s * disp:+.2f} A), scale {fit.k * 100:+.3f} %, quad {fit.q:.3g}, RMS "
           f"{fit.rms_a:.3f} A",
           f"target wavelength anchors: {c['sky']} sky, {c['telluric']} telluric, {c['ism']} ISM, "
           f"{c['stellar']} stellar" + (f"; v {fit.v:+.0f} km/s" if fit.v_determined and fit.v is not None else "")]
    if d:
        if d["verdict"] == "pure_shift":
            out.append(f"target solution = reference + shift {d['shift_a']:+.1f} A, flat within "
                       f"{max(d['max_dev_a'], 0.05):.1f} A")
        else:
            out.append(f"target solution = reference + shift {d['shift_a']:+.1f} A, Delta span {d['span_a']:+.1f} A,"
                       f" max deviation {d['max_dev_a']:.1f} A ({d['verdict']})")
    return out


def to_data(fit: TargetFit, ref: Solution, sky: SkyResult | None, ref_sky: SkyResult | None) -> dict[str, Any]:
    """Plain data for the screen and the logbook."""
    disp = ref.dispersion_a(fit.x0)
    sky_plot = None
    if sky is not None and sky.sky is not None:
        sky_plot = {"flux": sky.sky, "noise": sky.noise,
                    "peaks": [(p.x, p.amp, p.fwhm) for p in sky.peaks],
                    "matches": [(m.line.label, m.line.lam_a, m.peak.x) for m in sky.matches],
                    "identified": sky.identified, "reason": sky.reason, "offset": sky.offset_px}
    return {"ok": fit.ok, "mode": fit.mode, "model": fit.model, "model_name": MODEL_NAMES[fit.model],
            "s": fit.s, "s_a": fit.s * disp, "k": fit.k, "q": fit.q, "v": fit.v, "v_determined": fit.v_determined,
            "errs": fit.errs, "rms_px": fit.rms_px, "rms_a": fit.rms_a, "x0": fit.x0,
            "summary": summary(fit, ref), "notes": fit.notes, "chips": chips(fit, sky),
            "anchors": [{"key": a.key, "kind": a.kind, "name": a.name, "lam": a.lam, "x": a.x, "err": a.err_px,
                         "weight": a.weight, "use": a.use, "resid_px": a.resid_px, "resid_a": a.resid_a,
                         "loo_px": a.loo_px, "rejected": a.rejected, "stellar": a.stellar, "emission": a.emission,
                         "d_px": a.d_px} for a in fit.anchors],
            "delta": fit.delta, "sky": sky_plot, "sky_text": sky.text() if sky is not None else "",
            "ref_sky_text": ref_sky.text() if ref_sky is not None else "",
            "sky_ref_residual_a": fit.sky_ref_residual_a, "sky_ref_n": fit.sky_ref_n,
            "star_slit_offset_px": fit.star_slit_offset_px, "metrics": metrics(fit, sky),
            "coeffs": list(fit.solution.coeffs_nm) if fit.solution else None}
