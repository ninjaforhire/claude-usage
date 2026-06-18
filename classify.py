"""
classify.py - Merge daemons with the registry, bucket them, and emit remediation.

Buckets:
  HEALTHY - declared, in its expected state, no failures.
  WASTE   - loaded but expected disabled / repeated nonzero exit / cost-tier daemon
            idle 30d / past EOL date.
  UNKNOWN - a plist with no registry annotation (needs the user to declare it).
  ROGUE   - (processes, not plists) handled by processes.find_rogues.

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


def _bootout_cmd(label):
    uid = os.getuid()
    return f"launchctl bootout gui/{uid}/{label}"


def classify_daemon(d):
    """Return (bucket, reasons[], remediation|None) for one merged daemon dict."""
    reasons = []
    expected = d.get("expected_state")
    annotated = expected not in (None, registry_mod.TODO, "")

    if not annotated:
        return ("UNKNOWN", ["not declared in daemons.json"], None)

    # Past end-of-life.
    eol = d.get("eol_date")
    if eol:
        try:
            if date.fromisoformat(str(eol)) < date.today():
                reasons.append(f"past EOL ({eol})")
        except ValueError:
            pass

    # Declared off but actually loaded.
    if expected == "disabled" and d.get("loaded"):
        reasons.append("declared disabled but currently loaded")

    # Repeated failures (nonzero last exit on a loaded job).
    # ok_exit_codes lets a daemon declare nonzero exits that are healthy
    # signals (e.g. mission-watchdog exits 2 when it fires alerts).
    le = d.get("last_exit")
    ok_exits = d.get("ok_exit_codes") or []
    if le not in (None, 0) and le not in ok_exits:
        reasons.append(f"last exit code {le}")

    # Cost-tier daemon with no measured activity in 30 days.
    # Only a real, non-mixed cwd_prefix yields an attributable measurement. A
    # null/mixed prefix means the daemon's claude -p cost lands in the shared
    # _Code bucket (e.g. code-council, audit-templates) and is not
    # attributable, so zero measured turns is NOT evidence of death. Liveness
    # for those is a separate signal (last_run), not cost attribution.
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
    # run. This catches the silent-no-op class (exits 0 but did nothing) that
    # stdout/stderr-mtime liveness misses — the failure mode behind the
    # 2026-06-09 notion-sync outage. Generic: any daemon that declares a
    # heartbeat_file gets freshness coverage for free.
    hb = d.get("heartbeat_file")
    if hb and expected in ("enabled", "scheduled"):
        # Coerce defensively: a string like "6" or "TODO" in the registry must
        # not crash the whole report with a float-vs-str TypeError.
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
        return ("WASTE", reasons, _bootout_cmd(d["label"]))
    return ("HEALTHY", [], None)


def build_report(agents_dir=None, db_path=None, registry_file=None):
    """Full report dict consumed by the dashboard API and the CLI.

    {
      "daemons": [ {label, bucket, reasons, remediation, schedule, loaded,
                    last_exit, cost_7d, cost_30d, cost_mixed, purpose, ...}, ... ],
      "rogues":  [ {pid, command, reasons, remediation, cpu, ...}, ... ],
      "counts":  {HEALTHY, WASTE, UNKNOWN, ROGUE},
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

    counts = {"HEALTHY": 0, "WASTE": 0, "UNKNOWN": 0, "ROGUE": 0}
    for d in merged:
        bucket, reasons, remediation = classify_daemon(d)
        d["bucket"] = bucket
        d["reasons"] = reasons
        d["remediation"] = remediation
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
