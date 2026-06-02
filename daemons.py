"""
daemons.py - Inventory and health of launchd agents under ~/Library/LaunchAgents.

Pure stdlib (plistlib, subprocess) so it runs under the system python3.9 that the
KeepAlive dashboard daemon uses. Reads the git-tracked registry (daemons.json) to
diff actual state against declared expected state.
"""

import plistlib
import re
import subprocess
from pathlib import Path

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def list_plist_files(agents_dir=LAUNCH_AGENTS_DIR):
    """Return (active, disabled) lists of plist Paths.

    Active = *.plist, disabled = *.plist.disabled (the cleanup convention).
    """
    agents_dir = Path(agents_dir)
    if not agents_dir.exists():
        return [], []
    active = sorted(agents_dir.glob("*.plist"))
    disabled = sorted(agents_dir.glob("*.plist.disabled"))
    return active, disabled


def parse_plist(path):
    """Parse a launchd plist into a normalized dict. Never raises on bad files."""
    path = Path(path)
    try:
        with path.open("rb") as fh:
            data = plistlib.load(fh)
    except Exception as exc:  # noqa: BLE001 - report, never crash the scan
        return {"label": path.stem, "parse_error": str(exc), "path": str(path)}

    program_args = data.get("ProgramArguments") or (
        [data["Program"]] if data.get("Program") else []
    )
    return {
        "label": data.get("Label", path.stem),
        "path": str(path),
        "program_args": program_args,
        "working_dir": data.get("WorkingDirectory"),
        "stdout_path": data.get("StandardOutPath"),
        "stderr_path": data.get("StandardErrorPath"),
        "run_at_load": bool(data.get("RunAtLoad")),
        "keep_alive": data.get("KeepAlive"),
        "start_interval": data.get("StartInterval"),
        "start_calendar": data.get("StartCalendarInterval"),
        "parse_error": None,
    }


def schedule_summary(plist):
    """Human-readable cadence string from a parsed plist dict."""
    if plist.get("keep_alive"):
        return "always-on (KeepAlive)"
    iv = plist.get("start_interval")
    if iv:
        if iv % 3600 == 0:
            return f"every {iv // 3600}h"
        if iv % 60 == 0:
            return f"every {iv // 60}m"
        return f"every {iv}s"
    cal = plist.get("start_calendar")
    if cal:
        entries = cal if isinstance(cal, list) else [cal]
        parts = []
        for e in entries:
            h = e.get("Hour")
            m = e.get("Minute", 0)
            wd = e.get("Weekday")
            label = ""
            if h is not None:
                label = f"{h:02d}:{m:02d}"
            if wd is not None:
                label = f"wd{wd} {label}".strip()
            parts.append(label or "calendar")
        return "at " + ", ".join(parts)
    if plist.get("run_at_load"):
        return "at load only"
    return "on-demand"


_LIST_RE = re.compile(r"^(?P<pid>-|\d+)\s+(?P<status>-?\d+)\s+(?P<label>\S+)$")


def launchctl_state():
    """Map label -> {pid, last_exit} from `launchctl list`.

    pid is None when not running; last_exit is the last exit status int.
    """
    out = subprocess.run(
        ["launchctl", "list"], capture_output=True, text=True
    ).stdout
    state = {}
    for line in out.splitlines()[1:]:  # skip header
        m = _LIST_RE.match(line.strip())
        if not m:
            continue
        pid = m.group("pid")
        state[m.group("label")] = {
            "pid": None if pid == "-" else int(pid),
            "last_exit": int(m.group("status")),
        }
    return state


def last_run_epoch(stdout_path):
    """mtime of the daemon's stdout log as a last-run proxy. None if absent."""
    if not stdout_path:
        return None
    p = Path(stdout_path)
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def gather(agents_dir=LAUNCH_AGENTS_DIR):
    """Return a list of merged daemon dicts: plist fields + live launchctl state.

    Each entry: label, loaded(bool), pid, last_exit, schedule, working_dir,
    stdout_path, last_run (epoch|None), disabled_file(bool), parse_error.
    """
    active, disabled = list_plist_files(agents_dir)
    state = launchctl_state()
    result = []
    for path in active:
        pl = parse_plist(path)
        label = pl["label"]
        live = state.get(label)
        result.append(
            {
                **pl,
                "disabled_file": False,
                "loaded": live is not None,
                "pid": live["pid"] if live else None,
                "last_exit": live["last_exit"] if live else None,
                "schedule": schedule_summary(pl),
                "last_run": last_run_epoch(pl.get("stdout_path")),
            }
        )
    for path in disabled:
        pl = parse_plist(path)
        result.append(
            {
                **pl,
                "disabled_file": True,
                "loaded": False,
                "pid": None,
                "last_exit": None,
                "schedule": schedule_summary(pl),
                "last_run": last_run_epoch(pl.get("stdout_path")),
            }
        )
    return result
