"""Behavior tests for freshness_watch transition-only alerting + daily digest."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import freshness_watch as fw


def _report(*daemons):
    return {"daemons": list(daemons)}


def _d(label, bucket, reasons=None, severity=None):
    return {
        "label": label,
        "bucket": bucket,
        "reasons": reasons or [],
        "severity": severity,
    }


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, label, reasons, ts=None):
        self.calls.append((label, tuple(reasons)))


def _entry(state, label):
    return state[label]


# --- transition-only alerting -------------------------------------------------


def test_new_critical_waste_alerts_realtime_and_queues():
    spy = _Spy()
    rep = _report(_d("com.a", "WASTE", ["last exit code 78"], severity="critical"))
    state, events = fw.check_and_alert(rep, {}, now=1000, notifier=spy)
    assert len(spy.calls) == 1
    assert _entry(state, "com.a")["first_seen"] == 1000
    assert [e["type"] for e in events] == ["issue"]
    assert events[0]["critical"] is True


def test_new_normal_waste_is_digest_only():
    spy = _Spy()
    rep = _report(_d("com.a", "WASTE", ["last exit code 78"]))
    state, events = fw.check_and_alert(rep, {}, now=1000, notifier=spy)
    assert spy.calls == []  # no realtime push for non-critical
    assert [e["type"] for e in events] == ["issue"]
    assert events[0]["critical"] is False
    assert "com.a" in state


def test_same_signature_is_silent_forever():
    spy = _Spy()
    rep = _report(_d("com.a", "WASTE", ["last exit code 78"], severity="critical"))
    state, _ = fw.check_and_alert(rep, {}, now=1000, notifier=spy)
    for now in (3000, 99999, 10**9):  # far beyond any cooldown
        state, events = fw.check_and_alert(rep, state, now=now, notifier=spy)
        assert events == []
    assert len(spy.calls) == 1  # only the original transition


def test_changed_signature_realerts():
    spy = _Spy()
    rep1 = _report(_d("com.a", "WASTE", ["last exit code 78"], severity="critical"))
    state, _ = fw.check_and_alert(rep1, {}, now=1000, notifier=spy)
    rep2 = _report(_d("com.a", "WASTE", ["last exit code 1"], severity="critical"))
    state, events = fw.check_and_alert(rep2, state, now=2000, notifier=spy)
    assert len(spy.calls) == 2
    assert events[0]["type"] == "issue"
    assert _entry(state, "com.a")["first_seen"] == 1000  # original onset preserved


def test_volatile_age_change_is_not_a_new_issue():
    spy = _Spy()
    rep1 = _report(_d("com.a", "WASTE", ["heartbeat stale (4h old)"], severity="critical"))
    state, _ = fw.check_and_alert(rep1, {}, now=1000, notifier=spy)
    rep2 = _report(_d("com.a", "WASTE", ["heartbeat stale (9h old)"], severity="critical"))
    state, events = fw.check_and_alert(rep2, state, now=2000, notifier=spy)
    assert len(spy.calls) == 1
    assert events == []


def test_recovery_clears_and_queues_event():
    spy = _Spy()
    rep = _report(_d("com.a", "HEALTHY"))
    prior = {"com.a": {"sig": "x", "first_seen": 1, "last_alert": 1}}
    state, events = fw.check_and_alert(rep, prior, now=5000, notifier=spy)
    assert spy.calls == []
    assert "com.a" not in state
    assert [e["type"] for e in events] == ["recovered"]


def test_healthy_only_no_alerts_no_events():
    spy = _Spy()
    rep = _report(_d("com.a", "HEALTHY"), _d("com.b", "UNKNOWN"))
    state, events = fw.check_and_alert(rep, {}, now=1, notifier=spy)
    assert spy.calls == []
    assert state == {} and events == []


# --- legacy state migration ----------------------------------------------------


def test_legacy_float_state_migrates_without_respam(tmp_path):
    p = tmp_path / ".alerted.json"
    p.write_text(json.dumps({"com.a": 1781150737.19}))
    state = fw._load_state(p)
    assert state["com.a"]["sig"] == ""
    spy = _Spy()
    # Non-critical daemon: migration re-evaluates sig but routes to digest only.
    rep = _report(_d("com.a", "WASTE", ["last exit code 78"]))
    state, events = fw.check_and_alert(rep, state, now=2000, notifier=spy)
    assert spy.calls == []
    assert len(events) == 1
    # Original first_seen survives the migration.
    assert _entry(state, "com.a")["first_seen"] == 1781150737.19


# --- digest queue + builder ----------------------------------------------------


def test_queue_append_and_drain_roundtrip(tmp_path):
    q = tmp_path / "digest-queue.jsonl"
    fw.append_events([{"type": "issue", "label": "com.a"}], path=q)
    fw.append_events([{"type": "recovered", "label": "com.b"}], path=q)
    events = fw.drain_queue(q)
    assert [e["type"] for e in events] == ["issue", "recovered"]
    assert fw.drain_queue(q) == []  # drained
    assert not q.exists()


def test_drain_skips_corrupt_lines(tmp_path):
    q = tmp_path / "digest-queue.jsonl"
    q.write_text('{"type": "note", "line": "ok"}\nnot json\n')
    events = fw.drain_queue(q)
    assert len(events) == 1


def test_build_digest_lines():
    state = {"com.a": {"sig": "x", "first_seen": 0, "last_alert": 0}}
    rep = _report(_d("com.a", "WASTE", ["last exit code 78"]))
    events = [
        {"type": "recovered", "label": "com.b"},
        {"type": "recovered", "label": "com.b"},  # deduped
        {"type": "mission_failed", "name": "morning_brief", "error": "timeout"},
        {"type": "repaired", "target": "com.c"},
        {"type": "repair_proposal", "target": "com.d", "root_cause": "TCC"},
        {"type": "note", "line": "legacy memory decoy reappeared"},
        {"type": "issue", "label": "com.a", "reasons": ["x"]},  # skipped
    ]
    title, lines = fw.build_digest(rep, state, events, now=7200)
    assert "1 unresolved" in title
    assert any(l.startswith("UNRESOLVED com.a") and "(2h)" in l for l in lines)
    assert lines.count("RECOVERED com.b") == 1
    assert any("MISSION FAIL morning_brief" in l for l in lines)
    assert any("SELF-REPAIRED com.c" in l for l in lines)
    assert any("REPAIR PROPOSAL com.d" in l for l in lines)
    assert any("decoy" in l for l in lines)
    assert not any(l.startswith("issue") for l in lines)


def test_build_digest_empty_when_nothing_to_say():
    _, lines = fw.build_digest(_report(), {}, [], now=0)
    assert lines == []


def test_digest_due_respects_period_and_hour():
    noon = lambda now: type("t", (), {"tm_hour": 12})()
    predawn = lambda now: type("t", (), {"tm_hour": 5})()
    assert fw.digest_due(0, 10**6, localtime=noon)
    assert not fw.digest_due(0, 10**6, localtime=predawn)  # too early locally
    assert not fw.digest_due(10**6 - 100, 10**6, localtime=noon)  # period not elapsed


# --- decoy tripwire --------------------------------------------------------------


def test_decoy_event_fires_on_transition_only(tmp_path):
    decoy = tmp_path / "mighty_shared.db"
    present, events = fw._decoy_events(False, now=1, path=decoy)
    assert (present, events) == (False, [])
    decoy.write_text("")
    present, events = fw._decoy_events(False, now=2, path=decoy)
    assert present is True and len(events) == 1
    present, events = fw._decoy_events(True, now=3, path=decoy)
    assert present is True and events == []  # already noted
