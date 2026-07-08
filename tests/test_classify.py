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
        "pid": 123,
        "last_exit": 0,
        "schedule": "at 05:00",
        "cost_tier": "none",
        "cwd_prefix": None,
        "eol_date": None,
        "cost_mixed": False,
        "turns_30d": 0,
    }
    base.update(over)
    return base


def test_unannotated_is_undeclared():
    bucket, reasons, cmd = classify_daemon(_daemon(expected_state="TODO"))
    assert bucket == "UNDECLARED"
    assert cmd is None
    assert any("daemons.json" in r for r in reasons)


def test_healthy_when_declared_and_quiet():
    bucket, reasons, cmd = classify_daemon(_daemon())
    assert bucket == "HEALTHY"
    assert reasons == []
    assert cmd is None


def test_disabled_but_loaded_is_disabled_drift():
    bucket, reasons, cmd = classify_daemon(
        _daemon(expected_state="disabled", loaded=True)
    )
    assert bucket == "DISABLED-DRIFT"
    assert any("disabled" in r for r in reasons)
    assert cmd == f"launchctl bootout gui/{__import__('os').getuid()}/com.test.job"


def test_nonzero_exit_on_scheduled_is_broken():
    bucket, reasons, cmd = classify_daemon(_daemon(last_exit=2, schedule="at 05:00"))
    assert bucket == "BROKEN"
    assert any("exit code 2" in r for r in reasons)
    assert cmd is None  # fix, don't bootout


def test_sigterm_exit_is_healthy():
    # -15 = SIGTERM = normal launchd stop/restart, never a failure.
    bucket, reasons, cmd = classify_daemon(_daemon(last_exit=-15))
    assert bucket == "HEALTHY"


def test_keepalive_running_with_nonzero_exit_is_healthy():
    # forge-tunnel class: currently loaded + live PID, nonzero last exit is a
    # historical reconnect artifact.
    bucket, reasons, cmd = classify_daemon(
        _daemon(last_exit=1, schedule="always-on (KeepAlive)", pid=999, loaded=True)
    )
    assert bucket == "HEALTHY"


def test_keepalive_dead_with_nonzero_exit_is_broken():
    # KeepAlive but NOT running (pid None) and nonzero exit → genuinely broken.
    bucket, reasons, cmd = classify_daemon(
        _daemon(last_exit=1, schedule="always-on (KeepAlive)", pid=None, loaded=False)
    )
    assert bucket == "BROKEN"


def test_cost_tier_idle_is_broken():
    bucket, reasons, cmd = classify_daemon(
        _daemon(cost_tier="opus", turns_30d=0, cwd_prefix="tools/x")
    )
    assert bucket == "BROKEN"
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
    assert cmd is not None


def test_future_eol_is_healthy():
    bucket, reasons, cmd = classify_daemon(_daemon(eol_date="2999-01-01"))
    assert bucket == "HEALTHY"


def test_bad_eol_date_ignored():
    bucket, reasons, cmd = classify_daemon(_daemon(eol_date="not-a-date"))
    assert bucket == "HEALTHY"


# ── Vendor helpers ───────────────────────────────────────────────────────────


def test_vendor_helper_is_vendor_ignore():
    bucket, reasons, cmd = classify_daemon(
        _daemon(label="com.adobe.GC.Scheduler-1.0", expected_state="disabled", loaded=False)
    )
    assert bucket == "VENDOR-IGNORE"
    assert cmd is None


def test_vendor_helper_loaded_drift_is_still_vendor_ignore():
    # Steam/Samsung/Adobe loaded despite disabled decl → cosmetic, NOT WASTE.
    bucket, reasons, cmd = classify_daemon(
        _daemon(label="com.valvesoftware.steamclean", expected_state="disabled", loaded=True, last_exit=78)
    )
    assert bucket == "VENDOR-IGNORE"
    assert any("cosmetic" in r for r in reasons)
    assert cmd is None  # bootout is cosmetic for vendors; never suggested


def test_disabled_off_with_stale_exit_is_healthy():
    # A declared-disabled daemon that is off must not become BROKEN off a stale
    # historical nonzero exit — BROKEN is only for wanted (enabled/scheduled).
    bucket, reasons, cmd = classify_daemon(
        _daemon(expected_state="disabled", loaded=False, pid=None, last_exit=1)
    )
    assert bucket == "HEALTHY"


def test_vendor_prefix_beats_eol():
    # A vendor daemon past EOL is still surfaced as vendor, not MIGHTY waste.
    bucket, reasons, cmd = classify_daemon(
        _daemon(label="com.samsung.portablessdplus.mon", eol_date="2020-01-01")
    )
    assert bucket == "VENDOR-IGNORE"


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


def test_missing_heartbeat_is_broken(tmp_path):
    bucket, reasons, cmd = classify_daemon(
        _daemon(heartbeat_file=str(tmp_path / "nope.json"), freshness_max_hours=3)
    )
    assert bucket == "BROKEN"
    assert any("heartbeat missing" in r for r in reasons)


def test_stale_heartbeat_is_broken(tmp_path):
    hb = _heartbeat(tmp_path, ok=True, age_hours=5)
    bucket, reasons, cmd = classify_daemon(_daemon(heartbeat_file=hb, freshness_max_hours=3))
    assert bucket == "BROKEN"
    assert any("stale" in r for r in reasons)


def test_failed_run_heartbeat_is_broken(tmp_path):
    hb = _heartbeat(tmp_path, ok=False, error="notion 500")
    bucket, reasons, cmd = classify_daemon(_daemon(heartbeat_file=hb, freshness_max_hours=3))
    assert bucket == "BROKEN"
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
    assert bucket == "BROKEN"  # "2" -> 2.0, 4h > 2h
