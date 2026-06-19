"""Read Codex (ChatGPT/OpenAI) 5h + weekly rate limits from the Codex CLI sessions.

The Codex CLI records a ``rate_limits`` snapshot in each session rollout JSONL at
``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``:

    "rate_limits": {
        "primary":   {"used_percent": 5.0,  "window_minutes": 300,   "resets_at": <epoch>},
        "secondary": {"used_percent": 11.0, "window_minutes": 10080, "resets_at": <epoch>},
        "plan_type": "prolite", ...
    }

primary = the 5-hour window, secondary = the weekly window. The newest snapshot is
the current limit state. ``codex_orb_data`` maps it to the same shape the Claude
account orbs use (``windows.five_hour`` / ``windows.seven_day`` with remaining_pct,
resets_at, color_hi/lo), so the dashboard renders Codex orbs identically.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import accounts  # reuse remaining_color()

SESSIONS_DIR = Path.home() / ".codex" / "sessions"
_MAX_FILES_SCANNED = 12  # newest few rollouts always carry a fresh snapshot

# ── Plan-tier caps ────────────────────────────────────────────────────────────
# Each entry defines the billing cost and the rate-limit windows for the plan.
# five_hour_limit_h: the total compute-hours available in the 5-hour rolling window.
# seven_day_limit_h: the total compute-hours available in the 7-day rolling window.
# monthly_usd: the flat subscription price in USD.
#
# pro-5x ($100/mo) is 5× the Plus (chatgpt-plus / prolite) tier — it must be
# distinct from chatgpt-pro ($200/mo), which ships higher / effectively unlimited caps.
PLAN_CAPS: dict[str, dict] = {
    "chatgpt-plus": {
        "monthly_usd": 20,
        "five_hour_limit_h": 5.0,
        "seven_day_limit_h": 35.0,   # ~5h/day × 7
    },
    "pro-5x": {
        "monthly_usd": 100,
        "five_hour_limit_h": 25.0,   # 5× Plus five-hour cap
        "seven_day_limit_h": 175.0,  # 5× Plus weekly cap
    },
    "chatgpt-pro": {
        "monthly_usd": 200,
        "five_hour_limit_h": None,   # Pro = effectively unlimited (no published hard cap)
        "seven_day_limit_h": None,
    },
}


def get_plan_caps(plan: str) -> dict:
    """Return the cap entry for *plan*, or an empty dict if unknown."""
    return PLAN_CAPS.get(plan, {})


def _find_rate_limits(obj: object) -> dict | None:
    """Locate a ``rate_limits`` dict anywhere in a parsed JSONL record."""
    if isinstance(obj, dict):
        rl = obj.get("rate_limits")
        if isinstance(rl, dict):
            return rl
        for v in obj.values():
            found = _find_rate_limits(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_rate_limits(v)
            if found:
                return found
    return None


def _last_rate_limits_in(path: Path) -> dict | None:
    """The last ``rate_limits`` snapshot in one rollout file (most recent wins)."""
    last = None
    try:
        lines = path.read_text(errors="ignore").splitlines()
    except OSError:
        return None
    for line in lines:
        if '"rate_limits"' not in line:
            continue
        try:
            rl = _find_rate_limits(json.loads(line))
        except ValueError:
            continue
        if rl:
            last = rl
    return last


def latest_rate_limits(sessions_dir: Path = SESSIONS_DIR) -> dict | None:
    """The most recent Codex rate-limit snapshot across all session rollouts."""
    files = sorted(sessions_dir.glob("**/rollout-*.jsonl"))
    for path in reversed(files[-_MAX_FILES_SCANNED:] if len(files) > _MAX_FILES_SCANNED else files):
        rl = _last_rate_limits_in(path)
        if rl:
            return rl
    return None


def _window(part: dict | None) -> dict | None:
    """Map a primary/secondary limit part to the orb window shape."""
    if not part:
        return None
    remaining = round(min(100, max(0, 100 - part.get("used_percent", 0))))
    hi, lo = accounts.remaining_color(remaining)
    resets_at = part.get("resets_at")
    iso = (
        _dt.datetime.fromtimestamp(resets_at, tz=_dt.timezone.utc).isoformat()
        if resets_at
        else None
    )
    return {"remaining_pct": remaining, "resets_at": iso, "color_hi": hi, "color_lo": lo}


def codex_orb_data(sessions_dir: Path = SESSIONS_DIR, plan: str | None = None) -> dict:
    """Codex 5h + weekly orbs in the same shape as a Claude account entry.

    Args:
        sessions_dir: Root of the Codex CLI session rollouts.
        plan: The *billing* plan string from ``subscriptions.json`` (e.g.
            ``"pro-5x"``). The CLI rate-limit snapshot only carries an internal
            ``plan_type`` (e.g. ``"prolite"``), which does NOT distinguish the
            $100 pro-5x tier from the $200 chatgpt-pro tier. Passing the billing
            plan lets the orb card show the correct tier label + caps.

    Returns a dict with ``plan_type`` (CLI internal), ``plan`` (billing tier),
    ``caps`` (the resolved :data:`PLAN_CAPS` entry for *plan*), ``windows``, and
    ``error``.
    """
    rl = latest_rate_limits(sessions_dir)
    if not rl:
        return {"plan_type": None, "plan": plan, "caps": get_plan_caps(plan or ""),
                "windows": {}, "error": "no Codex rate-limit data found"}
    windows = {}
    five = _window(rl.get("primary"))
    week = _window(rl.get("secondary"))
    if five:
        windows["five_hour"] = five
    if week:
        windows["seven_day"] = week
    return {"plan_type": rl.get("plan_type"), "plan": plan,
            "caps": get_plan_caps(plan or ""), "windows": windows, "error": None}
