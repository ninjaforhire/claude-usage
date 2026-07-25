"""
cli.py - Command-line interface for the Claude Code usage dashboard.

Commands:
  scan      - Scan JSONL files and update the database
  today     - Print today's usage summary
  stats     - Print all-time usage statistics
  dashboard - Scan + open browser + start dashboard server
"""

import os
import re
import sys
import sqlite3
import argparse
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

DB_PATH = Path.home() / ".claude" / "usage.db"

PRICING = {
    "claude-opus-4-7":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-6":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-5":   {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-sonnet-4-7": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-7":  {"input": 1.00, "output":  5.00, "cache_read": 0.10, "cache_write": 1.25},
    "claude-haiku-4-6":  {"input": 1.00, "output":  5.00, "cache_read": 0.10, "cache_write": 1.25},
    "claude-haiku-4-5":  {"input": 1.00, "output":  5.00, "cache_read": 0.10, "cache_write": 1.25},
}

def get_pricing(model):
    if not model:
        return None
    if model in PRICING:
        return PRICING[model]
    for key in PRICING:
        if model.startswith(key):
            return PRICING[key]
    # Substring fallback: match model family by keyword
    m = model.lower()
    if "opus" in m:
        return PRICING["claude-opus-4-7"]
    if "sonnet" in m:
        return PRICING["claude-sonnet-4-6"]
    if "haiku" in m:
        return PRICING["claude-haiku-4-5"]
    return None

def calc_cost(model, inp, out, cache_read, cache_creation):
    p = get_pricing(model)
    if not p:
        return 0.0
    return (
        inp            * p["input"]       / 1_000_000 +
        out            * p["output"]      / 1_000_000 +
        cache_read     * p["cache_read"]  / 1_000_000 +
        cache_creation * p["cache_write"] / 1_000_000
    )

def fmt(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def fmt_cost(c):
    return f"${c:.4f}"

def hr(char="-", width=60):
    print(char * width)

def require_db():
    if not DB_PATH.exists():
        print("Database not found. Run: python cli.py scan")
        sys.exit(1)
    return sqlite3.connect(DB_PATH)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_scan(projects_dir=None):
    from scanner import scan
    scan(projects_dir=Path(projects_dir) if projects_dir else None)


def cmd_today():
    conn = require_db()
    conn.row_factory = sqlite3.Row
    today = date.today().isoformat()

    rows = conn.execute("""
        SELECT
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as inp,
            SUM(output_tokens)         as out,
            SUM(cache_read_tokens)     as cr,
            SUM(cache_creation_tokens) as cc,
            COUNT(*)                   as turns
        FROM turns
        WHERE substr(timestamp, 1, 10) = ?
        GROUP BY model
        ORDER BY inp + out DESC
    """, (today,)).fetchall()

    sessions = conn.execute("""
        SELECT COUNT(DISTINCT session_id) as cnt
        FROM turns
        WHERE substr(timestamp, 1, 10) = ?
    """, (today,)).fetchone()

    print()
    hr()
    print(f"  Today's Usage  ({today})")
    hr()

    if not rows:
        print("  No usage recorded today.")
        print()
        return

    total_inp = total_out = total_cr = total_cc = total_turns = 0
    total_cost = 0.0

    for r in rows:
        cost = calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)
        total_cost += cost
        total_inp += r["inp"] or 0
        total_out += r["out"] or 0
        total_cr  += r["cr"]  or 0
        total_cc  += r["cc"]  or 0
        total_turns += r["turns"]
        print(f"  {r['model']:<30}  turns={r['turns']:<4}  in={fmt(r['inp'] or 0):<8}  out={fmt(r['out'] or 0):<8}  cost={fmt_cost(cost)}")

    hr()
    print(f"  {'TOTAL':<30}  turns={total_turns:<4}  in={fmt(total_inp):<8}  out={fmt(total_out):<8}  cost={fmt_cost(total_cost)}")
    print()
    print(f"  Sessions today:   {sessions['cnt']}")
    print(f"  Cache read:       {fmt(total_cr)}")
    print(f"  Cache creation:   {fmt(total_cc)}")
    hr()
    print()
    conn.close()


def cmd_week():
    conn = require_db()
    conn.row_factory = sqlite3.Row

    today_d = date.today()
    start_d = today_d - timedelta(days=6)
    start = start_d.isoformat()
    end = today_d.isoformat()

    by_day_model = conn.execute("""
        SELECT
            substr(timestamp, 1, 10)   as day,
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as inp,
            SUM(output_tokens)         as out,
            SUM(cache_read_tokens)     as cr,
            SUM(cache_creation_tokens) as cc,
            COUNT(*)                   as turns
        FROM turns
        WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?
        GROUP BY day, model
    """, (start, end)).fetchall()

    by_model = conn.execute("""
        SELECT
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as inp,
            SUM(output_tokens)         as out,
            SUM(cache_read_tokens)     as cr,
            SUM(cache_creation_tokens) as cc,
            COUNT(*)                   as turns
        FROM turns
        WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?
        GROUP BY model
        ORDER BY inp + out DESC
    """, (start, end)).fetchall()

    sessions = conn.execute("""
        SELECT COUNT(DISTINCT session_id) as cnt
        FROM turns
        WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?
    """, (start, end)).fetchone()

    print()
    hr()
    print(f"  Weekly Usage  ({start} to {end})")
    hr()

    if not by_model:
        print("  No usage recorded in the last 7 days.")
        print()
        conn.close()
        return

    # Aggregate per-day across models (with per-turn cost attribution)
    per_day = {}
    for r in by_day_model:
        d = r["day"]
        bucket = per_day.setdefault(d, {"turns": 0, "inp": 0, "out": 0, "cost": 0.0})
        bucket["turns"] += r["turns"]
        bucket["inp"]   += r["inp"] or 0
        bucket["out"]   += r["out"] or 0
        bucket["cost"]  += calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)

    print("  By Day:")
    for i in range(7):
        d = (start_d + timedelta(days=i)).isoformat()
        b = per_day.get(d, {"turns": 0, "inp": 0, "out": 0, "cost": 0.0})
        print(f"    {d}  turns={b['turns']:<4}  in={fmt(b['inp']):<8}  out={fmt(b['out']):<8}  cost={fmt_cost(b['cost'])}")

    hr()
    print("  By Model:")

    total_inp = total_out = total_cr = total_cc = total_turns = 0
    total_cost = 0.0
    for r in by_model:
        cost = calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)
        total_cost  += cost
        total_inp   += r["inp"] or 0
        total_out   += r["out"] or 0
        total_cr    += r["cr"]  or 0
        total_cc    += r["cc"]  or 0
        total_turns += r["turns"]
        print(f"    {r['model']:<30}  turns={r['turns']:<4}  in={fmt(r['inp'] or 0):<8}  out={fmt(r['out'] or 0):<8}  cost={fmt_cost(cost)}")

    hr()
    print(f"    {'TOTAL':<30}  turns={total_turns:<4}  in={fmt(total_inp):<8}  out={fmt(total_out):<8}  cost={fmt_cost(total_cost)}")
    print()
    print(f"  Sessions this week:  {sessions['cnt']}")
    print(f"  Cache read:          {fmt(total_cr)}")
    print(f"  Cache creation:      {fmt(total_cc)}")
    hr()
    print()
    conn.close()


def cmd_stats():
    conn = require_db()
    conn.row_factory = sqlite3.Row

    # Session-level info (count, date range)
    session_info = conn.execute("""
        SELECT
            COUNT(*)                  as sessions,
            MIN(first_timestamp)      as first,
            MAX(last_timestamp)       as last
        FROM sessions
    """).fetchone()

    # All-time totals from turns (more accurate — per-turn model attribution)
    totals = conn.execute("""
        SELECT
            SUM(input_tokens)             as inp,
            SUM(output_tokens)            as out,
            SUM(cache_read_tokens)        as cr,
            SUM(cache_creation_tokens)    as cc,
            COUNT(*)                      as turns
        FROM turns
    """).fetchone()

    # By model from turns (each turn has the actual model used)
    by_model = conn.execute("""
        SELECT
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as inp,
            SUM(output_tokens)         as out,
            SUM(cache_read_tokens)     as cr,
            SUM(cache_creation_tokens) as cc,
            COUNT(*)                   as turns,
            COUNT(DISTINCT session_id) as sessions
        FROM turns
        GROUP BY model
        ORDER BY inp + out DESC
    """).fetchall()

    # Top 5 projects from turns (join with sessions for project name)
    top_projects = conn.execute("""
        SELECT
            COALESCE(s.project_name, 'unknown') as project_name,
            SUM(t.input_tokens)  as inp,
            SUM(t.output_tokens) as out,
            COUNT(*)             as turns,
            COUNT(DISTINCT t.session_id) as sessions
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        GROUP BY s.project_name
        ORDER BY inp + out DESC
        LIMIT 5
    """).fetchall()

    # Daily average (last 30 days)
    daily_avg = conn.execute("""
        SELECT
            AVG(daily_inp) as avg_inp,
            AVG(daily_out) as avg_out
        FROM (
            SELECT
                substr(timestamp, 1, 10) as day,
                SUM(input_tokens) as daily_inp,
                SUM(output_tokens) as daily_out
            FROM turns
            WHERE timestamp >= datetime('now', '-30 days')
            GROUP BY day
        )
    """).fetchone()

    # Build total cost across all models
    total_cost = sum(
        calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)
        for r in by_model
    )

    print()
    hr("=")
    print("  Claude Code Usage - All-Time Statistics")
    hr("=")

    first_date = (session_info["first"] or "")[:10]
    last_date = (session_info["last"] or "")[:10]
    print(f"  Period:           {first_date} to {last_date}")
    print(f"  Total sessions:   {session_info['sessions'] or 0:,}")
    print(f"  Total turns:      {fmt(totals['turns'] or 0)}")
    print()
    print(f"  Input tokens:     {fmt(totals['inp'] or 0):<12}  (raw prompt tokens)")
    print(f"  Output tokens:    {fmt(totals['out'] or 0):<12}  (generated tokens)")
    print(f"  Cache read:       {fmt(totals['cr'] or 0):<12}  (90% cheaper than input)")
    print(f"  Cache creation:   {fmt(totals['cc'] or 0):<12}  (25% premium on input)")
    print()
    print(f"  Est. total cost:  ${total_cost:.4f}")
    hr()

    print("  By Model:")
    for r in by_model:
        cost = calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)
        print(f"    {r['model']:<30}  sessions={r['sessions']:<4}  turns={fmt(r['turns'] or 0):<6}  "
              f"in={fmt(r['inp'] or 0):<8}  out={fmt(r['out'] or 0):<8}  cost={fmt_cost(cost)}")

    hr()
    print("  Top Projects:")
    for r in top_projects:
        print(f"    {(r['project_name'] or 'unknown'):<40}  sessions={r['sessions']:<3}  "
              f"turns={fmt(r['turns'] or 0):<6}  tokens={fmt((r['inp'] or 0)+(r['out'] or 0))}")

    if daily_avg["avg_inp"]:
        hr()
        print("  Daily Average (last 30 days):")
        print(f"    Input:   {fmt(int(daily_avg['avg_inp'] or 0))}")
        print(f"    Output:  {fmt(int(daily_avg['avg_out'] or 0))}")

    hr("=")
    print()
    conn.close()


def cmd_dashboard(
    projects_dir=None, host=None, port=None, no_browser=False, test_mode=False
):
    if test_mode:
        print("Starting isolated first-run preview (real histories and account data are not read)...")
    else:
        print("Scanning Claude history...")
        cmd_scan(projects_dir=projects_dir)
        print("\nScanning Codex history...")
        import codex_scanner

        codex_scanner.scan(verbose=False)

    print("\nStarting dashboard server...")
    from dashboard import serve

    host = host or os.environ.get("HOST", "localhost")
    port = int(port or os.environ.get("PORT", "8080"))

    # Open a browser for users running this as a script (see README). The VS Code
    # extension passes --no-browser since it embeds the dashboard in a webview.
    if not no_browser:
        import webbrowser
        import threading
        import time

        def open_browser():
            time.sleep(1.0)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=open_browser, daemon=True).start()

    serve(host=host, port=port, test_mode=test_mode)


def _account_store_from_args(value: Optional[str], testing: bool = False) -> Path:
    """Return an explicit, standard, or isolated testing profile store path."""
    if value:
        return Path(value).expanduser()
    from account_profiles import DEFAULT_STORE_PATH, TESTING_STORE_PATH

    return TESTING_STORE_PATH if testing else DEFAULT_STORE_PATH


def _account_parser() -> argparse.ArgumentParser:
    """Build the local-only account command parser."""
    parser = argparse.ArgumentParser(
        prog="python cli.py accounts",
        description="Manage local-only account profiles outside this repository.",
    )
    parser.add_argument("--store", help="Local profile registry path")
    parser.add_argument(
        "--testing",
        action="store_true",
        help="Use the isolated first-run testing registry only",
    )
    commands = parser.add_subparsers(dest="action", required=True)
    add = commands.add_parser("add", help="Add a local account profile without credentials")
    add.add_argument("--id", required=True, help="Stable local profile identifier")
    add.add_argument("--label", required=True, help="Dashboard label")
    add.add_argument("--claude-email", help="Expected active Claude account email")
    add.add_argument("--codex-email", help="Expected active Codex account email")
    add.add_argument("--tier", default="Max 20x", help="Non-secret plan label")
    add.add_argument("--inactive", action="store_true", help="Mark this profile inactive")
    snapshot = commands.add_parser("snapshot", help="Save the currently active account")
    snapshot.add_argument("--profile", required=True, help="Existing local profile identifier")
    snapshot.add_argument(
        "--provider", choices=("claude", "codex", "all"), default="all"
    )
    snapshot.add_argument(
        "--claude-helper",
        help="Explicit trusted helper for Claude usage windows; never downloaded",
    )
    commands.add_parser("list", help="List local labels and snapshot state without emails")
    setup = commands.add_parser(
        "setup", help="Safely add the currently signed-in Claude and/or Codex account"
    )
    setup.add_argument("--label", help="Bold dashboard name for this account")
    setup.add_argument(
        "--providers",
        choices=("claude", "codex", "all"),
        default="all",
        help="Providers to inspect from official local CLIs (default: all)",
    )
    setup.add_argument(
        "--tier",
        help="Optional non-secret plan label when the official CLI does not report one",
    )
    reset = commands.add_parser(
        "reset", help="Clear only the isolated first-run testing profiles"
    )
    reset.add_argument(
        "--yes",
        action="store_true",
        help="Confirm clearing the isolated testing registry",
    )
    commands.add_parser(
        "samples", help="Replace isolated testing state with fake sample accounts"
    )
    return parser


def _profile_by_id(registry: dict, profile_id: str) -> dict:
    """Find one local profile or raise a safe CLI error."""
    for profile in registry.get("profiles", []):
        if profile.get("id") == profile_id:
            return profile
    raise ValueError(f"Unknown local account profile: {profile_id}")


def _profile_id_for_label(label: str, registry: dict) -> str:
    """Create a stable, non-secret local profile id from a display label."""
    base = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-") or "account"
    base = base[:56].rstrip("-") or "account"
    existing = {str(profile.get("id", "")) for profile in registry.get("profiles", [])}
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base[:60 - len(str(suffix))]}-{suffix}"
        suffix += 1
    return candidate


def _setup_label(value: Optional[str], parser: argparse.ArgumentParser) -> str:
    """Return a safe display label, asking only when running interactively."""
    label = value.strip() if isinstance(value, str) else ""
    if not label and sys.stdin.isatty():
        try:
            label = input("Account name to show in bold (for example, Work Max): ").strip()
        except EOFError:
            label = ""
    if not label:
        parser.error("setup requires --label when it cannot prompt interactively")
    if len(label) > 100:
        parser.error("setup account name must be 100 characters or fewer")
    return label


def cmd_accounts(arguments: list[str]) -> None:
    """Manage untracked profiles and snapshot only the interactive login."""
    from account_profiles import (
        add_profile,
        load_registry,
        record_snapshot,
        reset_testing_registry,
        save_registry,
        seed_testing_registry,
    )
    from connectors.claude_subscription import read_subscription as read_claude
    from connectors.codex_subscription import read_subscription as read_codex

    parser = _account_parser()
    args = parser.parse_args(arguments)
    mode = "testing" if args.testing else "standard"
    store = _account_store_from_args(args.store, testing=args.testing)
    if args.action == "reset":
        if not args.testing:
            parser.error("reset requires --testing so normal account profiles cannot be cleared")
        if not args.yes:
            parser.error("reset requires --yes to confirm clearing isolated testing profiles")
        reset_testing_registry(store)
        print("Reset isolated first-run testing profiles. Normal account profiles were not touched.")
        return
    if args.action == "samples":
        if not args.testing:
            parser.error("samples requires --testing so normal account profiles cannot be replaced")
        seed_testing_registry(store)
        print("Loaded fake sample accounts into isolated first-run testing profiles.")
        return
    registry = load_registry(store, mode=mode)
    if args.action == "setup":
        label = _setup_label(args.label, parser)
        targets = ("claude", "codex") if args.providers == "all" else (args.providers,)
        snapshots: dict[str, dict] = {}
        for provider in targets:
            snapshot = read_claude() if provider == "claude" else read_codex()
            if snapshot.get("available"):
                snapshots[provider] = snapshot
                continue
            if provider == "claude":
                print("Claude is not connected. Run `claude` to sign in yourself, then rerun setup.")
            else:
                print("Codex is not connected. Run `codex login` to sign in yourself, then rerun setup.")
        if not snapshots:
            print("No account profile was saved. Sign in through the official CLI, then rerun setup.")
            return
        providers: dict[str, dict[str, str]] = {}
        for provider, snapshot in snapshots.items():
            account = snapshot.get("account", {})
            details: dict[str, str] = {}
            if isinstance(account, dict):
                email = account.get("email")
                plan = account.get("plan")
                if isinstance(email, str) and email.strip():
                    details["expected_email"] = email.strip()
                if isinstance(plan, str) and plan.strip():
                    details["plan_label"] = plan.strip()
            if "plan_label" not in details and args.tier:
                details["plan_label"] = args.tier.strip()
            providers[provider] = details
        profile_id = _profile_id_for_label(label, registry)
        add_profile(registry, profile_id, label, providers)
        for provider, snapshot in snapshots.items():
            record_snapshot(registry, profile_id, provider, snapshot)
        save_registry(registry, store, mode=mode)
        print(f"Saved local-only account profile: {label} ({', '.join(sorted(snapshots))}).")
        return
    if args.action == "add":
        providers: dict[str, dict[str, str]] = {}
        if args.claude_email:
            providers["claude"] = {
                "expected_email": args.claude_email,
                "plan_label": args.tier,
            }
        if args.codex_email:
            providers["codex"] = {
                "expected_email": args.codex_email,
                "plan_label": args.tier,
            }
        if not providers:
            parser.error("add requires --claude-email and/or --codex-email")
        add_profile(registry, args.id, args.label, providers, inactive=args.inactive)
        save_registry(registry, store, mode=mode)
        print(f"Saved local-only account profile: {args.label}")
        return
    if args.action == "list":
        if not registry["profiles"]:
            print("No local account profiles configured.")
            return
        for profile in registry["profiles"]:
            providers = ", ".join(sorted(profile["providers"]))
            snapshots = ", ".join(sorted(profile.get("snapshots", {}))) or "none"
            inactive = " · inactive" if profile.get("inactive") else ""
            print(f"{profile['id']}: {profile['label']} · {providers} · snapshots: {snapshots}{inactive}")
        return

    profile = _profile_by_id(registry, args.profile)
    targets = (
        sorted(profile["providers"])
        if args.provider == "all"
        else [args.provider]
    )
    for provider in targets:
        if provider not in profile["providers"]:
            print(f"Skipping {provider}: not configured for {profile['label']}")
            continue
        snapshot = (
            read_claude(helper=Path(args.claude_helper).expanduser())
            if provider == "claude" and args.claude_helper
            else read_claude()
            if provider == "claude"
            else read_codex()
        )
        if not snapshot.get("available"):
            print(f"{provider.title()} snapshot unavailable; previous local snapshot is unchanged.")
            continue
        record_snapshot(registry, profile["id"], provider, snapshot)
        print(f"Saved sanitized {provider.title()} snapshot for {profile['label']}.")
    save_registry(registry, store, mode=mode)


def _percent(value: object) -> str:
    """Format a usage percentage for terminal ranking output."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "--"
    return f"{value:.0f}%"


def cmd_fable_next(arguments: list[str]) -> None:
    """Rank local Max profiles by conservative Fable 5 weekly headroom."""
    from account_profiles import load_registry, rank_fable_profiles

    parser = argparse.ArgumentParser(prog="python cli.py fable-next")
    parser.add_argument("--store", help="Local profile registry path")
    parser.add_argument("--testing", action="store_true", help="Use isolated testing profiles")
    args = parser.parse_args(arguments)
    mode = "testing" if args.testing else "standard"
    rows = rank_fable_profiles(
        load_registry(_account_store_from_args(args.store, args.testing), mode=mode)
    )
    if not rows:
        print("No local Claude account profiles configured.")
        return
    print("FABLE NEXT — guaranteed headroom from the shared 50% weekly cap")
    for index, row in enumerate(rows):
        fable = row["fable"]
        if fable is None:
            summary = "weekly snapshot unavailable"
        else:
            summary = (
                f"Fable guaranteed {_percent(fable['guaranteed_percent'])}"
                f" · weekly {_percent(fable['weekly_remaining_percent'])}"
            )
        marker = "→" if index == 0 and fable and fable["guaranteed_percent"] > 0 else " "
        inactive = " · inactive" if row["inactive"] else ""
        print(f"{marker} {row['label']}: {summary}{inactive}")


def cmd_codex_next(arguments: list[str]) -> None:
    """Rank local Codex profiles and surface reset credits without consuming them."""
    from account_profiles import load_registry, rank_codex_profiles

    parser = argparse.ArgumentParser(prog="python cli.py codex-next")
    parser.add_argument("--store", help="Local profile registry path")
    parser.add_argument("--testing", action="store_true", help="Use isolated testing profiles")
    args = parser.parse_args(arguments)
    mode = "testing" if args.testing else "standard"
    rows = rank_codex_profiles(
        load_registry(_account_store_from_args(args.store, args.testing), mode=mode)
    )
    if not rows:
        print("No local Codex account profiles configured.")
        return
    print("CODEX NEXT — direct headroom first; reset credits are never consumed automatically")
    for index, row in enumerate(rows):
        resets = row["reset_credits"]
        reset_note = f" · reset credits {resets['available_count']}" if resets["available_count"] else ""
        marker = "→" if index == 0 and row["direct_ready"] and not row["inactive"] else " "
        inactive = " · inactive" if row["inactive"] else ""
        print(
            f"{marker} {row['label']}: 5h {_percent(row['five_hour_remaining_percent'])}"
            f" · week {_percent(row['weekly_remaining_percent'])}{reset_note}{inactive}"
        )


# ── Entry point ───────────────────────────────────────────────────────────────

USAGE = """
Claude Code Usage Dashboard

Usage:
  python cli.py scan [--projects-dir PATH]   Scan JSONL files and update database
  python cli.py today                        Show today's usage summary
  python cli.py week                         Show last 7 days (per-day + by-model)
  python cli.py stats                        Show all-time statistics
  python cli.py dashboard [--projects-dir PATH] [--host HOST] [--port PORT] [--no-browser] [--test-mode]
                                                 Scan + start dashboard (opens a browser unless --no-browser)
  python cli.py accounts ...                 Manage local-only account profiles
  python cli.py fable-next                   Recommend a local Claude Max profile
  python cli.py codex-next                   Recommend a local Codex profile without using reset credits
"""

COMMANDS = {
    "scan": cmd_scan,
    "today": cmd_today,
    "week": cmd_week,
    "stats": cmd_stats,
    "dashboard": cmd_dashboard,
    "accounts": cmd_accounts,
    "fable-next": cmd_fable_next,
    "codex-next": cmd_codex_next,
}

def parse_named_arg(args, flag):
    """Extract a --flag VALUE pair from an argument list."""
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
    return None

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(USAGE)
        sys.exit(0)

    command = sys.argv[1]
    rest = sys.argv[2:]
    projects_dir = parse_named_arg(rest, "--projects-dir")

    if command == "dashboard":
        cmd_dashboard(
            projects_dir=projects_dir,
            host=parse_named_arg(rest, "--host"),
            port=parse_named_arg(rest, "--port"),
            no_browser="--no-browser" in rest,
            test_mode="--test-mode" in rest,
        )
    elif command == "accounts":
        cmd_accounts(rest)
    elif command == "fable-next":
        cmd_fable_next(rest)
    elif command == "codex-next":
        cmd_codex_next(rest)
    elif command == "scan" and projects_dir:
        cmd_scan(projects_dir=projects_dir)
    else:
        COMMANDS[command]()
