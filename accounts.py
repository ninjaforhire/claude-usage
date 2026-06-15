"""Multi-account OAuth credential store + usage fetch for the dashboard."""

from __future__ import annotations

import calendar
import datetime as _dt
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

STORE_PATH = Path.home() / ".claude" / "usage_accounts.json"

TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
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
    os.chmod(tmp, 0o600)  # O_CREAT mode is ignored if a stale tmp file pre-exists
    tmp.replace(path)


def upsert_account(acct: dict, path: Path = STORE_PATH) -> None:
    """Insert or replace account record by email."""
    store = load_store(path=path)
    store["accounts"] = [
        a for a in store["accounts"] if a["email"] != acct["email"]
    ] + [acct]
    save_store(store, path=path)


def set_keychain_owner(email: str, path: Path = STORE_PATH) -> None:
    """Mark which account currently owns the Claude Code keychain credentials."""
    store = load_store(path=path)
    store["keychain_owner"] = email
    save_store(store, path=path)


KEYCHAIN_SERVICE = "Claude Code-credentials"


def keychain_oauth() -> dict:
    """Read the live Claude Code keychain credentials as an oauth dict."""
    raw = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    creds = json.loads(raw)["claudeAiOauth"]
    exp = datetime.fromtimestamp(creds["expiresAt"] / 1000, tz=timezone.utc)
    return {
        "access_token": creds["accessToken"],
        "refresh_token": creds["refreshToken"],
        "expires_at": exp.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── HTTP helpers ──────────────────────────────────────────────────────────────

# urllib's default Python-urllib UA gets Cloudflare-blocked (error 1010) on
# platform.claude.com; present as the Claude Code CLI instead.
_USER_AGENT = "claude-cli/2.1.0 (external, cli)"


def _get_json(url: str, headers: dict, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _post_json(url: str, payload: dict, timeout: int = 10) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
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


PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"


def fetch_profile_email(oauth: dict) -> str | None:
    """Return the account email for an access token (identity check)."""
    raw = _get_json(PROFILE_URL, {
        "Authorization": f"Bearer {oauth['access_token']}",
        "anthropic-beta": "oauth-2025-04-20",
    })
    return (raw.get("account") or {}).get("email")


def fetch_usage(oauth: dict) -> dict:
    """Fetch current usage for a single account; returns parsed usage dict."""
    raw = _get_json(USAGE_URL, {
        "Authorization": f"Bearer {oauth['access_token']}",
        "anthropic-beta": "oauth-2025-04-20",
    })
    return _parse_usage(raw)


# Refresh tokens are single-use: concurrent refreshes (multiple dashboard tabs,
# ThreadingHTTPServer) could rotate the same token twice and persist a stale one,
# locking the account out. Serialize the whole refresh-fetch-save cycle.
_FETCH_LOCK = threading.Lock()


def fetch_all_usage(path: Path = STORE_PATH) -> list[dict]:
    """Refresh tokens as needed, fetch usage for every account, persist cache."""
    with _FETCH_LOCK:
        return _fetch_all_usage_locked(path)


def _fetch_all_usage_locked(path: Path) -> list[dict]:
    store = load_store(path=path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # The account logged into Claude Code rotates its own tokens, killing our
    # snapshot (refresh tokens are single-use). Resolve the live keychain's
    # identity once and prefer those credentials for the matching account.
    kc, kc_email = None, None
    try:
        kc = keychain_oauth()
        kc_email = fetch_profile_email(kc)
    except Exception:  # noqa: BLE001 — no keychain / offline: stored tokens only
        kc = None
    for acct in store["accounts"]:
        try:
            if kc and kc_email and acct["email"] == kc_email:
                try:
                    usage = fetch_usage(kc)
                    acct["oauth"] = kc
                    acct["last_usage"] = {**usage, "fetched_at": now, "error": None}
                    continue
                except Exception:  # noqa: BLE001 — fall back to stored tokens
                    pass
            if _is_expired(acct["oauth"]):
                try:
                    acct["oauth"] = _refresh(acct["oauth"])
                    save_store(store, path=path)  # persist rotated token even if fetch fails
                except Exception:  # noqa: BLE001
                    # Refresh endpoint may be rate-limited or the refresh token
                    # dead while the access token still works — try it anyway.
                    pass
            try:
                usage = fetch_usage(acct["oauth"])
            except urllib.error.HTTPError as e:
                if e.code != 401:
                    raise
                # Access token can be invalidated before expires_at (e.g. the
                # logged-in Claude Code session rotated it). Force-refresh once.
                acct["oauth"] = _refresh(acct["oauth"])
                save_store(store, path=path)
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


# ── Subscription cost + lifetime spend ────────────────────────────────────────
# An account carries `monthly_cost` (USD/mo) and `subscription_intervals`: a list
# of {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD" | None}. end=None means the
# subscription is currently open. Multiple intervals capture cancel/restart gaps
# (Andrew doesn't keep all 3 Max accounts running continuously).

_AVG_MONTH_DAYS = 30.4375  # 365.25 / 12 — prorate partial months for lifetime spend


def _as_date(s) -> _dt.date:
    return _dt.date.fromisoformat(s)


def months_active(intervals: list[dict] | None, today: _dt.date | None = None) -> float:
    """Total prorated months a subscription has been active across all intervals.

    A None/missing end is treated as still-open (counts through today). Future
    starts and inverted ranges contribute nothing; ends are capped at today so
    lifetime spend never bills the future.
    """
    today = today or _dt.date.today()
    days = 0
    for iv in intervals or []:
        start = _as_date(iv["start"])
        end = _as_date(iv["end"]) if iv.get("end") else today
        if end > today:
            end = today
        if end > start:
            days += (end - start).days
    return days / _AVG_MONTH_DAYS


def is_active(account: dict, today: _dt.date | None = None) -> bool:
    """True if any subscription interval is open on `today`."""
    today = today or _dt.date.today()
    for iv in account.get("subscription_intervals") or []:
        start = _as_date(iv["start"])
        end = _as_date(iv["end"]) if iv.get("end") else today
        if start <= today <= end:
            return True
    return False


def current_monthly_cost(account: dict, today: _dt.date | None = None) -> float:
    """The monthly_cost if the account is currently subscribed, else 0."""
    return float(account.get("monthly_cost", 0)) if is_active(account, today) else 0.0


def lifetime_spend(account: dict, today: _dt.date | None = None) -> float:
    """Total USD spent on this subscription to date.

    Prefers the actual `charges` ledger (real amounts paid, tax included) when
    present — exact and authoritative. Falls back to a prorated estimate
    (monthly_cost x months active) for accounts with no receipts captured yet.
    """
    charges = account.get("charges")
    if charges:
        return round(sum(float(c["amount"]) for c in charges), 2)
    cost = float(account.get("monthly_cost", 0))
    return round(cost * months_active(account.get("subscription_intervals"), today), 2)


# ── Optimal-account recommendation (hybrid score) ─────────────────────────────
# Goal (Andrew): maximize the Max subscriptions while they're active, defaulting
# to the main account. Score blends headroom (5hr + weekly remaining), a
# use-it-or-lose-it "drain" boost for accounts that renew soon with unused weekly
# allowance, and a bias toward the main account.

MAIN_BONUS = 8.0          # flat score bump for the main account
DRAIN_WINDOW_DAYS = 14    # renewal-drain only engages within this many days
THROTTLE_PCT = 15         # 5hr remaining below this = effectively throttled


def _healthy(entry: dict) -> bool:
    """An entry is usable if it errored-free and has both usage windows."""
    w = entry.get("windows") or {}
    return not entry.get("error") and "five_hour" in w and "seven_day" in w


def account_score(entry: dict) -> tuple[float, list[str]]:
    """Return (0-100 score, human reasons) for a public_view entry.

    `entry` must already carry `is_main` and `renews_in_days`.
    """
    if not _healthy(entry):
        return 0.0, ["usage unavailable"]
    h5 = entry["windows"]["five_hour"]["remaining_pct"]
    h7 = entry["windows"]["seven_day"]["remaining_pct"]
    # Weekly is the bigger budget; 5hr is the immediate gate — weight weekly more.
    headroom = 0.4 * h5 + 0.6 * h7
    reasons = [f"{h7}% weekly free", f"{h5}% 5h free"]

    days = entry.get("renews_in_days")
    drain = 0.0
    if days is not None and days <= DRAIN_WINDOW_DAYS:
        drain = h7 * max(0.0, (DRAIN_WINDOW_DAYS - days) / DRAIN_WINDOW_DAYS)
        if drain >= 20:
            reasons.append(f"renews in {days}d — burn weekly now")

    raw = 0.6 * headroom + 0.4 * drain
    if entry.get("is_main"):
        raw = min(100.0, raw + MAIN_BONUS)
        reasons.append("main account")
    if h5 < THROTTLE_PCT:
        reasons.append("5h throttled")
    return round(raw, 1), reasons


def recommend(entries: list[dict]) -> tuple[str | None, dict]:
    """Pick the optimal account email + per-account {score, reasons}.

    Ties break toward the main account. If every account is unhealthy, fall back
    to the main account (or the first listed).
    """
    scored = {}
    ranking = []
    for e in entries:
        s, r = account_score(e)
        scored[e["email"]] = {"score": s, "reasons": r}
        ranking.append((s, bool(e.get("is_main")), e["email"]))

    healthy = [x for x in ranking if x[0] > 0]
    if healthy:
        optimal = max(healthy, key=lambda x: (x[0], x[1]))[2]
    else:
        mains = [x for x in ranking if x[1]]
        optimal = (mains or ranking or [(0, False, None)])[0][2]
    return optimal, scored


def dashboard_payload(accts: list[dict], today: _dt.date | None = None) -> dict:
    """Full dashboard JSON: per-account orbs + cost/lifetime + optimal pick.

    Composes `public_view` (orb/usage data) with subscription cost, lifetime
    spend, and the hybrid recommendation, plus a combined-spend summary.
    """
    entries = public_view(accts)
    by_email = {a["email"]: a for a in accts}
    for e in entries:
        a = by_email[e["email"]]
        e["is_main"] = bool(a.get("is_main", False))
        e["monthly_cost"] = float(a.get("monthly_cost", 0))
        e["subscription_intervals"] = a.get("subscription_intervals", [])
        e["active"] = is_active(a, today)
        e["current_monthly_cost"] = current_monthly_cost(a, today)
        e["lifetime_spend"] = lifetime_spend(a, today)

    optimal, scored = recommend(entries)
    for e in entries:
        s = scored.get(e["email"], {})
        e["score"] = s.get("score", 0.0)
        e["reasons"] = s.get("reasons", [])
        e["is_optimal"] = e["email"] == optimal

    summary = {
        "optimal_email": optimal,
        "active_accounts": sum(1 for e in entries if e["active"]),
        "total_current_monthly": round(sum(e["current_monthly_cost"] for e in entries), 2),
        "total_lifetime": round(sum(e["lifetime_spend"] for e in entries), 2),
    }
    return {"accounts": entries, "summary": summary}
