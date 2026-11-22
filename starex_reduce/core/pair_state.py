"""The loaded pair as stored in the session (params["files"]["pairs"]). Plain dicts, no numpy: the main window
reads it while it is being built, before any heavy import (core/pair.py re-exports these)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .session import Session


def pairs_of(session: Session) -> list[dict[str, Any]]:
    p = session.params.get("files")
    pairs = p.get("pairs") if isinstance(p, dict) else None
    return pairs if isinstance(pairs, list) else []


def active_pair(session: Session) -> dict[str, Any] | None:
    pairs = pairs_of(session)
    return pairs[0] if pairs and isinstance(pairs[0], dict) else None


def pair_text(pair: dict[str, Any]) -> str:
    return f"{pair['target']['name'] or 'Target'} + {pair['reference']['name'] or 'Reference'}"
