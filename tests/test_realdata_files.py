"""M1 acceptance on the real V8055 Sgr set (docs/EXAMPLE_NOVA.md). Read-only on example_spectra."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from scipy.ndimage import median_filter

from starextractor.core.fitsio import read_image
from starextractor.core.frames import BIAS, DARK, scan
from starextractor.core.jobs import Ctx
from starextractor.core.masters import (
    LightSettings,
    MasterLibrary,
    build_master,
    calibration_frame,
    group_for_masters,
    pick_calibration,
)
from starextractor.core.stack import StackParams, run_stack

EXAMPLE = Path(__file__).resolve().parents[1] / "example_spectra" / "novasrg2026"
pytestmark = [pytest.mark.realdata,
              pytest.mark.skipif(not EXAMPLE.exists(), reason="example_spectra/novasrg2026 not present")]
SETS = {"Target": ("SgrNov32026", "SgrNov3_2026_stacked.fit", 1070),
        "Reference": ("hd163955", "hd163955_stacked.fit", 1042)}


def isolated(d: np.ndarray) -> tuple[int, int]:
    sub = d[200:1900, 200:3600]
    r = sub - median_filter(sub, 3)
    return int((r > 200).sum()), int((r < -200).sum())


@pytest.fixture(scope="module")
def lib(tmp_path_factory: pytest.TempPathFactory) -> MasterLibrary:
    lib = MasterLibrary(tmp_path_factory.mktemp("masters"))
    for kind, folder in ((BIAS, "bias"), (DARK, "darks")):
        groups = group_for_masters(scan(EXAMPLE / folder))
        assert len(groups) == 1 and len(groups[0]) == 11
        t = time.perf_counter()
        build_master(groups[0], kind, lib, Ctx(), method="sigma_clip_mean")
        assert time.perf_counter() - t < 3.0 * 1.5, "master build budget (SPEC §8) exceeded by > 50 %"
    return lib


@pytest.mark.parametrize("image", ["Target", "Reference"])
def test_real_stack_matches_bass(image: str, lib: MasterLibrary, tmp_path: Path) -> None:
    folder, bass_file, bass_row = SETS[image]
    lights = scan(EXAMPLE / folder)
    plan = pick_calibration(LightSettings.from_frames(lights), lib)
    assert plan.ok and plan.mode == "pedestal"  # 251/20 masters vs 200/3 lights
    assert plan.gain_scale == pytest.approx(0.556, abs=1e-3)

    raw = run_stack(Ctx(), lights, None, StackParams(), tmp_path / "raw.fit", [])
    t = time.perf_counter()
    cal = run_stack(Ctx(), lights, calibration_frame(plan, lib), StackParams(), tmp_path / "cal.fit",
                    plan.describe())
    assert time.perf_counter() - t < 4.0
    assert 340 <= cal.background <= 400, "calibrated background must stay near the light pedestal"
    assert abs(cal.peak_row - bass_row) <= 5
    ours = read_image(cal.path)
    bass = read_image(EXAMPLE / bass_file)
    assert float(np.median(ours)) > 0
    assert abs(float(np.median(ours)) - float(np.median(bass))) < 15

    hot_raw, _ = isolated(read_image(raw.path))
    hot_cal, cold_cal = isolated(ours)
    if image == "Target":  # 300 s: the dark signal matters; 30 s reference barely changes
        assert hot_cal < 0.6 * hot_raw
        assert cold_cal < hot_raw  # no flood of over-subtracted pixels
