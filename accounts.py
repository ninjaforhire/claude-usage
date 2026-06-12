"""Multi-account OAuth credential store + usage fetch for the dashboard."""

from __future__ import annotations

import calendar
import datetime as _dt
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

STORE_PATH = Path.home() / ".claude" / "usage_accounts.json"

TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # Claude Code's public OAuth client identifier — not a secret
SKEW = timedelta(seconds=60)

COLOR_RAMP = [  # (min_remaining_pct, fill_hi, fill_lo)
    (60, "#39ff6e", "#0fae3e"),
    (35, "#ffe23d", "#c69b08"),
    (15, "#ff9433", "#c45b06"),
    (0,  "#ff3b3b", "#b30f0f"),
]


# ── Store layer ───────────────────────────────────────────────────────────────

def load_store(path: Path = STORE_PATH) -> dict:
    """Load the account store from disk, returning empty store if missing."""
    if not Path(path).exists():
        return {"accounts": []}
    with open(path) as f:
        return json.load(f)


def save_store(store: dict, path: Path = STORE_PATH) -> None:
    """Atomically write the account store with mode 600."""
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(store, f, indent=2)
    tmp.replace(path)


def upsert_account(acct: dict, path: Path = STORE_PATH) -> None:
    """Insert or replace account record by email."""
    store = load_store(path=path)
    store["accounts"] = [
        a for a in store["accounts"] if a["email"] != acct["email"]
    ] + [acct]
    save_store(store, path=path)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get_json(url: str, headers: dict, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _post_json(url: str, payload: dict, timeout: int = 10) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# ── Token refresh + usage fetch ───────────────────────────────────────────────

def _is_expired(oauth: dict) -> bool:
    """Return True if the access token is expired or expiry is unknown."""
    try:
        exp = datetime.fromisoformat(oauth["expires_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return True
    return datetime.now(timezone.utc) >= exp - SKEW


def _refresh(oauth: dict) -> dict:
    """Exchange a refresh token for a new access token; return updated oauth dict."""
    data = _post_json(TOKEN_URL, {
        "grant_type": "refresh_token",
        "refresh_token": oauth["refresh_token"],
        "client_id": CLIENT_ID,
    })
    exp = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", oauth["refresh_token"]),
        "expires_at": exp.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _parse_usage(raw: dict) -> dict:
    """Extract five_hour and seven_day windows from the raw API response.

    Tolerates extra top-level keys and microsecond/offset timestamp formats.
    """
    try:
        return _extract_windows(raw)
    except KeyError as e:
        raise ValueError(f"unexpected usage response shape: {list(raw)}") from e


def _extract_windows(raw: dict) -> dict:
    return {
        "five_hour": {
            "utilization": raw["five_hour"]["utilization"],
            "resets_at": raw["five_hour"]["resets_at"],
        },
        "seven_day": {
            "utilization": raw["seven_day"]["utilization"],
            "resets_at": raw["seven_day"]["resets_at"],
        },
    }


def fetch_usage(oauth: dict) -> dict:
    """Fetch current usage for a single account; returns parsed usage dict."""
    raw = _get_json(USAGE_URL, {
        "Authorization": f"Bearer {oauth['access_token']}",
        "anthropic-beta": "oauth-2025-04-20",
    })
    return _parse_usage(raw)


def fetch_all_usage(path: Path = STORE_PATH) -> list[dict]:
    """Refresh tokens as needed, fetch usage for every account, persist cache."""
    store = load_store(path=path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for acct in store["accounts"]:
        try:
            if _is_expired(acct["oauth"]):
                acct["oauth"] = _refresh(acct["oauth"])
                save_store(store, path=path)  # persist rotated token even if fetch fails
            usage = fetch_usage(acct["oauth"])
            acct["last_usage"] = {**usage, "fetched_at": now, "error": None}
        except Exception as e:  # noqa: BLE001 — any failure grays this orb only
            prev = acct.get("last_usage") or {}
            acct["last_usage"] = {**prev, "fetched_at": now, "error": str(e)}
    save_store(store, path=path)
    return store["accounts"]


# ── Presentation helpers ──────────────────────────────────────────────────────

def remaining_color(pct: int | float) -> tuple[str, str]:
    """Return (color_hi, color_lo) for a given remaining-capacity percentage."""
    for floor, hi, lo in COLOR_RAMP:
        if pct >= floor:
            return hi, lo
    return COLOR_RAMP[-1][1], COLOR_RAMP[-1][2]


def days_until_renewal(billing_day: int, today: _dt.date | None = None) -> int:
    """Return calendar days until the next billing renewal date."""
    today = today or _dt.date.today()
    last = calendar.monthrange(today.year, today.month)[1]
    target = _dt.date(today.year, today.month, min(billing_day, last))
    if target < today:
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        last = calendar.monthrange(y, m)[1]
        target = _dt.date(y, m, min(billing_day, last))
    return (target - today).days


def public_view(accts: list[dict]) -> list[dict]:
    """Strip credentials; return everything the dashboard JS needs."""
    out = []
    for a in accts:
        u = a.get("last_usage") or {}
        entry = {
            "email": a["email"],
            "plan": a.get("plan", ""),
            "renews_in_days": days_until_renewal(a["billing_day"]) if a.get("billing_day") else None,
            "fetched_at": u.get("fetched_at"),
            "error": u.get("error"),
            "windows": {},
        }
        for key in ("five_hour", "seven_day"):
            if key in u:
                remaining = round(min(100, max(0, 100 - u[key]["utilization"])))
                hi, lo = remaining_color(remaining)
                entry["windows"][key] = {
                    "remaining_pct": remaining,
                    "resets_at": u[key]["resets_at"],
                    "color_hi": hi,
                    "color_lo": lo,
                }
        out.append(entry)
    return out
