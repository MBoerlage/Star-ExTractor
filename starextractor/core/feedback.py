"""User Feedback (Help › User Feedback): the payload, its redaction, sending it to the relay and the zip fallback.
No Qt. The only network request is `send()`, run when the user presses Send (docs/FEEDBACK.md).

The relay (outside this repository) validates payload v1, files it as a private GitHub issue and answers with a
reference. There is no developer e-mail address anywhere in the app: the user's own optional address is the only one
that can appear, and only in the "email" field or in the message they typed.
"""
from __future__ import annotations

import json
import os
import platform
import re
import socket
import sys
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RELAY_URL = "https://starex-relay.starex-tools.workers.dev/feedback"
MESSAGE_MAX = 5000
LOG_MAX_CHARS = 150_000  # the TAIL of the log is kept; the relay refuses logs above 200,000 characters
BODY_MAX_BYTES = 256 * 1024  # the relay refuses bodies above this
TIMEOUT_S = 15
RETRY_AFTER_S = 2.0
TYPES = ("problem", "idea", "question", "other")
KEYS = ("v", "type", "message", "email", "app_version", "windows", "log", "website")
REF_RE = re.compile(r"^FB-[A-Z0-9]{6}$")
# "Looks like an address": a name, the at sign and a domain with a dot. The relay checks again.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ---- redaction -----------------------------------------------------------------------------------------------------
_WIN_USER = re.compile(r"(?i)\b([A-Z]:(?:\\\\|\\|/)+Users(?:\\\\|\\|/)+)([^\\/\s\"'<>|:*?]+)")
_POSIX_USER = re.compile(r"(?<![A-Za-z0-9_.])(/(?:Users|home)/)([^/\s\"'<>|:*?]+)")
_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
MIN_NAME_LEN = 3  # a user or computer name shorter than this is not replaced: it would damage ordinary words


def _names() -> list[tuple[str, str]]:
    """(name, placeholder) of the Windows user and the computer, longest first."""
    out: list[tuple[str, str]] = []
    host = socket.gethostname() or ""
    for name, tag in ((os.environ.get("USERNAME", ""), "<user>"), (os.environ.get("USER", ""), "<user>"),
                      (os.environ.get("COMPUTERNAME", ""), "<host>"), (host, "<host>"),
                      (host.split(".")[0], "<host>")):
        name = name.strip()
        if len(name) >= MIN_NAME_LEN and (name, tag) not in out:
            out.append((name, tag))
    return sorted(out, key=lambda nt: -len(nt[0]))


def redact(text: str, emails: bool = False, names: list[tuple[str, str]] | None = None) -> str:
    """Folder paths under a user's home → <user>; the Windows user name and the computer name → <user> / <host>
    (as whole words, case ignored); with `emails`, e-mail addresses → <email> (used for the log only: an address the
    user types in the message is theirs to share)."""
    if not text:
        return ""
    text = _WIN_USER.sub(lambda m: m.group(1) + "<user>", text)
    text = _POSIX_USER.sub(lambda m: m.group(1) + "<user>", text)
    if emails:
        text = _EMAIL_IN_TEXT.sub("<email>", text)
    for name, tag in (_names() if names is None else names):
        text = re.sub(rf"(?i)(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", tag, text)
    return text


# ---- the payload ---------------------------------------------------------------------------------------------------
def app_version() -> str:
    from starextractor import __version__

    return str(__version__)


def windows_version() -> str:
    """'Windows 11 10.0.26100' (build 22000 and up is Windows 11), or the platform string elsewhere."""
    if sys.platform == "win32":
        v = sys.getwindowsversion()
        major = "11" if v.major == 10 and v.build >= 22000 else platform.release()
        return f"Windows {major} {v.major}.{v.minor}.{v.build}"
    return platform.platform(terse=True)


def tail(text: str, limit: int = LOG_MAX_CHARS) -> str:
    """The last `limit` characters, starting at a line when possible."""
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    nl = cut.find("\n")
    return cut[nl + 1:] if 0 <= nl < 200 else cut


def build_payload(kind: str, message: str, email: str, include_log: bool, log_text: str = "") -> dict[str, Any]:
    """Payload v1 with the redacted message and, when asked, the redacted tail of the application log. The message is
    never trimmed: an empty or too long one raises ValueError."""
    kind = kind.lower()
    if kind not in TYPES:
        raise ValueError(f"unknown type {kind!r}")
    msg = redact(message.strip())
    if not msg:
        raise ValueError("the message is empty")
    if len(msg) > MESSAGE_MAX:
        raise ValueError(f"the message is longer than {MESSAGE_MAX} characters")
    email = email.strip()
    if email and not EMAIL_RE.match(email):
        raise ValueError("the email address does not look like one")
    log = tail(redact(log_text, emails=True)) if include_log and log_text else ""
    return {"v": 1, "type": kind, "message": msg, "email": email, "app_version": app_version(),
            "windows": windows_version(), "log": log, "website": ""}


def body_of(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def preview(payload: dict[str, Any]) -> str:
    """The exact JSON that would be posted, indented for reading (the body itself is compact)."""
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---- sending -------------------------------------------------------------------------------------------------------
@dataclass
class SendResult:
    ok: bool
    ref: str = ""
    kind: str = ""  # sent | invalid | too_large | busy | unavailable | network | error
    text: str = ""  # a plain explanation for the UI (never the server's text)
    status: int | None = None


MESSAGES = {
    "invalid": "The feedback service did not accept the message. Check the email address and try again.",
    "too_large": "The message is too large to send. Try again without the application log.",
    "busy": "Too many messages right now, please try later.",
    "unavailable": "The feedback service is temporarily unavailable. Please try again later.",
    "network": "Could not reach the feedback service (no internet connection?).",
    "error": "The feedback service had a problem. Please try again later.",
}

# (url, body, headers, timeout) -> (HTTP status, response body); raises OSError for a network error
Transport = Callable[[str, bytes, dict[str, str], float], tuple[int, bytes]]


def urllib_transport(url: str, body: bytes, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(64 * 1024)
    except urllib.error.HTTPError as e:
        return e.code, (e.read(64 * 1024) if e.fp else b"")


def _result(status: int, data: bytes) -> SendResult:
    if status == 200:
        try:
            ans = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            ans = {}
        ref = str(ans.get("ref") or "") if isinstance(ans, dict) else ""
        if isinstance(ans, dict) and ans.get("ok") and REF_RE.match(ref):
            return SendResult(True, ref, "sent", "", status)
        return SendResult(False, "", "error", MESSAGES["error"], status)
    kind = {400: "invalid", 411: "too_large", 413: "too_large", 429: "busy", 503: "unavailable"}.get(status, "error")
    return SendResult(False, "", kind, MESSAGES[kind], status)


def send(payload: dict[str, Any], transport: Transport | None = None,
         sleep: Callable[[float], None] = time.sleep) -> SendResult:
    """POST the payload as JSON (Content-Length set, 15 s timeout). One retry after 2 s on a network error or an
    HTTP 5xx; none on a 4xx."""
    transport = transport or urllib_transport
    body = body_of(payload)
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body)),
               "User-Agent": f"StarExReduce/{app_version()}"}
    if len(body) > BODY_MAX_BYTES:
        return SendResult(False, "", "too_large", MESSAGES["too_large"], None)
    result = SendResult(False, "", "network", MESSAGES["network"])
    for attempt in range(2):
        if attempt:
            sleep(RETRY_AFTER_S)
        try:
            status, data = transport(RELAY_URL, body, headers, TIMEOUT_S)
        except (OSError, TimeoutError):
            result = SendResult(False, "", "network", MESSAGES["network"])
            continue
        result = _result(status, data)
        if result.ok or status < 500:
            return result
    return result


# ---- the zip fallback ----------------------------------------------------------------------------------------------
README = """Star ExTractor feedback that could not be sent

This file holds a message written in Help > User Feedback that could not be sent at the time.

  feedback.json  the exact message that would have been sent (type, your text, your optional email, the app and
                 Windows version, and the application log if you attached it)
  log.txt        the application log, with folder paths, your user name, computer name and email addresses
                 removed (only when you attached it)

Keep this file. When you are online again, open Help > User Feedback in Star ExTractor and send a new message
(you can paste your text from feedback.json).
"""


def default_zip_name(when: time.struct_time | None = None) -> str:
    return f"starex-feedback-{time.strftime('%Y-%m-%d', when or time.localtime())}.zip"


def save_fallback_zip(path: Path | str, payload: dict[str, Any]) -> Path:
    path = Path(path)
    if path.suffix.lower() != ".zip":
        path = path.with_suffix(".zip")
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("feedback.json", preview(payload) + "\n")
        if payload.get("log"):
            z.writestr("log.txt", payload["log"])
        z.writestr("README.txt", README)
    return path


def application_log(lines: list[str] | None, error_log: Path | None = None) -> str:
    """The application log as the app keeps it: the Log panel's lines (this pair) and, when it exists, the end of
    error.log (unexpected errors)."""
    parts = ["\n".join(lines or [])]
    if error_log is not None and error_log.exists():
        try:
            parts.append("--- error.log\n" + tail(error_log.read_text(encoding="utf-8", errors="replace"), 50_000))
        except OSError:
            pass
    return "\n".join(p for p in parts if p.strip())
