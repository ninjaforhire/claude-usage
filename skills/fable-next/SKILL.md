---
name: fable-next
description: "Choose the local Claude Max account with the most reliable Fable 5.1 capacity. Use when the user asks '/fable-next', 'which account for Fable', or 'where should I run Fable'."
allowed-tools:
  - Bash
---

# Fable Next

Recommend a local Claude account profile using sanitized snapshots created by this
dashboard. Do not read browser data, keychains, credential files, transcripts,
or environment variables.

## Run

From the dashboard repository:

For a person choosing an interactive profile:

```bash
python3 cli.py fable-next --profiles
```

For a logic router, request the PII-free machine contract:

```bash
python3 cli.py fable-next --json --refresh
```

The refresh is required because automated routing accepts usage snapshots no
older than 15 minutes. The JSON response names `claude-opus-5` as the backup. Route to Fable 5.1 only
when `available` is `true`; otherwise use the backup immediately. Do not wait,
retry Fable, or require Fable as a strict dependency.

If the repository uses `python` rather than `python3`, use that interpreter
consistently.

## Meaning

Fable 5.1 is limited to 50% of an eligible Max or premium weekly subscription
window. The supported snapshot provides only total weekly remaining capacity, so
the command reports the conservative, guaranteed amount:

```text
guaranteed Fable 5.1 headroom = max(0, weekly remaining − 50)
```

It is intentionally not presented as an exact per-model Fable meter. A value of
zero means no Fable capacity is guaranteed from the available aggregate data;
it does not prove that Fable is unavailable. Automated routes still fail over
to Opus 5 so a stale or uncertain snapshot cannot block work.

## If snapshots are stale

Ask the user to switch their official Claude Code login interactively, then
run:

```bash
python3 cli.py accounts profiles snapshot --profile <profile-id> --provider claude
```

Only the user performs sign-in or account switching. Never automate `/login`,
copy credentials, or alter a profile's expected email to force a match.

## Decision rule

1. Prefer an active profile with non-zero guaranteed Fable headroom.
2. Among those, prefer the weekly window that resets sooner, so capacity does
   not expire unused.
3. Treat an inactive profile or a missing weekly snapshot as unavailable until
   the user explicitly refreshes it.
