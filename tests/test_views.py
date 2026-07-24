"""Tests for views.py — fetch_period_data and display functions."""

import sqlite3
import unittest
from unittest.mock import patch
import io
import sys


def _make_db():
    """In-memory SQLite DB with minimal schema and fixture data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE sessions (
            session_id      TEXT PRIMARY KEY,
            project_name    TEXT,
            first_timestamp TEXT,
            last_timestamp  TEXT,
            model           TEXT,
            turn_count      INTEGER DEFAULT 0
        );
        CREATE TABLE turns (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id              TEXT,
            timestamp               TEXT,
            model                   TEXT,
            input_tokens            INTEGER DEFAULT 0,
            output_tokens           INTEGER DEFAULT 0,
            cache_read_tokens       INTEGER DEFAULT 0,
            cache_creation_tokens   INTEGER DEFAULT 0,
            tool_name               TEXT,
            cwd                     TEXT,
            message_id              TEXT
        );
    """)
    conn.executemany(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
        [
            ("sess-a", "proj-x", "2026-06-11T10:00:00Z", "2026-06-11T11:00:00Z", "claude-opus-4-8", 3),
            ("sess-b", "proj-x", "2026-06-11T12:00:00Z", "2026-06-11T13:00:00Z", "claude-sonnet-4-6", 2),
        ]
    )
    conn.executemany(
        "INSERT INTO turns (session_id, timestamp, model, input_tokens, output_tokens, "
        "cache_read_tokens, cache_creation_tokens, tool_name) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("sess-a", "2026-06-11T10:00:00Z", "claude-opus-4-8",   1000, 500, 200, 100, None),
            ("sess-a", "2026-06-11T10:30:00Z", "claude-opus-4-8",   2000, 800, 0,   0,   None),
            ("sess-a", "2026-06-11T10:59:00Z", "claude-opus-4-8",   500,  200, 0,   0,   "Workflow"),
            ("sess-b", "2026-06-11T12:00:00Z", "claude-sonnet-4-6", 800,  300, 100, 50,  None),
            ("sess-b", "2026-06-11T12:30:00Z", "claude-sonnet-4-6", 600,  200, 0,   0,   None),
        ]
    )
    conn.commit()
    return conn


class TestFetchPeriodData(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def test_today_returns_correct_session_count(self):
        from views import fetch_period_data
        with patch("views.date") as mock_date:
            from datetime import date as real_date
            mock_date.today.return_value = real_date(2026, 6, 11)
            mock_date.fromisoformat = real_date.fromisoformat
            data = fetch_period_data(self.conn, "today")
        self.assertEqual(data["total_sessions"], 2)

    def test_workflow_sessions_count(self):
        from views import fetch_period_data
        with patch("views.date") as mock_date:
            from datetime import date as real_date
            mock_date.today.return_value = real_date(2026, 6, 11)
            mock_date.fromisoformat = real_date.fromisoformat
            data = fetch_period_data(self.conn, "today")
        self.assertEqual(data["workflow_sessions"], 1)

    def test_by_model_has_opus_and_sonnet(self):
        from views import fetch_period_data
        with patch("views.date") as mock_date:
            from datetime import date as real_date
            mock_date.today.return_value = real_date(2026, 6, 11)
            mock_date.fromisoformat = real_date.fromisoformat
            data = fetch_period_data(self.conn, "today")
        models = [r["model"] for r in data["by_model"]]
        self.assertIn("claude-opus-4-8", models)
        self.assertIn("claude-sonnet-4-6", models)
        # Verify all required keys are present on each row
        for row in data["by_model"]:
            self.assertEqual(set(row.keys()), {"model", "inp", "out", "cr", "cc", "turns", "cost"})

    def test_cache_totals_summed(self):
        from views import fetch_period_data
        with patch("views.date") as mock_date:
            from datetime import date as real_date
            mock_date.today.return_value = real_date(2026, 6, 11)
            mock_date.fromisoformat = real_date.fromisoformat
            data = fetch_period_data(self.conn, "today")
        self.assertEqual(data["cache_read"], 300)     # 200 + 100
        self.assertEqual(data["cache_creation"], 150) # 100 + 50

    def test_all_period_has_no_date_filter(self):
        from views import fetch_period_data
        data = fetch_period_data(self.conn, "all")
        self.assertEqual(data["total_sessions"], 2)
        self.assertEqual(data["date_range"], (None, None))

    def test_by_day_empty_for_all(self):
        from views import fetch_period_data
        data = fetch_period_data(self.conn, "all")
        self.assertEqual(data["by_day"], [])

    def test_by_day_populated_for_week(self):
        from views import fetch_period_data
        with patch("views.date") as mock_date:
            from datetime import date as real_date
            mock_date.today.return_value = real_date(2026, 6, 11)
            mock_date.fromisoformat = real_date.fromisoformat
            data = fetch_period_data(self.conn, "week")
        self.assertIsInstance(data["by_day"], list)
        self.assertGreater(len(data["by_day"]), 0)

    def test_by_day_empty_for_today(self):
        from views import fetch_period_data
        with patch("views.date") as mock_date:
            from datetime import date as real_date
            mock_date.today.return_value = real_date(2026, 6, 11)
            mock_date.fromisoformat = real_date.fromisoformat
            data = fetch_period_data(self.conn, "today")
        self.assertEqual(data["by_day"], [])


class TestSparkLine(unittest.TestCase):
    def test_all_zeros_returns_flat(self):
        from views import _spark_line
        result = _spark_line([0, 0, 0, 0])
        self.assertEqual(result, "▁▁▁▁")

    def test_single_nonzero_returns_max_at_that_position(self):
        from views import _spark_line
        result = _spark_line([0, 0, 5.0, 0])
        self.assertIn("█", result)
        self.assertEqual(result[2], "█")
        self.assertEqual(result[0], "▁")

    def test_all_equal_nonzero_returns_all_max(self):
        from views import _spark_line
        result = _spark_line([3.0, 3.0, 3.0])
        self.assertTrue(all(c == "█" for c in result))

    def test_length_matches_input(self):
        from views import _spark_line
        self.assertEqual(len(_spark_line([1, 2, 3, 4, 5, 6, 7, 8])), 8)

    def test_ascending_sequence_is_monotone(self):
        from views import _spark_line
        blocks = "▁▂▃▄▅▆▇█"
        result = _spark_line([1, 2, 3, 4, 5, 6, 7, 8])
        for i in range(len(result) - 1):
            self.assertLessEqual(
                blocks.index(result[i]), blocks.index(result[i + 1]),
                f"Non-monotone at position {i}: {result}"
            )

    def test_empty_list_returns_empty_string(self):
        from views import _spark_line
        self.assertEqual(_spark_line([]), "")


class TestTableReport(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _capture(self, period):
        from views import table_report
        buf = io.StringIO()
        with patch("views.date") as mock_date:
            from datetime import date as real_date
            mock_date.today.return_value = real_date(2026, 6, 11)
            mock_date.fromisoformat = real_date.fromisoformat
            with patch("sys.stdout", buf):
                table_report(self.conn, period)
        return buf.getvalue()

    def test_output_contains_model_names(self):
        out = self._capture("today")
        self.assertIn("claude-opus-4-8", out)
        self.assertIn("claude-sonnet-4-6", out)

    def test_output_contains_workflow_count(self):
        out = self._capture("today")
        self.assertIn("Workflow", out)
        self.assertIn("1", out)

    def test_output_contains_total_line(self):
        out = self._capture("today")
        self.assertIn("TOTAL", out)

    def test_output_contains_period_label(self):
        out = self._capture("today")
        self.assertIn("2026-06-11", out)


class TestCardReport(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _capture(self, period):
        from views import card_report
        buf = io.StringIO()
        with patch("views.date") as mock_date:
            from datetime import date as real_date
            mock_date.today.return_value = real_date(2026, 6, 11)
            mock_date.fromisoformat = real_date.fromisoformat
            with patch("sys.stdout", buf):
                card_report(self.conn, period)
        return buf.getvalue()

    def test_output_contains_cost_header(self):
        out = self._capture("today")
        self.assertIn("Cost", out)

    def test_output_contains_workflow_marker(self):
        out = self._capture("today")
        self.assertIn("Workflow", out)

    def test_output_contains_cache_panel(self):
        out = self._capture("today")
        self.assertIn("Cache", out)

    def test_output_contains_top_model(self):
        out = self._capture("today")
        self.assertIn("Opus 4.8", out)


class TestSparkReport(unittest.TestCase):
    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _capture(self, period):
        from views import spark_report
        buf = io.StringIO()
        with patch("views.date") as mock_date:
            from datetime import date as real_date
            mock_date.today.return_value = real_date(2026, 6, 11)
            mock_date.fromisoformat = real_date.fromisoformat
            with patch("sys.stdout", buf):
                spark_report(self.conn, period)
        return buf.getvalue()

    def test_today_falls_back_to_table(self):
        out = self._capture("today")
        self.assertIn("TOTAL", out)

    def test_all_falls_back_to_table(self):
        out = self._capture("all")
        self.assertIn("TOTAL", out)

    def test_week_shows_spark_chars(self):
        out = self._capture("week")
        spark_chars = set("▁▂▃▄▅▆▇█")
        self.assertTrue(any(c in spark_chars for c in out))

    def test_month_shows_spark_chars(self):
        out = self._capture("month")
        spark_chars = set("▁▂▃▄▅▆▇█")
        self.assertTrue(any(c in spark_chars for c in out))


class TestModelShort(unittest.TestCase):
    """Display labels must survive new major versions (Opus 5, Sonnet 5, ...)."""

    def test_single_digit_major_version(self):
        from views import _model_short
        self.assertEqual(_model_short("claude-opus-5"), "Opus 5")
        self.assertEqual(_model_short("claude-sonnet-5"), "Sonnet 5")

    def test_dotted_version(self):
        from views import _model_short
        self.assertEqual(_model_short("claude-opus-4-8"), "Opus 4.8")
        self.assertEqual(_model_short("claude-haiku-4-5-20251001"), "Haiku 4.5")

    def test_legacy_name_with_leading_version(self):
        from views import _model_short
        self.assertEqual(_model_short("claude-3-5-haiku-20241022"), "Haiku")

    def test_premium_families(self):
        from views import _model_short
        self.assertEqual(_model_short("claude-fable-5"), "Fable 5")
        self.assertEqual(_model_short("claude-mythos-5"), "Mythos 5")
