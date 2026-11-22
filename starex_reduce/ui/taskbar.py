"""Windows taskbar identity. Stdlib only: app.py calls it before the splash, the first window."""
from __future__ import annotations

import sys

APP_USER_MODEL_ID = "StarExTractor.App"


def set_taskbar_id() -> None:
    """Windows: group the app under its own taskbar entry (and icon) instead of python.exe's."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except (AttributeError, OSError):
            pass
