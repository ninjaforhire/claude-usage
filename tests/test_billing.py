"""Tests for subscription renewal tracking across the registry and aggregator."""

from __future__ import annotations

import base64
import datetime as _dt
import json
from pathlib import Path

import aggregator
import codex_limits
from account_registry import AccountProfile, Billing, StandaloneSubscription

# ── Billing.next_renewal ──────────────────────────────────────────────────────


def test_day_anchor_returns_this_month_when_still_upcoming() -> None:
    billing = Billing("s", "monthly", 20, None, 10.0)
    assert billing.next_renewal(_dt.date(2026, 8, 5)) == _dt.date(2026, 8, 20)


def test_day_anchor_rolls_to_next_month_once_the_day_has_passed() -> None:
    billing = Billing("s", "monthly", 3, None, 10.0)
    assert billing.next_renewal(_dt.date(2026, 8, 5)) == _dt.date(2026, 9, 3)


def test_day_anchor_on_today_counts_as_due_today() -> None:
    billing = Billing("s", "monthly", 9, None, 10.0)
    assert billing.next_renewal(_dt.date(2026, 8, 9)) == _dt.date(2026, 8, 9)


def test_annual_cycle_rolls_a_past_anchor_forward_a_whole_year() -> None:
    billing = Billing("s", "annual", None, "2026-03-01", 120.0)
    assert billing.next_renewal(_dt.date(2026, 8, 5)) == _dt.date(2027, 3, 1)


def test_explicit_renews_on_in_the_future_is_used_as_is() -> None:
    billing = Billing("s", "monthly", None, "2026-08-14", None)
    assert billing.next_renewal(_dt.date(2026, 8, 5)) == _dt.date(2026, 8, 14)


def test_rollforward_clamps_into_short_months() -> None:
    billing = Billing("s", "monthly", None, "2026-01-31", None)
    assert billing.next_renewal(_dt.date(2026, 2, 15)) == _dt.date(2026, 2, 28)


def test_rejects_a_billing_block_with_no_date_anchor() -> None:
    try:
        Billing.from_dict({"subscription_id": "s", "cycle": "monthly"})
    except ValueError as error:
        assert "anchor" in str(error)
    else:
        raise AssertionError("expected a ValueError for an anchorless billing block")


def test_rejects_an_unsupported_cycle() -> None:
    try:
        Billing.from_dict({"subscription_id": "s", "cycle": "weekly", "day": 1})
    except ValueError as error:
        assert "cycle" in str(error)
    else:
        raise AssertionError("expected a ValueError for an unsupported cycle")


# ── Codex subscription claims ─────────────────────────────────────────────────


def _write_auth(home: Path, claims: dict) -> None:
    """Write a Codex auth.json whose id_token carries *claims*."""
    home.mkdir(parents=True, exist_ok=True)
    payload = base64.urlsafe_b64encode(
        json.dumps({"https://api.openai.com/auth": claims}).encode()
    ).decode()
    (home / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": f"h.{payload}.s"}})
    )


def test_reads_subscription_period_without_returning_any_token(tmp_path: Path) -> None:
    _write_auth(
        tmp_path,
        {
            "chatgpt_account_id": "a85e0195-9821",
            "chatgpt_plan_type": "pro",
            "chatgpt_subscription_active_until": "2026-08-14T19:12:57+00:00",
        },
    )
    claims = codex_limits.subscription_claims(tmp_path)
    assert claims["chatgpt_plan_type"] == "pro"
    assert claims["chatgpt_subscription_active_until"].startswith("2026-08-14")
    assert not any("token" in key for key in claims)


def test_returns_none_when_the_home_has_no_auth_file(tmp_path: Path) -> None:
    assert codex_limits.subscription_claims(tmp_path) is None


def test_returns_none_for_api_key_mode_without_an_id_token(tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": "sk-x"}))
    assert codex_limits.subscription_claims(tmp_path) is None


def test_returns_none_for_a_malformed_token(tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text(
        json.dumps({"tokens": {"id_token": "not.a.jwt"}})
    )
    assert codex_limits.subscription_claims(tmp_path) is None


# ── subscriptions() rollup ────────────────────────────────────────────────────


def _entry(profile_id: str, billing: dict, active: bool = True) -> dict:
    return {
        "id": profile_id,
        "provider": "codex",
        "plan": "pro",
        "active": active,
        "billing": billing,
    }


def _billing(sub_id: str, renewal: str, cost: float | None) -> dict:
    return {
        "known": True,
        "subscription_id": sub_id,
        "cycle": "monthly",
        "cost_usd": cost,
        "next_renewal": renewal,
        "days_until": 9,
        "source": "chatgpt-token",
    }


def test_profiles_sharing_a_subscription_id_are_billed_once() -> None:
    entries = [
        _entry("codex", _billing("chatgpt-pro-a85e", "2026-08-14", 200.0)),
        _entry("codex-jimbo", _billing("chatgpt-pro-a85e", "2026-08-14", 200.0)),
    ]
    rollup = aggregator.subscriptions(entries)
    assert len(rollup["billed"]) == 1
    assert rollup["billed"][0]["profiles"] == ["codex", "codex-jimbo"]
    assert rollup["monthly_total_usd"] == 200.0


def test_disabled_profiles_are_excluded_from_the_rollup() -> None:
    entries = [
        _entry("live", _billing("a", "2026-08-14", 100.0)),
        _entry("off", _billing("b", "2026-08-20", 999.0), active=False),
    ]
    rollup = aggregator.subscriptions(entries)
    assert [item["subscription_id"] for item in rollup["billed"]] == ["a"]
    assert rollup["monthly_total_usd"] == 100.0


def test_unpriced_subscriptions_are_reported_and_left_out_of_the_total() -> None:
    entries = [
        _entry("codex", _billing("chatgpt-pro", "2026-08-14", None)),
        _entry("claude", _billing("claude-max", "2026-08-09", 213.20)),
    ]
    rollup = aggregator.subscriptions(entries)
    assert rollup["unpriced_subscriptions"] == ["chatgpt-pro"]
    assert rollup["monthly_total_usd"] == 213.20


def test_annual_subscriptions_are_amortized_into_the_monthly_total() -> None:
    billing = _billing("annual-thing", "2026-12-01", 120.0)
    billing["cycle"] = "annual"
    rollup = aggregator.subscriptions([_entry("x", billing)])
    assert rollup["monthly_total_usd"] == 10.0


def test_profiles_without_billing_are_listed_as_unset() -> None:
    entries = [_entry("codex", {"known": False, "subscription_id": None})]
    rollup = aggregator.subscriptions(entries)
    assert rollup["unset_profiles"] == ["codex"]
    assert rollup["billed"] == []


def test_codex_profile_pairs_a_token_derived_date_with_a_configured_cost(
    tmp_path: Path,
) -> None:
    """The token supplies the date; the registry supplies only the price."""
    _write_auth(
        tmp_path,
        {
            "chatgpt_account_id": "a85e0195-9821",
            "chatgpt_plan_type": "pro",
            "chatgpt_subscription_active_until": "2026-08-14T19:12:57+00:00",
        },
    )
    profile = AccountProfile.from_dict(
        {
            "id": "codex",
            "email": None,
            "provider": "codex",
            "config_dir": str(tmp_path),
            "keychain_slot": None,
            "plan": "pro_20x",
            "caps": ["terra"],
            "active": True,
            "is_main": True,
            "billing_cost_usd": 212.80,
        }
    )
    state = aggregator._billing_state(profile, today=_dt.date(2026, 8, 6))
    assert state["source"] == "chatgpt-token"
    assert state["next_renewal"] == "2026-08-14"
    assert state["cost_usd"] == 212.80


def test_codex_profile_without_a_configured_cost_stays_unpriced(tmp_path: Path) -> None:
    _write_auth(
        tmp_path,
        {
            "chatgpt_account_id": "a85e0195-9821",
            "chatgpt_subscription_active_until": "2026-08-14T19:12:57+00:00",
        },
    )
    profile = AccountProfile.from_dict(
        {
            "id": "codex",
            "email": None,
            "provider": "codex",
            "config_dir": str(tmp_path),
            "keychain_slot": None,
            "plan": "pro_20x",
            "caps": ["terra"],
            "active": True,
            "is_main": True,
        }
    )
    assert aggregator._billing_state(profile)["cost_usd"] is None


def test_standalone_subscriptions_are_billed_even_with_no_local_profile() -> None:
    standalone = [
        StandaloneSubscription.from_dict(
            {
                "subscription_id": "chatgpt-pro20x-awebber2k",
                "label": "ChatGPT Pro 20x (awebber2k@gmail.com)",
                "provider": "chatgpt",
                "plan": "pro_20x",
                "cycle": "monthly",
                "day": 23,
                "cost_usd": 212.80,
            }
        )
    ]
    rollup = aggregator.subscriptions([], standalone, today=_dt.date(2026, 8, 5))
    assert rollup["billed"][0]["profiles"] == []
    assert rollup["billed"][0]["next_renewal"] == "2026-08-23"
    assert rollup["monthly_total_usd"] == 212.80


def test_a_profile_backed_subscription_wins_over_a_standalone_duplicate() -> None:
    standalone = [
        StandaloneSubscription.from_dict(
            {
                "subscription_id": "shared",
                "label": "dupe",
                "provider": "chatgpt",
                "cycle": "monthly",
                "day": 23,
                "cost_usd": 999.0,
            }
        )
    ]
    entries = [_entry("codex", _billing("shared", "2026-08-14", 212.80))]
    rollup = aggregator.subscriptions(entries, standalone, today=_dt.date(2026, 8, 5))
    assert len(rollup["billed"]) == 1
    assert rollup["billed"][0]["cost_usd"] == 212.80


def test_standalone_subscription_requires_a_label() -> None:
    try:
        StandaloneSubscription.from_dict(
            {"subscription_id": "x", "provider": "chatgpt", "day": 1}
        )
    except ValueError as error:
        assert "label" in str(error)
    else:
        raise AssertionError("expected a ValueError for a missing label")


def test_subscriptions_are_ordered_by_soonest_renewal() -> None:
    entries = [
        _entry("later", _billing("b", "2026-08-20", 1.0)),
        _entry("sooner", _billing("a", "2026-08-09", 1.0)),
    ]
    rollup = aggregator.subscriptions(entries)
    assert [item["subscription_id"] for item in rollup["billed"]] == ["a", "b"]
