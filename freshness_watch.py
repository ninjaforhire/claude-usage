"""freshness_watch.py - background daemon-freshness watcher for the dashboard.

Transition-only alerting: a daemon alerts when it ENTERS a WASTE state (or its
failure signature changes) — never again while the issue is unchanged. A
persistently-broken daemon shows up in the daily digest until fixed instead of
re-alerting every cooldown. Realtime pushes are reserved for daemons whose
registry entry carries severity == "critical"; everything else is digest-only.

State persists across dashboard restarts in .alerted.json:

  {"<label>": {"sig": str, "first_seen": float, "last_alert": float},
   "_digest": {"last_sent": float},
   "_decoy": bool}

Legacy entries (bare float = last-alert timestamp from the cooldown era) are
migrated on load with an empty signature, so the first post-upgrade tick
re-evaluates each daemon without a realtime re-spam (non-critical issues go to
the digest).

Digest events accumulate in digest-queue.jsonl — an append-only JSONL file that
is the shared producer contract for the watcher, the mission watchdog, and
Jimbo's self-repair sweep. Events drain into one daily digest message sent via
Jimbo after DIGEST_HOUR local time.

Stdlib-only / py3.9 — the dashboard runs under system python3.9.
"""

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import classify
import notify

DEFAULT_STATE = Path.home() / ".claude" / "daemon-registry" / ".alerted.json"
QUEUE_PATH = Path.home() / ".claude" / "daemon-registry" / "digest-queue.jsonl"
# Legacy shared-memory DB location retired by the 2026-06-10 unification. Any
# file reappearing here means a stale process still resolves the old path.
LEGACY_DB_DECOY = (
    Path.home()
    / "Desktop"
    / "_Code"
    / "agents"
    / "_shared"
    / "memory"
    / "db"
    / "mighty_shared.db"
)
INTERVAL_S = 15 * 60
DIGEST_PERIOD_S = int(23.5 * 3600)  # daily, with slack so send time can't creep
DIGEST_HOUR = 8  # earliest local hour to send the daily digest

# Reasons carry volatile numbers ("heartbeat stale (7h old)") that must not
# count as a new issue every tick. Exit codes stay — a changed code IS news.
_VOLATILE = re.compile(r"\(\d+h old\)")

_RESERVED = ("_digest", "_decoy")


def issue_signature(reasons):
    """Stable identity for a daemon's current failure mode."""
    normalized = sorted(_VOLATILE.sub("()", r) for r in reasons)
    return "|".join(normalized) or "unhealthy"


def _load_state(path):
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    state = {}
    for key, value in raw.items():
        if key in _RESERVED or isinstance(value, dict):
            state[key] = value
        else:
            # Legacy cooldown-era schema: bare last-alert timestamp. Empty sig
            # means the next tick treats the issue as new — but routes it to
            # the digest unless critical, so migration does not re-spam.
            state[key] = {"sig": "", "first_seen": value, "last_alert": value}
    return state


def _save_state(path, data):
    # Atomic temp+rename: a crash or a second dashboard instance must never leave
    # a truncated .alerted.json (which would reset dedupe and spam alerts).
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name("%s.%d.tmp" % (p.name, os.getpid()))
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(p)
    except OSError:
        pass


def check_and_alert(report, alerted, now, notifier=notify.alert):
    """Transition-only alert pass over one classify report.

    Returns ``(updated, events)`` where ``updated`` is the new per-label state
    map and ``events`` are digest-queue dicts describing what changed.

    - WASTE with no entry, or with a changed signature → one event; realtime
      ``notifier`` fires ONLY when the daemon's registry severity is critical.
    - WASTE with an unchanged signature → silent (the digest carries it).
    - recovered (left WASTE) → entry cleared + a recovery event, so a later
      regression alerts as new.

    Pure: no I/O, no clock — ``now`` and ``notifier`` are injected. The thread
    loop in ``start_watcher`` supplies the real clock and persistence.
    """
    updated = {k: dict(v) for k, v in alerted.items()}
    events = []
    waste = {
        d["label"]: d
        for d in report.get("daemons", [])
        if d.get("bucket") in classify.ALERT_BUCKETS
    }
    for label in list(updated):
        if label not in waste:
            updated.pop(label, None)
            events.append({"type": "recovered", "label": label, "ts": now})
    for label, d in sorted(waste.items()):
        sig = issue_signature(d.get("reasons", []))
        prev = updated.get(label)
        if prev is not None and prev.get("sig") == sig:
            continue  # known, unchanged — digest covers it
        critical = d.get("severity") == "critical"
        if critical:
            notifier(label, d.get("reasons", []), ts=None)
        events.append(
            {
                "type": "issue",
                "label": label,
                "reasons": d.get("reasons", []),
                "critical": critical,
                "ts": now,
            }
        )
        updated[label] = {
            "sig": sig,
            "first_seen": prev["first_seen"] if prev else now,
            "last_alert": now if critical else (prev or {}).get("last_alert", 0),
        }
    return updated, events


def append_events(events, path=QUEUE_PATH):
    """Append digest events as JSONL. Shared producer contract — the mission
    watchdog and self-repair write the same shape. Single-write lines keep
    concurrent appends safe enough for this volume."""
    if not events:
        return
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")
    except OSError:
        pass


def drain_queue(path=QUEUE_PATH):
    """Atomically take all queued events (rename then read, so concurrent
    producers keep appending to a fresh file)."""
    p = Path(path)
    if not p.exists():
        return []
    draining = p.with_name(p.name + ".draining")
    try:
        p.replace(draining)
    except OSError:
        return []
    events = []
    try:
        for line in draining.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    try:
        draining.unlink()
    except OSError:
        pass
    return events


def build_digest(report, state, events, now):
    """Compose the daily digest. Returns (title, lines); empty lines = nothing
    worth sending today."""
    lines = []
    waste = sorted(
        (d for d in report.get("daemons", []) if d.get("bucket") in classify.ALERT_BUCKETS),
        key=lambda d: d["label"],
    )
    for d in waste:
        entry = state.get(d["label"]) or {}
        age_h = max(0.0, (now - entry.get("first_seen", now)) / 3600.0)
        reasons = "; ".join(_VOLATILE.sub("()", r) for r in d.get("reasons", []))
        lines.append("UNRESOLVED %s — %s (%.0fh)" % (d["label"], reasons, age_h))
    seen_recovered = set()
    for e in events:
        etype = e.get("type")
        if etype == "recovered":
            label = e.get("label", "?")
            if label not in seen_recovered:
                seen_recovered.add(label)
                lines.append("RECOVERED %s" % label)
        elif etype == "mission_failed":
            lines.append(
                "MISSION FAIL %s — %s" % (e.get("name", "?"), e.get("error", "?"))
            )
        elif etype == "repaired":
            lines.append("SELF-REPAIRED %s" % (e.get("target") or e.get("label", "?")))
        elif etype == "repair_proposal":
            lines.append(
                "REPAIR PROPOSAL %s — %s"
                % (e.get("target", "?"), e.get("root_cause", "?"))
            )
        elif etype == "note":
            lines.append(str(e.get("line", "")))
        # "issue" events are already covered by UNRESOLVED (still broken) or
        # RECOVERED (transient) lines — skip to keep the digest tight.
    title = "Daemon digest — %d unresolved" % len(waste)
    return title, [l for l in lines if l]


def digest_due(last_sent, now, localtime=time.localtime):
    return (now - last_sent) >= DIGEST_PERIOD_S and localtime(now).tm_hour >= DIGEST_HOUR


def _decoy_events(decoy_prev, now, path=LEGACY_DB_DECOY):
    """T4 tripwire: legacy memory DB path must stay absent. Transition-only."""
    try:
        present = Path(path).exists()
    except OSError:
        present = False
    events = []
    if present and not decoy_prev:
        events.append(
            {
                "type": "note",
                "line": "legacy memory decoy reappeared: %s — a stale process "
                "still resolves the old path (lsof the file to catch it)" % path,
                "ts": now,
            }
        )
    return present, events


def _tick(state_path=DEFAULT_STATE, queue_path=QUEUE_PATH, now=None):
    """One watcher cycle: classify, transition-diff, queue, maybe digest."""
    if now is None:
        now = time.time()
    report = classify.build_report()
    state = _load_state(state_path)
    digest_meta = state.pop("_digest", None) or {}
    decoy_prev = bool(state.pop("_decoy", False))

    updated, events = check_and_alert(report, state, now=now)
    decoy_now, decoy_evts = _decoy_events(decoy_prev, now)
    events.extend(decoy_evts)
    append_events(events, queue_path)

    if digest_due(digest_meta.get("last_sent", 0), now):
        drained = drain_queue(queue_path)
        title, lines = build_digest(report, updated, drained, now)
        if lines:
            notify.digest(title, lines)
        digest_meta["last_sent"] = now

    persisted = dict(updated)
    persisted["_digest"] = digest_meta
    persisted["_decoy"] = decoy_now
    _save_state(state_path, persisted)


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


HEARTBEAT_PATH = Path.home() / ".claude" / "daemon-registry" / "claude_usage_freshness_health.json"


def run_once(state_path=DEFAULT_STATE):
    """Run exactly one watcher cycle and write a heartbeat receipt.

    The watcher used to live inside the :8080 dashboard process (via
    start_watcher's daemon thread). With :8080 retired in favor of Jimbo's
    /usage tab, the launchd maintenance tick calls this every 15 min instead,
    so daemon-health alerting never goes silent. Returns True on success.
    """
    from datetime import datetime, timezone

    ok, err = True, None
    try:
        _tick(state_path)
    except Exception as exc:  # noqa: BLE001 — record failure, never raise
        ok, err = False, str(exc)
        print("freshness run_once failed: %s" % exc, file=sys.stderr)
    try:
        HEARTBEAT_PATH.write_text(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "ok": ok,
            "error": err,
        }))
    except OSError:
        pass
    return ok
