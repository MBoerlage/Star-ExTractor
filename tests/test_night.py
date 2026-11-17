"""Sessions named after the observing night; the night's sets file found from another session folder."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from starex_reduce.core import acq_sets
from starex_reduce.core.session import night_of


def test_night_runs_noon_to_noon_local() -> None:
    evening = datetime(2026, 8, 20, 22, 30).astimezone()  # aware local times
    after_midnight = datetime(2026, 8, 21, 2, 15).astimezone()
    next_noon = datetime(2026, 8, 21, 12, 30).astimezone()
    assert night_of(evening) == night_of(after_midnight) == "2026-08-20"
    assert night_of(next_noon) == "2026-08-21"
    # FITS DATE-OBS is UTC: the same instant gives the same night
    utc = after_midnight.astimezone(tz=None).replace(tzinfo=None) - (after_midnight.utcoffset() or timedelta())
    assert night_of(utc.isoformat()) == "2026-08-20"


def _sets(folder: Path, night: str, lights: Path) -> None:
    folder.mkdir(parents=True)
    acq_sets.save_sets(folder / acq_sets.FILE_NAME, {
        "version": 1, "night": night, "folder": str(lights),
        "target": {"name": "V8055 Sgr", "good": ["a.fits"], "excluded": []},
        "reference": {"name": "HD 163955", "good": [], "excluded": []}})


def test_sets_found_in_the_nights_session(tmp_path: Path) -> None:
    lights = tmp_path / "lights" / "nova"
    _sets(tmp_path / "sessions" / "2026-08-20", "2026-08-20", lights)
    _sets(tmp_path / "sessions" / "2026-08-19", "2026-08-19", tmp_path / "lights" / "other")
    today = tmp_path / "sessions" / "2026-08-23"  # reduced three days later
    today.mkdir()
    assert acq_sets.find_sets(today, [str(lights)]).parent.name == "2026-08-20"  # same light folder
    assert acq_sets.find_sets(today, []) is None  # nothing ties it to this session
    own = tmp_path / "sessions" / "2026-08-19"
    assert acq_sets.find_sets(own, [str(lights)]) == own / acq_sets.FILE_NAME  # its own file first
    # a session named after the night finds a sibling's sets of that night
    _sets(tmp_path / "s2" / "acq-run", "2026-08-21", tmp_path / "lights" / "x")
    named = tmp_path / "s2" / "2026-08-21"
    named.mkdir()
    assert acq_sets.find_sets(named, []).parent.name == "acq-run"
