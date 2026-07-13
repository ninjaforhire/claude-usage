# PLAN-REVIEW-LOG — Account-orb resilience (429/403 cooldowns + stale orbs)

## Act 3 — Build (2026-07-12, /codex-build)

Spec: PLAN.md (frozen; grounded in live-API evidence gathered same day).
Builder: Codex gpt-5.6-sol, thread `019f5937-ff41-7b93-a17f-e0df7e0e102e`, --yolo.
Reviewer: Claude (Fable 5), this session.

### Root cause (verified before spec)

- Anthropic org-level 403 on USAGE_URL for hotfixops + awebber2k orgs:
  "OAuth authentication is currently not allowed for this organization."
  Fresh token still 403s (proved); profile endpoint 200; re-auth cannot fix.
- 15-min daemon + page loads re-hammered the dead call → server escalated to
  429 with Retry-After 3294s. Client ignored the header.
- Dashboard rendered error-only grey cards despite cached usage windows, with
  misleading "re-auth" advice.

### Round 1 — Codex build

Implemented PLAN.md sections A–D: `_http_error_detail`, `_record_usage_http_error`,
cooldown skip (lazy keychain resolve), keychain double-fetch fix, public_view
passthrough, STALE orb cards + error-kind hints, bestPct/USE ME exclusion,
timestamped refresh ticks, 9 new tests, CHANGELOG v1.3.1 — TBD.
Proof (Codex): 153 tests OK.

### Claude's verdict (round 1)

Verified independently: full diff read, suite re-run (153 OK). Two defects found:
- F1 accounts.py: generic exception handler `{**prev, error}` leaked stale
  `error_kind`/`retry_until` from a prior 429/403 (wrong hint after network blip).
- F2 dashboard.py: `fmtCountdown(a.retry_until)` unguarded in rate_limit hint →
  "retry in NaN" when missing/past.

### Round 2 — Codex fix (same thread)

Both fixed + regression tests added. Proof (Codex): 154 OK.

### Claude's verdict (round 2) — APPROVED

- Diff verified: handler nulls error_kind/retry_until; JS guard mirrors stale-card.
- Suite re-run by Claude: **154 tests OK**.
- Live verify: daemon kickstarted; POST /api/accounts/refresh → mighty healthy
  (USE ME), hotfixops + awebber2k report `error_kind: rate_limit` with server
  Retry-After honored in `retry_until`, cached windows intact → STALE orb cards.
  Subsequent fetches skip until cooldown lapses; post-cooldown 403 will record
  `permission` kind + 6h backoff + honest org-block message.

Rounds used: 1 fix round of MAX 2. Deviations from spec: none.
