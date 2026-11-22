"""The OS "fewer animations" preference. Stdlib only: the start-up splash reads it before Qt widgets exist."""
from __future__ import annotations

import sys


def reduced_motion() -> bool:
    """True when the OS asks for fewer animations (Windows: client-area animation off)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        flag = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(flag), 0)  # SPI_GETCLIENTAREAANIMATION
        return bool(ok) and not flag.value
    except (AttributeError, OSError):
        return False
