"""Static account-profile registry for the unified ``/accounts`` agent.

The registry deliberately contains account metadata only. OAuth credentials and
last-known usage stay in ``~/.claude/usage_accounts.json``; mixing them would
turn a live cache into configuration source of truth.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path.home() / ".claude" / "accounts_registry.json"

CYCLE_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}


@dataclass(frozen=True)
class Billing:
    """What one subscription costs and when it next renews.

    ``subscription_id`` lets several profiles share a single real subscription
    (two local Codex homes signed into one ChatGPT account) so spend totals
    count that subscription once instead of once per profile.
    """

    subscription_id: str
    cycle: str
    day: int | None
    renews_on: str | None
    cost_usd: float | None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Billing":
        """Create a validated billing block from a registry object."""
        cycle = str(raw.get("cycle", "monthly"))
        if cycle not in CYCLE_MONTHS:
            raise ValueError(f"unsupported billing cycle: {cycle}")
        day = raw.get("day")
        if day is not None and not 1 <= int(day) <= 28:
            raise ValueError(f"billing day must be 1-28, got {day}")
        renews_on = raw.get("renews_on")
        if renews_on is not None:
            _dt.date.fromisoformat(str(renews_on))
        if day is None and renews_on is None:
            raise ValueError("billing needs a day-of-month anchor or renews_on date")
        cost = raw.get("cost_usd")
        return cls(
            subscription_id=str(raw.get("subscription_id") or raw.get("id") or ""),
            cycle=cycle,
            day=int(day) if day is not None else None,
            renews_on=str(renews_on) if renews_on is not None else None,
            cost_usd=float(cost) if cost is not None else None,
        )

    def next_renewal(self, today: _dt.date | None = None) -> _dt.date:
        """Return the next renewal date at or after ``today``.

        ``renews_on`` is an explicit anchor that is rolled forward by whole
        cycles when it has already passed; otherwise the day-of-month anchor
        drives a monthly-style cycle.
        """
        today = today or _dt.date.today()
        months = CYCLE_MONTHS[self.cycle]
        if self.renews_on:
            date = _dt.date.fromisoformat(self.renews_on)
            while date < today:
                date = _add_months(date, months)
            return date
        date = _clamp_day(today.year, today.month, self.day or 1)
        if date < today:
            date = _add_months(date, months)
        return date


def _add_months(date: _dt.date, months: int) -> _dt.date:
    """Shift a date forward by whole months, clamping to the month's length."""
    index = (date.year * 12 + date.month - 1) + months
    return _clamp_day(index // 12, index % 12 + 1, date.day)


def _clamp_day(year: int, month: int, day: int) -> _dt.date:
    """Build a date, clamping the day to the last valid day of the month."""
    if month == 12:
        last = 31
    else:
        last = (_dt.date(year, month + 1, 1) - _dt.timedelta(days=1)).day
    return _dt.date(year, month, min(day, last))


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
    billing: Billing | None = None
    billing_cost_usd: float | None = None
    """Price a subscription whose *date* is discovered rather than configured.

    A Codex home reads its renewal date straight from the ChatGPT token, so a
    full ``billing`` block would only duplicate — and eventually contradict —
    what the token already states. This carries the one fact the token omits.
    """

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
            billing=(Billing.from_dict(raw["billing"]) if raw.get("billing") else None),
            billing_cost_usd=(
                float(raw["billing_cost_usd"])
                if raw.get("billing_cost_usd") is not None
                else None
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-safe static configuration without runtime state."""
        value = asdict(self)
        value["config_dir"] = str(self.config_dir)
        value["caps"] = list(self.caps)
        if self.billing is None:
            value.pop("billing", None)
        if self.billing_cost_usd is None:
            value.pop("billing_cost_usd", None)
        return value


DEFAULT_PROFILES: tuple[AccountProfile, ...] = (
    AccountProfile(
        id="claude-andrew",
        email="andrew@mightyphotobooths.com",
        provider="claude",
        config_dir=Path.home() / ".claude",
        keychain_slot=None,
        plan="max_20x",
        caps=("fable", "mythos", "opus", "sonnet", "haiku"),
        active=True,
        is_main=True,
        billing=Billing(
            subscription_id="claude-max-andrew",
            cycle="monthly",
            day=9,
            renews_on=None,
            cost_usd=213.20,
        ),
    ),
    AccountProfile(
        id="claude-awebber2k",
        email="awebber2k@gmail.com",
        provider="claude",
        config_dir=Path.home() / ".claude",
        keychain_slot=None,
        plan="max_20x",
        caps=("fable", "mythos", "opus", "sonnet", "haiku"),
        active=True,
        is_main=False,
        billing=Billing(
            subscription_id="claude-max-awebber2k",
            cycle="monthly",
            day=10,
            renews_on=None,
            cost_usd=213.20,
        ),
    ),
    AccountProfile(
        id="claude-hotfixops",
        email="andrew@hotfixops.com",
        provider="claude",
        config_dir=Path.home() / ".claude",
        keychain_slot=None,
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


@dataclass(frozen=True)
class StandaloneSubscription:
    """A bill with no local CLI profile attached.

    Not every paid subscription shows up as a Codex home or Claude login — a
    second ChatGPT account used only in the browser still costs money every
    month. Recording it here keeps the spend picture complete instead of
    counting only what happens to have a config directory on this machine.
    """

    subscription_id: str
    label: str
    provider: str
    plan: str
    billing: Billing

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StandaloneSubscription":
        """Create a validated standalone subscription from a registry object."""
        for field in ("subscription_id", "label", "provider"):
            if not raw.get(field):
                raise ValueError(f"standalone subscription missing: {field}")
        return cls(
            subscription_id=str(raw["subscription_id"]),
            label=str(raw["label"]),
            provider=str(raw["provider"]),
            plan=str(raw.get("plan", "")),
            billing=Billing.from_dict(
                {**raw, "subscription_id": raw["subscription_id"]}
            ),
        )


def load_standalone_subscriptions(
    path: Path = REGISTRY_PATH,
) -> list[StandaloneSubscription]:
    """Load bills recorded without an associated local profile."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return []
    entries = raw.get("subscriptions") or []
    if not isinstance(entries, list):
        raise ValueError("registry 'subscriptions' must be a list")
    return [StandaloneSubscription.from_dict(entry) for entry in entries]


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
