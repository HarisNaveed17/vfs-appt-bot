# Deployment — self-hosted systemd user timer

The bot must run from a **residential IP** (datacenter/cloud IPs are
Cloudflare/VFS-flagged) on a **persistent host** (the Chrome profile,
`session.json`, and `state.json` must survive between runs). It runs **headed
under Xvfb** — a real Chrome fingerprint, but unattended (no screen, no human).

Target host: this machine. Every run logs in (session reuse is unsolved — see
`CLAUDE.md`), so cadence is deliberately conservative: ~20 min + jitter.

## One-time setup (run these yourself)

```bash
# 1. Virtual display + deps
sudo apt install -y xvfb
uv sync
uv run patchright install chrome

# 2. Make the wrapper executable
chmod +x deploy/run.sh

# 3. Install the user units (symlinked, so edits here take effect)
mkdir -p ~/.config/systemd/user
ln -sf "$PWD/deploy/vfs-check.service" ~/.config/systemd/user/vfs-check.service
ln -sf "$PWD/deploy/vfs-check.timer"   ~/.config/systemd/user/vfs-check.timer

# 4. Let the timer run even when you're not logged in
sudo loginctl enable-linger "$USER"

# 5. Start it
systemctl --user daemon-reload
systemctl --user enable --now vfs-check.timer
```

## Operate

```bash
# When does it next fire?
systemctl --user list-timers vfs-check.timer

# Follow logs (the bot logs slot counts + status, never secrets/PII)
journalctl --user -u vfs-check -f

# Force a one-off run now
systemctl --user start vfs-check.service

# Pause / resume
systemctl --user stop vfs-check.timer
systemctl --user start vfs-check.timer
```

## Notes

- Paths in `vfs-check.service` are hardcoded to
  `/home/haris/projects/vfs-appt-bot`. Move the repo → edit the unit and
  `systemctl --user daemon-reload`.
- `.env` is read from the repo dir (the service sets `WorkingDirectory`). It
  must contain `VFS_*`, `GMAIL_*`, and `PUSHOVER_*` (see `.env.example`).
- Steady state is **0 slots → silent**. The bot only notifies (email record +
  Pushover Emergency) when a *new* slot appears, so "no notifications" is
  normal and means it's working, not broken — watch the journal to confirm
  runs are happening.
- Effective cadence ≈ 20 min between runs + up to ~3 min systemd jitter + up
  to 4 min in-process jitter; never on a fixed minute. Don't tighten it —
  every run logs in and rapid logins escalate Turnstile.
