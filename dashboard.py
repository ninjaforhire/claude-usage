"""
dashboard.py - Local web dashboard served on localhost:8080.
"""

import json
import ipaddress
import os
import sqlite3
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from connectors.claude_subscription import read_subscription as read_claude_subscription
from connectors.codex_subscription import read_subscription as read_codex_subscription
from account_profiles import (
    TESTING_STORE_PATH,
    load_registry,
    profile_provider_cards,
    reset_testing_registry,
    seed_testing_registry,
)

DB_PATH = Path.home() / ".claude" / "usage.db"
CODEX_DB_PATH = Path.home() / ".claude-codex-usage" / "codex.db"
CHART_JS_PATH = Path(__file__).resolve().parent / "vendor" / "chart.umd.min.js"
HFO_ICON_PATH = Path(__file__).resolve().parent / "assets" / "hfo-icon.png"
SUBSCRIPTION_CACHE_TTL_SECONDS = 30.0
_SUBSCRIPTION_CACHE = None
_SUBSCRIPTION_CACHE_TIME = 0.0
_SUBSCRIPTION_CACHE_LOCK = threading.Lock()


def get_dashboard_data(db_path=DB_PATH):
    if not db_path.exists():
        return {"error": "Database not found. Run: python cli.py scan"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── All models (for filter UI) ────────────────────────────────────────────
    # GROUP BY uses the normalised expression too so NULL and '' don't end up
    # as two separate "unknown" rows.
    model_rows = conn.execute("""
        SELECT COALESCE(NULLIF(model, ''), 'unknown') as model
        FROM turns
        GROUP BY COALESCE(NULLIF(model, ''), 'unknown')
        ORDER BY SUM(input_tokens + output_tokens) DESC
    """).fetchall()
    all_models = [r["model"] for r in model_rows]

    # ── Daily per-model, ALL history (client filters by range) ────────────────
    daily_rows = conn.execute("""
        SELECT
            substr(timestamp, 1, 10)   as day,
            COALESCE(NULLIF(model, ''), 'unknown') as model,
            SUM(input_tokens)          as input,
            SUM(output_tokens)         as output,
            SUM(cache_read_tokens)     as cache_read,
            SUM(cache_creation_tokens) as cache_creation,
            COUNT(*)                   as turns
        FROM turns
        GROUP BY day, COALESCE(NULLIF(model, ''), 'unknown')
        ORDER BY day, model
    """).fetchall()

    daily_by_model = [
        {
            "day": r["day"],
            "model": r["model"],
            "input": r["input"] or 0,
            "output": r["output"] or 0,
            "cache_read": r["cache_read"] or 0,
            "cache_creation": r["cache_creation"] or 0,
            "turns": r["turns"] or 0,
        }
        for r in daily_rows
    ]

    # ── Hourly per-day per-model (client filters by range + TZ-shifts) ────────
    # Timestamps are ISO8601 UTC (e.g. "2026-04-08T09:30:00Z"); chars 12-13 = hour.
    hourly_rows = conn.execute("""
        SELECT
            substr(timestamp, 1, 10)                  as day,
            CAST(substr(timestamp, 12, 2) AS INTEGER) as hour,
            COALESCE(NULLIF(model, ''), 'unknown')    as model,
            SUM(output_tokens)                        as output,
            COUNT(*)                                  as turns
        FROM turns
        WHERE timestamp IS NOT NULL AND length(timestamp) >= 13
        GROUP BY day, hour, COALESCE(NULLIF(model, ''), 'unknown')
        ORDER BY day, hour, model
    """).fetchall()

    hourly_by_model = [
        {
            "day": r["day"],
            "hour": r["hour"] if r["hour"] is not None else 0,
            "model": r["model"],
            "output": r["output"] or 0,
            "turns": r["turns"] or 0,
        }
        for r in hourly_rows
    ]

    # ── All sessions (client filters by range and model) ──────────────────────
    session_rows = conn.execute("""
        SELECT
            session_id, project_name, first_timestamp, last_timestamp,
            total_input_tokens, total_output_tokens,
            total_cache_read, total_cache_creation, model, turn_count,
            git_branch
        FROM sessions
        ORDER BY last_timestamp DESC
    """).fetchall()

    sessions_all = []
    for r in session_rows:
        try:
            t1 = datetime.fromisoformat(r["first_timestamp"].replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(r["last_timestamp"].replace("Z", "+00:00"))
            duration_min = round((t2 - t1).total_seconds() / 60, 1)
        except Exception:
            duration_min = 0
        sessions_all.append(
            {
                "session_id": r["session_id"][:8],
                "project": r["project_name"] or "unknown",
                "branch": r["git_branch"] or "",
                "last": (r["last_timestamp"] or "")[:16].replace("T", " "),
                "last_date": (r["last_timestamp"] or "")[:10],
                "duration_min": duration_min,
                "model": r["model"] or "unknown",
                "turns": r["turn_count"] or 0,
                "input": r["total_input_tokens"] or 0,
                "output": r["total_output_tokens"] or 0,
                "cache_read": r["total_cache_read"] or 0,
                "cache_creation": r["total_cache_creation"] or 0,
            }
        )

    # ── Scan freshness (drives the staleness banner) ─────────────────────────
    row = conn.execute("SELECT MAX(mtime) AS m FROM processed_files").fetchone()
    last_scan_epoch = row["m"] or 0

    conn.close()

    unscanned = 0
    if last_scan_epoch:
        try:
            import scanner

            for d in scanner.DEFAULT_PROJECTS_DIRS:
                base = Path(d).expanduser()
                if not base.is_dir():
                    continue
                for p in base.rglob("*.jsonl"):
                    try:
                        if p.stat().st_mtime > last_scan_epoch:
                            unscanned += 1
                    except OSError:
                        continue
        except Exception:
            unscanned = -1  # walk failed; banner shows age only

    return {
        "freshness": {
            "last_scan_epoch": last_scan_epoch,
            "unscanned_files": unscanned,
        },
        "all_models": all_models,
        "daily_by_model": daily_by_model,
        "hourly_by_model": hourly_by_model,
        "sessions_all": sessions_all,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _empty_history(error: Optional[str] = None) -> dict[str, Any]:
    return {
        "all_models": [],
        "daily_by_model": [],
        "hourly_by_model": [],
        "sessions_all": [],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **({"error": error} if error else {}),
    }


def _provider_history(db_path: Path) -> dict[str, Any]:
    data = get_dashboard_data(db_path=db_path)
    if "error" in data:
        return _empty_history(data["error"])
    return data


def _history_totals(
    history: dict[str, Any], include_cache_creation: bool = True
) -> dict[str, int]:
    totals = {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_creation": 0,
        "turns": 0,
        "sessions": len(history["sessions_all"]),
    }
    for row in history["daily_by_model"]:
        for key in ("input", "output", "cache_read", "cache_creation", "turns"):
            totals[key] += row.get(key, 0) or 0
    totals["tokens"] = (
        totals["input"]
        + totals["output"]
        + totals["cache_read"]
        + (totals["cache_creation"] if include_cache_creation else 0)
    )
    return totals


def _unavailable_subscription(provider: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "available": False,
        "account": {},
        "windows": {},
    }


def get_subscription_data(force: bool = False) -> dict[str, Any]:
    """Read connectors through a bounded, single-flight in-memory cache."""
    global _SUBSCRIPTION_CACHE, _SUBSCRIPTION_CACHE_TIME
    with _SUBSCRIPTION_CACHE_LOCK:
        age = time.monotonic() - _SUBSCRIPTION_CACHE_TIME
        if (
            not force
            and _SUBSCRIPTION_CACHE is not None
            and age < SUBSCRIPTION_CACHE_TTL_SECONDS
        ):
            return _SUBSCRIPTION_CACHE
        _SUBSCRIPTION_CACHE = {
            "claude": read_claude_subscription(),
            "codex": read_codex_subscription(),
        }
        _SUBSCRIPTION_CACHE_TIME = time.monotonic()
        return _SUBSCRIPTION_CACHE


def get_public_dashboard_data(
    claude_db_path: Path = DB_PATH,
    codex_db_path: Path = CODEX_DB_PATH,
    include_subscriptions: bool = True,
    force_subscriptions: bool = False,
    profile_store_path: Optional[Path] = None,
    include_history: bool = True,
    test_mode: bool = False,
) -> dict[str, Any]:
    """Return provider-separated history plus safe account summaries."""
    claude_history = (
        _provider_history(Path(claude_db_path)) if include_history else _empty_history()
    )
    codex_history = (
        _provider_history(Path(codex_db_path)) if include_history else _empty_history()
    )
    subscriptions = (
        get_subscription_data(force=force_subscriptions)
        if include_subscriptions
        else {
            "claude": _unavailable_subscription("anthropic"),
            "codex": _unavailable_subscription("openai"),
        }
    )
    claude_totals = _history_totals(claude_history)
    codex_totals = _history_totals(codex_history, include_cache_creation=False)
    try:
        registry = (
            load_registry(profile_store_path, mode="testing")
            if profile_store_path is not None
            else load_registry()
        )
        test_profile_cards = profile_provider_cards(registry)
    except ValueError:
        # A malformed private test file must not make the public dashboard fail.
        test_profile_cards = []
    return {
        "providers": {
            "claude": {
                "history": claude_history,
                "subscription": subscriptions["claude"],
                "totals": claude_totals,
            },
            "codex": {
                "history": codex_history,
                "subscription": subscriptions["codex"],
                "totals": codex_totals,
            },
        },
        "overview": {
            "tokens": claude_totals["tokens"] + codex_totals["tokens"],
            "turns": claude_totals["turns"] + codex_totals["turns"],
            "sessions": claude_totals["sessions"] + codex_totals["sessions"],
        },
        "test_profile_cards": test_profile_cards,
        "test_mode": test_mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def refresh_local_histories() -> dict[str, Any]:
    """Run both incremental scanners without deleting a usable database."""
    import codex_scanner
    import scanner

    results = {}
    try:
        results["claude"] = scanner.scan(
            db_path=DB_PATH,
            projects_dirs=scanner.DEFAULT_PROJECTS_DIRS,
            verbose=False,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        results["claude"] = {"error": f"Claude scan failed: {exc}"}
    try:
        results["codex"] = codex_scanner.scan(
            db_path=CODEX_DB_PATH,
            session_dirs=codex_scanner.DEFAULT_SESSION_DIRS,
            verbose=False,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        results["codex"] = {"error": f"Codex scan failed: {exc}"}
    subscriptions = get_subscription_data(force=True)
    results["subscriptions"] = {
        provider: {
            "available": value.get("available", False),
            "error": value.get("error"),
        }
        for provider, value in subscriptions.items()
    }
    return results


def _is_loopback_host(value: Optional[str]) -> bool:
    if not value:
        return False
    hostname = value.strip().lower()
    if hostname.startswith("["):
        hostname = hostname[1:].split("]", 1)[0]
    elif hostname.count(":") == 1:
        hostname = hostname.rsplit(":", 1)[0]
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _request_is_local(handler: BaseHTTPRequestHandler) -> bool:
    if not _is_loopback_host(handler.headers.get("Host")):
        return False
    fetch_site = handler.headers.get("Sec-Fetch-Site", "").lower()
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return False
    origin = handler.headers.get("Origin")
    if origin:
        parsed = urlparse(origin)
        request_host = handler.headers.get("Host", "").lower()
        if (
            parsed.scheme != "http"
            or not _is_loopback_host(parsed.netloc)
            or parsed.netloc.lower() != request_host
        ):
            return False
    return True


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HotFix Ops Usage Dashboard</title>
<script src="/vendor/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #10161d;       /* deep operations surface */
    --card: #18212b;     /* Midnight Ops raised surface */
    --border: #3a4755;
    --text: #f5f5f7;     /* Clean Slate */
    --muted: #9aa8b6;
    --accent: #e8611b;   /* Come In Hot */
    --blue: #72b9d6;
    --green: #8cd2b3;
    --red: #ff7d67;
    --raised: #24313e;
    --selected: #2d3640; /* Midnight Ops */
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Plus Jakarta Sans', 'Avenir Next', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }

  /* Deliberate operations-console scrollbars: no arrows, steel thumb, deep
     surface track, and enough gutter for dense telemetry tables. */
  * { scrollbar-width: auto; scrollbar-color: #3a4755 #10161d; }
  ::-webkit-scrollbar { width: 21px; height: 21px; }
  ::-webkit-scrollbar-track { background: #10161d; }
  ::-webkit-scrollbar-thumb { background-color: #3a4755; border: 3px solid transparent; background-clip: padding-box; }
  ::-webkit-scrollbar-thumb:hover { background-color: #6b7b8d; }
  ::-webkit-scrollbar-thumb:active { background-color: #6b7b8d; }
  ::-webkit-scrollbar-corner { background: #10161d; }

  header { background: linear-gradient(115deg, #18212b, #2d3640); border-bottom: 1px solid var(--border); padding: 15px 24px; display: flex; align-items: center; justify-content: space-between; box-shadow: inset 0 -1px rgba(232,97,27,.16); }
  header h1 { font-family: 'Space Grotesk', 'Avenir Next', -apple-system, sans-serif; font-size: 17px; font-weight: 700; color: var(--text); letter-spacing: .1em; line-height: 1.15; text-transform: uppercase; }
  header .header-title { display: flex; align-items: center; gap: 12px; }
  .brand-link { align-items: center; color: inherit; display: flex; gap: 12px; text-decoration: none; }
  .brand-link:hover .header-kicker, .brand-link:focus-visible .header-kicker { color: #ff8a4c; }
  .brand-link:focus-visible, .footer-link:focus-visible { border-radius: 4px; outline: 2px solid var(--accent); outline-offset: 4px; }
  .header-brand { display: grid; gap: 3px; }
  .header-kicker { color: var(--accent); font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace; font-size: 10px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }
  header .header-icon { width: 38px; height: 38px; flex-shrink: 0; display: block; filter: drop-shadow(0 0 10px rgba(232,97,27,.24)); }
  header .meta { color: var(--muted); font-size: 12px; text-align: right; line-height: 1.5; margin-right: 20px; }
  #rescan-btn { background: var(--card); border: 1px solid var(--border); color: var(--muted); padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; margin-top: 4px; }
  #rescan-btn:hover { color: var(--text); border-color: var(--accent); }
  #rescan-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  #provider-nav { background: var(--card); border-bottom: 1px solid var(--border); padding: 0 24px; display: flex; gap: 4px; }
  .provider-tab { border: 0; border-bottom: 2px solid transparent; color: var(--muted); background: transparent; padding: 12px 16px; cursor: pointer; font-weight: 600; }
  .provider-tab:hover { color: var(--text); }
  .provider-tab.active { color: var(--text); border-bottom-color: var(--accent); }
  .overview-grid { display: grid; grid-template-columns: repeat(2, minmax(280px, 1fr)); gap: 18px; margin-bottom: 20px; }
  .provider-account-group { min-width: 0; }
  .provider-account-group-title { color: var(--muted); font-family: 'Space Grotesk', 'Avenir Next', -apple-system, sans-serif; font-size: 12px; font-weight: 700; letter-spacing: .1em; margin: 0 0 10px; text-transform: uppercase; }
  .provider-card-stack { display: grid; gap: 18px; }
  .provider-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 22px; display: flex; align-items: center; gap: 22px; }
  #detail-provider-summary { padding-top: 20px; padding-bottom: 0; }
  .provider-orb { --fill: 0; --orb: var(--accent); position: relative; isolation: isolate; overflow: hidden; width: 124px; height: 124px; flex: 0 0 124px; border: 1px solid color-mix(in srgb, var(--orb) 60%, var(--border)); border-radius: 50%; display: grid; place-items: center; background: radial-gradient(circle at 40% 28%, #3A3B3D 0%, #252628 48%, #111214 100%); box-shadow: inset 0 0 22px #090909, 0 0 34px color-mix(in srgb, var(--orb) 22%, transparent); }
  .provider-orb::before { content: ""; position: absolute; z-index: 1; inset: 7px 16px 56px 20px; border-radius: 50%; background: linear-gradient(145deg, rgba(255,255,255,.2), transparent 58%); pointer-events: none; }
  .orb-liquid { position: absolute; z-index: 0; left: -4%; right: -4%; bottom: -2%; height: calc(var(--fill) * 1% + 3%); min-height: 0; background: linear-gradient(180deg, color-mix(in srgb, var(--orb) 78%, white) 0%, var(--orb) 38%, color-mix(in srgb, var(--orb) 68%, #081118) 100%); box-shadow: 0 -3px 15px color-mix(in srgb, var(--orb) 65%, transparent), inset 0 12px 18px rgba(255,255,255,.12); transition: height .5s ease; }
  .orb-liquid::before { content: ""; position: absolute; left: -8%; top: -9px; width: 116%; height: 18px; border-radius: 50%; background: color-mix(in srgb, var(--orb) 82%, white); box-shadow: 0 -2px 10px color-mix(in srgb, var(--orb) 70%, transparent); }
  .orb-copy { position: relative; z-index: 2; text-align: center; }
  .orb-value { color: white; font-size: 24px; font-weight: 700; }
  .orb-label { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .08em; }
  .provider-copy h2 { color: var(--text); font-family: 'Space Grotesk', 'Avenir Next', -apple-system, sans-serif; letter-spacing: .03em; margin-bottom: 5px; }
  .provider-copy p { color: var(--muted); line-height: 1.55; }
  .provider-copy .account-name { color: var(--text); font-size: 16px; margin-bottom: 6px; overflow-wrap: anywhere; }
  .provider-copy .account-name strong { font-weight: 700; }
  .provider-copy .account-status { color: var(--muted); font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace; font-size: 10px; font-weight: 700; letter-spacing: .08em; margin-bottom: 5px; text-transform: uppercase; }
  .provider-copy .fable-headroom, .provider-copy .reset-credit-status { color: var(--text); font-size: 12px; margin-top: 8px; }
  .provider-copy .fable-headroom strong, .provider-copy .reset-credit-status strong { color: var(--accent); }
  .overview-totals { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
  .overview-total { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 18px; }
  .overview-total strong { display: block; color: var(--text); font-size: 25px; margin-top: 4px; }
  .overview-note { color: var(--muted); margin-top: 16px; line-height: 1.6; }
  .testing-mode-banner { align-items: center; background: rgba(232,97,27,.12); border-bottom: 1px solid rgba(232,97,27,.42); color: var(--text); display: flex; font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace; font-size: 11px; font-weight: 700; gap: 12px; justify-content: space-between; letter-spacing: .05em; padding: 10px 24px; text-transform: uppercase; }
  .testing-mode-banner strong { color: var(--accent); }
  .testing-mode-banner button { background: transparent; border: 1px solid var(--accent); border-radius: 5px; color: var(--accent); cursor: pointer; font: inherit; padding: 5px 9px; }
  .testing-mode-banner button:hover { background: var(--accent); color: #10161d; }
  .first-run-preview { background: var(--card); border: 1px dashed var(--accent); border-radius: 12px; color: var(--muted); grid-column: 1 / -1; padding: 28px; }
  .first-run-preview h2 { color: var(--text); font-family: 'Space Grotesk', 'Avenir Next', -apple-system, sans-serif; margin-bottom: 10px; }
  .first-run-preview ol { margin: 14px 0 0 20px; }
  .first-run-preview li { margin-bottom: 7px; }
  .hidden { display: none !important; }

  #filter-bar { background: var(--card); border-bottom: 1px solid var(--border); padding: 10px 24px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .filter-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); white-space: nowrap; }
  .filter-sep { width: 1px; height: 22px; background: var(--border); flex-shrink: 0; }
  #model-checkboxes { display: flex; flex-wrap: wrap; gap: 6px; }
  .model-cb-label { display: flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 20px; border: 1px solid var(--border); cursor: pointer; font-size: 12px; color: var(--muted); transition: border-color 0.15s, color 0.15s, background 0.15s; user-select: none; }
  .model-cb-label:hover { border-color: var(--accent); color: var(--text); }
  .model-cb-label.checked { background: var(--selected); border-color: var(--accent); color: var(--text); }
  .model-cb-label input { display: none; }
  .filter-btn { padding: 3px 10px; border-radius: 4px; border: 1px solid var(--border); background: transparent; color: var(--muted); font-size: 11px; cursor: pointer; white-space: nowrap; }
  .filter-btn:hover { border-color: var(--accent); color: var(--text); }
  .range-group { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; flex-shrink: 0; }
  .range-btn { padding: 4px 13px; background: transparent; border: none; border-right: 1px solid var(--border); color: var(--muted); font-size: 12px; cursor: pointer; transition: background 0.15s, color 0.15s; }
  .range-btn:last-child { border-right: none; }
  .range-btn:hover { background: var(--raised); color: var(--text); }
  .range-btn.active { background: var(--selected); color: var(--text); font-weight: 600; }

  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
  .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
  .stat-card .label { color: var(--muted); font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 6px; }
  .stat-card .value { font-size: 22px; font-weight: 700; }
  .stat-card .sub { color: var(--muted); font-size: 11px; margin-top: 4px; }

  .charts-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  /* min-width:0 lets the grid column shrink below the canvas's intrinsic
     pixel width; without it, narrowing the window can't narrow the container,
     so Chart.js's ResizeObserver never fires until a data refresh rebuilds the
     canvas. (Expanding already works — 1fr columns grow freely.) */
  .chart-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; min-width: 0; }
  .chart-card.wide { grid-column: 1 / -1; }
  .chart-card h2 { font-family: 'Space Grotesk', 'Avenir Next', -apple-system, sans-serif; font-size: 13px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 16px; }
  .chart-wrap { position: relative; height: 240px; }
  .chart-wrap.tall { height: 300px; }
  .chart-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
  .chart-header h2 { margin-bottom: 0; }
  .chart-header-right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .chart-day-count { font-size: 11px; color: var(--muted); }
  .tz-group { display: flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
  .tz-btn { padding: 3px 10px; background: transparent; border: none; border-right: 1px solid var(--border); color: var(--muted); font-size: 11px; cursor: pointer; transition: background 0.15s, color 0.15s; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }
  .tz-btn:last-child { border-right: none; }
  .tz-btn:hover { background: var(--raised); color: var(--text); }
  .tz-btn.active { background: var(--selected); color: var(--text); }
  .peak-legend { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); }
  .peak-swatch { width: 10px; height: 10px; background: var(--red); border-radius: 2px; display: inline-block; }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); border-bottom: 1px solid var(--border); white-space: nowrap; }
  th.sortable { cursor: pointer; user-select: none; }
  th.sortable:hover { color: var(--text); }
  .sort-icon { font-size: 9px; opacity: 0.8; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); font-size: 13px; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--raised); }
  .model-tag { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; background: rgba(72,160,199,0.15); color: var(--blue); }
  .cost { color: var(--green); font-family: monospace; }
  .cost-na { color: var(--muted); font-family: monospace; font-size: 11px; }
  .num { font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, monospace; }
  .muted { color: var(--muted); }
  .section-title { font-family: 'Space Grotesk', 'Avenir Next', -apple-system, sans-serif; font-size: 13px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px; }
  .section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .section-header .section-title { margin-bottom: 0; }
  .export-btn { background: var(--card); border: 1px solid var(--border); color: var(--muted); padding: 3px 10px; border-radius: 5px; cursor: pointer; font-size: 11px; }
  .export-btn:hover { color: var(--text); border-color: var(--accent); }
  .table-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 24px; overflow-x: auto; }
  .table-foot { display: flex; justify-content: flex-end; align-items: center; gap: 12px; margin-top: 12px; }
  .table-foot:empty { margin-top: 0; }
  .show-more-btn { background: transparent; border: 1px solid var(--border); color: var(--muted); padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
  .show-more-btn:hover { color: var(--text); border-color: var(--accent); }
  .show-more-link { color: var(--blue); text-decoration: none; font-size: 12px; cursor: pointer; }
  .show-more-link:hover { text-decoration: underline; }

  footer { border-top: 1px solid var(--border); padding: 20px 24px; margin-top: 8px; }
  .footer-content { max-width: 1400px; margin: 0 auto; }
  .footer-content p { color: var(--muted); font-size: 12px; line-height: 1.7; margin-bottom: 4px; }
  .footer-content .footer-brand { color: var(--accent); font-family: 'Space Grotesk', 'Avenir Next', -apple-system, sans-serif; font-weight: 700; letter-spacing: .08em; }
  .footer-link { color: inherit; text-decoration: none; }
  .footer-link:hover .footer-brand { color: #ff8a4c; }
  .footer-content p:last-child { margin-bottom: 0; }
  .footer-content a { color: var(--blue); text-decoration: none; }
  .footer-content a:hover { text-decoration: underline; }

  /* ── Account limit orbs ── */
  #accounts-row{display:flex;gap:18px;flex-wrap:wrap;padding:18px 24px 6px}
  .acct-card{flex:1;min-width:280px;background:#0d1118;border:1px solid #1d2530;border-radius:12px;padding:16px 18px}
  .acct-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px}
  .acct-email{font-size:12.5px;font-weight:600}
  .acct-plan{font-size:10.5px;color:#7a8696;text-transform:uppercase;letter-spacing:.08em}
  .acct-pair{display:flex;gap:16px;justify-content:center}
  .acct-gauge{text-align:center}
  .acct-glabel{font-size:10px;color:#7a8696;text-transform:uppercase;letter-spacing:.1em;margin-top:8px}
  .acct-timer{font-size:11px;font-variant-numeric:tabular-nums;margin-top:2px}
  .acct-meta{display:flex;justify-content:space-between;margin-top:14px;padding-top:10px;border-top:1px solid #1d2530;font-size:10.5px;color:#7a8696}
  .acct-badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;background:#0f2;color:#031;box-shadow:0 0 12px #0f26}
  .acct-badge-inactive{background:#4F4F50;color:#161617;box-shadow:none}
  .acct-inactive{opacity:.55;filter:saturate(.4)}
  .acct-error{filter:grayscale(1) brightness(.6)}
  .acct-error-msg{font-size:10.5px;color:#ff6b6b;margin-top:8px;text-align:center}
  .acct-stale-note{font-size:10.5px;color:#7a8696;margin-top:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #accounts-bar{display:flex;align-items:center;gap:10px;padding:0 24px;font-size:11px;color:#7a8696}
  .orbC{position:relative;width:104px;height:104px;border-radius:50%;margin:0 auto;overflow:hidden;
    background:radial-gradient(circle at 30% 25%, rgba(255,255,255,.10), rgba(255,255,255,0) 45%),
               radial-gradient(circle at 70% 80%, rgba(120,160,255,.06), transparent 60%),
               radial-gradient(circle at 50% 50%, #0c1119, #04060a 80%);
    border:1px solid rgba(255,255,255,.14);
    box-shadow:inset 0 0 30px rgba(0,0,0,.9), inset 0 2px 6px rgba(255,255,255,.18),
               0 10px 24px rgba(0,0,0,.7), 0 0 28px var(--c-glow);}
  .orbC .fill{position:absolute;left:-12%;right:-12%;bottom:-4px;transition:height .8s;border-radius:42% 46% 0 0/14px 18px 0 0;
    background:linear-gradient(180deg, var(--c-hi), var(--c-lo) 85%);
    box-shadow:0 -2px 18px var(--c-hi), inset 0 10px 20px rgba(255,255,255,.28);
    animation:tilt 5s ease-in-out infinite}
  @keyframes tilt{0%,100%{transform:rotate(-1.6deg)}50%{transform:rotate(1.6deg)}}
  .orbC .glints{position:absolute;inset:0;pointer-events:none;
    background:radial-gradient(ellipse 40% 18% at 30% 14%, rgba(255,255,255,.6), transparent 70%),
               radial-gradient(ellipse 16% 8% at 68% 22%, rgba(255,255,255,.35), transparent 70%)}
  .orbC .rim{position:absolute;inset:-1px;border-radius:50%;pointer-events:none;
    border:2px solid transparent;
    background:linear-gradient(160deg, rgba(255,255,255,.35), transparent 30%, transparent 70%, rgba(255,255,255,.12)) border-box;
    -webkit-mask:linear-gradient(#fff 0 0) padding-box, linear-gradient(#fff 0 0);
    -webkit-mask-composite:xor;mask-composite:exclude}
  .orbC .num{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
    font-weight:800;font-size:19px;letter-spacing:-.02em;text-shadow:0 2px 6px rgba(0,0,0,.95);z-index:2}
  @media (max-width: 768px) {
    .charts-grid, .overview-grid { grid-template-columns: 1fr; }
    .chart-card.wide { grid-column: 1; }
    .overview-totals { grid-template-columns: 1fr; }
    .provider-card { align-items: flex-start; flex-direction: column; }
    .testing-mode-banner { align-items: flex-start; flex-direction: column; }
  }
</style>
</head>
<body>
<header>
  <div class="header-title">
    <a class="brand-link" href="https://hotfixops.com/" target="_blank" rel="noopener noreferrer" aria-label="Visit HotFix Ops">
      <img class="header-icon" src="/assets/hfo-icon.png" alt="HotFix Ops">
      <div class="header-brand">
        <span class="header-kicker">HotFix Ops</span>
        <h1 id="page-title">Usage Dashboard</h1>
      </div>
    </a>
    <a href="/daemons" style="color:#5b9bd5;text-decoration:none;font-size:13px;margin-left:8px">Daemons &amp; Waste &rarr;</a>
  </div>
  <div class="meta" id="meta">Loading...</div>
  <button id="rescan-btn" onclick="triggerRefresh()" title="Scan local Claude and Codex history and refresh supported account limits.">&#x21bb; Refresh</button>
</header>

<div id="freshness-banner" style="display:none;padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)"></div>

<div id="accounts-bar">
  <strong style="color:#d7dee8">Account Limits</strong>
  <span id="accounts-fetched">never fetched</span>
  <span id="accounts-total" style="margin-left:auto;color:#9aa7b6;font-variant-numeric:tabular-nums"></span>
  <button id="accounts-refresh-btn" class="filter-btn" onclick="refreshAccounts()">&#x21bb; Refresh</button>
</div>
<div id="accounts-row"></div>

<nav id="provider-nav" aria-label="Usage provider">
  <button class="provider-tab active" data-provider="overview" onclick="setProvider('overview')">Overview</button>
  <button class="provider-tab" data-provider="claude" onclick="setProvider('claude')">Claude</button>
  <button class="provider-tab" data-provider="codex" onclick="setProvider('codex')">Codex</button>
</nav>

<section id="testing-mode-banner" class="testing-mode-banner hidden" aria-live="polite">
  <span><strong>Test mode</strong> · isolated first-run preview · no normal history or account data is read</span>
  <span>
    <button id="seed-testing-mode" type="button" onclick="seedTestingMode()">Load sample accounts</button>
    <button id="reset-testing-mode" type="button" onclick="resetTestingMode()">Reset preview</button>
  </span>
</section>

<main id="overview-view" class="container">
  <div class="overview-grid" id="overview-grid"></div>
  <div class="overview-totals" id="overview-totals"></div>
  <p class="overview-note" id="overview-note">Subscription windows stay provider-specific. Claude and Codex percentages are never combined into a misleading universal limit.</p>
</main>

<div id="details-view" class="hidden">
<div id="detail-provider-summary" class="container"></div>
<div id="filter-bar">
  <div class="filter-label">Models</div>
  <div id="model-checkboxes"></div>
  <button class="filter-btn" onclick="selectAllModels()">All</button>
  <button class="filter-btn" onclick="clearAllModels()">None</button>
  <div class="filter-sep"></div>
  <div class="filter-label">Range</div>
  <div class="range-group">
    <button class="range-btn" data-range="today" onclick="setRange('today')">Today</button>
    <button class="range-btn" data-range="week" onclick="setRange('week')">This Week</button>
    <button class="range-btn" data-range="month" onclick="setRange('month')">This Month</button>
    <button class="range-btn" data-range="prev-month" onclick="setRange('prev-month')">Prev Month</button>
    <button class="range-btn" data-range="7d"  onclick="setRange('7d')">7d</button>
    <button class="range-btn" data-range="30d" onclick="setRange('30d')">30d</button>
    <button class="range-btn" data-range="90d" onclick="setRange('90d')">90d</button>
    <button class="range-btn" data-range="all" onclick="setRange('all')">All</button>
  </div>
</div>

<div class="container">
  <div class="stats-row" id="stats-row"></div>
  <div class="charts-grid">
    <div class="chart-card wide">
      <h2 id="daily-chart-title">Daily Token Usage</h2>
      <div class="chart-wrap tall"><canvas id="chart-daily"></canvas></div>
    </div>
    <div class="chart-card wide">
      <div class="chart-header">
        <h2 id="hourly-chart-title">Average Hourly Distribution</h2>
        <div class="chart-header-right">
          <span class="peak-legend" id="peak-legend" title="Mon–Fri 05:00–11:00 PT — Anthropic peak-hour throttling window"><span class="peak-swatch"></span>Peak hours (PT)</span>
          <span class="chart-day-count" id="hourly-day-count"></span>
          <div class="tz-group">
            <button class="tz-btn" data-tz="local" onclick="setHourlyTZ('local')">Local</button>
            <button class="tz-btn" data-tz="utc"   onclick="setHourlyTZ('utc')">UTC</button>
          </div>
        </div>
      </div>
      <div class="chart-wrap"><canvas id="chart-hourly"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>By Model</h2>
      <div class="chart-wrap"><canvas id="chart-model"></canvas></div>
    </div>
    <div class="chart-card">
      <h2>Top Projects by Tokens</h2>
      <div class="chart-wrap"><canvas id="chart-project"></canvas></div>
    </div>
  </div>
  <div class="table-card">
    <div class="section-title" id="model-table-title">Cost by Model</div>
    <table>
      <thead><tr>
        <th>Model</th>
        <th class="sortable" onclick="setModelSort('turns')">Turns <span class="sort-icon" id="msort-turns"></span></th>
        <th class="sortable" onclick="setModelSort('input')">Fresh Input <span class="sort-icon" id="msort-input"></span></th>
        <th class="sortable" onclick="setModelSort('output')">Output <span class="sort-icon" id="msort-output"></span></th>
        <th class="sortable" onclick="setModelSort('cache_read')">Cache Read <span class="sort-icon" id="msort-cache_read"></span></th>
        <th id="cache-creation-head" class="sortable" onclick="setModelSort('cache_creation')">Cache Creation <span class="sort-icon" id="msort-cache_creation"></span></th>
        <th class="sortable" onclick="setModelSort('cost')">Est. Cost <span class="sort-icon" id="msort-cost"></span></th>
      </tr></thead>
      <tbody id="model-cost-body"></tbody>
    </table>
    <div class="table-foot" id="model-cost-foot"></div>
  </div>
  <div class="table-card">
    <div class="section-header"><div class="section-title">Recent Sessions</div><button class="export-btn" onclick="exportSessionsCSV()" title="Export all filtered sessions to CSV">&#x2913; CSV</button></div>
    <table>
      <thead><tr>
        <th>Session</th>
        <th>Project</th>
        <th class="sortable" onclick="setSessionSort('last')">Last Active <span class="sort-icon" id="sort-icon-last"></span></th>
        <th class="sortable" onclick="setSessionSort('duration_min')">Duration <span class="sort-icon" id="sort-icon-duration_min"></span></th>
        <th>Model</th>
        <th class="sortable" onclick="setSessionSort('turns')">Turns <span class="sort-icon" id="sort-icon-turns"></span></th>
        <th class="sortable" onclick="setSessionSort('input')">Fresh Input <span class="sort-icon" id="sort-icon-input"></span></th>
        <th class="sortable" onclick="setSessionSort('output')">Output <span class="sort-icon" id="sort-icon-output"></span></th>
        <th class="sortable" onclick="setSessionSort('cost')">Est. Cost <span class="sort-icon" id="sort-icon-cost"></span></th>
      </tr></thead>
      <tbody id="sessions-body"></tbody>
    </table>
    <div class="table-foot" id="sessions-foot"></div>
  </div>
  <div class="table-card">
    <div class="section-header"><div class="section-title" id="project-table-title">Cost by Project</div><button class="export-btn" onclick="exportProjectsCSV()" title="Export all projects to CSV">&#x2913; CSV</button></div>
    <table>
      <thead><tr>
        <th>Project</th>
        <th class="sortable" onclick="setProjectSort('sessions')">Sessions <span class="sort-icon" id="psort-sessions"></span></th>
        <th class="sortable" onclick="setProjectSort('turns')">Turns <span class="sort-icon" id="psort-turns"></span></th>
        <th class="sortable" onclick="setProjectSort('input')">Fresh Input <span class="sort-icon" id="psort-input"></span></th>
        <th class="sortable" onclick="setProjectSort('output')">Output <span class="sort-icon" id="psort-output"></span></th>
        <th class="sortable" onclick="setProjectSort('cost')">Est. Cost <span class="sort-icon" id="psort-cost"></span></th>
      </tr></thead>
      <tbody id="project-cost-body"></tbody>
    </table>
    <div class="table-foot" id="project-cost-foot"></div>
  </div>
  <div class="table-card">
    <div class="section-header"><div class="section-title" id="branch-table-title">Cost by Project &amp; Branch</div><button class="export-btn" onclick="exportProjectBranchCSV()" title="Export project+branch breakdown to CSV">&#x2913; CSV</button></div>
    <table>
      <thead><tr>
        <th>Project</th>
        <th>Branch</th>
        <th class="sortable" onclick="setProjectBranchSort('sessions')">Sessions <span class="sort-icon" id="pbsort-sessions"></span></th>
        <th class="sortable" onclick="setProjectBranchSort('turns')">Turns <span class="sort-icon" id="pbsort-turns"></span></th>
        <th class="sortable" onclick="setProjectBranchSort('input')">Fresh Input <span class="sort-icon" id="pbsort-input"></span></th>
        <th class="sortable" onclick="setProjectBranchSort('output')">Output <span class="sort-icon" id="pbsort-output"></span></th>
        <th class="sortable" onclick="setProjectBranchSort('cost')">Est. Cost <span class="sort-icon" id="pbsort-cost"></span></th>
      </tr></thead>
      <tbody id="project-branch-cost-body"></tbody>
    </table>
    <div class="table-foot" id="project-branch-cost-foot"></div>
  </div>
</div>
</div>

<footer>
  <div class="footer-content">
    <p>Cost estimates based on Anthropic API pricing (<a href="https://claude.com/pricing#api" target="_blank">claude.com/pricing#api</a>) as of May 2026. Only models containing <em>fable</em>, <em>mythos</em>, <em>opus</em>, <em>sonnet</em>, or <em>haiku</em> in the name are included in cost calculations. Actual costs for Max/Pro subscribers differ from API pricing.</p>
    <p>
      Source: <a href="https://github.com/ninjaforhire/claude-usage" target="_blank">HotFix Ops Usage on GitHub</a>
      &nbsp;&middot;&nbsp;
      Based on <a href="https://github.com/phuryn/claude-usage" target="_blank">phuryn/claude-usage</a>
      &nbsp;&middot;&nbsp;
      License: MIT
    </p>
    <p>Any displayed cost is an API-equivalent estimate, not a subscription charge. Claude subscription limits are shown only when a user-supplied connector provides them; unsupported fields remain unavailable.</p>
    <p><a class="footer-link" href="https://hotfixops.com/" target="_blank" rel="noopener noreferrer"><span class="footer-brand">HOTFIX OPS</span></a> &nbsp;&middot;&nbsp; Local-first usage telemetry &nbsp;&middot;&nbsp; Your data stays on your device</p>
  </div>
</footer>

<script>
// ── Helpers ────────────────────────────────────────────────────────────────
function esc(s) {
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

// ── State ──────────────────────────────────────────────────────────────────
let dashboardPayload = null;
let activeProvider = 'overview';
let rawData = null;
let selectedModels = new Set();
let selectedRange = '30d';
let charts = {};
let sessionSortCol = 'last';
let modelSortCol = 'cost';
let modelSortDir = 'desc';
let projectSortCol = 'cost';
let projectSortDir = 'desc';
let branchSortCol = 'cost';
let branchSortDir = 'desc';
let lastFilteredSessions = [];
let lastByModel = [];
let lastByProject = [];
let lastByProjectBranch = [];
let sessionSortDir = 'desc';

// Tables reveal rows in steps: 10 -> 25 -> 50, capped at 50 because rendering
// more than that visibly hurts performance. Past 50 the footer offers a
// "Download CSV to see more" link instead of another in-table step, plus a
// Show less button that resets straight back to 10. Limits persist across
// re-renders so sorting/filtering keeps the user's chosen depth (visible rows
// always reflect the active sort).
const TABLE_STEPS = [10, 25, 50];
const TABLE_MAX = TABLE_STEPS[TABLE_STEPS.length - 1];  // hard cap on in-table rows
function nextTableLimit(current, total) {
  for (const s of TABLE_STEPS) {
    if (s > current && s < total) return s;
  }
  return Math.min(total, TABLE_MAX);  // reveal everything, but never past the cap
}
let modelLimit = TABLE_STEPS[0];
let sessionsLimit = TABLE_STEPS[0];
let projectLimit = TABLE_STEPS[0];
let branchLimit = TABLE_STEPS[0];
let hourlyTZ = 'local';  // 'local' or 'utc'

function providerLabel(provider) {
  return provider === 'claude' ? 'Claude' : 'Codex';
}

function setProvider(provider) {
  if (!['overview', 'claude', 'codex'].includes(provider)) return;
  const previousProvider = activeProvider;
  const providerChanged = previousProvider !== provider;
  const previousModelValues = Array.from(
    document.querySelectorAll('#model-checkboxes input')
  ).map(input => input.value);
  const previousSelectionWasAll = !providerChanged
    && previousModelValues.length > 0
    && previousModelValues.every(model => selectedModels.has(model));
  activeProvider = provider;
  document.querySelectorAll('.provider-tab').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.provider === provider)
  );
  const overview = provider === 'overview';
  document.getElementById('overview-view').classList.toggle('hidden', !overview);
  document.getElementById('details-view').classList.toggle('hidden', overview);
  document.getElementById('page-title').textContent = overview
    ? 'Usage Dashboard'
    : providerLabel(provider) + ' Usage';
  if (!overview && dashboardPayload) {
    rawData = dashboardPayload.providers[provider].history;
    const isCodex = provider === 'codex';
    document.getElementById('cache-creation-head').childNodes[0].textContent =
      isCodex ? 'Reasoning ' : 'Cache Creation ';
    document.getElementById('model-table-title').textContent =
      isCodex ? 'Usage by Model' : 'Cost by Model';
    document.getElementById('project-table-title').textContent =
      isCodex ? 'Usage by Project' : 'Cost by Project';
    document.getElementById('branch-table-title').textContent =
      isCodex ? 'Usage by Project & Branch' : 'Cost by Project & Branch';
    document.getElementById('peak-legend').classList.toggle('hidden', isCodex);
    document.getElementById('detail-provider-summary').innerHTML = renderProviderAccountGroup(
      provider,
      providerCards(dashboardPayload, provider)
    );
    buildFilterUI(
      rawData.all_models,
      providerChanged && previousProvider === 'overview',
      providerChanged ? null : selectedModels,
      previousSelectionWasAll
    );
    updateSortIcons();
    updateModelSortIcons();
    updateProjectSortIcons();
    updateProjectBranchSortIcons();
    applyFilter();
  }
}

function subscriptionSummary(subscription) {
  const account = subscription.account || {};
  const label = account.email || account.label || (subscription.available ? 'Connected account' : 'Not connected');
  const plan = account.plan ? account.plan.charAt(0).toUpperCase() + account.plan.slice(1) : 'Plan unavailable';
  return { label, plan };
}

function orbWindow(subscription) {
  const windows = subscription.windows || {};
  return windows.seven_day || windows.five_hour || null;
}

function renderProviderCard(key, provider) {
  const subscription = provider.subscription || {};
  const account = subscriptionSummary(subscription);
  const accountName = provider.profile_label || account.label;
  const windowData = orbWindow(subscription);
  const totals = provider.totals || { tokens: 0, sessions: 0 };
  const used = windowData ? Math.round(windowData.used_percent) : 0;
  const value = windowData ? used + '%' : (subscription.available ? '\u2713' : '\u2014');
  const orbLabel = windowData ? 'used' : (subscription.available ? 'connected' : 'offline');
  const color = key === 'claude' ? '#e8611b' : '#72b9d6';
  const status = subscription.error
    ? subscription.error
    : (windowData ? (100 - used) + '% remains in the displayed window' : 'Live limits unavailable');
  const accountStatus = provider.inactive
    ? '<p class="account-status">Inactive account</p>'
    : provider.profile_label
    ? '<p class="account-status">Configured account</p>'
    : '';
  const fable = key === 'claude' ? (provider.fable || fableHeadroom(subscription)) : null;
  const fableStatus = fable
    ? `<p class="fable-headroom">Fable 5 · <strong>${fmtPercent(fable.guaranteed_percent)} guaranteed weekly headroom</strong> · ${fmtPercent(fable.weekly_remaining_percent)} total week remains</p>`
    : '';
  const resetCredits = key === 'codex' ? subscription.reset_credits : null;
  const resetStatus = resetCredits && Number.isInteger(resetCredits.available_count)
    ? `<p class="reset-credit-status">Codex reset credits · <strong>${esc(resetCredits.available_count)} available</strong>${resetCredits.expires_at ? ' · expires ' + esc(resetCredits.expires_at) : ''}</p>`
    : '';
  const historySummary = provider.profile_label
    ? '<p>Sanitized local quota snapshot · history is not attributed to this profile.</p>'
    : `<p>${fmt(totals.tokens)} locally recorded tokens \u00b7 ${fmt(totals.sessions)} sessions</p>`;
  return `<article class="provider-card">
    <div class="provider-orb" style="--fill:${used};--orb:${color}">
      <div class="orb-liquid" aria-hidden="true"></div>
      <div class="orb-copy"><div class="orb-value">${esc(value)}</div><div class="orb-label">${esc(orbLabel)}</div></div>
    </div>
    <div class="provider-copy">
      <h2>${esc(providerLabel(key))}</h2>
      <p class="account-name"><strong>${esc(accountName)}</strong></p>
      ${accountStatus}
      <p>${esc(account.plan)} \u00b7 ${esc(status)}</p>
      ${fableStatus}
      ${resetStatus}
      ${historySummary}
    </div>
  </article>`;
}

function configuredProfileCards(payload) {
  return Array.isArray(payload.test_profile_cards) ? payload.test_profile_cards : [];
}

function providerCards(payload, provider) {
  const cards = configuredProfileCards(payload).filter(card => card.provider === provider);
  if (payload.test_mode && cards.length) return cards;
  return cards.length
    ? [payload.providers[provider], ...cards]
    : [payload.providers[provider]];
}

function renderProviderAccountGroup(provider, cards) {
  return `<section class="provider-account-group" aria-label="${esc(providerLabel(provider))} accounts">
    <h2 class="provider-account-group-title">${esc(providerLabel(provider))} accounts</h2>
    <div class="provider-card-stack">
      ${cards.map(card => renderProviderCard(provider, card)).join('')}
    </div>
  </section>`;
}

function renderTestingMode(testMode) {
  document.getElementById('testing-mode-banner').classList.toggle('hidden', !testMode);
}

function fmtPercent(value) {
  return Number.isFinite(value) ? Math.round(value) + '%' : '\u2014';
}

function fableHeadroom(subscription) {
  const plan = String((subscription.account || {}).plan || '').toLowerCase();
  if (!plan.includes('max') && !plan.includes('premium')) return null;
  const weekly = (subscription.windows || {}).seven_day;
  const remaining = weekly && Number(weekly.remaining_percent);
  if (!Number.isFinite(remaining)) return null;
  return {
    guaranteed_percent: Math.max(0, Math.min(50, remaining - 50)),
    weekly_remaining_percent: Math.max(0, Math.min(100, remaining)),
  };
}

function renderOverview(payload) {
  const profileCards = configuredProfileCards(payload);
  document.getElementById('overview-grid').innerHTML = payload.test_mode && !profileCards.length
    ? `<section class="first-run-preview">
        <h2>Connect your accounts</h2>
        <p>This is the blank first-run view. It reads no normal usage or account data.</p>
        <ol>
          <li>Run <code>python3 cli.py accounts setup --label "Work Max"</code> to add a Claude or Codex account.</li>
          <li>Return here to confirm its account card and mana orb.</li>
          <li>Use <strong>Reset preview</strong> above to return to this exact state.</li>
        </ol>
      </section>`
    : ['claude', 'codex']
      .map(provider => renderProviderAccountGroup(provider, providerCards(payload, provider)))
      .join('');
  document.getElementById('overview-note').textContent = profileCards.length
    ? 'Account cards are sanitized local quota snapshots. Historical tokens, projects, branches, and sessions remain unassigned unless their local source stores are separated by account.'
    : 'Subscription windows stay provider-specific. Claude and Codex percentages are never combined into a misleading universal limit.';
  document.getElementById('overview-totals').innerHTML = [
    ['All recorded tokens', payload.overview.tokens],
    ['All turns', payload.overview.turns],
    ['All sessions', payload.overview.sessions],
  ].map(([label, value]) =>
    `<div class="overview-total"><span>${esc(label)}</span><strong>${fmt(value)}</strong></div>`
  ).join('');
}

// ── Peak-hour config ───────────────────────────────────────────────────────
// Anthropic throttles Mon–Fri 05:00–11:00 PT. We approximate as fixed UTC hours
// 12–17 (matches PDT; during PST the window shifts by 1h — accepted simplification).
const PEAK_HOURS_UTC = new Set([12, 13, 14, 15, 16, 17]);

// Local-timezone offset in hours (signed). Fractional offsets (e.g. India UTC+5:30)
// are rounded to the nearest hour for bucket alignment.
function localOffsetHours() {
  return Math.round(-new Date().getTimezoneOffset() / 60);
}

// Return the UTC hour (0–23) corresponding to a displayed-hour bucket.
function displayHourToUTC(displayHour, tzMode) {
  if (tzMode === 'utc') return displayHour;
  return ((displayHour - localOffsetHours()) % 24 + 24) % 24;
}

// Return the displayed-hour bucket for a UTC hour.
function utcHourToDisplay(utcHour, tzMode) {
  if (tzMode === 'utc') return utcHour;
  return ((utcHour + localOffsetHours()) % 24 + 24) % 24;
}

function isPeakHour(displayHour, tzMode) {
  return PEAK_HOURS_UTC.has(displayHourToUTC(displayHour, tzMode));
}

function formatHourLabel(h) {
  return String(h).padStart(2, '0') + ':00';
}

function tzDisplayName(tzMode) {
  if (tzMode === 'utc') return 'UTC';
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local';
  } catch(e) {
    return 'Local';
  }
}

// ── Pricing (Anthropic API, April 2026) ────────────────────────────────────
const PRICING = {
  'claude-fable-5':    { input: 10.00, output: 50.00, cache_write: 12.50, cache_read: 1.00 },
  'claude-mythos-5':   { input: 10.00, output: 50.00, cache_write: 12.50, cache_read: 1.00 },
  'claude-opus-5':     { input:  5.00, output: 25.00, cache_write:  6.25, cache_read: 0.50 },
  'claude-opus-4-8':   { input:  5.00, output: 25.00, cache_write:  6.25, cache_read: 0.50 },
  'claude-opus-4-7':   { input:  5.00, output: 25.00, cache_write:  6.25, cache_read: 0.50 },
  'claude-opus-4-6':   { input:  5.00, output: 25.00, cache_write:  6.25, cache_read: 0.50 },
  'claude-opus-4-5':   { input:  5.00, output: 25.00, cache_write:  6.25, cache_read: 0.50 },
  'claude-sonnet-5':   { input:  3.00, output: 15.00, cache_write:  3.75, cache_read: 0.30 },
  'claude-sonnet-4-7': { input:  3.00, output: 15.00, cache_write:  3.75, cache_read: 0.30 },
  'claude-sonnet-4-6': { input:  3.00, output: 15.00, cache_write:  3.75, cache_read: 0.30 },
  'claude-sonnet-4-5': { input:  3.00, output: 15.00, cache_write:  3.75, cache_read: 0.30 },
  'claude-haiku-4-7':  { input:  1.00, output:  5.00, cache_write:  1.25, cache_read: 0.10 },
  'claude-haiku-4-6':  { input:  1.00, output:  5.00, cache_write:  1.25, cache_read: 0.10 },
  'claude-haiku-4-5':  { input:  1.00, output:  5.00, cache_write:  1.25, cache_read: 0.10 },
};

function isBillable(model) {
  if (!model) return false;
  const m = model.toLowerCase();
  return m.includes('fable') || m.includes('mythos') || m.includes('opus') || m.includes('sonnet') || m.includes('haiku');
}

function getPricing(model) {
  if (!model) return null;
  if (PRICING[model]) return PRICING[model];
  for (const key of Object.keys(PRICING)) {
    if (model.startsWith(key)) return PRICING[key];
  }
  const m = model.toLowerCase();
  if (m.includes('fable'))  return PRICING['claude-fable-5'];
  if (m.includes('mythos')) return PRICING['claude-mythos-5'];
  if (m.includes('opus'))   return PRICING['claude-opus-5'];
  if (m.includes('sonnet')) return PRICING['claude-sonnet-5'];
  if (m.includes('haiku'))  return PRICING['claude-haiku-4-5'];
  return null;
}

function calcCost(model, inp, out, cacheRead, cacheCreation) {
  if (!isBillable(model)) return 0;
  const p = getPricing(model);
  if (!p) return 0;
  return (
    inp           * p.input       / 1e6 +
    out           * p.output      / 1e6 +
    cacheRead     * p.cache_read  / 1e6 +
    cacheCreation * p.cache_write / 1e6
  );
}

// ── Formatting ─────────────────────────────────────────────────────────────
function fmt(n) {
  if (n >= 1e9) return (n/1e9).toFixed(2)+'B';
  if (n >= 1e6) return (n/1e6).toFixed(2)+'M';
  if (n >= 1e3) return (n/1e3).toFixed(1)+'K';
  return n.toLocaleString();
}
function fmtCost(c)    { return '$' + c.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 4 }); }
function fmtCostBig(c) { return '$' + c.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

// ── Chart colors ───────────────────────────────────────────────────────────
// HotFix Ops palette kept in sync with the CSS :root variables. Chart
// legends/axes use C.axis (a touch lighter than --muted for dense telemetry);
// grid uses C.border.
const C = {
  text:   '#F5F5F7',
  muted:  '#9AA8B6',
  axis:   '#B6C2CE',
  border: '#3A4755',
  card:   '#18212B',
  blue:   '#72B9D6',
  green:  '#8CD2B3',
  red:    '#FF7D67',
  accent: '#E8611B',
  amber:  '#F0B45B',
  purple: '#B69ADD',
  teal:   '#66C6B6',
  mauve:  '#D98EA4',
};
const TOKEN_COLORS = {
  input:          'rgba(114,185,214,0.85)',  // telemetry blue
  output:         'rgba(232,97,27,0.85)',    // Come In Hot
  cache_read:     'rgba(140,210,179,0.75)',  // clear green
  cache_creation: 'rgba(240,180,91,0.75)',   // amber
};
// Hover lifts on a dark theme: bars/series go to full opacity (a touch brighter).
const TOKEN_HOVER = {
  input:          'rgba(114,185,214,1)',
  output:         'rgba(232,97,27,1)',
  cache_read:     'rgba(140,210,179,1)',
  cache_creation: 'rgba(240,180,91,1)',
};
// Donut / categorical palette — warm, Anthropic-leaning (clay, tan, sage, dusty
// blue, mauve, ochre, taupe, terracotta) rather than a saturated rainbow.
const MODEL_COLORS = ['#E8611B','#72B9D6','#8CD2B3','#F0B45B','#B69ADD','#66C6B6','#D98EA4','#6B7B8D'];

// Tooltip color swatches: solid fill, no border (Chart.js's default draws a
// bordered box that looked offset/inconsistent). Lines use their solid stroke
// color instead of the translucent area fill.
Chart.defaults.color = C.axis;
// multiKeyBackground defaults to white and is drawn behind each tooltip swatch,
// peeking out as a thin white border on plain-box charts — make it transparent.
Chart.defaults.plugins.tooltip.multiKeyBackground = 'transparent';
Chart.defaults.plugins.tooltip.callbacks.labelColor = (ctx) => {
  const ds = ctx.dataset || {};
  let col = Array.isArray(ds.backgroundColor) ? ds.backgroundColor[ctx.dataIndex] : ds.backgroundColor;
  if (ds.type === 'line') col = ds.borderColor;
  return { borderColor: col, backgroundColor: col, borderWidth: 0 };
};

// Legend visibility must survive repaints (filter changes, auto-refresh, sort) —
// the charts are destroyed and rebuilt each render, which otherwise resets any
// series the user toggled off. We track hidden series by label per chart and
// reapply on rebuild: dataset charts via `dataset.hidden`, the doughnut via
// per-slice data visibility (see applyModelHidden).
const hiddenSeries = { daily: new Set(), hourly: new Set(), project: new Set(), model: new Set() };
function legendToggle(key) {
  return (e, item, legend) => {
    const ci = legend.chart;
    const ds = ci.data.datasets[item.datasetIndex];
    ds.hidden = !ds.hidden;
    if (ds.hidden) hiddenSeries[key].add(ds.label); else hiddenSeries[key].delete(ds.label);
    ci.update();
  };
}

// ── Time range ─────────────────────────────────────────────────────────────
const RANGE_LABELS = { 'today': 'Today', 'week': 'This Week', 'month': 'This Month', 'prev-month': 'Previous Month', '7d': 'Last 7 Days', '30d': 'Last 30 Days', '90d': 'Last 90 Days', 'all': 'All Time' };
const RANGE_TICKS  = { 'today': 1, 'week': 7, 'month': 15, 'prev-month': 15, '7d': 7, '30d': 15, '90d': 13, 'all': 12 };
const VALID_RANGES = Object.keys(RANGE_LABELS);

function rangeIncludesToday(range) {
  if (range === 'all') return true;
  const { start, end } = getRangeBounds(range);
  const today = new Date().toISOString().slice(0, 10);
  if (start && today < start) return false;
  if (end && today > end) return false;
  return true;
}

function getRangeBounds(range) {
  if (range === 'all') return { start: null, end: null };
  const today = new Date();
  const iso = d => d.toISOString().slice(0, 10);
  if (range === 'today') {
    const t = iso(today);
    return { start: t, end: t };
  }
  if (range === 'week') {
    const day = today.getDay();
    const diffToMon = day === 0 ? 6 : day - 1;
    const mon = new Date(today); mon.setDate(today.getDate() - diffToMon);
    const sun = new Date(mon); sun.setDate(mon.getDate() + 6);
    return { start: iso(mon), end: iso(sun) };
  }
  if (range === 'month') {
    const start = new Date(today.getFullYear(), today.getMonth(), 1);
    const end = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    return { start: iso(start), end: iso(end) };
  }
  if (range === 'prev-month') {
    const start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    const end = new Date(today.getFullYear(), today.getMonth(), 0);
    return { start: iso(start), end: iso(end) };
  }
  const days = range === '7d' ? 7 : range === '30d' ? 30 : 90;
  const d = new Date();
  d.setDate(d.getDate() - days);
  return { start: iso(d), end: null };
}

function readURLRange() {
  const p = new URLSearchParams(window.location.search).get('range');
  return VALID_RANGES.includes(p) ? p : '30d';
}

function setRange(range) {
  selectedRange = range;
  document.querySelectorAll('.range-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.range === range)
  );
  updateURL();
  applyFilter();
  scheduleAutoRefresh();
}

function setHourlyTZ(mode) {
  hourlyTZ = mode;
  document.querySelectorAll('.tz-btn').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.tz === mode)
  );
  applyFilter();
}

// ── Model filter ───────────────────────────────────────────────────────────
function modelPriority(m) {
  const ml = m.toLowerCase();
  if (ml.includes('opus'))   return 0;
  if (ml.includes('sonnet')) return 1;
  if (ml.includes('haiku'))  return 2;
  return 3;
}

function readURLModels(allModels) {
  const param = new URLSearchParams(window.location.search).get('models');
  if (!param) {
    const billable = allModels.filter(m => isBillable(m));
    // Fallback: if the user only has non-billable / unknown models (e.g. all
    // local-LLM runs), default to all models so the dashboard isn't blank.
    return new Set(billable.length ? billable : allModels);
  }
  const fromURL = new Set(param.split(',').map(s => s.trim()).filter(Boolean));
  return new Set(allModels.filter(m => fromURL.has(m)));
}

function isDefaultModelSelection(allModels) {
  const billable = allModels.filter(m => isBillable(m));
  const expected = billable.length ? billable : allModels;
  if (selectedModels.size !== expected.length) return false;
  return expected.every(m => selectedModels.has(m));
}

function buildFilterUI(
  allModels,
  restoreURL = true,
  preservedModels = null,
  selectEveryModel = false
) {
  const sorted = [...allModels].sort((a, b) => {
    const pa = modelPriority(a), pb = modelPriority(b);
    return pa !== pb ? pa - pb : a.localeCompare(b);
  });
  selectedModels = selectEveryModel
    ? new Set(allModels)
    : (preservedModels
      ? new Set(allModels.filter(model => preservedModels.has(model)))
      : (restoreURL ? readURLModels(allModels) : new Set(allModels)));
  const container = document.getElementById('model-checkboxes');
  container.innerHTML = sorted.map(m => {
    const checked = selectedModels.has(m);
    return `<label class="model-cb-label ${checked ? 'checked' : ''}" data-model="${esc(m)}">
      <input type="checkbox" value="${esc(m)}" ${checked ? 'checked' : ''} onchange="onModelToggle(this)">
      ${esc(m)}
    </label>`;
  }).join('');
}

function onModelToggle(cb) {
  const label = cb.closest('label');
  if (cb.checked) { selectedModels.add(cb.value);    label.classList.add('checked'); }
  else            { selectedModels.delete(cb.value); label.classList.remove('checked'); }
  updateURL();
  applyFilter();
}

function selectAllModels() {
  document.querySelectorAll('#model-checkboxes input').forEach(cb => {
    cb.checked = true; selectedModels.add(cb.value); cb.closest('label').classList.add('checked');
  });
  updateURL(); applyFilter();
}

function clearAllModels() {
  document.querySelectorAll('#model-checkboxes input').forEach(cb => {
    cb.checked = false; selectedModels.delete(cb.value); cb.closest('label').classList.remove('checked');
  });
  updateURL(); applyFilter();
}

// ── URL persistence ────────────────────────────────────────────────────────
function updateURL() {
  const allModels = Array.from(document.querySelectorAll('#model-checkboxes input')).map(cb => cb.value);
  const params = new URLSearchParams();
  if (selectedRange !== '30d') params.set('range', selectedRange);
  if (!isDefaultModelSelection(allModels)) params.set('models', Array.from(selectedModels).join(','));
  const search = params.toString() ? '?' + params.toString() : '';
  history.replaceState(null, '', window.location.pathname + search);
}

// ── Session sort ───────────────────────────────────────────────────────────
function setSessionSort(col) {
  if (sessionSortCol === col) {
    sessionSortDir = sessionSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    sessionSortCol = col;
    sessionSortDir = 'desc';
  }
  updateSortIcons();
  applyFilter();
}

function updateSortIcons() {
  document.querySelectorAll('.sort-icon').forEach(el => el.textContent = '');
  const icon = document.getElementById('sort-icon-' + sessionSortCol);
  if (icon) icon.textContent = sessionSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortSessions(sessions) {
  return [...sessions].sort((a, b) => {
    let av, bv;
    if (sessionSortCol === 'cost') {
      av = calcCost(a.model, a.input, a.output, a.cache_read, a.cache_creation);
      bv = calcCost(b.model, b.input, b.output, b.cache_read, b.cache_creation);
    } else if (sessionSortCol === 'duration_min') {
      av = parseFloat(a.duration_min) || 0;
      bv = parseFloat(b.duration_min) || 0;
    } else {
      av = a[sessionSortCol] ?? 0;
      bv = b[sessionSortCol] ?? 0;
    }
    if (av < bv) return sessionSortDir === 'desc' ? 1 : -1;
    if (av > bv) return sessionSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

// ── Aggregation & filtering ────────────────────────────────────────────────
function applyFilter() {
  if (!rawData) return;

  const { start, end } = getRangeBounds(selectedRange);

  // Filter daily rows by model + date range
  const filteredDaily = rawData.daily_by_model.filter(r =>
    selectedModels.has(r.model) && (!start || r.day >= start) && (!end || r.day <= end)
  );

  // Daily chart: aggregate by day
  const dailyMap = {};
  for (const r of filteredDaily) {
    if (!dailyMap[r.day]) dailyMap[r.day] = { day: r.day, input: 0, output: 0, cache_read: 0, cache_creation: 0 };
    const d = dailyMap[r.day];
    d.input          += r.input;
    d.output         += r.output;
    d.cache_read     += r.cache_read;
    d.cache_creation += r.cache_creation;
  }
  const daily = Object.values(dailyMap).sort((a, b) => a.day.localeCompare(b.day));

  // By model: aggregate tokens + turns from daily data
  const modelMap = {};
  for (const r of filteredDaily) {
    if (!modelMap[r.model]) modelMap[r.model] = { model: r.model, input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, sessions: 0 };
    const m = modelMap[r.model];
    m.input          += r.input;
    m.output         += r.output;
    m.cache_read     += r.cache_read;
    m.cache_creation += r.cache_creation;
    m.turns          += r.turns;
  }

  // Filter sessions by model + date range
  const filteredSessions = rawData.sessions_all.filter(s =>
    selectedModels.has(s.model) && (!start || s.last_date >= start) && (!end || s.last_date <= end)
  );

  // Add session counts into modelMap
  for (const s of filteredSessions) {
    if (modelMap[s.model]) modelMap[s.model].sessions++;
  }

  const byModel = Object.values(modelMap).sort((a, b) => (b.input + b.output) - (a.input + a.output));

  // By project: aggregate from filtered sessions
  const projMap = {};
  for (const s of filteredSessions) {
    if (!projMap[s.project]) projMap[s.project] = { project: s.project, input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, sessions: 0, cost: 0 };
    const p = projMap[s.project];
    p.input          += s.input;
    p.output         += s.output;
    p.cache_read     += s.cache_read;
    p.cache_creation += s.cache_creation;
    p.turns          += s.turns;
    p.sessions++;
    p.cost += calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
  }
  const byProject = Object.values(projMap).sort((a, b) => (b.input + b.output) - (a.input + a.output));

  // By project+branch: aggregate from filtered sessions
  const projBranchMap = {};
  for (const s of filteredSessions) {
    const key = s.project + '\x00' + (s.branch || '');
    if (!projBranchMap[key]) projBranchMap[key] = { project: s.project, branch: s.branch || '', input: 0, output: 0, cache_read: 0, cache_creation: 0, turns: 0, sessions: 0, cost: 0 };
    const pb = projBranchMap[key];
    pb.input          += s.input;
    pb.output         += s.output;
    pb.cache_read     += s.cache_read;
    pb.cache_creation += s.cache_creation;
    pb.turns          += s.turns;
    pb.sessions++;
    pb.cost += calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
  }
  const byProjectBranch = Object.values(projBranchMap).sort((a, b) => b.cost - a.cost);

  // Totals
  const totals = {
    sessions:       filteredSessions.length,
    turns:          byModel.reduce((s, m) => s + m.turns, 0),
    input:          byModel.reduce((s, m) => s + m.input, 0),
    output:         byModel.reduce((s, m) => s + m.output, 0),
    cache_read:     byModel.reduce((s, m) => s + m.cache_read, 0),
    cache_creation: byModel.reduce((s, m) => s + m.cache_creation, 0),
    cost:           byModel.reduce((s, m) => s + calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation), 0),
  };

  // Hourly aggregation (filtered by model + range, then bucketed by UTC hour)
  const hourlySrc = (rawData.hourly_by_model || []).filter(r =>
    selectedModels.has(r.model) && (!start || r.day >= start) && (!end || r.day <= end)
  );
  const hourlyAgg = aggregateHourly(hourlySrc, hourlyTZ);

  // Update daily chart title
  document.getElementById('daily-chart-title').textContent = 'Daily Token Usage \u2014 ' + RANGE_LABELS[selectedRange];
  document.getElementById('hourly-chart-title').textContent = 'Average Hourly Distribution \u2014 ' + RANGE_LABELS[selectedRange];

  renderStats(totals);
  renderDailyChart(daily);
  renderHourlyChart(hourlyAgg);
  renderModelChart(byModel);
  renderProjectChart(byProject);
  lastFilteredSessions = sortSessions(filteredSessions);
  lastByModel = byModel;
  lastByProject = sortProjects(byProject);
  lastByProjectBranch = sortProjectBranch(byProjectBranch);
  renderSessionsTable(lastFilteredSessions);
  renderModelCostTable(lastByModel);
  renderProjectCostTable(lastByProject);
  renderProjectBranchCostTable(lastByProjectBranch);
}

// ── Renderers ──────────────────────────────────────────────────────────────
function renderStats(t) {
  const rangeLabel = RANGE_LABELS[selectedRange].toLowerCase();
  const cacheCreationLabel = activeProvider === 'codex' ? 'Reasoning' : 'Cache Creation';
  const cacheCreationSub = activeProvider === 'codex' ? 'included in output' : 'writes to prompt cache';
  const stats = [
    { label: 'Sessions',       value: t.sessions.toLocaleString(), sub: rangeLabel },
    { label: 'Turns',          value: fmt(t.turns),                sub: rangeLabel },
    { label: 'Fresh Input',    value: fmt(t.input),                sub: rangeLabel + ' · cache excluded' },
    { label: 'Output Tokens',  value: fmt(t.output),               sub: rangeLabel },
    { label: 'Cache Read',     value: fmt(t.cache_read),           sub: 'from prompt cache' },
    { label: cacheCreationLabel, value: fmt(t.cache_creation),     sub: cacheCreationSub },
  ];
  if (activeProvider === 'claude') {
    stats.push({ label: 'Est. Cost', value: fmtCostBig(t.cost), sub: 'API-equivalent estimate', color: C.green });
  }
  document.getElementById('stats-row').innerHTML = stats.map(s => `
    <div class="stat-card">
      <div class="label">${s.label}</div>
      <div class="value" style="${s.color ? 'color:' + s.color : ''}">${esc(s.value)}</div>
      ${s.sub ? `<div class="sub">${esc(s.sub)}</div>` : ''}
    </div>
  `).join('');
}

// Bucket rows into 24 hours (display-TZ), summing turns + output, and count
// the unique days in the input so the caller can compute per-day averages.
function aggregateHourly(rows, tzMode) {
  const byHour = {};
  for (let h = 0; h < 24; h++) byHour[h] = { turns: 0, output: 0 };
  const days = new Set();
  for (const r of rows) {
    const displayHour = utcHourToDisplay(r.hour, tzMode);
    byHour[displayHour].turns  += r.turns  || 0;
    byHour[displayHour].output += r.output || 0;
    if (r.day) days.add(r.day);
  }
  const dayCount = days.size;
  const hours = [];
  for (let h = 0; h < 24; h++) {
    hours.push({
      hour:       h,
      avgTurns:   dayCount ? byHour[h].turns  / dayCount : 0,
      avgOutput:  dayCount ? byHour[h].output / dayCount : 0,
      totalTurns: byHour[h].turns,
      peak:       isPeakHour(h, tzMode),
    });
  }
  return { hours, dayCount };
}

function renderHourlyChart(agg) {
  const dayCountEl = document.getElementById('hourly-day-count');
  dayCountEl.textContent = agg.dayCount
    ? agg.dayCount + ' day' + (agg.dayCount === 1 ? '' : 's') + ' averaged · ' + tzDisplayName(hourlyTZ)
    : 'No data · ' + tzDisplayName(hourlyTZ);

  const ctx = document.getElementById('chart-hourly').getContext('2d');
  if (charts.hourly) charts.hourly.destroy();

  const labels = agg.hours.map(h => formatHourLabel(h.hour));
  const turns  = agg.hours.map(h => h.avgTurns);
  const output = agg.hours.map(h => h.avgOutput);
  const barColors      = agg.hours.map(h => h.peak ? 'rgba(199,78,57,0.9)' : TOKEN_COLORS.input);
  const barHoverColors = agg.hours.map(h => h.peak ? 'rgba(199,78,57,1)'   : TOKEN_HOVER.input);

  charts.hourly = new Chart(ctx, {
    data: {
      labels: labels,
      datasets: [
        {
          type: 'bar',
          label: 'Avg turns / hour',
          hidden: hiddenSeries.hourly.has('Avg turns / hour'),
          data: turns,
          backgroundColor: barColors,
          hoverBackgroundColor: barHoverColors,
          pointStyle: 'rect',
          yAxisID: 'y',
          order: 2,
        },
        {
          type: 'line',
          label: 'Avg output tokens / hour',
          hidden: hiddenSeries.hourly.has('Avg output tokens / hour'),
          data: output,
          borderColor: TOKEN_COLORS.output,
          backgroundColor: 'rgba(217,119,87,0.15)',
          borderWidth: 2,
          pointRadius: 2,
          pointHoverRadius: 4,
          pointHoverBackgroundColor: TOKEN_HOVER.output,
          pointStyle: 'circle',
          pointBackgroundColor: TOKEN_COLORS.output,
          pointBorderColor: TOKEN_COLORS.output,
          tension: 0.3,
          yAxisID: 'y1',
          order: 1,
        },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false, resizeDelay: 150,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { onClick: legendToggle('hourly'), labels: { color: C.axis, usePointStyle: true, boxWidth: 8, boxHeight: 8 } },
        tooltip: {
          usePointStyle: true,
          callbacks: {
            title: (items) => {
              if (!items.length) return '';
              const idx = items[0].dataIndex;
              const h = agg.hours[idx];
              const base = formatHourLabel(h.hour) + ' ' + tzDisplayName(hourlyTZ);
              return h.peak ? base + ' · Peak — Anthropic US hours' : base;
            },
            label: (item) => {
              if (item.dataset.label && item.dataset.label.indexOf('turns') !== -1) {
                return ' Avg turns: ' + item.parsed.y.toFixed(2);
              }
              return ' Avg output: ' + fmt(item.parsed.y);
            },
          }
        },
      },
      scales: {
        x: { ticks: { color: C.axis, maxRotation: 0, autoSkip: false, font: { size: 10 } }, grid: { color: C.border } },
        y:  { position: 'left',  beginAtZero: true, ticks: { color: C.axis, callback: v => v.toFixed(1) },     grid: { color: C.border }, title: { display: true, text: 'Avg turns / hour',         color: C.axis, font: { size: 11 } } },
        y1: { position: 'right', beginAtZero: true, ticks: { color: C.axis, callback: v => fmt(v) }, grid: { drawOnChartArea: false },   title: { display: true, text: 'Avg output tokens / hour', color: C.axis, font: { size: 11 } } },
      }
    }
  });
}

function renderDailyChart(daily) {
  const ctx = document.getElementById('chart-daily').getContext('2d');
  if (charts.daily) charts.daily.destroy();
  charts.daily = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: daily.map(d => d.day),
      datasets: [
        { label: 'Fresh Input',    hidden: hiddenSeries.daily.has('Fresh Input'),    data: daily.map(d => d.input),          backgroundColor: TOKEN_COLORS.input,          hoverBackgroundColor: TOKEN_HOVER.input,          minBarLength: 2, stack: 'io', yAxisID: 'y1' },
        { label: 'Output',         hidden: hiddenSeries.daily.has('Output'),         data: daily.map(d => d.output),         backgroundColor: TOKEN_COLORS.output,         hoverBackgroundColor: TOKEN_HOVER.output,         stack: 'io',    yAxisID: 'y1' },
        { label: 'Cache Read',     hidden: hiddenSeries.daily.has('Cache Read'),     data: daily.map(d => d.cache_read),     backgroundColor: TOKEN_COLORS.cache_read,     hoverBackgroundColor: TOKEN_HOVER.cache_read,     stack: 'cache', yAxisID: 'y' },
        { label: activeProvider === 'codex' ? 'Reasoning' : 'Cache Creation', hidden: hiddenSeries.daily.has(activeProvider === 'codex' ? 'Reasoning' : 'Cache Creation'), data: daily.map(d => d.cache_creation), backgroundColor: TOKEN_COLORS.cache_creation, hoverBackgroundColor: TOKEN_HOVER.cache_creation, stack: 'cache', yAxisID: 'y' },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false, resizeDelay: 150,
      plugins: { legend: { onClick: legendToggle('daily'), labels: { color: C.axis, boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: C.axis, maxTicksLimit: RANGE_TICKS[selectedRange] }, grid: { color: C.border } },
        y:  { position: 'left',  ticks: { color: C.green, callback: v => fmt(v) }, grid: { color: C.border }, title: { display: true, text: activeProvider === 'codex' ? 'Cache / Reasoning' : 'Cache', color: C.green } },
        y1: { position: 'right', ticks: { color: C.blue, callback: v => fmt(v) }, grid: { drawOnChartArea: false },    title: { display: true, text: 'Fresh Input / Output', color: C.blue } },
      }
    }
  });
}

function renderModelChart(byModel) {
  const ctx = document.getElementById('chart-model').getContext('2d');
  if (charts.model) charts.model.destroy();
  if (!byModel.length) { charts.model = null; return; }
  charts.model = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: byModel.map(m => m.model),
      datasets: [{ data: byModel.map(m => m.input + m.output), backgroundColor: MODEL_COLORS, hoverBackgroundColor: MODEL_COLORS, hoverOffset: 8, borderWidth: 2, borderColor: C.card, hoverBorderColor: C.card }]
    },
    options: {
      responsive: true, maintainAspectRatio: false, resizeDelay: 150,
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: C.axis, boxWidth: 12, font: { size: 11 } },
          onClick: (e, item, legend) => {
            const ci = legend.chart;
            ci.toggleDataVisibility(item.index);
            const label = ci.data.labels[item.index];
            if (!ci.getDataVisibility(item.index)) hiddenSeries.model.add(label); else hiddenSeries.model.delete(label);
            ci.update();
          },
        },
        tooltip: { callbacks: { label: ctx => ` ${ctx.label}: ${fmt(ctx.raw)} tokens` } }
      }
    }
  });
  // Reapply any slices the user toggled off in a previous render.
  byModel.forEach((m, i) => {
    if (hiddenSeries.model.has(m.model) && charts.model.getDataVisibility(i)) charts.model.toggleDataVisibility(i);
  });
  charts.model.update();
}

function renderProjectChart(byProject) {
  const top = byProject.slice(0, 10);
  const ctx = document.getElementById('chart-project').getContext('2d');
  if (charts.project) charts.project.destroy();
  if (!top.length) { charts.project = null; return; }
  charts.project = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: top.map(p => p.project.length > 22 ? '\u2026' + p.project.slice(-20) : p.project),
      datasets: [
        { label: 'Fresh Input', hidden: hiddenSeries.project.has('Fresh Input'), data: top.map(p => p.input), backgroundColor: TOKEN_COLORS.input, hoverBackgroundColor: TOKEN_HOVER.input, minBarLength: 2 },
        { label: 'Output', hidden: hiddenSeries.project.has('Output'), data: top.map(p => p.output), backgroundColor: TOKEN_COLORS.output, hoverBackgroundColor: TOKEN_HOVER.output },
      ]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false, resizeDelay: 150,
      plugins: { legend: { onClick: legendToggle('project'), labels: { color: C.axis, boxWidth: 12 } } },
      scales: {
        x: { ticks: { color: C.axis, callback: v => fmt(v) }, grid: { color: C.border } },
        y: { ticks: { color: C.axis, font: { size: 11 } }, grid: { color: C.border } },
      }
    }
  });
}

// Fills a table card's footer with the row-reveal control. Three states:
//   - more rows fit under the cap        -> "Show more" (plus "Show less" once expanded)
//   - cap reached but more records exist -> "Download CSV to see all (N)" + "Show less"
//   - every row is already visible       -> "Show less"
// "Show less" is hidden at the initial step (nothing to collapse yet). Renders
// nothing when the whole table fits in the first step. Carets: more = down (▾),
// less = up (▴).
function renderTableToggle(footId, total, limit, lessName, moreName, csvName) {
  const foot = document.getElementById(footId);
  if (!foot) return;
  if (total <= TABLE_STEPS[0]) { foot.innerHTML = ''; return; }
  const less = '<button class="show-more-btn" onclick="' + lessName + '()">Show less ▴</button>';
  const more = '<button class="show-more-btn" onclick="' + moreName + '()">Show more ▾</button>';
  let html;
  if (limit < total && limit < TABLE_MAX) {
    // more rows fit under the cap; Show less only once we're past the first step
    html = (limit > TABLE_STEPS[0] ? less : '') + more;
  } else if (limit < total) {           // cap reached, remaining rows only via CSV
    html = '<a class="show-more-link" href="#" onclick="' + csvName + '(); return false;">Download CSV to see all (' + total + ')</a>' + less;
  } else {                              // everything already visible
    html = less;
  }
  foot.innerHTML = html;
}

// After collapsing a table, bring its top back into view — the user may have
// scrolled down through the expanded rows.
function scrollTableToTop(bodyId) {
  const card = document.getElementById(bodyId)?.closest('.table-card');
  if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// "Show more" advances one step (capped at TABLE_MAX); "Show less" resets to 10
// and scrolls back to the top of that table.
function moreModelRows()   { modelLimit    = nextTableLimit(modelLimit,    lastByModel.length);        renderModelCostTable(lastByModel); }
function lessModelRows()   { modelLimit    = TABLE_STEPS[0]; renderModelCostTable(lastByModel);            scrollTableToTop('model-cost-body'); }
function moreSessionRows() { sessionsLimit = nextTableLimit(sessionsLimit, lastFilteredSessions.length); renderSessionsTable(lastFilteredSessions); }
function lessSessionRows() { sessionsLimit = TABLE_STEPS[0]; renderSessionsTable(lastFilteredSessions);    scrollTableToTop('sessions-body'); }
function moreProjectRows() { projectLimit  = nextTableLimit(projectLimit,  lastByProject.length);       renderProjectCostTable(lastByProject); }
function lessProjectRows() { projectLimit  = TABLE_STEPS[0]; renderProjectCostTable(lastByProject);        scrollTableToTop('project-cost-body'); }
function moreBranchRows()  { branchLimit   = nextTableLimit(branchLimit,   lastByProjectBranch.length); renderProjectBranchCostTable(lastByProjectBranch); }
function lessBranchRows()  { branchLimit   = TABLE_STEPS[0]; renderProjectBranchCostTable(lastByProjectBranch); scrollTableToTop('project-branch-cost-body'); }

function renderSessionsTable(sessions) {
  const shown = sessions.slice(0, sessionsLimit);
  document.getElementById('sessions-body').innerHTML = shown.map(s => {
    const cost = calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
    const costCell = isBillable(s.model)
      ? `<td class="cost">${fmtCost(cost)}</td>`
      : `<td class="cost-na">n/a</td>`;
    return `<tr>
      <td class="muted" style="font-family:monospace">${esc(s.session_id)}&hellip;</td>
      <td>${esc(s.project)}</td>
      <td class="muted">${esc(s.last)}</td>
      <td class="muted">${esc(s.duration_min)}m</td>
      <td><span class="model-tag">${esc(s.model)}</span></td>
      <td class="num">${s.turns}</td>
      <td class="num">${fmt(s.input)}</td>
      <td class="num">${fmt(s.output)}</td>
      ${costCell}
    </tr>`;
  }).join('');
  renderTableToggle('sessions-foot', sessions.length, sessionsLimit, 'lessSessionRows', 'moreSessionRows', 'exportSessionsCSV');
}

function setModelSort(col) {
  if (modelSortCol === col) {
    modelSortDir = modelSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    modelSortCol = col;
    modelSortDir = 'desc';
  }
  updateModelSortIcons();
  applyFilter();
}

function updateModelSortIcons() {
  document.querySelectorAll('[id^="msort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('msort-' + modelSortCol);
  if (icon) icon.textContent = modelSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortModels(byModel) {
  return [...byModel].sort((a, b) => {
    let av, bv;
    if (modelSortCol === 'cost') {
      av = calcCost(a.model, a.input, a.output, a.cache_read, a.cache_creation);
      bv = calcCost(b.model, b.input, b.output, b.cache_read, b.cache_creation);
    } else {
      av = a[modelSortCol] ?? 0;
      bv = b[modelSortCol] ?? 0;
    }
    if (av < bv) return modelSortDir === 'desc' ? 1 : -1;
    if (av > bv) return modelSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderModelCostTable(byModel) {
  const sorted = sortModels(byModel);
  const shown = sorted.slice(0, modelLimit);
  document.getElementById('model-cost-body').innerHTML = shown.map(m => {
    const cost = calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation);
    const costCell = isBillable(m.model)
      ? `<td class="cost">${fmtCost(cost)}</td>`
      : `<td class="cost-na">n/a</td>`;
    return `<tr>
      <td><span class="model-tag">${esc(m.model)}</span></td>
      <td class="num">${fmt(m.turns)}</td>
      <td class="num">${fmt(m.input)}</td>
      <td class="num">${fmt(m.output)}</td>
      <td class="num">${fmt(m.cache_read)}</td>
      <td class="num">${fmt(m.cache_creation)}</td>
      ${costCell}
    </tr>`;
  }).join('');
  renderTableToggle('model-cost-foot', sorted.length, modelLimit, 'lessModelRows', 'moreModelRows', 'exportModelCSV');
}

// ── Project cost table sorting ────────────────────────────────────────────
function setProjectSort(col) {
  if (projectSortCol === col) {
    projectSortDir = projectSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    projectSortCol = col;
    projectSortDir = 'desc';
  }
  updateProjectSortIcons();
  applyFilter();
}

function updateProjectSortIcons() {
  document.querySelectorAll('[id^="psort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('psort-' + projectSortCol);
  if (icon) icon.textContent = projectSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortProjects(byProject) {
  return [...byProject].sort((a, b) => {
    const av = a[projectSortCol] ?? 0;
    const bv = b[projectSortCol] ?? 0;
    if (av < bv) return projectSortDir === 'desc' ? 1 : -1;
    if (av > bv) return projectSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderProjectCostTable(byProject) {
  const sorted = sortProjects(byProject);
  const shown = sorted.slice(0, projectLimit);
  document.getElementById('project-cost-body').innerHTML = shown.map(p => {
    return `<tr>
      <td>${esc(p.project)}</td>
      <td class="num">${p.sessions}</td>
      <td class="num">${fmt(p.turns)}</td>
      <td class="num">${fmt(p.input)}</td>
      <td class="num">${fmt(p.output)}</td>
      <td class="cost">${fmtCost(p.cost)}</td>
    </tr>`;
  }).join('');
  renderTableToggle('project-cost-foot', sorted.length, projectLimit, 'lessProjectRows', 'moreProjectRows', 'exportProjectsCSV');
}

// ── Project+Branch cost table sorting ────────────────────────────────────
function setProjectBranchSort(col) {
  if (branchSortCol === col) {
    branchSortDir = branchSortDir === 'desc' ? 'asc' : 'desc';
  } else {
    branchSortCol = col;
    branchSortDir = 'desc';
  }
  updateProjectBranchSortIcons();
  applyFilter();
}

function updateProjectBranchSortIcons() {
  document.querySelectorAll('[id^="pbsort-"]').forEach(el => el.textContent = '');
  const icon = document.getElementById('pbsort-' + branchSortCol);
  if (icon) icon.textContent = branchSortDir === 'desc' ? ' \u25bc' : ' \u25b2';
}

function sortProjectBranch(rows) {
  return [...rows].sort((a, b) => {
    const pa = (a.project || '').toLowerCase();
    const pb = (b.project || '').toLowerCase();
    if (pa < pb) return -1;
    if (pa > pb) return 1;
    const av = a[branchSortCol] ?? 0;
    const bv = b[branchSortCol] ?? 0;
    if (av < bv) return branchSortDir === 'desc' ? 1 : -1;
    if (av > bv) return branchSortDir === 'desc' ? -1 : 1;
    return 0;
  });
}

function renderProjectBranchCostTable(rows) {
  const sorted = sortProjectBranch(rows);
  const shown = sorted.slice(0, branchLimit);
  document.getElementById('project-branch-cost-body').innerHTML = shown.map(pb => {
    return `<tr>
      <td>${esc(pb.project)}</td>
      <td class="muted" style="font-family:monospace">${esc(pb.branch || '\u2014')}</td>
      <td class="num">${pb.sessions}</td>
      <td class="num">${fmt(pb.turns)}</td>
      <td class="num">${fmt(pb.input)}</td>
      <td class="num">${fmt(pb.output)}</td>
      <td class="cost">${fmtCost(pb.cost)}</td>
    </tr>`;
  }).join('');
  renderTableToggle('project-branch-cost-foot', sorted.length, branchLimit, 'lessBranchRows', 'moreBranchRows', 'exportProjectBranchCSV');
}

// ── CSV Export ────────────────────────────────────────────────────────────
function csvField(val) {
  const s = String(val);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

function csvTimestamp() {
  const d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0')
    + '_' + String(d.getHours()).padStart(2,'0') + String(d.getMinutes()).padStart(2,'0');
}

function downloadCSV(reportType, header, rows) {
  const lines = [header.map(csvField).join(',')];
  for (const row of rows) {
    lines.push(row.map(csvField).join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = reportType + '_' + csvTimestamp() + '.csv';
  a.click();
  URL.revokeObjectURL(a.href);
}

function exportModelCSV() {
  const header = ['Model', 'Turns', 'Fresh Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = sortModels(lastByModel).map(m => {
    const cost = calcCost(m.model, m.input, m.output, m.cache_read, m.cache_creation);
    return [m.model, m.turns, m.input, m.output, m.cache_read, m.cache_creation, cost.toFixed(4)];
  });
  downloadCSV('cost_by_model', header, rows);
}

function exportSessionsCSV() {
  const header = ['Session', 'Project', 'Last Active', 'Duration (min)', 'Model', 'Turns', 'Fresh Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastFilteredSessions.map(s => {
    const cost = calcCost(s.model, s.input, s.output, s.cache_read, s.cache_creation);
    return [s.session_id, s.project, s.last, s.duration_min, s.model, s.turns, s.input, s.output, s.cache_read, s.cache_creation, cost.toFixed(4)];
  });
  downloadCSV('sessions', header, rows);
}

function exportProjectsCSV() {
  const header = ['Project', 'Sessions', 'Turns', 'Fresh Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastByProject.map(p => {
    return [p.project, p.sessions, p.turns, p.input, p.output, p.cache_read, p.cache_creation, p.cost.toFixed(4)];
  });
  downloadCSV('projects', header, rows);
}

function exportProjectBranchCSV() {
  const header = ['Project', 'Branch', 'Sessions', 'Turns', 'Fresh Input', 'Output', 'Cache Read', 'Cache Creation', 'Est. Cost'];
  const rows = lastByProjectBranch.map(pb => {
    return [pb.project, pb.branch, pb.sessions, pb.turns, pb.input, pb.output, pb.cache_read, pb.cache_creation, pb.cost.toFixed(4)];
  });
  downloadCSV('projects_by_branch', header, rows);
}

// ── Refresh ───────────────────────────────────────────────────────────────
async function triggerRefresh(initial = false) {
  const btn = document.getElementById('rescan-btn');
  btn.disabled = true;
  btn.textContent = '\u21bb Refreshing...';
  try {
    const resp = await fetch('/api/refresh', { method: 'POST' });
    if (!resp.ok) throw new Error('Refresh failed');
    const d = await resp.json();
    const added = (d.claude?.turns || 0) + (d.codex?.turns || 0);
    btn.textContent = '\u21bb Refreshed (' + added + ' turns)';
    await loadData();
  } catch(e) {
    btn.textContent = '\u21bb Refresh error';
    console.error(e);
    if (initial) await loadData();
  }
  setTimeout(() => { btn.textContent = '\u21bb Refresh'; btn.disabled = false; }, 3000);
}

async function resetTestingMode() {
  if (!window.confirm('Reset the isolated first-run preview? This clears only test-mode profiles.')) return;
  const button = document.getElementById('reset-testing-mode');
  button.disabled = true;
  button.textContent = 'Resetting...';
  try {
    const resp = await fetch('/api/testing/reset', { method: 'POST' });
    if (!resp.ok) throw new Error('Testing reset failed');
    await loadData();
  } catch(e) {
    console.error(e);
    button.textContent = 'Reset failed';
  } finally {
    setTimeout(() => {
      button.textContent = 'Reset preview';
      button.disabled = false;
    }, 800);
  }
}

async function seedTestingMode() {
  if (!window.confirm('Replace isolated test-mode profiles with fake sample accounts?')) return;
  const button = document.getElementById('seed-testing-mode');
  button.disabled = true;
  button.textContent = 'Loading...';
  try {
    const resp = await fetch('/api/testing/seed', { method: 'POST' });
    if (!resp.ok) throw new Error('Testing sample load failed');
    await loadData();
  } catch(e) {
    console.error(e);
    button.textContent = 'Load failed';
  } finally {
    setTimeout(() => {
      button.textContent = 'Load sample accounts';
      button.disabled = false;
    }, 800);
  }
}

// ── Data loading ───────────────────────────────────────────────────────────
async function loadData() {
  try {
    const resp = await fetch('/api/overview');
    const d = await resp.json();
    if (d.error) {
      document.body.innerHTML = '<div style="padding:40px;color:#FF7D67">' + esc(d.error) + '</div>';
      return;
    }
    const refreshNote = rangeIncludesToday(selectedRange) ? '<br>Auto-refresh in 30s' : '';
    document.getElementById('meta').innerHTML = 'Updated: ' + esc(d.generated_at) + refreshNote;
    // Freshness banner: surface a stale scan instead of silently charting old data.
    const fb = document.getElementById('freshness-banner');
    const freshness = d.providers?.claude?.history?.freshness;
    if (fb && freshness && freshness.last_scan_epoch) {
      const ageMin = Math.floor((Date.now()/1000 - freshness.last_scan_epoch) / 60);
      const ageTxt = ageMin >= 120 ? Math.floor(ageMin/60) + 'h ' + (ageMin%60) + 'm' : ageMin + 'm';
      const un = freshness.unscanned_files;
      const stale = un > 50 || (ageMin > 120 && un > 0);  // quiet period with nothing unscanned is not stale
      fb.style.display = '';
      fb.style.color = stale ? '#ff6b6b' : 'var(--muted)';
      fb.style.background = stale ? 'rgba(199,78,57,0.12)' : 'transparent';
      fb.textContent = 'Last scan ' + ageTxt + ' ago' +
        (un > 0 ? ' \u00b7 ' + un + ' transcript file' + (un === 1 ? '' : 's') + ' not yet scanned' : '') +
        (stale ? ' \u2014 data below is INCOMPLETE. Hit Rescan or wait for the 30-min scan daemon.' : '');
    } else if (fb) { fb.style.display = 'none'; }

    const firstLoad = dashboardPayload === null;
    dashboardPayload = d;
    renderTestingMode(Boolean(d.test_mode));
    renderOverview(d);
    if (firstLoad) {
      // Restore range from URL, mark active buttons.
      selectedRange = readURLRange();
      document.querySelectorAll('.range-btn').forEach(btn =>
        btn.classList.toggle('active', btn.dataset.range === selectedRange)
      );
      document.querySelectorAll('.tz-btn').forEach(btn =>
        btn.classList.toggle('active', btn.dataset.tz === hourlyTZ)
      );
    }
    if (activeProvider !== 'overview') setProvider(activeProvider);
  } catch(e) {
    console.error(e);
  }
}

// ── Account limit orbs ──────────────────────────────────────────────
let ACCT_DATA = null;

function fmtCountdown(iso) {
  if (!iso) return 'full';   // null resets_at = window untouched, nothing to reset
  const ms = new Date(iso) - Date.now();
  if (isNaN(ms) || ms <= 0) return 'resetting…';
  const h = Math.floor(ms / 3600000), m = Math.floor(ms % 3600000 / 60000);
  return h >= 48 ? `${Math.floor(h/24)}d ${h%24}h` : `${h}h ${String(m).padStart(2,'0')}m`;
}

function fmtAgo(iso) {
  if (!iso) return 'never';
  const s = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (isNaN(s)) return 'never';
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  return Math.floor(s/3600) + 'h ' + Math.floor(s%3600/60) + 'm ago';
}

function orbHtml(w) {
  return `<div class="orbC" style="--c-hi:${w.color_hi};--c-lo:${w.color_lo};--c-glow:${w.color_hi}55">
    <div class="fill" style="height:${w.remaining_pct}%"></div>
    <div class="glints"></div><div class="rim"></div>
    <div class="num">${w.remaining_pct}%</div></div>`;
}

function renderAccounts() {
  if (!ACCT_DATA) return;
  const fetched = document.getElementById('accounts-fetched');
  if (ACCT_DATA.error) {
    fetched.textContent = 'fetch failed: ' + ACCT_DATA.error;
    fetched.style.color = '#ff6b6b';
    return;
  }
  fetched.style.color = '';
  const accts = ACCT_DATA.accounts;
  const bestPct = Math.max(...accts.map(a => a.active !== false && !a.error && a.windows.five_hour ? a.windows.five_hour.remaining_pct : -1));
  document.getElementById('accounts-row').innerHTML = accts.map(a => {
    const cost = `<span title="actual receipts, tax incl">$${(a.lifetime_spend||0).toFixed(2)} lifetime · $${(a.monthly_cost||0).toFixed(0)}/mo</span>`;
    const gauge = (label, w) => w ? `<div class="acct-gauge">${orbHtml(w)}
      <div class="acct-glabel">${label}</div>
      <div class="acct-timer">${w.resets_at ? 'resets ' + fmtCountdown(w.resets_at) : 'full'}</div></div>` : '';
    if (a.active === false) {
      return `<div class="acct-card acct-inactive">
        <div class="acct-head"><div><div class="acct-email">${esc(a.email)}</div>
        <div class="acct-plan">${esc(a.plan)}</div></div><span class="acct-badge acct-badge-inactive">INACTIVE</span></div>
        <div class="acct-pair">${gauge('5 hr', a.windows.five_hour)}${gauge('Weekly', a.windows.seven_day)}</div>
        <div class="acct-meta"><span></span>${cost}</div>
      </div>`;
    }
    if (a.error) {
      if (a.windows.five_hour || a.windows.seven_day) {
        const retry = a.retry_until && new Date(a.retry_until) > Date.now()
          ? ' · retry in ' + fmtCountdown(a.retry_until) : '';
        const staleNote = esc(a.error) + ' · cached ' + fmtAgo(a.last_success_at || a.fetched_at) + retry;
        return `<div class="acct-card acct-inactive">
          <div class="acct-head"><div><div class="acct-email">${esc(a.email)}</div>
          <div class="acct-plan">${esc(a.plan)}</div></div><span class="acct-badge acct-badge-inactive">STALE</span></div>
          <div class="acct-pair">${gauge('5 hr', a.windows.five_hour)}${gauge('Weekly', a.windows.seven_day)}</div>
          <div class="acct-stale-note">${staleNote}</div>
          <div class="acct-meta"><span>${a.renews_in_days != null ? 'renews in ' + a.renews_in_days + 'd' : ''}</span>${cost}</div>
        </div>`;
      }
      let hint = 're-auth: python3 cli.py accounts add';
      if (a.error_kind === 'permission') {
        hint = "org blocked usage API (Anthropic-side) — re-auth won't help";
      } else if (a.error_kind === 'rate_limit') {
        hint = a.retry_until && new Date(a.retry_until) > Date.now()
          ? 'rate-limited — retry in ' + fmtCountdown(a.retry_until)
          : 'rate-limited — retrying soon';
      }
      return `<div class="acct-card acct-error">
        <div class="acct-head"><div><div class="acct-email">${esc(a.email)}</div>
        <div class="acct-plan">${esc(a.plan)}</div></div></div>
        <div class="acct-error-msg">${esc(hint)}<br>${esc(a.error)}</div>
        <div class="acct-meta"><span>${a.renews_in_days != null ? 'renews in ' + a.renews_in_days + 'd' : ''}</span>${cost}</div></div>`;
    }
    const badge = a.is_optimal && !a.error ? '<span class="acct-badge">USE ME</span>' : '';
    return `<div class="acct-card">
      <div class="acct-head"><div><div class="acct-email">${esc(a.email)}</div>
      <div class="acct-plan">${esc(a.plan)}</div></div>${badge}</div>
      <div class="acct-pair">${gauge('5 hr', a.windows.five_hour)}${gauge('Weekly', a.windows.seven_day)}</div>
      <div class="acct-meta"><span>${a.renews_in_days != null ? 'renews in ' + a.renews_in_days + 'd' : ''}</span>${cost}</div>
    </div>`;
  }).join('');
  const newest = accts.map(a => a.fetched_at).filter(Boolean).sort().pop();
  document.getElementById('accounts-fetched').textContent =
    newest ? 'last scan ' + fmtAgo(newest) : 'never fetched';
  const sum = ACCT_DATA.summary;
  if (sum) document.getElementById('accounts-total').textContent =
    `$${sum.total_lifetime.toFixed(2)} lifetime · $${sum.total_current_monthly.toFixed(0)}/mo · ${sum.active_accounts} active`;
}

async function loadAccounts() {
  ACCT_DATA = await (await fetch('/api/accounts')).json();  // instant from cache
  renderAccounts();
  refreshAccounts();                                         // then one live fetch
}

async function refreshAccounts() {
  if (refreshAccounts.busy) return;
  refreshAccounts.busy = true;
  const btn = document.getElementById('accounts-refresh-btn');
  btn.disabled = true;
  try {
    const resp = await fetch('/api/accounts/refresh', {method: 'POST'});
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    ACCT_DATA = await resp.json();
    renderAccounts();
  } catch(e) {
    document.getElementById('accounts-fetched').textContent = 'refresh failed';
  } finally {
    refreshAccounts.busy = false;
    btn.disabled = false;
  }
}

setInterval(renderAccounts, 30000);  // tick countdowns + freshness label

let autoRefreshTimer = null;
function scheduleAutoRefresh() {
  if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
  if (rangeIncludesToday(selectedRange)) {
    autoRefreshTimer = setInterval(loadData, 30000);
  }
}

async function initializeDashboard() {
  await triggerRefresh(true);
  if (dashboardPayload?.test_mode) {
    document.getElementById('accounts-bar').classList.add('hidden');
    document.getElementById('accounts-row').classList.add('hidden');
  } else {
    await loadAccounts();
  }
  scheduleAutoRefresh();
}

initializeDashboard();
</script>
</body>
</html>
"""


def get_accounts_data(refresh=False):
    """Account limit data for the orb row; credential-free public view."""
    import accounts

    accts = accounts.fetch_all_usage() if refresh else accounts.load_store()["accounts"]
    return accounts.dashboard_payload(accts)


def find_icon_file():
    """Locate the extension's icon.svg across both run contexts.

    - Bundled in the .vsix: this file lives at ``python/dashboard.py`` and the
      icon is a sibling-of-parent at ``../resources/icon.svg``.
    - Standalone repo (``python cli.py dashboard``): this file is the repo-root
      ``dashboard.py`` and the icon is at ``vscode-extension/resources/icon.svg``.

    Returns the first existing path, or ``None`` so the /icon.svg route can 404
    gracefully (the header ``<img>`` then just renders empty alt text).
    """
    here = Path(__file__).resolve().parent
    for candidate in (
        here.parent / "resources" / "icon.svg",
        here / "vscode-extension" / "resources" / "icon.svg",
    ):
        if candidate.is_file():
            return candidate
    return None


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _testing_mode(self) -> bool:
        """Return whether this server is the isolated first-run preview."""
        return bool(getattr(self.server, "testing_mode", False))

    def _public_dashboard_data(self) -> dict[str, Any]:
        """Return normal data or isolated first-run preview data."""
        if self._testing_mode():
            return get_public_dashboard_data(
                include_subscriptions=False,
                include_history=False,
                profile_store_path=TESTING_STORE_PATH,
                test_mode=True,
            )
        return get_public_dashboard_data()

    def do_GET(self):
        # self.path includes the query string, but every URL the UI emits has
        # one (e.g. "/?range=all"); compare the bare path so bookmarkable
        # URLs don't fall through to 404.
        path = urlparse(self.path).path
        if path.startswith("/api/") and not _request_is_local(self):
            self.send_response(403)
            self.end_headers()
            return
        if path in ("/", "/index.html"):
            body = HTML_TEMPLATE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # No-store on the HTML shell so a code update is never masked by a
            # stale browser cache. Static assets keep their own max-age.
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'",
            )
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/data":
            data = _empty_history() if self._testing_mode() else get_dashboard_data()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/daemons":
            from daemon_page import PAGE

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE.encode("utf-8"))

        elif path == "/api/daemons":
            if self._testing_mode():
                self.send_response(404)
                self.end_headers()
                return
            import classify

            self._send_json(classify.build_report())

        elif path == "/api/vitals":
            if self._testing_mode():
                self.send_response(404)
                self.end_headers()
                return
            # Live system vitals snapshot written by the system-sentinel daemon
            # (com.mighty.system-sentinel). Report-only; the dashboard never acts.
            vpath = (
                Path.home()
                / ".claude"
                / "daemon-registry"
                / "system_vitals_latest.json"
            )
            try:
                with open(vpath) as fh:
                    self._send_json(json.load(fh))
            except (OSError, ValueError) as exc:
                # Don't echo raw exception text (filesystem paths / OS error
                # detail) back to the client; log locally, return a generic error.
                print(f"[/api/vitals] read failed: {exc}")
                self._send_json(
                    {"error": "vitals unavailable", "vitals": None, "findings": []}
                )

        elif path == "/api/accounts":
            if self._testing_mode():
                self._send_json({"accounts": [], "summary": None})
                return
            try:
                self._send_json(get_accounts_data(refresh=False))
            except Exception as e:
                self._send_json({"accounts": [], "error": str(e)})

        elif path == "/api/overview":
            data = self._public_dashboard_data()
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        elif path == "/icon.svg":
            icon = find_icon_file()
            if icon is None:
                self.send_response(404)
                self.end_headers()
                return
            body = icon.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)

        elif path == "/assets/hfo-icon.png":
            if not HFO_ICON_PATH.is_file():
                self.send_response(404)
                self.end_headers()
                return
            body = HFO_ICON_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)

        elif path == "/vendor/chart.umd.min.js":
            if not CHART_JS_PATH.is_file():
                self.send_response(404)
                self.end_headers()
                return
            body = CHART_JS_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if not _request_is_local(self):
            self.send_response(403)
            self.end_headers()
            return
        if path == "/api/refresh":
            result = (
                {"testing_mode": True, "claude": {"turns": 0}, "codex": {"turns": 0}}
                if self._testing_mode()
                else refresh_local_histories()
            )
            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/testing/reset":
            if not self._testing_mode():
                self.send_response(404)
                self.end_headers()
                return
            reset_testing_registry()
            body = json.dumps({"testing_mode": True, "reset": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/testing/seed":
            if not self._testing_mode():
                self.send_response(404)
                self.end_headers()
                return
            seed_testing_registry()
            body = json.dumps({"testing_mode": True, "seeded": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/rescan":
            if self._testing_mode():
                self.send_response(404)
                self.end_headers()
                return
            # Full rebuild: delete DB and rescan from scratch.
            # Pass DB_PATH / DEFAULT_PROJECTS_DIRS explicitly so tests that
            # patch the module globals are honored (scan's defaults are
            # frozen at def time and would otherwise target the real paths).
            import scanner

            db_path = DB_PATH
            if db_path.exists():
                db_path.unlink()
            result = scanner.scan(
                db_path=db_path,
                projects_dirs=scanner.DEFAULT_PROJECTS_DIRS,
                verbose=False,
            )
            body = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/api/prompt":
            import promptgen

            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or "{}")
            self._send_json(
                {"prompt": promptgen.build_prompt(payload.get("findings", []))}
            )

        elif path == "/api/accounts/refresh":
            if self._testing_mode():
                self.send_response(404)
                self.end_headers()
                return
            try:
                self._send_json(get_accounts_data(refresh=True))
            except Exception as e:
                self._send_json({"accounts": [], "error": str(e)})

        else:
            self.send_response(404)
            self.end_headers()


def serve(host=None, port=None, test_mode=False):
    host = host or os.environ.get("HOST", "localhost")
    port = port or int(os.environ.get("PORT", "8080"))
    if not _is_loopback_host(host):
        raise ValueError(
            "For account privacy, the dashboard only binds to localhost or a "
            "loopback IP address"
        )
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    server.testing_mode = bool(test_mode)
    print(f"Dashboard running at http://{host}:{port}")
    # Background freshness watcher: the dashboard is request-driven, so without
    # this a daemon could go stale for hours with nobody looking. Additive and
    # guarded. Test mode stays isolated and must not start normal-data watchers.
    if test_mode:
        print("Isolated first-run preview is active. Normal data is not read.")
    else:
        try:
            import freshness_watch

            freshness_watch.start_watcher()
            print("Freshness watcher started (15m interval).")
        except Exception as exc:  # noqa: BLE001
            print(f"Freshness watcher not started ({exc}).")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    serve()
