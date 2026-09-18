import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

test("Juridiq mutation utilities use the documented routes and payloads", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "juridiq-client-"));
  const previous = { ...process.env };
  const originalFetch = globalThis.fetch;
  process.env.TEST_JURIDIQ_EMAIL = "client-test@example.invalid";
  process.env.TEST_JURIDIQ_PASSWORD = "test-only";
  process.env.TEST_JURIDIQ_SESSION_FILE = path.join(directory, "session.json");
  process.env.TEST_JURIDIQ_API_BASE_URL = "https://client.example.invalid";
  process.env.TEST_JURIDIQ_LOGIN_URL = "https://client.example.invalid/auth/login";
  process.env.JURIDIQ_ALLOW_WRITES = "true";
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    const parsed = new URL(url);
    requests.push({ path: parsed.pathname, method: options.method ?? "GET", body: options.body && JSON.parse(options.body) });
    if (parsed.pathname === "/auth/login") return Response.json({ token: "token" });
    return Response.json({ id: "task-1", message: "ok" });
  };
  try {
    const client = await import("../integrations/juridiq/client.mjs?client-test");
    await client.createLawSuit({ processNumber: "123" }, { account: "test" });
    await client.updateLawSuit("law-1", { court: "TRF1" }, { account: "test" });
    await client.deleteLawSuit("law-1", { account: "test" });
    await client.updateTask("task-1", { description: "updated" }, { account: "test" });
    await client.deleteTask("task-1", { account: "test" });
    await client.createEvent({ title: "Test", initialHour: "10:00", finalHour: "11:00", date: "2026-09-08", responsibleIds: [] }, { account: "test" });
    await client.markPublicationsRead(["pub-1"], { account: "test" });
    assert.deepEqual(requests.slice(1), [
      { path: "/lawSuit/", method: "POST", body: { processNumber: "123" } },
      { path: "/lawSuit/law-1", method: "PATCH", body: { court: "TRF1" } },
      { path: "/lawSuit/", method: "DELETE", body: { id: ["law-1"] } },
      { path: "/task/task-1", method: "PATCH", body: { description: "updated" } },
      { path: "/task/", method: "DELETE", body: { id: ["task-1"] } },
      { path: "/event/", method: "POST", body: { title: "Test", initialHour: "10:00", finalHour: "11:00", date: "2026-09-08", responsibleIds: [] } },
      { path: "/publication/read-movements-many", method: "PATCH", body: { ids: ["pub-1"] } },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
    for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
    Object.assign(process.env, previous);
    await rm(directory, { recursive: true, force: true });
  }
});

test("Juridiq API errors preserve the response details", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "juridiq-client-error-"));
  const previous = { ...process.env };
  const originalFetch = globalThis.fetch;
  process.env.JURIDIQ_EMAIL = "client-error@example.invalid";
  process.env.JURIDIQ_PASSWORD = "read-only";
  process.env.JURIDIQ_SESSION_FILE = path.join(directory, "session.json");
  process.env.JURIDIQ_API_BASE_URL = "https://client-error.example.invalid";
  process.env.JURIDIQ_LOGIN_URL = "https://client-error.example.invalid/auth/login";
  process.env.JURIDIQ_ALLOW_WRITES = "true";
  globalThis.fetch = async (url) => String(url).endsWith("/auth/login")
    ? Response.json({ token: "token" })
    : Response.json({ message: "Campo inválido", statusCode: 400, translateMessage: "Verifique os dados" }, { status: 400 });
  try {
    const client = await import("../integrations/juridiq/client.mjs?client-error-test");
    await assert.rejects(client.createLawSuit({ processNumber: "123" }, { account: "operational" }), /Campo inválido.*Verifique os dados/);
  } finally {
    globalThis.fetch = originalFetch;
    for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
    Object.assign(process.env, previous);
    await rm(directory, { recursive: true, force: true });
  }
});

test("Juridiq read utilities preserve documented filters and routes", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "juridiq-client-read-"));
  const previous = { ...process.env };
  const originalFetch = globalThis.fetch;
  process.env.JURIDIQ_EMAIL = "client-read@example.invalid";
  process.env.JURIDIQ_PASSWORD = "read-only";
  process.env.JURIDIQ_SESSION_FILE = path.join(directory, "session.json");
  process.env.JURIDIQ_API_BASE_URL = "https://client-read.example.invalid";
  process.env.JURIDIQ_LOGIN_URL = "https://client-read.example.invalid/auth/login";
  const requests = [];
  globalThis.fetch = async (url, options = {}) => {
    const parsed = new URL(url);
    requests.push(parsed);
    if (parsed.pathname === "/auth/login") return Response.json({ token: "token" });
    if (parsed.pathname === "/publication") return Response.json({ data: [], sum: { total: 0, read: 0, unread: 0 } });
    if (parsed.pathname === "/lawSuit/movements/law-1") return Response.json([]);
    if (parsed.pathname === "/publication/pub-1") return Response.json({ id: "pub-1", content: "publication" });
    if (parsed.pathname === "/event/") return Response.json({ data: [], totalResults: 0, totalPages: 0 });
    if (parsed.pathname === "/event/event-1") return Response.json({ id: "event-1", title: "Event" });
    if (parsed.pathname === "/task/") return Response.json({ data: [], totalResults: 0, totalPages: 0 });
    if (parsed.pathname === "/history/task-1") return Response.json({ history: [] });
    if (parsed.pathname === "/comment/") return Response.json({ data: [] });
    throw new Error(`unexpected ${parsed.pathname}`);
  };
  try {
    const client = await import("../integrations/juridiq/client.mjs?client-read-test");
    await client.listPublicationPage({ page: 2, limit: 10, unreadOnly: false, isHandled: true, keySearch: "CNJ", start: "2026-09-01", end: "2026-09-09" });
    await client.listLawSuitMovements("law-1", { content: "intimação", instance: 1 });
    await client.getPublication("pub-1");
    await client.listEvents({ lawSuitId: "law-1", statusFilter: "pending", itemType: "events", start: "2026-09-01", end: "2026-09-09" });
    await client.getEvent("event-1");
    await client.listTasks({ lawSuitId: "law-1", isArchived: false, dateMethod: "finalDate", tag: ["tag-1", "tag-2"] });
    await client.getTaskHistory("task-1");
    await client.listTaskComments("task-1");
    assert.deepEqual(requests.slice(1).map((request) => `${request.pathname}${request.search}`), [
      "/publication?page=2&limit=10&keySearch=CNJ&isHandled=true&start=2026-09-01&end=2026-09-09",
      "/lawSuit/movements/law-1?content=intima%C3%A7%C3%A3o&instance=1",
      "/publication/pub-1",
      "/event/?page=1&limit=50&lawSuitId=law-1&start=2026-09-01&end=2026-09-09&statusFilter=pending&itemType=events",
      "/event/event-1",
      "/task/?page=1&limit=30&isArchived=false&tag=tag-1%2Ctag-2&dateMethod=finalDate&lawSuit=law-1",
      "/history/task-1",
      "/comment/?taskId=task-1",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
    for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
    Object.assign(process.env, previous);
    await rm(directory, { recursive: true, force: true });
  }
});
