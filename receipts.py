"""Auto-ingest Anthropic billing emails into the account charges ledger.

Reads Anthropic receipts/welcome/cancellation emails from the reachable Gmail
mailbox (via the ``gws`` CLI), parses date/amount/plan/account, and idempotently
upserts them into the per-account ``charges`` ledger + ``subscription_intervals``
so the dashboard's expense data never needs manual editing again.

Receipts for the other accounts (awebber2k, hotfixops) arrive here once their
Gmail auto-forwards Anthropic mail to the reachable mailbox; ``route_account``
assigns each message to the right account by the recipient found in its headers.

Pure parsing (``parse_*``, ``classify``, ``route_account``) is IO-free and unit
tested; ``ingest`` wires it to gws + the store.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import logging
import re
import subprocess
from email.utils import parsedate_to_datetime
from pathlib import Path

import accounts

logger = logging.getLogger("claude-usage.receipts")

SEEN_PATH = Path.home() / ".claude" / "usage_receipts_seen.json"
ANTHROPIC_QUERY = "from:mail.anthropic.com"

_AMOUNT_RE = re.compile(r"Amount paid\s*\$\s*([\d,]+\.\d{2})", re.I)
_TOTAL_RE = re.compile(r"\bTotal\s*\$\s*([\d,]+\.\d{2})", re.I)
_PLAN_RE = re.compile(r"(Max plan\s*-?\s*\d+x|Max\s*\d+x|Claude\s*Pro)", re.I)


# ── Pure parsing (IO-free, unit tested) ───────────────────────────────────────

def parse_amount(text: str) -> float | None:
    """The actual amount paid (tax included). Prefers 'Amount paid', then 'Total'."""
    m = _AMOUNT_RE.search(text) or _TOTAL_RE.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def parse_plan(text: str) -> str:
    """Normalize the plan name, e.g. 'Max plan - 20x' or 'Claude Pro'."""
    m = _PLAN_RE.search(text)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def parse_email_date(raw: str) -> str | None:
    """RFC-2822 Date header -> 'YYYY-MM-DD' in UTC (the charge date)."""
    try:
        return parsedate_to_datetime(raw).astimezone(_dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def classify(subject: str) -> str:
    """charge | start | cancel | other, from the subject line."""
    s = subject.lower()
    if "welcome to the max" in s or "starting your max" in s:
        return "start"
    if "cancel" in s:
        return "cancel"
    if "receipt" in s:
        return "charge"
    return "other"


def route_account(headers: dict, body: str, known: list[str], owner: str) -> str:
    """Assign a message to one of the known account emails.

    Forwarded receipts carry the original recipient in their headers; direct
    receipts belong to the mailbox owner. Falls back to a body scan, then owner.
    """
    hay = " ".join(
        headers.get(h, "") for h in
        ("To", "Delivered-To", "X-Forwarded-To", "X-Forwarded-For", "Cc", "X-Original-To")
    ).lower()
    present = [e for e in known if e.lower() in hay]
    # The owner mailbox is only the forward *destination*; a forwarded receipt's
    # true account is the other (original) recipient. Prefer the non-owner.
    non_owner = [e for e in present if e != owner]
    if non_owner:
        return non_owner[0]
    if owner in present:
        return owner
    bl = (body or "").lower()
    for e in known:
        if e != owner and e.lower() in bl:
            return e
    return owner if owner in known else (known[0] if known else owner)


def parse_receipt(headers: dict, body: str, known: list[str], owner: str) -> dict | None:
    """Parse one Anthropic billing email into a structured event, or None."""
    kind = classify(headers.get("Subject", ""))
    if kind == "other":
        return None
    event = {
        "account": route_account(headers, body, known, owner),
        "date": parse_email_date(headers.get("Date", "")),
        "kind": kind,
        "plan": parse_plan(body) or parse_plan(headers.get("Subject", "")),
    }
    if kind == "charge":
        event["amount"] = parse_amount(body)
    return event


# ── gws IO ────────────────────────────────────────────────────────────────────

def _gws_json(args: list[str]) -> dict:
    """Run a gws command and parse its JSON (stripping the keyring banner line)."""
    out = subprocess.run(["gws", *args], capture_output=True, text=True).stdout
    i = out.find("{")
    if i < 0:
        i = out.find("[")
    return json.loads(out[i:]) if i >= 0 else {}


def mailbox_owner() -> str:
    return _gws_json(["gmail", "users", "getProfile", "--params", '{"userId":"me"}']).get(
        "emailAddress", ""
    )


def list_message_ids(query: str = ANTHROPIC_QUERY, limit: int = 100) -> list[str]:
    d = _gws_json([
        "gmail", "users", "messages", "list",
        "--params", json.dumps({"userId": "me", "q": query, "maxResults": limit}),
    ])
    return [m["id"] for m in d.get("messages", [])]


def get_message(msg_id: str) -> dict:
    return _gws_json([
        "gmail", "users", "messages", "get",
        "--params", json.dumps({"userId": "me", "id": msg_id, "format": "full"}),
    ])


def _headers(msg: dict) -> dict:
    return {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _flatten_body(payload: dict) -> str:
    """Decode every MIME part and strip HTML tags to flat text."""
    text = ""
    data = payload.get("body", {}).get("data")
    if data:
        try:
            text += base64.urlsafe_b64decode(data).decode("utf-8", "ignore")
        except (ValueError, base64.binascii.Error):
            pass
    for part in payload.get("parts", []):
        text += _flatten_body(part)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text)


# ── Ledger upsert ─────────────────────────────────────────────────────────────

def _load_seen() -> set[str]:
    if SEEN_PATH.exists():
        return set(json.loads(SEEN_PATH.read_text()))
    return set()


def _save_seen(seen: set[str]) -> None:
    SEEN_PATH.write_text(json.dumps(sorted(seen)))


def apply_event(account: dict, event: dict) -> bool:
    """Apply a parsed event to one account record. Returns True if it changed.

    Idempotent: a charge whose (date, amount) already exists is skipped, so this
    never double-counts the hand-seeded ledger or a re-scan of the same email.
    """
    changed = False
    # Only subscription receipts (a recognized Max/Pro plan line) belong in the
    # charges ledger. API pay-as-you-go receipts have no plan -> skip them; they
    # are a separate spend bucket, not a subscription.
    if event["kind"] == "charge" and event.get("amount") and event.get("date") and event.get("plan"):
        ledger = account.setdefault("charges", [])
        key = (event["date"], round(event["amount"], 2))
        if key not in {(c["date"], round(float(c["amount"]), 2)) for c in ledger}:
            ledger.append({"date": event["date"], "plan": event["plan"],
                           "amount": round(event["amount"], 2), "source": "gmail"})
            ledger.sort(key=lambda c: c["date"])
            changed = True
    elif event["kind"] == "start" and event.get("date"):
        ivs = account.setdefault("subscription_intervals", [])
        covered = any(
            iv["start"] <= event["date"] and (iv["end"] is None or iv["end"] >= event["date"])
            for iv in ivs
        )
        if not covered:
            ivs.append({"start": event["date"], "end": None})
            ivs.sort(key=lambda iv: iv["start"])
            changed = True
    elif event["kind"] == "cancel" and event.get("date"):
        for iv in account.get("subscription_intervals", []):
            if iv["end"] is None and iv["start"] <= event["date"]:
                iv["end"] = event["date"]
                changed = True
    return changed


def ingest(store_path: Path = accounts.STORE_PATH, dry_run: bool = False) -> dict:
    """Scan the mailbox, upsert new charges/intervals, persist. Returns a summary."""
    store = accounts.load_store(store_path)
    known = [a["email"] for a in store["accounts"]]
    by_email = {a["email"]: a for a in store["accounts"]}
    owner = mailbox_owner()
    seen = _load_seen()

    added, scanned = [], 0
    for msg_id in list_message_ids():
        if msg_id in seen:
            continue
        scanned += 1
        msg = get_message(msg_id)
        event = parse_receipt(_headers(msg), _flatten_body(msg.get("payload", {})), known, owner)
        seen.add(msg_id)
        if not event or event["account"] not in by_email:
            continue
        if apply_event(by_email[event["account"]], event):
            added.append(event)

    if not dry_run:
        accounts.save_store(store, store_path)
        _save_seen(seen)
    return {"scanned": scanned, "added": added, "owner": owner}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Ingest Anthropic receipts into the charges ledger.")
    ap.add_argument("--dry-run", action="store_true", help="parse + report, don't write")
    args = ap.parse_args()
    summary = ingest(dry_run=args.dry_run)
    logger.info("owner=%s scanned=%d added=%d", summary["owner"], summary["scanned"], len(summary["added"]))
    for e in summary["added"]:
        logger.info("  + %s %s %s %s", e["account"], e["date"], e.get("amount", e["kind"]), e["plan"])


if __name__ == "__main__":
    main()
