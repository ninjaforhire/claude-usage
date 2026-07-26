import * as vscode from "vscode";
import { randomBytes } from "node:crypto";
import { ServerManager } from "./server-manager";

/**
 * Webview HTML that embeds the running Python dashboard via an iframe.
 *
 * Two states:
 *   - When `url` is set we render the iframe at that URL.
 *   - When `url` is null we render a status / error pane (with the latest
 *     status text the host pushed via setStatus).
 *
 * We rely on VS Code's webview Content-Security-Policy. Allowing the
 * dashboard's localhost origin via `frame-src http://127.0.0.1:* http://localhost:*`
 * is enough; the dashboard ships its own CSP for what it loads inside.
 *
 * The iframe sandbox includes `allow-downloads` so the dashboard's CSV export
 * (a Blob + `a.download` click) works inside the webview — without it Chromium
 * silently blocks the download.
 */
export function renderHtml(
  url: string | null,
  statusText: string,
  nonce: string,
  iconUri = "",
  cspSource = "",
): string {
  if (url) {
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; frame-src http://127.0.0.1:* http://localhost:*; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<title>HotFix Ops Usage</title>
<style>
  html, body { margin: 0; padding: 0; height: 100%; background: #10161d; }
  iframe { border: 0; width: 100%; height: 100vh; display: block; }
</style>
</head>
<body>
<iframe src="${escapeHtml(url)}" sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"></iframe>
</body>
</html>`;
  }

  // Status / loading pane — styled to match the dashboard header (same icon,
  // title, and elevated-palette colors) so the cold-start screen doesn't jar.
  const imgSrc = cspSource ? ` img-src ${cspSource};` : "";
  const logo = iconUri ? `<img class="logo" src="${escapeHtml(iconUri)}" alt="">` : "";
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none';${imgSrc} style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
<title>HotFix Ops Usage</title>
<style>
  body { font-family: 'Avenir Next', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #f5f5f7; background: #10161d; padding: 24px; line-height: 1.5; }
  .brand { display: flex; align-items: center; gap: 10px; margin: 0 0 18px; }
  .brand .logo { width: 34px; height: 34px; flex-shrink: 0; object-fit: contain; filter: drop-shadow(0 0 9px rgba(232,97,27,.28)); }
  .brand h1 { font-size: 18px; font-weight: 700; color: #f5f5f7; letter-spacing: .06em; margin: 0; text-transform: uppercase; }
  p { color: #f5f5f7; font-size: 13px; margin: 0 0 8px; }
  p.hint { color: #9aa8b6; }
  code { background: #18212b; border: 1px solid #3a4755; border-radius: 4px; color: #e8611b; padding: 1px 5px; font-size: 12px; }
</style>
</head>
<body>
<div class="brand">${logo}<h1>HotFix Ops Usage</h1></div>
<p>${escapeHtml(statusText) || "The dashboard server is not running yet."}</p>
<p class="hint">Run <code>HotFix Ops: Open Usage Dashboard</code> from the command palette to start it.</p>
</body>
</html>`;
}

/**
 * Escape HTML for safe interpolation into the templates above.
 * Exported for testability.
 */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Generate a one-shot nonce for the CSP script-src directive.
 * 24 bytes of crypto-random output, base64url-encoded (URL-safe, no
 * padding, always 32 chars).
 */
export function makeNonce(): string {
  return randomBytes(24).toString("base64url");
}

export class DashboardSidebar implements vscode.WebviewViewProvider {
  public static readonly viewId = "claudeUsage.dashboard";

  private view: vscode.WebviewView | undefined;
  private currentUrl: string | null = null;
  private statusText = "";
  private readonly onShow: () => void;
  private readonly extensionUri: vscode.Uri | undefined;
  private iconUri = "";
  private cspSource = "";

  constructor(onShow: () => void = () => {}, extensionUri?: vscode.Uri) {
    this.onShow = onShow;
    this.extensionUri = extensionUri;
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    // Resolve a webview-safe URI for the bundled icon so the status pane shows
    // the same logo as the dashboard header. Guarded so node-only tests (whose
    // fake view has no asWebviewUri / no vscode.Uri) don't blow up.
    if (this.extensionUri && typeof view.webview.asWebviewUri === "function") {
      this.iconUri = view.webview
        .asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "resources", "icon.png"))
        .toString();
      this.cspSource = view.webview.cspSource ?? "";
    }
    this.render();
    view.onDidDispose(() => {
      this.view = undefined;
    });
    // Kick the host to start the server now that the user has revealed the
    // panel. extension.ts wires this to openDashboard(); the in-flight
    // coalescing on that side means clicking the icon repeatedly is safe.
    this.onShow();
  }

  /** Called from extension.ts after the server is ready. */
  setUrl(url: string | null): void {
    this.currentUrl = url;
    this.render();
  }

  setStatus(text: string): void {
    this.statusText = text;
    this.render();
  }

  /** Force the iframe to reload (e.g. after a rescan). */
  refresh(): void {
    if (!this.view) return;
    // Re-render same URL — the iframe will reload because the HTML is regenerated.
    this.render();
  }

  private render(): void {
    if (!this.view) return;
    this.view.webview.html = renderHtml(this.currentUrl, this.statusText, makeNonce(), this.iconUri, this.cspSource);
  }
}

// Note: in extension.ts we connect ServerManager to DashboardSidebar via:
//   server.start().then(() => sidebar.setUrl(`http://${host}:${port}/`))
// Importing ServerManager here only so the type stays in the module graph; not
// used at runtime. Stripped on build.
export type { ServerManager };
