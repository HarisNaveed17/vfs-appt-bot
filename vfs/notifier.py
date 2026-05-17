from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Iterable

import httpx

from vfs.scraper import Slot

_PUSHOVER_API = "https://api.pushover.net/1/messages.json"


def format_message(new_slots: Iterable[Slot]) -> str:
    lines = ["New VFS appointment slots available:"]
    for s in new_slots:
        when = f"{s.date} {s.time}" if s.time else s.date
        lines.append(f"  - {when} | {s.center} | {s.category}")
    return "\n".join(lines)


def send_email(*, gmail_user: str, app_password: str, to: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "VFS Netherlands: appointment slot opened"
    msg["From"] = gmail_user
    msg["To"] = to
    msg.set_content(body)
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.starttls()
        smtp.login(gmail_user, app_password)
        smtp.send_message(msg)


def send_pushover(*, token: str, user: str, body: str) -> None:
    """Send an Emergency-priority Pushover alert.

    priority=2 re-alerts every `retry` seconds until acknowledged in the
    Pushover app, giving up after `expire` seconds — i.e. a continuous alarm
    you can't sleep through, not a one-shot push.
    """
    resp = httpx.post(
        _PUSHOVER_API,
        data={
            "token": token,
            "user": user,
            "title": "VFS Netherlands: appointment slot opened",
            "message": body,
            "priority": 2,
            "retry": 30,      # re-alert every 30s — Pushover's HARD minimum;
                              # the API rejects anything < 30 (no alert sent)
            "expire": 3600,   # keep nagging up to 1h, then stop
        },
        timeout=15.0,
    )
    resp.raise_for_status()
