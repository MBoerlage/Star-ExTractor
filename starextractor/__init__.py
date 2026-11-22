"""Star ExTractor (package `starextractor`): Star'Ex LR spectrum reduction.

Importing the package must stay cheap (the splash is shown right after it, app.py): `__version__` is computed
on first use, from the cheapest source that has it.
"""
import sys
from pathlib import Path

APP_NAME = "Star ExTractor"
DIST_NAME = "starextractor"
_cached: str | None = None


def _from_pyproject() -> str | None:
    """A source checkout (also the editable install): pyproject.toml next to the package is the single source."""
    import re

    try:
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def _from_bundled_metadata() -> str | None:
    """The frozen build bundles the package metadata next to the package (copy_metadata): a *.dist-info/METADATA
    or, from a setuptools develop install, a *.egg-info/PKG-INFO. Its Version: line."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    for pattern in ("starextractor*.dist-info/METADATA", "starextractor*.egg-info/PKG-INFO"):
        for f in Path(base).glob(pattern):
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.startswith("Version:"):
                        return line.split(":", 1)[1].strip()
                    if not line:
                        break  # the headers end at the first empty line
            except OSError:
                continue
    return None


def _from_metadata() -> str | None:
    try:
        from importlib.metadata import version

        return version(DIST_NAME)
    except Exception:  # noqa: BLE001 - not installed
        return None


def version() -> str:
    global _cached
    if _cached is None:
        frozen = getattr(sys, "frozen", False)
        order = (_from_bundled_metadata, _from_metadata) if frozen else (_from_pyproject, _from_metadata)
        _cached = next((v for v in (f() for f in order) if v), "0.0.0")
    return _cached


def __getattr__(name: str) -> str:
    if name == "__version__":
        return version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
