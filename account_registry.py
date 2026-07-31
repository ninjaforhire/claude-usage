"""Static account-profile registry for the unified ``/accounts`` agent.

The registry deliberately contains account metadata only. OAuth credentials and
last-known usage stay in ``~/.claude/usage_accounts.json``; mixing them would
turn a live cache into configuration source of truth.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path.home() / ".claude" / "accounts_registry.json"


@dataclass(frozen=True)
class AccountProfile:
    """One configured Claude or Codex account/profile."""

    id: str
    email: str | None
    provider: str
    config_dir: Path
    keychain_slot: str | None
    plan: str
    caps: tuple[str, ...]
    active: bool
    is_main: bool

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AccountProfile":
        """Create a validated profile from a registry object."""
        required = {
            "id",
            "provider",
            "config_dir",
            "keychain_slot",
            "plan",
            "caps",
            "active",
            "is_main",
        }
        missing = required - raw.keys()
        if missing:
            raise ValueError(f"account registry entry missing: {sorted(missing)}")
        provider = str(raw["provider"])
        if provider not in {"claude", "codex"}:
            raise ValueError(f"unsupported account provider: {provider}")
        email = raw.get("email")
        if provider == "claude" and not isinstance(email, str):
            raise ValueError("Claude account profiles require an email")
        return cls(
            id=str(raw["id"]),
            email=str(email) if email is not None else None,
            provider=provider,
            config_dir=Path(str(raw["config_dir"])).expanduser(),
            keychain_slot=(
                str(raw["keychain_slot"]) if raw["keychain_slot"] is not None else None
            ),
            plan=str(raw["plan"]),
            caps=tuple(str(cap) for cap in raw["caps"]),
            active=bool(raw["active"]),
            is_main=bool(raw["is_main"]),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-safe static configuration without runtime state."""
        value = asdict(self)
        value["config_dir"] = str(self.config_dir)
        value["caps"] = list(self.caps)
        return value


DEFAULT_PROFILES: tuple[AccountProfile, ...] = (
    AccountProfile(
        id="claude-andrew",
        email="andrew@mightyphotobooths.com",
        provider="claude",
        config_dir=Path.home() / ".claude",
        keychain_slot="Claude Code-credentials",
        plan="max_20x",
        caps=("fable", "mythos", "opus", "sonnet", "haiku"),
        active=True,
        is_main=True,
    ),
    AccountProfile(
        id="claude-awebber2k",
        email="awebber2k@gmail.com",
        provider="claude",
        config_dir=Path.home() / ".claude",
        keychain_slot="Claude Code-credentials",
        plan="max_20x",
        caps=("fable", "mythos", "opus", "sonnet", "haiku"),
        active=True,
        is_main=False,
    ),
    AccountProfile(
        id="claude-hotfixops",
        email="andrew@hotfixops.com",
        provider="claude",
        config_dir=Path.home() / ".claude",
        keychain_slot="Claude Code-credentials",
        plan="max_20x",
        caps=("fable", "mythos", "opus", "sonnet", "haiku"),
        active=False,
        is_main=False,
    ),
    AccountProfile(
        id="codex",
        email=None,
        provider="codex",
        config_dir=Path.home() / ".codex",
        keychain_slot=None,
        plan="pro",
        caps=("terra", "sol", "luna"),
        active=True,
        is_main=True,
    ),
    AccountProfile(
        id="codex-jimbo",
        email=None,
        provider="codex",
        config_dir=Path.home() / ".codex-jimbo",
        keychain_slot=None,
        plan="pro",
        caps=("terra", "sol", "luna"),
        active=True,
        is_main=False,
    ),
)


def load_registry(path: Path = REGISTRY_PATH) -> list[AccountProfile]:
    """Load static profile configuration, falling back to documented defaults.

    The fallback keeps the decision tool usable before the authoritative config
    file is installed; it never reads or writes the credential/live-cache store.
    """
    if not path.exists():
        return list(DEFAULT_PROFILES)
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("accounts") if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError("accounts registry must contain an accounts list")
    profiles = [AccountProfile.from_dict(entry) for entry in entries]
    ids = [profile.id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("accounts registry contains duplicate ids")
    return profiles


def registry_document() -> dict[str, Any]:
    """Return the canonical static registry payload for manual installation."""
    return {"version": 1, "accounts": [item.as_dict() for item in DEFAULT_PROFILES]}
