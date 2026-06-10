"""Behavior tests for classify.classify_daemon bucketing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classify import classify_daemon


def _daemon(**over):
    base = {
        "label": "com.test.job",
        "expected_state": "enabled",
        "loaded": True,
        "last_exit": 0,
        "cost_tier": "none",
        "cwd_prefix": None,
        "eol_date": None,
        "cost_mixed": False,
        "turns_30d": 0,
    }
    base.update(over)
    return base


def test_unannotated_is_unknown():
    bucket, reasons, cmd = classify_daemon(_daemon(expected_state="TODO"))
    assert bucket == "UNKNOWN"
    assert cmd is None


def test_healthy_when_declared_and_quiet():
    bucket, reasons, cmd = classify_daemon(_daemon())
    assert bucket == "HEALTHY"
    assert reasons == []
    assert cmd is None


def test_disabled_but_loaded_is_waste():
    bucket, reasons, cmd = classify_daemon(
        _daemon(expected_state="disabled", loaded=True)
    )
    assert bucket == "WASTE"
    assert any("disabled" in r for r in reasons)
    assert cmd == f"launchctl bootout gui/{__import__('os').getuid()}/com.test.job"


def test_nonzero_exit_is_waste():
    bucket, reasons, cmd = classify_daemon(_daemon(last_exit=2))
    assert bucket == "WASTE"
    assert any("exit code 2" in r for r in reasons)


def test_cost_tier_idle_is_waste():
    bucket, reasons, cmd = classify_daemon(
        _daemon(cost_tier="opus", turns_30d=0, cwd_prefix="tools/x")
    )
    assert bucket == "WASTE"
    assert any("no usage.db activity" in r for r in reasons)


def test_cost_tier_active_is_healthy():
    bucket, reasons, cmd = classify_daemon(
        _daemon(cost_tier="opus", turns_30d=12, cwd_prefix="tools/x")
    )
    assert bucket == "HEALTHY"


def test_mixed_dir_not_flagged_idle():
    bucket, reasons, cmd = classify_daemon(
        _daemon(cost_tier="opus", turns_30d=0, cost_mixed=True)
    )
    assert bucket == "HEALTHY"


def test_disabled_idle_not_double_flagged():
    bucket, reasons, cmd = classify_daemon(
        _daemon(expected_state="disabled", loaded=False, cost_tier="opus", turns_30d=0)
    )
    assert bucket == "HEALTHY"


def test_past_eol_is_waste():
    bucket, reasons, cmd = classify_daemon(_daemon(eol_date="2020-01-01"))
    assert bucket == "WASTE"
    assert any("past EOL" in r for r in reasons)


def test_future_eol_is_healthy():
    bucket, reasons, cmd = classify_daemon(_daemon(eol_date="2999-01-01"))
    assert bucket == "HEALTHY"


def test_bad_eol_date_ignored():
    bucket, reasons, cmd = classify_daemon(_daemon(eol_date="not-a-date"))
    assert bucket == "HEALTHY"


# ── Heartbeat freshness (generic per-run receipt check) ──────────────────────
import json as _json
import os as _os
import time as _time


def _heartbeat(tmp_path, ok=True, error=None, age_hours=0.0):
    p = tmp_path / "hb.json"
    p.write_text(_json.dumps({"ts": "2026-06-09T00:00:00+00:00", "ok": ok, "error": error}))
    if age_hours:
        old = _time.time() - age_hours * 3600
        _os.utime(p, (old, old))
    return str(p)


def test_fresh_ok_heartbeat_is_healthy(tmp_path):
    hb = _heartbeat(tmp_path, ok=True)
    bucket, reasons, cmd = classify_daemon(_daemon(heartbeat_file=hb, freshness_max_hours=3))
    assert bucket == "HEALTHY"
    assert reasons == []


def test_missing_heartbeat_is_waste(tmp_path):
    bucket, reasons, cmd = classify_daemon(
        _daemon(heartbeat_file=str(tmp_path / "nope.json"), freshness_max_hours=3)
    )
    assert bucket == "WASTE"
    assert any("heartbeat missing" in r for r in reasons)


def test_stale_heartbeat_is_waste(tmp_path):
    hb = _heartbeat(tmp_path, ok=True, age_hours=5)
    bucket, reasons, cmd = classify_daemon(_daemon(heartbeat_file=hb, freshness_max_hours=3))
    assert bucket == "WASTE"
    assert any("stale" in r for r in reasons)


def test_failed_run_heartbeat_is_waste(tmp_path):
    hb = _heartbeat(tmp_path, ok=False, error="notion 500")
    bucket, reasons, cmd = classify_daemon(_daemon(heartbeat_file=hb, freshness_max_hours=3))
    assert bucket == "WASTE"
    assert any("last run failed" in r for r in reasons)


def test_no_heartbeat_configured_is_unaffected():
    bucket, reasons, cmd = classify_daemon(_daemon())
    assert bucket == "HEALTHY"


def test_string_freshness_does_not_crash(tmp_path):
    hb = _heartbeat(tmp_path, ok=True)
    bucket, reasons, cmd = classify_daemon(
        _daemon(heartbeat_file=hb, freshness_max_hours="notanumber")
    )
    assert bucket == "HEALTHY"  # coerced to default 6, file is fresh


def test_numeric_string_freshness_is_coerced(tmp_path):
    hb = _heartbeat(tmp_path, ok=True, age_hours=4)
    bucket, reasons, cmd = classify_daemon(
        _daemon(heartbeat_file=hb, freshness_max_hours="2")
    )
    assert bucket == "WASTE"  # "2" -> 2.0, 4h > 2h
