"""Local-only account profiles and isolated first-run test state.

Profile data intentionally lives outside the checkout. It stores only labels,
expected account emails, and sanitized connector snapshots; never credentials,
tokens, transcripts, or raw provider responses.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)
STATE_DIRECTORY = Path.home() / ".hotfix-ops-usage"
DEFAULT_STORE_PATH = STATE_DIRECTORY / "accounts.json"
TESTING_STORE_PATH = STATE_DIRECTORY / "testing" / "accounts.json"
SUPPORTED_PROVIDERS = frozenset({"claude", "codex"})
WINDOW_NAMES = frozenset({"five_hour", "seven_day"})
REGISTRY_MODES = frozenset({"standard", "testing"})


def _empty_registry(mode: str = "standard") -> dict[str, Any]:
    """Return a valid empty local registry for the requested mode."""
    if mode not in REGISTRY_MODES:
        raise ValueError(f"Unsupported local registry mode: {mode}")
    return {"version": 1, "mode": mode, "profiles": []}


def _as_nonempty_string(value: Any, maximum: int = 160) -> Optional[str]:
    """Return a bounded plain string, or ``None`` for unusable values."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > maximum:
        return None
    return value


def _safe_window(value: Any) -> Optional[dict[str, Any]]:
    """Normalize one percent-based provider usage window."""
    if not isinstance(value, dict):
        return None
    used = value.get("used_percent")
    remaining = value.get("remaining_percent")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    safe_used = max(0.0, min(100.0, float(used)))
    if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
        remaining = 100.0 - safe_used
    safe_remaining = max(0.0, min(100.0, float(remaining)))
    safe: dict[str, Any] = {
        "used_percent": safe_used,
        "remaining_percent": safe_remaining,
    }
    resets_at = _as_nonempty_string(value.get("resets_at"), maximum=64)
    if resets_at is not None:
        safe["resets_at"] = resets_at
    return safe


def sanitize_snapshot(provider: str, value: Any) -> dict[str, Any]:
    """Keep only the bounded, browser-safe connector snapshot fields.

    Args:
        provider: Dashboard provider key, ``claude`` or ``codex``.
        value: Untrusted connector output.

    Returns:
        A normalized snapshot suitable for the local account registry.

    Raises:
        ValueError: If the provider is unsupported or the value is malformed.
    """
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    if not isinstance(value, dict):
        raise ValueError("Connector snapshot must be an object")
    account_value = value.get("account")
    account: dict[str, str] = {}
    if isinstance(account_value, dict):
        for key in ("email", "label", "plan", "auth_method"):
            safe_value = _as_nonempty_string(account_value.get(key))
            if safe_value is not None:
                account[key] = safe_value
    windows: dict[str, dict[str, Any]] = {}
    raw_windows = value.get("windows")
    if isinstance(raw_windows, dict):
        for name in WINDOW_NAMES:
            safe_window = _safe_window(raw_windows.get(name))
            if safe_window is not None:
                windows[name] = safe_window
    snapshot: dict[str, Any] = {
        "provider": provider,
        "available": bool(value.get("available")),
        "source": _as_nonempty_string(value.get("source"), maximum=64) or "unknown",
        "account": account,
        "windows": windows,
        "fetched_at": _as_nonempty_string(value.get("fetched_at"), maximum=64)
        or datetime.now(timezone.utc).isoformat(),
    }
    error = _as_nonempty_string(value.get("error"), maximum=240)
    if error is not None:
        snapshot["error"] = error
    reset_credits = value.get("reset_credits")
    if provider == "codex" and isinstance(reset_credits, dict):
        count = reset_credits.get("available_count")
        if isinstance(count, int) and not isinstance(count, bool):
            safe_credits: dict[str, Any] = {
                "available_count": max(0, min(1000, count)),
            }
            expires_at = _as_nonempty_string(reset_credits.get("expires_at"), 64)
            if expires_at is not None:
                safe_credits["expires_at"] = expires_at
            snapshot["reset_credits"] = safe_credits
    return snapshot


def load_registry(
    store_path: Path = DEFAULT_STORE_PATH, mode: str = "standard"
) -> dict[str, Any]:
    """Load the local-only profile registry without creating it."""
    if mode not in REGISTRY_MODES:
        raise ValueError(f"Unsupported local registry mode: {mode}")
    path = Path(store_path)
    if not path.is_file():
        return _empty_registry(mode)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read local account registry: {exc}") from exc
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Local account registry has an unsupported format")
    profiles = value.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("Local account registry profiles must be a list")
    stored_mode = value.get("mode")
    if stored_mode == "local-test":
        stored_mode = "testing"
    sanitized = _empty_registry(stored_mode if stored_mode in REGISTRY_MODES else mode)
    for profile in profiles:
        safe_profile = _sanitize_profile(profile)
        if safe_profile is not None:
            sanitized["profiles"].append(safe_profile)
    return sanitized


def _sanitize_profile(value: Any) -> Optional[dict[str, Any]]:
    """Normalize one profile loaded from the local registry."""
    if not isinstance(value, dict):
        return None
    profile_id = _as_nonempty_string(value.get("id"), maximum=64)
    label = _as_nonempty_string(value.get("label"), maximum=100)
    if profile_id is None or label is None:
        return None
    providers_value = value.get("providers")
    if not isinstance(providers_value, dict):
        return None
    providers: dict[str, dict[str, str]] = {}
    for provider in SUPPORTED_PROVIDERS:
        details = providers_value.get(provider)
        if not isinstance(details, dict):
            continue
        safe_details: dict[str, str] = {}
        for key in ("expected_email", "plan_label"):
            safe_value = _as_nonempty_string(details.get(key))
            if safe_value is not None:
                safe_details[key] = safe_value
        providers[provider] = safe_details
    if not providers:
        return None
    snapshots: dict[str, dict[str, Any]] = {}
    raw_snapshots = value.get("snapshots")
    if isinstance(raw_snapshots, dict):
        for provider in providers:
            raw_snapshot = raw_snapshots.get(provider)
            if isinstance(raw_snapshot, dict):
                snapshots[provider] = sanitize_snapshot(provider, raw_snapshot)
    safe_profile: dict[str, Any] = {
        "id": profile_id,
        "label": label,
        "providers": providers,
        "snapshots": snapshots,
    }
    inactive = value.get("inactive")
    if isinstance(inactive, bool):
        safe_profile["inactive"] = inactive
    return safe_profile


def save_registry(
    registry: dict[str, Any],
    store_path: Path = DEFAULT_STORE_PATH,
    mode: str = "standard",
) -> None:
    """Atomically write a validated registry with owner-only permissions."""
    if mode not in REGISTRY_MODES:
        raise ValueError(f"Unsupported local registry mode: {mode}")
    profile_values = registry.get("profiles") if isinstance(registry, dict) else None
    if not isinstance(profile_values, list):
        raise ValueError("Local account registry profiles must be a list")
    normalized = _empty_registry(mode)
    for profile in profile_values:
        safe_profile = _sanitize_profile(profile)
        if safe_profile is None:
            raise ValueError("Local account registry contains an invalid profile")
        normalized["profiles"].append(safe_profile)
    path = Path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix="accounts-", suffix=".json", dir=path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temporary_file:
            json.dump(normalized, temporary_file, indent=2, sort_keys=True)
            temporary_file.write("\n")
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
        path.chmod(0o600)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def reset_testing_registry(store_path: Path = TESTING_STORE_PATH) -> None:
    """Replace only the isolated testing registry with an empty safe state."""
    save_registry(_empty_registry("testing"), store_path, mode="testing")


def seed_testing_registry(store_path: Path = TESTING_STORE_PATH) -> None:
    """Replace isolated testing state with clearly fake multi-account samples."""
    registry = _empty_registry("testing")
    profiles = (
        (
            "studio-max",
            "Studio Max",
            {"claude": {"plan_label": "Max 20x"}, "codex": {"plan_label": "Max 20x"}},
        ),
        (
            "personal-max",
            "Personal Max",
            {"claude": {"plan_label": "Max 20x"}, "codex": {"plan_label": "Max 20x"}},
        ),
        (
            "standby-max",
            "Standby Max",
            {"claude": {"plan_label": "Max 20x"}},
        ),
    )
    for profile_id, label, providers in profiles:
        add_profile(
            registry, profile_id, label, providers, inactive=profile_id == "standby-max"
        )
    record_snapshot(
        registry,
        "studio-max",
        "claude",
        {
            "provider": "claude",
            "available": True,
            "source": "testing-sample",
            "account": {"label": "Studio Max", "plan": "max"},
            "windows": {"seven_day": {"used_percent": 28, "remaining_percent": 72}},
        },
    )
    record_snapshot(
        registry,
        "studio-max",
        "codex",
        {
            "provider": "codex",
            "available": True,
            "source": "testing-sample",
            "account": {"label": "Studio Max", "plan": "max"},
            "windows": {
                "five_hour": {"used_percent": 35, "remaining_percent": 65},
                "seven_day": {"used_percent": 42, "remaining_percent": 58},
            },
            "reset_credits": {"available_count": 1},
        },
    )
    record_snapshot(
        registry,
        "personal-max",
        "claude",
        {
            "provider": "claude",
            "available": True,
            "source": "testing-sample",
            "account": {"label": "Personal Max", "plan": "max"},
            "windows": {"seven_day": {"used_percent": 71, "remaining_percent": 29}},
        },
    )
    record_snapshot(
        registry,
        "personal-max",
        "codex",
        {
            "provider": "codex",
            "available": True,
            "source": "testing-sample",
            "account": {"label": "Personal Max", "plan": "max"},
            "windows": {
                "five_hour": {"used_percent": 82, "remaining_percent": 18},
                "seven_day": {"used_percent": 68, "remaining_percent": 32},
            },
        },
    )
    save_registry(registry, store_path, mode="testing")


def add_profile(
    registry: dict[str, Any],
    profile_id: str,
    label: str,
    providers: dict[str, dict[str, str]],
    inactive: bool = False,
) -> dict[str, Any]:
    """Add a profile definition without credentials or a live snapshot."""
    if any(profile.get("id") == profile_id for profile in registry.get("profiles", [])):
        raise ValueError(f"Local account profile already exists: {profile_id}")
    candidate = _sanitize_profile(
        {
            "id": profile_id,
            "label": label,
            "providers": providers,
            "snapshots": {},
            "inactive": inactive,
        }
    )
    if candidate is None:
        raise ValueError("Invalid local account profile")
    registry["profiles"].append(candidate)
    return candidate


def record_snapshot(
    registry: dict[str, Any], profile_id: str, provider: str, snapshot: dict[str, Any]
) -> None:
    """Associate one sanitized active-account snapshot with a local profile."""
    safe_snapshot = sanitize_snapshot(provider, snapshot)
    for profile in registry.get("profiles", []):
        if profile.get("id") != profile_id:
            continue
        expected = profile.get("providers", {}).get(provider)
        if not isinstance(expected, dict):
            raise ValueError(f"Profile {profile_id} does not support {provider}")
        expected_email = expected.get("expected_email", "").casefold()
        current_email = safe_snapshot.get("account", {}).get("email", "").casefold()
        if expected_email and current_email and expected_email != current_email:
            raise ValueError(
                "Active account does not match the selected account profile"
            )
        profile.setdefault("snapshots", {})[provider] = safe_snapshot
        profile["inactive"] = False
        return
    raise ValueError(f"Unknown local account profile: {profile_id}")
