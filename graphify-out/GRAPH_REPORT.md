# Graph Report - claude-usage  (2026-06-29)

## Corpus Check
- 58 files · ~119,833 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 976 nodes · 1556 edges · 65 communities (58 shown, 7 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 7 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `bd2eb22c`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]

## God Nodes (most connected - your core abstractions)
1. `get_pricing()` - 24 edges
2. `classify_daemon()` - 23 edges
3. `scan()` - 23 edges
4. `get_dashboard_data()` - 19 edges
5. `parse_jsonl_file()` - 19 edges
6. `_daemon()` - 19 edges
7. `TestGetPricing` - 19 edges
8. `_make_assistant_record()` - 19 edges
9. `calc_cost()` - 18 edges
10. `fetch_period_data()` - 17 edges

## Surprising Connections (you probably didn't know these)
- `TestDashboardHTTP` --uses--> `DashboardHandler`  [INFERRED]
  tests/test_dashboard.py → dashboard.py
- `TestEmptyStringModelNormalization` --uses--> `DashboardHandler`  [INFERRED]
  tests/test_dashboard.py → dashboard.py
- `TestGetDashboardData` --uses--> `DashboardHandler`  [INFERRED]
  tests/test_dashboard.py → dashboard.py
- `TestHTMLTemplate` --uses--> `DashboardHandler`  [INFERRED]
  tests/test_dashboard.py → dashboard.py
- `TestMixedNullAndEmptyModel` --uses--> `DashboardHandler`  [INFERRED]
  tests/test_dashboard.py → dashboard.py

## Import Cycles
- None detected.

## Communities (65 total, 7 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (34): aggregate_sessions(), parse_jsonl_file(), Parse a JSONL file and return (session_metas, turns, line_count).      Deduplica, Aggregate turn data back into session-level stats., scan(), _make_assistant_record(), _make_user_record(), Tests for scanner.py - JSONL parsing, DB operations, and scanning. (+26 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (32): deactivate(), describeMode(), Extension, noInstallMessage(), noPythonMessage(), claudeUsageCandidateNames(), dashboardSpawnArgs(), InstallMode (+24 more)

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (56): account_score(), _as_date(), current_monthly_cost(), dashboard_payload(), days_until_renewal(), _extract_windows(), fetch_all_usage(), _fetch_all_usage_locked() (+48 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (43): codex_orb_data(), _find_rate_limits(), get_plan_caps(), _last_rate_limits_in(), latest_rate_limits(), Path, Read Codex (ChatGPT/OpenAI) 5h + weekly rate limits from the Codex CLI sessions., Map a primary/secondary limit part to the orb window shape. (+35 more)

### Community 4 - "Community 4"
Cohesion: 0.06
Nodes (31): Architecture, CHANGELOG conventions, Common commands, Cost calculation, Dashboard server, Data flow, Homebrew formula and self-referential SHA, Non-obvious invariants (+23 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (31): gather(), last_run_epoch(), launchctl_state(), list_plist_files(), parse_plist(), daemons.py - Inventory and health of launchd agents under ~/Library/LaunchAgents, mtime of the daemon's stdout log as a last-run proxy. None if absent., Return a list of merged daemon dicts: plist fields + live launchctl state. (+23 more)

### Community 6 - "Community 6"
Cohesion: 0.11
Nodes (29): apply_event(), classify(), _flatten_body(), get_message(), _gws_json(), _headers(), ingest(), list_message_ids() (+21 more)

### Community 7 - "Community 7"
Cohesion: 0.10
Nodes (22): _acct(), _expired_acct(), _full_acct(), Tests for accounts.py — store layer, token refresh, usage fetch, presentation., Refresh AND usage fetch both fail -> that account grays; others fine., Refresh endpoint failing (429/dead) must not gray an account whose     access to, Refresh succeeds (tokens rotated) but usage fetch fails — rotated     tokens MUS, Access token invalidated before expires_at (rotated elsewhere) — one forced refr (+14 more)

### Community 8 - "Community 8"
Cohesion: 0.18
Nodes (27): _bootout_cmd(), build_report(), classify_daemon(), classify.py - Merge daemons with the registry, bucket them, and emit remediation, Full report dict consumed by the dashboard API and the CLI.      {       "daemon, Return (bucket, reasons[], remediation|None) for one merged daemon dict., _daemon(), _heartbeat() (+19 more)

### Community 9 - "Community 9"
Cohesion: 0.13
Nodes (6): get_pricing(), Ensure CLI pricing matches known Anthropic API rates., Regression guard for issue #61 — Opus 4.7 must be present., Model strings from JSONL often have date suffixes., TestGetPricing, TestPricingConsistency

### Community 10 - "Community 10"
Cohesion: 0.11
Nodes (5): _make_db(), In-memory SQLite DB with minimal schema and fixture data., TestCardReport, TestSparkReport, TestTableReport

### Community 11 - "Community 11"
Cohesion: 0.12
Nodes (18): _acct(), _entry(), Tests for subscription cost, lifetime spend, and optimal-account scoring., A public_view-shaped entry for score/recommend tests., test_all_unhealthy_falls_back_to_main(), test_current_monthly_cost_full_when_active(), test_current_monthly_cost_zero_when_inactive(), test_is_active_false_when_cancelled() (+10 more)

### Community 12 - "Community 12"
Cohesion: 0.19
Nodes (15): _d(), _entry(), Behavior tests for freshness_watch transition-only alerting + daily digest., _report(), _Spy, test_build_digest_empty_when_nothing_to_say(), test_build_digest_lines(), test_changed_signature_realerts() (+7 more)

### Community 13 - "Community 13"
Cohesion: 0.13
Nodes (22): append_events(), build_digest(), check_and_alert(), _decoy_events(), digest_due(), drain_queue(), issue_signature(), _load_state() (+14 more)

### Community 14 - "Community 14"
Cohesion: 0.11
Nodes (12): alert(), digest(), _osa_escape(), _osascript_notify(), _post_jimbo(), notify.py - fire-and-forget daemon alerting.  Two sinks, both best-effort, neith, POST a payload to Jimbo. Returns True on 2xx, False on any failure., Escape a string for safe embedding in an AppleScript double-quoted literal. (+4 more)

### Community 15 - "Community 15"
Cohesion: 0.17
Nodes (9): get_db(), init_db(), insert_turns(), _model_priority(), scanner.py - Scans Claude Code JSONL transcript files and stores data in SQLite., Return a priority score for a model name (higher = more capable)., upsert_sessions(), Existing DBs without message_id column should be upgraded. (+1 more)

### Community 17 - "Community 17"
Cohesion: 0.17
Nodes (4): get_dashboard_data(), Regression: turns with model='' (empty string) must group as 'unknown'.     COAL, TestEmptyStringModelNormalization, TestGetDashboardData

### Community 18 - "Community 18"
Cohesion: 0.19
Nodes (17): build_prompt(), _finding_block(), _is_rogue(), promptgen.py - Turn a selection of findings into a copyable repair-request promp, haiku = trivial toggle/kill, sonnet = log/root-cause, opus = multi-file logic., Return a markdown repair-request prompt for the selected findings., recommend_model(), Behavior tests for promptgen model recommendation + prompt assembly. (+9 more)

### Community 19 - "Community 19"
Cohesion: 0.11
Nodes (12): Tests for cli.py - pricing, formatting, and cost calculation., The VS Code extension passes --no-browser; CLI users get a browser., Existing account -> update_oauth (preserve history), never upsert., A brand-new account in --quiet mode can't prompt -> hard exit., New account + --billing-day registers a full record non-interactively., accounts refresh' rotates via fetch_all_usage (no HTTP server)., test_quiet_existing_account_recaptures_not_upserts(), test_quiet_new_account_with_billing_day_upserts() (+4 more)

### Community 20 - "Community 20"
Cohesion: 0.24
Nodes (15): attribute(), cost_for_prefix(), is_mixed(), _norm(), attribution.py - Attribute usage.db cost to a daemon via its working-directory p, Sum estimated cost over turns whose session project_name ends with cwd_prefix., Annotate each daemon dict (that carries a cwd_prefix) with cost_7d / cost_30d., _add() (+7 more)

### Community 21 - "Community 21"
Cohesion: 0.18
Nodes (12): fmt(), Connection, TestFmt, _cache_savings(), card_report(), _model_short(), views.py - Display modes for the claude-usage report command.  Public API:     f, Estimate dollars saved by cache reads vs paying full input price. (+4 more)

### Community 22 - "Community 22"
Cohesion: 0.26
Nodes (14): cmd_daemons(), cmd_dashboard(), cmd_freshness_tick(), cmd_report(), cmd_scan(), cmd_stats(), cmd_today(), cmd_week() (+6 more)

### Community 23 - "Community 23"
Cohesion: 0.13
Nodes (7): Verify XSS protection is present (PR #10)., Verify getPricing falls back to substring match for unknown models., Verify getPricing returns null for non-Anthropic models., Hourly distribution chart has a canvas + TZ toggle., Peak-hour set covers UTC 12–17 (Mon–Fri 05:00–11:00 PT)., The 'Today' range button is wired into RANGE_LABELS, RANGE_TICKS,         getRan, TestHTMLTemplate

### Community 24 - "Community 24"
Cohesion: 0.13
Nodes (14): activationEvents, categories, description, displayName, engines, vscode, homepage, icon (+6 more)

### Community 25 - "Community 25"
Cohesion: 0.22
Nodes (5): TestFetchPeriodData, _date_range(), fetch_period_data(), Return (start_iso, end_iso) for a period string, or (None, None) for 'all'., Run all DB queries for a period and return a unified result dict.

### Community 26 - "Community 26"
Cohesion: 0.14
Nodes (13): compilerOptions, esModuleInterop, lib, module, outDir, resolveJsonModule, rootDir, skipLibCheck (+5 more)

### Community 27 - "Community 27"
Cohesion: 0.24
Nodes (6): Tests for views.py — fetch_period_data and display functions., TestSparkLine, Map a list of floats to an 8-level block-character spark string., Print a sparkline trend report. Falls back to table for today/all., _spark_line(), spark_report()

### Community 28 - "Community 28"
Cohesion: 0.15
Nodes (13): default, description, type, properties, title, contributes, commands, configuration (+5 more)

### Community 29 - "Community 29"
Cohesion: 0.24
Nodes (7): BaseHTTPRequestHandler, DashboardHandler, find_icon_file(), get_accounts_data(), dashboard.py - Local web dashboard served on localhost:8080., Account limit data for the orb row; credential-free public view., Locate the extension's icon.svg across both run contexts.      - Bundled in the

### Community 30 - "Community 30"
Cohesion: 0.17
Nodes (12): Account limit orbs (multi-account), Account orbs stay alive without manual re-login, Changelog, Dashboard, Extension, Project / docs, Unreleased, v1.0.0 — 2026-04-09 (+4 more)

### Community 31 - "Community 31"
Cohesion: 0.17
Nodes (11): Tests for the cross-process store lock (accounts.store_lock).  Regression guard, Concurrent locked increments never lose an update (200 == 4 * 50)., The lock is released on exit so the next acquirer is not blocked., Where fcntl is unavailable (Windows), the lock degrades to a no-op., store_lock is non-reentrant: the function fetch_all_usage runs *inside*     the, upsert_account still round-trips with the lock wrapping it., test_locked_helper_takes_no_reentrant_lock(), test_store_lock_noop_without_fcntl() (+3 more)

### Community 32 - "Community 32"
Cohesion: 0.17
Nodes (12): Claude Code Usage — VS Code extension, Commands, From a `.vsix` file (local install), From the VS Code Marketplace, How discovery works, Install, Privacy, Requirements (+4 more)

### Community 34 - "Community 34"
Cohesion: 0.33
Nodes (3): project_name_from_cwd(), Derive a friendly project name from cwd path., TestProjectNameFromCwd

### Community 36 - "Community 36"
Cohesion: 0.31
Nodes (8): find_rogues(), _is_interactive_claude(), _is_whitelisted(), processes.py - Live process snapshot + rogue detection.  Rogue = a long-lived pr, Return list of {pid, ppid, cpu, mem, etime, command} for all processes., A `claude` CLI launched from an interactive shell (the user's terminals)., Return claude processes that look like runaway/orphaned background work.      Sc, snapshot()

### Community 37 - "Community 37"
Cohesion: 0.22
Nodes (9): scripts, compile, copy-python, package, publish, test, test:watch, vscode:prepublish (+1 more)

### Community 38 - "Community 38"
Cohesion: 0.25
Nodes (8): cmd_accounts(), parse_keychain_credentials(), parse_named_arg(), Parse macOS Keychain JSON into a normalised OAuth dict.      Args:         raw:, Read credentials from macOS Keychain; returns raw JSON string., Manage tracked Claude accounts for limit orbs., Extract a --flag VALUE pair from an argument list., _read_keychain()

### Community 39 - "Community 39"
Cohesion: 0.25
Nodes (8): Claude Code Usage Dashboard, Cost estimates, Files, How it works, Requirements, Usage, VS Code extension, What this tracks

### Community 40 - "Community 40"
Cohesion: 0.29
Nodes (3): Tests for dashboard.py - API endpoint and data retrieval., Regression: when the user has only non-billable models (e.g. gemma, glm,     loc, TestNonBillableModelFallback

### Community 41 - "Community 41"
Cohesion: 0.33
Nodes (5): files, fs, path, repoRoot, targetDir

### Community 43 - "Community 43"
Cohesion: 0.47
Nodes (3): Verify CLI and dashboard pricing tables stay in sync., Extract pricing values from the dashboard JS PRICING object., TestPricingParity

### Community 44 - "Community 44"
Cohesion: 0.40
Nodes (5): devDependencies, @types/node, @types/vscode, typescript, vitest

### Community 45 - "Community 45"
Cohesion: 0.50
Nodes (4): Dashboard, Project / docs, Scanner, v1.1.0 — 2026-05-28

### Community 46 - "Community 46"
Cohesion: 0.50
Nodes (4): macOS / Linux (clone), macOS / Linux (Homebrew), Quick Start, Windows

### Community 47 - "Community 47"
Cohesion: 0.50
Nodes (4): default, description, type, claudeUsage.cliPath

### Community 48 - "Community 48"
Cohesion: 0.50
Nodes (4): default, description, type, claudeUsage.pythonPath

### Community 50 - "Community 50"
Cohesion: 0.67
Nodes (3): CI, Distribution, v1.2.0 — 2026-05-29

### Community 51 - "Community 51"
Cohesion: 0.67
Nodes (3): Dashboard, Extension, v1.2.4 — 2026-05-30

### Community 52 - "Community 52"
Cohesion: 0.67
Nodes (3): Extension, Scanner / CLI, v1.2.3 — 2026-05-30

### Community 53 - "Community 53"
Cohesion: 0.67
Nodes (3): Packaging, Project / docs, v1.1.1 — 2026-05-28

### Community 56 - "Community 56"
Cohesion: 0.67
Nodes (3): author, name, url

### Community 57 - "Community 57"
Cohesion: 0.67
Nodes (3): repository, type, url

## Knowledge Gaps
- **128 isolated node(s):** `Path`, `name`, `displayName`, `description`, `version` (+123 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `scan()` connect `Community 0` to `Community 34`, `Community 22`, `Community 15`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Why does `calc_cost()` connect `Community 33` to `Community 9`, `Community 19`, `Community 20`, `Community 21`, `Community 22`, `Community 25`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Why does `serve()` connect `Community 22` to `Community 29`?**
  _High betweenness centrality (0.026) - this node is a cross-community bridge._
- **What connects `Multi-account OAuth credential store + usage fetch for the dashboard.`, `Exclusive cross-process lock around a store read-modify-write cycle.      OAuth`, `Load the account store from disk, returning empty store if missing.` to the rest of the system?**
  _314 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.055135135135135134 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.05541346973572037 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.06516290726817042 - nodes in this community are weakly interconnected._