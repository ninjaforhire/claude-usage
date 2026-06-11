"""Behavior tests for notify.alert — fire-and-forget daemon alerting."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify


def test_alert_hits_both_sinks(monkeypatch):
    calls = {}
    monkeypatch.setattr(notify, "_post_jimbo", lambda payload, **k: calls.setdefault("payload", payload) or True)
    monkeypatch.setattr(notify, "_osascript_notify", lambda title, msg: calls.setdefault("osa", (title, msg)) or True)

    result = notify.alert("com.test.job", ["heartbeat stale (4h old)"], ts="2026-06-10T00:00:00Z")

    assert result == {"jimbo": True, "osascript": True}
    assert calls["payload"]["label"] == "com.test.job"
    assert "stale" in calls["payload"]["message"]
    assert calls["osa"][0].endswith("com.test.job")


def test_alert_never_raises_when_both_fail(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(notify, "_post_jimbo", boom)
    monkeypatch.setattr(notify, "_osascript_notify", boom)

    # alert() must swallow everything — alerting can never crash the watcher.
    result = notify.alert("com.test.job", ["x"])
    assert result["jimbo"] is False
    assert result["osascript"] is False


def test_digest_posts_to_digest_url(monkeypatch):
    seen = {}

    def fake_post(payload, timeout=3, url=notify.JIMBO_ALERT_URL):
        seen["url"] = url
        seen["payload"] = payload
        return True

    monkeypatch.setattr(notify, "_post_jimbo", fake_post)
    result = notify.digest("Daemon digest — 2 unresolved", ["UNRESOLVED com.a", "RECOVERED com.b"])
    assert result == {"jimbo": True}
    assert seen["url"] == notify.JIMBO_DIGEST_URL
    assert seen["payload"]["title"].startswith("Daemon digest")
    assert len(seen["payload"]["lines"]) == 2


def test_digest_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(notify, "_post_jimbo", boom)
    assert notify.digest("t", ["x"]) == {"jimbo": False}


def test_post_jimbo_swallows_network_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    assert notify._post_jimbo({"x": 1}) is False


def test_osascript_returns_false_on_nonzero(monkeypatch):
    class _R:
        returncode = 1

    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: _R())
    assert notify._osascript_notify("t", "m") is False


def test_osa_escape_neutralizes_quotes_and_newlines():
    out = notify._osa_escape('say "hi"\nthen')
    assert "\n" not in out
    assert '\\"' in out  # double-quotes are backslash-escaped
