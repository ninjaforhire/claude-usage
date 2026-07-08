"""
classify.py - Merge daemons with the registry, bucket them, and emit remediation.

Buckets (a daemon lands in exactly one):
  HEALTHY        - declared, in its expected state, no failures.
  BROKEN         - a declared, WANTED daemon that is failing (nonzero exit on a
                   scheduled job, stale/missing/failed heartbeat, cost-tier idle
                   30d). Fix it — do NOT bootout. This is the "broken but wanted"
                   class that used to be mislabeled WASTE.
  DISABLED-DRIFT - declared disabled but currently loaded. Bootout resolves it.
  WASTE          - genuinely should not exist: past its EOL date. Safe to remove.
  VENDOR-IGNORE  - third-party helper (Adobe / Steam / Samsung / Google Keystone /
                   Elgato / Canva). App-managed, re-registers itself; never MIGHTY
                   waste. Surfaced for visibility only.
  UNDECLARED     - a plist with no registry annotation. Action = declare it in
                   daemons.json, not "investigate".
  ROGUE          - (processes, not plists) handled by processes.find_rogues.

Exit-code nuance: -15 (SIGTERM) is a normal launchd stop/restart and never a
failure. A KeepAlive daemon that is currently loaded with a live PID is running
NOW, so a nonzero *last* exit is a historical restart artifact, not a fault.

Report-only: every finding carries the exact command, but nothing is executed.
"""

import json
import os
import time
from datetime import date

import daemons as daemons_mod
import processes as processes_mod
import registry as registry_mod
from attribution import attribute

# Third-party helpers whose plists their own apps install and re-install. We
# neither own nor permanently bootout these — a bootout is cosmetic because the
# app re-adds the job on its next launch/update. Never WASTE.
VENDOR_PREFIXES = (
    "com.adobe.",
    "com.valvesoftware.",
    "com.samsung.",
    "com.google.keystone.",
    "com.google.GoogleUpdater",
    "com.elgato.",
    "com.canva.",
)

# Claude-tier daemons run `claude -p` on the ClaudeMax subscription. Their
# "cost" is subscription-token attribution, NOT billed API dollars.
CLAUDE_TIERS = ("opus", "sonnet", "haiku")

# The bucket set the freshness watcher alerts on (actionable). VENDOR-IGNORE and
# UNDECLARED are surfaced but never paged.
ALERT_BUCKETS = ("WASTE", "BROKEN", "DISABLED-DRIFT")

COUNT_BUCKETS = (
    "HEALTHY",
    "BROKEN",
    "DISABLED-DRIFT",
    "WASTE",
    "VENDOR-IGNORE",
    "UNDECLARED",
    "ROGUE",
)


def _bootout_cmd(label):
    uid = os.getuid()
    return f"launchctl bootout gui/{uid}/{label}"


def _is_vendor(label):
    return any(label.startswith(p) for p in VENDOR_PREFIXES)


def classify_daemon(d):
    """Return (bucket, reasons[], remediation|None) for one merged daemon dict."""
    reasons = []
    label = d.get("label", "")
    expected = d.get("expected_state")
    annotated = expected not in (None, registry_mod.TODO, "")
    schedule = d.get("schedule") or ""
    is_keepalive = schedule.startswith("always-on")

    # Vendor helpers first — they can be "declared disabled but loaded" (drift),
    # but that is cosmetic, not MIGHTY waste, so short-circuit before WASTE logic.
    if _is_vendor(label):
        if expected == "disabled" and d.get("loaded"):
            # No remediation: bootout is cosmetic (the app re-adds the job), and
            # surfacing the command would invite exactly the pointless action the
            # VENDOR-IGNORE bucket exists to discourage.
            return (
                "VENDOR-IGNORE",
                ["vendor helper loaded despite disabled decl — bootout is cosmetic (its app re-adds it)"],
                None,
            )
        return (
            "VENDOR-IGNORE",
            ["third-party vendor helper (app-managed, not MIGHTY-owned)"],
            None,
        )

    if not annotated:
        return (
            "UNDECLARED",
            ["not declared in daemons.json — add an entry to classify it"],
            None,
        )

    # Past end-of-life → genuine WASTE (retired, safe to remove).
    eol = d.get("eol_date")
    if eol:
        try:
            if date.fromisoformat(str(eol)) < date.today():
                return (
                    "WASTE",
                    [f"past EOL ({eol}) — retired, safe to remove"],
                    _bootout_cmd(label),
                )
        except ValueError:
            pass

    # Declared off but actually loaded → its own DISABLED-DRIFT state; bootout
    # resolves it cleanly. (Separate from WASTE so the dashboard can tell "left
    # running by mistake" apart from "should not exist at all".)
    if expected == "disabled" and d.get("loaded"):
        return (
            "DISABLED-DRIFT",
            ["declared disabled but currently loaded"],
            _bootout_cmd(label),
        )

    # Repeated failures (nonzero last exit). ok_exit_codes lets a daemon declare
    # nonzero exits that are healthy signals (e.g. mission-watchdog exits 2 when
    # it fires alerts). -15 (SIGTERM) is ALWAYS ok — it's a normal launchd
    # stop/restart. A KeepAlive daemon currently loaded with a live PID is
    # running NOW, so its last nonzero exit is a prior restart artifact, not a
    # fault (this is the forge-tunnel / server-reconnect false-positive class).
    # Runtime failure checks apply only to WANTED daemons (enabled/scheduled). A
    # declared-disabled daemon that is merely off must never become BROKEN off a
    # stale historical exit — BROKEN is reserved for daemons we expect to run.
    wanted = expected in ("enabled", "scheduled")
    le = d.get("last_exit")
    ok_exits = set(d.get("ok_exit_codes") or [])
    ok_exits.add(-15)
    keepalive_running = is_keepalive and d.get("pid") is not None
    if wanted and le not in (None, 0) and le not in ok_exits and not keepalive_running:
        reasons.append(f"last exit code {le}")

    # Cost-tier daemon with no measured activity in 30 days. Only a real,
    # non-mixed cwd_prefix yields an attributable measurement; a null/mixed
    # prefix means the spend lands in the shared _Code bucket and zero measured
    # turns is NOT evidence of death.
    prefix = d.get("cwd_prefix")
    attributable = prefix not in (None, "", registry_mod.TODO) and not d.get("cost_mixed")
    if (
        d.get("cost_tier") not in (None, "none", registry_mod.TODO)
        and attributable
        and (d.get("turns_30d") or 0) == 0
        and expected != "disabled"
    ):
        reasons.append("no usage.db activity in 30d (possible dead spend)")

    # Heartbeat freshness. A daemon that writes a per-run receipt is stale when
    # the file is missing, older than its freshness budget, or records a failed
    # run. Catches the silent-no-op class (exits 0 but did nothing).
    hb = d.get("heartbeat_file")
    if hb and expected in ("enabled", "scheduled"):
        try:
            max_h = float(d.get("freshness_max_hours") or 6)
        except (TypeError, ValueError):
            max_h = 6
        try:
            age_h = (time.time() - os.path.getmtime(hb)) / 3600.0
        except OSError:
            reasons.append("heartbeat missing")
        else:
            if age_h > max_h:
                reasons.append("heartbeat stale (%dh old)" % round(age_h))
            else:
                try:
                    with open(hb) as fh:
                        rec = json.load(fh)
                except (OSError, ValueError):
                    reasons.append("heartbeat unreadable")
                else:
                    if rec.get("ok") is False:
                        reasons.append("last run failed: %s" % rec.get("error"))

    if reasons:
        # A declared, WANTED daemon that is failing is BROKEN, not WASTE. The
        # fix is to repair it (dashboard "Fix →" runs /fix-daemon), not bootout,
        # so remediation is None here.
        return ("BROKEN", reasons, None)
    return ("HEALTHY", [], None)


def build_report(agents_dir=None, db_path=None, registry_file=None):
    """Full report dict consumed by the dashboard API and the CLI.

    {
      "daemons": [ {label, bucket, reasons, remediation, schedule, loaded,
                    last_exit, cost_7d, cost_30d, cost_mixed, purpose, ...}, ... ],
      "rogues":  [ {pid, command, reasons, remediation, cpu, ...}, ... ],
      "counts":  {HEALTHY, BROKEN, DISABLED-DRIFT, WASTE, VENDOR-IGNORE,
                  UNDECLARED, ROGUE},
      "registry_path": str,
    }
    """
    kwargs = {}
    if agents_dir:
        kwargs["agents_dir"] = agents_dir
    merged = daemons_mod.gather(**kwargs)

    reg = registry_mod.load(registry_file)
    for d in merged:
        entry = reg.get(d["label"], {})
        for f in registry_mod.ANNOTATION_FIELDS:
            d.setdefault(f, entry.get(f))
        # registry values win over the setdefault placeholder
        for f in registry_mod.ANNOTATION_FIELDS:
            if f in entry:
                d[f] = entry[f]

    attribute(merged, db_path=db_path) if db_path else attribute(merged)

    counts = {b: 0 for b in COUNT_BUCKETS}
    for d in merged:
        bucket, reasons, remediation = classify_daemon(d)
        d["bucket"] = bucket
        d["reasons"] = reasons
        d["remediation"] = remediation
        # Flag claude -p subscription-token spend so the UI never renders it as
        # billed API dollars.
        d["cost_subscription"] = d.get("cost_tier") in CLAUDE_TIERS
        counts[bucket] += 1

    rogues = processes_mod.find_rogues()
    for r in rogues:
        r["bucket"] = "ROGUE"
    counts["ROGUE"] = len(rogues)

    return {
        "daemons": merged,
        "rogues": rogues,
        "counts": counts,
        "registry_path": str(registry_mod.registry_path()),
    }
