import { chmod, mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";

const LOGIN_URL = process.env.JURIDIQ_LOGIN_URL ?? "https://www.api.juridiq.com.br/auth/login";
const API_BASE_URL = process.env.JURIDIQ_API_BASE_URL ?? "https://api.juridiq.com.br";
const ACCOUNT_EMAIL = (process.env.JURIDIQ_EMAIL ?? "").trim().toLowerCase();
const SESSION_NAME = ACCOUNT_EMAIL
  ? `bearer-${createHash("sha256").update(ACCOUNT_EMAIL).digest("hex").slice(0, 16)}.json`
  : "bearer-session.json";
const SESSION_FILE =
  process.env.JURIDIQ_SESSION_FILE ??
  path.join(process.env.LOCALAPPDATA ?? process.cwd(), "Juridiq", SESSION_NAME);

function accountConfig(account = "operational") {
  if (account === "operational") {
    return {
      apiBaseUrl: process.env.JURIDIQ_API_BASE_URL ?? API_BASE_URL,
      loginUrl: process.env.JURIDIQ_LOGIN_URL ?? LOGIN_URL,
      email: process.env.JURIDIQ_EMAIL,
      password: process.env.JURIDIQ_PASSWORD,
      sessionFile: process.env.JURIDIQ_SESSION_FILE ?? SESSION_FILE,
    };
  }
  if (account === "test") {
    const email = (process.env.TEST_JURIDIQ_EMAIL ?? "").trim().toLowerCase();
    return {
      apiBaseUrl: process.env.TEST_JURIDIQ_API_BASE_URL ?? process.env.JURIDIQ_API_BASE_URL ?? API_BASE_URL,
      loginUrl: process.env.TEST_JURIDIQ_LOGIN_URL ?? process.env.JURIDIQ_LOGIN_URL ?? LOGIN_URL,
      email,
      password: process.env.TEST_JURIDIQ_PASSWORD,
      sessionFile: process.env.TEST_JURIDIQ_SESSION_FILE ?? path.join(
        process.env.LOCALAPPDATA ?? process.cwd(), "Juridiq", email
          ? `bearer-test-${createHash("sha256").update(email).digest("hex").slice(0, 16)}.json`
          : "bearer-test-session.json",
      ),
    };
  }
  throw new Error("Juridiq account must be operational or test.");
}

function jwtExpiry(token) {
  try {
    const payload = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString("utf8"));
    return Number.isFinite(payload.exp) ? new Date(payload.exp * 1000).toISOString() : null;
  } catch {
    return null;
  }
}

function isFresh(session, skewSeconds = 60) {
  if (!session?.token) return false;
  if (!session.expiresAt) return true;
  return Date.parse(session.expiresAt) - Date.now() > skewSeconds * 1000;
}

async function readCachedSession(sessionFile) {
  try {
    return JSON.parse(await readFile(sessionFile, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

async function saveSession(token, config) {
  await mkdir(path.dirname(config.sessionFile), { recursive: true });
  await writeFile(
    config.sessionFile,
    `${JSON.stringify({ token, baseUrl: config.apiBaseUrl, expiresAt: jwtExpiry(token), updatedAt: new Date().toISOString() }, null, 2)}\n`,
    "utf8",
  );
  try {
    await chmod(config.sessionFile, 0o600);
  } catch {
    // Windows ACLs are managed by the parent directory when chmod is unavailable.
  }
}

async function login({ email, password, account = "operational" } = {}) {
  const config = accountConfig(account);
  const loginEmail = email ?? config.email;
  const loginPassword = password ?? config.password;
  if (!loginEmail || !loginPassword) {
    throw new Error(account === "test"
      ? "Set TEST_JURIDIQ_EMAIL/TEST_JURIDIQ_PASSWORD for test-account writes."
      : "Set JURIDIQ_EMAIL/JURIDIQ_PASSWORD.");
  }

  const response = await fetch(config.loginUrl, {
    method: "POST",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify({ email: loginEmail, password: loginPassword }),
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    // Preserve the HTTP error below when the server returns non-JSON.
  }
  if (!response.ok || typeof payload.token !== "string") {
    const error = new Error(payload.message ?? `Juridiq login failed (${response.status}).`);
    error.status = response.status;
    error.retryAfter = response.headers.get("retry-after");
    throw error;
  }
  await saveSession(payload.token, config);
  return payload.token;
}

export async function getJuridiqToken({ forceRefresh = false, email, password, account = "operational" } = {}) {
  const config = accountConfig(account);
  const cached = await readCachedSession(config.sessionFile);
  if (!forceRefresh && isFresh(cached)) return cached.token;
  return login({ email: email ?? config.email, password: password ?? config.password, account });
}

export async function clearJuridiqSession({ account = "operational" } = {}) {
  const sessionFile = accountConfig(account).sessionFile;
  try {
    await unlink(sessionFile);
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

export async function juridiqRequest(pathname, options = {}) {
  const { account = "operational", ...requestOptions } = options;
  const config = accountConfig(account);
  const url = new URL(pathname, config.apiBaseUrl);
  if (url.origin !== new URL(config.apiBaseUrl).origin || url.username || url.password) {
    throw new Error("Juridiq requests must use the configured API origin.");
  }
  const method = (options.method ?? "GET").toUpperCase();
  // generic-tables is a read query despite using POST.
  const readQuery = method === "POST" && url.pathname === "/generic-tables";
  if (!["GET", "HEAD"].includes(method) && !readQuery && process.env.JURIDIQ_ALLOW_WRITES !== "true") {
    throw new Error("Juridiq writes are disabled. Set JURIDIQ_ALLOW_WRITES=true after confirming the selected account and operation.");
  }
  let token = await getJuridiqToken({ account });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const headers = new Headers(requestOptions.headers);
    headers.set("accept", headers.get("accept") ?? "application/json");
    headers.set("authorization", `Bearer ${token}`);
    const response = await fetch(url, { ...requestOptions, headers, redirect: "error" });
    if (response.status !== 401 || attempt === 1) return response;
    await clearJuridiqSession({ account });
    token = await getJuridiqToken({ forceRefresh: true, account });
  }
  throw new Error("Unreachable");
}

export { API_BASE_URL, LOGIN_URL, SESSION_FILE };
