"""
processes.py - Live process snapshot + rogue detection.

Rogue = a long-lived process that no plist explains and whose parent is not an
interactive shell. Interactive `claude` CLIs (parent -zsh) and Claude.app helpers
are explicitly whitelisted so they never get flagged.

Two classes of `claude` run headless BY DESIGN and must also never be flagged:
  * Claude Code's own background daemon supervisor (`claude daemon run ...`) -
    auth refresh + bg workers. It double-forks and reparents to launchd, so the
    interactive-shell heuristic always misfires on it.
  * Jimbo mission workers (`claude -p ... --mcp-config .../jimbo-mcp.json`),
    spawned by run_mission.py / the jimbo server and bounded by --max-turns.
See `_is_managed_claude`.
"""

import subprocess

DEFAULT_CPU_THRESHOLD = 50.0  # percent; runaway flag for non-interactive procs

# Substrings that mark a process as a legit interactive/desktop app, never rogue.
WHITELIST_SUBSTR = (
    "/Claude.app/",
    "Claude Helper",
    "chrome_crashpad_handler",
    "/Applications/",
)


def snapshot():
    """Return list of {pid, ppid, cpu, mem, etime, command} for all processes."""
    out = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pcpu=,pmem=,etime=,command="],
        capture_output=True,
        text=True,
    ).stdout
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid, ppid, cpu, mem, etime, command = parts
        try:
            procs.append(
                {
                    "pid": int(pid),
                    "ppid": int(ppid),
                    "cpu": float(cpu),
                    "mem": float(mem),
                    "etime": etime,
                    "command": command,
                }
            )
        except ValueError:
            continue
    return procs


def _is_interactive_claude(proc, by_pid):
    """A `claude` CLI launched from an interactive shell (the user's terminals)."""
    cmd = proc["command"]
    if "/.local/bin/claude" not in cmd and not cmd.endswith("/claude"):
        return False
    parent = by_pid.get(proc["ppid"], {})
    return "zsh" in parent.get("command", "") or "bash" in parent.get("command", "")


def _is_whitelisted(proc):
    return any(s in proc["command"] for s in WHITELIST_SUBSTR)


# Substrings on a managed claude's own command line (checked first, cheap).
_MANAGED_CLAUDE_SUBSTR = (
    " daemon run",     # Claude Code's built-in background daemon supervisor
    "jimbo-mcp.json",  # Jimbo mission worker (its --mcp-config path)
    "/jimbo",          # Jimbo-spawned (mcp path or spawned-by cwd)
)

# Ancestor command substrings that mark a claude as Jimbo-managed (parent walk).
_MANAGED_PARENT_SUBSTR = (
    "run_mission.py",  # standalone / crontab mission runner
    "run_server.py",   # jimbo server that spawns mission workers
    "/jimbo/",         # any jimbo host process
)


def _is_managed_claude(proc, by_pid):
    """A headless `claude` that runs by design - never rogue.

    Covers Claude Code's own daemon supervisor and Jimbo mission workers, both
    of which are correctly detached from any interactive shell. Detected either
    by the process's own command line or by walking its parent chain up to a
    known Jimbo runner (the mission child's parent is the mission process, which
    is itself reparented to launchd once the standalone runner exits).
    """
    cmd = proc["command"]
    if any(s in cmd for s in _MANAGED_CLAUDE_SUBSTR):
        return True
    seen = set()
    cur = proc
    for _ in range(6):  # bounded parent walk; guards against pid-reuse cycles
        ppid = cur.get("ppid")
        if ppid is None or ppid in seen:
            break
        seen.add(ppid)
        parent = by_pid.get(ppid)
        if parent is None:
            break
        if any(s in parent.get("command", "") for s in _MANAGED_PARENT_SUBSTR):
            return True
        cur = parent
    return False


def find_rogues(procs=None, cpu_threshold=DEFAULT_CPU_THRESHOLD):
    """Return claude processes that look like runaway/orphaned background work.

    Scoped to `claude` processes only - those are the billing risk and the generic
    high-CPU net flags too many legit daemons (postgres, redis, the REPL itself).

    Heuristics (report-only, conservative):
      - claude CLI NOT launched from an interactive shell (orphaned `claude -p`)
      - interactive claude pegged over the CPU threshold (runaway)
    """
    procs = procs if procs is not None else snapshot()
    by_pid = {p["pid"]: p for p in procs}
    rogues = []
    for p in procs:
        if _is_whitelisted(p):
            continue
        cmd = p["command"]
        is_claude = "/.local/bin/claude" in cmd or cmd.endswith("/claude")
        if not is_claude:
            continue
        if _is_managed_claude(p, by_pid):
            continue  # Claude's own daemon / Jimbo mission worker - headless by design
        reasons = []
        if not _is_interactive_claude(p, by_pid):
            reasons.append("claude process not attached to an interactive shell")
        elif p["cpu"] >= cpu_threshold:
            reasons.append(f"high CPU ({p['cpu']:.0f}%)")
        if reasons:
            rogues.append({**p, "reasons": reasons, "remediation": f"kill {p['pid']}"})
    return rogues
