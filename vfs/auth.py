"""Playwright-based VFS login that returns a fresh accessToken."""
from __future__ import annotations

import asyncio
import logging

from playwright.async_api import async_playwright

log = logging.getLogger("vfs-bot")

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


def get_access_token(
    *,
    email: str,
    password: str,
    country_code: str,
    mission_code: str,
) -> str:
    return asyncio.run(_login(
        email=email,
        password=password,
        country_code=country_code,
        mission_code=mission_code,
    ))


async def _login(*, email: str, password: str, country_code: str, mission_code: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        context = await browser.new_context(user_agent=_USER_AGENT)
        page = await context.new_page()

        token: str | None = None

        async def _capture_token(response):
            nonlocal token
            if "/user/login" in response.url and response.ok:
                try:
                    data = await response.json()
                    token = data.get("accessToken") or token
                except Exception:
                    pass

        page.on("response", _capture_token)

        url = f"https://visa.vfsglobal.com/{country_code}/en/{mission_code}/"
        log.info("Navigating to %s", url)
        await page.goto(url, wait_until="networkidle", timeout=60_000)

        # VFS uses Angular Material — selectors based on formcontrolname.
        # Update if the site changes; check browser DevTools Elements panel.
        await page.wait_for_selector('input[formcontrolname="username"]', timeout=30_000)
        await page.fill('input[formcontrolname="username"]', email)
        await page.fill('input[formcontrolname="password"]', password)
        await page.click('button[type="submit"]')

        # Cloudflare Turnstile runs invisibly; allow up to 30s for it to resolve.
        for _ in range(60):
            if token:
                break
            await asyncio.sleep(0.5)

        await browser.close()

    if not token:
        raise RuntimeError(
            "VFS login did not return an accessToken within 30s. "
            "Possible causes: wrong credentials, Cloudflare blocked the headless browser, "
            "or login form selectors changed."
        )

    log.info("Fresh access token obtained via Playwright.")
    return token
