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


def codex_orb_data(sessions_dir: Path = SESSIONS_DIR) -> dict:
    """Codex 5h + weekly orbs in the same shape as a Claude account entry."""
    rl = latest_rate_limits(sessions_dir)
    if not rl:
        return {"plan_type": None, "windows": {}, "error": "no Codex rate-limit data found"}
    windows = {}
    five = _window(rl.get("primary"))
    week = _window(rl.get("secondary"))
    if five:
        windows["five_hour"] = five
    if week:
        windows["seven_day"] = week
    return {"plan_type": rl.get("plan_type"), "windows": windows, "error": None}
