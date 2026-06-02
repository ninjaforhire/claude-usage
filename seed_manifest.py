"""
seed_manifest.py - Generate / refresh the daemon registry from current plists.

Idempotent: new plists are added with TODO annotations; existing annotations are
never clobbered. Run after adding/removing LaunchAgents.

  python3 seed_manifest.py            # merge into the default registry
  python3 seed_manifest.py --dry-run  # print what would change, write nothing
"""

import sys

import daemons as daemons_mod
import registry as registry_mod


def _guess_cwd_prefix(working_dir):
    """Best-effort cwd_prefix from a plist WorkingDirectory under _Code."""
    if not working_dir:
        return None
    marker = "_Code/"
    idx = working_dir.find(marker)
    if idx == -1:
        return None
    return working_dir[idx + len(marker):].strip("/") or None


def build_seed_entry(daemon):
    """A fresh registry entry with auto-filled facts + TODO annotations."""
    return {
        "label": daemon["label"],
        "purpose": registry_mod.TODO,
        "owner": registry_mod.TODO,
        "expected_state": registry_mod.TODO,  # enabled | scheduled | disabled
        "cost_tier": registry_mod.TODO,        # opus | sonnet | haiku | none
        "cwd_prefix": _guess_cwd_prefix(daemon.get("working_dir")),
        "eol_date": None,
        # Non-authoritative hints to make annotation easier (auto-refreshed).
        "_schedule": daemon.get("schedule"),
        "_program": " ".join(daemon.get("program_args") or [])[:200],
        "_disabled_file": daemon.get("disabled_file", False),
    }


def merge(existing_by_label, daemons):
    """Return (merged_by_label, added_labels). Preserves user annotations."""
    merged = dict(existing_by_label)
    added = []
    for d in daemons:
        label = d["label"]
        if label in merged:
            # Refresh the non-authoritative hints only.
            merged[label]["_schedule"] = d.get("schedule")
            merged[label]["_program"] = " ".join(d.get("program_args") or [])[:200]
            merged[label]["_disabled_file"] = d.get("disabled_file", False)
        else:
            merged[label] = build_seed_entry(d)
            added.append(label)
    return merged, added


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    dry_run = "--dry-run" in argv

    found = daemons_mod.gather()
    existing = registry_mod.load()
    merged, added = merge(existing, found)

    print(f"Scanned {len(found)} plists; registry had {len(existing)} entries.")
    if added:
        print(f"New entries needing annotation ({len(added)}):")
        for label in sorted(added):
            print(f"  + {label}")
    else:
        print("No new daemons.")

    if dry_run:
        print("\n--dry-run: nothing written.")
        return

    registry_mod.save(merged)
    print(f"\nWrote {len(merged)} entries to {registry_mod.registry_path()}")
    print("Annotate the TODO fields (expected_state, cost_tier, purpose), then commit.")


if __name__ == "__main__":
    main()
