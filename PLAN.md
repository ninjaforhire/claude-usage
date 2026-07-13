# PLAN 2: Charge-based account activity + quiet inactive cards (both UIs)

## Verified facts (2026-07-12, do not re-derive)

Store `~/.claude/usage_accounts.json` (via receipts ingest):

| account | charges ledger (last) | subscription_intervals | is_active() says | REALITY (owner-confirmed) |
|---|---|---|---|---|
| andrew@mightyphotobooths.com | 2026-04-09, 05-09, 06-09, **07-12** ($213.20 ea) | `[{start:2026-04-09, end:2026-07-08}]` (closed) | **inactive** | **ACTIVE — the only active account** |
| andrew@hotfixops.com | 2026-06-11 only | `[{start:2026-06-11, end:None}]` (open) | **active** | INACTIVE (lapsed 2026-07-11) |
| awebber2k@gmail.com | ..., 2026-06-10 | `[..., {start:2026-06-10, end:None}]` (open) | **active** | INACTIVE (lapsed 2026-07-10) |

Diagnosis: `is_active()` trusts `subscription_intervals`, but cancel receipts
never arrive (open intervals live forever) and renewal receipts can lag or a
closer can end an interval that later renews. The `charges` ledger is the
authoritative signal (code comments already call it "exact and authoritative").
Dashboard header consequently shows "2 active" — inverted from reality — and
the two lapsed accounts render noisy STALE/error cards instead of quiet
INACTIVE ones.

## Changes

### A. `accounts.py` — charge-based `is_active`

1. Rework `is_active(account, today=None)`:
   - If the account has a non-empty `charges` ledger:
     `active = today < last_charge_date + 31 days` (paid-through-one-month
     rule; use the max `date` in the ledger; dates are `YYYY-MM-DD` strings).
   - Else (no ledger — hand-seeded accounts): keep the existing
     interval-based logic unchanged as fallback.
   - Do NOT mutate the store; intervals remain untouched (they still feed
     `months_active` / `lifetime_spend` — those functions must not change).
2. `recommend(entries)`: only entries with `entry.get("active")` truthy are
   eligible for `optimal` (the USE ME pick). Keep the existing fallback: if no
   active+healthy entry, fall back to main account (or first listed). Note
   `dashboard_payload` sets `e["active"]` before calling `recommend` — the
   data is already on the entries.
3. Tests (`tests/test_accounts_cost.py` or wherever `is_active` is covered —
   follow existing file organization):
   - fresh charge (today) → active even when its interval is closed
     (the exact mighty scenario above).
   - last charge 31+ days ago with an OPEN interval → inactive
     (the exact hotfixops scenario).
   - boundary: last charge exactly 31 days ago → inactive; 30 days → active.
   - empty/missing charges ledger → interval fallback still honored both ways.
   - `recommend` never picks an inactive entry as optimal when an active one
     exists; falls back to main when nothing is active.

### B. `dashboard.py` (JS in HTML_TEMPLATE) — quiet inactive cards

In `renderAccounts()`, reorder branches: check `a.active === false` FIRST,
before the error branch:

- Inactive card: `acct-card acct-inactive` class, `INACTIVE` badge
  (existing `acct-badge-inactive` style), orbs rendered if windows exist,
  cost line kept, and NO error text, NO STALE badge, NO retry countdown,
  NO "renews in Xd" (meaningless on a lapsed sub — omit that span).
- Active accounts keep the current behavior exactly (STALE orbs card /
  error-kind hints from the previous change).
- Inactive accounts must not participate in `bestPct` (already excluded when
  errored; extend the exclusion to `a.active === false`).

### C. Jimbo Usage tab — port the SAME rendering

File: `/Users/mightydesigncenter/_Code/mighty/agents/tools/jimbo/static/usage.html`
(a different git repo — only touch this one file there).

Its account row JS is the OLD pre-fix version (error-only card with the
"re-auth: python3 cli.py accounts add" hint, ~line 348). Port both behaviors
so the two UIs render identically:

1. Quiet-inactive branch (as in B).
2. The stale-orbs / error-kind rendering for ACTIVE accounts (mirror what
   `dashboard.py` now does: STALE badge card with cached windows + note line
   `error · cached Xm ago · retry in Ym`; error-only card with kind-specific
   hint — permission → "org blocked usage API (Anthropic-side) — re-auth
   won't help", rate_limit → guarded countdown or "retrying soon").
   Add the missing CSS (`.acct-stale-note`, `acct-inactive` if absent) to its
   style block, matching its existing card typography. The payload it
   receives comes from the same `dashboard_payload` (Jimbo imports this repo's
   `accounts.py` from `~/tools/claude-usage`), so `error_kind`, `retry_until`,
   `active`, `windows` are all present.
3. Adapt helper functions to what usage.html already has — if it lacks
   `fmtCountdown`/`fmtAgo` equivalents, add minimal local versions.

## Expected outcome (acceptance)

- Payload: mighty `active: true` + `is_optimal: true`; hotfixops + awebber2k
  `active: false`; summary `active_accounts: 1`.
- Both UIs: mighty green + USE ME; other two = quiet grey INACTIVE cards.

## Constraints

- Touch ONLY: `accounts.py`, `dashboard.py`, tests, and the one `usage.html`.
- No store/data mutation, no changes to `months_active`/`lifetime_spend`/
  receipts ingest, no Jimbo Python files, stdlib only, no new deps.
- NEVER print or log token values.

## Proof

`python3 -m unittest discover -s tests -v` in this repo — full suite green
including new tests.
