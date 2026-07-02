"""Tests for cli.fable-next — Fable-5 account routing logic."""

import pytest

from cli import _fable_rank, _fmt_reset_local, FABLE_CAP_PCT, DRAIN_BOOST


def _entry(weekly_free, h5, *, resets_at="2026-07-08T10:00:00Z",
           active=True, error=None, is_main=False):
    """Build a dashboard-payload-shaped entry for the ranker."""
    windows = {}
    if error is None:
        windows = {
            "five_hour": {"remaining_pct": h5, "resets_at": "2026-07-02T03:00:00Z"},
            "seven_day": {"remaining_pct": weekly_free, "resets_at": resets_at},
        }
    return {
        "email": "x@y.com",
        "active": active,
        "error": error,
        "is_main": is_main,
        "renews_in_days": 8,
        "windows": windows,
    }


def test_fable_room_is_weekly_free_minus_cap():
    r = _fable_rank(_entry(weekly_free=98, h5=90))
    assert r["fable_room"] == 98 - FABLE_CAP_PCT


def test_full_weekly_gives_full_cap_room():
    r = _fable_rank(_entry(weekly_free=100, h5=100))
    assert r["fable_room"] == FABLE_CAP_PCT


def test_running_window_beats_fresh_reserve_despite_less_room():
    # 48% room but a ticking clock should outrank 50% room with no clock.
    running = _fable_rank(_entry(weekly_free=98, h5=90, resets_at="2026-07-08T10:00:00Z"))
    reserve = _fable_rank(_entry(weekly_free=100, h5=100, resets_at=None))
    assert running["score"] > reserve["score"]
    assert running["score"] == 48 + DRAIN_BOOST


def test_exhausted_fable_budget_scores_negative():
    # weekly_free 40 -> already past the 50% Fable line.
    r = _fable_rank(_entry(weekly_free=40, h5=90))
    assert r["fable_room"] == 0
    assert r["score"] == -1.0


def test_throttled_5h_drops_drain_boost():
    r = _fable_rank(_entry(weekly_free=98, h5=10))  # 5h < 15 = throttled
    assert r["score"] == 48  # no +DRAIN_BOOST
    assert any("throttled" in reason for reason in r["reasons"])


def test_inactive_account_excluded():
    r = _fable_rank(_entry(weekly_free=100, h5=100, active=False))
    assert r["score"] is None


def test_error_account_excluded():
    r = _fable_rank(_entry(weekly_free=100, h5=100, error="boom"))
    assert r["score"] is None


def test_reset_formatter_handles_none_and_bad_input():
    assert _fmt_reset_local(None) == "--"
    assert _fmt_reset_local("not-a-date") == "--"
    assert _fmt_reset_local("2026-07-08T10:00:00Z") != "--"
