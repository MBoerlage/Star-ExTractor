"""Start-up benchmark: launch the app N times and print the medians of splash visible, window shown and ready.

    python tools/startup_bench.py [-n 5] [--no-splash] [--legacy] [--save-baseline] [--app-dir DIR] [--entry PY]
    python tools/startup_bench.py --exe dist/StarExtractor/StarExtractor.exe     # the frozen build

Real start (default): each run starts `python -m starex_reduce.app` with STAREX_STARTUP_LOG=<temp file>; the app
appends one JSON line with the three times in ms from its process creation and exits once ready (app.py ›
StartupClock). Ready = the window has painted and the page it opened on is built. "From launch" adds the time
between this script starting the process and the process itself (the venv launcher). The saved settings apply
(palette, the page the app reopens on), so this is what you see when you start it.

--legacy times the older `--startup-time` protocol instead (window shown and Select built, no restore of the last
page), which also works on a checkout from before the splash (--app-dir, e.g. a git worktree). --entry runs a
script instead of `-m starex_reduce.app` that writes the same JSON line: used once to time a real start of the
code from before the splash with a probe. tools/startup_baseline.json keeps one baseline per mode;
--save-baseline writes the entry for the mode that ran, otherwise the matching entry is compared.

--empty-home runs with a new, empty settings folder each time (STAREX_REDUCE_HOME), as the tests do; its baselines
are kept apart ("legacy-empty", "real-empty"). The budget test in tests/test_splash.py compares with "legacy-empty".

--exe times the frozen build (its process is created by the bootloader, so "from process start" includes it) and
also reports "first window": when any window of that process first became visible, polled here every ~2 ms
(Windows). With the bootloader splash in the build that is stage 1, before Python has started.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / "startup_baseline.json"
KEYS = ("splash", "window", "ready")


def _first_visible_window_ms(proc: subprocess.Popen, log: Path, t0: float) -> float | None:
    """ms from launch until a window of proc is visible (Windows), polling until the app has written its log."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[bool] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == proc.pid and user32.IsWindowVisible(hwnd):
            found.append(True)
            return False
        return True

    enum = proto(cb)
    while proc.poll() is None and not log.exists():
        user32.EnumWindows(enum, 0)
        if found:
            return (time.perf_counter() - t0) * 1000.0
        time.sleep(0.002)
    return None


def run_once(python: str, app_dir: Path, legacy: bool, extra: list[str],
             entry: Path | None = None, exe: Path | None = None, home: Path | None = None) -> dict[str, float | None]:
    env = dict(os.environ)
    if home is not None:
        env["STAREX_REDUCE_HOME"] = str(home)
    env.pop("QT_QPA_PLATFORM", None)
    env["PYTHONPATH"] = str(app_dir)
    if legacy:
        t = time.perf_counter()
        p = subprocess.Popen([python, "-m", "starex_reduce.app", "--startup-time", *extra], cwd=app_dir, env=env,
                             stdout=subprocess.PIPE, text=True)
        row: dict[str, float | None] = {"splash": None}
        assert p.stdout is not None
        for line in p.stdout:  # printed (and flushed) the moment each happens: time them from the launch
            key = line.split("=")[0].strip()
            if key in ("shown_s", "startup_s"):
                row["window" if key == "shown_s" else "ready"] = (time.perf_counter() - t) * 1000.0
        p.wait(timeout=60)
        return row
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "startup.jsonl"
        env["STAREX_STARTUP_LOG"] = str(log)
        first = None
        launch = time.time()
        if exe is not None:
            env.pop("PYTHONPATH")
            t = time.perf_counter()
            proc = subprocess.Popen([str(exe), *extra], cwd=exe.parent, env=env)
            first = _first_visible_window_ms(proc, log, t)
            proc.wait(timeout=60)
        else:
            cmd = [str(entry)] if entry else ["-m", "starex_reduce.app"]
            subprocess.run([python, *cmd, *extra], cwd=app_dir, env=env, timeout=60, check=False)
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    lead = (row["t0_epoch"] - launch) * 1000.0 - row["from_process_ms"]  # launch → process creation (launcher)
    out: dict[str, float | None] = {k: row[k] for k in KEYS}
    out |= {f"{k}_from_launch": None if row[k] is None else row[k] + lead for k in KEYS}
    out["first_window"] = first
    return out


def median(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return statistics.median(vals) if vals else None


def fmt(v: float | None) -> str:
    return "      -" if v is None else f"{v:7.0f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--app-dir", type=Path, default=ROOT, help="checkout to run (default: this one)")
    ap.add_argument("--legacy", action="store_true", help="time the --startup-time protocol (works on old code)")
    ap.add_argument("--no-splash", action="store_true", help="pass --no-splash")
    ap.add_argument("--entry", type=Path, help="script to run instead of -m starex_reduce.app (real start only)")
    ap.add_argument("--exe", type=Path, help="time this frozen StarExtractor.exe instead (real start)")
    ap.add_argument("--empty-home", action="store_true", help="a new empty settings folder for every run")
    ap.add_argument("--save-baseline", action="store_true", help=f"write the medians to {BASELINE.name}")
    args = ap.parse_args()
    extra = ["--no-splash"] if args.no_splash else []
    rows = []
    for i in range(args.n):
        with tempfile.TemporaryDirectory() as home:
            r = run_once(args.python, args.app_dir.resolve(), args.legacy, extra, args.entry,
                         args.exe.resolve() if args.exe else None, Path(home) if args.empty_home else None)
        rows.append(r)
        print(f"run {i + 1}: " + "  ".join(f"{k} {fmt(r.get(k))}" for k in KEYS), flush=True)
    mode = ("legacy" if args.legacy else ("frozen" if args.exe else "real")) + ("-empty" if args.empty_home else "")
    from_launch = {k: median(rows, k if args.legacy else f"{k}_from_launch") for k in KEYS}
    print(f"\nmedian of {args.n}, {'--startup-time protocol' if args.legacy else 'real start'}, ms")
    print("                    splash  window   ready")
    if not args.legacy:
        print("from process start " + " ".join(fmt(median(rows, k)) for k in KEYS))
    print("from launch        " + " ".join(fmt(from_launch[k]) for k in KEYS))
    if args.exe:
        print(f"first window (stage 1 when built in): {fmt(median(rows, 'first_window')).strip()} ms from launch")
    saved = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    base = saved.get(mode)
    if base and not args.save_baseline:
        print("baseline           " + " ".join(fmt(base.get(k)) for k in KEYS) + f"   ({base.get('note', '')})")
        print("change             " + " ".join(
            fmt(None if from_launch[k] is None or base.get(k) is None else from_launch[k] - base[k]) for k in KEYS))
    if args.save_baseline:
        saved[mode] = {**from_launch, "note": f"{time.strftime('%Y-%m-%d')}, {args.app_dir.resolve().name}, "
                                              f"n={args.n}"}
        BASELINE.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
        print(f"saved {BASELINE.relative_to(ROOT)} [{mode}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
