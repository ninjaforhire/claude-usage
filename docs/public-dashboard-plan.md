# Public Claude + Codex Usage Dashboard Plan

## Goal

Create a clean, local-first public fork of `phuryn/claude-usage` that shows:

1. Claude statistics
2. Codex statistics
3. A combined Overview equivalent to MIGHTY's All Usage tab

The project must work without Jimbo, MIGHTY paths, launchd, Bitwarden, or any
other private infrastructure.

## User Experience

1. Clone or download the source.
2. Give the included setup prompt to a local coding agent.
3. The agent verifies Python, Claude Code, and Codex CLI, then runs the
   non-destructive setup and tests.
4. Run `python3 cli.py dashboard`.
5. The dashboard opens locally and automatically:
   - rescans Claude and Codex local history;
   - detects the currently logged-in Claude and Codex accounts;
   - refreshes supported subscription limits;
   - renders Overview, Claude, and Codex tabs.
6. A visible Refresh button repeats the same operation.

No provider password, browser cookie, raw OAuth token, API key, or credential
file is copied into this repository or returned to the browser.

## Decision Lock

- Base: clean `upstream/main` (`phuryn/claude-usage`), not the MIGHTY-expanded
  fork.
- Runtime: Python standard library and local browser, preserving the original
  no-install architecture.
- Storage: SQLite usage databases. Connector responses are fetched live and are
  not persisted.
- Tabs: `Overview`, `Claude`, and `Codex`.
- Connectors are optional and live in separate files:
  - `connectors/claude_subscription.py`
  - `connectors/codex_subscription.py`
- Claude connector:
  - uses `claude auth status --json` for supported account identity and plan;
  - never reads Claude credential stores;
  - accepts an optional user-local helper that returns the normalized usage
    schema when a user chooses to configure subscription windows;
  - clearly labels unavailable or unsupported live usage instead of estimating.
- Codex connector:
  - starts the authenticated local `codex app-server --stdio`;
  - calls `initialize`, `account/read`, and `account/rateLimits/read`;
  - returns only sanitized account identity, plan, and rate-limit windows;
  - never reads or copies Codex credential files.
- Historical usage:
  - Claude comes from local Claude Code JSONL transcripts;
  - Codex comes from local Codex rollout JSONL transcripts;
  - both normalize into the existing sessions/turns data shape.
- Combined totals must preserve provider labels. Claude and Codex rate-limit
  percentages are never added together into a fake universal percentage.
- Pricing shown for subscription users is explicitly API-equivalent estimated
  value unless the user manually supplies subscription cost metadata.
- Distribution in this phase is source-only. No binary executable, installer
  package, hosted service, account proxy, or cloud database.
- No commits, pushes, releases, or GitHub repository creation without Andrew's
  explicit approval after reviewing the diff.
- Optional local-only test profiles live under the user's home directory, not
  the checkout. They retain sanitized account/rate-limit snapshots only and are
  never part of source distribution.

## Shared Connector Schema

Each connector returns a dictionary with:

```json
{
  "provider": "anthropic|openai",
  "available": true,
  "source": "claude-auth-status|codex-app-server|user-helper",
  "account": {
    "email": "optional",
    "label": "optional",
    "plan": "optional"
  },
  "windows": {
    "five_hour": {
      "used_percent": 0,
      "remaining_percent": 100,
      "resets_at": "optional ISO-8601"
    },
    "seven_day": {
      "used_percent": 0,
      "remaining_percent": 100,
      "resets_at": "optional ISO-8601"
    }
  },
  "reset_credits": {
    "available_count": 0,
    "expires_at": "optional ISO-8601"
  },
  "fetched_at": "ISO-8601",
  "error": null
}
```

Unavailable fields are omitted or `null`; they are never invented.

## Implementation Waves

### Wave 1: Provider-neutral data layer

- Add the shared connector schema and safe subprocess helpers.
- Add a Codex JSONL scanner mirroring the upstream Claude scanner.
- Add isolated tests with temporary homes and fixture JSONL.

### Wave 2: Separate optional connectors

- Implement `connectors/claude_subscription.py`.
- Implement `connectors/codex_subscription.py`.
- Add fixture-driven tests for successful, unavailable, malformed, and timeout
  responses.
- Verify no credential/token values cross the connector boundary.

### Wave 3: Dashboard and refresh

- Add a provider-neutral API payload.
- Add Overview, Claude, and Codex tabs.
- Automatically refresh on dashboard startup.
- Keep a manual Refresh button with progress/error state.
- Preserve existing date/model filters and CSV export where applicable.

### Wave 4: Agent-assisted setup

- Add `INSTALL_WITH_AGENT.md` containing one paste-ready prompt.
- The prompt instructs the user's agent to:
  - inspect before changing;
  - verify official CLI authentication;
  - install no secrets;
  - run scans and tests;
  - start the local dashboard;
  - report unsupported connector capabilities honestly.
- Add normal README instructions for users who do not use an agent.

## Done Criteria

- Fresh checkout works with Python 3.9+ and no third-party Python packages.
- Claude-only, Codex-only, both-provider, and neither-provider states render.
- Existing upstream Claude scanner tests remain green.
- Codex fixture totals exactly match expected input/output/cache/reasoning
  values without double counting.
- Codex subscription connector passes a live read against the installed local
  Codex app-server on Andrew's machine.
- Claude connector passes a live sanitized `claude auth status --json` read
  without reading credentials.
- Opening the dashboard triggers one bounded refresh; the Refresh button
  triggers another without deleting valid cached data on failure.
- API routes reject non-loopback hosts and cross-origin browser requests.
- Chart.js is vendored and served locally under a restrictive response policy;
  no third-party script receives same-origin access to dashboard data.
- Subscription reads use a single-flight in-memory TTL cache so concurrent page
  loads cannot spawn duplicate provider processes.
- Unsupported CLI command versions and implausible reset timestamps degrade to
  explicit unavailable states.
- Codex reasoning is shown separately but excluded from combined token totals
  because it is already included in Codex output tokens; Codex cost remains
  explicitly unavailable rather than zero.
- Browser-visible API payload contains no tokens, credential paths, raw rollout
  content, prompts, or message bodies.
- Codex reset-credit views retain only count and earliest expiry, never opaque
  IDs; neither the dashboard nor skills redeem credits automatically.
- Security scan finds no secrets or credential files.
- Full test suite passes.
- Browser QA confirms the three tabs, responsive layout, empty/error states,
  and refresh feedback.
- `git diff --check` passes.
- Independent review approves the uncommitted diff.

## Destructive Operations

None. Scans may rebuild only the dashboard-owned derived SQLite databases.
Provider logins, credentials, source transcripts, browser data, and upstream
repositories are read-only. The implementation remains uncommitted until Andrew
approves the diff.
