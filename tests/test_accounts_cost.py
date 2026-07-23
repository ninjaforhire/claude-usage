"""Tests for subscription cost, lifetime spend, and optimal-account scoring."""

import datetime as dt
import unittest

import accounts


# ── Fixtures / helpers ────────────────────────────────────────────────────────

TODAY = dt.date(2026, 6, 15)


def _acct(email, *, is_main=False, cost=200, intervals=None):
    return {
        "email": email,
        "is_main": is_main,
        "plan": "max_20x",
        "monthly_cost": cost,
        "subscription_intervals": intervals if intervals is not None else [],
    }


def _entry(
    email,
    h5,
    h7,
    *,
    is_main=False,
    renews=20,
    error=None,
    windows=True,
    active=True,
):
    """A public_view-shaped entry for score/recommend tests."""
    w = {}
    if windows:
        w = {
            "five_hour": {"remaining_pct": h5},
            "seven_day": {"remaining_pct": h7},
        }
    return {
        "email": email,
        "is_main": is_main,
        "renews_in_days": renews,
        "error": error,
        "windows": w,
        "active": active,
    }


# ── months_active ─────────────────────────────────────────────────────────────

def test_open_interval_counts_through_today():
    iv = [{"start": "2026-04-09", "end": None}]
    months = accounts.months_active(iv, today=TODAY)
    assert round(months, 2) == round((TODAY - dt.date(2026, 4, 9)).days / 30.4375, 2)


def test_closed_interval_uses_its_end():
    iv = [{"start": "2026-01-01", "end": "2026-02-01"}]
    assert accounts.months_active(iv, today=TODAY) == 31 / 30.4375


def test_gap_intervals_sum_only_active_days():
    iv = [
        {"start": "2026-01-01", "end": "2026-01-31"},  # 30 days
        {"start": "2026-03-01", "end": "2026-03-31"},  # 30 days
    ]
    assert round(accounts.months_active(iv, today=TODAY), 4) == round(60 / 30.4375, 4)


def test_future_start_and_inverted_contribute_nothing():
    iv = [
        {"start": "2027-01-01", "end": None},          # future
        {"start": "2026-05-01", "end": "2026-04-01"},  # inverted
    ]
    assert accounts.months_active(iv, today=TODAY) == 0.0


def test_open_end_capped_at_today_not_future():
    # An open interval must never bill beyond today.
    iv = [{"start": "2026-06-01", "end": None}]
    assert accounts.months_active(iv, today=TODAY) == 14 / 30.4375


def test_empty_intervals_zero():
    assert accounts.months_active([], today=TODAY) == 0.0
    assert accounts.months_active(None, today=TODAY) == 0.0


# ── lifetime_spend / is_active / current_monthly_cost ─────────────────────────

def test_lifetime_spend_is_cost_times_months():
    a = _acct("a@b.com", cost=200, intervals=[{"start": "2026-04-09", "end": None}])
    expected = round(200 * (TODAY - dt.date(2026, 4, 9)).days / 30.4375, 2)
    assert accounts.lifetime_spend(a, today=TODAY) == expected


def test_is_active_true_for_open_interval():
    a = _acct("a@b.com", intervals=[{"start": "2026-04-09", "end": None}])
    assert accounts.is_active(a, today=TODAY) is True


def test_is_active_false_when_cancelled():
    a = _acct("a@b.com", intervals=[{"start": "2026-01-01", "end": "2026-02-01"}])
    assert accounts.is_active(a, today=TODAY) is False


def test_current_monthly_cost_zero_when_inactive():
    a = _acct("a@b.com", cost=200, intervals=[{"start": "2026-01-01", "end": "2026-02-01"}])
    assert accounts.current_monthly_cost(a, today=TODAY) == 0.0


def test_current_monthly_cost_full_when_active():
    a = _acct("a@b.com", cost=200, intervals=[{"start": "2026-04-09", "end": None}])
    assert accounts.current_monthly_cost(a, today=TODAY) == 200.0


# ── account_score ─────────────────────────────────────────────────────────────

def test_unhealthy_entry_scores_zero():
    assert accounts.account_score(_entry("a@b.com", 90, 90, windows=False))[0] == 0.0
    assert accounts.account_score(_entry("a@b.com", 90, 90, error="HTTP 429"))[0] == 0.0


def test_main_account_gets_bonus_over_identical_secondary():
    main = accounts.account_score(_entry("m", 80, 80, is_main=True, renews=20))[0]
    sec = accounts.account_score(_entry("s", 80, 80, is_main=False, renews=20))[0]
    assert main == sec + accounts.MAIN_BONUS


def test_throttled_5h_flagged_in_reasons():
    _, reasons = accounts.account_score(_entry("a", 5, 90, renews=20))
    assert any("throttled" in r for r in reasons)


def test_soon_renewal_with_high_weekly_boosts_score():
    soon = accounts.account_score(_entry("a", 80, 90, renews=2))[0]
    far = accounts.account_score(_entry("a", 80, 90, renews=25))[0]
    assert soon > far


# ── recommend ─────────────────────────────────────────────────────────────────

def test_main_wins_when_all_have_headroom():
    entries = [
        _entry("main", 85, 85, is_main=True, renews=20),
        _entry("sec1", 85, 85, renews=20),
        _entry("sec2", 85, 85, renews=20),
    ]
    optimal, _ = accounts.recommend(entries)
    assert optimal == "main"


def test_secondary_wins_when_main_throttled():
    entries = [
        _entry("main", 5, 5, is_main=True, renews=20),    # drained
        _entry("sec1", 95, 95, renews=20),                # fresh
    ]
    optimal, _ = accounts.recommend(entries)
    assert optimal == "sec1"


def test_all_unhealthy_falls_back_to_main():
    entries = [
        _entry("main", 0, 0, is_main=True, error="x", windows=False),
        _entry("sec1", 0, 0, error="x", windows=False),
    ]
    optimal, _ = accounts.recommend(entries)
    assert optimal == "main"


class TestChargeBasedActivityAndRecommendation(unittest.TestCase):
    def test_fresh_charge_is_active_even_when_interval_is_closed(self):
        account = _acct(
            "a@b.com",
            intervals=[{"start": "2026-04-09", "end": "2026-06-14"}],
        )
        account["charges"] = [{"date": TODAY.isoformat(), "amount": 213.20}]
        self.assertTrue(accounts.is_active(account, today=TODAY))

    def test_open_interval_is_active_when_receipt_capture_is_stale(self):
        account = _acct(
            "a@b.com", intervals=[{"start": "2026-04-09", "end": None}]
        )
        account["charges"] = [{"date": "2026-05-15", "amount": 213.20}]
        self.assertTrue(accounts.is_active(account, today=TODAY))

    def test_max_20x_uses_current_public_monthly_rate(self):
        account = _acct(
            "a@b.com", cost=213.20,
            intervals=[{"start": "2026-04-09", "end": None}],
        )
        self.assertEqual(accounts.current_monthly_cost(account, today=TODAY), 200.0)

    def test_charge_activity_boundary_is_paid_through_30_days(self):
        account = _acct(
            "a@b.com", intervals=[{"start": "2026-04-09", "end": "2026-05-14"}]
        )
        account["charges"] = [{"date": "2026-05-15", "amount": 213.20}]
        self.assertTrue(accounts.is_active(account, today=dt.date(2026, 6, 14)))
        self.assertFalse(accounts.is_active(account, today=dt.date(2026, 6, 15)))

    def test_empty_or_missing_charges_fall_back_to_intervals(self):
        active = _acct(
            "active", intervals=[{"start": "2026-04-09", "end": None}]
        )
        active["charges"] = []
        inactive = _acct(
            "inactive",
            intervals=[{"start": "2026-01-01", "end": "2026-02-01"}],
        )
        self.assertTrue(accounts.is_active(active, today=TODAY))
        self.assertFalse(accounts.is_active(inactive, today=TODAY))

    def test_recommend_never_picks_inactive_when_active_entry_exists(self):
        entries = [
            _entry("main", 95, 95, is_main=True, active=False),
            _entry("active", 30, 30, active=True),
        ]
        optimal, _ = accounts.recommend(entries)
        self.assertEqual(optimal, "active")

    def test_recommend_falls_back_to_main_when_nothing_is_active(self):
        entries = [
            _entry("main", 5, 5, is_main=True, active=False),
            _entry("secondary", 95, 95, active=False),
        ]
        optimal, _ = accounts.recommend(entries)
        self.assertEqual(optimal, "main")


# ── dashboard_payload ─────────────────────────────────────────────────────────

def test_dashboard_payload_shape_and_totals(monkeypatch):
    accts = [
        {
            "email": "main@x.com", "plan": "max_20x", "billing_day": 9, "is_main": True,
            "monthly_cost": 200,
            "subscription_intervals": [{"start": "2026-04-09", "end": None}],
            "last_usage": {
                "five_hour": {"utilization": 20.0, "resets_at": None},
                "seven_day": {"utilization": 10.0, "resets_at": None},
                "fetched_at": "2026-06-15T00:00:00Z", "error": None,
            },
        },
        {
            "email": "sec@x.com", "plan": "max_20x", "billing_day": 11, "is_main": False,
            "monthly_cost": 200,
            "subscription_intervals": [{"start": "2026-06-11", "end": None}],
            "last_usage": {
                "five_hour": {"utilization": 5.0, "resets_at": None},
                "seven_day": {"utilization": 5.0, "resets_at": None},
                "fetched_at": "2026-06-15T00:00:00Z", "error": None,
            },
        },
    ]
    payload = accounts.dashboard_payload(accts, today=TODAY)
    assert set(payload) == {"accounts", "summary"}
    assert len(payload["accounts"]) == 2
    a0 = payload["accounts"][0]
    for key in ("is_main", "monthly_cost", "lifetime_spend", "score", "is_optimal", "active"):
        assert key in a0
    s = payload["summary"]
    assert s["active_accounts"] == 2
    assert s["total_current_monthly"] == 400.0
    assert s["optimal_email"] in {"main@x.com", "sec@x.com"}
    assert s["total_lifetime"] == round(
        sum(accounts.lifetime_spend(a, TODAY) for a in accts), 2
    )


# ── charges ledger (receipt-accurate lifetime) ────────────────────────────────

def test_lifetime_prefers_charges_ledger_over_proration():
    a = _acct("a@b.com", cost=200, intervals=[{"start": "2026-01-01", "end": None}])
    a["charges"] = [
        {"date": "2025-12-28", "plan": "Claude Pro", "amount": 21.32},
        {"date": "2026-02-01", "plan": "Max 20x", "amount": 213.20},
    ]
    # Sum of actual receipts (234.52), NOT the prorated months estimate.
    assert accounts.lifetime_spend(a, today=TODAY) == 234.52


def test_lifetime_falls_back_to_proration_without_charges():
    a = _acct("a@b.com", cost=213.20, intervals=[{"start": "2026-04-09", "end": None}])
    expected = round(213.20 * accounts.months_active(a["subscription_intervals"], TODAY), 2)
    assert accounts.lifetime_spend(a, today=TODAY) == expected
