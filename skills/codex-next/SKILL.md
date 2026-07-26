---
name: codex-next
description: "Choose the local Codex account with the best direct rate-limit headroom, while accounting for earned reset credits. Use when the user asks '/codex-next', 'which Codex account', or 'where should I run Codex'."
allowed-tools:
  - Bash
---

# Codex Next

Recommend a local Codex account profile from sanitized Codex app-server snapshots.
Never inspect credential files, browser storage, or raw app-server account data.

## Run

From the dashboard repository:

```bash
python3 cli.py codex-next
```

## What it considers

- Remaining five-hour and weekly Codex windows.
- Upcoming window resets.
- Earned rate-limit reset credits and their earliest reported expiry.

The command prefers direct, currently usable headroom. It then flags accounts
that have reset credits as **recoverable capacity**. It never calls the Codex
reset-credit consume endpoint, never redeems a credit, and never makes that
decision for the user.

## If snapshots are stale

Ask the user to switch their official Codex login interactively, then run:

```bash
python3 cli.py accounts profiles snapshot --profile <profile-id> --provider codex
```

Only the user performs sign-in or reset-credit redemption. After a user manually
redeems a reset credit in the official Codex experience, refresh the snapshot
before recommending that profile again.

## Decision rule

1. Prefer active profiles with at least 15% five-hour capacity and non-zero
   weekly capacity; among ties, drain the weekly window that resets sooner.
2. Next, surface profiles with reset credits, ordering an expiring credit ahead
   of a non-expiring/unknown one.
3. Leave throttled, exhausted, inactive, and unsnapshotted profiles last.
