"""Shared fixtures: keep tests off this machine's live Claude Code credentials."""

from __future__ import annotations

import accounts
import pytest

_REAL_LIVE_SESSION_CREDENTIALS = accounts.live_session_credentials


@pytest.fixture(autouse=True)
def _no_session_config_dir(monkeypatch):
    """Orca shells export CLAUDE_CONFIG_DIR; unset it so keychain_oauth stays hermetic."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


@pytest.fixture(autouse=True)
def _no_live_session_scan(monkeypatch):
    """Stale-copy fallbacks must never scan the real keychain or call the API."""
    monkeypatch.setattr(accounts, "live_session_credentials", lambda: {})


@pytest.fixture
def real_live_session_scan(monkeypatch):
    """Restore the real scanner for tests that patch its sources themselves."""
    monkeypatch.setattr(
        accounts, "live_session_credentials", _REAL_LIVE_SESSION_CREDENTIALS
    )
