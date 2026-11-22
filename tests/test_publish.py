"""Export → Publish: four save tiles, AVSpec gate, saved state, confetti, unchanged writers."""
# ruff: noqa: F811  (the `win` fixture is shared with test_export_screen)
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
from pytestqt.qtbot import QtBot
from test_export_screen import _fill, save_tile, win  # noqa: F401 - fixture

from starextractor.core.fitsio import write_float32
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow
from starextractor.ui.screens import export_publish as ep

REF = Path(__file__).parent / "data" / "export_ref"  # written by the writers before the Publish redesign


def _publish(win: MainWindow, qtbot: QtBot, ack: bool = True, celebrate: bool = False):
    win.settings["celebrate_saves"] = celebrate
    win.show_tab("export")
    ex = win.ensure_export()
    qtbot.waitUntil(lambda: ex.flux is not None, timeout=10000)
    _fill(ex)
    ex.show_step(1)
    if ack and ex.ack_cb.isVisibleTo(ex.publish):
        ex.ack_cb.setChecked(True)
    ex.show_step(2)
    return ex


def _states(ex) -> dict[str, bool]:
    return {k: t.saved is not None for k, t in ex.publish.tiles.items()}


def test_tiles_start_not_saved_and_save_one_at_a_time(win: MainWindow, qtbot: QtBot) -> None:  # 1, 2
    ex = _publish(win, qtbot)
    pub = ex.publish
    assert list(pub.tiles) == ["avspec", "fits", "csv", "png"]
    assert all(t.status.text() == "Not saved" and t.btn.text() == "Save" for t in pub.tiles.values())
    assert pub.tiles["avspec"].file.text() == "_v8055sgr_20260821_155_XYZW.fit"
    assert pub.tiles["fits"].file.text() == "_v8055sgr_20260821_155_XYZW_1d.fit"
    save_tile(win, qtbot, "csv")
    assert _states(ex) == {"avspec": False, "fits": False, "csv": True, "png": False}
    t = pub.tiles["csv"]
    assert t.status.text().startswith("Saved ") and " · " in t.status.text() and "KB" in t.status.text()
    assert t.btn.text() == "Save again" and t.link.isVisibleTo(t) and theme.GREEN in t.styleSheet()
    first = Path(t.saved["path"])
    save_tile(win, qtbot, "csv")  # Save again: same file name, overwritten
    assert Path(t.saved["path"]) == first and len(list(first.parent.glob("*.csv"))) == 1
    assert not hasattr(ex, "avspec_btn") and not hasattr(ex, "files_result")  # yellow bar and Files card gone


def test_avspec_gate_errors_then_warnings(win: MainWindow, qtbot: QtBot) -> None:  # 3
    ex = _publish(win, qtbot, ack=False)
    pub = ex.publish
    ex.obscode.setText("")  # a failing AVSpec header check (err)
    ex._fields_changed()
    ex.show_step(1)
    assert not ex.ack_cb.isVisibleTo(ex.publish)  # errors cannot be acknowledged
    ex.show_step(2)
    assert pub.save("avspec") is False and "The test fails" in pub.block.text.text()
    assert "Not saved" in pub.tiles["avspec"].status.text() or "fails" in pub.tiles["avspec"].status.text()
    for kind in ("fits", "csv", "png"):  # never blocked
        save_tile(win, qtbot, kind)
    _fill(ex)  # fixed: only warnings remain (always at least the first-submission note)
    ex.show_step(1)
    assert ex.ack_cb.isVisibleTo(ex.publish) and "I have read the" in ex.ack_cb.text()
    ex.show_step(2)
    assert pub.save("avspec") is False and "tick" in pub.block.text.text()
    assert pub.ack_cb.parent() is pub.tiles["avspec"]  # the tick lives in the AVSpec tile only
    assert not pub.block.isVisibleTo(pub) and "tick the box" in pub.tiles["avspec"].status.text()
    assert "warning" in pub.chip.text() and "to read" in pub.chip.text() and theme.AMBER not in pub.chip.styleSheet()
    save_tile(win, qtbot, "csv")  # still not blocked
    ex.show_step(1)
    ex.ack_cb.setChecked(True)
    ex.show_step(2)
    assert "warnings acknowledged" in pub.chip.text() or "warning acknowledged" in pub.chip.text()
    assert theme.AMBER in pub.chip.styleSheet()
    save_tile(win, qtbot, "avspec")
    assert pub.tiles["avspec"].saved is not None
    assert any("acknowledged" in ln for ln in win.session.log_lines)
    from astropy.io import fits

    with fits.open(pub.tiles["avspec"].saved["path"]) as h:  # nothing from the test in the header
        text = repr(h[0].header)
    assert "acknowledg" not in text.lower() and "warning" not in text.lower()
    ex.show_step(1)  # a new Test run asks again
    assert not ex.ack_cb.isChecked() and pub.avspec_block() is not None


def test_a_failing_writer_leaves_the_tile_not_saved(win: MainWindow, qtbot: QtBot,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:  # 4
    ex = _publish(win, qtbot)

    def boom(*_a):
        raise OSError("disk full")
    monkeypatch.setattr(ep, "save_table_job", boom)
    with qtbot.waitSignal(win.runner.failed, timeout=10000, check_params_cb=lambda slot, _m: slot == "pub.csv"):
        ex.publish.save("csv")
    t = ex.publish.tiles["csv"]
    assert t.saved is None and "disk full" in t.status.text() and theme.RED in t.status.styleSheet()
    assert t.btn.isEnabled() and t.btn.text() == "Save"
    assert any("CSV save failed" in ln and "disk full" in ln for ln in win.session.log_lines)


def test_confetti_once_per_save_never_on_open_or_when_off(win: MainWindow, qtbot: QtBot) -> None:  # 5
    ex = _publish(win, qtbot, celebrate=True)
    pub = ex.publish
    assert pub.confetti_count == 0
    save_tile(win, qtbot, "csv")
    assert pub.confetti_count == 1 and pub.last_overlay is not None
    ex.show_step(0)
    ex.show_step(2)  # reopening shows the saved state without a burst
    ex.reload()
    assert pub.confetti_count == 1 and pub.tiles["csv"].saved is not None
    win.settings["celebrate_saves"] = False
    save_tile(win, qtbot, "png")
    assert pub.confetti_count == 1 and pub.tiles["png"].saved is not None
    win.settings["celebrate_saves"] = True
    save_tile(win, qtbot, "png")
    assert pub.confetti_count == 2


def test_re_reducing_resets_the_tiles(win: MainWindow, qtbot: QtBot) -> None:  # 6
    ex = _publish(win, qtbot)
    save_tile(win, qtbot, "csv")
    save_tile(win, qtbot, "avspec")
    assert ex.step_states[2][1].startswith("Saved")
    ex.show_step(0)
    ex.show_step(2)
    assert _states(ex)["csv"] and _states(ex)["avspec"]  # kept while the spectrum is the same
    write_float32(win.session.folder / "Target_R.fit", (ex.flux * 1.01).astype(np.float32), dict(ex.r_header),
                  ["re-reduced"])
    ex.reload()
    assert not any(_states(ex).values()) and ex.step_states[2][1] == "Not saved"


def test_writers_are_byte_identical_to_before(win: MainWindow, qtbot: QtBot) -> None:  # 7
    ex = _publish(win, qtbot)
    from PySide6.QtGui import QImage

    ex.plot.grab()
    before = ex.plot.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)  # what the old Save PNG wrote
    for kind in ("avspec", "fits", "csv", "png"):
        save_tile(win, qtbot, kind)
    t = {k: Path(v.saved["path"]) for k, v in ex.publish.tiles.items()}
    assert t["avspec"].read_bytes() == (REF / "avspec.fit").read_bytes()
    assert t["fits"].read_bytes() == (REF / "avspec.fit").read_bytes()  # same writer, archive name
    assert t["csv"].read_bytes() == (REF / "table.csv").read_bytes()
    assert before.size() == QImage(str(t["png"])).size()
    # PNG: the old Save PNG was `plot.grab().save(path)`; at the same moment the tile writes the same bytes
    old = t["png"].with_name("old_way.png")
    save_tile(win, qtbot, "png")
    ex.plot.grab().save(str(old))
    assert t["png"].read_bytes() == old.read_bytes()


def test_publish_opens_fast_and_confetti_keeps_the_frame_rate(win: MainWindow, qtbot: QtBot) -> None:  # 8
    ex = _publish(win, qtbot, celebrate=True)
    ex.show_step(0)
    t0 = time.perf_counter()
    ex.show_step(2)
    opened = time.perf_counter() - t0
    save_tile(win, qtbot, "csv")
    ov = ex.publish.last_overlay
    frames = ov.frame_times
    qtbot.waitUntil(lambda: len(frames) > 0 and frames[-1] - frames[0] >= 0.9, timeout=3000)
    fps = (len(frames) - 1) / (frames[-1] - frames[0])
    print(f"\nPublish opens in {opened * 1000:.1f} ms; confetti {fps:.0f} fps over {len(frames)} frames")
    assert opened < 0.1 and fps >= 30
