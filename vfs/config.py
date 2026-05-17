from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    # VFS booking params
    vfs_country_code: str   # e.g. "pak"
    vfs_mission_code: str   # e.g. "nld"
    vfs_vac_codes: tuple[str, ...]  # all centres to sweep, in order
    vfs_visa_category: str  # e.g. "TR" (Tourist)
    vfs_email: str

    # The browser logs in fresh every run with this password (the page JS
    # encrypts it). The old VFS_AUTHORIZE/httpx path is gone — Cloudflare
    # blocks direct API calls, so a stored token never works.
    vfs_password: str

    # Notifications.
    # Email is kept as a record/audit log (not watched in real time);
    # Pushover (Emergency priority) is the can't-miss real-time alarm.
    gmail_user: str
    gmail_app_password: str
    alert_email_to: str

    pushover_token: str
    pushover_user: str

    dry_run: bool


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def load(dry_run: bool = False) -> Config:
    load_dotenv()

    # VFS_VAC_CODE is comma-separated to sweep multiple centres in one session
    # (e.g. "NISL,Netherlands Lahore"). Each entry is a code or a raw UI label;
    # vfs/auth.py:_VAC_LABELS maps known codes -> labels and falls back to the
    # raw value, so an unknown-code centre can be given by its label directly.
    raw_vac = _required("VFS_VAC_CODE")
    vac_codes = tuple(c.strip() for c in raw_vac.split(",") if c.strip())

    return Config(
        vfs_country_code=_required("VFS_COUNTRY_CODE"),
        vfs_mission_code=_required("VFS_MISSION_CODE"),
        vfs_vac_codes=vac_codes,
        vfs_visa_category=_required("VFS_VISA_CATEGORY"),
        vfs_email=_required("VFS_EMAIL"),
        vfs_password=_required("VFS_PASSWORD"),
        gmail_user=_required("GMAIL_USER"),
        gmail_app_password=_required("GMAIL_APP_PASSWORD"),
        alert_email_to=_required("ALERT_EMAIL_TO"),
        pushover_token=_required("PUSHOVER_TOKEN"),
        pushover_user=_required("PUSHOVER_USER"),
        dry_run=dry_run,
    )
