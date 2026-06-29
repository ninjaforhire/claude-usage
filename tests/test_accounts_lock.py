"""Tests for the cross-process store lock (accounts.store_lock).

Regression guard for the 2026-06-29 incident: the launchd token-refresh job and
a manual ``accounts add``/``refresh`` ran concurrently in separate processes,
both rotated the same single-use OAuth refresh token, and persisted a stale one —
graying the mighty + awebber2k orbs with HTTP 400/429. ``threading.Lock`` cannot
serialize separate processes; ``store_lock`` (flock) does. flock contends across
distinct file descriptions, so a threaded read-modify-write race reproduces the
cross-process behaviour without spawning subprocesses.
"""

import inspect
import threading
import time

import accounts


def test_store_lock_serializes_read_modify_write(tmp_path):
    """Concurrent locked increments never lose an update (200 == 4 * 50)."""
    lock = tmp_path / "test.lock"
    counter = tmp_path / "counter.txt"
    counter.write_text("0")

    def worker():
        for _ in range(50):
            with accounts.store_lock(lock):
                value = int(counter.read_text())
                time.sleep(0.0003)  # widen the race window between read and write
                counter.write_text(str(value + 1))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert int(counter.read_text()) == 200


def test_store_lock_releases_for_sequential_acquire(tmp_path):
    """The lock is released on exit so the next acquirer is not blocked."""
    lock = tmp_path / "test.lock"
    with accounts.store_lock(lock):
        pass
    # If the first acquire leaked the lock, this second one would hang the test.
    with accounts.store_lock(lock):
        pass


def test_store_lock_noop_without_fcntl(tmp_path, monkeypatch):
    """Where fcntl is unavailable (Windows), the lock degrades to a no-op."""
    monkeypatch.setattr(accounts, "fcntl", None)
    lock = tmp_path / "test.lock"
    with accounts.store_lock(lock):
        pass
    # No-op path must not create the lock file.
    assert not lock.exists()


def test_locked_helper_takes_no_reentrant_lock():
    """store_lock is non-reentrant: the function fetch_all_usage runs *inside*
    the lock (_fetch_all_usage_locked) must never call store_lock or a public
    store-mutating API, or the same process self-deadlocks on flock. This static
    guard fails loudly if a future edit reintroduces that nesting.
    """
    src = inspect.getsource(accounts._fetch_all_usage_locked)
    for forbidden in ("store_lock(", "update_oauth(", "upsert_account("):
        assert forbidden not in src, f"_fetch_all_usage_locked must not call {forbidden}"


def test_upsert_account_works_under_lock(tmp_path, monkeypatch):
    """upsert_account still round-trips with the lock wrapping it."""
    monkeypatch.setattr(accounts, "LOCK_PATH", tmp_path / "usage_accounts.lock")
    store = tmp_path / "usage_accounts.json"
    accounts.save_store({"accounts": []}, path=store)
    accounts.upsert_account(
        {"email": "a@b.com", "plan": "max_20x", "billing_day": 9,
         "oauth": {}, "last_usage": None},
        path=store,
    )
    loaded = accounts.load_store(path=store)
    assert [a["email"] for a in loaded["accounts"]] == ["a@b.com"]
