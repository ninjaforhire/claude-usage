"""notify.py - fire-and-forget daemon alerting.

Two sinks, both best-effort, neither allowed to raise:

  1. POST to Jimbo's daemon-alert route (Telegram transport) through the shared
     authenticated client (_shared.integrations.jimbo_internal.post_internal_alert)
     run under Homebrew python3 in a subprocess, because that client needs 3.11+
     and the vault; it adds the X-Jimbo-Internal header, retries, and spools when
     Jimbo is down.
  2. A local macOS notification (osascript) so the alert is never fully lost
     even when Jimbo can't deliver it.

Alerting must NEVER crash its caller (the dashboard watcher), so every failure
is swallowed and reported as a False in the returned sink map.

Stdlib-only / py3.9 — the dashboard runs under system python3.9.
"""

import json
import os
import subprocess

JIMBO_ALERT_URL = "http://127.0.0.1:8324/internal/daemon-alert"
JIMBO_DIGEST_URL = "http://127.0.0.1:8324/internal/daemon-digest"
BRIDGE_PYTHON = "/opt/homebrew/bin/python3"
AGENTS_ROOT = os.path.join(os.path.expanduser("~"), "_Code", "mighty", "agents")
BRIDGE_TIMEOUT_S = 25
# Leave room below Jimbo's 16 KB limit for the signed spool envelope.
DIGEST_MAX_BYTES = 12 * 1024
_BRIDGE_SHIM = """import json, sys
sys.path.insert(0, sys.argv[1])
from _shared.integrations.jimbo_internal import post_internal_alert
ok = post_internal_alert(sys.argv[2], json.load(sys.stdin), timeout=float(sys.argv[3]))
sys.exit(0 if ok else 1)
"""


def _post_jimbo(payload, timeout=3, url=JIMBO_ALERT_URL):
    """Return shared-client durable acceptance, or False on any failure.

    The shared client reports durable acceptance when the alert was sent, held
    for the digest flush, or spooled.
    """
    try:
        result = subprocess.run(
            [BRIDGE_PYTHON, "-I", "-c", _BRIDGE_SHIM, AGENTS_ROOT, url, str(timeout)],
            input=json.dumps(payload).encode(),
            capture_output=True,
            timeout=BRIDGE_TIMEOUT_S,
        )
        return result.returncode == 0
    except Exception:
        return False


def _osa_escape(s):
    """Escape a string for safe embedding in an AppleScript double-quoted literal."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _osascript_notify(title, message):
    """Fire a local macOS notification. Returns True on success, False otherwise."""
    try:
        safe_msg = _osa_escape(message)
        safe_title = _osa_escape(title)
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'display notification "%s" with title "%s"' % (safe_msg, safe_title),
            ],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def alert(label, reasons, ts=None):
    """Alert that ``label`` went unhealthy. Hits both sinks; never raises.

    Args:
        label: launchd label of the offending daemon.
        reasons: list of human-readable reason strings from classify.
        ts: optional ISO timestamp of the detection.

    Returns:
        dict mapping each sink to whether it succeeded, e.g.
        ``{"jimbo": False, "osascript": True}``.
    """
    reason_text = "; ".join(reasons) if reasons else "unhealthy"
    payload = {
        "ts": ts,
        "label": label,
        "message": "Daemon %s: %s" % (label, reason_text),
        "reasons": reasons,
    }
    try:
        jimbo_ok = bool(_post_jimbo(payload))
    except Exception:
        jimbo_ok = False
    try:
        notify_ok = bool(_osascript_notify("Daemon alert: %s" % label, reason_text))
    except Exception:
        notify_ok = False
    return {"jimbo": jimbo_ok, "osascript": notify_ok}


def _fit_digest(title, lines, max_bytes=DIGEST_MAX_BYTES):
    """Bound the serialized digest while preserving the longest line prefix."""
    title = title if len(title) <= 200 else title[:197] + "..."
    lines = [line if len(line) <= 300 else line[:297] + "..." for line in lines]

    def size(kept):
        return len(json.dumps({"title": title, "lines": kept}).encode())

    if size(lines) <= max_bytes:
        return title, lines
    low, high = 0, len(lines) - 1
    best = None
    while low <= high:
        mid = (low + high) // 2
        marker = "... %d more line(s) not shown" % (len(lines) - mid)
        candidate = lines[:mid] + [marker]
        if size(candidate) <= max_bytes:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    if best is None:
        raise ValueError("digest limit is too small for title and marker")
    return title, best


def digest(title, lines):
    """Send the daily digest (one batched Telegram message via Jimbo).

    Jimbo-only sink — the digest is a summary, not an interrupt, so no
    osascript popup. Never raises.

    Returns:
        dict: ``{"jimbo": bool}``.
    """
    try:
        title, lines = _fit_digest(str(title), [str(line) for line in lines])
        payload = {"title": title, "lines": lines}
        jimbo_ok = bool(_post_jimbo(payload, url=JIMBO_DIGEST_URL))
    except Exception:
        jimbo_ok = False
    return {"jimbo": jimbo_ok}
