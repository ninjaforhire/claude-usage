"""Tests for accounts.py — store layer, token refresh, usage fetch, presentation."""

import datetime as dt
import json
import stat
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import accounts


def _acct(email="a@b.com"):
    return {
        "email": email,
        "plan": "max_20x",
        "billing_day": 11,
        "oauth": {"access_token": "at", "refresh_token": "rt",
                  "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)
                                 ).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "last_usage": None,
    }


# ── Task 1: Store layer ──────────────────────────────────────────────────────

def test_store_round_trip(tmp_path):
    path = tmp_path / "usage_accounts.json"
    accounts.save_store({"accounts": [_acct()]}, path=path)
    loaded = accounts.load_store(path=path)
    assert loaded["accounts"][0]["email"] == "a@b.com"


def test_store_file_mode_600(tmp_path):
    path = tmp_path / "usage_accounts.json"
    accounts.save_store({"accounts": []}, path=path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_load_missing_store_returns_empty(tmp_path):
    assert accounts.load_store(path=tmp_path / "nope.json") == {"accounts": []}


def test_upsert_replaces_by_email(tmp_path):
    path = tmp_path / "s.json"
    accounts.save_store({"accounts": [_acct()]}, path=path)
    newer = _acct()
    newer["billing_day"] = 25
    accounts.upsert_account(newer, path=path)
    store = accounts.load_store(path=path)
    assert len(store["accounts"]) == 1
    assert store["accounts"][0]["billing_day"] == 25


# ── Task 2: Token refresh + usage fetch ─────────────────────────────────────

def _expired_acct():
    a = _acct()
    a["oauth"]["expires_at"] = "2020-01-01T00:00:00Z"
    return a


USAGE_RESPONSE = {
    "five_hour": {"utilization": 42.0, "resets_at": "2026-06-12T19:00:00Z"},
    "seven_day": {"utilization": 17.0, "resets_at": "2026-06-15T07:00:00Z"},
}


def test_fetch_refreshes_expired_token(tmp_path):
    path = tmp_path / "s.json"
    accounts.save_store({"accounts": [_expired_acct()]}, path=path)
    with patch.object(accounts, "_post_json") as post, \
         patch.object(accounts, "_get_json") as get:
        post.return_value = {"access_token": "new_at",
                             "refresh_token": "new_rt", "expires_in": 3600}
        get.return_value = USAGE_RESPONSE
        result = accounts.fetch_all_usage(path=path)
    store = accounts.load_store(path=path)
    assert store["accounts"][0]["oauth"]["access_token"] == "new_at"
    assert store["accounts"][0]["oauth"]["refresh_token"] == "new_rt"
    assert result[0]["last_usage"]["five_hour"]["utilization"] == 42.0
    assert result[0]["last_usage"]["error"] is None


def test_fetch_skips_refresh_when_token_fresh(tmp_path):
    path = tmp_path / "s.json"
    a = _acct()
    a["oauth"]["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    accounts.save_store({"accounts": [a]}, path=path)
    with patch.object(accounts, "_post_json") as post, \
         patch.object(accounts, "_get_json") as get:
        get.return_value = USAGE_RESPONSE
        accounts.fetch_all_usage(path=path)
    post.assert_not_called()


def test_refresh_failure_grays_account_not_others(tmp_path):
    path = tmp_path / "s.json"
    bad = _expired_acct()
    good = _acct("good@b.com")
    good["oauth"]["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    accounts.save_store({"accounts": [bad, good]}, path=path)
    with patch.object(accounts, "_post_json", side_effect=OSError("401")), \
         patch.object(accounts, "_get_json", return_value=USAGE_RESPONSE):
        result = accounts.fetch_all_usage(path=path)
    by_email = {r["email"]: r for r in result}
    assert by_email["a@b.com"]["last_usage"]["error"]
    assert by_email["good@b.com"]["last_usage"]["error"] is None


def test_rotated_tokens_persist_when_usage_fetch_fails(tmp_path):
    """Refresh succeeds (tokens rotated) but usage fetch fails — rotated
    tokens MUST be on disk (refresh tokens may be single-use) and the
    account must carry an error."""
    path = tmp_path / "s.json"
    accounts.save_store({"accounts": [_expired_acct()]}, path=path)
    with patch.object(accounts, "_post_json") as post, \
         patch.object(accounts, "_get_json", side_effect=OSError("network down")):
        post.return_value = {"access_token": "new_at",
                             "refresh_token": "new_rt", "expires_in": 3600}
        result = accounts.fetch_all_usage(path=path)
    store = accounts.load_store(path=path)
    assert store["accounts"][0]["oauth"]["access_token"] == "new_at"
    assert store["accounts"][0]["oauth"]["refresh_token"] == "new_rt"
    assert result[0]["last_usage"]["error"]


def test_malformed_usage_response_grays_account(tmp_path):
    path = tmp_path / "s.json"
    a = _acct()
    a["oauth"]["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    accounts.save_store({"accounts": [a]}, path=path)
    with patch.object(accounts, "_get_json", return_value={}):
        result = accounts.fetch_all_usage(path=path)
    assert result[0]["last_usage"]["error"]


# ── Task 3: Color ramp, billing countdown, public_view ───────────────────────


def test_color_ramp_boundaries():
    assert accounts.remaining_color(60)[0] == "#39ff6e"   # neon green
    assert accounts.remaining_color(59)[0] == "#ffe23d"   # yellow
    assert accounts.remaining_color(35)[0] == "#ffe23d"
    assert accounts.remaining_color(34)[0] == "#ff9433"   # orange
    assert accounts.remaining_color(15)[0] == "#ff9433"
    assert accounts.remaining_color(14)[0] == "#ff3b3b"   # red


def test_days_until_renewal_same_month():
    today = dt.date(2026, 6, 12)
    assert accounts.days_until_renewal(20, today=today) == 8


def test_days_until_renewal_today_is_zero():
    today = dt.date(2026, 6, 12)
    assert accounts.days_until_renewal(12, today=today) == 0


def test_days_until_renewal_rolls_to_next_month():
    today = dt.date(2026, 6, 12)
    assert accounts.days_until_renewal(5, today=today) == 23  # Jul 5


def test_days_until_renewal_clamps_short_month():
    today = dt.date(2026, 2, 1)
    assert accounts.days_until_renewal(31, today=today) == 27  # Feb 28


def test_public_view_strips_oauth(tmp_path):
    acct = _acct()
    acct["last_usage"] = {
        "five_hour": {"utilization": 40.0, "resets_at": "2026-06-12T19:00:00Z"},
        "seven_day": {"utilization": 10.0, "resets_at": "2026-06-15T07:00:00Z"},
        "fetched_at": "2026-06-12T14:00:00Z",
        "error": None,
    }
    views = accounts.public_view([acct])
    assert len(views) == 1
    assert "oauth" not in views[0]
    assert views[0]["windows"]["five_hour"]["remaining_pct"] == 60
    assert views[0]["windows"]["five_hour"]["color_hi"] == "#39ff6e"


def test_public_view_remaining_is_int():
    acct = _acct()
    acct["last_usage"] = {
        "five_hour": {"utilization": 59.0, "resets_at": "2026-06-12T19:00:00Z"},
        "seven_day": {"utilization": 11.0, "resets_at": "2026-06-15T07:00:00Z"},
        "fetched_at": "2026-06-12T14:00:00Z",
        "error": None,
    }
    views = accounts.public_view([acct])
    assert isinstance(views[0]["windows"]["five_hour"]["remaining_pct"], int)
    assert views[0]["windows"]["five_hour"]["remaining_pct"] == 41


def test_401_on_fresh_token_forces_refresh_and_retries(tmp_path):
    """Access token invalidated before expires_at (rotated elsewhere) — one forced refresh."""
    import urllib.error
    path = tmp_path / "s.json"
    accounts.save_store({"accounts": [_acct()]}, path=path)
    calls = {"n": 0}

    def fake_get(url, headers, timeout=10):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", None, None)
        return USAGE_RESPONSE

    with patch.object(accounts, "_get_json", side_effect=fake_get), \
         patch.object(accounts, "_post_json") as post:
        post.return_value = {"access_token": "new_at",
                             "refresh_token": "new_rt", "expires_in": 3600}
        result = accounts.fetch_all_usage(path=path)
    assert post.call_count == 1
    assert calls["n"] == 2
    assert result[0]["last_usage"]["error"] is None
    store = accounts.load_store(path=path)
    assert store["accounts"][0]["oauth"]["access_token"] == "new_at"
