"""Developer switches read from settings.json at start-up. No imports: MainWindow sets them before anything heavy is
loaded (the start-up budget, tests/test_splash.py)."""
from __future__ import annotations

# settings.json "wavelength": {"centre_method": ...}; read by core/wavelength_stage.params_of
centre_method: str | None = None
