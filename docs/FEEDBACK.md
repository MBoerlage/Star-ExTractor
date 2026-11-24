# User Feedback (Help › User Feedback)

A user writes a message in the Help window's **User Feedback** tab and presses **Send**; the app posts it to our
relay, which files it in a private place. There is no public email address anywhere: not in the app, the zip, the
Help or these docs.

## Architecture

- **App** (`starextractor/core/feedback.py`, no Qt; the tab is `starextractor/ui/feedback_tab.py` inside
  `ui/help.py`): builds payload v1, redacts it, and on Send posts it once (one retry) from a worker thread. Nothing
  goes online at startup or when the tab opens. The draft is kept in memory only.
- **Relay**: a small HTTPS service at `https://starex-relay.starex-tools.workers.dev/feedback`. It validates the
  payload, rate-limits, and files each message as a **private GitHub issue**, then answers with a reference
  `FB-XXXXXX`. The relay's code and configuration live outside this repository.
- **Fallback**: when sending fails, the tab offers a zip (`starex-feedback-<date>.zip`, Documents by default) with
  `feedback.json` (the exact payload), `log.txt` (the redacted log, if attached) and `README.txt`. **Save as file…**
  makes it at any time.

The relay URL is the constant `RELAY_URL` in `core/feedback.py`; to move the relay, change that one constant (and
this file). It is public by design; there are no secrets in the app.

## Payload v1

```json
{"v": 1, "type": "problem" | "idea" | "question" | "other", "message": "1..5000 characters",
 "email": "the user's own address for replies, or \"\"", "app_version": "0.3.0",
 "windows": "Windows 11 10.0.26100", "log": "redacted log tail, or \"\"", "website": ""}
```

- Exactly these keys; no others. `website` is a honeypot: always `""`, never shown.
- `message` is never trimmed (the form refuses more than 5000 characters); `log` is the **tail**, at most
  150,000 characters (`LOG_MAX_CHARS`; the relay refuses more than 200,000).
- The relay refuses bodies above 256 KB and an invalid email.
- Request: HTTPS POST, `Content-Type: application/json`, an explicit `Content-Length`, `User-Agent:
  StarExReduce/<version>`, timeout 15 s. One retry after 2 s on a network error or HTTP 5xx; no retry on 4xx.

## Status codes

| relay answer | result kind | what the user reads |
|---|---|---|
| 200 `{ok: true, ref}` | sent | "Thank you. Your reference is FB-XXXXXX; quote it if you write to us again." |
| 400 | invalid | the service did not accept the message; check the email |
| 411, 413 | too_large | too large; try again without the application log |
| 429 | busy | too many messages right now, please try later |
| 503 | unavailable | temporarily unavailable, try later |
| 502, other, odd 200 | error | the service had a problem, try later |
| no connection, timeout | network | could not reach the service |

The server's own text is never shown. After any failure the tab offers the zip. Send stays off while sending and
for 30 s after a success.

## Redaction (before anything is shown or sent)

- `C:\Users\<name>\…` and `C:/Users/<name>/…` (any drive, also with doubled backslashes) → `C:\Users\<user>\…`;
  `/Users/<name>` and `/home/<name>` → `<user>`.
- The Windows user name (`USERNAME`) and the computer name (`COMPUTERNAME`, the host name and its first part) →
  `<user>` / `<host>`, as whole words, case ignored. Names shorter than 3 characters are skipped: they would damage
  ordinary words.
- Email addresses in the **log** → `<email>`. An address the user types in the message or the email field is theirs
  to share and stays.
- The "application log" is the Log panel's lines of the open pair plus the end of `error.log` in the settings
  folder.

## Retention

Feedback and emails are kept for 12 months. A user asks for deletion by sending a message of type Question with
their reference.

## Testing

All tests use a fake transport (`tests/test_feedback.py`, `tests/test_feedback_tab.py`); none reaches the relay.
