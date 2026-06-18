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

# The canonical registry lives in _Code (git-tracked, PR-reviewed). But _Code is
# under ~/Desktop, which macOS TCC blocks launchd daemons from reading. The
# dashboard runs as a launchd daemon, so it reads this mirror instead (~/.claude is
# already daemon-readable - that's where usage.db lives). save() refreshes both.
MIRROR_REGISTRY = Path.home() / ".claude" / "daemon-registry" / "daemons.json"

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
    """Return {label: entry_dict}. Reads the canonical registry, falling back to the
    daemon-readable mirror when the canonical path is missing or TCC-blocked."""
    primary = Path(path) if path else registry_path()
    try:
        if primary.exists():
            return _read(primary)
    except (PermissionError, OSError):
        pass  # launchd daemon under TCC - fall through to the mirror
    if MIRROR_REGISTRY.exists():
        try:
            return _read(MIRROR_REGISTRY)
        except (PermissionError, OSError):
            pass
    return {}


def _write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        json.dump(entries, fh, indent=2)
        fh.write("\n")


def save(entries_by_label, path=None):
    """Write the registry (sorted, pretty) to the canonical path and refresh the
    daemon-readable mirror. Mirror failures are non-fatal."""
    primary = Path(path) if path else registry_path()
    if isinstance(entries_by_label, dict):
        entries = list(entries_by_label.values())
    else:
        entries = list(entries_by_label)
    entries.sort(key=lambda e: e["label"])
    _write(primary, entries)
    try:
        _write(MIRROR_REGISTRY, entries)
    except OSError:
        pass


def is_annotated(entry):
    """True once the user has filled in the expected_state (no longer TODO)."""
    return entry.get("expected_state") not in (None, TODO, "")
