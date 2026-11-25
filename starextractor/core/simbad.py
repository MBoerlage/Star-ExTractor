"""SIMBAD name lookup with mirrors (stdlib only, docs/SPEC.md "SIMBAD endpoints and fallback").

Order: SIMBAD at CDS Strasbourg, the SIMBAD mirror at CfA Harvard (both with the same sim-script query), then the
Sesame name resolver at CDS and at CfA (names only). The server that answered last is tried first for the rest of the
session; the first server is tried again after RECHECK_S. A server that fails in any way (no connection, DNS, TLS,
timeout, a redirect, an HTTP status other than 200, an HTML page, an empty or unreadable answer) costs one try and the
next one is asked at once. "Not found" from a server that answers properly is final. Successful lookups are kept 30
days in simbad_cache.json; when every server fails, a kept result is used and marked as such.

Nothing goes online at import; the only requests are the ones a user click starts (Plan, Acquisition).
"""
from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .jobs import Cancelled

# ---- endpoints -----------------------------------------------------------------------------------------------------
SIMBAD_CDS = "https://simbad.cds.unistra.fr/simbad/"
SIMBAD_CFA = "https://simbad.cfa.harvard.edu/simbad/"
SESAME_CDS = "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxpF/S?"  # F: with magnitudes (V for the Plan tab)
SESAME_CFA = "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oxpF/S?"


@dataclass(frozen=True)
class Endpoint:
    key: str
    label: str  # shown to the user: "SIMBAD (CfA mirror)"
    kind: str  # script | sesame
    url: str
    site: str  # cds | cfa (the "SIMBAD server" setting)


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("cds", "CDS Strasbourg", "script", SIMBAD_CDS + "sim-script", "cds"),
    Endpoint("cfa", "CfA mirror", "script", SIMBAD_CFA + "sim-script", "cfa"),
    Endpoint("sesame-cds", "Sesame at CDS Strasbourg", "sesame", SESAME_CDS, "cds"),
    Endpoint("sesame-cfa", "Sesame at the CfA mirror", "sesame", SESAME_CFA, "cfa"),
)
BY_KEY = {e.key: e for e in ENDPOINTS}
SERVERS = {"auto": "Automatic", "cds": "CDS Strasbourg", "cfa": "CfA Harvard"}  # the setting

CONNECT_TIMEOUT_S = 4.0
READ_TIMEOUT_S = 10.0
BUDGET_S = 30.0  # one lookup, all servers together
RECHECK_S = 600.0  # try the first server again after this long on a mirror
CACHE_FILE = "simbad_cache.json"
CACHE_DAYS = 30
MAX_BODY = 1024 * 1024
USER_AGENT = "starextractor"
# One query, the same on every SIMBAD mirror: main id | RA Dec (deg) | object type | spectral type | V
SCRIPT = ('output console=off script=off\n'
          'format object "%IDLIST(1)|%COO(d;A D)|%OTYPE(S)|%SP(S)|%FLUXLIST(V;F)"\n'
          'query id {name}\n')


class LookupError_(RuntimeError):
    """No server answered sensibly (not 'not found')."""


class BadAnswer(Exception):
    """A server answered, but not with what this query expects: a failure, not 'not found'."""


@dataclass
class SimbadResult:
    query: str
    found: bool
    main_id: str = ""
    ra_deg: float | None = None
    dec_deg: float | None = None
    otype: str = ""
    sp_type: str = ""
    vmag: float | None = None
    aliases: list[str] = field(default_factory=list)

    @property
    def usable_name(self) -> str:
        return usable_name(self.main_id)


_PREFIX = re.compile(r"^(?:\*\*|\*|V\*|NAME|EM\*|Cl\*)\s+")


def usable_name(main_id: str) -> str:
    """SIMBAD main identifier -> a name that still resolves but is file/yaml friendly.

    '* bet Lyr' -> 'bet Lyr', 'V* V1500 Cyg' -> 'V1500 Cyg', 'NAME Sheliak' -> 'Sheliak'.
    """
    return re.sub(r"\s+", " ", _PREFIX.sub("", main_id.strip()))


def clean_name(name: str) -> str:
    """One line, no control characters: a name can never add a line to the SIMBAD script."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", name)).strip()


# ---- parsing -------------------------------------------------------------------------------------------------------
def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith(("<!doctype html", "<html")) or "<head" in head


# SIMBAD's words for a name it does not know (in the ::error:: section). Anything else there is a server problem.
_UNKNOWN_ID = re.compile(r"(?i)not found|incorrect format|no known catalog|unknown (?:identifier|catalog)")


def parse_script(text: str, query: str) -> SimbadResult:
    """The plain-text answer of the sim-script query above."""
    if not text.strip():
        raise BadAnswer("empty answer")
    if _looks_like_html(text):
        raise BadAnswer("an HTML page instead of data")
    if "::error::" in text:
        msgs = [ln.strip() for ln in text.split("::error::", 1)[1].splitlines() if ln.strip().strip(":")]
        if msgs and _UNKNOWN_ID.search(" ".join(msgs)):
            return SimbadResult(query, False)
        raise BadAnswer("an error from the server")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("::")]
    rows = [ln.split("|") for ln in lines if ln.count("|") == 4]
    if not rows or len(rows) != len(lines):
        raise BadAnswer("an answer in an unexpected format")
    main_id, coo, otype, sp, v = (p.strip() for p in rows[0])
    if not main_id:
        raise BadAnswer("an answer without an identifier")
    ra = dec = None
    if coo:
        parts = coo.split()
        try:
            ra, dec = float(parts[0]), float(parts[1])
        except (IndexError, ValueError) as exc:
            raise BadAnswer("coordinates in an unexpected format") from exc
    try:
        vmag = float(v) if v and v != "~" else None
    except ValueError:
        vmag = None
    return SimbadResult(query, True, main_id, ra, dec, otype if otype != "~" else "", sp if sp != "~" else "", vmag)


def parse_sesame(xml_text: str, query: str) -> SimbadResult:
    if not xml_text.strip():
        raise BadAnswer("empty answer")
    if _looks_like_html(xml_text):
        raise BadAnswer("an HTML page instead of data")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise BadAnswer("an unreadable answer") from exc
    res = root.find(".//Resolver")
    if res is None:
        target = root.find(".//Target")
        info = " ".join((e.text or "") for e in root.iter() if e.tag in ("INFO", "ERROR"))
        if target is not None and "nothing found" in info.lower() and root.find(".//ERROR") is None:
            return SimbadResult(query, False)
        raise BadAnswer("no result and no 'nothing found'")

    def txt(tag):
        e = res.find(tag)
        return e.text.strip() if e is not None and e.text else ""

    ra, de = txt("jradeg"), txt("jdedeg")
    v = res.find("mag[@band='V']/v")
    try:
        vmag = float(v.text) if v is not None and v.text else None
    except ValueError:
        vmag = None
    try:
        return SimbadResult(
            query=query, found=True, main_id=txt("oname"),
            ra_deg=float(ra) if ra else None, dec_deg=float(de) if de else None,
            otype=txt("otype"), sp_type=txt("spType"), vmag=vmag,
            aliases=[e.text.strip() for e in res.findall("alias") if e.text],
        )
    except ValueError as exc:
        raise BadAnswer("coordinates in an unexpected format") from exc


# ---- the request ---------------------------------------------------------------------------------------------------
def request_for(ep: Endpoint, name: str) -> tuple[str, str, bytes | None, dict[str, str]]:
    """(method, url, body, headers): the same query on every mirror of a kind."""
    q = clean_name(name)
    headers = {"User-Agent": USER_AGENT}
    if ep.kind == "script":
        body = urllib.parse.urlencode({"script": SCRIPT.format(name=q)}).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        return "POST", ep.url, body, headers
    return "GET", ep.url + urllib.parse.quote(q), None, headers


# (method, url, body, headers, connect timeout, read timeout) -> (HTTP status, body bytes); raises on network errors
Transport = Callable[[str, str, "bytes | None", dict[str, str], float, float], tuple[int, bytes]]


def http_transport(method: str, url: str, body: bytes | None, headers: dict[str, str], connect_timeout: float,
                   read_timeout: float) -> tuple[int, bytes]:
    """One HTTPS request: separate connect and read timeouts, redirects NOT followed (a redirect is a failure: the
    CDS Sesame address redirects to the CfA one, so following it would ask the same server twice)."""
    u = urllib.parse.urlsplit(url)
    conn = http.client.HTTPSConnection(u.hostname or "", u.port or 443, timeout=connect_timeout,
                                       context=ssl.create_default_context())
    try:
        conn.connect()
        if conn.sock is not None:
            conn.sock.settimeout(read_timeout)
        path = u.path + (f"?{u.query}" if u.query else "")
        conn.request(method, path, body=body, headers={**headers, "Connection": "close"})
        r = conn.getresponse()
        return r.status, r.read(MAX_BODY)
    finally:
        conn.close()


def failure_kind(exc: BaseException) -> str:
    """A short type for the log and the tests (never the server's text)."""
    if isinstance(exc, BadAnswer):
        return str(exc)
    if isinstance(exc, socket.gaierror) or "getaddrinfo" in str(exc):
        return "name lookup failed (DNS)"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timed out"
    if isinstance(exc, ConnectionRefusedError):
        return "connection refused"
    if isinstance(exc, ssl.SSLError):
        return "secure connection failed (TLS)"
    if isinstance(exc, (http.client.HTTPException, ValueError)):
        return "broken answer"
    if isinstance(exc, OSError):
        return "network error"
    return type(exc).__name__


def guarded(transport: Transport, cancel: Any = None, poll_s: float = 0.1) -> Transport:
    """Run each request on a daemon thread and wait for it here, checking the cancel token: cancelling a lookup or
    closing the app never waits on a socket (and DNS, which has no timeout of its own, is bounded too)."""

    def call(method, url, body, headers, connect_timeout, read_timeout):
        box: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            try:
                box["value"] = transport(method, url, body, headers, connect_timeout, read_timeout)
            except BaseException as exc:  # noqa: BLE001 - handed to the waiting side
                box["error"] = exc
            done.set()

        threading.Thread(target=run, name="simbad-request", daemon=True).start()
        limit = time.monotonic() + connect_timeout + read_timeout + 1.0
        while not done.wait(poll_s):
            if cancel is not None:
                cancel.check()
            if time.monotonic() > limit:
                raise TimeoutError("no answer in time")
        if "error" in box:
            raise box["error"]
        return box["value"]

    return call


# ---- the order of the servers --------------------------------------------------------------------------------------
_state: dict[str, Any] = {"last_good": None, "since": 0.0}  # this session only


def reset_memory() -> None:
    _state.update(last_good=None, since=0.0)


def order(server: str = "auto", now: float | None = None) -> list[Endpoint]:
    """The servers to ask, in order. A chosen server goes first (its Sesame too); with Automatic, the one that answered
    last goes first, until RECHECK_S has passed: then the normal order again, so the first server gets another go."""
    now = time.monotonic() if now is None else now
    base = list(ENDPOINTS)
    if server in ("cds", "cfa"):
        return sorted(base, key=lambda e: (e.kind != "script", e.site != server))
    last = _state["last_good"]
    if last and last != base[0].key and now - _state["since"] < RECHECK_S:
        return sorted(base, key=lambda e: e.key != last)
    return base


def _remember(ep: Endpoint, now: float, first_failed: bool) -> None:
    """The server that answered goes first from now on; when the first server was just tried again and failed, the
    10 minutes start over."""
    if _state["last_good"] != ep.key or first_failed:
        _state.update(last_good=ep.key, since=now)


# ---- cache ---------------------------------------------------------------------------------------------------------
def cache_path() -> Path:
    from .settings import app_dir

    return app_dir() / CACHE_FILE


def cache_key(name: str) -> str:
    return clean_name(name).lower()


def cache_get(name: str, path: Path | None = None, now: float | None = None) -> dict[str, Any] | None:
    path = path or cache_path()
    try:
        hit = json.loads(path.read_text(encoding="utf-8")).get(cache_key(name))
    except (OSError, ValueError, AttributeError):
        return None
    now = time.time() if now is None else now
    if not isinstance(hit, dict) or now - float(hit.get("saved_at", 0)) > CACHE_DAYS * 86400:
        return None
    return hit


def cache_put(name: str, r: SimbadResult, server: str, path: Path | None = None, now: float | None = None) -> None:
    from .settings import write_json_atomic

    path = path or cache_path()
    now = time.time() if now is None else now
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data = {k: v for k, v in data.items()
            if isinstance(v, dict) and now - float(v.get("saved_at", 0)) <= CACHE_DAYS * 86400}
    data[cache_key(name)] = {**asdict(r), "server": server, "saved_at": now,
                             "saved": time.strftime("%Y-%m-%d", time.localtime(now))}
    try:
        write_json_atomic(path, data)
    except OSError:
        pass  # a cache that cannot be written is no reason to fail the lookup


# ---- the lookup ----------------------------------------------------------------------------------------------------
@dataclass
class Attempt:
    server: str  # endpoint key
    outcome: str  # "answered" | "not found" | a failure kind
    seconds: float


@dataclass
class Lookup:
    result: SimbadResult | None  # None: no server answered
    server: str = ""  # endpoint key that answered
    attempts: list[Attempt] = field(default_factory=list)


def lookup_detailed(name: str, transport: Transport | None = None, server: str = "auto",
                    clock: Callable[[], float] = time.monotonic, budget_s: float = BUDGET_S,
                    unreachable: tuple[str, ...] = (), cancel: Any = None) -> Lookup:
    """Ask the servers in order until one answers properly. Each server at most once; stops when the budget is
    spent. `unreachable` (endpoint keys or 'cds' / 'cfa') makes servers fail at once: a debug setting to watch the
    fallback (settings simbad.debug_unreachable)."""
    q = clean_name(name)
    out = Lookup(None)
    if not q:
        out.result = SimbadResult(q, False)
        return out
    transport = transport or http_transport
    start = clock()
    for ep in order(server, start):
        if cancel is not None:
            cancel.check()
        left = budget_s - (clock() - start)
        if left <= 0:
            break
        t0 = clock()
        try:
            if ep.key in unreachable or ep.site in unreachable:
                raise ConnectionRefusedError("made unreachable by the debug setting")
            method, url, body, headers = request_for(ep, q)
            status, data = transport(method, url, body, headers, min(CONNECT_TIMEOUT_S, left),
                                     min(READ_TIMEOUT_S, left))
            if status != 200:
                raise BadAnswer(f"HTTP {status}")
            text = data.decode("utf-8", errors="replace")
            result = parse_script(text, q) if ep.kind == "script" else parse_sesame(text, q)
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - every failure moves on to the next server
            out.attempts.append(Attempt(ep.key, failure_kind(exc), clock() - t0))
            continue
        out.attempts.append(Attempt(ep.key, "answered" if result.found else "not found", clock() - t0))
        out.result, out.server = result, ep.key
        _remember(ep, clock(), any(a.server == ENDPOINTS[0].key for a in out.attempts[:-1]))
        return out
    return out


def lookup(name: str, timeout: float | None = None, fetch: Transport | None = None, **kw: Any) -> SimbadResult:
    """Resolve `name` (SimbadResult; found False when the name is unknown). Raises LookupError_ when no server
    answered. `timeout` is ignored (the timeouts are the constants above); kept for older callers."""
    r = lookup_detailed(name, transport=fetch, **kw)
    if r.result is None:
        raise LookupError_("SIMBAD lookup failed - " + "; ".join(f"{a.server}: {a.outcome}" for a in r.attempts))
    return r.result


def fixed_answer(result: SimbadResult | None, server: str = "cds",
                 attempts: list[Attempt] | None = None) -> Callable[..., Lookup]:
    """A stand-in for lookup_detailed that answers at once (tests and screenshots; never the network). None: no
    server answered."""
    def answer(*_a: Any, **_k: Any) -> Lookup:
        if result is None:
            return Lookup(None, "", attempts if attempts is not None else [Attempt("cds", "timed out", 4.0)])
        return Lookup(result, server, attempts if attempts is not None else [Attempt(server, "answered", 0.3)])
    return answer


# ---- what Plan and Acquisition run ---------------------------------------------------------------------------------
def prefs(settings: Any) -> dict[str, Any]:
    """The 'simbad' settings: server (auto | cds | cfa) and the hidden debug list."""
    try:
        s = settings["simbad"] or {}
    except (KeyError, TypeError):
        s = {}
    server = s.get("server", "auto") if s.get("server") in SERVERS else "auto"
    bad = s.get("debug_unreachable") or []
    return {"server": server, "unreachable": tuple(str(x) for x in bad) if isinstance(bad, list) else ()}


def _as_dict(name: str, r: SimbadResult) -> dict[str, Any]:
    return {"query": name, "found": True, "main_id": r.main_id, "name": r.usable_name, "ra": r.ra_deg,
            "dec": r.dec_deg, "sp_type": r.sp_type, "otype": r.otype, "vmag": r.vmag}


def log_line(res: dict[str, Any]) -> str:
    """Which server answered, each failure by type, and the times. Never the name that was typed."""
    parts = [f"{BY_KEY[a['server']].label}: {a['outcome']} ({a['seconds']:.1f} s)" for a in res.get("attempts", [])
             if a.get("server") in BY_KEY]
    head = ("SIMBAD: answered by " + BY_KEY[res["server"]].label if res.get("server") in BY_KEY else
            "SIMBAD: used a saved result" if res.get("cached") else "SIMBAD: no server answered")
    return head + (" · " + "; ".join(parts) if parts else "")


def simbad_job(ctx: Any, name: str, settings_prefs: dict[str, Any] | None = None,
               transport: Transport | None = None, cache_file: Path | None = None,
               budget_s: float = BUDGET_S) -> dict[str, Any]:
    """Runs in a worker (Plan, Acquisition). Never raises for a network problem: the dict says what happened.

    Keys: query, found, main_id, name, ra, dec, sp_type, otype, vmag (when found); server, server_label, attempts,
    cached / saved (a kept result), offline + error (no answer and nothing kept)."""
    p = settings_prefs or {}
    cancel = getattr(ctx, "cancel", None)
    t = guarded(transport or http_transport, cancel)
    r = lookup_detailed(name, t, p.get("server", "auto"), budget_s=budget_s,
                        unreachable=tuple(p.get("unreachable", ())), cancel=cancel)
    attempts = [{"server": a.server, "outcome": a.outcome, "seconds": round(a.seconds, 2)} for a in r.attempts]
    if r.result is not None:
        ep = BY_KEY[r.server]
        out: dict[str, Any] = (_as_dict(name, r.result) if r.result.found else {"query": name, "found": False})
        out.update(server=ep.key, server_label=ep.label, source=ep.kind, attempts=attempts)
        if r.result.found:
            cache_put(name, r.result, ep.key, cache_file)
        return out
    hit = cache_get(name, cache_file)
    if hit is not None and hit.get("found"):
        res = SimbadResult(**{k: hit.get(k) for k in SimbadResult.__dataclass_fields__})
        return {**_as_dict(name, res), "cached": True, "saved": hit.get("saved", ""), "attempts": attempts}
    return {"query": name, "found": False, "offline": True, "attempts": attempts,
            "error": "; ".join(f"{a['server']}: {a['outcome']}" for a in attempts) or "no server"}


def source_label(res: dict[str, Any]) -> str:
    """'SIMBAD (CDS Strasbourg)', 'SIMBAD (CfA mirror)', 'SIMBAD via Sesame at CDS Strasbourg', or 'SIMBAD'."""
    ep = BY_KEY.get(str(res.get("server") or ""))
    if ep is None:
        return "SIMBAD"
    return f"SIMBAD via {ep.label}" if ep.kind == "sesame" else f"SIMBAD ({ep.label})"


CACHED = "SIMBAD: all servers unreachable; using the saved result{when}."
UNREACHABLE = "SIMBAD unreachable. You can enter the coordinates by hand."


def cached_text(res: dict[str, Any]) -> str:
    return CACHED.format(when=f" from {res['saved']}" if res.get("saved") else " from an earlier lookup")
