"""
cli.py - Command-line interface for the Claude Code usage dashboard.

Commands:
  scan      - Scan JSONL files and update the database
  today     - Print today's usage summary
  stats     - Print all-time usage statistics
  dashboard - Scan + open browser + start dashboard server
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta, timezone

DB_PATH = Path.home() / ".claude" / "usage.db"

PRICING = {
    "claude-fable-5":    {"input": 10.00, "output": 50.00, "cache_read": 1.00, "cache_write": 12.50},
    "claude-mythos-5":   {"input": 10.00, "output": 50.00, "cache_read": 1.00, "cache_write": 12.50},
    "claude-opus-4-8":   {"input":  5.00, "output": 25.00, "cache_read": 0.50, "cache_write":  6.25},
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
    if "fable" in m:
        return PRICING["claude-fable-5"]
    if "mythos" in m:
        return PRICING["claude-mythos-5"]
    if "opus" in m:
        return PRICING["claude-opus-4-8"]
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


def cmd_report(period: str = "today", view: str = "card") -> None:
    from views import table_report, card_report, spark_report
    conn = require_db()
    conn.row_factory = sqlite3.Row
    dispatch = {"table": table_report, "card": card_report, "spark": spark_report}
    fn = dispatch.get(view)
    if fn is None:
        print(f"Unknown view '{view}'. Choose: table, card, spark")
        conn.close()
        return
    fn(conn, period)
    conn.close()


def cmd_daemons(prompt=False, labels=None, out=None):
    import classify
    report = classify.build_report()
    c = report["counts"]

    if prompt:
        import promptgen
        report_daemons = report["daemons"]
        if labels:
            wanted = set(labels)
            selected = [d for d in report_daemons if d["label"] in wanted]
        else:
            selected = [d for d in report_daemons if d["bucket"] in ("WASTE", "UNKNOWN")]
            selected += report["rogues"]
        text = promptgen.build_prompt(selected)
        if out:
            Path(out).write_text(text)
            print(f"Wrote repair prompt ({len(selected)} items) to {out}")
        else:
            print(text)
        return

    print()
    hr("=")
    print("  Daemon Health + Waste")
    hr("=")
    print(f"  HEALTHY={c['HEALTHY']}  WASTE={c['WASTE']}  "
          f"UNKNOWN={c['UNKNOWN']}  ROGUE={c['ROGUE']}")
    print(f"  Registry: {report['registry_path']}")
    hr()

    flagged = [d for d in report["daemons"] if d["bucket"] in ("WASTE",)]
    if flagged:
        print("  WASTE:")
        for d in flagged:
            print(f"    {d['label']}")
            print(f"      why: {'; '.join(d['reasons'])}")
            print(f"      fix: {d['remediation']}")
    if report["rogues"]:
        print("  ROGUE processes:")
        for r in report["rogues"]:
            print(f"    pid {r['pid']}: {'; '.join(r['reasons'])}")
            print(f"      fix: {r['remediation']}")
    unknown = sum(1 for d in report["daemons"] if d["bucket"] == "UNKNOWN")
    if unknown:
        print(f"  {unknown} UNKNOWN daemons need annotation in the registry "
              f"(run seed_manifest.py, then edit daemons.json).")
    hr("=")
    print()


def cmd_dashboard(projects_dir=None, host=None, port=None, no_browser=False):
    print("Running scan first...")
    cmd_scan(projects_dir=projects_dir)

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

    serve(host=host, port=port)


# ── Account credential helpers ────────────────────────────────────────────────

KEYCHAIN_SERVICE = "Claude Code-credentials"
_KEYCHAIN_CMD = ["security", "find-generic-password"]  # list-args; safe from shell injection


def parse_keychain_credentials(raw: str) -> dict[str, str]:
    """Parse macOS Keychain JSON into a normalised OAuth dict.

    Args:
        raw: JSON string from the security CLI.

    Returns:
        Dict with access_token, refresh_token, expires_at (ISO-8601 UTC).
    """
    creds = json.loads(raw)["claudeAiOauth"]
    exp = datetime.fromtimestamp(creds["expiresAt"] / 1000, tz=timezone.utc)
    return {
        "access_token": creds["accessToken"],
        "refresh_token": creds["refreshToken"],
        "expires_at": exp.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _read_keychain() -> str:
    """Read credentials from macOS Keychain; returns raw JSON string."""
    args = _KEYCHAIN_CMD + ["-s", KEYCHAIN_SERVICE, "-w"]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def cmd_accounts(rest: list[str] | None = None) -> None:
    """Manage tracked Claude accounts for limit orbs."""
    import accounts as _accts  # local import — only needed for this subcommand

    rest = rest or []
    sub = rest[0] if rest else "list"

    if sub == "add":
        # --quiet: non-interactive (no prompts) for the launchd token-refresh
        # job. --billing-day N: supply the renewal day for a brand-new account
        # without prompting.
        quiet = "--quiet" in rest
        billing_arg = parse_named_arg(rest, "--billing-day")
        raw = _read_keychain()
        oauth = parse_keychain_credentials(raw)
        email = _accts.fetch_profile_email(oauth)
        try:
            usage = _accts.fetch_usage(oauth)
        except Exception as e:  # noqa: BLE001 — fresh token still worth saving
            usage = None
            if not quiet:
                print(f"Warning: usage fetch failed ({e}); saving credentials anyway.")
        if not email:
            if quiet:
                print("ERROR: could not detect account email from keychain", file=sys.stderr)
                sys.exit(1)
            email = input("Account email for these credentials: ").strip()
        elif not quiet:
            print(f"Detected account: {email}")

        # Re-capture path: account already tracked -> refresh credentials only,
        # preserving billing history (charges, intervals, billing_day, is_main).
        store = _accts.load_store()
        if any(a["email"] == email for a in store["accounts"]):
            _accts.update_oauth(email, oauth, usage)
            if not quiet:
                print(f"Refreshed credentials for {email}.")
            return

        # New account: needs a billing day.
        if billing_arg is not None:
            billing = billing_arg
        elif quiet:
            print(f"ERROR: {email} is new; --billing-day required in --quiet mode",
                  file=sys.stderr)
            sys.exit(1)
        else:
            billing = input("Billing renewal day-of-month (e.g. 11): ").strip()
        try:
            billing_day = int(billing)
        except ValueError:
            print(f"Invalid billing day: {billing!r} (expected a number 1-28)")
            return
        if not 1 <= billing_day <= 28:
            print(f"Billing day must be between 1 and 28, got {billing_day}")
            return
        if usage is None:
            try:
                usage = _accts.fetch_usage(oauth)
            except Exception as e:  # noqa: BLE001
                print(f"Cannot register new account without usage data: {e}")
                return
        _accts.upsert_account({
            "email": email,
            "plan": "max_20x",
            "billing_day": billing_day,
            "oauth": oauth,
            "last_usage": {
                **usage,
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "error": None,
            },
        })
        print(f"Saved {email}. 5hr utilization: {usage['five_hour']['utilization']}%")

    elif sub == "list":
        for a in _accts.load_store()["accounts"]:
            u = a.get("last_usage") or {}
            err = f"  ERROR: {u['error']}" if u.get("error") else ""
            print(
                f"{a['email']:40s} billing day {a.get('billing_day')}  "
                f"last fetch {u.get('fetched_at', 'never')}{err}"
            )

    elif sub == "remove":
        if len(rest) < 2:
            print("usage: cli.py accounts remove <email>")
            return
        email = rest[1]
        store = _accts.load_store()
        store["accounts"] = [a for a in store["accounts"] if a["email"] != email]
        _accts.save_store(store)
        print(f"Removed {email}")

    else:
        print("usage: cli.py accounts [add|list|remove <email>]")


# ── Entry point ───────────────────────────────────────────────────────────────

USAGE = """
Claude Code Usage Dashboard

Usage:
  python cli.py scan [--projects-dir PATH]   Scan JSONL files and update database
  python cli.py today                        Show today's usage summary
  python cli.py week                         Show last 7 days (per-day + by-model)
  python cli.py stats                        Show all-time statistics
  python cli.py dashboard [--projects-dir PATH] [--host HOST] [--port PORT] [--no-browser]
                                                 Scan + start dashboard (opens a browser unless --no-browser)
  python cli.py daemons [--prompt] [--out FILE] [LABEL ...]
                                                 Daemon health/waste snapshot;
                                                 --prompt emits a repair prompt
  python cli.py report [today|week|month|all] [--view table|card|spark]
                                                 Usage report (default: today, card view)
  python cli.py accounts [add|list|remove]    Manage tracked Claude accounts for limit orbs
                                                 add [--quiet] [--billing-day N]
                                                 --quiet: no prompts (re-captures the live
                                                 keychain account, preserving billing history)
"""

COMMANDS = {
    "scan": cmd_scan,
    "today": cmd_today,
    "week": cmd_week,
    "stats": cmd_stats,
    "dashboard": cmd_dashboard,
    "daemons": cmd_daemons,
    "report": cmd_report,
    "accounts": cmd_accounts,
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
        )
    elif command == "scan" and projects_dir:
        cmd_scan(projects_dir=projects_dir)
    elif command == "daemons":
        out = parse_named_arg(rest, "--out")
        labels = []
        skip_next = False
        for i, a in enumerate(rest):
            if skip_next:
                skip_next = False
                continue
            if a == "--out":
                skip_next = True
            elif not a.startswith("--"):
                labels.append(a)
        cmd_daemons(prompt="--prompt" in rest, labels=labels or None, out=out)
    elif command == "report":
        period_arg = "today"
        view_arg = "card"
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--view" and i + 1 < len(rest):
                view_arg = rest[i + 1]
                i += 2
            elif not a.startswith("--"):
                period_arg = a
                i += 1
            else:
                i += 1
        cmd_report(period=period_arg, view=view_arg)
    elif command == "accounts":
        cmd_accounts(rest=rest)
    else:
        COMMANDS[command]()
