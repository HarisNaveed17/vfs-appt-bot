from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Iterable

from twilio.rest import Client

from vfs.scraper import Slot


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


def send_whatsapp(*, sid: str, token: str, from_: str, to: str, body: str) -> None:
    client = Client(sid, token)
    client.messages.create(from_=from_, to=to, body=body)
