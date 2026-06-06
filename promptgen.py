"""
promptgen.py - Turn a selection of findings into a copyable repair-request prompt.

Each finding gets a model recommendation (haiku/sonnet/opus) based on repair
complexity, and the prompt tells Andrew to batch cheap fixes first. The generated
prompt is self-contained: it carries every diagnostic the fixer needs.
"""

from datetime import datetime

MODEL_ORDER = ("haiku", "sonnet", "opus")

# Label substrings whose repairs touch multi-file logic / claude -p spawn loops.
OPUS_LABEL_HINTS = ("watchdog", "self-repair", "pipeline", "council", "jimbo", "agent")


def _is_rogue(f):
    return f.get("pid") is not None and not f.get("label")


def recommend_model(finding):
    """haiku = trivial toggle/kill, sonnet = log/root-cause, opus = multi-file logic."""
    if _is_rogue(finding):
        return "haiku"  # a kill is a one-liner

    reasons = " ".join(finding.get("reasons", [])).lower()
    label = (finding.get("label") or "").lower()

    if finding.get("parse_error"):
        return "sonnet"  # malformed plist, single-file fix

    # If the declared intent is "off", the fix is just a bootout - cheapest model,
    # regardless of any exit code (we're disabling it, not debugging it).
    if finding.get("expected_state") == "disabled":
        return "haiku"

    # Pure dead-spend / EOL cleanup with no failure to debug -> cheapest model.
    toggle_only = all(
        any(k in r for k in ("declared disabled", "past eol", "no usage.db activity"))
        for r in finding.get("reasons", [])
    ) and finding.get("reasons")
    if toggle_only:
        return "haiku"

    # Anything touching watchdog/pipeline/agent logic -> opus.
    if any(h in label for h in OPUS_LABEL_HINTS) and "last exit" in reasons:
        return "opus"

    # Exit-code root cause on a single script -> sonnet.
    if "last exit" in reasons:
        return "sonnet"

    return "sonnet"


def _finding_block(f):
    model = recommend_model(f)
    if _is_rogue(f):
        lines = [
            f"### ROGUE process (pid {f['pid']})  -  recommended model: **{model}**",
            f"- command: `{f.get('command', '')}`",
            f"- cpu: {f.get('cpu', 0):.0f}%  mem: {f.get('mem', 0):.0f}%  uptime: {f.get('etime', '?')}",
            f"- why flagged: {'; '.join(f.get('reasons', []))}",
            f"- suggested command (confirm first): `{f.get('remediation', '')}`",
        ]
        return model, "\n".join(lines)

    lines = [
        f"### {f['label']}  -  {f.get('bucket', '?')}  -  recommended model: **{model}**",
        f"- purpose: {f.get('purpose') or 'UNDECLARED'}",
        f"- state: {'loaded' if f.get('loaded') else 'not loaded'}"
        f"  |  schedule: {f.get('schedule', '?')}"
        f"  |  last exit: {f.get('last_exit')}",
        f"- expected_state: {f.get('expected_state')}  |  cost_tier: {f.get('cost_tier')}",
        f"- 30d cost: ${f.get('cost_30d', 0):.2f}"
        + ("  (mixed dir, not attributable)" if f.get("cost_mixed") else ""),
        f"- working_dir: {f.get('working_dir') or '-'}",
        f"- log: {f.get('stdout_path') or '-'}",
        f"- why flagged: {'; '.join(f.get('reasons', [])) or '-'}",
        f"- suggested command (confirm first): "
        f"`{f.get('remediation') or 'investigate; no auto-command'}`",
    ]
    return model, "\n".join(lines)


def build_prompt(findings, generated_at=None):
    """Return a markdown repair-request prompt for the selected findings."""
    if not findings:
        return "_No findings selected._"

    blocks = [_finding_block(f) for f in findings]
    by_model = {m: 0 for m in MODEL_ORDER}
    for model, _ in blocks:
        by_model[model] = by_model.get(model, 0) + 1

    ts = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
    summary = ", ".join(
        f"{by_model[m]} {m}" for m in MODEL_ORDER if by_model.get(m)
    )

    header = [
        f"# launchd daemon repair request  ({ts})",
        "",
        "You are auditing and repairing launchd agents on macOS (Apple Silicon). "
        "Diagnostics for each selected item are below. These are **report-only** "
        "findings - do NOT run any destructive command (`launchctl bootout`, "
        "`disable`, `kill`) until you confirm the action with Andrew.",
        "",
        f"**Batch by cost - run cheapest first:** {summary}.",
        "Do all `haiku` items first (trivial toggles/kills), then `sonnet` "
        "(log triage / single-script root cause), then `opus` (multi-file logic, "
        "watchdog/pipeline/claude -p spawn repairs).",
        "",
        "For each item: confirm the diagnosis from the log path, fix the root cause "
        "(not just the symptom), then state the exact command for Andrew to run.",
        "",
        "---",
        "",
    ]
    body = "\n\n".join(b for _, b in blocks)
    return "\n".join(header) + body + "\n"
