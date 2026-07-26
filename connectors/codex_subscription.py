"""Read sanitized Codex subscription limits from the authenticated Codex CLI."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional, TextIO


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_result() -> dict[str, Any]:
    return {
        "provider": "openai",
        "available": False,
        "source": "codex-app-server",
        "account": {},
        "windows": {},
        "reset_credits": {},
        "fetched_at": _now_iso(),
        "error": None,
    }


def _reader(stream: TextIO, messages: queue.Queue[Optional[dict[str, Any]]]) -> None:
    for line in stream:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            messages.put(message)
    messages.put(None)


def _write_request(stream: TextIO, request_id: int, method: str) -> None:
    params: Any = (
        {"clientInfo": {"name": "claude-codex-usage", "version": "0.1.0"}}
        if method == "initialize"
        else None
    )
    stream.write(
        json.dumps({"id": request_id, "method": method, "params": params}) + "\n"
    )
    stream.flush()


def _window_name(duration_minutes: Any) -> Optional[str]:
    if not isinstance(duration_minutes, (int, float)):
        return None
    if duration_minutes <= 6 * 60:
        return "five_hour"
    if duration_minutes >= 6 * 24 * 60:
        return "seven_day"
    return None


def _safe_window(value: Any) -> Optional[tuple[str, dict[str, Any]]]:
    if not isinstance(value, dict):
        return None
    name = _window_name(value.get("windowDurationMins"))
    used = value.get("usedPercent")
    if name is None or not isinstance(used, (int, float)) or isinstance(used, bool):
        return None
    used = max(0.0, min(100.0, float(used)))
    window: dict[str, Any] = {
        "used_percent": used,
        "remaining_percent": 100.0 - used,
    }
    resets_at = value.get("resetsAt")
    if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
        now = datetime.now(timezone.utc).timestamp()
        if 1_577_836_800 <= resets_at <= now + (2 * 365 * 24 * 60 * 60):
            window["resets_at"] = datetime.fromtimestamp(
                resets_at, tz=timezone.utc
            ).isoformat()
    return name, window


def _safe_reset_credits(payload: Any) -> dict[str, Any]:
    """Keep reset-credit availability without retaining opaque credit IDs."""
    if not isinstance(payload, dict):
        return {}
    count = payload.get("availableCount")
    if not isinstance(count, int) or isinstance(count, bool):
        return {}
    safe: dict[str, Any] = {"available_count": max(0, min(1000, count))}
    earliest_expiry: Optional[datetime] = None
    credits = payload.get("credits")
    if isinstance(credits, list):
        for credit in credits:
            if not isinstance(credit, dict) or credit.get("status") != "available":
                continue
            expires_at = credit.get("expiresAt")
            if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool):
                continue
            now = datetime.now(timezone.utc).timestamp()
            if not 1_577_836_800 <= expires_at <= now + (2 * 365 * 24 * 60 * 60):
                continue
            expiry = datetime.fromtimestamp(expires_at, tz=timezone.utc)
            if earliest_expiry is None or expiry < earliest_expiry:
                earliest_expiry = expiry
    if earliest_expiry is not None:
        safe["expires_at"] = earliest_expiry.isoformat()
    return safe


def _extract_rate_limits(
    payload: Any,
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}, {}, {}
    reset_credits = _safe_reset_credits(payload.get("rateLimitResetCredits"))
    limits = payload.get("rateLimits")
    if not isinstance(limits, dict):
        return {}, {}, reset_credits
    account: dict[str, str] = {}
    plan = limits.get("planType")
    if isinstance(plan, str) and plan:
        account["plan"] = plan
    windows: dict[str, Any] = {}
    for key in ("primary", "secondary"):
        parsed = _safe_window(limits.get(key))
        if parsed is not None:
            name, window = parsed
            windows[name] = window
    return account, windows, reset_credits


def _safe_account(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    value = payload.get("account", payload)
    if not isinstance(value, dict):
        return {}
    account: dict[str, str] = {}
    for source, target in (
        ("email", "email"),
        ("planType", "plan"),
        ("type", "auth_method"),
    ):
        item = value.get(source)
        if isinstance(item, str) and item:
            account[target] = item
    return account


def read_subscription(
    codex_command: str = "codex",
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Return account-safe Codex plan and rate-limit windows."""
    result = _base_result()
    process: Optional[subprocess.Popen[str]] = None
    try:
        process = subprocess.Popen(
            [codex_command, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("Codex app server did not expose stdio")
        messages: queue.Queue[Optional[dict[str, Any]]] = queue.Queue()
        threading.Thread(
            target=_reader, args=(process.stdout, messages), daemon=True
        ).start()
        _write_request(process.stdin, 1, "initialize")
        deadline = time.monotonic() + timeout
        requested = False
        received: set[int] = set()
        while time.monotonic() < deadline and received != {2, 3}:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                message = messages.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if message is None:
                break
            if message.get("id") == 1 and message.get("result") is not None:
                _write_request(process.stdin, 2, "account/read")
                _write_request(process.stdin, 3, "account/rateLimits/read")
                requested = True
                continue
            message_id = message.get("id")
            if message_id == 2:
                if not message.get("error"):
                    result["account"].update(_safe_account(message.get("result")))
                received.add(2)
            elif message_id == 3:
                if message.get("error"):
                    result["error"] = "Codex subscription lookup is unsupported"
                else:
                    account, windows, reset_credits = _extract_rate_limits(
                        message.get("result")
                    )
                    result["account"].update(account)
                    result["windows"] = windows
                    result["reset_credits"] = reset_credits
                received.add(3)
        if not requested:
            result["error"] = "Codex app server did not initialize"
        elif 3 not in received:
            result["error"] = "Codex subscription lookup timed out"
        elif result["error"] is None:
            result["available"] = True
    except FileNotFoundError:
        result["error"] = "Codex CLI was not found"
    except (OSError, RuntimeError, ValueError) as exc:
        result["error"] = str(exc)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
    result["fetched_at"] = _now_iso()
    return result


if __name__ == "__main__":
    print(json.dumps(read_subscription(), indent=2))
