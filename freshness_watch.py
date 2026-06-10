"""freshness_watch.py - background daemon-freshness watcher for the dashboard.

The dashboard is request-driven (it only computes a report when a client hits
the API), so a daemon could go stale for hours with nobody looking. This module
adds a daemon thread that runs classify.build_report() on an interval and fires
one notify.alert() per daemon that is in (or transitions into) a WASTE bucket —
deduped with a cooldown so a persistently-broken daemon does not spam.

Cooldown state persists across dashboard restarts in a small JSON file, so a
restart does not re-spam every currently-broken daemon.

Stdlib-only / py3.9 — the dashboard runs under system python3.9.
"""

import json
import sys
import threading
import time
from pathlib import Path

import classify
import notify

DEFAULT_STATE = Path.home() / ".claude" / "daemon-registry" / ".alerted.json"
COOLDOWN_S = 30 * 60
INTERVAL_S = 15 * 60


def _load_alerted(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def _save_alerted(path, data):
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


def check_and_alert(report, alerted, now, cooldown_s=COOLDOWN_S, notifier=notify.alert):
    """Alert on WASTE daemons and return the updated last-alerted map.

    A daemon alerts when it is WASTE and either has no recorded alert or its
    cooldown has elapsed. Recovered (non-WASTE) daemons have their entry cleared
    so a later regression re-alerts immediately.

    Pure: no I/O, no clock — ``now`` and ``notifier`` are injected. The thread
    loop in ``start_watcher`` supplies the real clock and persistence.
    """
    updated = dict(alerted)
    waste = {
        d["label"]: d
        for d in report.get("daemons", [])
        if d.get("bucket") == "WASTE"
    }
    # Clear recovered daemons so a future regression alerts at once.
    for label in list(updated):
        if label not in waste:
            updated.pop(label, None)
    for label, d in waste.items():
        first_seen = label not in updated
        if first_seen or now - updated[label] >= cooldown_s:
            notifier(label, d.get("reasons", []), ts=None)
            updated[label] = now
    return updated


def _tick(state_path):
    """One watcher cycle: build report, diff against state, alert, persist."""
    report = classify.build_report()
    alerted = _load_alerted(state_path)
    updated = check_and_alert(report, alerted, now=time.time())
    _save_alerted(state_path, updated)


def start_watcher(interval_s=INTERVAL_S, state_path=DEFAULT_STATE):
    """Spawn a daemon thread that runs _tick() every interval_s. Returns the
    Thread. Every cycle is wrapped so a transient failure never kills the loop."""
    def _loop():
        while True:
            try:
                _tick(state_path)
            except Exception as exc:  # noqa: BLE001
                # Never die, but never go silent either — a watcher that stops
                # working without a trace defeats its own purpose.
                print("freshness-watch tick failed: %s" % exc, file=sys.stderr)
            time.sleep(interval_s)

    t = threading.Thread(target=_loop, name="freshness-watch", daemon=True)
    t.start()
    return t
