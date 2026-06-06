import * as net from "node:net";

/**
 * Pick a free TCP port the OS hands us.
 *
 * We bind a server to port 0 on the given host, read back the OS-assigned
 * port, close the server, and return. Brief race window exists between close
 * and the server-manager's spawn, but that's the standard pattern and the
 * server-manager will retry on EADDRINUSE.
 */
export function pickFreePort(host = "127.0.0.1"): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, host, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("could not read assigned port"));
        return;
      }
      const port = address.port;
      server.close(() => resolve(port));
    });
  });
}

/**
 * Validate a configured port value. 0 means auto-pick.
 * Anything outside 1024-65535 is treated as "auto" rather than failing.
 */
export function normalizeConfiguredPort(configured: number | undefined | null): number {
  if (typeof configured !== "number" || !Number.isFinite(configured)) return 0;
  if (configured === 0) return 0;
  if (configured < 1024 || configured > 65535) return 0;
  return Math.floor(configured);
}

/**
 * Resolve a port: if configured is non-zero use it; otherwise ask the OS.
 */
export async function resolvePort(configured: number | undefined | null, host = "127.0.0.1"): Promise<number> {
  const c = normalizeConfiguredPort(configured);
  if (c !== 0) return c;
  return pickFreePort(host);
}
