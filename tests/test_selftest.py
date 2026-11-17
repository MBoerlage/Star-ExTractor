"""--selftest from source: bundled data, the small example reduced end to end, a valid AVSpec file."""
from __future__ import annotations

from pathlib import Path

import pytest

from starex_reduce import __version__
from starex_reduce.app import main


def test_selftest_passes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("STAREX_REDUCE_HOME", "unused")  # the self-test sets its own; restored after the test
    assert main(["--selftest"]) == 0
    out = capsys.readouterr().out
    assert "self-test PASSED" in out and "FAIL" not in out
    for step in ("bundled data", "build the masters", "stack", "geometry", "1D extract", "wavelength", "response",
                 "AVSpec export and validation"):
        assert f"ok    {step}" in out


def test_make_example_and_version(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--make-example", str(tmp_path / "night")]) == 0
    for sub, n in (("target", 3), ("reference", 3), ("darks", 3), ("bias", 3)):
        assert len(list((tmp_path / "night" / sub).glob("*.fits"))) == n
    assert main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out and __version__ != "0.0.0"


def test_version_comes_from_pyproject() -> None:
    import re

    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE).group(1) == __version__
