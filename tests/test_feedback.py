"""core/feedback.py: redaction, payload v1, send() against a fake transport (never the live relay), the zip."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from starextractor.core import feedback as fb

NAMES = [("jdoe-test", "<user>"), ("STARLAB-PC", "<host>")]


@pytest.fixture(autouse=True)
def fake_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """A made-up user and computer, so the tests do not depend on (or print) the real ones."""
    monkeypatch.setenv("USERNAME", "jdoe-test")
    monkeypatch.setenv("USER", "jdoe-test")
    monkeypatch.setenv("COMPUTERNAME", "STARLAB-PC")
    monkeypatch.setattr(fb.socket, "gethostname", lambda: "starlab-pc.local")


# ---- 1. redaction --------------------------------------------------------------------------------------------------
def test_paths_names_and_emails_are_redacted() -> None:
    log = ("12:00  Select: copied C:\\Users\\jdoe-test\\Spectra\\a.fit → work\n"
           "12:01  json: \"C:\\\\Users\\\\jdoe-test\\\\x.fit\" and C:/Users/jdoe-test/y.fit and d:\\users\\Other\\z\n"
           "12:02  /Users/jdoe-test/data and /home/jdoe-test/.cache, user jdoe-test on STARLAB-PC (starlab-pc)\n"
           "12:03  contact someone.else@example.org, a@b.co\n")
    out = fb.redact(log, emails=True)
    assert "jdoe-test" not in out and "STARLAB" not in out.upper() and "Other" not in out
    assert "C:\\Users\\<user>\\Spectra\\a.fit" in out and "C:/Users/<user>/y.fit" in out
    assert "C:\\\\Users\\\\<user>\\\\x.fit" in out and "d:\\users\\<user>\\z" in out
    assert "/Users/<user>/data" in out and "/home/<user>/.cache" in out
    assert "user <user> on <host> (<host>)" in out
    assert "<email>, <email>" in out and "@" not in out


def test_message_keeps_typed_emails_and_short_names_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = "Please reply to me@example.net; my files are in C:\\Users\\jdoe-test\\Night"
    out = fb.redact(msg)
    assert "me@example.net" in out and "C:\\Users\\<user>\\Night" in out
    monkeypatch.setenv("USERNAME", "al")  # too short: would damage words such as "also" or "final"
    monkeypatch.setenv("USER", "al")
    assert fb.redact("also al final") == "also al final"
    # whole words only: a name inside another word is left alone
    assert fb.redact("jdoe-testing and jdoe-test", names=NAMES) == "jdoe-testing and <user>"


# ---- 2. payload ----------------------------------------------------------------------------------------------------
def test_payload_v1_exact_keys_and_values() -> None:
    p = fb.build_payload("Problem", "  It crashed in C:\\Users\\jdoe-test\\x  ", "", True,
                         "line one at C:\\Users\\jdoe-test\\a\nmail x@y.org\n")
    assert list(p) == list(fb.KEYS) and p["v"] == 1 and p["website"] == "" and p["type"] == "problem"
    assert p["message"] == "It crashed in C:\\Users\\<user>\\x" and p["email"] == ""
    assert p["log"] == "line one at C:\\Users\\<user>\\a\nmail <email>\n"
    assert p["app_version"] and p["windows"]
    assert fb.build_payload("idea", "x", "me@example.net", True, "")["log"] == ""
    assert fb.build_payload("idea", "x", "", False, "a log")["log"] == ""
    for bad in (("problem", "   ", ""), ("nope", "x", ""), ("idea", "x", "not-an-address"),
                ("idea", "x" * (fb.MESSAGE_MAX + 1), "")):
        with pytest.raises(ValueError):
            fb.build_payload(bad[0], bad[1], bad[2], False)


def test_message_never_trimmed_log_tail_kept_and_size_fits() -> None:
    msg = "é" * fb.MESSAGE_MAX  # two bytes each in UTF-8
    log = "\n".join(f"{i:07d} C:\\Users\\jdoe-test\\file{i}.fit on STARLAB-PC" for i in range(20_000))
    p = fb.build_payload("problem", msg, "me@example.net", True, log)
    assert p["message"] == msg  # never trimmed
    assert len(p["log"]) <= fb.LOG_MAX_CHARS and p["log"].endswith("file19999.fit on <host>")
    body = fb.body_of(p)
    assert len(body) < fb.BODY_MAX_BYTES
    text = body.decode("utf-8")
    assert "jdoe-test" not in text and "STARLAB" not in text.upper() and "Users\\\\jdoe" not in text


# ---- 3. send() -----------------------------------------------------------------------------------------------------
class FakeTransport:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, bytes, dict, float]] = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers, timeout))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def _payload() -> dict:
    return fb.build_payload("question", "Hello", "", False)


def test_200_returns_the_reference_and_the_request_is_right() -> None:
    t = FakeTransport((200, b'{"ok": true, "ref": "FB-A1B2C3"}'))
    sleeps: list[float] = []
    r = fb.send(_payload(), t, sleeps.append)
    assert r.ok and r.ref == "FB-A1B2C3" and r.kind == "sent" and sleeps == []
    url, body, headers, timeout = t.calls[0]
    assert url == fb.RELAY_URL == "https://starex-relay.starex-tools.workers.dev/feedback"
    assert timeout == fb.TIMEOUT_S == 15
    assert headers["Content-Type"] == "application/json" and headers["Content-Length"] == str(len(body))
    assert headers["User-Agent"] == f"StarExReduce/{fb.app_version()}"
    assert json.loads(body) == _payload()


@pytest.mark.parametrize(("status", "kind"), [(400, "invalid"), (411, "too_large"), (413, "too_large"),
                                              (429, "busy"), (404, "error")])
def test_4xx_map_and_are_not_retried(status: int, kind: str) -> None:
    t = FakeTransport((status, b'{"ok": false, "error": "server words never shown"}'))
    r = fb.send(_payload(), t, lambda _s: None)
    assert not r.ok and r.kind == kind and len(t.calls) == 1
    assert "server words" not in r.text and r.text == fb.MESSAGES[kind]
    if kind == "too_large":
        assert "without the application log" in r.text
    if kind == "busy":
        assert "too many messages right now" in r.text.lower()


@pytest.mark.parametrize(("status", "kind"), [(503, "unavailable"), (502, "error"), (500, "error")])
def test_5xx_retried_once_after_two_seconds(status: int, kind: str) -> None:
    sleeps: list[float] = []
    t = FakeTransport((status, b""), (status, b"<html>"))
    r = fb.send(_payload(), t, sleeps.append)
    assert not r.ok and r.kind == kind and len(t.calls) == 2 and sleeps == [2.0]
    t = FakeTransport((status, b""), (200, b'{"ok": true, "ref": "FB-ZZ9999"}'))
    assert fb.send(_payload(), t, lambda _s: None).ref == "FB-ZZ9999"


def test_network_error_retried_once() -> None:
    t = FakeTransport(OSError("no route"), TimeoutError("slow"))
    r = fb.send(_payload(), t, lambda _s: None)
    assert not r.ok and r.kind == "network" and len(t.calls) == 2
    t = FakeTransport(OSError("no route"), (200, b'{"ok": true, "ref": "FB-000001"}'))
    assert fb.send(_payload(), t, lambda _s: None).ok


def test_odd_200_answers_are_errors() -> None:
    for data in (b"not json", b'{"ok": true}', b'{"ok": true, "ref": "nope"}', b'{"ok": false, "ref": "FB-AAAAAA"}'):
        r = fb.send(_payload(), FakeTransport((200, data)), lambda _s: None)
        assert not r.ok and r.kind == "error"


# ---- zip -----------------------------------------------------------------------------------------------------------
def test_zip_holds_the_payload_the_log_and_a_readme(tmp_path: Path) -> None:
    p = fb.build_payload("problem", "It failed", "me@example.net", True, "a log line\n")
    path = fb.save_fallback_zip(tmp_path / "out" / "starex-feedback-2026-11-24", p)
    assert path.name == "starex-feedback-2026-11-24.zip"
    with zipfile.ZipFile(path) as z:
        assert sorted(z.namelist()) == ["README.txt", "feedback.json", "log.txt"]
        assert json.loads(z.read("feedback.json")) == p
        assert z.read("log.txt").decode() == p["log"]
        readme = z.read("README.txt").decode()
    assert "User Feedback" in readme and "@" not in readme
    p2 = fb.build_payload("idea", "No log", "", False)
    with zipfile.ZipFile(fb.save_fallback_zip(tmp_path / "b.zip", p2)) as z:
        assert sorted(z.namelist()) == ["README.txt", "feedback.json"]
    assert fb.default_zip_name().startswith("starex-feedback-") and fb.default_zip_name().endswith(".zip")


def test_application_log_joins_panel_lines_and_error_log(tmp_path: Path) -> None:
    err = tmp_path / "error.log"
    err.write_text("--- 2026\nTraceback ...\n", encoding="utf-8")
    text = fb.application_log(["12:00  one", "12:01  two"], err)
    assert text.startswith("12:00  one\n12:01  two\n--- error.log\n") and "Traceback" in text
    assert fb.application_log([], tmp_path / "missing.log") == ""
