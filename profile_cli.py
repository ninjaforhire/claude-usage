"""Credential-free local account profile CLI helpers."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional


def _account_store_from_args(value: Optional[str], testing: bool = False) -> Path:
    """Return an explicit, standard, or isolated testing profile store path."""
    if value:
        return Path(value).expanduser()
    from account_profiles import DEFAULT_STORE_PATH, TESTING_STORE_PATH

    return TESTING_STORE_PATH if testing else DEFAULT_STORE_PATH


def _account_parser() -> argparse.ArgumentParser:
    """Build the local-only account command parser."""
    parser = argparse.ArgumentParser(
        prog="python cli.py accounts",
        description="Manage local-only account profiles outside this repository.",
    )
    parser.add_argument("--store", help="Local profile registry path")
    parser.add_argument(
        "--testing",
        action="store_true",
        help="Use the isolated first-run testing registry only",
    )
    commands = parser.add_subparsers(dest="action", required=True)
    add = commands.add_parser(
        "add", help="Add a local account profile without credentials"
    )
    add.add_argument("--id", required=True, help="Stable local profile identifier")
    add.add_argument("--label", required=True, help="Dashboard label")
    add.add_argument("--claude-email", help="Expected active Claude account email")
    add.add_argument("--codex-email", help="Expected active Codex account email")
    add.add_argument("--tier", default="Max 20x", help="Non-secret plan label")
    add.add_argument(
        "--inactive", action="store_true", help="Mark this profile inactive"
    )
    snapshot = commands.add_parser("snapshot", help="Save the currently active account")
    snapshot.add_argument(
        "--profile", required=True, help="Existing local profile identifier"
    )
    snapshot.add_argument(
        "--provider", choices=("claude", "codex", "all"), default="all"
    )
    snapshot.add_argument(
        "--claude-helper",
        help="Explicit trusted helper for Claude usage windows; never downloaded",
    )
    commands.add_parser(
        "list", help="List local labels and snapshot state without emails"
    )
    setup = commands.add_parser(
        "setup", help="Safely add the currently signed-in Claude and/or Codex account"
    )
    setup.add_argument("--label", help="Bold dashboard name for this account")
    setup.add_argument(
        "--providers",
        choices=("claude", "codex", "all"),
        default="all",
        help="Providers to inspect from official local CLIs (default: all)",
    )
    setup.add_argument(
        "--tier",
        help="Optional non-secret plan label when the official CLI does not report one",
    )
    reset = commands.add_parser(
        "reset", help="Clear only the isolated first-run testing profiles"
    )
    reset.add_argument(
        "--yes",
        action="store_true",
        help="Confirm clearing the isolated testing registry",
    )
    commands.add_parser(
        "samples", help="Replace isolated testing state with fake sample accounts"
    )
    return parser


def _profile_by_id(registry: dict, profile_id: str) -> dict:
    """Find one local profile or raise a safe CLI error."""
    for profile in registry.get("profiles", []):
        if profile.get("id") == profile_id:
            return profile
    raise ValueError(f"Unknown local account profile: {profile_id}")


def _profile_id_for_label(label: str, registry: dict) -> str:
    """Create a stable, non-secret local profile id from a display label."""
    base = re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-") or "account"
    base = base[:56].rstrip("-") or "account"
    existing = {str(profile.get("id", "")) for profile in registry.get("profiles", [])}
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base[:60 - len(str(suffix))]}-{suffix}"
        suffix += 1
    return candidate


def _setup_label(value: Optional[str], parser: argparse.ArgumentParser) -> str:
    """Return a safe display label, asking only when running interactively."""
    label = value.strip() if isinstance(value, str) else ""
    if not label and sys.stdin.isatty():
        try:
            label = input(
                "Account name to show in bold (for example, Work Max): "
            ).strip()
        except EOFError:
            label = ""
    if not label:
        parser.error("setup requires --label when it cannot prompt interactively")
    if len(label) > 100:
        parser.error("setup account name must be 100 characters or fewer")
    return label


def cmd_accounts(arguments: list[str]) -> None:
    """Manage untracked profiles and snapshot only the interactive login."""
    from account_profiles import (
        add_profile,
        load_registry,
        record_snapshot,
        reset_testing_registry,
        save_registry,
        seed_testing_registry,
    )
    from connectors.claude_subscription import read_subscription as read_claude
    from connectors.codex_subscription import read_subscription as read_codex

    parser = _account_parser()
    args = parser.parse_args(arguments)
    mode = "testing" if args.testing else "standard"
    store = _account_store_from_args(args.store, testing=args.testing)
    if args.action == "reset":
        if not args.testing:
            parser.error(
                "reset requires --testing so normal account profiles cannot be cleared"
            )
        if not args.yes:
            parser.error(
                "reset requires --yes to confirm clearing isolated testing profiles"
            )
        reset_testing_registry(store)
        print(
            "Reset isolated first-run testing profiles. Normal account profiles were not touched."
        )
        return
    if args.action == "samples":
        if not args.testing:
            parser.error(
                "samples requires --testing so normal account profiles cannot be replaced"
            )
        seed_testing_registry(store)
        print("Loaded fake sample accounts into isolated first-run testing profiles.")
        return
    registry = load_registry(store, mode=mode)
    if args.action == "setup":
        label = _setup_label(args.label, parser)
        targets = ("claude", "codex") if args.providers == "all" else (args.providers,)
        snapshots: dict[str, dict] = {}
        for provider in targets:
            snapshot = read_claude() if provider == "claude" else read_codex()
            if snapshot.get("available"):
                snapshots[provider] = snapshot
                continue
            if provider == "claude":
                print(
                    "Claude is not connected. Run `claude` to sign in yourself, then rerun setup."
                )
            else:
                print(
                    "Codex is not connected. Run `codex login` to sign in yourself, then rerun setup."
                )
        if not snapshots:
            print(
                "No account profile was saved. Sign in through the official CLI, then rerun setup."
            )
            return
        providers: dict[str, dict[str, str]] = {}
        for provider, snapshot in snapshots.items():
            account = snapshot.get("account", {})
            details: dict[str, str] = {}
            if isinstance(account, dict):
                email = account.get("email")
                plan = account.get("plan")
                if isinstance(email, str) and email.strip():
                    details["expected_email"] = email.strip()
                if isinstance(plan, str) and plan.strip():
                    details["plan_label"] = plan.strip()
            if "plan_label" not in details and args.tier:
                details["plan_label"] = args.tier.strip()
            providers[provider] = details
        profile_id = _profile_id_for_label(label, registry)
        add_profile(registry, profile_id, label, providers)
        for provider, snapshot in snapshots.items():
            record_snapshot(registry, profile_id, provider, snapshot)
        save_registry(registry, store, mode=mode)
        print(
            f"Saved local-only account profile: {label} ({', '.join(sorted(snapshots))})."
        )
        return
    if args.action == "add":
        providers: dict[str, dict[str, str]] = {}
        if args.claude_email:
            providers["claude"] = {
                "expected_email": args.claude_email,
                "plan_label": args.tier,
            }
        if args.codex_email:
            providers["codex"] = {
                "expected_email": args.codex_email,
                "plan_label": args.tier,
            }
        if not providers:
            parser.error("add requires --claude-email and/or --codex-email")
        add_profile(registry, args.id, args.label, providers, inactive=args.inactive)
        save_registry(registry, store, mode=mode)
        print(f"Saved local-only account profile: {args.label}")
        return
    if args.action == "list":
        if not registry["profiles"]:
            print("No local account profiles configured.")
            return
        for profile in registry["profiles"]:
            providers = ", ".join(sorted(profile["providers"]))
            snapshots = ", ".join(sorted(profile.get("snapshots", {}))) or "none"
            inactive = " · inactive" if profile.get("inactive") else ""
            print(
                f"{profile['id']}: {profile['label']} · {providers} · snapshots: {snapshots}{inactive}"
            )
        return

    profile = _profile_by_id(registry, args.profile)
    targets = (
        sorted(profile["providers"]) if args.provider == "all" else [args.provider]
    )
    for provider in targets:
        if provider not in profile["providers"]:
            print(f"Skipping {provider}: not configured for {profile['label']}")
            continue
        snapshot = (
            read_claude(helper=Path(args.claude_helper).expanduser())
            if provider == "claude" and args.claude_helper
            else read_claude() if provider == "claude" else read_codex()
        )
        if not snapshot.get("available"):
            print(
                f"{provider.title()} snapshot unavailable; previous local snapshot is unchanged."
            )
            continue
        record_snapshot(registry, profile["id"], provider, snapshot)
        print(f"Saved sanitized {provider.title()} snapshot for {profile['label']}.")
    save_registry(registry, store, mode=mode)


def _percent(value: object) -> str:
    """Format a usage percentage for terminal ranking output."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "--"
    return f"{value:.0f}%"


def cmd_fable_next(arguments: list[str]) -> None:
    """Rank local Max profiles by conservative Fable 5 weekly headroom."""
    from account_profiles import load_registry, rank_fable_profiles

    parser = argparse.ArgumentParser(prog="python cli.py fable-next")
    parser.add_argument("--store", help="Local profile registry path")
    parser.add_argument(
        "--testing", action="store_true", help="Use isolated testing profiles"
    )
    args = parser.parse_args(arguments)
    mode = "testing" if args.testing else "standard"
    rows = rank_fable_profiles(
        load_registry(_account_store_from_args(args.store, args.testing), mode=mode)
    )
    if not rows:
        print("No local Claude account profiles configured.")
        return
    print("FABLE NEXT — guaranteed headroom from the shared 50% weekly cap")
    for index, row in enumerate(rows):
        fable = row["fable"]
        if fable is None:
            summary = "weekly snapshot unavailable"
        else:
            summary = (
                f"Fable guaranteed {_percent(fable['guaranteed_percent'])}"
                f" · weekly {_percent(fable['weekly_remaining_percent'])}"
            )
        marker = (
            "→" if index == 0 and fable and fable["guaranteed_percent"] > 0 else " "
        )
        inactive = " · inactive" if row["inactive"] else ""
        print(f"{marker} {row['label']}: {summary}{inactive}")


def cmd_codex_next(arguments: list[str]) -> None:
    """Rank local Codex profiles and surface reset credits without consuming them."""
    from account_profiles import load_registry, rank_codex_profiles

    parser = argparse.ArgumentParser(prog="python cli.py codex-next")
    parser.add_argument("--store", help="Local profile registry path")
    parser.add_argument(
        "--testing", action="store_true", help="Use isolated testing profiles"
    )
    args = parser.parse_args(arguments)
    mode = "testing" if args.testing else "standard"
    rows = rank_codex_profiles(
        load_registry(_account_store_from_args(args.store, args.testing), mode=mode)
    )
    if not rows:
        print("No local Codex account profiles configured.")
        return
    print(
        "CODEX NEXT — direct headroom first; reset credits are never consumed automatically"
    )
    for index, row in enumerate(rows):
        resets = row["reset_credits"]
        reset_note = (
            f" · reset credits {resets['available_count']}"
            if resets["available_count"]
            else ""
        )
        marker = (
            "→" if index == 0 and row["direct_ready"] and not row["inactive"] else " "
        )
        inactive = " · inactive" if row["inactive"] else ""
        print(
            f"{marker} {row['label']}: 5h {_percent(row['five_hour_remaining_percent'])}"
            f" · week {_percent(row['weekly_remaining_percent'])}{reset_note}{inactive}"
        )
