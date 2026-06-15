"""Tests for the Anthropic receipt auto-ingest parser + idempotent ledger upsert."""

import receipts


# Real flattened receipt text (from andrew@mightyphotobooths.com, Jun 9 2026).
RECEIPT_BODY = (
    "Anthropic, PBC Receipt from Anthropic, PBC $213.20 Paid June 9, 2026 "
    "Max plan - 20x Qty 1 $200.00 Subtotal $200.00 "
    "Tax - Texas (8.25%) $13.20 Total $213.20 Amount paid $213.20 Questions?"
)
KNOWN = ["andrew@mightyphotobooths.com", "andrew@hotfixops.com", "awebber2k@gmail.com"]
OWNER = "andrew@mightyphotobooths.com"


# ── parse_amount ──────────────────────────────────────────────────────────────

def test_amount_is_total_paid_not_subtotal():
    assert receipts.parse_amount(RECEIPT_BODY) == 213.20


def test_amount_prefers_amount_paid_over_total():
    assert receipts.parse_amount("Total $200.00 Amount paid $213.20") == 213.20


def test_amount_handles_thousands_separator():
    assert receipts.parse_amount("Amount paid $1,213.20") == 1213.20


def test_amount_none_when_absent():
    assert receipts.parse_amount("no money here") is None


# ── parse_plan / parse_email_date / classify ──────────────────────────────────

def test_plan_extracts_max_20x():
    assert receipts.parse_plan(RECEIPT_BODY) == "Max plan - 20x"


def test_plan_extracts_pro():
    assert receipts.parse_plan("Thanks for subscribing to Claude Pro") == "Claude Pro"


def test_email_date_to_utc_iso():
    assert receipts.parse_email_date("Tue, 9 Jun 2026 17:39:51 +0000") == "2026-06-09"


def test_email_date_late_night_central_rolls_to_utc_day():
    # Jun 11 23:15 Central == Jun 12 04:15 UTC -> charge date is the UTC day.
    assert receipts.parse_email_date("Fri, 12 Jun 2026 04:15:00 +0000") == "2026-06-12"


def test_classify_kinds():
    assert receipts.classify("Your receipt from Anthropic, PBC #2780") == "charge"
    assert receipts.classify("Welcome to the Max plan") == "start"
    assert receipts.classify("Your Claude Max subscription was canceled") == "cancel"
    assert receipts.classify("Claude Fable 5 is here") == "other"


# ── route_account ─────────────────────────────────────────────────────────────

def test_routes_direct_receipt_to_owner():
    assert receipts.route_account({"To": OWNER}, "", KNOWN, OWNER) == OWNER


def test_routes_forwarded_receipt_by_original_recipient():
    headers = {"To": OWNER, "X-Forwarded-To": "awebber2k@gmail.com"}
    assert receipts.route_account(headers, "", KNOWN, OWNER) == "awebber2k@gmail.com"


def test_routes_unknown_to_owner():
    assert receipts.route_account({"To": "stranger@x.com"}, "", KNOWN, OWNER) == OWNER


# ── parse_receipt ─────────────────────────────────────────────────────────────

def test_parse_receipt_charge_event():
    headers = {"Subject": "Your receipt from Anthropic, PBC #2780",
               "Date": "Tue, 9 Jun 2026 17:39:51 +0000", "To": OWNER}
    e = receipts.parse_receipt(headers, RECEIPT_BODY, KNOWN, OWNER)
    assert e == {"account": OWNER, "date": "2026-06-09", "kind": "charge",
                 "plan": "Max plan - 20x", "amount": 213.20}


def test_parse_receipt_ignores_marketing():
    headers = {"Subject": "Claude Fable 5 is here", "Date": "Tue, 9 Jun 2026 23:01:53 +0000"}
    assert receipts.parse_receipt(headers, "new model", KNOWN, OWNER) is None


# ── apply_event (idempotent ledger) ───────────────────────────────────────────

def test_apply_charge_appends_once_and_dedups():
    acct = {"email": OWNER, "charges": []}
    e = {"account": OWNER, "date": "2026-06-09", "kind": "charge",
         "plan": "Max plan - 20x", "amount": 213.20}
    assert receipts.apply_event(acct, e) is True
    assert len(acct["charges"]) == 1
    # Re-applying the same charge is a no-op (idempotent).
    assert receipts.apply_event(acct, e) is False
    assert len(acct["charges"]) == 1


def test_apply_skips_api_paygo_charge_without_plan():
    # API pay-as-you-go receipt: a charge with no recognized subscription plan.
    acct = {"email": OWNER, "charges": []}
    e = {"account": OWNER, "date": "2026-01-28", "kind": "charge", "plan": "", "amount": 21.32}
    assert receipts.apply_event(acct, e) is False
    assert acct["charges"] == []


def test_apply_charge_dedups_against_hand_seeded_ledger():
    acct = {"email": OWNER, "charges": [{"date": "2026-06-09", "plan": "Max 20x", "amount": 213.20}]}
    e = {"account": OWNER, "date": "2026-06-09", "kind": "charge",
         "plan": "Max plan - 20x", "amount": 213.20}
    assert receipts.apply_event(acct, e) is False


def test_apply_start_opens_interval_when_uncovered():
    acct = {"email": OWNER, "subscription_intervals": []}
    e = {"account": OWNER, "date": "2026-06-11", "kind": "start", "plan": "Max 20x"}
    assert receipts.apply_event(acct, e) is True
    assert acct["subscription_intervals"] == [{"start": "2026-06-11", "end": None}]
    # Already covered -> no duplicate interval.
    assert receipts.apply_event(acct, e) is False


def test_apply_cancel_closes_open_interval():
    acct = {"email": OWNER, "subscription_intervals": [{"start": "2025-12-28", "end": None}]}
    e = {"account": OWNER, "date": "2026-04-21", "kind": "cancel", "plan": ""}
    assert receipts.apply_event(acct, e) is True
    assert acct["subscription_intervals"][0]["end"] == "2026-04-21"
