"""
registry.py - Load/save the git-tracked daemon registry (the "expected state" SoT).

JSON (not YAML) because the dashboard daemon runs under system python3.9, which has
no PyYAML. JSON keeps the whole tool stdlib-only.

Schema per entry:
  {
    "label": "com.mighty.design-center-pipeline",
    "purpose": "Design Center status pipeline",
    "owner": "andrew",
    "expected_state": "scheduled",   # enabled | scheduled | disabled
    "cost_tier": "opus",             # opus | sonnet | haiku | none
    "cwd_prefix": "tools/design-center-pipeline",  # usage.db join key, or null
    "eol_date": null                 # ISO date or null
  }
"""

import json
import os
from pathlib import Path

DEFAULT_REGISTRY = (
    Path.home() / "_Code" / "ops" / "daemon-registry" / "daemons.json"
)

# Single source of truth (2026-06-21). The registry lives ONLY in _Code
# (git-tracked, PR-reviewed). The former ~/.claude mirror existed to dodge macOS
# TCC, which blocked launchd daemons from reading ~/Desktop/_Code. After the
# 2026-05-20 reorg _Code lives at ~/_Code (a plain home subdir, NOT TCC-blocked),
# so launchd daemons read the canonical path directly and the mirror is obsolete.
# The old mirror PATH is kept only as a back-compat symlink -> canonical so no
# writer can ever desync the two again (that dual-write was the drift source).
LEGACY_MIRROR = Path.home() / ".claude" / "daemon-registry" / "daemons.json"

# Marker left by seed_manifest for fields the user still needs to fill in.
TODO = "TODO"

ANNOTATION_FIELDS = (
    "purpose",
    "owner",
    "expected_state",
    "cost_tier",
    "cwd_prefix",
    "eol_date",
    "ok_exit_codes",
    "heartbeat_file",
    "freshness_max_hours",
    # "critical" daemons get realtime Telegram pushes on WASTE transitions;
    # everything else lands in the daily digest only.
    "severity",
)


def registry_path():
    return Path(os.environ.get("DAEMON_REGISTRY", DEFAULT_REGISTRY))


def _read(path):
    with path.open() as fh:
        return {e["label"]: e for e in json.load(fh)}


def load(path=None):
    """Return {label: entry_dict} from the single git-tracked registry."""
    primary = Path(path) if path else registry_path()
    try:
        if primary.exists():
            return _read(primary)
    except (PermissionError, OSError):
        pass
    return {}


def _write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        json.dump(entries, fh, indent=2)
        fh.write("\n")


def save(entries_by_label, path=None):
    """Write the registry (sorted, pretty) to the single canonical path.

    The legacy ~/.claude path is a symlink to this file, so it reflects writes
    automatically — there is no second copy to refresh and therefore no drift.
    """
    primary = Path(path) if path else registry_path()
    if isinstance(entries_by_label, dict):
        entries = list(entries_by_label.values())
    else:
        entries = list(entries_by_label)
    entries.sort(key=lambda e: e["label"])
    _write(primary, entries)


def is_annotated(entry):
    """True once the user has filled in the expected_state (no longer TODO)."""
    return entry.get("expected_state") not in (None, TODO, "")
