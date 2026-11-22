"""Measure the Plan tab's layout at a window size and print it as JSON (tests/test_plan_layout.py).

Runs offscreen with the system fonts (QT_QPA_FONTDIR, as tools/shots_all.py does): the offscreen platform's
fallback font is about twice as wide as the real one, so widths measured without it say nothing.

    python tools/plan_layout_check.py 1280x800 [--pairs N] [--palette default|night|red] [--shot out.png]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STARS = [("bet Cas", 2.2945, 59.1498), ("V1405 Cas", 351.1592, 61.1525), ("P Cyg", 304.4466, 38.0329),
         ("Vega", 279.2347, 38.7837), ("Deneb", 310.3580, 45.2803), ("gam Cas", 14.1772, 60.7167)]


def setup_env() -> Path:
    home = Path(tempfile.mkdtemp(prefix="starex_layout_"))
    os.environ["STAREXTRACTOR_HOME"] = str(home)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
        import ctypes

        try:
            ctypes.CDLL("ucrtbase")._setmaxstdio(8192)
        except (OSError, AttributeError):
            pass
    sys.path.insert(0, str(ROOT))
    return home


def spin(app, n: int = 6) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtTest import QTest

    for _ in range(n):
        QTest.qWait(20)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # replaced table cell widgets


def add_pairs(win, page, n: int) -> None:
    from starextractor.core import plan as pl

    for k in range(n):
        name, ra, dec = STARS[k % len(STARS)]
        if k >= len(STARS):
            name = f"{name} {k // len(STARS) + 1}"
            ra = (ra + 3 * k) % 360
        pl.set_target(win.session, name, ra, dec, "simbad", None)
        page.load_session()
        if k % 3 != 2:  # every third pair has no reference: "! pick reference"
            page.use_selected(0)
        page.pairs.add_pair()


def measure(win, page, app) -> dict:
    from PySide6.QtGui import QFont, QFontMetrics
    from PySide6.QtWidgets import QAbstractButton, QLabel, QWidget

    from starextractor.ui.widgets.sky_polar import LEGEND_H

    def rect(w) -> list[int]:
        tl = w.mapTo(page.scroll.widget(), w.rect().topLeft())
        return [tl.x(), tl.y(), w.width(), w.height()]

    def win_rect(w) -> list[int]:
        tl = w.mapTo(win, w.rect().topLeft())
        return [tl.x(), tl.y(), w.width(), w.height()]

    bar = page.margin_bar

    def margins() -> list[int]:
        """Window edge to the outermost card, left and right (cards of rows 2 and 3 span the page)."""
        r = win_rect(page.pairs)
        return [r[0], win.width() - (r[0] + r[2])]

    def conditions_clipped() -> list:
        """Every visible control of the Observing conditions card: inside the card, its text fits its size hint, and
        no two controls overlap."""
        from PySide6.QtCore import QRect
        from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QLabel, QLineEdit

        card = page.conditions
        out, rects = [], []
        kinds = (QLabel, QLineEdit, QAbstractButton, QAbstractSpinBox)
        for w in card.findChildren(QWidget):
            if not isinstance(w, kinds) or not w.isVisibleTo(card) or w.parentWidget() is None:
                continue
            if isinstance(w.parentWidget(), kinds):  # sub-widgets of a control (a spin box's line edit)
                continue
            tl = w.mapTo(card, w.rect().topLeft())
            r = QRect(tl, w.size())
            name = getattr(w, "text", lambda: "")() if not isinstance(w, QAbstractSpinBox) else w.objectName()
            if not card.rect().contains(r):
                out.append(["outside the card", str(name), list(r.getRect())])
            if isinstance(w, (QAbstractButton, QLabel)) and w.text() and not getattr(w, "wordWrap", lambda: False)():
                if w.width() < w.sizeHint().width() - 1:
                    out.append(["text cut", w.text(), w.width(), w.sizeHint().width()])
            if isinstance(w, (QLineEdit, QAbstractSpinBox)) and w.width() < w.minimumSizeHint().width():
                out.append(["field too narrow", str(name), w.width()])
            for other, orect in rects:
                if r.intersects(orect) and not other.isAncestorOf(w) and not w.isAncestorOf(other):
                    out.append(["overlap", str(name), str(getattr(other, "text", lambda: "")())])
            rects.append((w, r))
        return out

    left, right = page.columns
    cond = page.conditions
    cards = {"header": page.header, "target": page.name.parentWidget(),
             "reference": page.n_box.parentWidget(), "conditions": cond, "polar": cond.polar_card,
             "chart": cond.chart_card, "pairs": page.pairs,
             "plan": page.plan_box.parentWidget()}
    clipped = []
    for b in page.findChildren(QAbstractButton):
        if b.isVisibleTo(page) and b.text() and b.width() < b.sizeHint().width():
            clipped.append([b.text(), b.width(), b.sizeHint().width()])
    for lab in page.findChildren(QLabel):
        if lab.isVisibleTo(page) and lab.text() and not lab.wordWrap() and lab.width() < lab.sizeHint().width() - 1:
            clipped.append([lab.text(), lab.width(), lab.sizeHint().width()])
    for t, pad in ((page.table, 12), (page.pairs.table, 16)):  # cell text cut short ("5.…")
        for r in range(t.rowCount()):
            for c in range(t.columnCount()):
                it = t.item(r, c)
                if it is not None and it.text():
                    need = QFontMetrics(it.font() if it.font() != QFont() else t.font()).horizontalAdvance(it.text())
                    if need + pad > t.columnWidth(c):
                        clipped.append([it.text(), t.columnWidth(c), need + pad])
    tz = [lab.text() for lab in page.findChildren(QLabel)
          if lab.isVisibleTo(page) and lab.text().startswith("Times use your computer's time zone")]
    sb = page.plan_box.verticalScrollBar()
    sim = page.simbad_btn
    card_t = cards["target"]
    sim_tl = sim.mapTo(card_t, sim.rect().topLeft())
    hbar = page.scroll.horizontalScrollBar()
    return {
        "window": [win.width(), win.height()],
        "columns": [rect(left), rect(right)],
        "in_left": [k for k, w in cards.items() if left.isAncestorOf(w)],
        "in_right": [k for k, w in cards.items() if right.isAncestorOf(w)],
        "rects": {k: rect(w) for k, w in cards.items()},
        "clipped": clipped,
        "simbad": {"x": sim_tl.x(), "w": sim.width(), "hint": sim.sizeHint().width(), "card_w": card_t.width(),
                   "visible": sim.isVisibleTo(page)},
        "tz_lines": tz,
        "polar": [cond.polar.width(), cond.polar.height() - LEGEND_H],
        "chart_h": cond.chart.height(),
        "plan_box": {"h": page.plan_box.height(), "min": page.plan_box.minimumHeight(),
                     "max": page.plan_box.maximumHeight(), "scroll_max": sb.maximum(),
                     "lines": page.plan_box.blockCount()},
        "page_w": page.scroll.widget().width(), "viewport_w": page.scroll.viewport().width(),
        "page_min_w": page.scroll.widget().minimumSizeHint().width(),
        "hbar_visible": hbar.isVisible(),
        "status": [page.pairs.status_text(r) for r in range(page.pairs.table.rowCount())],
        "pair_cols": [page.pairs.table.columnWidth(c) for c in range(page.pairs.table.columnCount())],
        "buttons": [b.text() for b in (page.pairs.add_btn, page.pairs.update_btn, page.pairs.remove_btn,
                                       page.pairs.up_btn, page.pairs.down_btn, page.pairs.sort_btn,
                                       page.pairs.load_btn)],
        "load_right": rect(page.pairs.load_btn)[0] + page.pairs.load_btn.width(),
        "pairs_right": rect(page.pairs)[0] + page.pairs.width(),
        "status_line": [rect(page.pairs.status)[1], page.pairs.status.isVisibleTo(page)],
        "in_window": {k: win_rect(w) for k, w in cards.items()},
        "title_intro": [win_rect(page.title), win_rect(page.intro)],
        "margins": margins(),
        "scrollbar": {"visible": bar.isVisible(), "rect": list(bar.geometry().getRect()),
                      "overlaps": [k for k, w in cards.items() if bar.overlaps(w)], "max": bar.maximum(),
                      "inner_max": page.scroll.verticalScrollBar().maximum()},
        "conditions_clipped": conditions_clipped(),
        "buttons_y": rect(page.pairs.add_btn)[1],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("size", help="WxH, or 'min' for the window's minimum size")
    ap.add_argument("--pairs", type=int, default=3)
    ap.add_argument("--palette", default="default")
    ap.add_argument("--shot", default="")
    a = ap.parse_args()
    home = setup_env()
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    from starextractor.core.session import Session
    from starextractor.ui import theme
    from starextractor.ui.main_window import MainWindow

    theme.apply(app, a.palette)
    win = MainWindow(session=Session.create(home / "pair"))
    win.settings["site"] = {"lat": 30.6714, "lon": -104.0225}  # McDonald Observatory
    win.show()
    win.show_tab("plan")
    page = win.ensure_plan()
    add_pairs(win, page, a.pairs)
    if a.pairs:
        page.pairs.load_for_acquisition(0)  # the first pair: In Acquisition
        win.show_tab("plan")
    size = (win.minimumWidth(), win.minimumHeight()) if a.size == "min" else tuple(int(v) for v in a.size.split("x"))
    win.resize(*size)
    spin(app, 10)
    for _ in range(200):  # the sky and the pairs' rows are computed in workers
        if page.conditions.idle() and page.pairs.idle():
            break
        spin(app, 1)
    spin(app, 5)
    page.setFocus()
    out = measure(win, page, app)
    if a.shot:
        (win if a.shot.endswith("_win.png") else page.scroll.widget()).grab().save(a.shot)
    print(json.dumps(out))
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
