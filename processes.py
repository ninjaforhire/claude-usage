"""
processes.py - Live process snapshot + rogue detection.

Rogue = a long-lived process that no plist explains and whose parent is not an
interactive shell. Interactive `claude` CLIs (parent -zsh) and Claude.app helpers
are explicitly whitelisted so they never get flagged.
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
        reasons = []
        if not _is_interactive_claude(p, by_pid):
            reasons.append("claude process not attached to an interactive shell")
        elif p["cpu"] >= cpu_threshold:
            reasons.append(f"high CPU ({p['cpu']:.0f}%)")
        if reasons:
            rogues.append({**p, "reasons": reasons, "remediation": f"kill {p['pid']}"})
    return rogues
