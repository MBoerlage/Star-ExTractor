"""File and folder names of the data root (docs/DATA_ARCHITECTURE.md): the single source of truth. No Qt.

Names are for people only: the app never parses meaning back out of a name; logbook.json and the FITS headers
are the truth.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path

FALLBACK = "Unnamed"
PAIR_SEP = "__"  # reserved: separates target and reference in a pair folder name
ROLE_SUFFIX = {"target": "target", "reference": "ref"}
NOON = 12  # observations before local noon belong to the previous evening


def sanitize_name(text: str | None) -> str:
    """'AG Dra' → AGDra, 'HD 145454' → HD145454, 'V-799 Sgr' → V_799Sgr. Spaces are dropped, letters and digits
    kept (case unchanged), any other character becomes '_', repeated '_' collapse to one (a double underscore is
    reserved for the pair separator), no leading/trailing '_'. Never empty."""
    out = []
    for ch in str(text or ""):
        if ch.isspace():
            continue
        out.append(ch if ch.isascii() and ch.isalnum() else "_")
    name = re.sub(r"_+", "_", "".join(out)).strip("_")
    return name or FALLBACK


def observing_night(t: datetime | str | None = None, tz: tzinfo | None = None) -> str:
    """YYYY-MM-DD of the evening the observation belongs to, in local time (`tz`, default the machine's zone):
    times before local noon count to the previous date. `t` is UTC (aware, naive = UTC, or an ISO string such as
    DATE-OBS); None = now."""
    if t is None:
        t = datetime.now(UTC)
    elif isinstance(t, str):
        from .frames import parse_time

        parsed = parse_time(t)
        if parsed is None:
            raise ValueError(f"not a time: {t!r}")
        t = parsed
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    local = t.astimezone(tz) if tz is not None else t.astimezone()
    day = local.date() - timedelta(days=1) if local.hour < NOON else local.date()
    return day.isoformat()


def compact_date(night: str) -> str:
    """'2025-09-30' → '20250930' (AVSpec file names)."""
    return night.replace("-", "")


def stack_filename(name: str, role: str) -> str:
    """'<Name>_target_S.fit' / '<Name>_ref_S.fit'."""
    return f"{sanitize_name(name)}_{ROLE_SUFFIX[role]}_S.fit"


def pair_folder(night: str, target: str, reference: str) -> str:
    """'<night>_<Target>__<Ref>'."""
    return f"{night}_{sanitize_name(target)}{PAIR_SEP}{sanitize_name(reference)}"


def published_folder(year: int | str, night: str, target: str, version: int) -> Path:
    """'<year>/<night>_<Target>_v<N>' (relative to the Published folder)."""
    return Path(str(year)) / f"{night}_{sanitize_name(target)}_v{int(version)}"


def with_suffix_number(path: Path, n: int) -> Path:
    """'X_target_S.fit' → 'X_target_S_2.fit'; a folder 'P' → 'P_2'. n = 1 gives the path unchanged."""
    if n <= 1:
        return path
    if path.suffix:
        return path.with_name(f"{path.stem}_{n}{path.suffix}")
    return path.with_name(f"{path.name}_{n}")
