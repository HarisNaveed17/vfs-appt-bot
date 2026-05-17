from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path

from vfs import config, notifier, scraper, state

log = logging.getLogger("vfs-bot")

SESSION_PATH = Path("session.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and parse but do not send or persist state.")
    parser.add_argument("--no-jitter", action="store_true",
                        help="Skip the random pre-fetch delay (useful for local testing).")
    parser.add_argument("--bootstrap-session", action="store_true",
                        help="Open a browser, wait for manual login, save session.json, then exit.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.bootstrap_session:
        cfg = config.load(dry_run=True)
        _bootstrap_session(cfg)
        return 0

    cfg = config.load(dry_run=args.dry_run)

    if not args.no_jitter:
        delay = random.uniform(0, 240)
        log.info("Jitter sleep: %.0fs", delay)
        time.sleep(delay)

    current = _fetch(cfg)
    if current is None:
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


def _fetch(cfg: config.Config) -> list[scraper.Slot] | None:
    # The only path: drive real Chrome via patchright. Hitting the API
    # directly with httpx is Cloudflare-blocked, so a stored token (the old
    # VFS_AUTHORIZE fast path) never works and has been removed.
    from vfs import auth

    storage_state = _load_session()
    if storage_state:
        log.info("Restored cached browser session.")
    else:
        log.info("No cached session; will perform fresh login.")

    try:
        per_centre, new_storage_state = auth.fetch_via_browser(
            email=cfg.vfs_email,
            password=cfg.vfs_password,
            country_code=cfg.vfs_country_code,
            mission_code=cfg.vfs_mission_code,
            vac_codes=list(cfg.vfs_vac_codes),
            visa_category=cfg.vfs_visa_category,
            storage_state=storage_state,
        )
    except Exception:
        log.exception("Playwright session failed; skipping this run.")
        return None

    _save_session(new_storage_state)

    # per_centre maps each swept centre code -> its raw CheckIsSlotAvailable
    # response. Parse each with that centre as the identifier and combine.
    slots: list[scraper.Slot] = []
    for code, raw in per_centre.items():
        slots.extend(
            scraper.parse_availability(
                raw, vac_code=code, visa_category=cfg.vfs_visa_category
            )
        )
    return slots


def _bootstrap_session(cfg: config.Config) -> None:
    """Open a visible browser, wait for manual login, save session.json."""
    import asyncio
    from patchright.async_api import async_playwright

    from vfs.auth import BOT_PROFILE_DIR

    async def _run():
        login_url = (
            f"https://visa.vfsglobal.com/{cfg.vfs_country_code}"
            f"/en/{cfg.vfs_mission_code}/login"
        )
        base_path = f"/{cfg.vfs_country_code}/en/{cfg.vfs_mission_code}/"
        # Same dedicated profile dir the production fetch (vfs/auth.py) reuses.
        # Chrome blocks remote-debugging on the default profile, so we cannot
        # reuse ~/.config/google-chrome; a fresh profile still presents a real
        # Chrome fingerprint. Capturing into THIS dir is what lets the fetch
        # path replay cf_clearance against a matching fingerprint.
        BOT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(BOT_PROFILE_DIR),
                headless=False,
                channel="chrome",
            )
            # Clear stale VFS cookies so Angular doesn't show "Session Expired".
            await context.clear_cookies(domain="visa.vfsglobal.com")
            await context.clear_cookies(domain="vfsglobal.com")
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(login_url)
            log.info("Log in manually in the browser window. Waiting for post-login page…")

            # VFS is an Angular SPA: post-login routing is client-side pushState,
            # which fires no `load` event, so page.wait_for_url(..., until="load")
            # hangs even after the URL changes. Poll page.url directly instead, and
            # accept any authenticated route (dashboard or application-detail) — the
            # app may auto-advance past /dashboard faster than a coarse poll.
            deadline = time.monotonic() + 120
            while True:
                if time.monotonic() > deadline:
                    raise RuntimeError("Did not reach a post-login page within 120s.")
                try:
                    url = page.url
                except Exception as exc:
                    raise RuntimeError(
                        "Browser closed before login completed."
                    ) from exc
                if base_path in url and (
                    "/dashboard" in url or "/application-detail" in url
                ):
                    break
                await asyncio.sleep(0.25)

            log.info("Login detected (%s). Capturing session.", page.url)
            state = await context.storage_state()
            await context.close()
        return state

    state = asyncio.run(_run())
    _save_session(state)
    log.info("Session saved to %s", SESSION_PATH)


def _load_session() -> dict | None:
    # Persistent self-hosted host: the file from the previous run is the only
    # source. (cf_clearance/fingerprint continuity; the VFS auth token in it is
    # not reusable — see CLAUDE.md / project-account-restriction memory.)
    if SESSION_PATH.exists():
        try:
            return json.loads(SESSION_PATH.read_text())
        except Exception:
            pass
    return None


def _save_session(storage_state: dict) -> None:
    SESSION_PATH.write_text(json.dumps(storage_state))


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
        notifier.send_pushover(
            token=cfg.pushover_token,
            user=cfg.pushover_user,
            body=body,
        )
        log.info("Pushover alert sent.")
    except Exception:
        log.exception("Pushover dispatch failed.")


if __name__ == "__main__":
    sys.exit(main())
