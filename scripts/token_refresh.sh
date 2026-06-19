#!/usr/bin/env bash
# Keep the usage-dashboard account orbs alive without manual re-login.
#
# OAuth refresh tokens are single-use: each use rotates to a new one. If two
# holders (Claude Code's keychain + this dashboard's store) both hold the same
# account's chain, one gets invalidated and that orb 400s. This job keeps the
# store the sole rotating consumer of every *idle* account, and re-captures the
# *live* account before Claude Code rotates it out from under us:
#
#   1. accounts add --quiet  — snapshot the currently-logged-in Claude Code
#      keychain into the store (history-safe merge; runs even if the dashboard
#      server is down).
#   2. POST /api/accounts/refresh — rotate + re-save every other tracked
#      account's token before it lapses (and re-render the orbs).
#
# Wired to launchd via com.mighty.claude-usage-token-refresh.plist (every 15m).
set -uo pipefail

REPO="/Users/mightydesigncenter/tools/claude-usage"
PORT="${USAGE_DASHBOARD_PORT:-8080}"

/usr/bin/python3 "$REPO/cli.py" accounts add --quiet || true
/usr/bin/curl -fsS -X POST "http://127.0.0.1:${PORT}/api/accounts/refresh" >/dev/null || true
