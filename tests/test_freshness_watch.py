"""Behavior tests for freshness_watch.check_and_alert cooldown logic."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import freshness_watch as fw


def _report(*daemons):
    return {"daemons": list(daemons)}


def _d(label, bucket, reasons=None):
    return {"label": label, "bucket": bucket, "reasons": reasons or []}


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, label, reasons, ts=None):
        self.calls.append((label, tuple(reasons)))


def test_first_waste_alerts():
    spy = _Spy()
    rep = _report(_d("com.a", "WASTE", ["heartbeat stale (4h old)"]))
    out = fw.check_and_alert(rep, {}, now=1000, cooldown_s=1800, notifier=spy)
    assert len(spy.calls) == 1
    assert out["com.a"] == 1000


def test_repeat_within_cooldown_is_silent():
    spy = _Spy()
    rep = _report(_d("com.a", "WASTE", ["x"]))
    out = fw.check_and_alert(rep, {"com.a": 1000}, now=1500, cooldown_s=1800, notifier=spy)
    assert spy.calls == []
    assert out["com.a"] == 1000


def test_realert_after_cooldown():
    spy = _Spy()
    rep = _report(_d("com.a", "WASTE", ["x"]))
    out = fw.check_and_alert(rep, {"com.a": 1000}, now=3000, cooldown_s=1800, notifier=spy)
    assert len(spy.calls) == 1
    assert out["com.a"] == 3000


def test_recovery_clears_entry_and_is_silent():
    spy = _Spy()
    rep = _report(_d("com.a", "HEALTHY"))
    out = fw.check_and_alert(rep, {"com.a": 1000}, now=5000, cooldown_s=1800, notifier=spy)
    assert spy.calls == []
    assert "com.a" not in out  # cleared so a later regression re-alerts at once


def test_healthy_only_no_alerts():
    spy = _Spy()
    rep = _report(_d("com.a", "HEALTHY"), _d("com.b", "UNKNOWN"))
    out = fw.check_and_alert(rep, {}, now=1, cooldown_s=1800, notifier=spy)
    assert spy.calls == []
    assert out == {}
