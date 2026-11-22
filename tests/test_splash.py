"""Start-up splash (ui/splash.py, app.py): import order, when it shows, pixel sizes, closing, night dimming,
the timing log and packaging. Tests set STAREX_SPLASH_FORCE to show it on the offscreen platform."""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from starex_reduce import app as app_mod
from starex_reduce.ui import splash as sp

ROOT = Path(__file__).resolve().parents[1]
HEAVY = ("numpy", "scipy", "astropy", "pyqtgraph", "starex_reduce.ui.main_window")


def _env(home: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    env |= {"STAREX_REDUCE_HOME": str(home), "QT_QPA_PLATFORM": "offscreen", **extra}
    return env


def _settings(home: Path, data: object) -> Path:
    p = home / "settings.json"
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return p


def _render(s: sp.Splash, ratio: float = 1.0) -> np.ndarray:
    img = QImage(round(s.width() * ratio), round(s.height() * ratio), QImage.Format.Format_RGB32)
    img.setDevicePixelRatio(ratio)
    s.render(img)
    ptr = img.constBits()
    rows = np.frombuffer(ptr, np.uint8).reshape(img.height(), img.bytesPerLine())[:, : img.width() * 4]
    return rows.reshape(img.height(), img.width(), 4).copy()  # (y, x, BGRA)


# 1. import order ----------------------------------------------------------------------------------------------------
def test_nothing_heavy_is_imported_when_the_splash_is_on_screen(tmp_path: Path) -> None:
    out = subprocess.run([sys.executable, "-m", "starex_reduce.app"], cwd=ROOT, capture_output=True, text=True,
                         timeout=60, env=_env(tmp_path, STAREX_SPLASH_FORCE="1", STAREX_STARTUP_STOP="splash"))
    row = json.loads(out.stdout.strip().splitlines()[-1])
    assert row["splash"] is True
    assert row["loaded"] == [], f"imported before the splash was shown: {row['loaded']}"


def test_entry_module_imports_only_sys_os_time_and_pyside6() -> None:
    import ast

    tree = ast.parse((ROOT / "starex_reduce" / "app.py").read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            top.add(node.module or "")
    assert top <= {"sys", "os", "time", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"}, top
    first = tree.body[2] if isinstance(tree.body[0], ast.Expr) else tree.body[1]  # docstring, __future__, time
    assert isinstance(first, ast.Import) and first.names[0].name == "time"


# 2. when it shows ---------------------------------------------------------------------------------------------------
def test_by_default_not_under_tests_offscreen_or_the_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sp.ENV_FORCE, raising=False)
    assert sp.wanted([], True) == (False, "offscreen platform")
    monkeypatch.setenv("QT_QPA_PLATFORM", "windows")
    assert sp.wanted([], True) == (False, "test run")  # PYTEST_CURRENT_TEST
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert sp.wanted([], True) == (True, "")
    monkeypatch.setenv(sp.ENV_OFF, "1")  # tools/screenshot.py sets it
    assert sp.wanted([], True)[0] is False


def test_force_flag_command_line_and_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sp.ENV_FORCE, "1")
    monkeypatch.setenv(sp.ENV_OFF, "1")
    assert sp.wanted([], True) == (True, "")  # the tests' switch wins over the automatic ones
    for arg in ("--no-splash", "--selftest", "--selftest-gui", "--version", "--make-example"):
        assert sp.wanted([arg], True)[0] is False, arg
    assert sp.wanted([], False) == (False, "turned off in Settings")


def test_screenshot_tool_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sp.ENV_OFF, raising=False)
    sys.modules.pop("screenshot", None)
    import screenshot  # noqa: F401 - tools/ is on sys.path (conftest)

    assert os.environ.get(sp.ENV_OFF) == "1"


def test_settings_are_read_cheaply_with_safe_fallbacks(tmp_path: Path) -> None:
    missing = tmp_path / "none.json"
    assert sp.read_prefs(missing) == ("default", True)
    assert sp.read_prefs(_settings(tmp_path, "{not json")) == ("default", True)
    assert sp.read_prefs(_settings(tmp_path, [1, 2])) == ("default", True)
    assert sp.read_prefs(_settings(tmp_path, {"ui": "night"})) == ("default", True)
    assert sp.read_prefs(_settings(tmp_path, {"ui": {"palette": "night", "splash": False}})) == ("night", False)
    assert sp.read_prefs(_settings(tmp_path, {"ui": {"palette": "red", "splash": "yes"}})) == ("red", True)
    assert sp.read_prefs(_settings(tmp_path, {"ui": {"palette": "default"}})) == ("default", True)


def test_settings_file_follows_the_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from starex_reduce.core.settings import Settings

    monkeypatch.setenv("STAREX_REDUCE_HOME", str(tmp_path))
    assert sp.settings_file() == Settings().path


def test_settings_switch_turns_it_off(qtbot: QtBot) -> None:
    from starex_reduce.core.settings import Settings
    from starex_reduce.ui.settings_dialog import SettingsDialog

    st = Settings()
    st["ui"] = {"palette": "night"}
    dlg = SettingsDialog(st)
    qtbot.addWidget(dlg)
    assert dlg.splash.isChecked()  # on by default
    dlg.splash.setChecked(False)
    v = dlg.values()
    assert isinstance(v, dict) and v["ui"] == {"palette": "night", "splash": False}  # the palette is kept
    st.data.update(v)
    st.save()
    assert sp.read_prefs() == ("night", False)


# 3. pixel sizes, missing image --------------------------------------------------------------------------------------
@pytest.mark.parametrize(("ratio", "scale", "logical"), [(1.0, 1, (800, 448)), (2.0, 2, (800, 448)),
                                                          (1.25, 1, (640, 359)), (1.5, 2, (1067, 598))])
def test_native_pixel_size_per_device_pixel_ratio(qtbot: QtBot, ratio: float, scale: int,
                                                   logical: tuple[int, int]) -> None:
    s = sp.Splash("1.2.3", ratio=ratio)
    qtbot.addWidget(s)
    assert s.scale == scale
    file_size = QImage(str(sp.IMAGES[scale])).size()
    assert (s.pixmap.width(), s.pixmap.height()) == (file_size.width(), file_size.height())  # never rescaled
    assert s.pixmap.devicePixelRatio() == ratio  # one image pixel per device pixel
    assert (s.width(), s.height()) == logical


def test_assets_are_prescaled_1x_and_exact_2x() -> None:
    a, b = QImage(str(sp.IMAGES[1])), QImage(str(sp.IMAGES[2]))
    assert (a.width(), a.height()) == (800, 448)
    assert (b.width(), b.height()) == (2 * a.width(), 2 * a.height())
    assert sp.IMAGES[1].stat().st_size < 600_000 and sp.IMAGES[2].stat().st_size < 1_600_000


def test_missing_image_means_no_splash_and_a_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(sp.ENV_FORCE, "1")
    monkeypatch.setattr(sp, "IMAGES", {1: tmp_path / "no.png", 2: tmp_path / "no@2x.png"})
    monkeypatch.setattr(sp, "WARNINGS", [])
    assert sp.start([], "1.0") is None
    assert len(sp.WARNINGS) == 1 and "splash image missing" in sp.WARNINGS[0]


def test_version_and_status_are_drawn_and_status_updates(qtbot: QtBot) -> None:
    s = sp.Splash("9.9.9", ratio=1.0)
    qtbot.addWidget(s)
    before = _render(s)
    s.set_status(sp.STATUS_WINDOW)
    after = _render(s)
    bottom_right = (slice(s.height() - 30, s.height()), slice(s.width() - 200, s.width()))
    assert not np.array_equal(before[bottom_right], after[bottom_right])  # the status line changed
    plain = sp.Splash("9.9.9", ratio=1.0)
    qtbot.addWidget(plain)
    plain.version, plain.status = "", ""
    assert not np.array_equal(_render(plain)[s.height() - 30:, :200], before[s.height() - 30:, :200])  # version


# 4. closing, focus, dialogs, errors ---------------------------------------------------------------------------------
def test_never_focused_and_on_top_only_while_open(qtbot: QtBot) -> None:
    s = sp.Splash("1.0", ratio=1.0)
    qtbot.addWidget(s)
    assert s.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert s.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert s.focusPolicy() == Qt.FocusPolicy.NoFocus
    s.show_now()
    ran = []
    s.after_close(lambda: ran.append(1))
    assert not ran
    s.finish(fade=False)
    assert s.is_closed() and not s.isVisible() and ran == [1]
    s.after_close(lambda: ran.append(2))  # already gone: at once
    assert ran == [1, 2]


def test_fade_does_not_block_and_ends_closed(qtbot: QtBot) -> None:
    s = sp.Splash("1.0", ratio=1.0)
    qtbot.addWidget(s)
    s.show_now()
    s.finish(fade=True)
    assert s._fade is not None and not s.is_closed()  # returns at once, fades in the event loop
    qtbot.waitUntil(s.is_closed, timeout=2000)
    assert not s.isVisible()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[sp.Splash]:
    """main() in-process with the splash forced on; collects the splash it made."""
    made: list[sp.Splash] = []
    real = sp.start

    def start(argv, version):
        s = real(argv, version)
        if s is not None:
            made.append(s)
        return s

    monkeypatch.setenv(sp.ENV_FORCE, "1")
    monkeypatch.setattr(sp, "start", start)
    from PySide6.QtCore import QEventLoop

    loop = QEventLoop()  # never QApplication.exec/quit in-process (see app.quit_event_loop)
    monkeypatch.setattr(app_mod, "run_event_loop", lambda app: loop.exec())
    monkeypatch.setattr(app_mod, "quit_event_loop", lambda app: loop.quit())
    return made


def test_closes_when_the_window_is_shown_and_logs_the_timings(qtbot: QtBot, captured: list[sp.Splash],
                                                              monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from starex_reduce.ui import main_window, motion

    monkeypatch.setattr(motion, "reduced_motion", lambda: True)  # 5b: no fade with reduced motion
    log = tmp_path / "startup.jsonl"
    monkeypatch.setenv(app_mod.ENV_LOG, str(log))
    wins: list = []
    real_init = main_window.MainWindow.__init__

    def init(self, *a, **k):
        real_init(self, *a, **k)
        wins.append(self)

    monkeypatch.setattr(main_window.MainWindow, "__init__", init)
    assert app_mod.main(["--startup-time"]) == 0
    s = captured[0]
    assert s.is_closed() and not s.isVisible() and s._fade is None
    assert s.status == sp.STATUS_READY
    assert s.pumps <= sp.MAX_PROCESS_EVENTS
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert row["splash"] < row["window"] <= row["ready"]
    win = wins[0]
    assert any("startup: splash" in line and "window" in line and "ready" in line for line in win.session.log_lines)
    win.close()


def test_closed_before_the_first_run_dialog(qtbot: QtBot, captured: list[sp.Splash],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from starex_reduce.ui import data_locations

    seen: list[bool] = []

    def exec_(self) -> int:
        seen.append(captured[0].is_closed())
        return 0  # the user cancels: the app ends

    monkeypatch.setattr(data_locations.FirstRunDialog, "exec", exec_)
    assert app_mod.main([]) == 0  # no data root yet: first run
    assert seen == [True]


def test_startup_error_closes_it_before_the_message(qtbot: QtBot, captured: list[sp.Splash],
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from starex_reduce.ui import main_window

    def boom(self, *a, **k):
        raise RuntimeError("cannot build the window")

    monkeypatch.setattr(main_window.MainWindow, "__init__", boom)
    shown: list[tuple[str, bool]] = []
    monkeypatch.setattr(app_mod, "show_startup_error",
                        lambda msg: shown.append((msg, captured[0].is_closed() and not captured[0].isVisible())))
    assert app_mod.main(["--startup-time"]) == 1
    assert shown == [("RuntimeError: cannot build the window", True)]


def test_startup_error_is_written_to_error_log(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch,
                                               _isolated_home: Path) -> None:
    from starex_reduce.ui import main_window

    monkeypatch.setattr(main_window.MainWindow, "__init__", lambda self, *a, **k: (_ for _ in ()).throw(
        ValueError("broken settings")))
    monkeypatch.setattr(app_mod, "show_startup_error", lambda msg: None)
    assert app_mod.main(["--startup-time", "--no-splash"]) == 1
    assert "ValueError: broken settings" in (_isolated_home / "error.log").read_text(encoding="utf-8")


# 5. night dimming ---------------------------------------------------------------------------------------------------
def test_night_palette_dims_the_splash(qtbot: QtBot) -> None:
    day, night = sp.Splash("1.0", "default", ratio=1.0), sp.Splash("1.0", "night", ratio=1.0)
    red = sp.Splash("1.0", "red", ratio=1.0)
    for s in (day, night, red):
        qtbot.addWidget(s)
    d, n, r = (_render(s)[..., :3].mean() for s in (day, night, red))
    assert n < 0.6 * d and r < 0.6 * d  # a 60 % overlay of the palette's background


def test_reduced_motion_skips_the_fade(qtbot: QtBot) -> None:
    s = sp.Splash("1.0", ratio=1.0)
    qtbot.addWidget(s)
    s.show_now()
    s.finish(fade=False)  # what app.py passes when motion.reduced_motion() is true
    assert s._fade is None and s.is_closed()


# 6. budget ----------------------------------------------------------------------------------------------------------
@pytest.mark.skipif(os.environ.get("STAREX_SKIP_BUDGET") == "1" or sys.platform != "win32",
                    reason="start-up budget: skipped on slow machines (STAREX_SKIP_BUDGET=1) and off Windows")
def test_startup_budget_on_the_real_platform(tmp_path: Path) -> None:
    """Three real starts (a real window on the screen, splash forced on, --startup-time protocol, empty settings):
    the splash comes before the window, within a generous 1 s, and ready is within the old baseline + 50 ms
    (from process start here, from launch in the baseline: the comparison is in the baseline's favour)."""
    # the old code timed the same way: --startup-time, an empty settings folder (tools/startup_bench.py --legacy
    # --empty-home), from the launch
    base = json.loads((ROOT / "tools" / "startup_baseline.json").read_text(encoding="utf-8")).get("legacy-empty", {})
    rows = []
    for _ in range(3):
        log = tmp_path / "s.jsonl"
        env = _env(tmp_path, STAREX_SPLASH_FORCE="1", STAREX_STARTUP_LOG=str(log))
        env.pop("QT_QPA_PLATFORM")
        subprocess.run([sys.executable, "-m", "starex_reduce.app", "--startup-time"], cwd=ROOT, env=env,
                       capture_output=True, timeout=60, check=True)
        rows.append(json.loads(log.read_text(encoding="utf-8").splitlines()[-1]))
        log.unlink()
    splash, window, ready = (statistics.median(r[k] for r in rows) for k in ("splash", "window", "ready"))
    assert splash < window <= ready
    assert splash < 1000
    if base.get("ready"):
        assert ready <= base["ready"] + 50, f"ready {ready:.0f} ms vs baseline {base['ready']:.0f} ms"


# 7. packaging -------------------------------------------------------------------------------------------------------
def test_spec_bundles_the_assets_and_the_bootloader_splash() -> None:
    spec = (ROOT / "packaging" / "starex_extractor.spec").read_text(encoding="utf-8")
    assert '(str(PKG / "resources"), "starex_reduce/resources")' in spec  # splash.png and splash@2x.png
    assert 'Splash(' in spec and '"splash.png"' in spec and "splash.binaries" in spec
    assert "STAREX_BOOT_SPLASH" in spec and "except (SystemExit, Exception)" in spec  # never fails the build
    assert sp.IMAGES[1].parent.name == "resources" and sp.IMAGES[1].parent.parent.name == "starex_reduce"


def test_bootloader_splash_is_closed_only_in_the_frozen_build(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake = types.SimpleNamespace(is_alive=lambda: True, close=lambda: calls.append("close"))
    monkeypatch.setitem(sys.modules, "pyi_splash", fake)
    sp.close_bootloader_splash()
    assert calls == []  # a source run: no-op
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    sp.close_bootloader_splash()
    assert calls == ["close"]
    monkeypatch.delitem(sys.modules, "pyi_splash")
    sp.close_bootloader_splash()  # built without it: still no error


def test_command_line_modes_close_the_bootloader_splash_and_show_none(monkeypatch: pytest.MonkeyPatch,
                                                                       capsys: pytest.CaptureFixture) -> None:
    closed: list[int] = []
    monkeypatch.setattr(sp, "close_bootloader_splash", lambda: closed.append(1))
    monkeypatch.setattr(sp, "start", lambda *a: pytest.fail("no splash in a command-line mode"))
    assert app_mod.main(["--version"]) == 0
    assert closed == [1]


def test_frozen_version_from_bundled_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import starex_reduce as pkg

    (tmp_path / "starex_reduce.egg-info").mkdir()
    (tmp_path / "starex_reduce.egg-info" / "PKG-INFO").write_text("Metadata-Version: 2.4\nName: starex-reduce\n"
                                                                  "Version: 7.8.9\n\nbody", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert pkg._from_bundled_metadata() == "7.8.9"
    (tmp_path / "starex_reduce-7.9.0.dist-info").mkdir()
    (tmp_path / "starex_reduce-7.9.0.dist-info" / "METADATA").write_text("Version: 7.9.0\n", encoding="utf-8")
    assert pkg._from_bundled_metadata() == "7.9.0"  # dist-info first


def test_package_import_is_cheap() -> None:
    out = subprocess.run([sys.executable, "-c", "import sys, starex_reduce; print('importlib.metadata' in "
                          "sys.modules, 'PySide6' in sys.modules)"], cwd=ROOT, capture_output=True, text=True,
                         timeout=60, check=True)
    assert out.stdout.split() == ["False", "False"]


def test_qt_splash_matches_the_bootloader_splash_pixel_for_pixel(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """With stage 1 on screen the Qt splash uses the same 1x image at its native size, also at 200 %."""
    monkeypatch.setenv(sp.ENV_FORCE, "1")
    monkeypatch.setattr(sp, "bootloader_splash_alive", lambda: True)
    made = sp.Splash("1.0", ratio=2.0, scale=1)
    qtbot.addWidget(made)
    assert made.scale == 1 and (made.pixmap.width(), made.pixmap.height()) == (800, 448)
    assert (made.width(), made.height()) == (400, 224)  # 800 x 448 device pixels: stage 1's size
    s = sp.start([], "1.0")
    assert s is not None and s.scale == 1
    s.close_now()
