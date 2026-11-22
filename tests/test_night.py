"""Sessions named after the observing night; the night's sets file found from another session folder."""
from __future__ import annotations

from datetime import datetime, timedelta

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
