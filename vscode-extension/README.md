# HotFix Ops Usage — VS Code extension

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE.txt)

**See local Claude and Codex usage, account capacity, sessions, and operations health inside VS Code.**

The extension embeds the same local-only HotFix Ops dashboard shipped by the
Python tool. It reads supported local transcript stores and can query sanitized
status through the user's already-authenticated official provider CLIs. It has no
telemetry.

Works on **API, Pro, and Max plans**. Captures usage from the Claude Code CLI, the official VS Code extension, and dispatched Code sessions.

---

## Install

### From a `.vsix` file

```
git clone https://github.com/ninjaforhire/claude-usage
cd claude-usage/vscode-extension
./scripts/install.sh        # macOS / Linux / WSL
.\scripts\install.ps1       # Windows PowerShell
```

The scripts run `vsce package` then `code --install-extension` against your local VS Code install.

---

## Requirements

- **Python 3.9 or newer on your `PATH`.** Almost everyone running Claude Code already has Python installed; if not, see [python.org/downloads](https://www.python.org/downloads/). On Windows make sure to check **"Add Python to PATH"** during the installer.

That's the only dependency. The Python sources (`cli.py`, `scanner.py`, `dashboard.py`) are bundled inside the extension — no separate clone or Homebrew install needed.

---

## Usage

1. Click the **gauge icon** in the activity bar (left sidebar of VS Code).
2. The extension starts the dashboard server on a free local port and embeds it in a sidebar webview.
3. Filter by model, range, or project — same UI as the standalone web dashboard.

### Commands

Open the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`):

| Command | What it does |
|---|---|
| **HotFix Ops: Open Usage Dashboard** | Reveal the sidebar and start the server |
| **HotFix Ops: Rescan Usage** | Refresh the embedded dashboard |
| **HotFix Ops: Restart Usage Server** | Kill and respawn the local Python process |
| **HotFix Ops: Show Usage Logs** | Open the extension's output channel |

### Settings

| Setting | Default | Description |
|---|---|---|
| `claudeUsage.pythonPath` | _(auto-discover)_ | Path to a Python 3.9+ interpreter. Leave empty to auto-detect (`claude-usage` on PATH first, then `python3`, then `python`). |
| `claudeUsage.cliPath` | _(bundled)_ | Path to a custom `cli.py` (or its parent directory). Empty = use the bundled copy that ships with the extension. |
| `claudeUsage.port` | `0` | Port for the local dashboard server. `0` = OS picks a free one. |

---

## How discovery works

When you click the icon, the extension resolves how to run the dashboard in this order:

1. **`claudeUsage.cliPath` setting** if you've set one
2. **The bundled `python/cli.py`** that ships inside this `.vsix` (most installs hit this)
3. The `claude-usage` shim on `PATH` (if you installed via Homebrew)
4. A `cli.py` in any open VS Code workspace folder (the legacy "open the cloned repo" path)
5. A sibling `cli.py` from the extension dir (dev mode, when running from source via F5)

If none of those find anything, you'll get a friendly message in the sidebar — most often "Python 3.9+ is required" with a platform-specific install hint.

---

## Privacy

The extension only:
- Reads local JSONL transcripts from `~/.claude/projects/` (and the Xcode coding-assistant directory on macOS, if present)
- Runs a small HTTP server bound to `127.0.0.1` (localhost-only — never `0.0.0.0`) on a port the OS picks for you
- Embeds that server's dashboard in a VS Code webview

No prompts, transcript bodies, credentials, or raw provider tokens are sent by
the dashboard. There is no telemetry.

---

## Troubleshooting

- **"Python 3.9 or newer required"** — install from [python.org](https://www.python.org/downloads/) and reload VS Code (`Ctrl+Shift+P` → `Developer: Reload Window`). On Windows make sure "Add Python to PATH" is checked in the installer.
- **Sidebar stays blank or shows "starting…"** — run `HotFix Ops: Show Usage Logs`. The extension logs the resolved Python path, the install mode, the spawn command, and any stdout/stderr from the server.
- **Dashboard renders but shows "No usage recorded"** — Claude Code hasn't written transcripts to `~/.claude/projects/` yet. Run a Claude Code session first.

---

## Source

The Python tool, extension, and Homebrew formula live at
[github.com/ninjaforhire/claude-usage](https://github.com/ninjaforhire/claude-usage).

HotFix Ops Usage is based on the MIT-licensed
[`phuryn/claude-usage`](https://github.com/phuryn/claude-usage) project by
[The Product Compass Newsletter](https://www.productcompass.pm).
