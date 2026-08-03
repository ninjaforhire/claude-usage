"""Fresh, read-only account state aggregation and model routing."""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import accounts
import codex_limits
from account_registry import AccountProfile, REGISTRY_PATH, load_registry

FABLE_CAP_PCT = 50
DRAIN_BOOST = 100.0
THROTTLE_PCT = 15


def _burn_counters(config_dir: Path) -> dict[str, int | str | None]:
    """Read local scanner counters without mutating the usage database."""
    db_path = config_dir / "usage.db"
    if not db_path.exists():
        return {"database": str(db_path), "turns": 0, "tokens": 0, "error": "missing"}
    try:
        with sqlite3.connect(db_path) as connection:
            row = connection.execute("""
                SELECT COUNT(*) AS turns,
                       COALESCE(SUM(input_tokens), 0)
                       + COALESCE(SUM(output_tokens), 0)
                       + COALESCE(SUM(cache_read_tokens), 0)
                       + COALESCE(SUM(cache_creation_tokens), 0) AS tokens
                FROM turns
                """).fetchone()
    except sqlite3.Error as error:
        return {"database": str(db_path), "turns": 0, "tokens": 0, "error": str(error)}
    return {
        "database": str(db_path),
        "turns": int(row[0]),
        "tokens": int(row[1]),
        "error": None,
    }


def _keychain_health() -> dict[str, Any]:
    """Probe the file-first credential chain without exposing credentials."""
    try:
        accounts.keychain_oauth()
    except (
        OSError,
        ValueError,
        KeyError,
        subprocess.CalledProcessError,
        accounts.NoUsableCredentials,
    ) as error:
        return {"accessible": False, "error": str(error)}
    return {"accessible": True, "error": None}


def _windows(usage: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convert persisted utilization windows to decision-safe remaining values."""
    output: dict[str, dict[str, Any]] = {}
    for key in ("five_hour", "seven_day", "fable"):
        window = usage.get(key)
        if not isinstance(window, dict) or "utilization" not in window:
            continue
        remaining = round(min(100, max(0, 100 - float(window["utilization"]))))
        output[key] = {
            "remaining_pct": remaining,
            "resets_at": window.get("resets_at"),
        }
    return output


def _claude_state(
    profile: AccountProfile,
    record: dict[str, Any] | None,
    burn: dict[str, Any],
    keychain: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    """Build one Claude view, preserving failed-fetch state explicitly."""
    usage = (record or {}).get("last_usage") or {}
    error = usage.get("error")
    needs_relogin = bool(usage.get("needs_relogin"))
    if dry_run:
        state = "dry-run"
        fresh = False
    elif usage.get("error_kind") == "no_credentials":
        state = "no-credentials"
        fresh = False
    elif needs_relogin or usage.get("error_kind") == "auth":
        state = "auth-broken"
        fresh = False
    elif error:
        state = (
            "throttled" if usage.get("error_kind") == "rate_limit" else "stale-cached"
        )
        fresh = False
    elif record is None:
        state = "no-account-record"
        fresh = False
    else:
        state = "fresh"
        fresh = True
    return {
        "id": profile.id,
        "provider": profile.provider,
        "email": profile.email,
        "plan": profile.plan,
        "caps": list(profile.caps),
        "active": profile.active,
        "is_main": profile.is_main,
        "config_dir": str(profile.config_dir),
        "keychain_slot": profile.keychain_slot,
        "keychain": keychain,
        "burn": burn,
        "fresh": fresh,
        "state": state,
        "windows": _windows(usage),
        "error": error,
        "error_kind": usage.get("error_kind"),
        "needs_relogin": needs_relogin,
        "fetched_at": usage.get("fetched_at"),
        "last_success_at": usage.get("last_success_at"),
    }


def _codex_state(profile: AccountProfile) -> dict[str, Any]:
    """Build one Codex home view, including the no-session-data state."""
    data = codex_limits.codex_orb_data(
        sessions_dir=profile.config_dir / "sessions", plan=profile.plan
    )
    return {
        "id": profile.id,
        "provider": profile.provider,
        "email": None,
        "plan": profile.plan,
        "caps": list(profile.caps),
        "active": profile.active,
        "is_main": profile.is_main,
        "config_dir": str(profile.config_dir),
        "keychain_slot": None,
        "fresh": data.get("error") is None,
        "state": data.get(
            "state", "ok" if data.get("error") is None else "no_rate_limit_data"
        ),
        "windows": data.get("windows", {}),
        "error": data.get("error"),
        "plan_type": data.get("plan_type"),
        "model": data.get("model"),
        "credits": data.get("credits"),
        "burn": _burn_counters(profile.config_dir),
    }


def aggregate_accounts(
    registry_path: Path = REGISTRY_PATH,
    store_path: Path = accounts.STORE_PATH,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Read every configured account now, with no merged-result cache layer.

    ``dry_run`` validates the static fleet and local Codex session surfaces but
    intentionally avoids keychain and Claude usage API calls.
    """
    profiles = load_registry(registry_path)
    by_email: dict[str, dict[str, Any]] = {}
    keychain: dict[str, Any] = {"accessible": None, "error": None}
    if not dry_run:
        refreshed = accounts.fetch_all_usage(path=store_path, force=True)
        by_email = {item["email"]: item for item in refreshed if item.get("email")}
        keychain_failure = next(
            (
                (item.get("last_usage") or {}).get("error")
                for item in refreshed
                if (item.get("last_usage") or {}).get("error_kind") == "no_credentials"
            ),
            None,
        )
        keychain = (
            {"accessible": False, "error": keychain_failure}
            if keychain_failure
            else _keychain_health()
        )

    burn_by_dir: dict[Path, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    for profile in profiles:
        burn = burn_by_dir.setdefault(
            profile.config_dir, _burn_counters(profile.config_dir)
        )
        if profile.provider == "claude":
            entries.append(
                _claude_state(
                    profile,
                    by_email.get(profile.email or ""),
                    burn,
                    keychain,
                    dry_run,
                )
            )
        else:
            entries.append(_codex_state(profile))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "accounts": entries,
        "summary": {
            "fresh": sum(1 for item in entries if item["fresh"]),
            "degraded": sum(1 for item in entries if not item["fresh"]),
        },
    }


def _fable_choice(entries: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """Choose a fresh Claude account using Fable's weekly-cap drain ordering."""
    ranked: list[tuple[float, dict[str, Any], list[str]]] = []
    for entry in entries:
        if entry["provider"] != "claude" or not entry["active"] or not entry["fresh"]:
            continue
        windows = entry["windows"]
        if "five_hour" not in windows or "seven_day" not in windows:
            continue
        fable_room = (
            windows["fable"]["remaining_pct"]
            if "fable" in windows
            else max(0, windows["seven_day"]["remaining_pct"] - FABLE_CAP_PCT)
        )
        h5 = windows["five_hour"]["remaining_pct"]
        running = bool((windows.get("fable") or windows["seven_day"]).get("resets_at"))
        if fable_room <= 0:
            continue
        score = float(fable_room) + (
            DRAIN_BOOST if running and h5 >= THROTTLE_PCT else 0.0
        )
        reasons = [f"{fable_room}% Fable room", f"{h5}% 5h free"]
        reasons.append(
            "running window: drain before reserve" if running else "fresh reserve"
        )
        if h5 < THROTTLE_PCT:
            reasons.append("5h throttled")
        ranked.append((score, entry, reasons))
    if not ranked:
        return {
            "model": model,
            "recommendation": None,
            "reason": "no fresh Claude account has Fable room",
        }
    score, selected, reasons = max(
        ranked, key=lambda item: (item[0], item[1]["is_main"])
    )
    return {
        "model": model,
        "recommendation": {
            "id": selected["id"],
            "email": selected["email"],
            "config_dir": selected["config_dir"],
        },
        "score": score,
        "reasons": reasons,
    }


def choose_account(snapshot: dict[str, Any], model: str) -> dict[str, Any]:
    """Return the best fresh provider profile for a requested model family."""
    normalized = model.lower().strip()
    entries = snapshot["accounts"]
    if "fable" in normalized:
        return _fable_choice(entries, model)
    if normalized in {
        "terra",
        "sol",
        "luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-5.6-luna",
    }:
        candidates = [
            entry
            for entry in entries
            if entry["provider"] == "codex"
            and entry["active"]
            and entry["fresh"]
            and normalized.split("-")[-1] in entry["caps"]
        ]
        if candidates:
            selected = candidates[0]
            return {
                "model": model,
                "recommendation": {
                    "id": selected["id"],
                    "config_dir": selected["config_dir"],
                },
                "reasons": ["fresh Codex rate-limit data"],
            }
        return {
            "model": model,
            "recommendation": None,
            "reason": "no Codex home has fresh session data",
        }
    return _fable_choice(entries, model)
