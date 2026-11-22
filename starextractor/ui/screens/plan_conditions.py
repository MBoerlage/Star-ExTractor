"""Plan › Observing conditions: location, date, observation window and minimum altitude; the polar sky plot, the
altitude chart and one summary line (docs/PLAN_TAB.md).

The location is for planning only: it is kept in the settings (plan.location) and never written to a data file
(core/location.py). The default follows core/location.py: a location the user set here, else the app's site
(settings, the loaded pair's stack headers, the pair's Export fields), mirrored live until the user edits it.
"Use my location" is the only network call (a worker, 5 s). The sky is computed in a worker thread (core/sky.py,
a few ms), debounced by 150 ms; the UI thread only draws. "Now" moves once a minute while the card is visible.
"""
from __future__ import annotations

import threading
import time as _time
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QDate, QObject, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QDateEdit,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTimeEdit,
)

from starextractor.core import location as lc
from starextractor.core import sky

from ..widgets.alt_chart import AltitudeChart, ChartLegend
from ..widgets.common import Card, InfoButton, StatusLine, label
from ..widgets.flow import FlowLayout, add, unit
from ..widgets.sky_polar import NO_LOCATION, SkyPolar

if TYPE_CHECKING:
    from ..main_window import MainWindow

DEFAULT_FROM, DEFAULT_TO, DEFAULT_MIN_ALT = dtime(20, 0), dtime(6, 0), 25
DEBOUNCE_MS = 150
NOW_MS = 60_000
TIME_FMT = "HH:mm"


def tz_note(now: datetime | None = None) -> str:
    off = (now or datetime.now()).astimezone().utcoffset() or timedelta(0)
    sign = "+" if off >= timedelta(0) else "-"
    m = abs(int(off.total_seconds())) // 60
    return f"Times use your computer's time zone (UTC{sign}{m // 60:02d}:{m % 60:02d})"


def default_evening(start: dtime, end: dtime, now: datetime | None = None) -> datetime:
    """The evening the window starts: yesterday while last night's window is still running (after midnight),
    today until today's window ends, else tomorrow."""
    now = (now or datetime.now()).astimezone()
    for days in (-1, 0):
        day = now + timedelta(days=days)
        if now < sky.Window.from_local(day, start, end).end:
            return day
    return now + timedelta(days=1)


class _Bridge(QObject):
    """Results from worker threads, delivered on the GUI thread (queued)."""

    sky_done = Signal(int, object)
    site_done = Signal(int, object)
    geo_done = Signal(object)


class ConditionsCard(Card):
    computed = Signal()  # a new SkyPlan is shown: the planned pairs recompute their rows

    def __init__(self, host: MainWindow) -> None:
        super().__init__("Observing conditions")
        self.host = host
        self.head.insertWidget(1, InfoButton(
            "Where and when you observe, for planning only. The location stays in your settings and is never "
            "written to a logbook, FITS header, export or the observing plan. \"Use my location\" is the only "
            "internet request here, made only when you click it."))
        self.bridge = _Bridge()
        self.bridge.sky_done.connect(self._on_sky)
        self.bridge.site_done.connect(self._on_site)
        self.bridge.geo_done.connect(self._on_geo)
        self._gen = 0  # newest sky request; older results are dropped
        self._site_gen = 0
        self.site: lc.Location | None = None  # the app's site default (mirrored while the user has not set one)
        self.user: lc.Location | None = lc.user_location(self._plan_settings())
        self.plan: sky.SkyPlan | None = None
        self.summary_text = ""
        self.computing = False
        self.site_busy = False
        self.geo_busy = False
        self._loading = False
        self._build()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self.recompute)
        self._now_timer = QTimer(self)
        self._now_timer.setInterval(NOW_MS)
        self._now_timer.timeout.connect(self._tick_now)
        self._load_settings()
        self.refresh_site()
        host.pairs_changed.connect(self.refresh_site)
        host.library_changed.connect(self.refresh_site)

    # ---- construction ------------------------------------------------------------------------------------------
    def _build(self) -> None:
        """Two rows of fields that wrap when the column is narrow, the time zone line once under them; the polar
        plot and the altitude chart go in cards of their own (polar_card, chart_card) that the Plan screen places
        under this card."""
        row = FlowLayout()
        self.lat = QLineEdit()
        self.lat.setPlaceholderText("e.g. 30.67 N")
        self.lat.setToolTip("Degrees (30.67, 30.67 N) or degrees minutes seconds (30 40 17)")
        self.lon = QLineEdit()
        self.lon.setPlaceholderText("e.g. 104.02 W")
        self.lon.setToolTip("Degrees (-104.02, 104.02 W) or degrees minutes seconds (104 01 21 W)")
        for title, e in (("Latitude", self.lat), ("Longitude", self.lon)):
            e.setFixedWidth(120)
            e.editingFinished.connect(self._location_edited)
            add(row, unit(label(title, "muted"), e))
        self.source_tag = label("", "muted")
        self.geo_btn = QPushButton("↻ Use my location")
        self.geo_btn.setToolTip("One request to an internet location service (" + lc.GEO_URL + "): an approximate "
                                "position from your internet address. Nothing else is sent.")
        self.geo_btn.clicked.connect(self.use_my_location)
        add(row, self.geo_btn)
        add(row, self.source_tag)  # after the button: the tag wraps first when the column is narrow
        self.reset_btn = QPushButton("Reset to site default")
        self.reset_btn.setFlat(True)
        self.reset_btn.setProperty("role", "link")
        self.reset_btn.clicked.connect(self.reset_to_site)
        add(row, self.reset_btn)
        self.body.addLayout(row)

        row2 = FlowLayout()
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setToolTip("The evening the observation window starts")
        self.t_from, self.t_to = QTimeEdit(), QTimeEdit()
        for e in (self.t_from, self.t_to):
            e.setDisplayFormat(TIME_FMT)
        self.astro_btn = QPushButton("Astronomical night")
        self.astro_btn.setToolTip("From the end of evening astronomical twilight to its start in the morning "
                                  "(Sun at -18°) for this date and location")
        self.astro_btn.clicked.connect(self.set_astronomical_night)
        self.min_alt = QSpinBox()
        self.min_alt.setRange(0, 80)
        self.min_alt.setSuffix("°")
        for title, w in (("Date", self.date), ("From", self.t_from), ("To", self.t_to)):
            add(row2, unit(label(title, "muted"), w))
        add(row2, self.astro_btn)
        add(row2, unit(label("Min altitude", "muted"), self.min_alt))
        self.body.addLayout(row2)
        self.tz_label = label(tz_note(), "muted")
        self.body.addWidget(self.tz_label)
        self.message = StatusLine("err", "")
        self.message.hide()
        self.body.addWidget(self.message)

        self.summary = label("", "muted", wrap=True)
        self.body.addWidget(self.summary)

        self.polar_card = Card("Sky")
        self.polar_card.setObjectName("polarCard")
        self.polar = SkyPolar()
        self.polar_card.body.addStretch(1)  # the circle stays centred when the card is taller than the plot
        self.polar_card.body.addWidget(self.polar)
        self.polar_card.body.addStretch(1)
        self.chart_card = Card("Altitude")
        self.chart_card.setObjectName("chartCard")
        self.chart = AltitudeChart()
        self.legend = ChartLegend()
        self.chart_card.body.addWidget(self.chart, 1)  # the chart grows with the card, never below the plot's side
        self.chart_card.body.addWidget(self.legend)
        self.chart.setMinimumHeight(160)
        self.chart.setMaximumHeight(16777215)
        self.chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)  # fills the height
        self.polar.side_changed.connect(lambda side: self.chart.setMinimumHeight(max(side, 160)))
        for c in (self.polar_card, self.chart_card):
            c.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # widths by stretch: 45/55
        self.date.dateChanged.connect(self._window_changed)
        self.t_from.timeChanged.connect(self._window_changed)
        self.t_to.timeChanged.connect(self._window_changed)
        self.min_alt.valueChanged.connect(self._window_changed)

    # ---- settings ----------------------------------------------------------------------------------------------
    def _plan_settings(self) -> dict[str, Any]:
        return dict(self.host.settings["plan"] or {})

    def _save_plan_settings(self, **changes: Any) -> None:
        plan = self._plan_settings()
        for k, v in changes.items():
            if v is None:
                plan.pop(k, None)
            else:
                plan[k] = v
        self.host.settings["plan"] = plan
        self.host.settings.save()

    def _load_settings(self) -> None:
        self._loading = True
        p = self._plan_settings()
        start = QTime.fromString(str(p.get("from") or ""), TIME_FMT)
        end = QTime.fromString(str(p.get("to") or ""), TIME_FMT)
        start = start if start.isValid() else QTime(DEFAULT_FROM.hour, DEFAULT_FROM.minute)
        end = end if end.isValid() else QTime(DEFAULT_TO.hour, DEFAULT_TO.minute)
        self.t_from.setTime(start)
        self.t_to.setTime(end)
        self.min_alt.setValue(int(p.get("min_alt", DEFAULT_MIN_ALT)))
        ev = default_evening(dtime(start.hour(), start.minute()), dtime(end.hour(), end.minute()))
        self.date.setDate(QDate(ev.year, ev.month, ev.day))
        self._loading = False
        self._show_location()

    # ---- location ----------------------------------------------------------------------------------------------
    def current(self) -> lc.Location | None:
        return self.user or self.site

    def _show_location(self) -> None:
        loc = self.current()
        self._loading = True
        self.lat.setText("" if loc is None else f"{loc.lat:.4f}")
        self.lon.setText("" if loc is None else f"{loc.lon:.4f}")
        self._loading = False
        self.source_tag.setText(lc.SOURCES.get(loc.source, "") if loc else "")
        self.source_tag.setToolTip(lc.GEO_TOOLTIP if loc is not None and loc.source == "internet" else "")
        self.reset_btn.setVisible(self.user is not None and self.site is not None
                                  and not self.user.same_place(self.site))
        self.schedule()

    def refresh_site(self) -> None:
        """Look up the site default again (settings, the pair's stack headers, Export) in a worker."""
        self._site_gen += 1
        gen = self._site_gen
        self.site_busy = True
        settings = dict(self.host.settings.data)
        paths = self._stack_paths()
        export = (self.host.session.params.get("export") or {}).get("site") if isinstance(
            self.host.session.params.get("export"), dict) else None

        def run() -> None:
            try:
                found = lc.site_default(settings, paths, export)
            except Exception:  # noqa: BLE001 - an unreadable header: no default from it
                found = None
            self.bridge.site_done.emit(gen, found)

        threading.Thread(target=run, name="plan-site", daemon=True).start()

    def _stack_paths(self) -> list[Path]:
        from starextractor.core.pair_state import active_pair

        pair = active_pair(self.host.session) or {}
        return [Path(str(e["source"])) for e in (pair.get(r) or {} for r in ("target", "reference"))
                if e.get("source")]

    def _on_site(self, gen: int, found: lc.Location | None) -> None:
        if gen != self._site_gen:
            return
        self.site_busy = False
        self.site = found
        self._show_location()

    def _location_edited(self) -> None:
        if self._loading:
            return
        lat_t, lon_t = self.lat.text().strip(), self.lon.text().strip()
        if not lat_t and not lon_t:
            return
        try:
            lat, lon = lc.parse_angle(lat_t, "lat"), lc.parse_angle(lon_t, "lon")
        except lc.LocationError as e:
            self.message.set("err", f"Location: {e}. Use decimal degrees (30.67, -104.02), a direction (104.02 W) "
                                    "or degrees minutes seconds (104 01 21 W).")
            self.message.show()
            return
        self.message.hide()
        new = lc.Location(lat, lon, "manual")
        if new.same_place(self.current()):
            return
        self.set_user_location(new)

    def set_user_location(self, loc: lc.Location) -> None:
        """The user set the location (typed or the internet): stop mirroring the site default."""
        self.user = loc
        self._save_plan_settings(location=loc.to_settings())
        self._show_location()

    def reset_to_site(self) -> None:
        self.user = None
        self._save_plan_settings(location=None)
        self.message.hide()
        self._show_location()

    def use_my_location(self, lookup=None) -> None:
        """The one network call: in a worker, only on this click. Failure changes nothing."""
        if self.geo_busy:
            return
        self.geo_busy = True
        self.geo_btn.setEnabled(False)
        self.message.set("off", "Asking the location service…")
        self.message.show()
        fn = lookup or lc.lookup_ip_location

        def run() -> None:
            try:
                res: object = fn()
            except lc.LocationError as e:
                res = e
            except Exception as e:  # noqa: BLE001 - anything else is one short message too
                res = lc.LocationError(f"The location service could not be used ({type(e).__name__}).")
            self.bridge.geo_done.emit(res)

        threading.Thread(target=run, name="plan-geo", daemon=True).start()

    def _on_geo(self, res: object) -> None:
        self.geo_busy = False
        self.geo_btn.setEnabled(True)
        if isinstance(res, lc.Location):
            self.message.hide()
            self.set_user_location(res)
        else:
            self.message.set("err", f"{res} Your location was not changed.")
            self.message.show()

    # ---- window ------------------------------------------------------------------------------------------------
    def window(self) -> sky.Window:
        d, a, b = self.date.date(), self.t_from.time(), self.t_to.time()
        return sky.Window.from_local(datetime(d.year(), d.month(), d.day()), dtime(a.hour(), a.minute()),
                                     dtime(b.hour(), b.minute()))

    def _window_changed(self, *_a) -> None:
        if self._loading:
            return
        self._save_plan_settings(**{"from": self.t_from.time().toString(TIME_FMT),
                                    "to": self.t_to.time().toString(TIME_FMT), "min_alt": self.min_alt.value()})
        self.schedule()

    def set_astronomical_night(self) -> bool:
        loc = self.current()
        if loc is None:
            self.message.set("err", "Set your location first.")
            self.message.show()
            return False
        d = self.date.date()
        night = sky.astronomical_night(loc.lat, loc.lon, datetime(d.year(), d.month(), d.day()))
        if night is None:
            self.message.set("warn", "No astronomical night on this date here: the Sun stays above -18°.")
            self.message.show()
            return False
        self.message.hide()
        start, end = night
        self._loading = True
        self.t_from.setTime(QTime(start.hour, start.minute))
        self.t_to.setTime(QTime(end.hour, end.minute))
        self._loading = False
        self._window_changed()
        return True

    # ---- the sky -----------------------------------------------------------------------------------------------
    def target(self) -> tuple[float | None, float | None]:
        from starextractor.core import plan as pl

        t = pl.plan_state(self.host.session)["target"]
        return t.get("ra_deg"), t.get("dec_deg")

    def idle(self) -> bool:
        """Nothing pending: no debounce, no sky or site computation, no location request (tests, screenshots)."""
        return not (self._debounce.isActive() or self.computing or self.site_busy or self.geo_busy)

    def schedule(self) -> None:
        """Recompute after DEBOUNCE_MS (edits in a row cost one computation)."""
        self._debounce.start()

    def recompute(self) -> None:
        """Start the sky computation in a worker thread; the result arrives in _on_sky."""
        self._debounce.stop()
        self._gen += 1
        gen = self._gen
        loc = self.current()
        if loc is None:
            self.computing = False
            self._on_sky(gen, None)
            return
        ra, dec = self.target()
        w, min_alt = self.window(), float(self.min_alt.value())
        self.computing = True

        def run() -> None:
            try:
                plan = sky.compute(loc.lat, loc.lon, w, min_alt, ra, dec)
            except Exception:  # noqa: BLE001 - shown as no plot rather than an error dialog
                plan = None
            self.bridge.sky_done.emit(gen, plan)

        threading.Thread(target=run, name="plan-sky", daemon=True).start()

    def _on_sky(self, gen: int, plan: sky.SkyPlan | None) -> None:
        if gen != self._gen:
            return
        self.computing = False
        self.plan = plan
        now = _time.time()
        has_loc = self.current() is not None
        self.polar.set_plan(plan, now, has_loc)
        self.chart.set_plan(plan, now)
        self.summary_text = sky.summary(plan) if plan is not None else NO_LOCATION
        self.summary.setText(self.summary_text)
        self.host_plan_changed()
        self.computed.emit()

    def host_plan_changed(self) -> None:
        """The Observing plan block shows Date / Window / Visible: let the Plan screen redraw it."""
        screen = self.parent()
        while screen is not None and not hasattr(screen, "_show_plan"):
            screen = screen.parent()
        if screen is not None:
            screen._show_plan()

    def conditions(self) -> dict[str, Any] | None:
        """For the Observing plan text and JSON: never the location itself."""
        d = self.date.date()
        w = {"from": self.t_from.time().toString(TIME_FMT), "to": self.t_to.time().toString(TIME_FMT),
             "min_alt": self.min_alt.value()}
        visible = sky.visible_text(self.plan) if self.plan is not None else ""
        return {"date": d.toString("yyyy-MM-dd"), "window": w, "visible": visible or None}

    def _tick_now(self) -> None:
        now = _time.time()
        self.polar.set_now(now)
        self.chart.set_now(now)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._now_timer.start()
        self._tick_now()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._now_timer.stop()

    def retheme(self) -> None:
        self.chart.retheme()
        self.legend.retheme()
        self.polar.update()
