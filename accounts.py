"""Multi-account OAuth credential store + usage fetch for the dashboard."""

from __future__ import annotations

import calendar
import contextlib
import datetime as _dt
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl  # POSIX-only; the accounts/orbs feature is macOS-only anyway
except ImportError:  # pragma: no cover — Windows scanner imports accounts.py too
    fcntl = None

STORE_PATH = Path.home() / ".claude" / "usage_accounts.json"
LOCK_PATH = Path.home() / ".claude" / "usage_accounts.lock"
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"

TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"  # Claude Code's public OAuth client identifier — not a secret
SKEW = timedelta(seconds=60)
RATE_LIMIT_BACKOFF = 900
PERMISSION_BACKOFF = 6 * 3600

COLOR_RAMP = [  # (min_remaining_pct, fill_hi, fill_lo)
    (60, "#39ff6e", "#0fae3e"),
    (35, "#ffe23d", "#c69b08"),
    (15, "#ff9433", "#c45b06"),
    (0, "#ff3b3b", "#b30f0f"),
]


# ── Cross-process store lock ──────────────────────────────────────────────────


@contextlib.contextmanager
def store_lock(path: Path | None = None):
    """Exclusive cross-process lock around a store read-modify-write cycle.

    OAuth refresh tokens are single-use: the launchd token-refresh job and a
    manual ``accounts add``/``refresh`` run in SEPARATE processes, so the
    in-process ``threading.Lock`` cannot serialize them. Without a file lock both
    can rotate the same refresh token and persist a stale one, 400/429-ing the
    account — the failure that grayed the mighty + awebber2k orbs on 2026-06-29.
    ``flock`` makes the full refresh-fetch-save cycle atomic across processes;
    the threading lock still guards intra-process concurrency.

    ``path`` defaults to ``LOCK_PATH`` resolved at call time (not bound at import)
    so tests can redirect it via ``monkeypatch.setattr(accounts, "LOCK_PATH", …)``.
    No-op where ``fcntl`` is unavailable (Windows); the orbs feature is
    macOS-only, so the lock is only ever exercised there. Not reentrant — never
    nest ``store_lock`` within itself (would self-deadlock).
    """
    if fcntl is None:
        yield
        return
    p = Path(path) if path is not None else LOCK_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ── Store layer ───────────────────────────────────────────────────────────────


def load_store(path: Path = STORE_PATH) -> dict:
    """Load the account store from disk, returning empty store if missing."""
    if not Path(path).exists():
        return {"accounts": []}
    with open(path) as f:
        return json.load(f)


def save_store(store: dict, path: Path = STORE_PATH) -> None:
    """Atomically write the account store with mode 600."""
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(store, f, indent=2)
    os.chmod(tmp, 0o600)  # O_CREAT mode is ignored if a stale tmp file pre-exists
    tmp.replace(path)


def upsert_account(acct: dict, path: Path = STORE_PATH) -> None:
    """Insert or replace account record by email."""
    with store_lock():
        store = load_store(path=path)
        store["accounts"] = [
            a for a in store["accounts"] if a["email"] != acct["email"]
        ] + [acct]
        save_store(store, path=path)


def update_oauth(
    email: str, oauth: dict, usage: dict | None = None, path: Path = STORE_PATH
) -> bool:
    """Refresh only the OAuth tokens (and optionally usage) of a tracked account.

    Re-capturing a logged-in account's live keychain credentials must NOT clobber
    the record's billing history (charges, subscription_intervals, billing_day,
    is_main, monthly_cost). Update the credential fields in place and leave
    everything else untouched. Returns False if no account matches ``email``.
    """
    with store_lock():
        store = load_store(path=path)
        for acct in store["accounts"]:
            if acct["email"] == email:
                acct["oauth"] = oauth
                if usage is not None:
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    acct["last_usage"] = {
                        **usage,
                        "fetched_at": now,
                        "last_success_at": now,
                        "error": None,
                    }
                save_store(store, path=path)
                return True
        return False


def set_keychain_owner(email: str, path: Path = STORE_PATH) -> None:
    """Mark which account currently owns the Claude Code keychain credentials."""
    with store_lock():
        store = load_store(path=path)
        store["keychain_owner"] = email
        save_store(store, path=path)


KEYCHAIN_SERVICE = "Claude Code-credentials"
_KEYCHAIN_FIND_COMMAND = ["security", "find-generic-password"]
_KEYCHAIN_DUMP_COMMAND = ["security", "dump-keychain"]
_KEYCHAIN_SLOT_PATTERN = re.compile(r'"(Claude Code-credentials(?:-[0-9a-f]{8})?)"')
_RESOLVED_KEYCHAIN_SLOT: str | None = None


class NoUsableCredentials(RuntimeError):
    """Raised when no supported Claude Code credential store is usable."""

    def __init__(
        self,
        sources: dict[str, str],
        *,
        scanned: int = 0,
        empty_rejected: int = 0,
    ) -> None:
        self.sources = sources
        self.scanned = scanned
        self.empty_rejected = empty_rejected
        detail = "; ".join(f"{source}: {reason}" for source, reason in sources.items())
        super().__init__(f"No usable Claude Code credentials ({detail}).")

    @classmethod
    def keychain(cls, scanned: int, empty_rejected: int) -> "NoUsableCredentials":
        """Create a diagnostic failure for the keychain fallback alone."""
        return cls(
            {"keychain": f"scanned={scanned}, empty_rejected={empty_rejected}"},
            scanned=scanned,
            empty_rejected=empty_rejected,
        )


# Public compatibility name retained for callers that imported the round-1 type.
NoKeychainCredentials = NoUsableCredentials


def _normalise_oauth(credentials: Any) -> tuple[dict[str, str] | None, str]:
    """Validate Claude Code's persisted OAuth object without exposing values."""
    if not isinstance(credentials, dict):
        return None, "expected structure absent"
    access_token = credentials.get("accessToken")
    refresh_token = credentials.get("refreshToken")
    expires_at = credentials.get("expiresAt")
    if not isinstance(access_token, str) or not access_token:
        return None, "access token empty or absent"
    if not isinstance(refresh_token, str) or not refresh_token:
        return None, "refresh token empty or absent"
    if (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or expires_at <= 0
    ):
        return None, "expiry missing or invalid"
    try:
        expiry = datetime.fromtimestamp(expires_at / 1000, tz=timezone.utc)
    except (OverflowError, OSError, TypeError, ValueError):
        return None, "expiry unparseable"
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, "usable"


def _read_credentials_file_with_reason(path: Path) -> tuple[dict[str, str] | None, str]:
    """Read the live Claude Code file store with a safe rejection reason."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except PermissionError:
        return None, "unreadable"
    except (IsADirectoryError, OSError, UnicodeDecodeError):
        return None, "unreadable"
    except json.JSONDecodeError:
        return None, "invalid JSON"
    if not isinstance(raw, dict):
        return None, "expected structure absent"
    return _normalise_oauth(raw.get("claudeAiOauth"))


def read_credentials_file(
    path: Path = CREDENTIALS_PATH,
) -> dict[str, str] | None:
    """Return valid normalized OAuth from Claude Code's file store, else ``None``.

    The live file has one ``claudeAiOauth`` account object, not an account map.
    """
    oauth, _ = _read_credentials_file_with_reason(path)
    return oauth


def list_keychain_slots() -> list[str]:
    """Return candidate Claude Code keychain service names, suffixed first."""
    result = subprocess.run(
        _KEYCHAIN_DUMP_COMMAND,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    slots = set(_KEYCHAIN_SLOT_PATTERN.findall(result.stdout + result.stderr))
    return sorted(slots, key=lambda name: (name == KEYCHAIN_SERVICE, name))


def _read_keychain_slot_with_reason(name: str) -> tuple[dict[str, str] | None, str]:
    """Read one slot and return either normalized OAuth or a safe rejection reason."""
    try:
        result = subprocess.run(
            _KEYCHAIN_FIND_COMMAND + ["-s", name, "-w"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        raw = json.loads(result.stdout.strip())
        oauth, reason = _normalise_oauth(raw.get("claudeAiOauth"))
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except subprocess.CalledProcessError:
        return None, "read-failed"
    except (json.JSONDecodeError, OSError, OverflowError, TypeError, ValueError):
        return None, "invalid"
    if oauth is None:
        return None, "empty" if reason.startswith(("access", "refresh")) else "invalid"
    return oauth, "usable"


def read_keychain_slot(name: str) -> dict[str, str] | None:
    """Return usable normalized OAuth credentials for one service, else ``None``."""
    oauth, _ = _read_keychain_slot_with_reason(name)
    return oauth


def _persist_resolved_keychain_slot(name: str, path: Path) -> None:
    """Persist a resolved service name without touching credential material."""
    with store_lock():
        store = load_store(path=path)
        store["keychain_slot_resolved"] = name
        save_store(store, path=path)


def _cached_store_slot(path: Path) -> str | None:
    """Read the prior resolved service name, if it has the expected shape."""
    slot = load_store(path=path).get("keychain_slot_resolved")
    return slot if isinstance(slot, str) and slot else None


def _resolve_keychain_slot(
    *, verify: bool, store_path: Path
) -> tuple[str, dict[str, str]]:
    """Resolve one usable slot, preferring a live access token over an expired one."""
    global _RESOLVED_KEYCHAIN_SLOT

    scanned = 0
    empty_rejected = 0
    seen: set[str] = set()
    expired_candidate: tuple[str, dict[str, str]] | None = None

    def consider(name: str) -> tuple[str, dict[str, str]] | None:
        nonlocal scanned, empty_rejected, expired_candidate
        if name in seen:
            return None
        seen.add(name)
        scanned += 1
        oauth, reason = _read_keychain_slot_with_reason(name)
        if oauth is None:
            if reason == "empty":
                empty_rejected += 1
            return None
        if verify:
            try:
                if not fetch_profile_email(oauth):
                    return None
            except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError):
                return None
        if not _is_expired(oauth):
            return name, oauth
        if expired_candidate is None:
            expired_candidate = (name, oauth)
        return None

    for cached_slot in (_RESOLVED_KEYCHAIN_SLOT, _cached_store_slot(store_path)):
        if cached_slot is None:
            continue
        resolved = consider(cached_slot)
        if resolved is not None:
            _RESOLVED_KEYCHAIN_SLOT = resolved[0]
            return resolved

    try:
        slots = list_keychain_slots()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise NoUsableCredentials.keychain(scanned, empty_rejected) from None

    for slot in slots:
        resolved = consider(slot)
        if resolved is not None:
            _RESOLVED_KEYCHAIN_SLOT = resolved[0]
            _persist_resolved_keychain_slot(resolved[0], store_path)
            return resolved

    if expired_candidate is not None:
        _RESOLVED_KEYCHAIN_SLOT = expired_candidate[0]
        _persist_resolved_keychain_slot(expired_candidate[0], store_path)
        return expired_candidate
    raise NoUsableCredentials.keychain(scanned, empty_rejected)


def resolve_keychain_slot(*, verify: bool = False) -> tuple[str, dict[str, str]]:
    """Resolve and cache the live Claude Code keychain credential service."""
    return _resolve_keychain_slot(verify=verify, store_path=STORE_PATH)


def keychain_oauth() -> dict[str, str]:
    """Return live file OAuth, then legacy keychain OAuth, or a typed failure."""
    oauth, file_reason = _read_credentials_file_with_reason(CREDENTIALS_PATH)
    if oauth is not None:
        return oauth
    try:
        return resolve_keychain_slot()[1]
    except NoUsableCredentials as error:
        raise NoUsableCredentials(
            {"credentials-file": file_reason, **error.sources},
            scanned=error.scanned,
            empty_rejected=error.empty_rejected,
        ) from None


# ── HTTP helpers ──────────────────────────────────────────────────────────────

# urllib's default Python-urllib UA gets Cloudflare-blocked (error 1010) on
# platform.claude.com; present as the Claude Code CLI instead.
_USER_AGENT = "claude-cli/2.1.0 (external, cli)"


def _get_json(url: str, headers: dict, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _post_json(url: str, payload: dict, timeout: int = 10) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _http_error_detail(e: urllib.error.HTTPError) -> tuple[str, int | None]:
    """Return the API error message and optional Retry-After delay."""
    message = str(e)
    try:
        body = json.loads(e.read())
        api_message = (body.get("error") or {}).get("message")
        if api_message:
            message = str(api_message)
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        AttributeError,
        TypeError,
        OSError,
    ):
        pass

    retry_after = None
    try:
        value = e.headers.get("Retry-After") if e.headers else None
        retry_after = int(value) if value is not None else None
    except (TypeError, ValueError):
        pass
    return message, retry_after


# ── Token refresh + usage fetch ───────────────────────────────────────────────


def _is_expired(oauth: dict) -> bool:
    """Return True if the access token is expired or expiry is unknown."""
    try:
        exp = datetime.fromisoformat(oauth["expires_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return True
    return datetime.now(timezone.utc) >= exp - SKEW


def _refresh(oauth: dict) -> dict:
    """Exchange a refresh token for a new access token; return updated oauth dict."""
    # fmt: off
    data = _post_json(TOKEN_URL, {
        "grant_type": "refresh_token",
        "refresh_token": oauth["refresh_token"],
        "client_id": CLIENT_ID,
    })
    # fmt: on
    exp = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", oauth["refresh_token"]),
        "expires_at": exp.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _parse_usage(raw: dict) -> dict:
    """Extract five_hour, seven_day, and per-model (fable) windows from the raw
    API response.

    Tolerates extra top-level keys and microsecond/offset timestamp formats.
    """
    try:
        return _extract_windows(raw)
    except KeyError as e:
        raise ValueError(f"unexpected usage response shape: {list(raw)}") from e


def _extract_fable_limit(raw: dict) -> dict | None:
    """Pull the real per-model Fable weekly limit out of raw["limits"].

    The API reports a `weekly_scoped` entry per model (`scope.model.display_name`)
    alongside the overall `weekly_all` figure — this is the account's ACTUAL
    Fable-5 usage, not an estimate. Returns None if the account's response has
    no such entry (older API shape, or the model isn't in this plan).
    """
    for limit in raw.get("limits") or []:
        if limit.get("kind") != "weekly_scoped":
            continue
        model = (limit.get("scope") or {}).get("model") or {}
        if (model.get("display_name") or "").lower() == "fable":
            return {"utilization": limit["percent"], "resets_at": limit["resets_at"]}
    return None


def _extract_windows(raw: dict) -> dict:
    windows = {
        "five_hour": {
            "utilization": raw["five_hour"]["utilization"],
            "resets_at": raw["five_hour"]["resets_at"],
        },
        "seven_day": {
            "utilization": raw["seven_day"]["utilization"],
            "resets_at": raw["seven_day"]["resets_at"],
        },
    }
    fable = _extract_fable_limit(raw)
    if fable is not None:
        windows["fable"] = fable
    return windows


PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"


def fetch_profile_email(oauth: dict) -> str | None:
    """Return the account email for an access token (identity check)."""
    raw = _get_json(
        PROFILE_URL,
        {
            "Authorization": f"Bearer {oauth['access_token']}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    return (raw.get("account") or {}).get("email")


def fetch_usage(oauth: dict) -> dict:
    """Fetch current usage for a single account; returns parsed usage dict."""
    raw = _get_json(
        USAGE_URL,
        {
            "Authorization": f"Bearer {oauth['access_token']}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    return _parse_usage(raw)


# Refresh tokens are single-use: concurrent refreshes (multiple dashboard tabs,
# ThreadingHTTPServer) could rotate the same token twice and persist a stale one,
# locking the account out. Serialize the whole refresh-fetch-save cycle.
_FETCH_LOCK = threading.Lock()


def fetch_all_usage(path: Path = STORE_PATH, *, force: bool = False) -> list[dict]:
    """Refresh tokens as needed, fetch usage for every account, persist cache.

    ``force`` bypasses a persisted retry cooldown for a decision-time fresh read.
    It never turns stale windows into a successful response: failures retain the
    cache only with explicit error metadata.
    """
    with _FETCH_LOCK:
        credentials: dict[str, str] | None = None
        credentials_email: str | None = None
        credentials_error: NoUsableCredentials | None = None
        if _has_eligible_account(path, force=force):
            try:
                credentials = keychain_oauth()
            except NoUsableCredentials as error:
                credentials_error = error
            except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
                # Preserve the established stored-token path for a transient
                # local reader failure. Typed no-credential failures above are
                # the explicit all-sources-empty state.
                credentials = None
            else:
                try:
                    credentials_email = fetch_profile_email(credentials)
                except (
                    OSError,
                    urllib.error.URLError,
                    urllib.error.HTTPError,
                    ValueError,
                ):
                    # A verified source email is strongest, but an explicit
                    # switch owner remains a valid attribution fallback.
                    credentials_email = None
        with store_lock():
            return _fetch_all_usage_locked(
                path,
                force=force,
                credentials=credentials,
                credentials_email=credentials_email,
                credentials_error=credentials_error,
            )


def _has_eligible_account(path: Path, *, force: bool) -> bool:
    """Return whether any account can need a live keychain read this cycle."""
    if force:
        return True
    now = datetime.now(timezone.utc)
    for account in load_store(path=path).get("accounts", []):
        retry_until = (account.get("last_usage") or {}).get("retry_until")
        if not retry_until:
            return True
        try:
            retry_at = datetime.fromisoformat(retry_until.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            return True
        if retry_at <= now:
            return True
    return False


def _record_usage_http_error(
    acct: dict, error: urllib.error.HTTPError, now: datetime
) -> None:
    """Persist a 403/429 usage error while retaining cached usage windows."""
    message, retry_after = _http_error_detail(error)
    if error.code == 429:
        error_kind = "rate_limit"
        delay = retry_after or RATE_LIMIT_BACKOFF
    else:
        error_kind = "permission"
        delay = PERMISSION_BACKOFF
    previous = acct.get("last_usage") or {}
    last_success_at = previous.get("last_success_at") or (
        previous.get("fetched_at") if previous and not previous.get("error") else None
    )
    windows = {
        key: previous[key]
        for key in ("five_hour", "seven_day", "fable")
        if key in previous
    }
    acct["last_usage"] = {
        **windows,
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_success_at": last_success_at,
        "error": message,
        "error_kind": error_kind,
        "needs_relogin": False,
        "retry_until": (now + timedelta(seconds=delay)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _record_auth_error(acct: dict, error: Exception, now: datetime) -> None:
    """Persist a rejected stored refresh token while retaining cached windows."""
    previous = acct.get("last_usage") or {}
    windows = {
        key: previous[key]
        for key in ("five_hour", "seven_day", "fable")
        if key in previous
    }
    last_success_at = previous.get("last_success_at") or (
        previous.get("fetched_at") if previous and not previous.get("error") else None
    )
    acct["last_usage"] = {
        **windows,
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_success_at": last_success_at,
        "error": "Stored refresh token rejected; needs /login then fable-next --switch.",
        "error_detail": str(error),
        "error_kind": "auth",
        "needs_relogin": True,
        "retry_until": None,
    }


def _refresh_error_is_auth(error: Exception) -> bool:
    """Classify token-endpoint 400/401 as re-login failures, never throttles."""
    return isinstance(error, urllib.error.HTTPError) and error.code in (400, 401)


def _record_no_credentials_error(
    acct: dict[str, Any], error: NoUsableCredentials, now: datetime
) -> None:
    """Persist a live-credential failure without refreshing stored tokens."""
    previous = acct.get("last_usage") or {}
    windows = {
        key: previous[key]
        for key in ("five_hour", "seven_day", "fable")
        if key in previous
    }
    last_success_at = previous.get("last_success_at") or (
        previous.get("fetched_at") if previous and not previous.get("error") else None
    )
    acct["last_usage"] = {
        **windows,
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_success_at": last_success_at,
        "error": str(error),
        "error_kind": "no_credentials",
        "needs_relogin": False,
        "retry_until": None,
    }


def _is_credentials_owner(
    acct: dict[str, Any], store: dict[str, Any], credentials_email: str | None = None
) -> bool:
    """Identify the account expected to own Claude Code's live credentials."""
    if credentials_email:
        return acct.get("email") == credentials_email
    owner = store.get("keychain_owner")
    if isinstance(owner, str) and owner:
        return acct.get("email") == owner
    accounts = store.get("accounts") or []
    if any(bool(account.get("is_main")) for account in accounts):
        return bool(acct.get("is_main"))
    return False


def _fetch_all_usage_locked(
    path: Path,
    *,
    force: bool = False,
    credentials: dict[str, str] | None = None,
    credentials_email: str | None = None,
    credentials_error: NoUsableCredentials | None = None,
) -> list[dict]:
    store = load_store(path=path)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    # The account logged into Claude Code rotates its own tokens, killing our
    # snapshot (refresh tokens are single-use). Resolve the live credential
    # identity once, lazily after cooldown checks, and prefer those credentials
    # for the matching account.
    kc, kc_email = credentials, credentials_email
    for acct in store["accounts"]:
        previous = acct.get("last_usage") or {}
        retry_until = previous.get("retry_until")
        if retry_until and not force:
            try:
                retry_at = datetime.fromisoformat(retry_until.replace("Z", "+00:00"))
                if retry_at > now_dt:
                    continue
            except (AttributeError, TypeError, ValueError):
                pass

        if credentials_error is not None and _is_credentials_owner(
            acct, store, credentials_email
        ):
            _record_no_credentials_error(acct, credentials_error, now_dt)
            continue

        try:
            if kc and _is_credentials_owner(acct, store, kc_email):
                try:
                    usage = fetch_usage(kc)
                    acct["last_usage"] = {
                        **usage,
                        "fetched_at": now,
                        "last_success_at": now,
                        "error": None,
                        "needs_relogin": False,
                    }
                    continue
                except urllib.error.HTTPError as e:
                    if e.code in (403, 429):
                        _record_usage_http_error(acct, e, now_dt)
                        continue
                except Exception:  # noqa: BLE001 — fall back to stored tokens
                    pass
            if _is_expired(acct["oauth"]):
                try:
                    acct["oauth"] = _refresh(acct["oauth"])
                    save_store(
                        store, path=path
                    )  # persist rotated token even if fetch fails
                except Exception as refresh_error:  # noqa: BLE001
                    if _refresh_error_is_auth(refresh_error):
                        _record_auth_error(acct, refresh_error, now_dt)
                        continue
                    # Refresh endpoint may be rate-limited or the refresh token
                    # dead while the access token still works — try it anyway.
                    pass
            try:
                usage = fetch_usage(acct["oauth"])
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):
                    _record_usage_http_error(acct, e, now_dt)
                    continue
                if e.code != 401:
                    raise
                # Access token can be invalidated before expires_at (e.g. the
                # logged-in Claude Code session rotated it). Force-refresh once.
                try:
                    acct["oauth"] = _refresh(acct["oauth"])
                except Exception as refresh_error:  # noqa: BLE001
                    if _refresh_error_is_auth(refresh_error):
                        _record_auth_error(acct, refresh_error, now_dt)
                        continue
                    raise
                save_store(store, path=path)
                try:
                    usage = fetch_usage(acct["oauth"])
                except urllib.error.HTTPError as retry_error:
                    if retry_error.code in (403, 429):
                        _record_usage_http_error(acct, retry_error, now_dt)
                        continue
                    raise
            acct["last_usage"] = {
                **usage,
                "fetched_at": now,
                "last_success_at": now,
                "error": None,
                "needs_relogin": False,
            }
        except Exception as e:  # noqa: BLE001 — any failure grays this orb only
            prev = acct.get("last_usage") or {}
            last_success_at = prev.get("last_success_at") or (
                prev.get("fetched_at") if prev and not prev.get("error") else None
            )
            acct["last_usage"] = {
                **prev,
                "fetched_at": now,
                "last_success_at": last_success_at,
                "error": str(e),
                "error_kind": "auth" if _refresh_error_is_auth(e) else None,
                "needs_relogin": _refresh_error_is_auth(e),
                "retry_until": None,
            }
    save_store(store, path=path)
    return store["accounts"]


# ── Presentation helpers ──────────────────────────────────────────────────────


def remaining_color(pct: int | float) -> tuple[str, str]:
    """Return (color_hi, color_lo) for a given remaining-capacity percentage."""
    for floor, hi, lo in COLOR_RAMP:
        if pct >= floor:
            return hi, lo
    return COLOR_RAMP[-1][1], COLOR_RAMP[-1][2]


def days_until_renewal(billing_day: int, today: _dt.date | None = None) -> int:
    """Return calendar days until the next billing renewal date."""
    today = today or _dt.date.today()
    last = calendar.monthrange(today.year, today.month)[1]
    target = _dt.date(today.year, today.month, min(billing_day, last))
    if target < today:
        y, m = (
            (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        )
        last = calendar.monthrange(y, m)[1]
        target = _dt.date(y, m, min(billing_day, last))
    return (target - today).days


def public_view(accts: list[dict]) -> list[dict]:
    """Strip credentials; return everything the dashboard JS needs."""
    out = []
    for a in accts:
        u = a.get("last_usage") or {}
        entry = {
            "email": a["email"],
            "plan": a.get("plan", ""),
            "renews_in_days": (
                days_until_renewal(a["billing_day"]) if a.get("billing_day") else None
            ),
            "fetched_at": u.get("fetched_at"),
            "last_success_at": u.get("last_success_at"),
            "error": u.get("error"),
            "error_kind": u.get("error_kind"),
            "needs_relogin": bool(u.get("needs_relogin")),
            "retry_until": u.get("retry_until"),
            "windows": {},
        }
        for key in ("five_hour", "seven_day", "fable"):
            if key in u:
                remaining = round(min(100, max(0, 100 - u[key]["utilization"])))
                hi, lo = remaining_color(remaining)
                entry["windows"][key] = {
                    "remaining_pct": remaining,
                    "resets_at": u[key]["resets_at"],
                    "color_hi": hi,
                    "color_lo": lo,
                }
        out.append(entry)
    return out


# ── Subscription cost + lifetime spend ────────────────────────────────────────
# An account carries `monthly_cost` (USD/mo) and `subscription_intervals`: a list
# of {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD" | None}. end=None means the
# subscription is currently open. Multiple intervals capture cancel/restart gaps
# (Andrew doesn't keep all 3 Max accounts running continuously).

_AVG_MONTH_DAYS = 30.4375  # 365.25 / 12 — prorate partial months for lifetime spend
CURRENT_PLAN_MONTHLY_USD = {
    "max_20x": 200.0,
}


def _as_date(s) -> _dt.date:
    return _dt.date.fromisoformat(s)


def months_active(intervals: list[dict] | None, today: _dt.date | None = None) -> float:
    """Total prorated months a subscription has been active across all intervals.

    A None/missing end is treated as still-open (counts through today). Future
    starts and inverted ranges contribute nothing; ends are capped at today so
    lifetime spend never bills the future.
    """
    today = today or _dt.date.today()
    days = 0
    for iv in intervals or []:
        start = _as_date(iv["start"])
        end = _as_date(iv["end"]) if iv.get("end") else today
        if end > today:
            end = today
        if end > start:
            days += (end - start).days
    return days / _AVG_MONTH_DAYS


def is_active(account: dict, today: _dt.date | None = None) -> bool:
    """True when a current interval or recent charge covers today.

    A current open interval wins over an old receipt: receipt capture can lag a
    subscription renewal, while the interval is explicitly maintained as the
    current billing state.
    """
    today = today or _dt.date.today()
    for iv in account.get("subscription_intervals") or []:
        start = _as_date(iv["start"])
        end = _as_date(iv["end"]) if iv.get("end") else None
        if start <= today and (end is None or today <= end):
            return True
    charges = account.get("charges") or []
    if charges:
        last_charge = _as_date(max(charge["date"] for charge in charges))
        return today < last_charge + _dt.timedelta(days=31)
    return False


def current_plan_monthly_cost(account: dict) -> float:
    """Return the current public plan rate, preserving receipts for lifetime totals."""
    plan = str(account.get("plan", "")).lower()
    return CURRENT_PLAN_MONTHLY_USD.get(plan, float(account.get("monthly_cost", 0)))


def current_monthly_cost(account: dict, today: _dt.date | None = None) -> float:
    """The monthly_cost if the account is currently subscribed, else 0."""
    return current_plan_monthly_cost(account) if is_active(account, today) else 0.0


def lifetime_spend(account: dict, today: _dt.date | None = None) -> float:
    """Total USD spent on this subscription to date.

    Prefers the actual `charges` ledger (real amounts paid, tax included) when
    present — exact and authoritative. Falls back to a prorated estimate
    (monthly_cost x months active) for accounts with no receipts captured yet.
    """
    charges = account.get("charges")
    if charges:
        return round(sum(float(c["amount"]) for c in charges), 2)
    cost = float(account.get("monthly_cost", 0))
    return round(cost * months_active(account.get("subscription_intervals"), today), 2)


# ── Optimal-account recommendation (hybrid score) ─────────────────────────────
# Goal (Andrew): maximize the Max subscriptions while they're active, defaulting
# to the main account. Score blends headroom (5hr + weekly remaining), a
# use-it-or-lose-it "drain" boost for accounts that renew soon with unused weekly
# allowance, and a bias toward the main account.

MAIN_BONUS = 8.0  # flat score bump for the main account
DRAIN_WINDOW_DAYS = 14  # renewal-drain only engages within this many days
THROTTLE_PCT = 15  # 5hr remaining below this = effectively throttled


def _healthy(entry: dict) -> bool:
    """An entry is usable if it errored-free and has both usage windows."""
    w = entry.get("windows") or {}
    return not entry.get("error") and "five_hour" in w and "seven_day" in w


def account_score(entry: dict) -> tuple[float, list[str]]:
    """Return (0-100 score, human reasons) for a public_view entry.

    `entry` must already carry `is_main` and `renews_in_days`.
    """
    if not _healthy(entry):
        return 0.0, ["usage unavailable"]
    h5 = entry["windows"]["five_hour"]["remaining_pct"]
    h7 = entry["windows"]["seven_day"]["remaining_pct"]
    # Weekly is the bigger budget; 5hr is the immediate gate — weight weekly more.
    headroom = 0.4 * h5 + 0.6 * h7
    reasons = [f"{h7}% weekly free", f"{h5}% 5h free"]

    days = entry.get("renews_in_days")
    drain = 0.0
    if days is not None and days <= DRAIN_WINDOW_DAYS:
        drain = h7 * max(0.0, (DRAIN_WINDOW_DAYS - days) / DRAIN_WINDOW_DAYS)
        if drain >= 20:
            reasons.append(f"renews in {days}d — burn weekly now")

    raw = 0.6 * headroom + 0.4 * drain
    if entry.get("is_main"):
        raw = min(100.0, raw + MAIN_BONUS)
        reasons.append("main account")
    if h5 < THROTTLE_PCT:
        reasons.append("5h throttled")
    return round(raw, 1), reasons


def recommend(entries: list[dict]) -> tuple[str | None, dict]:
    """Pick the optimal account email + per-account {score, reasons}.

    Ties break toward the main account. If every account is unhealthy, fall back
    to the main account (or the first listed).
    """
    scored = {}
    ranking = []
    for e in entries:
        s, r = account_score(e)
        scored[e["email"]] = {"score": s, "reasons": r}
        ranking.append((s, bool(e.get("is_main")), e["email"], bool(e.get("active"))))

    healthy = [x for x in ranking if x[0] > 0 and x[3]]
    if healthy:
        optimal = max(healthy, key=lambda x: (x[0], x[1]))[2]
    else:
        mains = [x for x in ranking if x[1]]
        optimal = (mains or ranking or [(0, False, None, False)])[0][2]
    return optimal, scored


def dashboard_payload(accts: list[dict], today: _dt.date | None = None) -> dict:
    """Full dashboard JSON: per-account orbs + cost/lifetime + optimal pick.

    Composes `public_view` (orb/usage data) with subscription cost, lifetime
    spend, and the hybrid recommendation, plus a combined-spend summary.
    """
    entries = public_view(accts)
    by_email = {a["email"]: a for a in accts}
    for e in entries:
        a = by_email[e["email"]]
        e["is_main"] = bool(a.get("is_main", False))
        e["monthly_cost"] = current_plan_monthly_cost(a)
        e["subscription_intervals"] = a.get("subscription_intervals", [])
        e["active"] = is_active(a, today)
        e["current_monthly_cost"] = current_monthly_cost(a, today)
        e["lifetime_spend"] = lifetime_spend(a, today)

    optimal, scored = recommend(entries)
    for e in entries:
        s = scored.get(e["email"], {})
        e["score"] = s.get("score", 0.0)
        e["reasons"] = s.get("reasons", [])
        e["is_optimal"] = e["email"] == optimal

    summary = {
        "optimal_email": optimal,
        "active_accounts": sum(1 for e in entries if e["active"]),
        "total_current_monthly": round(
            sum(e["current_monthly_cost"] for e in entries), 2
        ),
        "total_lifetime": round(sum(e["lifetime_spend"] for e in entries), 2),
    }
    return {"accounts": entries, "summary": summary}
