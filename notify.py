"""notify.py - fire-and-forget daemon alerting.

Two sinks, both best-effort, neither allowed to raise:

  1. POST to Jimbo's daemon-alert route (Telegram transport). Fire-and-forget,
     3s timeout. If the route is absent or Jimbo is down the POST simply fails.
  2. A local macOS notification (osascript) so the alert is never fully lost
     even when Jimbo can't deliver it.

Alerting must NEVER crash its caller (the dashboard watcher), so every failure
is swallowed and reported as a False in the returned sink map.

Stdlib-only / py3.9 — the dashboard runs under system python3.9.
"""

import json
import subprocess
import urllib.request

JIMBO_ALERT_URL = "http://127.0.0.1:8324/internal/daemon-alert"


def _post_jimbo(payload, timeout=3):
    """POST the alert to Jimbo. Returns True on 2xx, False on any failure."""
    try:
        req = urllib.request.Request(
            JIMBO_ALERT_URL,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def _osascript_notify(title, message):
    """Fire a local macOS notification. Returns True on success, False otherwise."""
    try:
        safe_msg = message.replace('"', "'")
        safe_title = title.replace('"', "'")
        subprocess.run(
            [
                "osascript",
                "-e",
                'display notification "%s" with title "%s"' % (safe_msg, safe_title),
            ],
            capture_output=True,
            timeout=5,
        )
        return True
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
