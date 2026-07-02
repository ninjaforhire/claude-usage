"""Tests for cli.fable-next — Fable-5 account routing logic."""

from unittest import mock

from cli import (
    _fable_rank,
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


# ── --switch (live keychain snapshot) ─────────────────────────────────────────

def _fake_accts(tracked_emails, *, detected_email, oauth=None):
    """Build a stand-in accounts module for _switch_to_live_keychain."""
    m = mock.Mock()
    m.keychain_oauth.return_value = oauth or {"access_token": "t"}
    m.fetch_profile_email.return_value = detected_email
    m.fetch_usage.return_value = {"five_hour": {}, "seven_day": {}}
    m.load_store.return_value = {"accounts": [{"email": e} for e in tracked_emails]}
    return m


def test_switch_snapshots_tracked_account():
    m = _fake_accts(["a@x.com", "b@x.com"], detected_email="b@x.com")
    assert _switch_to_live_keychain(m) == "b@x.com"
    m.update_oauth.assert_called_once()
    m.set_keychain_owner.assert_called_once_with("b@x.com")


def test_switch_refuses_untracked_account():
    m = _fake_accts(["a@x.com"], detected_email="stranger@x.com")
    assert _switch_to_live_keychain(m) is None
    m.update_oauth.assert_not_called()
    m.set_keychain_owner.assert_not_called()


def test_switch_handles_unreadable_keychain():
    m = mock.Mock()
    m.keychain_oauth.side_effect = RuntimeError("no keychain")
    assert _switch_to_live_keychain(m) is None
    m.set_keychain_owner.assert_not_called()


def test_switch_saves_creds_even_when_usage_fetch_fails():
    m = _fake_accts(["a@x.com"], detected_email="a@x.com")
    m.fetch_usage.side_effect = RuntimeError("429")
    assert _switch_to_live_keychain(m) == "a@x.com"
    # usage falls back to None, but creds + ownership still persist
    args, _ = m.update_oauth.call_args
    assert args[0] == "a@x.com"
    assert args[2] is None
    m.set_keychain_owner.assert_called_once_with("a@x.com")
