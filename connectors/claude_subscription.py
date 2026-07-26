"""Read sanitized Claude account status from the authenticated Claude CLI.

Claude does not currently expose subscription usage windows through a supported
CLI command. This connector therefore reports identity/plan information only
unless the user explicitly supplies a local helper that emits the normalized
``windows`` object documented in ``docs/public-dashboard-plan.md``.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_result() -> dict[str, Any]:
    return {
        "provider": "anthropic",
        "available": False,
        "source": "claude-auth-status",
        "account": {},
        "windows": {},
        "fetched_at": _now_iso(),
        "error": None,
    }


def _safe_account(status: dict[str, Any]) -> dict[str, str]:
    account: dict[str, str] = {}
    for source, target in (
        ("email", "email"),
        ("subscriptionType", "plan"),
        ("authMethod", "auth_method"),
    ):
        value = status.get(source)
        if isinstance(value, str) and value:
            account[target] = value
    organization = status.get("organization")
    if isinstance(organization, dict):
        label = organization.get("name")
        if isinstance(label, str) and label:
            account["label"] = label
    return account


def _safe_window(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    used = value.get("used_percent")
    remaining = value.get("remaining_percent")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    used = max(0.0, min(100.0, float(used)))
    if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
        remaining = 100.0 - used
    remaining = max(0.0, min(100.0, float(remaining)))
    result: dict[str, Any] = {
        "used_percent": used,
        "remaining_percent": remaining,
    }
    resets_at = value.get("resets_at")
    if isinstance(resets_at, str) and resets_at:
        result["resets_at"] = resets_at
    return result


def _read_helper(helper: Path, timeout: float) -> dict[str, dict[str, Any]]:
    completed = subprocess.run(
        [str(helper)],
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError("Claude subscription helper returned a non-zero status")
    payload = json.loads(completed.stdout)
    raw_windows = payload.get("windows", payload)
    if not isinstance(raw_windows, dict):
        raise ValueError("Claude subscription helper did not return a windows object")
    windows: dict[str, dict[str, Any]] = {}
    for key in ("five_hour", "seven_day"):
        safe = _safe_window(raw_windows.get(key))
        if safe is not None:
            windows[key] = safe
    return windows


def read_subscription(
    claude_command: str = "claude",
    helper: Optional[Path] = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Return sanitized Claude account and optional subscription-window data."""
    result = _base_result()
    try:
        completed = subprocess.run(
            [claude_command, "auth", "status", "--json"],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            diagnostic = f"{completed.stdout}\n{completed.stderr}".lower()
            unsupported_markers = (
                "unknown command",
                "unknown option",
                "unrecognized",
                "invalid option",
            )
            if any(marker in diagnostic for marker in unsupported_markers):
                result["error"] = (
                    "This Claude CLI version does not support "
                    "`claude auth status --json`"
                )
            elif "not logged" in diagnostic or "login" in diagnostic:
                result["error"] = "Claude CLI is not logged in"
            else:
                result["error"] = "Claude account status command failed"
            return result
        status = json.loads(completed.stdout)
        if not isinstance(status, dict) or not status.get("loggedIn"):
            result["error"] = "Claude CLI is not logged in"
            return result
        result["available"] = True
        result["account"] = _safe_account(status)
        if helper is not None:
            result["windows"] = _read_helper(Path(helper), timeout)
            result["source"] = "user-helper"
        elif result["account"].get("plan"):
            result["error"] = (
                "Claude subscription limits are not exposed by a supported CLI command"
            )
    except FileNotFoundError:
        result["error"] = "Claude CLI was not found"
    except subprocess.TimeoutExpired:
        result["error"] = "Claude account lookup timed out"
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        result["error"] = str(exc)
    result["fetched_at"] = _now_iso()
    return result


if __name__ == "__main__":
    print(json.dumps(read_subscription(), indent=2))
