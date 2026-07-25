"""Tests for private, local-only account-profile snapshots."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from account_profiles import (
    add_profile,
    fable_headroom,
    load_registry,
    profile_provider_cards,
    rank_codex_profiles,
    rank_fable_profiles,
    record_snapshot,
    reset_testing_registry,
    save_registry,
    seed_testing_registry,
)


class TestAccountProfiles(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.store = Path(self.tmpdir.name) / "testing" / "accounts.json"
        self.registry = load_registry(self.store)
        add_profile(
            self.registry,
            "max-one",
            "Max account one",
            {
                "claude": {
                    "expected_email": "one@example.com",
                    "plan_label": "Max 20x",
                },
                "codex": {
                    "expected_email": "one@example.com",
                    "plan_label": "Max 20x",
                },
            },
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_registry_is_owner_only_and_never_stores_unknown_fields(self) -> None:
        record_snapshot(
            self.registry,
            "max-one",
            "codex",
            {
                "available": True,
                "source": "codex-app-server",
                "account": {
                    "email": "one@example.com",
                    "plan": "max",
                    "accessToken": "must-not-leak",
                },
                "windows": {
                    "seven_day": {
                        "used_percent": 20,
                        "remaining_percent": 80,
                    }
                },
                "reset_credits": {
                    "available_count": 2,
                    "credit_id": "must-not-leak",
                },
            },
        )
        save_registry(self.registry, self.store)
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o600)
        stored = self.store.read_text(encoding="utf-8")
        self.assertNotIn("accessToken", stored)
        self.assertNotIn("must-not-leak", stored)

    def test_snapshot_rejects_mismatched_active_email(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            record_snapshot(
                self.registry,
                "max-one",
                "claude",
                {
                    "account": {"email": "other@example.com"},
                    "windows": {},
                },
            )

    def test_fable_headroom_is_conservative_shared_weekly_capacity(self) -> None:
        headroom = fable_headroom({"windows": {"seven_day": {"remaining_percent": 82}}})
        self.assertEqual(headroom["guaranteed_percent"], 32)
        self.assertEqual(headroom["weekly_remaining_percent"], 82)

    def test_fable_ranking_prefers_expiring_running_window(self) -> None:
        add_profile(
            self.registry,
            "max-two",
            "Max account two",
            {"claude": {"plan_label": "Max 20x"}},
        )
        record_snapshot(
            self.registry,
            "max-one",
            "claude",
            {
                "available": True,
                "account": {"email": "one@example.com", "plan": "max"},
                "windows": {
                    "seven_day": {
                        "used_percent": 20,
                        "remaining_percent": 80,
                        "resets_at": "2026-07-25T00:00:00+00:00",
                    }
                },
            },
        )
        record_snapshot(
            self.registry,
            "max-two",
            "claude",
            {
                "available": True,
                "account": {"plan": "max"},
                "windows": {
                    "seven_day": {
                        "used_percent": 10,
                        "remaining_percent": 90,
                        "resets_at": "2026-07-30T00:00:00+00:00",
                    }
                },
            },
        )
        ranked = rank_fable_profiles(self.registry)
        self.assertEqual([row["id"] for row in ranked[:2]], ["max-one", "max-two"])

    def test_codex_ranking_tracks_reset_credits_without_consuming_them(self) -> None:
        add_profile(
            self.registry,
            "max-two",
            "Max account two",
            {"codex": {"plan_label": "Max 20x"}},
        )
        record_snapshot(
            self.registry,
            "max-one",
            "codex",
            {
                "available": True,
                "account": {"email": "one@example.com", "plan": "max"},
                "windows": {
                    "five_hour": {"used_percent": 96, "remaining_percent": 4},
                    "seven_day": {"used_percent": 90, "remaining_percent": 10},
                },
                "reset_credits": {"available_count": 2},
            },
        )
        record_snapshot(
            self.registry,
            "max-two",
            "codex",
            {
                "available": True,
                "account": {"plan": "max"},
                "windows": {
                    "five_hour": {"used_percent": 20, "remaining_percent": 80},
                    "seven_day": {"used_percent": 60, "remaining_percent": 40},
                },
            },
        )
        ranked = rank_codex_profiles(self.registry)
        self.assertEqual(ranked[0]["id"], "max-two")
        self.assertEqual(ranked[1]["reset_credits"]["available_count"], 2)

    def test_cards_contain_snapshots_not_history_or_credentials(self) -> None:
        cards = profile_provider_cards(self.registry)
        self.assertEqual(len(cards), 2)
        self.assertTrue(all("history" not in card for card in cards))
        self.assertNotIn("accessToken", json.dumps(cards))

    def test_reset_testing_registry_does_not_touch_standard_profiles(self) -> None:
        standard_store = Path(self.tmpdir.name) / "accounts.json"
        testing_store = Path(self.tmpdir.name) / "testing" / "accounts.json"
        standard = load_registry(standard_store)
        testing = load_registry(testing_store, mode="testing")
        add_profile(
            standard,
            "standard-account",
            "Standard account",
            {"claude": {"plan_label": "Max 20x"}},
        )
        add_profile(
            testing,
            "testing-account",
            "Testing account",
            {"codex": {"plan_label": "Max 20x"}},
        )
        save_registry(standard, standard_store)
        save_registry(testing, testing_store, mode="testing")

        reset_testing_registry(testing_store)

        self.assertEqual(len(load_registry(standard_store)["profiles"]), 1)
        reset = load_registry(testing_store, mode="testing")
        self.assertEqual(reset["mode"], "testing")
        self.assertEqual(reset["profiles"], [])

    def test_seed_testing_registry_contains_only_fake_safe_account_cards(self) -> None:
        testing_store = Path(self.tmpdir.name) / "testing" / "accounts.json"
        seed_testing_registry(testing_store)

        seeded = load_registry(testing_store, mode="testing")
        self.assertEqual(seeded["mode"], "testing")
        self.assertEqual(
            [profile["id"] for profile in seeded["profiles"]],
            ["studio-max", "personal-max", "standby-max"],
        )
        self.assertTrue(seeded["profiles"][-1]["inactive"])
        cards = profile_provider_cards(seeded)
        self.assertEqual(len(cards), 5)
        self.assertNotIn("@", json.dumps(cards))


if __name__ == "__main__":
    unittest.main()
