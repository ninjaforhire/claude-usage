"""
attribution.py - Attribute usage.db cost to a daemon via its working-directory prefix.

A `claude -p` subprocess writes a JSONL transcript keyed by its cwd, which the scanner
stores in turns.cwd / sessions.project_name. A daemon's plist WorkingDirectory maps to
that prefix, so we can sum the cost it generated.

Caveat: shared dirs (e.g. _Code) mix interactive terminals with daemon-spawned
sessions and cannot be cleanly attributed -> flagged "mixed".
"""

import sqlite3
from pathlib import Path

from cli import DB_PATH, calc_cost

# Prefixes that hold both interactive and daemon sessions. Cost here is not
# attributable to any single daemon.
MIXED_PREFIXES = ("_Code", "Desktop/-Code", "Users/mightydesigncenter")


def _norm(prefix):
    return (prefix or "").strip().strip("/")


def is_mixed(cwd_prefix):
    p = _norm(cwd_prefix)
    return p in (_norm(x) for x in MIXED_PREFIXES)


def cost_for_prefix(conn, cwd_prefix, days):
    """Sum estimated cost over turns whose session project_name ends with cwd_prefix.

    Returns {"cost": float, "turns": int, "mixed": bool}. cost/turns are 0 when the
    prefix is null or mixed.
    """
    prefix = _norm(cwd_prefix)
    if not prefix:
        return {"cost": 0.0, "turns": 0, "mixed": False}
    if is_mixed(prefix):
        return {"cost": 0.0, "turns": 0, "mixed": True}

    rows = conn.execute(
        """
        SELECT t.model, t.input_tokens, t.output_tokens,
               t.cache_read_tokens, t.cache_creation_tokens
        FROM turns t
        LEFT JOIN sessions s ON t.session_id = s.session_id
        WHERE t.timestamp >= datetime('now', ?)
          AND (s.project_name LIKE ? OR t.cwd LIKE ?)
        """,
        (f"-{int(days)} days", f"%{prefix}", f"%{prefix}"),
    ).fetchall()

    cost = 0.0
    for model, inp, out, cr, cc in rows:
        cost += calc_cost(model, inp or 0, out or 0, cr or 0, cc or 0)
    return {"cost": cost, "turns": len(rows), "mixed": False}


def attribute(daemons, db_path=DB_PATH):
    """Annotate each daemon dict (that carries a cwd_prefix) with cost_7d / cost_30d.

    cwd_prefix is expected to have been merged in from the registry. Daemons without
    a prefix get zeros. Returns the same list, mutated in place.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        for d in daemons:
            d["cost_7d"] = d["cost_30d"] = 0.0
            d["cost_mixed"] = False
        return daemons

    conn = sqlite3.connect(db_path)
    try:
        for d in daemons:
            prefix = d.get("cwd_prefix")
            c7 = cost_for_prefix(conn, prefix, 7)
            c30 = cost_for_prefix(conn, prefix, 30)
            d["cost_7d"] = c7["cost"]
            d["cost_30d"] = c30["cost"]
            d["cost_mixed"] = c30["mixed"]
            d["turns_30d"] = c30["turns"]
    finally:
        conn.close()
    return daemons
