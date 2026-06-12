"""
views.py - Display modes for the claude-usage report command.

Public API:
    fetch_period_data(conn, period) -> dict
    table_report(conn, period)
    card_report(conn, period)
    spark_report(conn, period)
"""

import sqlite3
from datetime import date, timedelta

from cli import calc_cost, fmt, fmt_cost


def _date_range(period: str) -> tuple[str | None, str | None]:
    """Return (start_iso, end_iso) for a period string, or (None, None) for 'all'."""
    today = date.today()
    if period == "today":
        s = today.isoformat()
        return s, s
    if period == "week":
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
    if period == "month":
        return (today - timedelta(days=29)).isoformat(), today.isoformat()
    return None, None  # "all"


def fetch_period_data(conn: sqlite3.Connection, period: str) -> dict:
    """Run all DB queries for a period and return a unified result dict."""
    start, end = _date_range(period)
    where = "WHERE substr(timestamp, 1, 10) BETWEEN ? AND ?" if start else ""
    params = (start, end) if start else ()

    by_model_rows = conn.execute(f"""
        SELECT
            COALESCE(model, 'unknown') as model,
            SUM(input_tokens)          as inp,
            SUM(output_tokens)         as out,
            SUM(cache_read_tokens)     as cr,
            SUM(cache_creation_tokens) as cc,
            COUNT(*)                   as turns
        FROM turns
        {where}
        GROUP BY model
        ORDER BY inp + out DESC
    """, params).fetchall()

    by_model = []
    for r in by_model_rows:
        cost = calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)
        by_model.append({
            "model": r["model"],
            "inp": r["inp"] or 0,
            "out": r["out"] or 0,
            "cr": r["cr"] or 0,
            "cc": r["cc"] or 0,
            "turns": r["turns"],
            "cost": cost,
        })

    by_day = []
    if period in ("week", "month"):
        day_rows = conn.execute(f"""
            SELECT
                substr(timestamp, 1, 10)   as day,
                COUNT(*)                   as turns,
                SUM(input_tokens)          as inp,
                SUM(output_tokens)         as out,
                COALESCE(model, 'unknown') as model,
                SUM(cache_read_tokens)     as cr,
                SUM(cache_creation_tokens) as cc
            FROM turns
            {where}
            GROUP BY day, model
        """, params).fetchall()
        per_day: dict = {}
        for r in day_rows:
            d = r["day"]
            bucket = per_day.setdefault(d, {"day": d, "turns": 0, "inp": 0, "out": 0, "cost": 0.0})
            bucket["turns"] += r["turns"]
            bucket["inp"]   += r["inp"] or 0
            bucket["out"]   += r["out"] or 0
            bucket["cost"]  += calc_cost(r["model"], r["inp"] or 0, r["out"] or 0, r["cr"] or 0, r["cc"] or 0)
        by_day = sorted(per_day.values(), key=lambda x: x["day"])

    session_row = conn.execute(f"""
        SELECT COUNT(DISTINCT t.session_id) as cnt
        FROM turns t
        {where}
    """, params).fetchone()

    workflow_where = (
        "WHERE tool_name = 'Workflow' AND substr(timestamp, 1, 10) BETWEEN ? AND ?"
        if start else
        "WHERE tool_name = 'Workflow'"
    )
    workflow_row = conn.execute(
        f"SELECT COUNT(DISTINCT session_id) as cnt FROM turns {workflow_where}",
        params
    ).fetchone()

    cache_row = conn.execute(f"""
        SELECT
            SUM(cache_read_tokens)     as cr,
            SUM(cache_creation_tokens) as cc
        FROM turns
        {where}
    """, params).fetchone()

    if period == "today":
        label = f"today · {date.today().isoformat()}"
    elif period == "week":
        label = f"last 7 days · {start} to {end}"
    elif period == "month":
        label = f"last 30 days · {start} to {end}"
    else:
        label = "all time"

    return {
        "by_model": by_model,
        "by_day": by_day,
        "total_sessions": session_row["cnt"] if session_row else 0,
        "workflow_sessions": workflow_row["cnt"] if workflow_row else 0,
        "cache_read": cache_row["cr"] or 0 if cache_row else 0,
        "cache_creation": cache_row["cc"] or 0 if cache_row else 0,
        "period_label": label,
        "date_range": (start, end),
    }
