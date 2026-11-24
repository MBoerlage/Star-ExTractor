"""Help › User Feedback: the form, validation, sending in a worker (fake transport only, never the live relay), the
cooldown, "See exactly what is sent", the zip on failure, palettes, no network until Send, no developer address."""
from __future__ import annotations

import json
import re
import socket
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from starextractor.core import feedback as fb
from starextractor.core.session import Session
from starextractor.ui import feedback_tab as ft
from starextractor.ui import theme
from starextractor.ui.main_window import MainWindow

ROOT = Path(__file__).resolve().parents[1]
OK = (200, b'{"ok": true, "ref": "FB-T3ST01"}')


class Fake:
    """Stands in for urllib: records every call, answers from a list."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[tuple] = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers, timeout))
        a = self.answers.pop(0) if self.answers else OK
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    monkeypatch.setattr(fb, "urllib_transport", f)  # send() picks it up as its default transport
    monkeypatch.setattr(fb, "RETRY_AFTER_S", 0.0)
    monkeypatch.setenv("USERNAME", "jdoe-test")
    monkeypatch.setenv("COMPUTERNAME", "STARLAB-PC")
    return f


@pytest.fixture
def win(qtbot: QtBot, tmp_path: Path) -> MainWindow:
    theme.apply(QApplication.instance(), "default")
    w = MainWindow(session=Session.create(tmp_path / "pair"))
    qtbot.addWidget(w)
    w.show()
    w.session.log("Select: copied C:\\Users\\jdoe-test\\Spectra\\a.fit on STARLAB-PC")
    return w


def _tab(win: MainWindow) -> ft.FeedbackTab:
    dlg = win.open_help()
    dlg.show_feedback()
    return dlg.feedback


def test_tab_fields_counter_and_validation(win: MainWindow) -> None:
    dlg = win.open_help()
    assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == ["Guide", "User Feedback"]
    t = _tab(win)
    assert dlg.tabs.currentWidget() is t and not dlg.find.isVisible()
    assert [b.text() for b in t.type.buttons] == ["Problem", "Idea", "Question", "Other"]
    assert t.kind() == "problem" and t.attach.isChecked()
    t.type.set_index(1)
    assert t.kind() == "idea" and not t.attach.isChecked()  # the default follows the type ...
    t.type.set_index(0)
    assert t.attach.isChecked()
    t.attach.setChecked(False)
    t.type.set_index(2)
    t.type.set_index(0)
    assert not t.attach.isChecked()  # ... until the user ticks or unticks it
    assert not t.send_btn.isEnabled() and t.counter.text() == "0 / 5000"
    t.message.setPlainText("Hello")
    assert t.send_btn.isEnabled() and t.counter.text() == "5 / 5000"
    t.email.setText("not an address")
    assert not t.send_btn.isEnabled() and t.email_hint.isVisible()
    t.email.setText("me@example.net")
    assert t.send_btn.isEnabled() and not t.email_hint.isVisible()
    t.message.setPlainText("x" * (fb.MESSAGE_MAX + 1))
    assert not t.send_btn.isEnabled() and "too long" in t.counter.text()
    assert t.privacy.text() == ft.PRIVACY and "12 months" in ft.PRIVACY and "type Question" in ft.PRIVACY
    assert t.email.parentWidget() is t and not hasattr(t, "website")  # the honeypot is never shown


def test_see_exactly_what_is_sent_is_the_payload(win: MainWindow, fake: Fake) -> None:
    t = _tab(win)
    t.message.setPlainText("It crashed in C:\\Users\\jdoe-test\\x")
    dlg = t.show_what_is_sent()
    shown = json.loads(dlg.preview_text.toPlainText())
    assert shown == t.payload() and list(shown) == list(fb.KEYS) and shown["website"] == ""
    assert "jdoe-test" not in dlg.preview_text.toPlainText() and "STARLAB" not in dlg.preview_text.toPlainText()
    assert "C:\\Users\\<user>\\Spectra" in shown["log"] and dlg.preview_text.isReadOnly()
    dlg.close()
    assert fake.calls == []  # looking is not sending


def test_send_in_a_worker_then_cooldown(win: MainWindow, qtbot: QtBot, fake: Fake,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    assert ft.COOLDOWN_MS == 30_000
    monkeypatch.setattr(ft, "COOLDOWN_MS", 400)
    t = _tab(win)
    t.message.setPlainText("An idea")
    assert t.send()
    assert t.sending and not t.send_btn.isEnabled() and t.status.text.text() == "Sending…"
    qtbot.waitUntil(lambda: not t.sending, timeout=5000)
    assert t.status.state == "ok"
    assert t.status.text.text() == "Thank you. Your reference is FB-T3ST01; quote it if you write to us again."
    assert len(fake.calls) == 1 and json.loads(fake.calls[0][1]) == t.sent_payload
    assert fake.calls[0][3] == fb.TIMEOUT_S
    t.message.setPlainText("Another one at once")
    assert not t.send_btn.isEnabled() and t.cooldown.isActive() and not t.send()
    qtbot.waitUntil(lambda: t.send_btn.isEnabled(), timeout=3000)


def test_ctrl_enter_sends_only_a_valid_form(win: MainWindow, qtbot: QtBot, fake: Fake) -> None:
    t = _tab(win)
    t.message.setFocus()
    qtbot.keyClick(t.message, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert fake.calls == [] and not t.sending
    t.message.setPlainText("Valid now")
    qtbot.keyClick(t.message, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: len(fake.calls) == 1 and not t.sending, timeout=5000)


def test_failure_offers_the_zip(win: MainWindow, qtbot: QtBot, fake: Fake, tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    fake.answers = [OSError("offline"), OSError("offline")]
    asked: list[str] = []
    monkeypatch.setattr(ft.FeedbackTab, "ask_save", lambda self, r: asked.append(r.kind) or True)
    starts: list[str] = []

    def save_file(_parent, _title, start="", _filter=""):
        starts.append(start)
        return str(tmp_path / "kept.zip")

    monkeypatch.setattr(ft.dialogs, "save_file", save_file)
    t = _tab(win)
    t.message.setPlainText("Something broke")
    t.send()
    qtbot.waitUntil(lambda: not t.sending, timeout=5000)
    assert len(fake.calls) == 2 and asked == ["network"]  # one retry, then the offer
    assert Path(starts[0]).name == fb.default_zip_name()
    path = tmp_path / "kept.zip"
    assert t.status.state == "err" and t.status.text.text() == ft.SAVED.format(path=path)
    with zipfile.ZipFile(path) as z:
        assert json.loads(z.read("feedback.json")) == t.sent_payload
        assert z.read("log.txt").decode() == t.sent_payload["log"]
    assert t.message.toPlainText() == "Something broke"  # the draft stays


@pytest.mark.parametrize(("status", "text"), [(429, "Too many messages right now"), (413, "without the application"),
                                              (503, "temporarily unavailable")])
def test_relay_errors_explained_plainly(win: MainWindow, qtbot: QtBot, fake: Fake, status: int, text: str,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    fake.answers = [(status, b'{"error": "raw server words"}')] * 2
    monkeypatch.setattr(ft.FeedbackTab, "ask_save", lambda self, r: False)
    t = _tab(win)
    t.message.setPlainText("x")
    t.send()
    qtbot.waitUntil(lambda: not t.sending, timeout=5000)
    assert t.status.text.text().startswith("Sending failed: ") and text in t.status.text.text()
    assert "raw server words" not in t.status.text.text()


def test_save_as_file_always_available(win: MainWindow, fake: Fake, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ft.dialogs, "save_file", lambda *_a, **_k: str(tmp_path / "now.zip"))
    t = _tab(win)
    t.message.setPlainText("Keep this")
    assert t.save_btn.isEnabled()
    assert t.save_as_file() == tmp_path / "now.zip" and fake.calls == []
    assert "Saved to" in t.status.text.text()


@pytest.mark.parametrize("palette", ["night", "red"])
def test_no_green_or_blue_in_the_night_palettes(win: MainWindow, qtbot: QtBot, fake: Fake, palette: str,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    from starextractor.ui import retheme

    monkeypatch.setattr(ft.FeedbackTab, "ask_save", lambda self, r: False)
    t = _tab(win)
    t.message.setPlainText("Thanks")
    t.email.setText("bad")
    retheme.switch(palette)
    try:
        for state, text in (("ok", ft.SENT.format(ref="FB-T3ST01")), ("err", "Sending failed: offline")):
            t.status.set(state, text)
            t.status.show()
            qtbot.wait(30)
            img = t.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
            a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)
            px = a[:, : img.width(), [2, 1, 0]].astype(int)
            lit = px.sum(axis=2) > 24
            r, g, b = (px[..., i][lit] for i in range(3))
            if palette == "red":
                assert np.all(g <= 2) and np.all(b <= 2), state
            else:
                assert np.all(g <= r + 2) and np.all(b <= r + 2), state
            assert t.status.icon.text()  # the state also has a mark (✓ / !), not colour only
    finally:
        retheme.switch("default")


def test_no_network_at_import_startup_or_opening_the_tab(qtbot: QtBot, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[object] = []

    def no_net(*a, **_k):
        opened.append(a)
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(fb, "urllib_transport", no_net)
    import importlib

    importlib.reload(fb)
    monkeypatch.setattr(fb, "urllib_transport", no_net)
    w = MainWindow(session=Session.create(tmp_path / "s"))
    qtbot.addWidget(w)
    w.show()
    t = _tab(w)
    t.message.setPlainText("typing is not sending")
    t.show_what_is_sent().close()
    assert opened == []


ADDRESS = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PLACEHOLDER_DOMAINS = ("example.org", "example.net", "example.com")  # reserved for examples (RFC 2606)


def test_no_developer_email_address_anywhere(tmp_path: Path) -> None:
    files = [ROOT / "starextractor/core/feedback.py", ROOT / "starextractor/ui/feedback_tab.py",
             ROOT / "starextractor/ui/help.py", ROOT / "starextractor/resources/help.md", ROOT / "docs/FEEDBACK.md"]
    texts = {str(f.relative_to(ROOT)): f.read_text(encoding="utf-8") for f in files}
    texts["zip README.txt"] = fb.README
    texts["privacy notice"] = ft.PRIVACY
    hits = [f"{name}: {m}" for name, text in texts.items() for m in ADDRESS.findall(text)
            if not m.lower().endswith(PLACEHOLDER_DOMAINS)]
    assert not hits, hits
    assert not any(n for n in dir(fb) if "ADDRESS" in n.upper() or n.upper().startswith("EMAIL_TO"))
