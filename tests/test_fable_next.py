"""Tests for cli.fable-next — Fable 5.1 account routing logic."""

from datetime import datetime, timezone

from unittest import mock

from cli import (
    _fable_rank,
    _fable_runtime_status,
    _fmt_reset_local,
    _norm_discount,
    _switch_to_live_keychain,
    FABLE_CAP_PCT,
    DRAIN_BOOST,
)

# ── fable-cost discount parsing ───────────────────────────────────────────────


def test_discount_default_is_30pct():
    assert _norm_discount(None) == 0.30


def test_discount_accepts_percent_int():
    assert _norm_discount("30") == 0.30
    assert _norm_discount("50") == 0.50


def test_discount_accepts_percent_sign():
    assert _norm_discount("30%") == 0.30


def test_discount_accepts_fraction():
    assert _norm_discount("0.3") == 0.3


def test_discount_bad_input_falls_back():
    assert _norm_discount("free") == 0.30


def _entry(
    weekly_free,
    h5,
    *,
    resets_at="2026-07-08T10:00:00Z",
    active=True,
    error=None,
    is_main=False,
    fable_remaining_pct=None,
    fable_resets_at=None,
    fetched_at="2026-07-02T02:55:00Z",
):
    """Build a dashboard-payload-shaped entry for the ranker."""
    windows = {}
    if error is None:
        windows = {
            "five_hour": {"remaining_pct": h5, "resets_at": "2026-07-02T03:00:00Z"},
            "seven_day": {"remaining_pct": weekly_free, "resets_at": resets_at},
        }
        if fable_remaining_pct is not None:
            windows["fable"] = {
                "remaining_pct": fable_remaining_pct,
                "resets_at": fable_resets_at or resets_at,
            }
    return {
        "email": "x@y.com",
        "active": active,
        "error": error,
        "is_main": is_main,
        "renews_in_days": 8,
        "fetched_at": fetched_at,
        "windows": windows,
    }


def test_real_fable_window_overrides_estimate():
    # weekly_free=41 -> old estimate would be 0 (past the 50% line), but the
    # real per-model API field says 27% room is actually left. Real data wins.
    r = _fable_rank(_entry(weekly_free=41, h5=100, fable_remaining_pct=27))
    assert r["fable_room"] == 27
    assert r["score"] > 0


def test_real_fable_window_can_be_lower_than_estimate():
    # weekly_free=86 -> old estimate would be 36, but the real per-model field
    # says only 25% is actually left (other models ate more of the budget).
    r = _fable_rank(_entry(weekly_free=86, h5=100, fable_remaining_pct=25))
    assert r["fable_room"] == 25


def test_fable_room_is_weekly_free_minus_cap():
    r = _fable_rank(_entry(weekly_free=98, h5=90))
    assert r["fable_room"] == 98 - FABLE_CAP_PCT


def test_full_weekly_gives_full_cap_room():
    r = _fable_rank(_entry(weekly_free=100, h5=100))
    assert r["fable_room"] == FABLE_CAP_PCT


def test_running_window_beats_fresh_reserve_despite_less_room():
    # 48% room but a ticking clock should outrank 50% room with no clock.
    running = _fable_rank(
        _entry(weekly_free=98, h5=90, resets_at="2026-07-08T10:00:00Z")
    )
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


def test_auth_broken_account_keeps_real_cached_fable_value_visible():
    entry = _entry(weekly_free=60, h5=80, fable_remaining_pct=20)
    entry.update(
        {"error": "invalid_grant", "error_kind": "auth", "needs_relogin": True}
    )
    rank = _fable_rank(entry)
    assert rank["score"] is None
    assert rank["fable_room"] == 20
    assert rank["stale"] is True
    assert "auth-broken" in rank["reasons"][0]


def test_reset_formatter_handles_none_and_bad_input():
    assert _fmt_reset_local(None) == "--"
    assert _fmt_reset_local("not-a-date") == "--"
    assert _fmt_reset_local("2026-07-08T10:00:00Z") != "--"


def test_runtime_status_uses_fable_51_when_current_profile_is_fresh():
    entry = _entry(weekly_free=70, h5=80, fable_remaining_pct=20)

    status = _fable_runtime_status(
        [entry],
        owner=entry["email"],
        observed_at=datetime(2026, 7, 2, 3, 0, tzinfo=timezone.utc),
    )

    assert status["available"] is True
    assert status["model"] == "claude-fable-5-1"
    assert status["backup_model"] == "claude-opus-5"
    assert status["headroom_percent"] == 20


def test_runtime_status_fails_to_opus_when_fable_headroom_is_exhausted():
    entry = _entry(weekly_free=40, h5=80, fable_remaining_pct=0)

    status = _fable_runtime_status(
        [entry],
        owner=entry["email"],
        observed_at=datetime(2026, 7, 2, 3, 0, tzinfo=timezone.utc),
    )

    assert status["available"] is False
    assert status["reason"] == "fable_headroom_exhausted"
    assert status["headroom_percent"] == 0


def test_runtime_status_rejects_stale_or_unknown_current_profile():
    entry = _entry(
        weekly_free=100,
        h5=100,
        fetched_at="2026-07-02T01:00:00Z",
    )
    observed = datetime(2026, 7, 2, 3, 0, tzinfo=timezone.utc)

    stale = _fable_runtime_status([entry], owner=entry["email"], observed_at=observed)
    unknown = _fable_runtime_status([entry], owner=None, observed_at=observed)

    assert stale["reason"] == "usage_stale"
    assert unknown["reason"] == "current_profile_unknown"
    assert "email" not in stale
    assert "email" not in unknown


def test_runtime_status_zeroes_cached_headroom_when_auth_is_broken():
    entry = _entry(weekly_free=100, h5=100, fable_remaining_pct=40)
    entry.update(
        {
            "error": "invalid_grant",
            "error_kind": "auth",
            "needs_relogin": True,
            "windows": {
                "five_hour": {"remaining_pct": 100, "resets_at": None},
                "seven_day": {"remaining_pct": 100, "resets_at": None},
                "fable": {"remaining_pct": 40, "resets_at": None},
            },
        }
    )

    status = _fable_runtime_status(
        [entry],
        owner=entry["email"],
        observed_at=datetime(2026, 7, 2, 3, 0, tzinfo=timezone.utc),
    )

    assert status["available"] is False
    assert status["reason"] == "auth_broken"
    assert status["headroom_percent"] == 0


# ── --switch (live credential ownership) ─────────────────────────────────────


def _fake_accts(tracked_emails, *, detected_email, oauth=None):
    """Build a stand-in accounts module for _switch_to_live_keychain."""
    m = mock.Mock()
    m.keychain_oauth.return_value = oauth or {"access_token": "t"}
    m.fetch_profile_email.return_value = detected_email
    m.load_store.return_value = {"accounts": [{"email": e} for e in tracked_emails]}
    return m


def test_switch_records_tracked_account_without_copying_oauth():
    m = _fake_accts(["a@x.com", "b@x.com"], detected_email="b@x.com")
    assert _switch_to_live_keychain(m) == "b@x.com"
    m.update_oauth.assert_not_called()
    m.set_keychain_owner.assert_called_once_with("b@x.com")
    m.fetch_all_usage.assert_called_once_with()


def test_switch_refuses_untracked_account():
    m = _fake_accts(["a@x.com"], detected_email="stranger@x.com")
    assert _switch_to_live_keychain(m) is None
    m.update_oauth.assert_not_called()
    m.set_keychain_owner.assert_not_called()
    m.fetch_all_usage.assert_not_called()


def test_switch_handles_unreadable_keychain():
    m = mock.Mock()
    m.keychain_oauth.side_effect = RuntimeError("no keychain")
    assert _switch_to_live_keychain(m) is None
    m.set_keychain_owner.assert_not_called()


def test_switch_refreshes_usage_after_recording_owner():
    m = _fake_accts(["a@x.com"], detected_email="a@x.com")
    assert _switch_to_live_keychain(m) == "a@x.com"
    m.update_oauth.assert_not_called()
    m.set_keychain_owner.assert_called_once_with("a@x.com")
    m.fetch_all_usage.assert_called_once_with()
