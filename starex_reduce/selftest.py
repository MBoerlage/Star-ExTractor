"""`--selftest`: the core without the GUI. Loads every bundled data file, writes the small synthetic example
night, reduces it end to end (masters, stack, geometry, extract, wavelength, response), writes an AVSpec file
and validates it. Everything happens in a temporary folder with its own settings folder, so nothing of the
user's is read or written. Returns 0 when every check passes."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

IMAGES = ["Target", "Reference"]


class Report:
    def __init__(self, out=None) -> None:
        self.out = out or sys.stdout
        self.failed = 0

    def check(self, name: str, fn: Callable[[], Any]) -> Any:
        t0 = time.perf_counter()
        try:
            res = fn()
        except Exception as exc:  # noqa: BLE001 - every failure is reported, the run goes on where it can
            self.failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}", file=self.out, flush=True)
            return None
        extra = f" ({res})" if isinstance(res, str) and res else ""
        print(f"ok    {name}{extra}  [{time.perf_counter() - t0:.2f} s]", file=self.out, flush=True)
        return res


def bundled_data() -> str:
    from .core import camera, response
    from .core.reference_stars import _load_catalog
    from .ui import about, brand

    n = len(response.list_templates())
    assert n >= 100, f"only {n} Pickles templates"
    lam, flux = response.load_template("a0v")
    assert len(lam) > 1000 and flux.max() > 0
    assert response.telluric_bands() and len(response.extinction()[0]) > 50
    stars = _load_catalog()
    assert len(stars) > 100
    assert camera.table()
    for p in (brand.LOGO, brand.LOGO_SMALL, brand.LOGO_ICON, brand.LOGO_ICON_SMALL, brand.ICO, about.SPLASH):
        assert p.exists(), f"missing {p.name}"
    import astropy_iers_data  # noqa: F401 - bundled so astropy never needs to download at first run

    return f"{n} templates, {len(stars)} reference stars, logo, icon, splash"


def _mark(session, run) -> None:
    for line in run.log:
        session.log(line)
    for img, out in run.outputs.items():
        session.mark_image_done(run.stage, img, out, run.summaries.get(img, ""))


def reduce_example(work: Path, rep: Report) -> Path | None:
    from .core.extract_stage import extract_job
    from .core.extract_stage import specs_for as extract_specs
    from .core.files_stage import build_masters_job, scan_lights, specs_for, stack_job, tolerances
    from .core.frames import BIAS, DARK, scan
    from .core.geometry_stage import geometry_job
    from .core.geometry_stage import specs_for as geometry_specs
    from .core.instrument import InstrumentProfile
    from .core.jobs import Ctx
    from .core.masters import MasterLibrary, group_for_masters
    from .core.response_stage import response_job
    from .core.response_stage import specs_for as response_specs
    from .core.session import Session
    from .core.wavelength_stage import specs_for as wavelength_specs
    from .core.wavelength_stage import wavelength_job
    from .synth import write_example

    night = work / "night"
    rep.check("write the example night", lambda: f"{sum(len(v) for v in write_example(night).values())} frames")
    session = Session.create(work / "session")
    lib = MasterLibrary(work / "masters")
    profile = InstrumentProfile.load(work / "instrument.json")
    tol = tolerances(None)

    def masters() -> str:
        groups = [(kind, g) for kind, sub in ((BIAS, "bias"), (DARK, "darks"))
                  for g in group_for_masters(scan(night / sub, kind))]
        for line in build_masters_job(Ctx(), groups, lib, "median", True, tol):
            session.log(line)
        return f"{len(lib.masters)} masters"
    rep.check("build the masters", masters)

    def lights() -> str:
        for side, sub, name in (("target", "target", "Synth Nova"), ("reference", "reference", "Synth A0V")):
            res = scan_lights(Ctx(), str(night / sub))
            session.inputs[side].update(folder=res["folder"], name=name,
                                        lights=[{"path": str(f.path), "used": True} for f in res["frames"]])
        return f"{len(session.used_lights('target'))} + {len(session.used_lights('reference'))} lights"
    rep.check("load the lights", lights)

    def stage(name: str, specs_fn: Callable[[], tuple], job: Callable[..., Any], *job_args: Any) -> str:
        specs, problems = specs_fn()
        assert not problems and specs, "; ".join(problems) or "nothing to run"
        run = job(Ctx(), specs if name in ("files", "geometry", "extract") else specs[0], *job_args)
        _mark(session, run)
        return run.ribbon or run.summary

    rep.check("stack", lambda: stage("files", lambda: specs_for(session, IMAGES, lib, tol), stack_job, lib,
                                     session.folder))
    rep.check("geometry", lambda: stage("geometry", lambda: geometry_specs(session, IMAGES, profile, lib),
                                        geometry_job))
    rep.check("1D extract", lambda: stage("extract", lambda: extract_specs(session, IMAGES, profile.bands),
                                          extract_job))
    rep.check("wavelength", lambda: stage("wavelength", lambda: wavelength_specs(session, IMAGES, profile),
                                          wavelength_job))
    rep.check("response", lambda: stage("response", lambda: response_specs(session, profile, {"site": {}}),
                                        response_job))
    out = session.folder / "Target_R.fit"
    return out if out.exists() else None


def export_avspec(target_r: Path, work: Path) -> str:
    import numpy as np
    from astropy.io import fits

    from .core import export as ex

    flux = np.asarray(fits.getdata(target_r), dtype=np.float32)
    info = ex.read_header_info(target_r)
    obs = ex.ObserverInfo("TEST", "Self-test", "Self-test site", 31.96, -111.60, 2096.0, "Self-test equipment")
    plan = ex.build_header(info.cards, obs, "Synth Nova", {"ra": "18 05 00", "dec": "-28 30 00"},
                           target_r.parent)
    path = ex.write_export(work / ex.export_name("Synth Nova", plan.times, obs.obscode), flux, plan)
    checks = ex.preflight_avspec(plan, flux, None) + ex.verify_file(path, int(flux.size))
    errors = [c.text for c in checks if c.state == "err"]
    assert not errors, "; ".join(errors)
    return f"{path.name}, {len(checks)} checks, no errors"


def run(keep: Path | None = None) -> int:
    t0 = time.perf_counter()
    work = Path(tempfile.mkdtemp(prefix="starex_selftest_"))
    os.environ["STAREX_REDUCE_HOME"] = str(work / "settings")  # never the user's settings or master library
    rep = Report()
    from . import APP_NAME, __version__

    print(f"{APP_NAME} {__version__} self-test ({'frozen' if getattr(sys, 'frozen', False) else 'source'})")
    rep.check("bundled data", bundled_data)
    target_r = reduce_example(work, rep)
    if target_r is None:
        rep.failed += 1
        print("FAIL  no Target_R.fit: the reduction did not finish")
    else:
        rep.check("AVSpec export and validation", lambda: export_avspec(target_r, work))
    if keep is not None:
        shutil.copytree(work, keep, dirs_exist_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    verdict = "PASSED" if rep.failed == 0 else f"FAILED ({rep.failed})"
    print(f"self-test {verdict} in {time.perf_counter() - t0:.1f} s")
    return 0 if rep.failed == 0 else 1


def gui_smoke() -> int:
    """`--selftest-gui`: build every screen offscreen (catches a Qt module or plugin missing from a build)."""
    t0 = time.perf_counter()
    work = Path(tempfile.mkdtemp(prefix="starex_guitest_"))
    os.environ["STAREX_REDUCE_HOME"] = str(work / "settings")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
    rep = Report()

    def build() -> str:
        from PySide6.QtWidgets import QApplication

        from .core.session import Session
        from .ui import theme
        from .ui.about import AboutDialog
        from .ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        theme.apply(app)
        win = MainWindow(session=Session.create(work / "session"))
        win.show()
        win._warm_imports()  # numpy, scipy, pyqtgraph, every stage module (in this thread here)
        for sid in ("files", "geometry", "extract", "wavelength", "response"):
            win.ensure_screen(sid)
        win.ensure_plan()
        win.ensure_acquisition()
        win.ensure_export()
        AboutDialog(win).show()
        app.processEvents()
        assert not win.windowIcon().isNull() and not win.logo.pixmap(1.0).isNull()
        win.close()
        return "main window, 5 Reduce screens, Plan, Acquisition, Export, About"
    rep.check("GUI screens build", build)
    shutil.rmtree(work, ignore_errors=True)
    print(f"GUI self-test {'PASSED' if rep.failed == 0 else 'FAILED'} in {time.perf_counter() - t0:.1f} s")
    return 0 if rep.failed == 0 else 1


def make_example(folder: Path) -> int:
    from .synth import write_example

    files = write_example(folder)
    print(f"Example night written to {folder}: " + ", ".join(f"{k} {len(v)}" for k, v in files.items()))
    return 0
