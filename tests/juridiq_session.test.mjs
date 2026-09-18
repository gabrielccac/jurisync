import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

test("cached auth, one 401 refresh, no 429 login retry, and read-only guard", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "juridiq-offline-test-"));
  const originalFetch = globalThis.fetch;
  const previous = { ...process.env };
  process.env.JURIDIQ_EMAIL = "offline@example.invalid";
  process.env.JURIDIQ_PASSWORD = "offline-only";
  process.env.JURIDIQ_ALLOW_WRITES = "false";
  process.env.JURIDIQ_API_BASE_URL = "https://api.example.invalid";
  process.env.JURIDIQ_LOGIN_URL = "https://api.example.invalid/auth/login";
  process.env.JURIDIQ_SESSION_FILE = path.join(directory, "session.json");
  let logins = 0;
  const statuses = [];
  const authHeaders = [];
  globalThis.fetch = async (url, options) => {
    if (String(url).endsWith("/auth/login")) {
      logins++;
      const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
      return Response.json({ token: `offline.${payload}.test${logins}` });
    }
    authHeaders.push(options.headers.get("authorization"));
    return Response.json({ data: [] }, { status: statuses.shift() ?? 200 });
  };
  try {
    const { juridiqRequest } = await import("../integrations/juridiq/session.mjs");
    await juridiqRequest("/user/me");
    await juridiqRequest("/publication?page=1&limit=10");
    assert.equal(logins, 1);
    assert.equal(authHeaders[0], authHeaders[1]);
    statuses.push(401, 200);
    assert.equal((await juridiqRequest("/user/me")).status, 200);
    assert.equal(logins, 2);
    statuses.push(429);
    assert.equal((await juridiqRequest("/user/me")).status, 429);
    assert.equal(logins, 2);
    await assert.rejects(juridiqRequest("/task", { method: "POST" }), /writes are disabled/);
    await assert.rejects(juridiqRequest("https://other.example.invalid/task"), /configured API origin/);
    await juridiqRequest("/generic-tables", { method: "POST", body: JSON.stringify({ module: "task" }) });
    assert.equal(logins, 2);
    delete process.env.JURIDIQ_SESSION_FILE;
    const accountA = await import("../integrations/juridiq/session.mjs?account=a");
    process.env.JURIDIQ_EMAIL = "second@example.invalid";
    const accountB = await import("../integrations/juridiq/session.mjs?account=b");
    assert.notEqual(accountA.SESSION_FILE, accountB.SESSION_FILE);
  } finally {
    globalThis.fetch = originalFetch;
    for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
    Object.assign(process.env, previous);
    await rm(directory, { recursive: true, force: true });
  }
});
