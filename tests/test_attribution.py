"""Behavior tests for attribution cost rollup + mixed-dir guard."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attribution import is_mixed, cost_for_prefix


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE sessions (session_id TEXT, project_name TEXT)"
    )
    conn.execute(
        """CREATE TABLE turns (
            session_id TEXT, timestamp TEXT, model TEXT, cwd TEXT,
            input_tokens INT, output_tokens INT,
            cache_read_tokens INT, cache_creation_tokens INT
        )"""
    )
    return conn


def _add(conn, project, cwd, model="claude-opus-4-7", out=1_000_000):
    conn.execute("INSERT INTO sessions VALUES ('s1', ?)", (project,))
    conn.execute(
        "INSERT INTO turns VALUES ('s1', datetime('now','-1 day'), ?, ?, 0, ?, 0, 0)",
        (model, cwd, out),
    )


def test_is_mixed_flags_shared_root():
    assert is_mixed("Desktop/_Code")
    assert is_mixed("/Desktop/_Code/")
    assert not is_mixed("tools/design-center-pipeline")


def test_null_prefix_returns_zero():
    conn = _conn()
    r = cost_for_prefix(conn, None, 30)
    assert r == {"cost": 0.0, "turns": 0, "mixed": False}


def test_mixed_prefix_not_attributed():
    conn = _conn()
    _add(conn, "Desktop/_Code", "Desktop/_Code")
    r = cost_for_prefix(conn, "Desktop/_Code", 30)
    assert r["mixed"] is True
    assert r["cost"] == 0.0


def test_unique_prefix_sums_cost():
    conn = _conn()
    _add(conn, "tools/x-pipeline", "tools/x-pipeline", out=1_000_000)
    r = cost_for_prefix(conn, "tools/x-pipeline", 30)
    assert r["turns"] == 1
    # 1M opus output tokens at $25/M = $25.00
    assert round(r["cost"], 2) == 25.00


def test_window_excludes_old_rows():
    conn = _conn()
    conn.execute("INSERT INTO sessions VALUES ('s9', 'tools/y')")
    conn.execute(
        "INSERT INTO turns VALUES ('s9', datetime('now','-40 days'), "
        "'claude-opus-4-7', 'tools/y', 0, 1000000, 0, 0)"
    )
    r = cost_for_prefix(conn, "tools/y", 30)
    assert r["turns"] == 0
