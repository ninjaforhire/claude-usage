"""Tests for Codex rollout parsing and aggregation."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import codex_scanner
from scanner import get_db


class TestCodexScanner(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.sessions_dir = self.root / "sessions"
        self.sessions_dir.mkdir()
        self.db_path = self.root / "usage.db"

    def tearDown(self):
        self.tempdir.cleanup()

    def _write_rollout(self, records):
        path = self.sessions_dir / "rollout.jsonl"
        path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _session_records(models_and_inputs):
        records = [
            {
                "timestamp": "2026-07-24T10:00:00Z",
                "type": "session_meta",
                "payload": {"id": "session-1", "cwd": "/work/mighty-crm"},
            }
        ]
        for index, (model, input_tokens) in enumerate(models_and_inputs, 1):
            records.extend(
                [
                    {
                        "timestamp": f"2026-07-24T10:00:{index * 2:02d}Z",
                        "type": "turn_context",
                        "payload": {"turn_id": f"turn-{index}", "model": model},
                    },
                    {
                        "timestamp": f"2026-07-24T10:00:{index * 2 + 1:02d}Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": input_tokens,
                                    "output_tokens": 1,
                                }
                            },
                        },
                    },
                ]
            )
        return records

    def test_scan_maps_codex_token_fields_without_double_counting(self):
        self._write_rollout(
            [
                {
                    "timestamp": "2026-07-24T10:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "session-1",
                        "timestamp": "2026-07-24T10:00:00Z",
                        "cwd": "/work/mighty-crm",
                    },
                },
                {
                    "timestamp": "2026-07-24T10:00:01Z",
                    "type": "turn_context",
                    "payload": {"turn_id": "turn-1", "model": "gpt-5.6-sol"},
                },
                {
                    "timestamp": "2026-07-24T10:00:02Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 100,
                                "cached_input_tokens": 40,
                                "cache_write_input_tokens": 10,
                                "output_tokens": 20,
                                "reasoning_output_tokens": 5,
                            }
                        },
                    },
                },
            ]
        )
        result = codex_scanner.scan(
            session_dirs=[self.sessions_dir],
            db_path=self.db_path,
            verbose=False,
        )
        self.assertEqual(result["turns"], 1)
        connection = get_db(self.db_path)
        row = connection.execute("SELECT * FROM sessions").fetchone()
        connection.close()
        self.assertEqual(row["total_input_tokens"], 60)
        self.assertEqual(row["total_cache_read"], 40)
        self.assertEqual(row["total_output_tokens"], 20)
        self.assertEqual(row["total_cache_creation"], 5)
        self.assertEqual(row["turn_count"], 1)
        self.assertEqual(row["model"], "gpt-5.6-sol")

    def test_scan_is_incremental(self):
        path = self._write_rollout(
            [
                {
                    "timestamp": "2026-07-24T10:00:00Z",
                    "type": "session_meta",
                    "payload": {"id": "session-1", "cwd": "/work/mighty-crm"},
                }
            ]
        )
        first = codex_scanner.scan(
            session_dirs=[self.sessions_dir],
            db_path=self.db_path,
            verbose=False,
        )
        second = codex_scanner.scan(
            session_dirs=[self.sessions_dir],
            db_path=self.db_path,
            verbose=False,
        )
        self.assertEqual(first["new"], 1)
        self.assertEqual(second["skipped"], 1)
        self.assertTrue(path.exists())

    def test_rewrite_replaces_old_rollout_rows(self):
        path = self._write_rollout(self._session_records([("gpt-5.6-sol", 100)]))
        codex_scanner.scan(
            session_dirs=[self.sessions_dir],
            db_path=self.db_path,
            verbose=False,
        )
        old_mtime = path.stat().st_mtime
        rewritten = self._session_records([("gpt-5.6-sol", 25)])
        for record in rewritten:
            record["timestamp"] = record["timestamp"].replace(
                "2026-07-24", "2026-07-25"
            )
        self._write_rollout(rewritten)
        os.utime(path, (old_mtime + 2, old_mtime + 2))
        codex_scanner.scan(
            session_dirs=[self.sessions_dir],
            db_path=self.db_path,
            verbose=False,
        )
        connection = get_db(self.db_path)
        session = connection.execute("SELECT * FROM sessions").fetchone()
        turn_count = connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        connection.close()
        self.assertEqual(turn_count, 1)
        self.assertEqual(session["total_input_tokens"], 25)
        self.assertTrue(session["first_timestamp"].startswith("2026-07-25"))
        self.assertTrue(session["last_timestamp"].startswith("2026-07-25"))

    def test_session_model_is_recomputed_from_all_turns(self):
        path = self._write_rollout(
            self._session_records([("gpt-5.6-sol", 10), ("gpt-5.6-sol", 10)])
        )
        codex_scanner.scan(
            session_dirs=[self.sessions_dir],
            db_path=self.db_path,
            verbose=False,
        )
        old_mtime = path.stat().st_mtime
        self._write_rollout(
            self._session_records(
                [
                    ("gpt-5.6-sol", 10),
                    ("gpt-5.6-sol", 10),
                    ("gpt-5.6-terra", 10),
                ]
            )
        )
        os.utime(path, (old_mtime + 2, old_mtime + 2))
        codex_scanner.scan(
            session_dirs=[self.sessions_dir],
            db_path=self.db_path,
            verbose=False,
        )
        connection = get_db(self.db_path)
        session = connection.execute("SELECT * FROM sessions").fetchone()
        connection.close()
        self.assertEqual(session["model"], "gpt-5.6-sol")


if __name__ == "__main__":
    unittest.main()
