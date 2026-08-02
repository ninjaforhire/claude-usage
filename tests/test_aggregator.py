"""Tests for fresh unified account aggregation and routing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import aggregator
from account_registry import load_registry


def _registry(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "id": "claude-test",
                        "email": "andrew@mightyphotobooths.com",
                        "provider": "claude",
                        "config_dir": str(path.parent / "claude"),
                        "keychain_slot": "Claude Code-credentials",
                        "plan": "max_20x",
                        "caps": ["fable"],
                        "active": True,
                        "is_main": True,
                    }
                ]
            }
        )
    )


def test_dry_run_does_not_read_claude_usage_api(tmp_path: Path) -> None:
    registry_path = tmp_path / "accounts_registry.json"
    _registry(registry_path)
    with mock.patch.object(aggregator.accounts, "fetch_all_usage") as fetch:
        snapshot = aggregator.aggregate_accounts(
            registry_path, tmp_path / "cache.json", dry_run=True
        )

    fetch.assert_not_called()
    assert snapshot["accounts"][0]["state"] == "dry-run"


def test_registry_is_static_metadata_not_live_cache(tmp_path: Path) -> None:
    registry_path = tmp_path / "accounts_registry.json"
    _registry(registry_path)

    profile = load_registry(registry_path)[0]

    assert profile.email == "andrew@mightyphotobooths.com"
    assert profile.keychain_slot == "Claude Code-credentials"
    assert not hasattr(profile, "oauth")


def test_fable_routing_rejects_auth_broken_cached_window() -> None:
    snapshot = {
        "accounts": [
            {
                "id": "broken",
                "provider": "claude",
                "email": "andrew@mightyphotobooths.com",
                "active": True,
                "fresh": False,
                "is_main": True,
                "windows": {
                    "five_hour": {"remaining_pct": 80, "resets_at": None},
                    "seven_day": {"remaining_pct": 60, "resets_at": None},
                    "fable": {"remaining_pct": 20, "resets_at": None},
                },
            }
        ]
    }

    decision = aggregator.choose_account(snapshot, "fable")

    assert decision["recommendation"] is None
    assert "no fresh" in decision["reason"]
