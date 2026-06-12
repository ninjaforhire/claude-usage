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

from cli import calc_cost, fmt, fmt_cost, PRICING


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


_BLOCKS = "▁▂▃▄▅▆▇█"


def _spark_line(values: list[float]) -> str:
    """Map a list of floats to an 8-level block-character spark string."""
    if not values:
        return ""
    max_val = max(values)
    if max_val == 0:
        return _BLOCKS[0] * len(values)
    return "".join(_BLOCKS[min(7, int(v / max_val * 8))] for v in values)


def _cache_savings(data: dict) -> float:
    """Estimate dollars saved by cache reads vs paying full input price."""
    total = 0.0
    for r in data["by_model"]:
        p = PRICING.get(r["model"])
        if p and r["cr"]:
            total += r["cr"] * (p["input"] - p["cache_read"]) / 1_000_000
    return total


def table_report(conn: sqlite3.Connection, period: str) -> None:
    """Print a tabular usage report to stdout."""
    data = fetch_period_data(conn, period)
    w = 60
    print()
    print("═" * w)
    print(f"  Report: {data['period_label']}")
    print("═" * w)
    print(f"  {'Model':<30}  {'Turns':<5}  {'Input':<8}  {'Output':<8}  {'Cost'}")
    print("  " + "─" * (w - 2))

    total_inp = total_out = total_turns = 0
    total_cost = 0.0
    for r in data["by_model"]:
        print(f"  {r['model']:<30}  {r['turns']:<5}  {fmt(r['inp']):<8}  "
              f"{fmt(r['out']):<8}  {fmt_cost(r['cost'])}")
        total_inp   += r["inp"]
        total_out   += r["out"]
        total_turns += r["turns"]
        total_cost  += r["cost"]

    print("  " + "─" * (w - 2))
    print(f"  {'TOTAL':<30}  {total_turns:<5}  {fmt(total_inp):<8}  "
          f"{fmt(total_out):<8}  {fmt_cost(total_cost)}")

    savings = _cache_savings(data)
    print()
    parts = [
        f"Sessions: {data['total_sessions']}",
        f"Workflow ⚡{data['workflow_sessions']}",
        f"Cache saved: ~{fmt_cost(savings)}",
    ]
    print("  " + "  │  ".join(parts))
    print("═" * w)
    print()


def _model_short(model: str) -> str:
    """Return a short display label for a model string."""
    m = model.lower()
    if "fable" in m:   return "Fable 5"
    if "mythos" in m:  return "Mythos 5"
    if "opus" in m:
        for v in ("4-8", "4-7", "4-6", "4-5"):
            if v in m:
                return f"Opus {v.replace('-', '.')}"
        return "Opus"
    if "sonnet" in m:
        for v in ("4-7", "4-6", "4-5"):
            if v in m:
                return f"Sonnet {v.replace('-', '.')}"
        return "Sonnet"
    if "haiku" in m:
        for v in ("4-7", "4-6", "4-5"):
            if v in m:
                return f"Haiku {v.replace('-', '.')}"
        return "Haiku"
    return model[:12]


def card_report(conn: sqlite3.Connection, period: str) -> None:
    """Print a card-style usage report to stdout."""
    data = fetch_period_data(conn, period)
    w = 59

    total_cost  = sum(r["cost"] for r in data["by_model"])
    total_turns = sum(r["turns"] for r in data["by_model"])
    savings     = _cache_savings(data)

    header = (f"  Cost {fmt_cost(total_cost)}   Turns {total_turns}   "
              f"Sessions {data['total_sessions']}   Workflow ⚡{data['workflow_sessions']}")

    print()
    print("┌" + "─" * w + "┐")
    print(f"│  {data['period_label']:<{w-2}}│")
    print(f"│  {header:<{w-2}}│")
    print("├" + "─" * 14 + "┬" + "─" * 14 + "┬" + "─" * (w - 30) + "┐")

    top = data["by_model"][:2]
    while len(top) < 2:
        top.append(None)

    other_cost  = sum(r["cost"] for r in data["by_model"][2:])
    other_turns = sum(r["turns"] for r in data["by_model"][2:])

    def pct(cost):
        return f"{int(cost / total_cost * 100)}%" if total_cost else "0%"

    def col(row):
        if row is None:
            return ["", "", ""]
        short = _model_short(row["model"])
        return [
            f"  {short}",
            f"  {fmt_cost(row['cost'])} ({pct(row['cost'])})",
            f"  {row['turns']} turns",
        ]

    cache_col = [
        "  Cache",
        f"  Saved ~{fmt_cost(savings)}",
        f"  Rd {fmt(data['cache_read'])} / Wr {fmt(data['cache_creation'])}",
    ]

    if other_cost > 0:
        other_col = [
            "  Other",
            f"  {fmt_cost(other_cost)} ({pct(other_cost)})",
            f"  {other_turns} turns",
        ]
        cols = [col(top[0]), other_col, cache_col]
    else:
        cols = [col(top[0]), col(top[1]), cache_col]

    for line_idx in range(3):
        cells = [f"{c[line_idx]:<14}" if i < 2 else f"{c[line_idx]:<{w-30}}"
                 for i, c in enumerate(cols)]
        print("│" + "│".join(cells) + "│")

    print("└" + "─" * 14 + "┴" + "─" * 14 + "┴" + "─" * (w - 30) + "┘")
    print()
