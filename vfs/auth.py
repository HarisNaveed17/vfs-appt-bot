"""Patchright-based VFS session: login + availability check in one browser pass.

Uses patchright (a drop-in Playwright fork that strips Cloudflare-detectable
automation signatures) with real Chrome. Do not add `--disable-blink-features`
args, `navigator.webdriver` patches, or a custom User-Agent: those are
themselves fingerprints, and patchright already handles them at a lower level.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from patchright.async_api import async_playwright

log = logging.getLogger("vfs-bot")


class AccountRestrictedError(RuntimeError):
    """VFS reports the account is temporarily restricted (post-login throttle)."""

# Dedicated Chrome profile dir. MUST stay the same dir bootstrap
# (_bootstrap_session in check.py) captured the session in: cf_clearance is
# bound to the browser fingerprint, so the profile that replays it has to match
# the one that earned it. check.py imports this constant — do not fork the path.
BOT_PROFILE_DIR = Path.home() / ".config" / "google-chrome-vfs-bot"

_USERNAME_SELECTORS = [
    'input[formcontrolname="username"]',
    'input[formcontrolname="email"]',
    'input[type="email"]',
    'input[name="username"]',
]

# The application-detail dropdowns show human labels, not the VAC/category codes
# stored in .env (which are what the API payload uses). Map code -> visible
# label; fall back to the raw value so a label can also be set directly in .env.
_VAC_LABELS = {
    "NISL": "Netherlands Islamabad",
    "KCHI": "Netherlands Karachi",
}
_SUBCATEGORY_LABELS = {
    # Real UI labels (verified 2026-05-17 from logged option text):
    #   Business / Family And Friends Visit / Other (...) / Tourism
    "TR": "Tourism",
}


def fetch_via_browser(
    *,
    email: str,
    password: str,
    country_code: str,
    mission_code: str,
    vac_codes: list[str],
    visa_category: str,
    storage_state: dict | None = None,
) -> tuple[dict[str, dict], dict]:
    """Sweep one or more centres and return ({centre_code: raw}, storage_state).

    vac_codes are swept in order with exactly ONE in-page centre switch between
    consecutive centres (more switching escalates Turnstile to an interactive
    captcha). storage_state is the Playwright storage state from the
    authenticated session; pass it back next call to skip re-login.
    """
    return asyncio.run(_run(
        email=email,
        password=password,
        country_code=country_code,
        mission_code=mission_code,
        vac_codes=vac_codes,
        visa_category=visa_category,
        storage_state=storage_state,
    ))


async def _run(
    *,
    email: str,
    password: str,
    country_code: str,
    mission_code: str,
    vac_codes: list[str],
    visa_category: str,
    storage_state: dict | None,
) -> tuple[dict[str, dict], dict]:
    base = f"https://visa.vfsglobal.com/{country_code}/en/{mission_code}"
    headless = bool(os.environ.get("CI"))  # headless in GitHub Actions, visible locally

    BOT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        # Persistent context on the bootstrap profile dir: reproduces the exact
        # browser fingerprint cf_clearance was issued against, and the profile
        # already holds the auth + clearance cookies from bootstrap. There is no
        # separate `browser` object in this mode — close the context instead.
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(BOT_PROFILE_DIR),
            headless=headless,
            channel="chrome",
        )
        # Cold profile (e.g. CI restoring SESSION_JSON where the on-disk profile
        # is empty): seed cookies from the passed-in storage_state. When the
        # profile already has cookies (local/self-hosted), it is the source of
        # truth and the dict is ignored so we never clobber a live cf_clearance.
        if storage_state and storage_state.get("cookies"):
            if not await context.cookies():
                await context.add_cookies(storage_state["cookies"])

        # Capture the saved localStorage for the VFS origin. The auth token
        # lives in localStorage["loginResponse"] (not a cookie), and a
        # persistent profile does not reliably carry localStorage between
        # processes. We restore it by navigating to the origin and writing it
        # via page.evaluate BEFORE going to /dashboard (done further below) —
        # NOT via context.add_init_script: that is broken under patchright +
        # launch_persistent_context (any init script, even an empty one, makes
        # the next navigation fail with net::ERR_NAME_NOT_RESOLVED).
        origin = "/".join(base.split("/", 3)[:3])  # https://visa.vfsglobal.com
        seed_ls: dict[str, str] = {}
        for o in (storage_state or {}).get("origins", []):
            if o.get("origin") == origin:
                seed_ls = {
                    e["name"]: e["value"] for e in o.get("localStorage", [])
                }
                break
        page = context.pages[0] if context.pages else await context.new_page()

        availability_data: dict | None = None
        login_error: str | None = None
        account_restricted: bool = False

        async def _on_response(response):
            nonlocal availability_data, login_error, account_restricted
            if "CheckIsSlotAvailable" in response.url and response.ok:
                try:
                    availability_data = await response.json()
                except Exception:
                    pass
            elif "/user/login" in response.url and not response.ok:
                try:
                    data = await response.json()
                    code = data.get("code") or data.get("errorCode") or response.status
                    import json as _json
                    if "restrict" in _json.dumps(data).lower():
                        account_restricted = True
                    login_error = f"VFS login API returned error {code}"
                except Exception:
                    pass

        page.on("response", _on_response)

        # --- Try dashboard directly. The persistent profile may still hold a
        #     valid VFS session; reuse it if so (re-login is the throttle
        #     trigger). But /dashboard renders its shell on domcontentloaded
        #     BEFORE Angular's auth guard runs, then redirects to /login if the
        #     session is dead — so a transient /dashboard URL is NOT proof of
        #     auth. Race the real authenticated marker ("Start New Booking")
        #     against the login email field; whichever actually appears wins.
        if seed_ls:
            # Land on the VFS origin, restore the saved auth token into
            # localStorage, then proceed — this is what makes /dashboard accept
            # the cached session instead of bouncing to /login.
            log.info("Restoring saved session into localStorage.")
            await page.goto(
                f"{base}/login", wait_until="domcontentloaded", timeout=30_000
            )
            await page.evaluate(
                "(kv) => { for (const k in kv) {"
                " try { localStorage.setItem(k, kv[k]); } catch (e) {} } }",
                seed_ls,
            )

        log.info("Attempting cached session; navigating to dashboard.")
        await page.goto(f"{base}/dashboard", wait_until="domcontentloaded", timeout=30_000)

        dashboard_marker = page.get_by_role("button", name="Start New Booking").first
        login_marker = page.locator(", ".join(_USERNAME_SELECTORS)).first

        on_dashboard = False
        for _ in range(50):  # ~25s for the SPA auth guard + data load to settle
            if "/login" in page.url:
                break  # auth guard already bounced us — session is dead
            if await dashboard_marker.is_visible():
                on_dashboard = True
                break
            if await login_marker.is_visible():
                break
            await asyncio.sleep(0.5)

        if on_dashboard:
            log.info("Cached session still valid; skipped login.")
        else:
            log.info("No valid cached session (on %s); falling back to login.", page.url)

        # --- Login if no valid cached session ---
        if not on_dashboard:
            # A stale restored token makes VFS render a "Session Expired or
            # Invalid" interstitial that hides the login form (and would also
            # poison the form even after navigating). We are on the VFS origin
            # here, so wipe web storage before loading a fresh /login. Cookies
            # (esp. cf_clearance) are intentionally kept for Cloudflare.
            try:
                await page.evaluate(
                    "() => { try { localStorage.clear();"
                    " sessionStorage.clear(); } catch (e) {} }"
                )
            except Exception:
                pass
            login_url = f"{base}/login"
            log.info("Navigating to %s", login_url)
            await page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)

            for sel in [
                'button:has-text("Accept Only Necessary")',
                'button:has-text("Accept All Cookies")',
            ]:
                try:
                    btn = page.locator(sel).first
                    await btn.wait_for(state="visible", timeout=5_000)
                    await btn.click()
                    log.info("Dismissed cookie consent via: %s", sel)
                    break
                except Exception:
                    pass

            username_input = None
            for sel in _USERNAME_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    await loc.wait_for(state="visible", timeout=15_000)
                    username_input = loc
                    log.info("Found username input: %s", sel)
                    break
                except Exception:
                    continue

            if username_input is None:
                try:
                    await page.screenshot(path="debug_no_form.png", full_page=True)
                except Exception:
                    pass
                await context.close()
                raise RuntimeError(
                    "Could not find the login form. "
                    "Check debug_no_form.png and update the selectors in vfs/auth.py."
                )

            password_input = page.locator('input[type="password"]').first
            await password_input.wait_for(state="visible", timeout=10_000)
            await username_input.fill(email)
            await password_input.fill(password)
            await page.locator('button:has-text("Sign In")').first.click()
            log.info("Submitted login form; waiting for dashboard")

            try:
                await page.wait_for_url(f"**/{country_code}/en/{mission_code}/dashboard**", timeout=30_000)
            except Exception:
                if login_error:
                    await context.close()
                    if account_restricted:
                        raise AccountRestrictedError(login_error)
                    raise RuntimeError(login_error)
                try:
                    await page.screenshot(path="debug_post_login.png", full_page=True)
                except Exception:
                    pass
                await context.close()
                raise RuntimeError(
                    "Did not reach dashboard after login. Check debug_post_login.png."
                )

        # Capture the reusable session NOW — right after dashboard auth and
        # BEFORE the booking flow, which overwrites localStorage["loginResponse"]
        # (the auth token) with the roleName string "Individual". Capturing at
        # the end of _run persists that 12-char husk and breaks session reuse.
        saved_state = await context.storage_state()

        # --- Booking flow ---
        # The dashboard reaches /dashboard before its data XHRs finish; until
        # they do, a full-screen loading overlay sits on top and "Start New
        # Booking" is dimmed/non-interactable (see debug_dashboard.png). Waiting
        # only for "load" raced that overlay. Settle on networkidle instead.
        log.info("On dashboard. Waiting for network/spinner to settle.")
        try:
            await page.wait_for_load_state("networkidle", timeout=30_000)
        except Exception:
            # VFS sometimes holds a background poll open so networkidle never
            # fires; fall through and rely on the explicit visibility wait.
            log.info("networkidle not reached; continuing on explicit waits.")

        # Best-effort: wait for a known loading overlay to detach. Each selector
        # is a guess, so failures are swallowed individually — a wrong guess
        # must never hard-fail the run.
        for spinner_sel in (
            ".loading-overlay", ".ngx-spinner-overlay", "mat-spinner",
            ".cdk-overlay-backdrop", ".spinner", ".loader",
        ):
            try:
                await page.locator(spinner_sel).first.wait_for(
                    state="hidden", timeout=5_000
                )
            except Exception:
                pass

        try:
            log.info("Clicking 'Start New Booking'.")
            start_btn = page.get_by_role("button", name="Start New Booking")
            if await start_btn.count() == 0:
                # Fallback if it is not a semantic <button> (e.g. <a> styled as one).
                start_btn = page.locator(
                    ':is(button, a, [role="button"]):has-text("Start New Booking")'
                )
            start_btn = start_btn.first
            await start_btn.wait_for(state="visible", timeout=30_000)
            await start_btn.click()
            await page.wait_for_url("**/application-detail**", timeout=15_000)
        except Exception:
            try:
                await page.screenshot(path="debug_dashboard.png", full_page=True)
            except Exception:
                pass
            await context.close()
            raise RuntimeError(
                "Could not start a new booking from the dashboard. Check "
                "debug_dashboard.png — the page is likely still behind a loading "
                "spinner, or the button selector changed."
            )
        log.info("On application-detail.")

        # Field mechanics (project memory project-booking-flow):
        #   dropdown 0 = Application Centre   (pick by label)
        #   dropdown 1 = Appointment Category (AUTO-fills "Schengen Visa" — leave)
        #   dropdown 2 = Sub-category         (pick "Tourist"; selecting it fires
        #                the CheckIsSlotAvailable XHR — no Continue click needed)
        # Sweep centres with at most ONE switch between consecutive centres;
        # more in-page switching escalates Turnstile to an interactive captcha.
        subcat_label = _SUBCATEGORY_LABELS.get(visa_category, visa_category)
        results: dict[str, dict] = {}

        for idx, code in enumerate(vac_codes):
            centre_label = _VAC_LABELS.get(code, code)
            availability_data = None  # reset; _on_response refills per centre
            verb = "Switching to" if idx else "Selecting"
            try:
                log.info("%s Application Centre: %s", verb, centre_label)
                centre_select = page.locator("mat-select, select").nth(0)
                await centre_select.wait_for(state="visible", timeout=20_000)
                await centre_select.click()
                # Options render in a CDK overlay after the panel opens — wait
                # for them or we race an empty dropdown.
                await page.locator("mat-option").first.wait_for(
                    state="visible", timeout=15_000
                )
                await page.locator(
                    f'mat-option:has-text("{centre_label}")'
                ).first.click(timeout=15_000)

                # Selecting the centre fires the category auto-fill XHR, which
                # in turn loads the sub-category options. networkidle is
                # unreliable here (VFS holds a background poll open), so wait on
                # concrete elements: the sub-category control, then its options.
                subcat_select = page.locator("mat-select, select").nth(2)
                await subcat_select.wait_for(state="visible", timeout=20_000)
                await subcat_select.click()
                # Wait specifically for the tourist option — not just any
                # mat-option, which could match a lingering option from the
                # centre dropdown before the sub-category XHR has finished
                # populating this panel.
                log.info("Selecting sub-category (~%s)", subcat_label)
                tourist_option = page.locator(
                    "mat-option", has_text=re.compile(r"touris", re.I)
                ).first
                await tourist_option.wait_for(state="visible", timeout=20_000)
                # dispatch_event bypasses Playwright's pointer simulation,
                # avoiding two intermittent failures: (a) cdk-overlay-backdrop
                # intercepting pointer events, (b) element outside the viewport
                # when the CDK panel extends below the visible area.
                await tourist_option.dispatch_event("click")
            except Exception:
                try:
                    await page.screenshot(
                        path="debug_application_detail.png", full_page=True
                    )
                    opts = await page.locator("mat-option").all_text_contents()
                    log.error("option labels seen: %r", opts)
                except Exception:
                    pass
                await context.close()
                raise RuntimeError(
                    f"Could not select centre {centre_label!r} / sub-category "
                    f"{subcat_label!r} on application-detail. Check "
                    "debug_application_detail.png and the logged option labels; "
                    "update _VAC_LABELS / _SUBCATEGORY_LABELS in vfs/auth.py."
                )

            # Sub-category selection triggers one CheckIsSlotAvailable;
            # _on_response captures it. Wait ~30s for that single response.
            for _ in range(60):
                if availability_data is not None:
                    break
                await asyncio.sleep(0.5)

            if availability_data is None:
                try:
                    await page.screenshot(
                        path="debug_application_detail.png", full_page=True
                    )
                except Exception:
                    pass
                await context.close()
                raise RuntimeError(
                    f"No CheckIsSlotAvailable response for centre "
                    f"{centre_label!r}. Check debug_application_detail.png."
                )

            results[code] = availability_data
            log.info("Captured availability for %s.", centre_label)

        # NOTE: saved_state was captured before the booking flow (above);
        # re-capturing here would persist the clobbered loginResponse.
        await context.close()

    if not results:
        raise RuntimeError("No centres swept — vac_codes was empty.")

    log.info("Availability captured for %d centre(s) via browser.", len(results))
    return results, saved_state
