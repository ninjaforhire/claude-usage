// Copies the Python sources from the repo root into vscode-extension/python/
// so they're bundled into the .vsix. Each release of the extension embeds the
// exact runtime dependency set from the commit it was packaged at, so end
// users get a self-contained install — their only dependency is Python 3.9+
// on PATH.
//
// Run from vscode-extension/. Invoked automatically by `vscode:prepublish`.

const fs = require("node:fs");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..", "..");
const targetDir = path.resolve(__dirname, "..", "python");
const files = [
  "account_profiles.py",
  "accounts.py",
  "attribution.py",
  "classify.py",
  "cli.py",
  "codex_limits.py",
  "codex_scanner.py",
  "connectors/__init__.py",
  "connectors/claude_subscription.py",
  "connectors/codex_subscription.py",
  "daemon_page.py",
  "daemons.py",
  "dashboard.py",
  "freshness_watch.py",
  "notify.py",
  "processes.py",
  "profile_cli.py",
  "promptgen.py",
  "receipts.py",
  "registry.py",
  "scanner.py",
  "seed_manifest.py",
  "views.py",
  "assets/hfo-icon.png",
  "vendor/chart.umd.min.js",
];

// Start from a clean generated bundle so stale modules and local __pycache__
// files cannot leak into a packaged extension.
fs.rmSync(targetDir, { recursive: true, force: true });
fs.mkdirSync(targetDir, { recursive: true });

let missing = false;
for (const file of files) {
  const src = path.join(repoRoot, file);
  if (!fs.existsSync(src)) {
    console.error(`copy-python: ERROR - missing source ${src}`);
    missing = true;
    continue;
  }
  const dst = path.join(targetDir, file);
  fs.mkdirSync(path.dirname(dst), { recursive: true });
  fs.copyFileSync(src, dst);
  console.log(`copy-python: ${file} -> python/${file}`);
}

if (missing) {
  console.error("copy-python: aborting — run from the vscode-extension/ subdirectory of the claude-usage repo.");
  process.exit(1);
}
