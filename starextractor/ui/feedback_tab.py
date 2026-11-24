"""Help › User Feedback: a message (type, text, the user's optional email, the redacted application log if ticked)
sent to the relay on Send, in a worker; a zip to keep when sending fails. Nothing is stored on disk except a zip the
user saves; the draft lives in this widget while the Help window exists. No network until Send (docs/FEEDBACK.md)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from starextractor.core import feedback as fb

from . import dialogs
from .widgets.common import Segmented, StatusLine, label

TYPE_LABELS = ("Problem", "Idea", "Question", "Other")
COOLDOWN_MS = 30_000  # Send stays off this long after a message went through (client-side spam guard)
PRIVACY = (
    "When you press Send, this message, your optional email, your app and Windows version and (if ticked) the "
    "redacted application log are sent to the developer's private feedback inbox. Folder paths, your Windows user "
    "name, computer name and email addresses inside the log are removed first; “See exactly what is sent” shows the "
    "exact text. No location, no file paths and no images are sent. Your email is used only to reply. Feedback and "
    "emails are kept for 12 months. To have yours deleted, send a new message of type Question from this tab and "
    "quote your reference.")
SENT = "Thank you. Your reference is {ref}; quote it if you write to us again."
SAVED = ("Sending failed. Your message was saved to {path}. Please try again later, or keep the file and send a new "
         "message from this tab when you are online.")


def documents_dir() -> Path:
    from PySide6.QtCore import QStandardPaths

    loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    return Path(loc) if loc else Path.home()


class FeedbackTab(QWidget):
    def __init__(self, log_source: Callable[[], str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("feedbackTab")
        self.log_source = log_source or (lambda: "")
        self.sending = False
        self.cooldown = QTimer(self)
        self.cooldown.setSingleShot(True)
        self.cooldown.timeout.connect(self._update)
        self.last_result: fb.SendResult | None = None
        self.saved_to: Path | None = None
        self._log_touched = False
        from starextractor.workers.runner import JobRunner

        self.runner = JobRunner(self, max_threads=1)
        self.runner.finished.connect(self._on_done)
        self.runner.failed.connect(lambda _slot, err: self._on_done("feedback.send", fb.SendResult(
            False, "", "error", fb.MESSAGES["error"])))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(10)
        lay.addWidget(label("Tell us about a problem, an idea or a question. Write in your own words; we read every "
                            "message.", "muted", wrap=True))
        row = QHBoxLayout()
        row.addWidget(label("Type", "muted"))
        self.type = Segmented(list(TYPE_LABELS))
        self.type.changed.connect(self._type_changed)
        row.addWidget(self.type)
        row.addStretch(1)
        lay.addLayout(row)
        self.message = QPlainTextEdit()
        self.message.setObjectName("feedbackMessage")
        self.message.setPlaceholderText("Your message (what happened, what you expected, or your idea)")
        self.message.setTabChangesFocus(True)
        self.message.textChanged.connect(self._update)
        lay.addWidget(self.message, 1)
        self.counter = label("", "mono")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.counter)
        erow = QHBoxLayout()
        erow.addWidget(label("Email (optional, only used to reply)", "muted"))
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@example.org")
        self.email.textChanged.connect(self._update)
        erow.addWidget(self.email, 1)
        lay.addLayout(erow)
        self.email_hint = StatusLine("warn", "This does not look like an email address (name@example.org).")
        self.email_hint.hide()
        lay.addWidget(self.email_hint)
        self.attach = QCheckBox("Attach the application log (recommended for problems)")
        self.attach.setChecked(True)  # Problem is the first type
        self.attach.toggled.connect(self._attach_toggled)
        lay.addWidget(self.attach)
        brow = QHBoxLayout()
        self.what_btn = QPushButton("See exactly what is sent")
        self.what_btn.setFlat(True)
        self.what_btn.setProperty("role", "link")
        self.what_btn.clicked.connect(lambda: self.show_what_is_sent())
        brow.addWidget(self.what_btn)
        brow.addStretch(1)
        self.save_btn = QPushButton("Save as file…")
        self.save_btn.setToolTip("Save the message as a zip file (always possible, also when online)")
        self.save_btn.clicked.connect(lambda: self.save_as_file())
        brow.addWidget(self.save_btn)
        self.send_btn = QPushButton("Send")
        self.send_btn.setToolTip("Send the message (Ctrl+Enter)")
        self.send_btn.clicked.connect(lambda: self.send())
        brow.addWidget(self.send_btn)
        lay.addLayout(brow)
        self.status = StatusLine("off", "")
        self.status.setObjectName("feedbackStatus")
        self.status.hide()
        lay.addWidget(self.status)
        self.privacy = label(PRIVACY, "muted", wrap=True, style="font-size: 12px;")
        self.privacy.setObjectName("feedbackPrivacy")
        lay.addWidget(self.privacy)
        for w in (self.message, self.email, self.attach):  # Ctrl+Enter sends (a valid form only)
            w.installEventFilter(self)
        QWidget.setTabOrder(self.message, self.email)
        QWidget.setTabOrder(self.email, self.attach)
        QWidget.setTabOrder(self.attach, self.what_btn)
        QWidget.setTabOrder(self.what_btn, self.save_btn)
        QWidget.setTabOrder(self.save_btn, self.send_btn)
        self._update()

    def eventFilter(self, obj, event) -> bool:
        if (event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            if self.can_send():
                self.send()
            return True  # never a line break
        return super().eventFilter(obj, event)

    # ---- form ----------------------------------------------------------------------------------------------------
    def kind(self) -> str:
        return fb.TYPES[max(0, self.type.index())]

    def _type_changed(self, i: int) -> None:
        if not self._log_touched:
            self.attach.blockSignals(True)
            self.attach.setChecked(fb.TYPES[i] == "problem")  # the default follows the type until the user chooses
            self.attach.blockSignals(False)

    def _attach_toggled(self, _on: bool) -> None:
        self._log_touched = True

    def problems(self) -> list[str]:
        """Why the form cannot be sent yet ([] = valid)."""
        out = []
        text = self.message.toPlainText().strip()
        if not text:
            out.append("Write a message.")
        elif len(text) > fb.MESSAGE_MAX:
            out.append(f"The message is longer than {fb.MESSAGE_MAX} characters.")
        if self.email.text().strip() and not fb.EMAIL_RE.match(self.email.text().strip()):
            out.append("email")
        return out

    def can_send(self) -> bool:
        return not self.problems() and not self.sending and not self.cooldown.isActive()

    def _update(self) -> None:
        n = len(self.message.toPlainText().strip())
        self.counter.setText(f"{n} / {fb.MESSAGE_MAX}" + ("  too long" if n > fb.MESSAGE_MAX else ""))
        self.counter.setProperty("role", "chip-warn" if n > fb.MESSAGE_MAX else "mono")
        from . import theme

        theme.repolish(self.counter)
        self.email_hint.setVisible("email" in self.problems())
        ok = self.can_send()
        self.send_btn.setEnabled(ok)
        self.send_btn.setProperty("role", "primary" if ok else None)
        theme.repolish(self.send_btn)
        self.what_btn.setEnabled(not self.problems())

    def payload(self) -> dict[str, Any]:
        return fb.build_payload(self.kind(), self.message.toPlainText(), self.email.text(), self.attach.isChecked(),
                                self.log_source() if self.attach.isChecked() else "")

    # ---- see exactly what is sent ------------------------------------------------------------------------------
    def show_what_is_sent(self) -> QDialog | None:
        try:
            text = fb.preview(self.payload())
        except ValueError as exc:
            self._say("warn", f"Nothing to show yet: {exc}.")
            return None
        dlg = QDialog(self)
        dlg.setObjectName("feedbackPreview")
        dlg.setWindowTitle("Exactly what is sent")
        dlg.resize(760, 560)
        v = QVBoxLayout(dlg)
        v.addWidget(label(f"This is the exact text that Send posts to {fb.RELAY_URL} (as JSON). Nothing else is "
                          "sent.", "muted", wrap=True))
        box = QPlainTextEdit(text)
        box.setObjectName("planOutput")  # monospace, like the observing plan
        box.setReadOnly(True)
        box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        v.addWidget(box, 1)
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(close)
        v.addLayout(h)
        dlg.preview_text = box  # type: ignore[attr-defined]
        dialogs.style_dialog(dlg)
        dlg.show()
        self.preview_dialog = dlg
        return dlg

    # ---- send ----------------------------------------------------------------------------------------------------
    def send(self) -> bool:
        if not self.can_send():
            return False
        try:
            payload = self.payload()
        except ValueError as exc:
            self._say("warn", f"Cannot send: {exc}.")
            return False
        self.sending = True
        self.sent_payload = payload
        self._say("busy", "Sending…")
        self._update()
        self.runner.submit("feedback.send", send_job, payload)
        return True

    def _on_done(self, slot: str, result: object) -> None:
        if slot != "feedback.send":
            return
        self.sending = False
        r = result if isinstance(result, fb.SendResult) else fb.SendResult(False, "", "error", fb.MESSAGES["error"])
        self.last_result = r
        if r.ok:
            self._say("ok", SENT.format(ref=r.ref))
            self.cooldown.start(COOLDOWN_MS)
            self.message.clear()
        else:
            self._say("err", f"Sending failed: {r.text}")
            self.offer_zip(r)
        self._update()

    def offer_zip(self, r: fb.SendResult) -> Path | None:
        """After a failure: ask, then save the zip; the status then says where it is."""
        if not self.ask_save(r):
            return None
        path = self.save_as_file(self.sent_payload)
        if path is not None:
            self._say("err", SAVED.format(path=path))
        return path

    def ask_save(self, r: fb.SendResult) -> bool:
        return dialogs.ask(self, "Sending failed", f"{r.text}\n\nSave your message as a file, to keep it and send it "
                                                    "later?")

    def save_as_file(self, payload: dict[str, Any] | None = None) -> Path | None:
        try:
            payload = payload or self.payload()
        except ValueError as exc:
            self._say("warn", f"Nothing to save yet: {exc}.")
            return None
        start = str(documents_dir() / fb.default_zip_name())
        chosen = dialogs.save_file(self, "Save the feedback as a file", start, "Zip files (*.zip)")
        if not chosen:
            return None
        try:
            path = fb.save_fallback_zip(chosen, payload)
        except OSError as exc:
            self._say("err", f"Could not save {chosen}: {exc.strerror or exc}.")
            return None
        self.saved_to = path
        if self.last_result is None or self.last_result.ok:
            self._say("ok", f"Saved to {path}.")
        return path

    def _say(self, state: str, text: str) -> None:
        self.status.set({"busy": "off"}.get(state, state), text)
        self.status.show()


def send_job(_ctx: Any, payload: dict[str, Any]) -> fb.SendResult:
    """Runs in the worker: the one request (with its one retry)."""
    return fb.send(payload)
