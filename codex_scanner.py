"""Scan Codex CLI rollout JSONL files into the dashboard's SQLite schema."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from scanner import get_db, init_db, project_name_from_cwd

LOGGER = logging.getLogger(__name__)
SESSIONS_DIR = Path.home() / ".codex" / "sessions"
DB_PATH = Path.home() / ".claude-codex-usage" / "codex.db"
DEFAULT_SESSION_DIRS = [SESSIONS_DIR]


def parse_jsonl_file(
    filepath: Path, start_line: int = 0
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Parse one Codex rollout, returning session metadata, turns, and line count."""
    session_meta: dict[str, dict[str, Any]] = {}
    turns: list[dict[str, Any]] = []
    line_count = 0
    current_model = "unknown"
    current_turn_id = ""

    try:
        with filepath.open(encoding="utf-8", errors="replace") as stream:
            for line_count, line in enumerate(stream, 1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                record_type = record.get("type")
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                timestamp = record.get("timestamp")
                if not isinstance(timestamp, str):
                    timestamp = ""

                if record_type == "session_meta":
                    session_id = payload.get("id")
                    if not isinstance(session_id, str) or not session_id:
                        continue
                    cwd = payload.get("cwd")
                    if not isinstance(cwd, str):
                        cwd = ""
                    first = payload.get("timestamp")
                    if not isinstance(first, str):
                        first = timestamp
                    session_meta[session_id] = {
                        "session_id": session_id,
                        "project_name": project_name_from_cwd(cwd),
                        "first_timestamp": first,
                        "last_timestamp": first,
                        # Codex's `originator` identifies the client, not a Git
                        # branch. Leave the shared branch field empty.
                        "git_branch": "",
                        "model": None,
                        "_cwd": cwd,
                    }
                    continue

                if record_type == "turn_context":
                    model = payload.get("model")
                    if isinstance(model, str) and model:
                        current_model = model
                    turn_id = payload.get("turn_id")
                    if isinstance(turn_id, str):
                        current_turn_id = turn_id
                    cwd = payload.get("cwd")
                    if isinstance(cwd, str) and cwd:
                        for meta in session_meta.values():
                            meta["_cwd"] = cwd
                            meta["project_name"] = project_name_from_cwd(cwd)
                    continue

                if line_count <= start_line or record_type != "event_msg":
                    continue
                event_type = payload.get("type")
                if event_type == "task_started":
                    turn_id = payload.get("turn_id")
                    if isinstance(turn_id, str):
                        current_turn_id = turn_id
                    continue
                if event_type != "token_count":
                    continue

                info = payload.get("info")
                if not isinstance(info, dict):
                    continue
                usage = info.get("last_token_usage")
                if not isinstance(usage, dict):
                    continue
                input_tokens = _token_count(usage.get("input_tokens"))
                output_tokens = _token_count(usage.get("output_tokens"))
                cached_tokens = _token_count(usage.get("cached_input_tokens"))
                reasoning_tokens = _token_count(usage.get("reasoning_output_tokens"))
                if not any(
                    (input_tokens, output_tokens, cached_tokens, reasoning_tokens)
                ):
                    continue
                session_id = next(iter(session_meta), None)
                if session_id is None:
                    continue
                meta = session_meta[session_id]
                if timestamp and timestamp > (meta["last_timestamp"] or ""):
                    meta["last_timestamp"] = timestamp
                if not meta["model"] and current_model != "unknown":
                    meta["model"] = current_model
                turns.append(
                    {
                        "session_id": session_id,
                        "timestamp": timestamp,
                        "model": current_model,
                        # Codex reports cached input as a subset of input_tokens.
                        # Store the non-cached portion in the shared input slot.
                        # Any cache-write input remains represented there, so
                        # it is counted once rather than silently discarded.
                        "input_tokens": max(0, input_tokens - cached_tokens),
                        "output_tokens": output_tokens,
                        "cache_read_tokens": cached_tokens,
                        "cache_creation_tokens": reasoning_tokens,
                        "tool_name": None,
                        "cwd": meta["_cwd"],
                        "message_id": (
                            f"{session_id}:{current_turn_id}:{timestamp}:{line_count}"
                        ),
                    }
                )
    except OSError as exc:
        LOGGER.warning("Could not read Codex rollout %s: %s", filepath, exc)
        raise

    for meta in session_meta.values():
        meta.pop("_cwd", None)
    return list(session_meta.values()), turns, line_count


def _token_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _aggregate_sessions(
    session_metas: Iterable[dict[str, Any]], turns: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_read": 0,
            "total_cache_creation": 0,
            "turn_count": 0,
            "model": None,
        }
    )
    model_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for turn in turns:
        values = stats[turn["session_id"]]
        values["total_input_tokens"] += turn["input_tokens"]
        values["total_output_tokens"] += turn["output_tokens"]
        values["total_cache_read"] += turn["cache_read_tokens"]
        values["total_cache_creation"] += turn["cache_creation_tokens"]
        values["turn_count"] += 1
        model_counts[turn["session_id"]][turn["model"]] += 1
    for session_id, counts in model_counts.items():
        if counts:
            stats[session_id]["model"] = counts.most_common(1)[0][0]
    return [{**meta, **stats[meta["session_id"]]} for meta in session_metas]


def _upsert_sessions(
    connection: sqlite3.Connection, sessions: Iterable[dict[str, Any]]
) -> None:
    for session in sessions:
        connection.execute(
            """
            INSERT INTO sessions
                (session_id, project_name, first_timestamp, last_timestamp,
                 git_branch, total_input_tokens, total_output_tokens,
                 total_cache_read, total_cache_creation, model, turn_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                project_name = excluded.project_name,
                last_timestamp = MAX(sessions.last_timestamp, excluded.last_timestamp),
                git_branch = COALESCE(NULLIF(excluded.git_branch, ''), sessions.git_branch),
                model = COALESCE(NULLIF(excluded.model, ''), sessions.model)
            """,
            (
                session["session_id"],
                session["project_name"],
                session["first_timestamp"],
                session["last_timestamp"],
                session["git_branch"],
                session["total_input_tokens"],
                session["total_output_tokens"],
                session["total_cache_read"],
                session["total_cache_creation"],
                session["model"],
                session["turn_count"],
            ),
        )


def _ensure_source_path_column(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(turns)").fetchall()
    }
    if "source_path" in columns:
        return
    connection.execute("ALTER TABLE turns ADD COLUMN source_path TEXT")
    # Older prerelease databases cannot associate existing rows with a rollout.
    # They are derived data, so rebuild them once from the read-only sources.
    connection.execute("DELETE FROM turns")
    connection.execute("DELETE FROM sessions")
    connection.execute("DELETE FROM processed_files")
    connection.commit()


def _insert_turns(
    connection: sqlite3.Connection, turns: Iterable[dict[str, Any]]
) -> None:
    connection.executemany(
        """
        INSERT OR IGNORE INTO turns
            (session_id, timestamp, model, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, tool_name, cwd,
             message_id, source_path)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                turn["session_id"],
                turn["timestamp"],
                turn["model"],
                turn["input_tokens"],
                turn["output_tokens"],
                turn["cache_read_tokens"],
                turn["cache_creation_tokens"],
                turn["tool_name"],
                turn["cwd"],
                turn["message_id"],
                turn["source_path"],
            )
            for turn in turns
        ],
    )


def _recompute_sessions(connection: sqlite3.Connection) -> None:
    connection.execute("""
        UPDATE sessions SET
            first_timestamp = COALESCE(
                (SELECT MIN(timestamp) FROM turns
                 WHERE turns.session_id = sessions.session_id
                   AND timestamp IS NOT NULL
                   AND timestamp != ''),
                sessions.first_timestamp),
            last_timestamp = COALESCE(
                (SELECT MAX(timestamp) FROM turns
                 WHERE turns.session_id = sessions.session_id
                   AND timestamp IS NOT NULL
                   AND timestamp != ''),
                sessions.last_timestamp),
            total_input_tokens = COALESCE(
                (SELECT SUM(input_tokens) FROM turns
                 WHERE turns.session_id = sessions.session_id), 0),
            total_output_tokens = COALESCE(
                (SELECT SUM(output_tokens) FROM turns
                 WHERE turns.session_id = sessions.session_id), 0),
            total_cache_read = COALESCE(
                (SELECT SUM(cache_read_tokens) FROM turns
                 WHERE turns.session_id = sessions.session_id), 0),
            total_cache_creation = COALESCE(
                (SELECT SUM(cache_creation_tokens) FROM turns
                 WHERE turns.session_id = sessions.session_id), 0),
            turn_count = COALESCE(
                (SELECT COUNT(*) FROM turns
                 WHERE turns.session_id = sessions.session_id), 0),
            model = COALESCE(
                (SELECT turns.model
                 FROM turns
                 WHERE turns.session_id = sessions.session_id
                   AND turns.model IS NOT NULL
                   AND turns.model != ''
                 GROUP BY turns.model
                 ORDER BY COUNT(*) DESC, turns.model
                 LIMIT 1),
                sessions.model)
        """)
    connection.execute("""
        DELETE FROM sessions
        WHERE NOT EXISTS (
            SELECT 1 FROM turns WHERE turns.session_id = sessions.session_id
        )
        """)


def scan(
    session_dirs: Optional[Iterable[Path]] = None,
    db_path: Path = DB_PATH,
    verbose: bool = True,
) -> dict[str, int]:
    """Incrementally scan Codex rollouts into a dashboard-owned database."""
    connection = get_db(db_path)
    init_db(connection)
    _ensure_source_path_column(connection)
    files: list[Path] = []
    for directory in session_dirs or DEFAULT_SESSION_DIRS:
        path = Path(directory)
        if path.exists():
            files.extend(path.rglob("*.jsonl"))

    result = {"new": 0, "updated": 0, "skipped": 0, "turns": 0, "sessions": 0}
    seen_sessions: set[str] = set()
    for filepath in sorted(files):
        try:
            modified = filepath.stat().st_mtime
        except OSError:
            continue
        prior = connection.execute(
            "SELECT mtime, lines FROM processed_files WHERE path = ?",
            (str(filepath),),
        ).fetchone()
        if prior and abs(prior["mtime"] - modified) < 0.01:
            result["skipped"] += 1
            continue
        try:
            session_metas, turns, line_count = parse_jsonl_file(filepath)
        except OSError:
            result["skipped"] += 1
            continue
        for turn in turns:
            turn["source_path"] = str(filepath)
        sessions = _aggregate_sessions(session_metas, turns)
        if prior is not None:
            connection.execute(
                "DELETE FROM turns WHERE source_path = ?", (str(filepath),)
            )
        _upsert_sessions(connection, sessions)
        _insert_turns(connection, turns)
        connection.execute(
            """
            INSERT OR REPLACE INTO processed_files (path, mtime, lines)
            VALUES (?, ?, ?)
            """,
            (str(filepath), modified, line_count),
        )
        result["new" if prior is None else "updated"] += 1
        result["turns"] += len(turns)
        seen_sessions.update(session["session_id"] for session in sessions)

    _recompute_sessions(connection)
    connection.commit()
    connection.close()
    result["sessions"] = len(seen_sessions)
    if verbose:
        LOGGER.info("Codex scan complete: %s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    scan()
