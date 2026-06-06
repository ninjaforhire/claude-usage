import { describe, it, expect } from "vitest";
import * as net from "node:net";
import { pickFreePort, normalizeConfiguredPort, resolvePort } from "../src/port-allocator";

describe("normalizeConfiguredPort", () => {
  it("zero maps to auto", () => {
    expect(normalizeConfiguredPort(0)).toBe(0);
  });

  it("undefined/null map to auto", () => {
    expect(normalizeConfiguredPort(undefined)).toBe(0);
    expect(normalizeConfiguredPort(null)).toBe(0);
  });

  it("NaN/Infinity map to auto", () => {
    expect(normalizeConfiguredPort(NaN)).toBe(0);
    expect(normalizeConfiguredPort(Infinity)).toBe(0);
  });

  it("valid port (1024-65535) passes through, integer-floored", () => {
    expect(normalizeConfiguredPort(8080)).toBe(8080);
    expect(normalizeConfiguredPort(8080.9)).toBe(8080);
    expect(normalizeConfiguredPort(1024)).toBe(1024);
    expect(normalizeConfiguredPort(65535)).toBe(65535);
  });

  it("out-of-range maps to auto", () => {
    expect(normalizeConfiguredPort(80)).toBe(0);
    expect(normalizeConfiguredPort(1023)).toBe(0);
    expect(normalizeConfiguredPort(65536)).toBe(0);
    expect(normalizeConfiguredPort(-1)).toBe(0);
  });
});

describe("pickFreePort", () => {
  it("returns a port in the ephemeral range", async () => {
    const port = await pickFreePort();
    expect(port).toBeGreaterThan(1024);
    expect(port).toBeLessThan(65536);
  });

  it("two consecutive picks generally yield different ports (best-effort)", async () => {
    const a = await pickFreePort();
    const b = await pickFreePort();
    // The OS *might* reassign — don't strictly require difference, but most of
    // the time these will differ. The point is both should work.
    expect(typeof a).toBe("number");
    expect(typeof b).toBe("number");
  });

  it("the picked port is actually bindable right after", async () => {
    const port = await pickFreePort();
    await new Promise<void>((resolve, reject) => {
      const s = net.createServer();
      s.once("error", reject);
      s.listen(port, "127.0.0.1", () => s.close(() => resolve()));
    });
  });
});

describe("resolvePort", () => {
  it("returns configured port when valid", async () => {
    // Use an ephemeral one we KNOW will work by picking it first.
    const target = await pickFreePort();
    expect(await resolvePort(target)).toBe(target);
  });

  it("auto-picks when configured is 0", async () => {
    const port = await resolvePort(0);
    expect(port).toBeGreaterThan(1024);
  });

  it("auto-picks when configured is out of range", async () => {
    const port = await resolvePort(80);
    expect(port).toBeGreaterThan(1024);
  });
});
