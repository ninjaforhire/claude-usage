"""Behavior tests for notify.alert — fire-and-forget daemon alerting."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify


@pytest.fixture(autouse=True)
def disable_real_bridge(monkeypatch):
    """Ensure any unfaked bridge invocation fails without starting it."""
    monkeypatch.setattr(notify, "BRIDGE_PYTHON", "/nonexistent/claude-usage-test-python")


def test_alert_hits_both_sinks(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        notify,
        "_post_jimbo",
        lambda payload, **k: calls.setdefault("payload", payload) or True,
    )
    monkeypatch.setattr(
        notify,
        "_osascript_notify",
        lambda title, msg: calls.setdefault("osa", (title, msg)) or True,
    )

    result = notify.alert(
        "com.test.job", ["heartbeat stale (4h old)"], ts="2026-06-10T00:00:00Z"
    )

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
    result = notify.digest(
        "Daemon digest — 2 unresolved", ["UNRESOLVED com.a", "RECOVERED com.b"]
    )
    assert result == {"jimbo": True}
    assert seen["url"] == notify.JIMBO_DIGEST_URL
    assert seen["payload"]["title"].startswith("Daemon digest")
    assert len(seen["payload"]["lines"]) == 2


def test_digest_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(notify, "_post_jimbo", boom)
    assert notify.digest("t", ["x"]) == {"jimbo": False}


def test_digest_small_payload_unchanged(monkeypatch):
    seen = []
    monkeypatch.setattr(notify, "_post_jimbo", lambda payload, **kwargs: seen.append(payload) or True)
    assert notify.digest("Daily digest", ["first", "second"]) == {"jimbo": True}
    assert seen == [{"title": "Daily digest", "lines": ["first", "second"]}]


def test_digest_caps_payload_and_reports_dropped_lines(monkeypatch):
    seen = []
    monkeypatch.setattr(notify, "_post_jimbo", lambda payload, **kwargs: seen.append(payload) or True)
    lines = ["%04d" % i + "x" * 76 for i in range(2000)]
    assert notify.digest("Daily digest", lines) == {"jimbo": True}
    payload = seen[0]
    assert len(json.dumps(payload).encode()) <= notify.DIGEST_MAX_BYTES
    kept = payload["lines"][:-1]
    assert kept == lines[: len(kept)]
    assert payload["lines"][-1] == "... %d more line(s) not shown" % (len(lines) - len(kept))
    longer = {"title": payload["title"], "lines": lines[: len(kept) + 1] + [
        "... %d more line(s) not shown" % (len(lines) - len(kept) - 1)
    ]}
    assert len(json.dumps(longer).encode()) > notify.DIGEST_MAX_BYTES


def test_digest_truncates_long_line(monkeypatch):
    seen = []
    monkeypatch.setattr(notify, "_post_jimbo", lambda payload, **kwargs: seen.append(payload) or True)
    assert notify.digest("Daily digest", ["x" * 301]) == {"jimbo": True}
    assert seen[0]["lines"] == ["x" * 297 + "..."]


def test_post_jimbo_uses_bridge(monkeypatch):
    payload = {"x": 1}
    seen = {}

    class Result:
        returncode = 0

    def fake_run(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(notify.subprocess, "run", fake_run)

    assert notify._post_jimbo(payload) is True
    assert seen["args"] == (
        [
            notify.BRIDGE_PYTHON,
            "-I",
            "-c",
            notify._BRIDGE_SHIM,
            notify.AGENTS_ROOT,
            notify.JIMBO_ALERT_URL,
            "3",
        ],
    )
    assert json.loads(seen["kwargs"]["input"].decode()) == payload
    assert seen["kwargs"]["capture_output"] is True
    assert seen["kwargs"]["timeout"] == notify.BRIDGE_TIMEOUT_S


def test_post_jimbo_returns_false_on_bridge_failure(monkeypatch):
    class Result:
        returncode = 1

    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: Result())
    assert notify._post_jimbo({"x": 1}) is False


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("missing"),
        subprocess.TimeoutExpired(["bridge"], 25),
        OSError("bridge error"),
    ],
)
def test_post_jimbo_swallows_bridge_errors(monkeypatch, error):
    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(notify.subprocess, "run", boom)
    assert notify._post_jimbo({"x": 1}) is False


def test_digest_uses_digest_url_with_real_post_jimbo(monkeypatch):
    seen = {}

    class Result:
        returncode = 0

    def fake_run(*args, **kwargs):
        seen["args"] = args
        return Result()

    monkeypatch.setattr(notify.subprocess, "run", fake_run)

    assert notify.digest("Daemon digest", ["UNRESOLVED com.a"]) == {"jimbo": True}
    assert seen["args"][0][5] == notify.JIMBO_DIGEST_URL


def test_bridge_shim_contract(tmp_path):
    root = tmp_path / "agents"
    integration_dir = root / "_shared" / "integrations"
    integration_dir.mkdir(parents=True)
    (root / "_shared" / "__init__.py").write_text("")
    (integration_dir / "__init__.py").write_text("")
    record_path = integration_dir / "record.json"
    (integration_dir / "jimbo_internal.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "\n"
        "def post_internal_alert(url, payload, *, timeout):\n"
        "    Path(__file__).with_name('record.json').write_text(json.dumps({\n"
        "        'url': url, 'payload': payload, 'timeout': timeout\n"
        "    }))\n"
        "    return payload.get('ok_flag', True)\n"
    )
    url = "http://example.invalid/internal/daemon-alert"
    payload = {"label": "com.test.job"}

    result = subprocess.run(
        [sys.executable, "-I", "-c", notify._BRIDGE_SHIM, str(root), url, "3"],
        input=json.dumps(payload).encode(),
        capture_output=True,
    )

    assert result.returncode == 0
    assert json.loads(record_path.read_text()) == {
        "url": url,
        "payload": payload,
        "timeout": 3.0,
    }

    result = subprocess.run(
        [sys.executable, "-I", "-c", notify._BRIDGE_SHIM, str(root), url, "3"],
        input=json.dumps({"ok_flag": False}).encode(),
        capture_output=True,
    )

    assert result.returncode == 1


def test_osascript_returns_false_on_nonzero(monkeypatch):
    class _R:
        returncode = 1

    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: _R())
    assert notify._osascript_notify("t", "m") is False


def test_osa_escape_neutralizes_quotes_and_newlines():
    out = notify._osa_escape('say "hi"\nthen')
    assert "\n" not in out
    assert '\\"' in out  # double-quotes are backslash-escaped
