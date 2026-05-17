# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

A personal monitor for the VFS Global Netherlands visa appointment booking page (Pakistan → Netherlands, centres **Islamabad + Lahore**). Each run drives a real browser through login → the booking flow → the availability check for each centre, and when a new slot appears it alerts via **email (record/audit log)** and **Pushover Emergency priority (the real-time, can't-sleep-through-it alarm)**. It does **not** auto-book — it only alerts.

Status: pipeline works end-to-end and is validated repeatedly (login → two-centre sweep → parse → diff → notify). `0 slots` is the normal steady state; the bot alerts when that goes above zero. Slot field names in `_map_slot()` verified against a real response: each entry is `{ "date": "MM/DD/YYYY 00:00:00", "applicant": "1" }` — no per-slot time field.

**Hard-won context lives in the agent memory** (`project-account-restriction`, `project-booking-flow`, `project-vfs-slot-shape`, `reference-patchright-init-script-bug`). Read those before changing auth/booking/session code — several non-obvious findings there cost a lot to learn.

## Stack and hosting

- **Language:** Python 3.11+ (3.14 in the current venv).
- **Browser automation:** `patchright` (a stealth Playwright fork) driving **real Chrome** (`channel="chrome"`) via `launch_persistent_context`. This is the **only** path — see below; the old "httpx-first" idea is dead.
- **HTTP:** `httpx` is used only for the Pushover API call. **Directly hitting `CheckIsSlotAvailable` with `httpx` does not work** — Cloudflare blocks non-browser requests — so there is no direct-API path; availability is captured by intercepting the XHR inside the real browser.
- **Notifications:** Gmail SMTP (`smtplib`, app password) as a record/audit log; **Pushover** (`httpx` POST, Emergency priority) as the real-time alarm. Twilio/WhatsApp was removed.
- **Runtime:** a stateless one-shot per run, driven by a **systemd user timer on this host** (~20 min + jitter), headed Chrome under `Xvfb`, residential IP. Cloud runners (GitHub Actions) are *not* used — datacenter IPs are Cloudflare/VFS-flagged. See `deploy/` (`deploy/README.md` for setup).
- **Dependency management:** `uv` with `pyproject.toml` + committed `uv.lock`. Use `uv add` / `uv remove` — do not hand-edit `[project.dependencies]`. Current deps: `patchright`, `httpx`, `python-dotenv` (twilio removed). After `uv sync`, run `uv run patchright install chrome`.

## Architecture

Per-run flow (actual):

1. Load config + secrets from env (`.env` locally).
2. Launch patchright + real Chrome on a **dedicated persistent profile** (`vfs.auth.BOT_PROFILE_DIR` = `~/.config/google-chrome-vfs-bot`; Chrome blocks remote-debugging on the default profile, so a dedicated dir is mandatory).
3. Restore `session.json` cookies; restore localStorage by `goto`-ing the VFS origin then `page.evaluate` (NOT `add_init_script` — that breaks navigation under patchright, see deliberate-about). Try `/dashboard`.
4. The cached VFS session is essentially never valid across runs (see Authentication) → fall back to an automated login (fills `VFS_EMAIL`/`VFS_PASSWORD`; the page JS does RSA + Cloudflare Turnstile; patchright auto-passes Turnstile when cadence is sane).
5. Booking flow on `/application-detail`, **sweeping each centre in `VFS_VAC_CODES` with at most ONE in-page centre switch** (more switching escalates Turnstile to an interactive captcha). Selecting the sub-category fires `CheckIsSlotAvailable`; the response is intercepted per centre.
6. Parse + diff each centre's response against the **last-seen state**; alert only on genuinely new slots.
7. Dispatch email (record) then Pushover (alarm), each independent — one failing must not block the other.
8. Persist updated state + the (best-effort) session.

Module layout:

- `check.py` — entry point; jitter, session load/save, fetch orchestration, diff, dispatch.
- `vfs/auth.py` — patchright session: persistent context, localStorage restore, login fallback, the one-switch multi-centre booking sweep. Exports `BOT_PROFILE_DIR`.
- `vfs/scraper.py` — parse-only: a `CheckIsSlotAvailable` dict → `Slot`s (`parse_availability`/`_parse`/`_map_slot`). No fetching.
- `vfs/notifier.py` — `send_email` + `send_pushover` + `format_message`.
- `vfs/state.py` — load/save last-seen slot fingerprint.
- `vfs/config.py` — env parsing; `VFS_VAC_CODE` is comma-split into `vfs_vac_codes`.

## State persistence

The host is persistent, so this is trivial: `state.json`, `session.json`, and
the Chrome profile (`~/.config/google-chrome-vfs-bot`) are plain local files
that survive between runs. No git-commit-back / cache / `SESSION_JSON` secret
machinery — those were removed along with the Actions workflow.

`state.json` holds a fingerprint of the slot set (not raw HTML); diffing it is
what makes alerts fire only on *genuinely new* slots.

## Anti-detection / rate-limit strategy

Cloudflare Turnstile + a VFS account/IP throttle are the real adversaries. Findings from this build:

- **patchright defeats Cloudflare/Turnstile** reliably *at sane cadence* — Turnstile auto-passes invisibly. Do not add `--disable-blink-features`, `navigator.webdriver` patches, or a custom UA: those are themselves fingerprints; patchright handles them lower down.
- **Turnstile escalates to an interactive checkbox** (which headless cannot solve) when behaviour looks bot-like: many logins in a short window, or repeated/back-and-forth centre switching on `/application-detail`. Empirically, well-spaced single logins are tolerated (many succeeded same-day, zero 403s); the one manual-captcha was self-inflicted by ~5 logins in minutes.
- **Cadence.** Schedule a coarse interval and keep `random.uniform(0, 240)`s jitter at the start of `check.py` (`--no-jitter` to skip for local testing). Keep frequency conservative — every run logs in (no working session reuse), so this is the dominant constraint.
- **One availability check per centre per run; at most one centre switch.** No re-polling in-page.
- **IP reputation.** Datacenter/cloud IPs (GitHub Actions, commercial VPNs incl. Surfshark) are flagged. Use a residential IP — a self-hosted/home runner. The VFS account-level throttle is keyed to the account (it triggers *after* login submit); cooldown relaxes within hours.
- **Honor backoff.** On 403/restriction/Cloudflare challenge, `_fetch` already logs and returns `None` so the run skips cleanly and the next scheduled run retries — never retry in-process.

## Notifications

- Both channels share one `format_message(new_slots)` payload.
- **Email (`send_email`)** — `smtp.gmail.com:587` STARTTLS + Gmail app password. Kept as a **record/audit log** (when slots opened), not the thing you watch live.
- **Pushover (`send_pushover`)** — `httpx` POST to `api.pushover.net/1/messages.json` with **`priority=2` (Emergency)**, `retry=60`, `expire=3600`: re-alerts every 60s until acknowledged in the Pushover app, giving up after 1h. This is the can't-sleep-through-it alarm. Needs `PUSHOVER_TOKEN` (a Pushover Application) + `PUSHOVER_USER`.
- Both are **required** in config and dispatched independently (one failing must not block the other).
- Deduplicate at the state layer, not the notifier — a notifier always sends what it is given.
- **Untested:** the live Pushover/email *send* has not been exercised (steady state is 0 slots, so `_dispatch` never fired). Verify with a deliberate test before relying on it.

## API shape

The availability endpoint is a POST (the bot does **not** call this directly — Cloudflare blocks non-browser requests; it intercepts this XHR fired by the SPA when the sub-category is selected):

```
POST https://lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable
```

Required headers: `authorize` (session token), `clientsource` (app token), `route` (`{countryCode}/en/{missionCode}`).

Request body: `{ countryCode, missionCode, vacCode, visaCategoryCode, roleName: "Individual", loginUser, payCode: "" }`

Response when no slots: `{ "earliestDate": null, "earliestSlotLists": [], "error": { "code": 1035, ... } }`

Response when slots exist: `{ "earliestDate": "05/15/2026 00:00:00", "earliestSlotLists": [{ "applicant": "1", "date": "05/15/2026 00:00:00" }], "error": null }` — each entry has `date` and `applicant` count; no per-slot time field.

## Authentication

**Browser login via `VFS_PASSWORD`, every run.** `vfs/auth.py` navigates to `/login`, fills `VFS_EMAIL`/`VFS_PASSWORD`, the page JS does RSA password encryption + Cloudflare Turnstile (patchright auto-passes), then the booking flow proceeds. There is no other path: the old `VFS_AUTHORIZE`/`VFS_CLIENT_SOURCE` httpx route was removed because Cloudflare blocks direct API calls, making a stored token useless.

**Session reuse is UNSOLVED (deferred, not required).** Extensive investigation: the token is *not* recoverable across runs. `localStorage["loginResponse"]` is the 12-char string `"Individual"` (the roleName), **not** the auth token — do not chase it. Auth is likely bound server-side to rotating Cloudflare cookies that a saved `storage_state` can't replay. The reuse machinery in `auth.py` (localStorage restore via `goto`+`evaluate`, early `storage_state()` capture, stale-token-clear before login fallback) is **harmless but non-functional**: it attempts reuse, fails, and falls back to login cleanly. It is kept in case reuse is later cracked. **Design assumption: every run logs in.** This is fine — well-spaced single logins are tolerated; conservative cadence is the mitigation, not reuse.

`session.json` and the persistent Chrome profile still matter for **cf_clearance / Cloudflare fingerprint continuity**, just not for skipping the VFS login.

Login selectors in `vfs/auth.py` (`_USERNAME_SELECTORS`) target `input[formcontrolname="username"]` (Angular). If the form changes, update them; a `debug_no_form.png` is dropped on failure.

## Booking flow (`/application-detail`)

Reached via dashboard → "Start New Booking". Three dropdowns, in order:

1. **Application Centre** — options are full labels: `Netherlands Islamabad`, `Netherlands Lahore`, `Netherlands Karachi` (no codes). `vfs/auth.py:_VAC_LABELS` maps env codes → labels (`NISL`→`Netherlands Islamabad`), with raw-value fallback so an unknown-code centre works by giving its label directly in `VFS_VAC_CODE`.
2. **Appointment Category** — **auto-fills to "Schengen Visa"** once a centre is chosen. Do **not** interact with it.
3. **Sub-category** — pick the tourism option. The label varies/renders inconsistently (`Tourist` vs `Tourism`), so it is matched by the case-insensitive stem `/touris/i`, after explicitly waiting for the `mat-option`s to render (they load via an XHR after the centre selection; `networkidle` is unreliable here).

Selecting the sub-category fires `CheckIsSlotAvailable` (no "Continue" click). The sweep does centre 1 → capture → **exactly one switch** to centre 2 → re-select sub-category → capture. Repeated/extra switching escalates Turnstile to an interactive captcha. Failures drop `debug_dashboard.png` / `debug_application_detail.png` plus the logged option labels.

## Required secrets / env vars

All consumed from the `.env` in the repo dir (see `.env.example`); the systemd service sets `WorkingDirectory` so `python-dotenv` finds it:

- `VFS_COUNTRY_CODE` — e.g. `pak`
- `VFS_MISSION_CODE` — e.g. `nld`
- `VFS_VAC_CODE` — **comma-separated** centre list, swept in order, e.g. `NISL,Netherlands Lahore`. Each entry is a code (mapped via `_VAC_LABELS`) or a raw UI label.
- `VFS_VISA_CATEGORY` — e.g. `TR` (resolves to the `/touris/i` sub-category)
- `VFS_EMAIL` — VFS account login email
- `VFS_PASSWORD` — plaintext VFS password; the page JS encrypts it. **Required** (the only auth path).
- `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ALERT_EMAIL_TO` — record/audit email.
- `PUSHOVER_TOKEN`, `PUSHOVER_USER` — Pushover Emergency alarm. Token = a Pushover Application (pushover.net/apps/build); user = your user key.
- `session.json` is a local file in the repo dir (auto-managed; not an env var).

All notification + Pushover vars are **required** by `config.load()`; it raises `Missing required env var: …` if blank. Never echo secret values in logs.

## Commands

```bash
# install deps + the patchright Chrome driver (one-time)
uv sync
uv run patchright install chrome

# one-shot run against the live site (jittered; uses .env)
uv run python check.py

# skip the up-to-4-min jitter (local testing)
uv run python check.py --no-jitter

# dry run: fetch + parse, do NOT send notifications or persist state
uv run python check.py --dry-run --no-jitter

# capture a fresh session.json by logging in manually in a visible browser
uv run python check.py --bootstrap-session

# tests
uv run pytest
```

Notes:
- Local runs use **headed** Chrome (a window opens). For an unattended server, run **headed under `Xvfb`** (`xvfb-run …`) — pure headless is a weaker fingerprint and forcing `CI=1` switches to headless.
- `tests/test_scraper.py::test_parse_slot_list` currently **fails** — a stale fixture uses `slotDate`/`slotTime`; the verified-real shape (and `scraper._map_slot`) uses `date`. The fixture is wrong, not the code; do not "fix" it by changing `scraper.py`.
- Deployment is a **systemd user timer** on this host — see `deploy/` (`run.sh`, `vfs-check.service`, `vfs-check.timer`, `README.md`). There is no GitHub Actions workflow (removed; cloud datacenter IPs are blocked).

## Things to be deliberate about

- **Do not auto-book.** Contract is "notify only." Any code that submits a booking form is out of scope.
- **Never use `context.add_init_script()` / `page.add_init_script()`.** Under patchright + `launch_persistent_context` + real Chrome, *any* init script (even an empty one) makes the next `page.goto()` fail with `net::ERR_NAME_NOT_RESOLVED`. Confirmed by bisection; easily misdiagnosed as DNS/network/headless. To inject localStorage etc., navigate to the origin then `page.evaluate()`.
- **Do not rapid-fire logins or switch centres repeatedly.** Both escalate Turnstile to an interactive captcha that headless can't solve. One login + one centre-switch per run; conservative cadence.
- **Do not chase session reuse blindly.** It's a known-hard, deferred problem with multiple disproven theories (see memory `project-account-restriction`). It is *not* `localStorage.loginResponse`. Don't burn logins guess-fixing it.
- **Do not parallelize polling.** One availability check per centre per run.
- **Do not log response bodies** — they may contain PII when logged in. Log slot counts/status only.
- **Treat the VFS DOM/JSON as unstable.** Parsing/selectors should fail loudly (they screenshot + log option labels) rather than silently return "no slots" — an outage must not look like a quiet market.
