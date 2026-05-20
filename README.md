# vfs-appt-bot

A personal monitor for the VFS Global Netherlands visa appointment page (Pakistan → Netherlands). On each run it drives a real Chrome browser through login → the booking flow → an availability check for each configured centre, then alerts via **email** (audit log) and **Pushover Emergency** (the alarm you can't sleep through) when new slots appear. It does **not** auto-book — notify only.

## How it works

Each run is stateless and one-shot:

1. Launches Chrome via `patchright` (a Playwright fork with Cloudflare/Turnstile stealth) against a dedicated persistent browser profile.
2. Logs in fresh with your VFS credentials — the page JS does RSA encryption + Cloudflare Turnstile, which patchright passes automatically at sane cadence.
3. Navigates the booking form on `/application-detail`, selects each configured centre in sequence, intercepts the `CheckIsSlotAvailable` XHR response for each.
4. Diffs the parsed slot set against `state.json` (the last-seen fingerprint). Alerts only on genuinely new slots.
5. Dispatches email + Pushover independently, then persists updated state.

The availability endpoint is Cloudflare-protected and cannot be hit with plain HTTP — browser automation is the only viable path.

## Prerequisites

- Linux with a persistent, **residential IP** (cloud/datacenter IPs are Cloudflare/VFS-flagged and blocked).
- Python 3.11+ managed by [`uv`](https://github.com/astral-sh/uv).
- Real Chrome installed (the `channel="chrome"` launch path requires it).
- `Xvfb` for headless-but-real-fingerprint operation.
- A [Pushover](https://pushover.net) account with an Application token.
- A Gmail account with an [App Password](https://myaccount.google.com/apppasswords) enabled.

## Setup

### 1. Install dependencies

```bash
sudo apt install -y xvfb
uv sync
uv run patchright install chrome
```

### 2. Configure environment

```bash
cp .env.example .env
# edit .env with your credentials — see Configuration below
```

### 3. Deploy the systemd timer

The service unit is hardcoded to `/home/haris/projects/vfs-appt-bot`. If you clone elsewhere, edit `deploy/vfs-check.service` to match before linking.

```bash
chmod +x deploy/run.sh
mkdir -p ~/.config/systemd/user
ln -sf "$PWD/deploy/vfs-check.service" ~/.config/systemd/user/vfs-check.service
ln -sf "$PWD/deploy/vfs-check.timer"   ~/.config/systemd/user/vfs-check.timer

sudo loginctl enable-linger "$USER"          # run timer even when logged out
systemctl --user daemon-reload
systemctl --user enable --now vfs-check.timer
```

### 4. Verify

```bash
systemctl --user list-timers vfs-check.timer   # next fire time
journalctl --user -u vfs-check -f              # live logs
```

Steady state is silent — "no notifications" means no slots, which is normal. Watch the journal to confirm runs are executing.

## Configuration

All variables are read from `.env` in the repo directory.

### VFS booking parameters

| Variable | Example | Notes |
|---|---|---|
| `VFS_COUNTRY_CODE` | `pak` | Origin country code used in the VFS URL path. |
| `VFS_MISSION_CODE` | `nld` | Destination country code used in the VFS URL path. |
| `VFS_VAC_CODE` | `NISL,Netherlands Lahore` | **Comma-separated** list of centres to sweep, in order. Each entry is either a known code (`NISL` → `Netherlands Islamabad`, `NLAH` → `Netherlands Lahore`) or a raw UI label as it appears in the dropdown. At most two centres are supported per run — a third would require a second centre switch, which escalates Cloudflare Turnstile to an interactive captcha. |
| `VFS_VISA_CATEGORY` | `TR` | Used internally for state fingerprinting. The sub-category is matched by the regex `/touris/i` against dropdown labels, not this value. |
| `VFS_EMAIL` | `you@example.com` | VFS account email. |
| `VFS_PASSWORD` | `your-password` | VFS account password (plaintext here; the browser's page JS encrypts it before transmission). |

### Notifications

| Variable | Notes |
|---|---|
| `GMAIL_USER` | Sending Gmail address. |
| `GMAIL_APP_PASSWORD` | 16-character app password from Google Account → Security → App passwords. Not your Gmail login password. |
| `ALERT_EMAIL_TO` | Recipient address for the audit-log email. |
| `PUSHOVER_TOKEN` | Application API token from [pushover.net/apps/build](https://pushover.net/apps/build). |
| `PUSHOVER_USER` | Your Pushover user key from the dashboard. |

All of the above are required. `config.load()` raises `Missing required env var: …` if any are blank.

Pushover is sent at **Emergency priority** (`priority=2`): it re-alerts every 60 seconds until you acknowledge it in the app, giving up after 1 hour. Email is a plain STARTTLS message to `smtp.gmail.com:587` — useful as a record of when slots appeared, but not suitable as a real-time alarm.

## CLI usage

```bash
# Standard one-shot run (jittered; uses .env)
uv run python check.py

# Skip the 0-240s jitter (for local testing)
uv run python check.py --no-jitter

# Fetch and parse without sending alerts or updating state
uv run python check.py --dry-run --no-jitter

# Open a visible browser for manual login, then save session.json and exit
# (useful to pre-warm the browser profile before the first timer-driven run)
uv run python check.py --bootstrap-session

# Run tests
uv run pytest
```

## Cadence and rate limiting

The timer fires roughly every 30 minutes (measured from the end of the previous run), with up to 3 minutes of OS-level jitter plus up to 4 minutes of in-process jitter. Effective gap between runs is 30–37 minutes.

**Do not tighten the cadence.** Every run performs a full browser login. Rapid successive logins (several within minutes) escalate Cloudflare Turnstile from an invisible auto-pass to an interactive checkbox that headless automation cannot solve, and also trigger the VFS account-level throttle.

The backoff file (`backoff_until.json`) is written automatically when an account restriction is detected. While active, the bot skips runs silently and logs the remaining cooldown time. The backoff window is 3 hours.

## Known drawbacks

### Account restriction

VFS imposes a per-account throttle that activates after login — not at the IP or browser-fingerprint level. If the account gets flagged (typically from running too many logins in a short window), all subsequent logins return a "user is temporarily restricted" error for a period of several hours.

The bot handles this gracefully: `AccountRestrictedError` is caught, a 3-hour backoff is written to disk, and subsequent runs skip silently until it clears. But you will miss slot notifications during the cooldown window.

Mitigations in place: conservative cadence (~30 min), randomised jitter, residential IP. The account restriction is a hard external constraint — there is no way to bypass it from the client side; you can only wait it out.

### Login every run

Session reuse across runs has been investigated extensively and does not work. The VFS auth token appears to be bound server-side to rotating Cloudflare cookies (`__cf_bm`, `cf_clearance`) that cannot be replayed from a saved `storage_state`. `localStorage["loginResponse"]` is the string `"Individual"` (the Angular role name), not the auth token.

The consequence: every run is a full browser login. This is fine at conservative cadence — well-spaced single logins succeed consistently — but it does mean the bot is sensitive to cadence and cannot recover quickly from a restriction.

### Cloudflare / residential IP requirement

Datacenter and commercial VPN IPs are flagged at Cloudflare's edge. The bot must run from a residential IP. GitHub Actions and similar CI runners will not work. A home server or a residential proxy are the only realistic options.

### Turnstile and centre switching

Selecting the appointment sub-category fires `CheckIsSlotAvailable`. The bot captures that XHR for each centre by switching centres inside the same browser session. Switching centres more than once per session (i.e. more than two centres total) consistently escalates Turnstile to an interactive captcha. Stick to at most two centres in `VFS_VAC_CODE`(Dev Notes: This is not strictly true, and there may be a possible workaround here, stay tuned)

### DOM instability

VFS uses an Angular SPA. Dropdown labels, form control selectors, and API response shapes can change without notice. The bot fails loudly when it can't find expected elements (screenshots dumped to `debug_*.png`, option labels logged) rather than silently returning "no slots" — a silent failure would be indistinguishable from an empty market.

## File layout

```
check.py              entry point; jitter, orchestration, diff, dispatch
vfs/
  auth.py             browser automation: login, booking flow, centre sweep
  scraper.py          parse-only: CheckIsSlotAvailable response → Slot objects
  notifier.py         send_email, send_pushover, format_message
  state.py            load/save last-seen slot fingerprint (state.json)
  config.py           env parsing
deploy/
  run.sh              xvfb-run wrapper called by the systemd service
  vfs-check.service   systemd user service unit
  vfs-check.timer     systemd user timer unit (~30 min cadence)
  README.md           deployment setup details
state.json            persisted slot fingerprint (auto-managed)
session.json          persisted browser session/cookies (auto-managed)
```

The Chrome profile lives at `~/.config/google-chrome-vfs-bot` (a dedicated dir — Chrome blocks remote debugging on the default profile).