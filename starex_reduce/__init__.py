"""Star ExTractor (package `starex_reduce`): Star'Ex LR spectrum reduction."""
APP_NAME = "Star ExTractor"


def _version() -> str:
    """pyproject.toml is the single source: read it from the installed metadata (also bundled in the frozen
    build), else from pyproject.toml next to the package when run from a plain checkout."""
    try:
        from importlib.metadata import version

        return version("starex-reduce")
    except Exception:  # noqa: BLE001 - not installed: fall back to the file
        import re
        from pathlib import Path

        try:
            text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
            m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
            return m.group(1) if m else "0.0.0"
        except OSError:
            return "0.0.0"


__version__ = _version()
