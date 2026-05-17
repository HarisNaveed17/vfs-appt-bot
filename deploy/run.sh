#!/usr/bin/env bash
# Wrapper invoked by the vfs-check systemd user service.
#
# Runs the bot headed under a virtual display (Xvfb) so Cloudflare/Turnstile
# sees a real-display Chrome fingerprint while the job stays fully unattended.
# check.py keeps its built-in 0-240s jitter (no --no-jitter) so runs never
# land on a predictable wall-clock minute.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REPO_DIR"

exec xvfb-run -a uv run python check.py
