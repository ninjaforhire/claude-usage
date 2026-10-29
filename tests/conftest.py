"""Shared fixtures: keep tests off this machine's live Claude Code credentials."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_session_config_dir(monkeypatch):
    """Orca shells export CLAUDE_CONFIG_DIR; unset it so keychain_oauth stays hermetic."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
