"""Tests for processes.find_rogues - managed-claude whitelisting.

Regression guard for 2026-07-08: the daemon audit flagged Claude Code's own
background daemon (`claude daemon run`) and a live Jimbo mission worker
(`claude -p ... jimbo-mcp.json`) as rogue and recommended `kill`. Both are
headless by design and must never be flagged.
"""

import unittest

import processes


def _proc(pid, ppid, command, cpu=0.0, mem=0.0, etime="01:00"):
    return {
        "pid": pid,
        "ppid": ppid,
        "cpu": cpu,
        "mem": mem,
        "etime": etime,
        "command": command,
    }


CLAUDE = "/Users/u/.local/bin/claude"


class TestFindRogues(unittest.TestCase):
    def _pids(self, procs):
        return {r["pid"] for r in processes.find_rogues(procs)}

    def test_claude_daemon_supervisor_not_rogue(self):
        # Reparented to launchd (ppid==1), detached by design.
        procs = [
            _proc(
                18877,
                1,
                f'{CLAUDE} daemon run --json-path /Users/u/.claude/daemon.json '
                '--log-file /Users/u/.claude/daemon.log --origin transient '
                '--spawned-by {"label":"claude","cwd":"/Users/u/_Code/mighty/'
                'agents/tools/jimbo","pid":80727}',
            )
        ]
        self.assertNotIn(18877, self._pids(procs))

    def test_jimbo_mission_worker_not_rogue_by_command(self):
        # --mcp-config path alone marks it managed even if the parent is gone.
        procs = [
            _proc(
                25242,
                1,
                f"{CLAUDE} -p --model sonnet --effort high "
                "--dangerously-skip-permissions --max-turns 30 "
                "--output-format stream-json --verbose --mcp-config "
                "/Users/u/_Code/mighty/agents/tools/jimbo/jimbo-mcp.json",
            )
        ]
        self.assertNotIn(25242, self._pids(procs))

    def test_jimbo_mission_worker_not_rogue_by_parent(self):
        # A plain `claude -p` child is managed because its parent is run_mission.py.
        runner = _proc(
            25190,
            1,
            "/opt/python3.14 /Users/u/_Code/mighty/agents/tools/jimbo/cron/"
            "run_mission.py --mission smarter_not_harder",
        )
        worker = _proc(25242, 25190, f"{CLAUDE} -p --model sonnet --max-turns 30")
        self.assertNotIn(25242, self._pids([runner, worker]))

    def test_interactive_claude_not_rogue(self):
        shell = _proc(80686, 500, "-zsh")
        session = _proc(80727, 80686, f"{CLAUDE}")
        self.assertNotIn(80727, self._pids([shell, session]))

    def test_genuine_orphan_still_rogue(self):
        # `claude -p` with no jimbo lineage and a dead launcher IS a real rogue.
        procs = [_proc(99999, 1, f"{CLAUDE} -p --model sonnet --max-turns 30")]
        pids = self._pids(procs)
        self.assertIn(99999, pids)

    def test_orphan_rogue_recommends_kill(self):
        procs = [_proc(99999, 1, f"{CLAUDE} -p --max-turns 30")]
        rogue = processes.find_rogues(procs)[0]
        self.assertEqual(rogue["remediation"], "kill 99999")

    def test_parent_walk_is_bounded_on_cycle(self):
        # ppid cycle must not hang the walk.
        a = _proc(2, 3, f"{CLAUDE} -p")
        b = _proc(3, 2, "python worker.py")
        # No jimbo ancestor -> still rogue, and it returns (no infinite loop).
        self.assertIn(2, self._pids([a, b]))


if __name__ == "__main__":
    unittest.main()
