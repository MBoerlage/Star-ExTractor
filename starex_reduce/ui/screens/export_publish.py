"""Export → Publish: one card with four save tiles (AAVSO AVSpec, 1D FITS, CSV, PNG). Each tile writes its own
file in the session's export folder with the existing writers, in a worker; a saved tile turns green and a
short confetti burst plays over it (optional). Saved state is kept per session and target, and resets when
the spectrum changes. Only the AVSpec tile is gated by the Test step."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage
from PySide6.QtWidgets import QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from starex_reduce.core import export as ex

from .. import theme
from ..widgets.common import Card, StatusLine, label
from ..widgets.confetti import ConfettiOverlay, reduced_motion

if TYPE_CHECKING:
    from .export import ExportScreen

KINDS = ("avspec", "fits", "csv", "png")
TITLES = {"avspec": "AAVSO AVSpec", "fits": "1D FITS", "csv": "CSV", "png": "PNG"}
DESCRIPTIONS = {
    "avspec": "The file you upload to AVSpec, with the process log next to it.",
    "fits": "The same 1D FITS for your own archive, saved without the AVSpec test.",
    "csv": "Two columns: wavelength (Å) and relative flux.",
    "png": "The plot as shown in Collect data.",
}
SLOT = "pub."


# ---- worker jobs (no Qt widgets: they run off the UI thread) -----------------------------------------
def _done(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size": path.stat().st_size}


def save_fits_job(_ctx: Any, path: Path, flux: np.ndarray, plan: ex.ExportPlan, log_src: Path | None) -> dict:
    ex.write_export(path, flux, plan)
    if log_src is not None and log_src.exists():  # the process log next to the AVSpec file
        path.with_name(path.stem + "_log.txt").write_text(log_src.read_text(encoding="utf-8"), encoding="utf-8")
    return _done(path)


def save_table_job(_ctx: Any, path: Path, lam: np.ndarray, flux: np.ndarray) -> dict:
    return _done(ex.write_table(path, lam, flux))


def save_png_job(_ctx: Any, path: Path, image: QImage) -> dict:
    if not image.save(str(path), "PNG"):
        raise OSError(f"could not write {path.name}")
    return _done(path)


def celebrate_enabled(settings: Any) -> bool:
    v = settings["celebrate_saves"]
    return (not reduced_motion()) if v is None else bool(v)


def human_size(n: int) -> str:
    return f"{n} B" if n < 1024 else (f"{n / 1024:.0f} KB" if n < 1024 ** 2 else f"{n / 1024 ** 2:.1f} MB")


class Tile(QFrame):
    def __init__(self, kind: str, card: PublishCard) -> None:
        super().__init__()
        self.kind = kind
        self.setObjectName("pubTile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(4)
        top = QHBoxLayout()
        title = QLabel(TITLES[kind])
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        top.addWidget(title, 1)
        self.link = QLabel(f"<a href='#' style='color:{theme.BLUE}; text-decoration:none'>Open folder</a>")
        self.link.setStyleSheet("font-size: 12px;")
        self.link.linkActivated.connect(lambda _h: card.open_folder())
        self.link.hide()
        top.addWidget(self.link)
        lay.addLayout(top)
        lay.addWidget(label(DESCRIPTIONS[kind], "muted", wrap=True, style="font-size: 12px;"))
        self.file = QLabel("")
        self.file.setFont(theme.mono_font(12))
        self.file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.file)
        self.ack: QCheckBox | None = None
        if kind == "avspec":  # the Test warnings are read here; the tick unlocks this tile only
            self.ack = QCheckBox("I have read the warnings")
            self.ack.setToolTip("The Test step's warnings do not stop the AVSpec file; reading them does. The "
                                "acknowledgement goes to the log only, never into the FITS header. Every Test run "
                                "asks again.")
            self.ack.toggled.connect(lambda on: card.screen._ack_toggled(on))
            self.ack.hide()
            lay.addWidget(self.ack)
        bottom = QHBoxLayout()
        self.status = QLabel("Not saved")
        self.status.setWordWrap(True)
        bottom.addWidget(self.status, 1)
        self.btn = QPushButton("Save")
        self.btn.clicked.connect(lambda: card.save(kind))
        bottom.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignBottom)
        lay.addLayout(bottom)
        self.saved: dict[str, Any] | None = None
        self.show_state()

    def show_state(self, error: str = "") -> None:
        saved = self.saved is not None and not error
        border = theme.GREEN if saved else (theme.RED if error else theme.LINE)
        self.setStyleSheet(f"#pubTile {{ background: {theme.RAISED}; border: 1px solid {border}; "
                           "border-radius: 8px; }")
        if error:
            self.status.setText(error)
            self.status.setStyleSheet(f"color: {theme.RED}; font-size: 12px;")
        elif saved:
            self.status.setText(f"Saved {self.saved['time']} · {human_size(int(self.saved['size']))}")
            self.status.setStyleSheet(f"color: {theme.GREEN}; font-size: 12px;")
        else:
            self.status.setText("Not saved")
            self.status.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
        self.link.setVisible(saved)
        self.btn.setText("Save again" if saved else "Save")


class PublishCard(Card):
    def __init__(self, screen: ExportScreen) -> None:
        super().__init__("Publish")
        self.screen = screen
        self.confetti_count = 0  # bursts started (tests, perf check)
        self.last_overlay: ConfettiOverlay | None = None
        self.meta = label("", "mono")
        self.head.insertWidget(1, self.meta)
        self.chip = QLabel("Not tested")
        self.head.addWidget(self.chip)
        self.block = StatusLine("warn", "")  # why the AVSpec tile cannot save (the old Publish message)
        self.block.hide()
        self.body.addWidget(self.block)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.tiles = {k: Tile(k, self) for k in KINDS}
        self.ack_cb: QCheckBox = self.tiles["avspec"].ack  # type: ignore[assignment]
        for i, k in enumerate(KINDS):
            grid.addWidget(self.tiles[k], i // 2, i % 2)
        self.body.addLayout(grid)
        foot = QHBoxLayout()
        self.folder_btn = QPushButton("Open export folder")
        self.folder_btn.clicked.connect(self.open_folder)
        self.copy_btn = QPushButton("Copy path")
        self.copy_btn.clicked.connect(self.copy_path)
        foot.addWidget(self.folder_btn)
        foot.addWidget(self.copy_btn)
        foot.addSpacing(12)
        foot.addWidget(label("Upload the AVSpec file at AAVSO yourself, the app does not upload.", "muted",
                             wrap=True, style="font-size: 12px;"), 1)
        self.body.addLayout(foot)

    # ---- names, state ----------------------------------------------------------------------------------
    def file_name(self, kind: str) -> str:
        base = self.screen.default_name()  # the existing naming logic
        stem = Path(base).stem
        return {"avspec": base, "fits": f"{stem}_1d.fit", "csv": f"{stem}.csv", "png": f"{stem}.png"}[kind]

    def spectrum_sig(self) -> str | None:
        """What the saved state belongs to: this target and this spectrum (re-reducing changes it)."""
        s = self.screen
        if s.flux is None:
            return None
        h = hashlib.sha1(np.ascontiguousarray(s.flux, dtype=np.float32).tobytes())
        h.update(s.objname.text().strip().encode())
        return h.hexdigest()

    def _store(self) -> dict[str, Any]:
        sess = self.screen.host.session
        exp = dict(sess.params.get("export") or {})
        sess.params["export"] = exp
        return exp.setdefault("published", {})

    def refresh(self) -> None:
        """Names, saved state (only for the current spectrum and target), header row, chip and gate."""
        sig = self.spectrum_sig()
        stored = (self.screen.host.session.params.get("export") or {}).get("published") or {}
        for k, t in self.tiles.items():
            have = self.screen.flux is not None and self.screen.plan is not None
            t.file.setText(self.file_name(k) if have else "—")
            rec = stored.get(k)
            t.saved = rec if rec and sig and rec.get("sig") == sig and Path(rec.get("path", "")).exists() else None
            if not self.screen.host.runner.is_busy(SLOT + k):
                t.btn.setEnabled(have)
            t.show_state()
        self._header_row()
        self._chip()
        self._gate()

    def _header_row(self) -> None:
        from starex_reduce.core.identity import plan_entry, same_name

        s = self.screen
        name = s.objname.text().strip() or "—"
        res = s.host.session.inputs["target"].get("simbad") or {}
        sp, vmag = (res.get("sp_type"), res.get("vmag")) if res.get("found") else (None, None)
        pe = plan_entry(s.host.session, "target")
        if (not sp or vmag is None) and pe and same_name(pe["name"], name):
            p = (s.host.session.params.get("plan") or {}).get("target") or {}
            sp, vmag = sp or pe["sp_type"], vmag if vmag is not None else p.get("vmag")
        date = s.plan.times.start.date().isoformat() if s.plan is not None and s.plan.times else None
        parts = [name] + ([sp] if sp else []) + ([f"V {float(vmag):.2f}"] if vmag is not None else []) + (
            [date] if date else [])
        self.meta.setText(" · ".join(parts))

    def test_counts(self) -> tuple[int, int, int, int] | None:
        """(passing, total, errors, warnings) of the last Test run on the current data, else None."""
        s = self.screen
        sig = s._signature()
        if sig is None or s.last_checks_sig != sig or not s.last_checks:
            return None
        c = s.last_checks
        errs = sum(x.state == "err" for x in c)
        warns = sum(x.state == "warn" for x in c)
        return len(c) - errs, len(c), errs, warns  # counted like the Test step's log line (warnings pass)

    def _chip(self) -> None:
        n = self.test_counts()
        colour, bg = theme.MUTED, theme.RAISED
        if n is None:
            text = "Not tested"
        else:
            ok, total, errs, warns = n
            text = f"{ok} of {total} checks pass"
            if errs:
                colour = theme.RED
            elif warns and self.screen.warnings_acknowledged():
                text += f" · {warns} warning{'s' if warns != 1 else ''} acknowledged"
                colour = theme.AMBER
            elif warns:
                text += f" · {warns} warning{'s' if warns != 1 else ''} to read"
            elif not warns:
                colour = theme.GREEN
        self.chip.setText(text)
        self.chip.setStyleSheet(f"color: {colour}; background: {bg}; border: 1px solid {colour}; "
                                "border-radius: 10px; padding: 2px 10px; font-size: 12px;")

    def avspec_block(self) -> str | None:
        """Why AVSpec cannot be saved now (None = it can). Errors always block; warnings until acknowledged."""
        s = self.screen
        if s.plan is None or s.flux is None:
            return "Missing data: run all five steps first."
        n = self.test_counts()
        if n is None:
            return None  # Save runs the test first
        _ok, _total, errs, warns = n
        if errs:
            texts = [c.text for c in s.last_checks if c.state == "err"]
            return "The test fails: " + ("; ".join(texts) or "see the Test step")
        if warns and not s.warnings_acknowledged():
            return (f"{warns} warning{'s' if warns != 1 else ''} from the Test step: tick “I have read the "
                    f"{warns} warning{'s' if warns != 1 else ''}” in the AVSpec tile to save it.")
        return None

    def _gate(self) -> None:
        """Errors (or missing data) above the tiles; warnings are handled by the tick in the AVSpec tile."""
        why = self.avspec_block()
        hard = bool(why) and not why.startswith(tuple("0123456789"))
        self.block.set("err" if why and why.startswith("The test fails") else "warn", why or "")
        self.block.setVisible(hard)
        n = self.test_counts()
        self.ack_cb.setToolTip("Warnings from the Test step:\n" + "\n".join(
            f"• {c.text}" for c in self.screen.last_checks if c.state == "warn") if n and n[3] else "")
        tile = self.tiles["avspec"]
        if why and not hard and tile.saved is None:
            tile.status.setText("Read the warnings, then tick the box to save.")
            tile.status.setStyleSheet(f"color: {theme.AMBER}; font-size: 12px;")

    # ---- saving --------------------------------------------------------------------------------------------
    def save(self, kind: str) -> bool:
        """Start one tile's save in a worker. Returns False when nothing was started."""
        s = self.screen
        s._refresh_header()
        if s.plan is None or s.flux is None:
            return False
        if kind == "avspec":
            if self.test_counts() is None:
                s.run_test()  # the same rule as before: AVSpec only after a test on this data
            why = self.avspec_block()
            if why:
                self.tiles[kind].show_state(why if why.startswith("The test fails") else "")
                self._gate()
                s.host.session.log(f"Export: AVSpec not saved: {why}")
                return False
        path = s._export_dir() / self.file_name(kind)
        tile = self.tiles[kind]
        tile.btn.setEnabled(False)
        tile.status.setText("Saving…")
        log = s.host.session.log
        if kind == "avspec":
            n = self.test_counts()
            if n and n[3]:
                warns = [c.text for c in s.last_checks if c.state == "warn"]
                log(f"Export: {n[3]} test warning{'s' if n[3] != 1 else ''} acknowledged: " + "; ".join(warns))
            log(f"Export: {path.name} ({len(s.plan.header)} keys, {len(s.plan.history)} HISTORY lines)")
            folder = s.host.session.folder
            args: tuple = (save_fits_job, path, s.flux.astype(np.float32), s.plan,
                           folder / "log.txt" if folder else None)
        elif kind == "fits":
            args = (save_fits_job, path, s.flux.astype(np.float32), s.plan, None)
        elif kind == "csv":
            args = (save_table_job, path, s.lam, s.flux)
        else:
            args = (save_png_job, path, s.plot.grab().toImage())  # grab on the UI thread, encode in the worker
        self._pending_sig = getattr(self, "_pending_sig", {})
        self._pending_sig[kind] = self.spectrum_sig()
        s.host.runner.submit(SLOT + kind, args[0], *args[1:])
        return True

    def on_saved(self, kind: str, result: dict[str, Any]) -> None:
        tile = self.tiles[kind]
        rec = {"path": result["path"], "size": result["size"], "time": time.strftime("%H:%M"),
               "sig": self._pending_sig.get(kind)}
        self._store()[kind] = rec
        sess = self.screen.host.session
        sess.save()
        sess.log(f"Export: {Path(result['path']).name} saved ({human_size(int(result['size']))})")
        tile.saved = rec
        tile.btn.setEnabled(True)
        tile.show_state()
        if kind == "avspec":
            self.screen.last_export = Path(result["path"])
            self.screen.published_sig = self.screen._signature()
            self.screen._update_steps()
        if celebrate_enabled(self.screen.host.settings):
            self.celebrate(tile)

    def on_failed(self, kind: str, msg: str) -> None:
        tile = self.tiles[kind]
        self.screen.host.session.log(f"Export: {TITLES[kind]} save failed: {msg}")
        tile.saved = None
        self._store().pop(kind, None)
        tile.btn.setEnabled(True)
        tile.show_state(f"Not saved: {msg.splitlines()[0]}")

    def celebrate(self, tile: Tile) -> None:
        origin = QPointF(tile.btn.geometry().center())
        self.last_overlay = ConfettiOverlay(tile, origin)
        self.confetti_count += 1

    # ---- footer --------------------------------------------------------------------------------------------
    def open_folder(self) -> None:
        if self.screen.host.session.folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.screen._export_dir())))

    def copy_path(self) -> None:
        if self.screen.host.session.folder is not None:
            QGuiApplication.clipboard().setText(str(self.screen._export_dir()))
            self.screen.host.session.log(f"Export folder path copied: {self.screen._export_dir()}")

