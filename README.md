# [HotFix Ops](https://hotfixops.com/) Usage Dashboard

A local, source-only [HotFix Ops](https://hotfixops.com/) dashboard for Claude
Code and OpenAI Codex usage.

It has three views:

- **Overview**: provider-separated account orbs and combined local totals
- **Claude**: Claude Code models, tokens, sessions, projects, and API-equivalent estimates
- **Codex**: Codex models, tokens, sessions, and projects

The dashboard uses your existing official CLI logins. It does not ask for,
copy, store, or send passwords, browser cookies, API keys, or OAuth tokens.

The interface uses the [HotFix Ops](https://hotfixops.com/) visual system and
local [HotFix Ops](https://hotfixops.com/) icon. It is a distinct local-first
product; the original project's MIT notices remain in this source distribution.

> This is an independent community project. It is not affiliated with,
> endorsed by, or supported by Anthropic or OpenAI.

## Fastest setup

**Recommended:** give [INSTALL_WITH_AGENT.md](INSTALL_WITH_AGENT.md) to a local
coding agent. It contains one copy-and-paste prompt that verifies the files,
tests the safe connectors, and starts the dashboard.

Manual setup:

macOS/Linux:

```bash
git clone <this-repository-url>
cd <this-repository-folder>
python3 -m unittest discover -s tests
python3 cli.py dashboard
```

Windows PowerShell:

```powershell
git clone <this-repository-url>
cd <this-repository-folder>
python -m unittest discover -s tests
python cli.py dashboard
```

Open <http://localhost:8080> if the browser does not open automatically.

## Requirements

- Python 3.9+
- Claude Code for Claude history/account detection
- Codex CLI or Codex app for Codex history/account detection
- No third-party Python packages
- Internet access only for normal provider CLI operations; Chart.js is vendored
  locally for privacy and offline dashboard rendering

Authenticate with the providers' official tools before starting:

```bash
claude
codex login
```

Complete provider login interactively. Do not give credentials to this project
or to an installation agent.

## What it reads

| Source | Purpose | Stored by this project |
|---|---|---|
| `~/.claude/projects/**/*.jsonl` | Claude Code local token/session history | Aggregated rows in `~/.claude/usage.db` |
| `~/.codex/sessions/**/*.jsonl` | Codex local token/session history | Aggregated rows in `~/.claude-codex-usage/codex.db` |
| `claude auth status --json` | Sanitized Claude account/plan status | Nothing |
| `codex app-server --stdio` | Sanitized Codex plan/rate-limit windows | Nothing |

The browser API receives aggregates and sanitized account fields only. It does
not receive prompts, message bodies, raw rollouts, credential paths, or tokens.

## Your data, not the publisher's

A fresh clone contains **no usage database, account snapshot, transcript, or
project history**. On first launch, the dashboard reads only the signed-in
user's local Claude/Codex files under their own home directory and creates their
own derived databases there. Their project names, branches, sessions, and costs
therefore belong to them, not to the person who shared the repository.

## App and browser coverage

This project tracks **local coding sessions that write the supported Claude Code
or Codex rollout formats**. That includes CLI/editor activity and can include
desktop Codex activity when it uses the same local Codex session store.

It does **not** claim to reconstruct general chats from claude.ai, ChatGPT,
Claude Desktop, ChatGPT Desktop, mobile apps, or cloud-only coding sessions.
Those services do not expose all consumer-subscription history through the local
transcript formats this project reads.

The account orbs are live provider status, not screenshots:

- Claude: supported account identity/plan from `claude auth status --json`
- Codex: supported rate-limit windows from the authenticated local Codex app server

## Subscription connectors

The optional connectors are deliberately separate:

| File | Behavior |
|---|---|
| `connectors/claude_subscription.py` | Reads safe Claude CLI status. Supported Claude subscription usage windows are unavailable by default. |
| `connectors/codex_subscription.py` | Reads supported Codex subscription windows through `codex app-server --stdio`. |

Run them independently:

macOS/Linux:

```bash
python3 connectors/claude_subscription.py
python3 connectors/codex_subscription.py
```

On Windows, replace `python3` with `python`.

### Claude subscription limitation

Claude Code currently provides supported CLI account status, but not a supported
non-interactive command for consumer subscription usage windows. The connector
therefore shows the account and plan while marking the limits unavailable.

Advanced users may pass an explicitly trusted local helper to
`read_subscription(helper=...)`. The helper must output only this normalized
shape:

```json
{
  "windows": {
    "five_hour": {
      "used_percent": 25,
      "remaining_percent": 75,
      "resets_at": "2026-07-24T20:00:00Z"
    },
    "seven_day": {
      "used_percent": 40,
      "remaining_percent": 60,
      "resets_at": "2026-07-30T20:00:00Z"
    }
  }
}
```

This repository intentionally does not include token extraction, keychain
reading, browser scraping, or private Anthropic endpoints.

The helper is executable code. Use only a helper you wrote or audited yourself;
the dashboard never downloads or enables one automatically.

## Refresh behavior

Opening the dashboard triggers one incremental scan of both local histories.
The **Refresh** button repeats the scan and reloads both supported account
connectors. Existing derived data is preserved if a provider is unavailable.

## Local-only accounts and first-run testing

Configured accounts are stored outside the checkout at
`~/.hotfix-ops-usage/accounts.json` with owner-only permissions. They contain
only labels, expected account emails, and sanitized plan/rate-limit snapshots,
never credentials, raw provider responses, transcripts, or project history.

Add an account profile from the currently signed-in official CLI:

```bash
python3 cli.py accounts setup --label "Work Max"
```

The guided command reads only safe account status, uses that identity to prevent
a later account mismatch, and never asks for credentials. If a provider is
signed out, it tells the user the official login command and saves nothing until
they return. For advanced/manual setup:

```bash
python3 cli.py accounts add --id max-one --label "Max account one" \
  --claude-email you@example.com --codex-email you@example.com --tier "Max 20x"
python3 cli.py accounts snapshot --profile max-one --provider all
python3 cli.py accounts list
```

Profiles are quota snapshots only. Claude and Codex local history stores do not
reliably tag rows with the signed-in account, so the dashboard never assigns
projects, branches, sessions, or local tokens to an account profile unless the user
has independently separated those source directories.

For an eligible Max/premium Claude profile with a live weekly snapshot, the UI
shows **guaranteed Fable headroom** as `max(0, weekly remaining − 50)`. It is a
conservative shared-limit calculation, not an invented per-model meter.

Codex snapshots also retain the number of available earned reset credits and an
optional earliest expiry. The dashboard and bundled skill only display them;
they never redeem a reset credit.

### Repeatable first-run preview

Use the isolated test mode to rehearse a brand-new-user experience without
reading your real histories, account connector data, or normal account profiles:

```bash
python3 cli.py dashboard --test-mode --port 8081
```

It uses only `~/.hotfix-ops-usage/testing/accounts.json`. The orange **TEST
MODE** banner includes **Load sample accounts** for a fake multi-account
overview and **Reset preview**, which clears that testing registry and returns
the dashboard to its blank first-run screen. The equivalent terminal commands
are deliberately explicit:

```bash
python3 cli.py accounts --testing samples
python3 cli.py accounts --testing reset --yes
```

Normal profiles at `~/.hotfix-ops-usage/accounts.json` are never read or
changed in test mode. To evaluate the multi-account layout with disposable
labels, add profiles only to the isolated registry:

```bash
python3 cli.py accounts --testing add --id studio --label "Studio Max" \
  --claude-email studio@example.com --codex-email studio@example.com
```

## Bundled account-selection skills

| Skill | Command | Behavior |
|---|---|---|
| [`skills/fable-next/SKILL.md`](skills/fable-next/SKILL.md) | `python3 cli.py fable-next` | Ranks Claude Max profiles by conservative Fable 5 headroom. |
| [`skills/codex-next/SKILL.md`](skills/codex-next/SKILL.md) | `python3 cli.py codex-next` | Ranks Codex profiles by live 5-hour/weekly room and flags reset credits without consuming them. |

Copy the skill directory into the appropriate local agent skill location, or
give the file to an agent that knows that environment. The skills require the
local-only account registry above; no account data is included in this project.

The older Claude-only terminal commands remain available:

```bash
python3 cli.py scan
python3 cli.py today
python3 cli.py week
python3 cli.py stats
```

For privacy, the server refuses non-loopback bind addresses. It can be opened
only through `localhost`, `127.0.0.1`, or `::1`. The Claude database path is
retained from the upstream project, so another `phuryn/claude-usage` checkout
may share `~/.claude/usage.db`.

## Estimates

Claude cost values are **Anthropic API-equivalent estimates**, not the amount a
Pro or Max subscriber was charged. Codex rows currently show usage tokens rather
than invented subscription or API costs; cost cells remain `n/a`. Codex
reasoning tokens are displayed separately for visibility but are already
included in output-token totals and are not added again in Overview. Provider
rate-limit percentages remain separate and are never added into a fake combined
percentage.

## Project lineage

This fork is based on Paweł Huryn's MIT-licensed
[`phuryn/claude-usage`](https://github.com/phuryn/claude-usage), created by
[The Product Compass Newsletter](https://www.productcompass.pm). The original
Claude scanner, dashboard charts, and attribution are retained under the
[MIT License](LICENSE).

## Files

| File | Purpose |
|---|---|
| `scanner.py` | Claude Code local-history scanner |
| `codex_scanner.py` | Codex local-history scanner |
| `connectors/claude_subscription.py` | Optional Claude status connector |
| `connectors/codex_subscription.py` | Optional Codex subscription connector |
| `dashboard.py` | Local HTTP server and three-view dashboard |
| `cli.py` | Terminal commands and dashboard launcher |
| `INSTALL_WITH_AGENT.md` | Copy-and-paste installation prompt |
| `assets/hfo-icon.png` | Local [HotFix Ops](https://hotfixops.com/) dashboard icon |
| `account_profiles.py` | Local-only test-profile registry and safe account ranking |
| `skills/fable-next/SKILL.md` | Portable Fable profile-selection skill |
| `skills/codex-next/SKILL.md` | Portable Codex profile-selection skill |
| `vendor/chart.umd.min.js` | Vendored Chart.js 4.4.0 (MIT) for local/offline charts |
