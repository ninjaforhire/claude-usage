"""Tests for accounts.py — store layer, token refresh, usage fetch, presentation."""

import datetime as dt
import io
import json
import stat
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    """Refresh AND usage fetch both fail -> that account grays; others fine."""
    path = tmp_path / "s.json"
    bad = _expired_acct()
    bad["oauth"]["access_token"] = "dead_at"
    good = _acct("good@b.com")
    good["oauth"]["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    accounts.save_store({"accounts": [bad, good]}, path=path)

    def fake_get(url, headers, timeout=10):
        if "dead_at" in headers.get("Authorization", ""):
            raise OSError("invalid token")
        return USAGE_RESPONSE

    with patch.object(accounts, "keychain_oauth", side_effect=OSError("no keychain")), \
         patch.object(accounts, "_post_json", side_effect=OSError("401")), \
         patch.object(accounts, "_get_json", side_effect=fake_get):
        result = accounts.fetch_all_usage(path=path)
    by_email = {r["email"]: r for r in result}
    assert by_email["a@b.com"]["last_usage"]["error"]
    assert by_email["good@b.com"]["last_usage"]["error"] is None


def test_stale_refresh_but_valid_access_token_still_fetches(tmp_path):
    """Refresh endpoint failing (429/dead) must not gray an account whose
    access token still works."""
    path = tmp_path / "s.json"
    accounts.save_store({"accounts": [_expired_acct()]}, path=path)
    with patch.object(accounts, "keychain_oauth", side_effect=OSError("no keychain")), \
         patch.object(accounts, "_post_json", side_effect=OSError("429")), \
         patch.object(accounts, "_get_json", return_value=USAGE_RESPONSE):
        result = accounts.fetch_all_usage(path=path)
    assert result[0]["last_usage"]["error"] is None
    assert result[0]["last_usage"]["five_hour"]["utilization"] == 42.0


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

    with patch.object(accounts, "keychain_oauth", side_effect=OSError("no keychain")), \
         patch.object(accounts, "_get_json", side_effect=fake_get), \
         patch.object(accounts, "_post_json") as post:
        post.return_value = {"access_token": "new_at",
                             "refresh_token": "new_rt", "expires_in": 3600}
        result = accounts.fetch_all_usage(path=path)
    assert post.call_count == 1
    assert calls["n"] == 2
    assert result[0]["last_usage"]["error"] is None
    store = accounts.load_store(path=path)
    assert store["accounts"][0]["oauth"]["access_token"] == "new_at"



# ── update_oauth: re-capture must preserve billing history ───────────────────

def _full_acct(email="x@y.com"):
    return {
        "email": email,
        "plan": "max_20x",
        "billing_day": 12,
        "oauth": {"access_token": "old", "refresh_token": "oldr",
                  "expires_at": "2020-01-01T00:00:00Z"},
        "last_usage": {"error": "HTTP Error 400: Bad Request"},
        "is_main": False,
        "monthly_cost": 213.2,
        "charges": [{"date": "2026-06-11", "amount": 213.2}],
        "subscription_intervals": [{"start": "2026-06-11", "end": None}],
    }


def test_update_oauth_preserves_billing_history(tmp_path):
    path = tmp_path / "s.json"
    accounts.save_store({"accounts": [_full_acct()]}, path=path)
    fresh = {"access_token": "new", "refresh_token": "newr",
             "expires_at": "2099-01-01T00:00:00Z"}
    usage = {"five_hour": {"utilization": 3.0, "resets_at": "2099"},
             "seven_day": {"utilization": 9.0, "resets_at": "2099"}}
    assert accounts.update_oauth("x@y.com", fresh, usage, path=path) is True
    a = accounts.load_store(path=path)["accounts"][0]
    assert a["oauth"]["access_token"] == "new"
    assert a["charges"][0]["amount"] == 213.2
    assert a["subscription_intervals"][0]["start"] == "2026-06-11"
    assert a["billing_day"] == 12 and a["monthly_cost"] == 213.2
    assert a["last_usage"]["error"] is None


def test_update_oauth_without_usage_keeps_prior_usage(tmp_path):
    path = tmp_path / "s.json"
    accounts.save_store({"accounts": [_full_acct()]}, path=path)
    fresh = {"access_token": "new", "refresh_token": "newr",
             "expires_at": "2099-01-01T00:00:00Z"}
    assert accounts.update_oauth("x@y.com", fresh, None, path=path) is True
    a = accounts.load_store(path=path)["accounts"][0]
    assert a["oauth"]["access_token"] == "new"
    assert a["last_usage"] == {"error": "HTTP Error 400: Bad Request"}


def test_update_oauth_unknown_email_returns_false(tmp_path):
    path = tmp_path / "s.json"
    accounts.save_store({"accounts": [_full_acct()]}, path=path)
    assert accounts.update_oauth("ghost@nope.com", {}, None, path=path) is False


class TestUsageCooldowns(unittest.TestCase):
    """Regression coverage for server-directed usage API cooldowns."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "accounts.json"
        self.http_errors = []

    def tearDown(self):
        for error in self.http_errors:
            error.close()
        self.tmpdir.cleanup()

    def _http_error(self, code, body, retry_after=None):
        headers = {}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        error = urllib.error.HTTPError(
            accounts.USAGE_URL,
            code,
            "usage error",
            headers,
            io.BytesIO(json.dumps(body).encode()),
        )
        self.http_errors.append(error)
        return error

    @staticmethod
    def _cached_account():
        acct = _acct()
        acct["last_usage"] = {
            **USAGE_RESPONSE,
            "fetched_at": "2026-07-12T12:00:00Z",
            "error": None,
        }
        return acct

    def _fetch_with_usage(self, account, side_effect):
        accounts.save_store({"accounts": [account]}, path=self.path)
        usage_patch = (
            patch.object(accounts, "fetch_usage", side_effect=side_effect)
            if isinstance(side_effect, Exception)
            else patch.object(accounts, "fetch_usage", return_value=side_effect)
        )
        with patch.object(
            accounts, "keychain_oauth", side_effect=OSError("no keychain")
        ), usage_patch:
            return accounts.fetch_all_usage(path=self.path)[0]

    def assert_retry_delay(self, usage, expected_seconds):
        fetched = datetime.strptime(
            usage["fetched_at"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        retry = datetime.strptime(
            usage["retry_until"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        self.assertAlmostEqual(
            (retry - fetched).total_seconds(), expected_seconds, delta=1
        )

    def test_429_honors_retry_after_and_preserves_windows(self):
        prior_success = self._cached_account()["last_usage"]["fetched_at"]
        error = self._http_error(
            429,
            {"type": "error", "error": {"message": "Slow down"}},
            retry_after=120,
        )
        result = self._fetch_with_usage(self._cached_account(), error)

        self.assertEqual(result["last_usage"]["error"], "Slow down")
        self.assertEqual(result["last_usage"]["error_kind"], "rate_limit")
        self.assertEqual(result["last_usage"]["last_success_at"], prior_success)
        self.assertNotEqual(result["last_usage"]["fetched_at"], prior_success)
        self.assertEqual(result["last_usage"]["five_hour"], USAGE_RESPONSE["five_hour"])
        self.assertEqual(result["last_usage"]["seven_day"], USAGE_RESPONSE["seven_day"])
        self.assert_retry_delay(result["last_usage"], 120)

    def test_chained_failures_preserve_original_success_time(self):
        acct = self._cached_account()
        prior_success = acct["last_usage"]["fetched_at"]
        first_error = self._http_error(429, {"error": {"message": "First"}})
        second_error = self._http_error(429, {"error": {"message": "Second"}})

        accounts._record_usage_http_error(
            acct, first_error, datetime(2026, 7, 12, 13, tzinfo=timezone.utc)
        )
        accounts._record_usage_http_error(
            acct, second_error, datetime(2026, 7, 12, 14, tzinfo=timezone.utc)
        )

        self.assertEqual(acct["last_usage"]["last_success_at"], prior_success)
        self.assertEqual(acct["last_usage"]["fetched_at"], "2026-07-12T14:00:00Z")

    def test_429_without_retry_after_uses_fallback(self):
        error = self._http_error(429, {"error": {"message": "Rate limited"}})
        result = self._fetch_with_usage(_acct(), error)

        self.assertEqual(result["last_usage"]["error_kind"], "rate_limit")
        self.assert_retry_delay(result["last_usage"], accounts.RATE_LIMIT_BACKOFF)

    def test_future_cooldown_skips_all_account_http_and_preserves_usage(self):
        acct = self._cached_account()
        acct["last_usage"].update(
            {
                "error": "Slow down",
                "error_kind": "rate_limit",
                "retry_until": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        before = json.dumps(acct["last_usage"], sort_keys=True)
        accounts.save_store({"accounts": [acct]}, path=self.path)

        with patch.object(accounts, "keychain_oauth") as keychain, patch.object(
            accounts, "fetch_profile_email"
        ) as profile, patch.object(accounts, "fetch_usage") as usage, patch.object(
            accounts, "_refresh"
        ) as refresh:
            result = accounts.fetch_all_usage(path=self.path)[0]

        keychain.assert_not_called()
        profile.assert_not_called()
        usage.assert_not_called()
        refresh.assert_not_called()
        self.assertEqual(json.dumps(result["last_usage"], sort_keys=True), before)

    def test_expired_cooldown_fetches_again(self):
        acct = self._cached_account()
        acct["last_usage"]["retry_until"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        accounts.save_store({"accounts": [acct]}, path=self.path)

        with patch.object(
            accounts, "keychain_oauth", side_effect=OSError("no keychain")
        ), patch.object(
            accounts, "fetch_usage", return_value=USAGE_RESPONSE
        ) as usage:
            result = accounts.fetch_all_usage(path=self.path)[0]

        usage.assert_called_once()
        self.assertIsNone(result["last_usage"]["error"])

    def test_403_surfaces_api_message_and_does_not_refresh(self):
        message = "OAuth authentication is currently not allowed for this organization."
        error = self._http_error(
            403,
            {
                "type": "error",
                "error": {"type": "permission_error", "message": message},
            },
        )
        accounts.save_store({"accounts": [self._cached_account()]}, path=self.path)

        with patch.object(
            accounts, "keychain_oauth", side_effect=OSError("no keychain")
        ), patch.object(accounts, "fetch_usage", side_effect=error), patch.object(
            accounts, "_refresh"
        ) as refresh:
            result = accounts.fetch_all_usage(path=self.path)[0]

        refresh.assert_not_called()
        self.assertEqual(result["last_usage"]["error"], message)
        self.assertEqual(result["last_usage"]["error_kind"], "permission")
        self.assert_retry_delay(result["last_usage"], accounts.PERMISSION_BACKOFF)

    def test_401_still_force_refreshes_once_and_retries(self):
        unauthorized = self._http_error(401, {"error": {"message": "Unauthorized"}})
        refreshed = {
            "access_token": "new_at",
            "refresh_token": "new_rt",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        accounts.save_store({"accounts": [_acct()]}, path=self.path)

        with patch.object(
            accounts, "keychain_oauth", side_effect=OSError("no keychain")
        ), patch.object(
            accounts, "fetch_usage", side_effect=[unauthorized, USAGE_RESPONSE]
        ) as usage, patch.object(
            accounts, "_refresh", return_value=refreshed
        ) as refresh:
            result = accounts.fetch_all_usage(path=self.path)[0]

        refresh.assert_called_once()
        self.assertEqual(usage.call_count, 2)
        self.assertIsNone(result["last_usage"]["error"])

    def test_keychain_403_does_not_fall_back_to_stored_credentials(self):
        error = self._http_error(403, {"error": {"message": "Org blocked"}})
        accounts.save_store({"accounts": [_acct()]}, path=self.path)

        with patch.object(accounts, "keychain_oauth", return_value={"access_token": "kc"}), \
             patch.object(accounts, "fetch_profile_email", return_value="a@b.com"), \
             patch.object(accounts, "fetch_usage", side_effect=error) as usage:
            result = accounts.fetch_all_usage(path=self.path)[0]

        usage.assert_called_once()
        self.assertEqual(result["last_usage"]["error_kind"], "permission")

    def test_success_clears_prior_error_metadata(self):
        acct = self._cached_account()
        acct["last_usage"].update(
            {
                "error": "old",
                "error_kind": "rate_limit",
                "retry_until": "2020-01-01T00:00:00Z",
            }
        )
        result = self._fetch_with_usage(acct, USAGE_RESPONSE)

        self.assertIsNone(result["last_usage"]["error"])
        self.assertEqual(
            result["last_usage"]["last_success_at"],
            result["last_usage"]["fetched_at"],
        )
        self.assertNotIn("error_kind", result["last_usage"])
        self.assertNotIn("retry_until", result["last_usage"])

    def test_transient_error_clears_prior_cooldown_metadata(self):
        acct = self._cached_account()
        acct["last_usage"].update(
            {
                "error": "old rate limit",
                "error_kind": "rate_limit",
                "last_success_at": "2026-07-12T12:00:00Z",
                "retry_until": "2020-01-01T00:00:00Z",
            }
        )

        result = self._fetch_with_usage(
            acct, urllib.error.URLError("network down")
        )

        self.assertEqual(result["last_usage"]["five_hour"], USAGE_RESPONSE["five_hour"])
        self.assertEqual(result["last_usage"]["seven_day"], USAGE_RESPONSE["seven_day"])
        self.assertIn("network down", result["last_usage"]["error"])
        self.assertEqual(
            result["last_usage"]["last_success_at"], "2026-07-12T12:00:00Z"
        )
        self.assertIsNone(result["last_usage"]["error_kind"])
        self.assertIsNone(result["last_usage"]["retry_until"])

    def test_public_view_passes_error_kind_and_retry_until(self):
        acct = self._cached_account()
        acct["last_usage"].update(
            {
                "error": "Org blocked",
                "error_kind": "permission",
                "last_success_at": "2026-07-12T12:00:00Z",
                "retry_until": "2026-07-12T18:00:00Z",
            }
        )

        view = accounts.public_view([acct])[0]

        self.assertEqual(view["error_kind"], "permission")
        self.assertEqual(view["last_success_at"], "2026-07-12T12:00:00Z")
        self.assertEqual(view["retry_until"], "2026-07-12T18:00:00Z")
