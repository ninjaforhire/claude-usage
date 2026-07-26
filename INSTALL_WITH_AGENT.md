# Install with your coding agent

Download or clone this repository, open your coding agent in the repository
folder, and paste the prompt below.

```text
Install and verify this local Claude + Codex Usage Dashboard for me.

Safety rules:
1. Work only inside this repository except for the dashboard-owned derived databases at ~/.claude/usage.db and ~/.claude-codex-usage/codex.db, normal profiles at ~/.hotfix-ops-usage/accounts.json, and isolated test profiles under ~/.hotfix-ops-usage/testing/.
2. Never read, print, copy, edit, or upload any credential file, keychain item, browser cookie, API key, OAuth token, .env file, prompt, or message body.
3. Do not automate provider login. If Claude or Codex is logged out, stop and tell me the exact official interactive login command I should run myself.
4. Do not add a hosted service, telemetry, executable installer, browser scraper, private endpoint, or third-party Python dependency.
5. Do not commit, push, publish, or delete source history.

Steps:
1. Read README.md, connectors/claude_subscription.py, connectors/codex_subscription.py, dashboard.py, and cli.py.
2. Verify Python 3.9 or newer, then verify whether `claude` and `codex` are available on PATH. Use `python` on Windows and `python3` on macOS/Linux for every command below.
3. Run `<PYTHON> -m unittest discover -s tests -v`. Fix only installation or compatibility problems you can reproduce, preserve unrelated work, and show me the diff before applying any source change.
4. Run `<PYTHON> connectors/claude_subscription.py` and confirm its output contains only provider, availability, source, sanitized account fields, windows, fetched_at, and an optional error.
5. Run `<PYTHON> connectors/codex_subscription.py` and apply the same safe-output check. It may also expose only the available reset-credit count and earliest expiry; it must never expose a reset-credit ID or consume one.
6. Offer to create an account card with `<PYTHON> cli.py accounts setup --label "<user chosen label>"`. Do not run it until the user supplies that display label. It may inspect only the currently signed-in official CLI account, must never automate login, and must save nothing if no provider is connected.
7. Confirm the dashboard imports both connector files separately and that POST /api/refresh scans both local history stores without deleting valid cached data on connector failure.
8. Start `<PYTHON> cli.py dashboard --no-browser`, open http://localhost:8080, and verify Overview, Claude, Codex, and Refresh.
9. Start `<PYTHON> cli.py dashboard --test-mode --no-browser --port 8334`, open http://localhost:8334, and verify the orange TEST MODE banner, blank first-run screen, fake multi-account samples, and Reset preview. Confirm this mode does not invoke provider login, scan local histories, or read normal account profiles.
10. Report what worked, any provider that needs me to log in, the local URLs, and any real blocker. Do not claim browser/desktop chat coverage beyond local Claude Code or Codex rollout files.

Important product behavior:
- Claude history comes from ~/.claude/projects/**/*.jsonl.
- Codex history comes from ~/.codex/sessions/**/*.jsonl.
- Claude account status comes from `claude auth status --json`.
- Codex limits come from the authenticated local `codex app-server --stdio`.
- Claude consumer subscription windows must remain labeled unavailable unless a supported source or an explicitly trusted user-local helper supplies the documented normalized windows schema.
- Never combine Claude and Codex limit percentages.
- Normal account profiles belong only under `~/.hotfix-ops-usage/accounts.json`; isolated first-run testing profiles belong only under `~/.hotfix-ops-usage/testing/`. Neither belongs in the repository. They may store sanitized snapshots but never credentials, raw provider responses, transcripts, or project history.
- `skills/fable-next/` and `skills/codex-next/` may be copied as source instructions, but only the user may switch provider logins or redeem a Codex reset credit.
```

The prompt is instructions, not an executable. The agent should inspect and
explain every compatibility change before you approve it.
