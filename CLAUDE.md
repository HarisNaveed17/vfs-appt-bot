# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

A personal monitor for the VFS Global Netherlands visa appointment booking page. It polls the availability endpoint on a jittered schedule and, when a new slot appears, fans out a notification to the user's Gmail (SMTP) and WhatsApp (Twilio). It does **not** auto-book — it only alerts.

Status: initial scaffold complete. Core pipeline works end-to-end; the `_map_slot()` field names in `vfs/scraper.py` are inferred — verify against a real non-empty response once slots open.

## Stack and hosting

- **Language:** Python 3.11+.
- **HTTP:** `httpx` (sync) for the primary path. `playwright` is the fallback when VFS gates a page behind JS/Cloudflare and `httpx` cannot reach the JSON/availability response directly.
- **Notifications:** Gmail SMTP (`smtplib` + app password) and Twilio WhatsApp (`twilio` SDK).
- **Runtime:** GitHub Actions on a `schedule:` cron. The bot is a stateless one-shot script invoked per run.
- **Dependency management:** `uv` with `pyproject.toml` and a committed `uv.lock`. Use `uv add <pkg>` / `uv add --dev <pkg>` to change deps — do not edit `[project.dependencies]` by hand.

## Architecture

Per-run flow:

1. Load config + secrets from env (`.env` locally, GitHub Actions secrets in CI).
2. Fetch availability via `httpx` first. Only spin up Playwright if the HTTP path returns a challenge page or an unparseable body. Browser launches are expensive on Actions runners, so they should not be the default.
3. Compare the fetched slot set against the **last-seen state** (see below). Emit alerts only for genuinely new slots, not on every poll.
4. Dispatch notifications in parallel where practical; one channel's failure must not block the other.
5. Persist updated state.

Suggested module layout (do not create files preemptively — add them as work requires):

- `check.py` — entry point invoked by the workflow.
- `vfs/scraper.py` — fetch + parse availability. HTTP path and Playwright fallback live here.
- `vfs/notifier.py` — Gmail + Twilio dispatchers behind a small common interface.
- `vfs/state.py` — load/save last-seen slot fingerprint.
- `vfs/config.py` — env parsing and validation.

## State persistence on GitHub Actions

Actions runners are ephemeral, so "what slots did we already alert on" must be stored outside the runner. Default approach: commit a small `state.json` back to a dedicated `state` branch (or to `main` with `[skip ci]` in the commit message to avoid recursive workflow triggers). Alternatives if that proves noisy: GitHub Gist via PAT, or Actions cache (note: cache is best-effort and can be evicted).

When implementing, keep `state.json` to a fingerprint of the slot set (e.g., a hash of sorted `(date, time, center)` tuples) plus a `last_alert_at` timestamp — not the raw HTML.

## Anti-detection / rate-limit strategy

VFS is known to throttle and IP-ban aggressive scrapers, and GitHub Actions IP ranges are shared and sometimes pre-flagged. Mitigations the implementation must respect:

- **Jittered cadence.** Cron at a fixed coarse interval (e.g., every 10 min) and add `random.uniform(0, 240)` seconds of sleep at the start of `check.py`. Do not poll on the minute.
- **Single request per run** to the availability endpoint; no parallel fan-out, no warm-up requests unless absolutely required by the site.
- **Realistic headers.** Set a current desktop `User-Agent`, `Accept-Language`, and `Referer`. Reuse the same UA across runs to look like one user, not rotate per request.
- **Honor backoff.** On 429/403/Cloudflare challenge, skip notifications, log, and exit cleanly so the next scheduled run retries — do not retry in-process.
- **Quiet hours.** Add an optional config to suppress polling overnight CET to reduce request volume.

## Notifications

- Both channels share a single "new slots" payload. Format the message once; render per-channel.
- Gmail: send via `smtp.gmail.com:587` with STARTTLS, using a Gmail app password (not the account password). Sender and recipient default to the user's Gmail.
- Twilio WhatsApp: use the sandbox number for development; switch to an approved sender for production. The recipient must have opted into the sandbox.
- Deduplicate at the state layer, not at the notifier — a notifier should always send what it is given.

## API shape

The availability endpoint is a POST:

```
POST https://lift-api.vfsglobal.com/appointment/CheckIsSlotAvailable
```

Required headers: `authorize` (session token), `clientsource` (app token), `route` (`{countryCode}/en/{missionCode}`).

Request body: `{ countryCode, missionCode, vacCode, visaCategoryCode, roleName: "Individual", loginUser, payCode: "" }`

Response when no slots: `{ "earliestDate": null, "earliestSlotLists": [], "error": { "code": 1035, ... } }`

Response when slots exist: `earliestSlotLists` is non-empty; `_map_slot()` field names (`slotDate`, `slotTime`) are guessed and must be verified against a real response.

## Authentication

Two auth modes; `VFS_AUTHORIZE` takes priority if set:

| Mode | How | When to use |
|---|---|---|
| `VFS_PASSWORD` set | Playwright navigates to the VFS site, fills the login form (browser JS handles RSA password encryption + Cloudflare Turnstile), intercepts the `/user/login` response, extracts `accessToken` | Fully automated — recommended for GitHub Actions |
| `VFS_AUTHORIZE` set | Uses the stored token directly, skips Playwright | Faster; use when debugging or when Playwright is blocked by Cloudflare |

The `accessToken` from the login response body maps 1-to-1 to the `authorize` header in subsequent API calls.

`VFS_CLIENT_SOURCE` is an RSA-encrypted client-identifier sent as a request header. It appears to differ per-session (not a static value), but the server may not strictly validate it. The bot omits it if not set. If requests start failing with 400/401, try capturing a fresh value from DevTools.

The Playwright login selectors in `vfs/auth.py` target `input[formcontrolname="username"]` (Angular `formcontrolname` attribute). If the login form changes, update those selectors.

## Required secrets / env vars

All consumed via `os.environ`. Locally use `.env` (see `.env.example`); in GitHub Actions add as repo Secrets:

- `VFS_COUNTRY_CODE` — e.g. `pak`
- `VFS_MISSION_CODE` — e.g. `nld`
- `VFS_VAC_CODE` — VAC centre code, e.g. `NISL` (Islamabad), `KCHI` (Karachi)
- `VFS_VISA_CATEGORY` — visa category code, e.g. `TR` (Tourist)
- `VFS_EMAIL` — VFS account login email
- `VFS_PASSWORD` — plaintext VFS password; Playwright handles encryption. Use this for automated runs.
- `VFS_AUTHORIZE` — optional override: raw `accessToken` from `/user/login` response. Takes priority over `VFS_PASSWORD`. Expires with the session.
- `VFS_CLIENT_SOURCE` — optional; `clientsource` header value from DevTools. Capture once and store.
- `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ALERT_EMAIL_TO`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, `ALERT_WHATSAPP_TO`
- `QUIET_HOURS_CET` — optional, e.g. `23-07`; can be a repo Variable rather than a Secret

Never echo secret values in logs.

## Commands

These do not exist yet; create them as the implementation lands and update this section if names change.

```bash
# install deps (creates .venv and uv.lock)
uv sync

# one-shot local run against the live site (uses .env)
uv run python check.py

# dry run: fetch + parse but do not send notifications or update state
uv run python check.py --dry-run

# unit tests (fixtures should include captured VFS responses, not live calls)
uv run pytest

# run a single test
uv run pytest tests/test_state.py::test_diff_returns_only_new_slots -q
```

The GitHub Actions workflow at `.github/workflows/check.yml` runs `uv run python check.py` directly — no make/just layer needed for a script this small.

## Things to be deliberate about

- **Do not auto-book.** This bot's contract is "notify only." Any code that submits a booking form is out of scope.
- **Do not parallelize polling.** One request per run, period. The whole point of jitter + low frequency is to stay under VFS's radar.
- **Do not log response bodies** in CI — they may contain partial PII if the user is logged in. Log slot counts and status codes only.
- **Treat the VFS HTML/JSON shape as unstable.** Parsing code should fail loudly with a clear error rather than silently returning "no slots" when the page structure changes, otherwise outages will look indistinguishable from a quiet market.
