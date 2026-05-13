from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class QuietHours:
    start: int
    end: int

    def contains(self, hour: int) -> bool:
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= hour < self.end
        return hour >= self.start or hour < self.end


@dataclass(frozen=True)
class Config:
    # VFS booking params
    vfs_country_code: str   # e.g. "pak"
    vfs_mission_code: str   # e.g. "nld"
    vfs_vac_code: str       # e.g. "NISL" (Islamabad), "KCHI" (Karachi)
    vfs_visa_category: str  # e.g. "TR" (Tourist)
    vfs_email: str

    # Auth: set VFS_AUTHORIZE to skip Playwright login (fast path / manual token).
    # Set VFS_PASSWORD to let Playwright fetch a fresh token on every run.
    # Exactly one must be provided.
    vfs_authorize: str | None
    vfs_password: str | None

    # Optional; sent as clientsource header. Captured once from DevTools.
    # Server appears to use this for routing/logging — omit if absent.
    vfs_client_source: str | None

    # Notifications
    gmail_user: str
    gmail_app_password: str
    alert_email_to: str

    twilio_sid: str
    twilio_token: str
    twilio_whatsapp_from: str
    alert_whatsapp_to: str

    quiet_hours: QuietHours | None
    dry_run: bool


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _parse_quiet_hours(raw: str | None) -> QuietHours | None:
    if not raw or not raw.strip():
        return None
    start_s, _, end_s = raw.partition("-")
    return QuietHours(start=int(start_s), end=int(end_s))


def load(dry_run: bool = False) -> Config:
    load_dotenv()

    authorize = _optional("VFS_AUTHORIZE")
    password = _optional("VFS_PASSWORD")
    if not authorize and not password:
        raise RuntimeError("Either VFS_AUTHORIZE or VFS_PASSWORD must be set.")

    return Config(
        vfs_country_code=_required("VFS_COUNTRY_CODE"),
        vfs_mission_code=_required("VFS_MISSION_CODE"),
        vfs_vac_code=_required("VFS_VAC_CODE"),
        vfs_visa_category=_required("VFS_VISA_CATEGORY"),
        vfs_email=_required("VFS_EMAIL"),
        vfs_authorize=authorize,
        vfs_password=password,
        vfs_client_source=_optional("VFS_CLIENT_SOURCE"),
        gmail_user=_required("GMAIL_USER"),
        gmail_app_password=_required("GMAIL_APP_PASSWORD"),
        alert_email_to=_required("ALERT_EMAIL_TO"),
        twilio_sid=_required("TWILIO_ACCOUNT_SID"),
        twilio_token=_required("TWILIO_AUTH_TOKEN"),
        twilio_whatsapp_from=_required("TWILIO_WHATSAPP_FROM"),
        alert_whatsapp_to=_required("ALERT_WHATSAPP_TO"),
        quiet_hours=_parse_quiet_hours(os.environ.get("QUIET_HOURS_CET")),
        dry_run=dry_run,
    )
