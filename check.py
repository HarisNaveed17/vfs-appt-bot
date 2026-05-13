from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from vfs import config, notifier, scraper, state

log = logging.getLogger("vfs-bot")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse but do not send or persist state.")
    parser.add_argument("--no-jitter", action="store_true",
                        help="Skip the random pre-fetch delay (useful for local testing).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load(dry_run=args.dry_run)

    if cfg.quiet_hours and _in_quiet_hours(cfg.quiet_hours):
        log.info("Within quiet hours; skipping run.")
        return 0

    if not args.no_jitter:
        delay = random.uniform(0, 240)
        log.info("Jitter sleep: %.0fs", delay)
        time.sleep(delay)

    authorize = _get_authorize(cfg)
    if authorize is None:
        return 0

    try:
        current = scraper.fetch_availability(
            country_code=cfg.vfs_country_code,
            mission_code=cfg.vfs_mission_code,
            vac_code=cfg.vfs_vac_code,
            visa_category=cfg.vfs_visa_category,
            email=cfg.vfs_email,
            authorize=authorize,
            client_source=cfg.vfs_client_source,
        )
    except scraper.BlockedError as exc:
        log.warning("Blocked by VFS (%s); will retry next run.", exc)
        return 0

    log.info("Fetched %d slot(s).", len(current))

    previous = state.load()
    new = state.diff(current, previous)

    if not new:
        log.info("No new slots.")
        if not cfg.dry_run:
            state.save(current)
        return 0

    log.info("%d new slot(s); dispatching alerts.", len(new))
    body = notifier.format_message(new)

    if cfg.dry_run:
        log.info("Dry run; would send:\n%s", body)
        return 0

    _dispatch(cfg, body)
    state.save(current)
    return 0


def _get_authorize(cfg: config.Config) -> str | None:
    if cfg.vfs_authorize:
        log.info("Using stored VFS_AUTHORIZE token.")
        return cfg.vfs_authorize

    log.info("VFS_AUTHORIZE not set; logging in via Playwright.")
    from vfs import auth
    try:
        return auth.get_access_token(
            email=cfg.vfs_email,
            password=cfg.vfs_password,  # type: ignore[arg-type]
            country_code=cfg.vfs_country_code,
            mission_code=cfg.vfs_mission_code,
        )
    except Exception:
        log.exception("Playwright login failed; skipping this run.")
        return None


def _in_quiet_hours(qh: config.QuietHours) -> bool:
    hour = datetime.now(ZoneInfo("Europe/Amsterdam")).hour
    return qh.contains(hour)


def _dispatch(cfg: config.Config, body: str) -> None:
    try:
        notifier.send_email(
            gmail_user=cfg.gmail_user,
            app_password=cfg.gmail_app_password,
            to=cfg.alert_email_to,
            body=body,
        )
        log.info("Email sent.")
    except Exception:
        log.exception("Email dispatch failed.")

    try:
        notifier.send_whatsapp(
            sid=cfg.twilio_sid,
            token=cfg.twilio_token,
            from_=cfg.twilio_whatsapp_from,
            to=cfg.alert_whatsapp_to,
            body=body,
        )
        log.info("WhatsApp sent.")
    except Exception:
        log.exception("WhatsApp dispatch failed.")


if __name__ == "__main__":
    sys.exit(main())
