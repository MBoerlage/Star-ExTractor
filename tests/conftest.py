from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

EXAMPLE = ROOT / "example_spectra" / "novasrg2026"
PROJECTS = ROOT.parent / "projects"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never read or write the real %APPDATA%\\starex_reduce during tests."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("STAREX_REDUCE_HOME", str(home))
    return home


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if EXAMPLE.exists() or PROJECTS.exists():
        return
    skip = pytest.mark.skip(reason="real sample data not present")
    for item in items:
        if "realdata" in item.keywords:
            item.add_marker(skip)
