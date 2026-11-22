"""Performance harness: times every stage on 3840×2160 frames against docs/SPEC.md §8.

    python tools/perf.py            # all benches, exit 1 if any budget is exceeded by > 50 %
    python tools/perf.py --only app # benches whose name contains "app"

Benches not built yet are listed as "not built". Synthetic frames are cached in %TEMP%.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

TOLERANCE = 1.5  # fail when a measurement exceeds its budget by more than 50 %
WORK = Path(tempfile.gettempdir()) / "starex_reduce_perf"


@dataclass
class Bench:
    name: str
    budget_s: float
    fn: Callable[[], float] | None  # returns seconds; None = not built yet
    repeat: int = 3


def best_of(fn: Callable[[], None], repeat: int) -> float:
    times = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t)
    return min(times)


# ---- benches ---------------------------------------------------------------------------------
OFFSCREEN = False


def bench_app_start() -> float:
    """In-process time until the rail is painted on a real window (Select fills in just after)."""
    env = {**os.environ, "STAREX_REDUCE_HOME": str(WORK / "home")}
    if OFFSCREEN:
        env["QT_QPA_PLATFORM"] = "offscreen"
    best, ready = float("inf"), float("inf")
    for _ in range(3):
        out = subprocess.run([sys.executable, "-m", "starex_reduce.app", "--startup-time"],
                             capture_output=True, text=True, env=env, cwd=ROOT, timeout=60)
        vals = dict(line.split("=") for line in out.stdout.split() if "=" in line)
        if "shown_s" not in vals:
            raise RuntimeError(out.stderr[-2000:])
        best = min(best, float(vals["shown_s"]))
        ready = min(ready, float(vals.get("startup_s", "nan")))
    print(f"  (Select screen ready at {ready:.3f} s)")
    return best


def bench_session_open() -> float:
    from starex_reduce.core.session import Session

    folder = WORK / "session"
    s = Session.create(folder)
    for side in ("target", "reference"):
        s.inputs[side]["lights"] = [
            {"path": str(folder / f"{side}_{i:05d}.fits"), "used": True} for i in range(15)]
    for sid in s.status:
        s.mark_done(sid, {}, "bench")
    s.save()
    return best_of(lambda: Session.load(folder), 5)


def synthetic_night() -> dict[str, list[Path]]:
    """Full-size synthetic night (15 lights + 15 ref, 11 darks, 11 bias), written once to %TEMP%."""
    import synth

    root = WORK / "night_3840"
    marker = root / "done.txt"
    if not marker.exists():
        print("  (writing synthetic 3840x2160 frames once, ~1 min)", flush=True)
        synth.write_set(root, "nova", n_target=15, n_ref=15, n_cal=11)
        marker.write_text("ok")
    return {k: sorted((root / k).glob("*.fits")) for k in ("target", "reference", "darks", "bias")}


def bench_scan() -> float:
    from starex_reduce.core.frames import scan

    night = synthetic_night()
    folder = night["target"][0].parent  # 15 frames; scan both light folders = 30 headers
    other = night["reference"][0].parent
    return best_of(lambda: (scan(folder), scan(other)), 3)


def _library(fresh: bool) -> tuple[object, dict[str, list[Path]]]:
    from starex_reduce.core.frames import BIAS, DARK, scan
    from starex_reduce.core.jobs import Ctx
    from starex_reduce.core.masters import MasterLibrary, build_master

    night = synthetic_night()
    folder = WORK / "masters"
    if fresh and folder.exists():
        import shutil
        shutil.rmtree(folder)
    lib = MasterLibrary(folder)
    if not lib.masters:
        build_master(scan(night["bias"][0].parent), BIAS, lib, Ctx(), method="sigma_clip_mean")
        build_master(scan(night["darks"][0].parent), DARK, lib, Ctx(), method="sigma_clip_mean")
    return lib, night


def bench_master() -> float:
    from starex_reduce.core.frames import DARK, scan
    from starex_reduce.core.jobs import Ctx
    from starex_reduce.core.masters import MasterLibrary, build_master

    night = synthetic_night()
    darks = scan(night["darks"][0].parent)
    lib = MasterLibrary(WORK / "masters_bench")
    return best_of(lambda: build_master(darks, DARK, lib, Ctx(), method="sigma_clip_mean"), 2)


def bench_stack() -> float:
    from starex_reduce.core.frames import scan
    from starex_reduce.core.jobs import Ctx
    from starex_reduce.core.masters import LightSettings, calibration_frame, pick_calibration
    from starex_reduce.core.stack import StackParams, run_stack

    lib, night = _library(fresh=False)
    lights = scan(night["target"][0].parent)

    def run() -> None:
        plan = pick_calibration(LightSettings.from_frames(lights), lib)  # type: ignore[arg-type]
        run_stack(Ctx(), lights, calibration_frame(plan, lib), StackParams(),  # type: ignore[arg-type]
                  WORK / "Target_S.fit", plan.describe())
    return best_of(run, 2)


def real_nova_report() -> None:
    """Informational: the real BUILD_PLAN M1 numbers on example_spectra/novasrg2026."""
    from starex_reduce.core.frames import BIAS, DARK, scan
    from starex_reduce.core.jobs import Ctx
    from starex_reduce.core.masters import (
        LightSettings,
        MasterLibrary,
        build_master,
        calibration_frame,
        pick_calibration,
    )
    from starex_reduce.core.stack import StackParams, run_stack

    base = ROOT / "example_spectra" / "novasrg2026"
    if not base.exists():
        return
    import shutil
    folder = WORK / "masters_nova"
    shutil.rmtree(folder, ignore_errors=True)
    lib = MasterLibrary(folder)
    print()
    print("Real data (example_spectra/novasrg2026):")
    for kind, sub in ((BIAS, "bias"), (DARK, "darks")):
        frames = scan(base / sub)
        t = time.perf_counter()
        build_master(frames, kind, lib, Ctx(), method="sigma_clip_mean")
        print(f"  master {kind} from {len(frames)} frames (sigma clip): {time.perf_counter() - t:.2f} s")
    for name, sub in (("nova 6 x 300 s", "SgrNov32026"), ("HD 163955 10 x 30 s", "hd163955")):
        lights = scan(base / sub)
        plan = pick_calibration(LightSettings.from_frames(lights), lib)
        t = time.perf_counter()
        res = run_stack(Ctx(), lights, calibration_frame(plan, lib), StackParams(), WORK / f"{sub}_S.fit",
                        plan.describe())
        print(f"  stack {name} ({plan.mode}, gain x{plan.gain_scale:.3f}): {time.perf_counter() - t:.2f} s,"
              f" background {res.background:.0f} ADU, spectrum row {res.peak_row}")


def _full_frame() -> tuple[object, object]:
    import numpy as np
    import synth

    p = synth.PRESETS["nova"]
    return synth.make_light(p, "target").astype(np.float32), synth.make_dark(p).astype(np.float32) - 2064


def bench_tilt() -> float:
    from starex_reduce.core.geometry import detect_tilt

    img, _ = _full_frame()
    return best_of(lambda: detect_tilt(img), 3)  # type: ignore[arg-type]


def bench_geometry() -> float:
    from starex_reduce.core.geometry import GeometryParams, correct
    from starex_reduce.core.jobs import Ctx

    img, dark = _full_frame()
    return best_of(lambda: correct(img, GeometryParams(), Ctx(), dark), 3)  # type: ignore[arg-type]


def bench_geometry_slant() -> float:
    from starex_reduce.core.geometry import GeometryParams, correct
    from starex_reduce.core.jobs import Ctx

    img, dark = _full_frame()
    return best_of(lambda: correct(img, GeometryParams(slant_deg=-1.45), Ctx(), dark), 3)  # type: ignore[arg-type]


def bench_slant_detect() -> float:
    from starex_reduce.core.geometry import detect_slant, detect_tilt, straighten

    img, _ = _full_frame()
    t = detect_tilt(img)
    st = straighten(img, t.angle_deg, 0.0, (t.row, t.x_centre))
    return best_of(lambda: detect_slant(st, t.row, t.lit), 3)


VIEW_REPORT: dict[str, float] = {}


def bench_view_fps() -> float:
    """Seconds per frame (worst of Y zoom, wheel, strip drag, Original/Corrected) on a real window."""
    import numpy as np
    from PySide6.QtWidgets import QApplication

    from starex_reduce.core.display import prepare
    from starex_reduce.core.geometry import GeometryParams, correct
    from starex_reduce.core.jobs import Ctx
    from starex_reduce.ui import theme
    from starex_reduce.ui.widgets.image_viewer import ImageViewer

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    img, _ = _full_frame()
    orig = prepare(img)  # type: ignore[arg-type]
    corr = prepare(correct(img, GeometryParams(), Ctx()).image, orig.levels)  # type: ignore[arg-type]
    v = ImageViewer()
    v.resize(900, 320)
    v.show()
    v.set_images(orig, corr)
    for _ in range(10):
        app.processEvents()
    rng = np.random.default_rng(0)

    def frames(step) -> float:
        n, t = 120, time.perf_counter()
        for i in range(n):
            step(i)
            v.plot.viewport().repaint()
            if v.strip is not None:
                v.strip.repaint()
            app.processEvents()
        return (time.perf_counter() - t) / n

    VIEW_REPORT["Y zoom (chips/rows)"] = frames(lambda i: v.set_rows(float(rng.uniform(0, 1800)), 0) or
                                                v.set_zoom([2, 4, 8, 16, "fit", "full"][i % 6]))
    VIEW_REPORT["wheel zoom"] = frames(lambda i: v.zoom_by(1.1 if (i // 20) % 2 == 0 else 1 / 1.1))
    VIEW_REPORT["strip drag"] = frames(lambda i: v.centre_on(200 + 15 * i))
    VIEW_REPORT["Original/Corrected"] = frames(lambda i: v.show_corrected(i % 2 == 0))
    v.close()
    for k, val in VIEW_REPORT.items():
        print(f"  ({k}: {val * 1000:.1f} ms/frame = {1 / val:.0f} fps)")
    return max(VIEW_REPORT.values())


def _straight() -> object:
    from starex_reduce.core.geometry import GeometryParams, correct
    from starex_reduce.core.jobs import Ctx

    img, _ = _full_frame()
    return correct(img, GeometryParams(), Ctx()).image  # type: ignore[arg-type]


def bench_extract() -> float:
    from starex_reduce.core.extract import auto_place, extract

    img = _straight()
    bands = auto_place(img).bands  # type: ignore[arg-type]
    return best_of(lambda: extract(img, bands), 5)  # type: ignore[arg-type]


def bench_band_drag() -> float:
    """Seconds per frame while dragging a band edge: overlay move + live profile + plot redraw."""
    import pyqtgraph as pg
    from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

    from starex_reduce.core.display import prepare
    from starex_reduce.core.extract import auto_place, extract
    from starex_reduce.ui import theme
    from starex_reduce.ui.widgets.bands import BandOverlay
    from starex_reduce.ui.widgets.image_viewer import ImageViewer

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    img = _straight()
    d = prepare(img)  # type: ignore[arg-type]
    w = QWidget()
    lay = QVBoxLayout(w)
    v = ImageViewer()
    plot = pg.PlotWidget()
    curve = plot.plot(pen=theme.TEAL)
    curve.setClipToView(True)
    curve.setDownsampling(auto=True, method="peak")
    lay.addWidget(v, 2)
    lay.addWidget(plot, 1)
    w.resize(900, 520)
    w.show()
    v.set_images(d, None)
    ov = BandOverlay(v.vb)
    bands = auto_place(img).bands  # type: ignore[arg-type]
    ov.set_bands(bands, img.shape[1])  # type: ignore[union-attr]
    ov.moving.connect(lambda b: curve.setData(extract(img, b).profile))  # type: ignore[arg-type]
    v.set_rows(bands.sky_a.top - 30, bands.sky_b.bottom + 30)
    for _ in range(10):
        app.processEvents()
    region = ov.regions["spectrum"]
    lo, _hi = region.getRegion()
    n, t = 120, time.perf_counter()
    for i in range(n):
        region.lines[0].setValue(lo + (i % 12) - 6)  # what a mouse drag on the top edge does
        v.plot.viewport().repaint()
        plot.viewport().repaint()
        app.processEvents()
    dt = (time.perf_counter() - t) / n
    w.close()
    print(f"  (band drag: {dt * 1000:.1f} ms/frame = {1 / dt:.0f} fps)")
    return dt


def bench_wavelength() -> float:
    """Free Balmer fit on a reference profile + resampling target and reference."""
    import numpy as np

    from starex_reduce.core import wavelength as w
    from starex_reduce.core.extract import auto_place, extract
    from starex_reduce.core.instrument import SEED_DISPERSION

    img = _straight()
    prof = extract(img, auto_place(img).bands).profile.astype(np.float64)  # type: ignore[arg-type]
    saved = w.Solution(list(SEED_DISPERSION["coeffs_nm"]))

    def run() -> None:
        cal = w.calibrate(prof, saved, "free", emission=True)
        crval, cdelt, n = w.linear_grid(cal.solution, 3900.0, 8000.0)
        for _ in range(2):
            w.resample(prof, cal.solution, crval, cdelt, n)
    return best_of(run, 3)


def bench_response() -> float:
    """Response from an A0V reference + apply to the target + normalise and crop."""
    import numpy as np

    from starex_reduce.core import response as r

    lam = 3900 + 1.2056 * np.arange(3401)
    ll, lf = r.load_template("a0v")
    resp = np.exp(-0.5 * ((lam - 6200) / 1500) ** 2)
    ref = np.interp(lam, ll, lf) * resp * 8000
    tgt = resp * 3000
    t = r.pick_template("A0")[0]

    def run() -> None:
        res = r.build_response(lam, ref, t, 6.0)
        corr = tgt / res.smooth * r.extinction_factor(lam, 1.4, 1.2)
        r.normalise_crop(lam, corr, (5500, 5600), (3900, 7700))
    return best_of(run, 3)


def bench_sub() -> float:
    """Acquisition: analyse one 3840x2160 sub with locked rows (strip read through the memmap)."""
    from starex_reduce.core.subs import analyse_sub, detect_rows

    real = sorted((ROOT / "example_spectra" / "novasrg2026" / "SgrNov32026").glob("*.fits"))
    path = real[0] if real else synthetic_night()["target"][0]
    rows = detect_rows(path)
    analyse_sub(path, rows)  # warm the file cache like a just-written frame
    return best_of(lambda: analyse_sub(path, rows), 5)


def bench_run_all() -> float:
    """Whole pipeline on the real nova night with masters already in the library (synthetic if absent)."""
    import shutil
    import tempfile

    import numpy as np

    from starex_reduce.core import geometry as g
    from starex_reduce.core import response as r
    from starex_reduce.core import wavelength as w
    from starex_reduce.core.extract import auto_place, extract
    from starex_reduce.core.frames import BIAS, DARK, scan
    from starex_reduce.core.instrument import SEED_DISPERSION
    from starex_reduce.core.jobs import Ctx
    from starex_reduce.core.masters import (
        LightSettings,
        MasterLibrary,
        build_master,
        calibration_frame,
        group_for_masters,
        pick_calibration,
    )
    from starex_reduce.core.stack import StackParams, stack

    base = ROOT / "example_spectra" / "novasrg2026"
    if base.exists():
        sets = {"Target": base / "SgrNov32026", "Reference": base / "hd163955"}
        cal_dirs = {BIAS: base / "bias", DARK: base / "darks"}
    else:
        night = synthetic_night()
        sets = {"Target": night["target"][0].parent, "Reference": night["reference"][0].parent}
        cal_dirs = {BIAS: night["bias"][0].parent, DARK: night["darks"][0].parent}
    folder = Path(tempfile.mkdtemp())
    lib = MasterLibrary(folder / "masters")
    for kind, d in cal_dirs.items():  # cached masters: built before the clock starts
        build_master(group_for_masters(scan(d))[0], kind, lib, Ctx())

    def run() -> None:
        profiles = {}
        for img, d in sets.items():
            lights = scan(d)
            plan = pick_calibration(LightSettings.from_frames(lights), lib)
            data, _ = stack(lights, calibration_frame(plan, lib), StackParams(), Ctx())
            straight = g.correct(data, g.GeometryParams(), Ctx()).image
            profiles[img] = extract(straight, auto_place(straight).bands).profile.astype(np.float64)
        cal = w.calibrate(profiles["Reference"], w.Solution(list(SEED_DISPERSION["coeffs_nm"])), "refine")
        crval, cdelt, n = w.linear_grid(cal.solution, 3900.0, 8000.0)
        lam = crval + cdelt * np.arange(n)
        ref = w.resample(profiles["Reference"], cal.solution, crval, cdelt, n).astype(np.float64)
        tgt = w.resample(profiles["Target"], cal.solution, crval, cdelt, n).astype(np.float64)
        res = r.build_response(lam, ref, r.pick_template("A0")[0], cal.fwhm_a)
        r.normalise_crop(lam, tgt / res.smooth, (5500, 5600), (3900, 7700))
    try:
        return best_of(run, 1)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


BENCHES: list[Bench] = [
    Bench("App start (window shown)", 1.0, bench_app_start, 1),
    Bench("Open a session (restore state, show rail)", 0.5, bench_session_open),
    Bench("Scan a folder of 30 frames (headers only)", 0.5, bench_scan),
    Bench("Build a master from 11 frames (sigma clip)", 3.0, bench_master),
    Bench("Stack 15 lights with masters", 4.0, bench_stack),
    Bench("Tilt detect", 0.5, bench_tilt),
    Bench("Apply tilt/smile/hot pixels to one full frame", 1.0, bench_geometry),
    Bench("Same with slant (tilt + slant, one resample)", 1.0, bench_geometry_slant),
    Bench("Detect slant (sky-row blocks)", 1.0, bench_slant_detect),
    Bench("Y-zoom, pan, wheel, Original/Corrected (s/frame)", 1 / 60, bench_view_fps, 1),
    Bench("1D extraction", 0.1, bench_extract),
    Bench("Band drag, line-marker drag, X-zoom on plots (s/frame)", 1 / 60, bench_band_drag, 1),
    Bench("Balmer fit + resample", 0.3, bench_wavelength),
    Bench("Response build + apply", 0.3, bench_response),
    Bench("Run all (cached masters)", 10.0, bench_run_all, 1),
    Bench("Acquisition: analyse one sub (3840x2160)", 0.1, bench_sub),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--offscreen", action="store_true", help="no window flash during the app-start bench")
    a = ap.parse_args()
    global OFFSCREEN
    OFFSCREEN = a.offscreen
    WORK.mkdir(parents=True, exist_ok=True)
    failed = False
    print(f"{'Action':<50} {'Budget':>8} {'Measured':>9}  Result")
    print("-" * 80)
    for b in BENCHES:
        if a.only.lower() not in b.name.lower():
            continue
        if b.fn is None:
            print(f"{b.name:<50} {b.budget_s:>7.2f}s {'-':>9}  not built")
            continue
        try:
            t = b.fn()
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"{b.name:<50} {b.budget_s:>7.2f}s {'error':>9}  {exc}")
            failed = True
            continue
        ratio = t / b.budget_s
        verdict = "ok" if ratio <= 1 else ("over budget" if ratio <= TOLERANCE else "FAIL >50 %")
        failed |= ratio > TOLERANCE
        print(f"{b.name:<50} {b.budget_s:>7.2f}s {t:>8.3f}s  {verdict} ({ratio:.0%})")
    if not a.only:
        real_nova_report()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
