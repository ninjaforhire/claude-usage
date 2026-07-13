#!/usr/bin/env bash
# claude-usage 15-minute maintenance tick (launchd: com.mighty.claude-usage-token-refresh).
# Two jobs, both server-independent so they survive :8080 being retired:
#   - keep the account orbs alive without manual re-login
#   - run the daemon-freshness watcher (re-homed off the old :8080 process)
#
# OAuth refresh tokens are single-use: each use rotates to a new one. If two
# holders (Claude Code's keychain + this dashboard's store) both hold the same
# account's chain, one gets invalidated and that orb 400s. This job keeps the
# store the sole rotating consumer of every *idle* account, and re-captures the
# *live* account before Claude Code rotates it out from under us:
#
#   1. accounts add --quiet  — snapshot the currently-logged-in Claude Code
#      keychain into the store (history-safe merge).
#   2. accounts refresh       — rotate + re-save every other tracked account's
#      token before it lapses. Server-independent: calls fetch_all_usage()
#      directly, so it works with NO dashboard server running (the standalone
#      :8080 dashboard has been retired in favor of Jimbo's /usage tab).
#
# Wired to launchd via com.mighty.claude-usage-token-refresh.plist (every 15m).
set -uo pipefail

REPO="/Users/mightydesigncenter/tools/claude-usage"

echo "[$(date -u +%FT%TZ)] tick"

/usr/bin/python3 "$REPO/cli.py" accounts add --quiet || true
/usr/bin/python3 "$REPO/cli.py" accounts refresh || true
/usr/bin/python3 "$REPO/cli.py" freshness-tick || true
