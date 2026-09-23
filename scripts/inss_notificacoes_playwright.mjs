#!/usr/bin/env node

import { parseArgs } from "node:util";
import { DEFAULT_PROFILE, loadEnvFile, loginAndAuthorize, qrSecret } from "./inss_gerid_playwright.mjs";

const NOTIFICATIONS_PATH = "/apis/requerimentosPortalApi/requerimento/ec/notificacao?first=0";
const RECAPTCHA_SITE_KEY = "6LfA-t8oAAAAAAMYMMHDthsSTek1eOYMwyjJcvoK";
const RECAPTCHA_ACTION = "buscaRequerimentos";

function args() {
  return parseArgs({
    options: {
      profile: { type: "string" },
      "profile-directory": { type: "string" },
      "qr-image": { type: "string" },
      account: { type: "string", default: "1" },
      headless: { type: "boolean", default: false },
      timeout: { type: "string", default: "120" },
      direct: { type: "boolean", default: false },
      "show-secrets": { type: "boolean", default: false },
    },
    strict: true,
  }).values;
}

async function recaptchaToken(page, timeout) {
  const siteKey = process.env.INSS_RECAPTCHA_SITE_KEY ?? RECAPTCHA_SITE_KEY;
  const action = process.env.INSS_RECAPTCHA_ACTION ?? RECAPTCHA_ACTION;
  await page.waitForFunction(() => {
    const api = window.grecaptcha?.enterprise ?? window.grecaptcha;
    return typeof api?.execute === "function";
  }, null, { timeout });
  return page.evaluate(async ({ siteKey, action, timeout }) => {
    const api = window.grecaptcha?.enterprise ?? window.grecaptcha;
    const value = await Promise.race([
      api.execute(siteKey, { action }),
      new Promise((_, reject) => setTimeout(() => reject(new Error("reCAPTCHA timeout")), timeout)),
    ]);
    if (typeof value !== "string" || !value) throw new Error("reCAPTCHA returned no token");
    return value;
  }, { siteKey, action, timeout });
}

async function directNotificationRequest(page, timeout, includePayload) {
  const token = await recaptchaToken(page, timeout);
  return page.evaluate(async ({ includePayload, path, token }) => {
    const auth = localStorage.getItem("ifs_auth") ?? localStorage.getItem("gerid_token");
    if (!auth) throw new Error("ifs_auth and gerid_token are missing from localStorage");
    const response = await fetch(path, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json", Authorization: `Bearer ${auth}`, "Content-Type": "application/json" },
      body: JSON.stringify({ tokenReCAPTCHA: token }),
    });
    const payload = await response.json().catch(() => null);
    return {
      method: "POST",
      status: response.status,
      contentType: response.headers.get("content-type") ?? "",
      topLevelKeys: payload && typeof payload === "object" && !Array.isArray(payload) ? Object.keys(payload).sort() : [],
      notificationCount: Array.isArray(payload) ? payload.length : Array.isArray(payload?.notificacoes) ? payload.notificacoes.length : null,
      ...(includePayload ? { payload } : {}),
    };
  }, { includePayload, path: NOTIFICATIONS_PATH, token });
}

async function main() {
  loadEnvFile();
  const options = args();
  if (options["show-secrets"]) console.log("Secret output enabled for this test run.");
  const profile = options.profile ?? process.env.INSS_BROWSER_PROFILE ?? DEFAULT_PROFILE;

  let secret = process.env.INSS_GERID_TOTP_SECRET;
  if (!secret && options["qr-image"]) secret = await qrSecret(options["qr-image"], options.account);

  const { close, credentials, page, summary } = await loginAndAuthorize({
    profile,
    profileDirectory: options["profile-directory"] ?? process.env.INSS_BROWSER_PROFILE_DIRECTORY,
    secret,
    otp: process.env.INSS_GERID_OTP,
    timeout: Number(options.timeout) * 1000,
    headless: options.headless,
  });
  if (options["show-secrets"]) {
    console.log(JSON.stringify({ storageCredentials: credentials }, null, 2));
  }

  try {
    if (options.direct) {
      await page.goto("https://atendimento.inss.gov.br/tarefas", { waitUntil: "domcontentloaded", timeout: Number(options.timeout) * 1000 });
      const response = await directNotificationRequest(page, Number(options.timeout) * 1000, options["show-secrets"]);
      console.log(JSON.stringify({
        ...summary,
        endpoint: NOTIFICATIONS_PATH,
        mode: "direct",
        response,
      }, null, 2));
      return;
    }
    const timeout = Number(options.timeout) * 1000;
    const responsePromise = page.waitForResponse((response) => response.url().includes(NOTIFICATIONS_PATH), { timeout });
    await page.goto("https://atendimento.inss.gov.br/tarefas", { waitUntil: "domcontentloaded", timeout });
    const apiResponse = await responsePromise;
    const requestHeaders = await apiResponse.request().allHeaders();
    const requestCredentials = Object.fromEntries(
      Object.entries(requestHeaders).filter(([name]) => /authorization|cookie|token|auth/i.test(name)),
    );
    const response = {
      method: apiResponse.request().method(),
      status: apiResponse.status(),
      contentType: apiResponse.headers()["content-type"] ?? "",
    };
    try {
      const payload = await apiResponse.json();
      response.topLevelKeys = payload && typeof payload === "object" && !Array.isArray(payload) ? Object.keys(payload).sort() : [];
      response.notificationCount = Array.isArray(payload) ? payload.length : Array.isArray(payload?.notificacoes) ? payload.notificacoes.length : null;
      if (options["show-secrets"]) response.payload = payload;
    } catch {
      response.json = false;
    }
    console.log(JSON.stringify({
      ...summary,
      endpoint: NOTIFICATIONS_PATH,
      responses: [response],
      ...(options["show-secrets"] ? { requestCredentials } : {}),
    }, null, 2));
  } finally {
    await close();
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
