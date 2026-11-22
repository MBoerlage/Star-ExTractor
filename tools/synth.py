"""Synthetic Star'Ex LR frames: the generator lives in the package (starextractor/synth.py) so the frozen
app can use it for `--selftest` and `--make-example`; this module keeps `import synth` working for the tests
and tools.

    python tools/synth.py OUT_DIR [--preset nova] [--n-target 6] [--n-ref 10] [--n-cal 11]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starextractor.synth import *  # noqa: F403
from starextractor.synth import main

if __name__ == "__main__":
    main()
