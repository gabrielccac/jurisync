#!/usr/bin/env node

import { execFile, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { promisify } from "node:util";
import { setTimeout as delay } from "node:timers/promises";
import { createHmac } from "node:crypto";
import { parseArgs } from "node:util";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const execFileAsync = promisify(execFile);
const PROJECT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const DEFAULT_PROFILE = path.join(PROJECT_DIR, "data", "inss-browser-profile");
const LOGIN_URL = "https://atendimento.inss.gov.br/auth/login?loginwith=gerid";
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? ".venv\\Scripts\\python.exe" : ".venv/bin/python");
const DEFAULT_CDP_PORT = 9338;
const GERID_CERTIFICATE_POLICY = JSON.stringify({
  pattern: "https://geridinss.dataprev.gov.br:8443",
  filter: {},
});

export function loadEnvFile() {
  if (typeof process.loadEnvFile !== "function") return;
  try { process.loadEnvFile(path.join(PROJECT_DIR, ".env")); } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

function parseCli() {
  return parseArgs({
    options: {
      profile: { type: "string" },
      "profile-directory": { type: "string" },
      "qr-image": { type: "string" },
      account: { type: "string", default: "1" },
      headless: { type: "boolean", default: false },
      "keep-open": { type: "boolean", default: false },
      "show-secrets": { type: "boolean", default: false },
      timeout: { type: "string", default: "120" },
    },
    strict: true,
  }).values;
}

function base32Decode(value) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const clean = value.replace(/\s+/g, "").replace(/=+$/, "").toUpperCase();
  let bits = 0;
  let bitCount = 0;
  const output = [];
  for (const character of clean) {
    const digit = alphabet.indexOf(character);
    if (digit < 0) throw new Error("Invalid base32 TOTP secret");
    bits = (bits << 5) | digit;
    bitCount += 5;
    if (bitCount >= 8) {
      bitCount -= 8;
      output.push((bits >> bitCount) & 0xff);
    }
  }
  return Buffer.from(output);
}

function totp(secret) {
  let algorithm = process.env.INSS_GERID_TOTP_ALGORITHM ?? "sha1";
  let digits = Number(process.env.INSS_GERID_TOTP_DIGITS ?? 6);
  let period = Number(process.env.INSS_GERID_TOTP_PERIOD ?? 30);
  if (secret.startsWith("otpauth://")) {
    const uri = new URL(secret);
    secret = uri.searchParams.get("secret") ?? "";
    algorithm = (uri.searchParams.get("algorithm") ?? algorithm).toLowerCase();
    digits = Number(uri.searchParams.get("digits") ?? digits);
    period = Number(uri.searchParams.get("period") ?? period);
  }
  const counter = Math.floor(Date.now() / 1000 / period);
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const digest = createHmac(algorithm, base32Decode(secret)).update(message).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const value = ((digest[offset] & 0x7f) << 24)
    | ((digest[offset + 1] & 0xff) << 16)
    | ((digest[offset + 2] & 0xff) << 8)
    | (digest[offset + 3] & 0xff);
  return String(value % (10 ** digits)).padStart(digits, "0");
}

export async function qrSecret(image, account) {
  const { stdout } = await execFileAsync(PYTHON, ["-m", "scripts.pje_2fa_from_qr", image, "--account", String(account)], {
    cwd: PROJECT_DIR,
    maxBuffer: 1024 * 1024,
  });
  const secret = stdout.trim().split(/\r?\n/).pop();
  if (!secret) throw new Error("QR parser returned no TOTP secret");
  return secret;
}

export function safeUrl(value) {
  try {
    const url = new URL(value);
    url.hash = "";
    for (const key of ["ticket", "access_token", "refresh_token"]) url.searchParams.delete(key);
    return url.toString();
  } catch {
    return "[unavailable]";
  }
}

async function firstOption(page, selector) {
  const option = page.locator(selector + ' option:not([value=""])').first();
  await option.waitFor({ state: "attached" });
  const value = await option.getAttribute("value");
  if (!value) throw new Error(`No selectable option found for ${selector}`);
  await page.locator(selector).selectOption(value);
  return value;
}

export function isReusableAuth(auth, now = Date.now()) {
  if (!auth) return false;
  const parts = auth.split(".");
  if (parts.length !== 3) return true;
  try {
    const { exp } = JSON.parse(Buffer.from(parts[1], "base64url"));
    return typeof exp !== "number" || exp * 1000 > now + 30000;
  } catch {
    return false;
  }
}

function chromeExecutable() {
  const candidates = [
    process.env.INSS_CHROME_PATH,
    process.platform === "win32" ? "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" : null,
    process.platform === "win32" ? "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe" : null,
    process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : null,
    process.platform === "linux" ? "/usr/bin/google-chrome" : null,
    process.platform === "linux" ? "/usr/bin/chromium" : null,
  ].filter(Boolean);
  const executable = candidates.find((candidate) => existsSync(candidate));
  if (!executable) throw new Error("Google Chrome executable not found; set INSS_CHROME_PATH");
  return executable;
}

async function configureMacCertificateAutoSelect(logger) {
  if (process.platform !== "darwin") return;

  const domain = "com.google.Chrome";
  const key = "AutoSelectCertificateForUrls";
  let operation = "-array";
  try {
    const { stdout } = await execFileAsync("/usr/bin/defaults", ["read", domain, key]);
    if (stdout.includes("geridinss.dataprev.gov.br")) return;
    operation = "-array-add";
  } catch {}

  await execFileAsync("/usr/bin/defaults", ["write", domain, key, operation, "-string", GERID_CERTIFICATE_POLICY]);
  logger.log("Configured Chrome to auto-select the client certificate for GERID.");
}

async function launchChrome(profile, profileDirectory, timeout, headless, logger) {
  try {
    await configureMacCertificateAutoSelect(logger);
  } catch (error) {
    logger.warn(`Could not configure Chrome certificate auto-selection: ${error.message}`);
  }

  const port = Number(process.env.INSS_CDP_PORT ?? DEFAULT_CDP_PORT);
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${path.resolve(profile)}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-component-update",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-translate",
    "--disable-default-apps",
  ];
  if (headless) args.push("--headless=new");
  if (profileDirectory) args.push(`--profile-directory=${profileDirectory}`);
  args.push("about:blank");
  const child = spawn(chromeExecutable(), args, {
    stdio: ["ignore", "ignore", "pipe"],
    windowsHide: false,
  });
  let chromeError = "";
  child.stderr.on("data", (chunk) => {
    chromeError = (chromeError + chunk).slice(-4000);
  });
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) break;
    } catch {}
    if (child.exitCode !== null) {
      const details = chromeError.trim();
      throw new Error(`Chrome exited before its CDP endpoint became available${details ? `: ${details}` : ""}`);
    }
    await delay(250);
  }
  if (Date.now() >= deadline) throw new Error(`Chrome CDP endpoint did not start on port ${port}`);
  const browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  const context = browser.contexts()[0];
  const page = context.pages()[0] ?? await context.newPage();
  const close = async () => {
    await browser.close().catch(() => {});
    if (process.platform === "win32" && child.pid) {
      await execFileAsync("taskkill", ["/PID", String(child.pid), "/T", "/F"]).catch(() => {});
    } else if (child.exitCode === null) {
      const exited = new Promise((resolve) => child.once("exit", resolve));
      child.kill();
      await Promise.race([exited, delay(5000)]);
    }
  };
  return { context, page, close };
}

export async function loginAndAuthorize({
  profile,
  profileDirectory,
  secret,
  otp,
  timeout = 120000,
  headless = false,
  logger = console,
}) {
  const { context, page, close } = await launchChrome(profile, profileDirectory, timeout, headless, logger);
  page.setDefaultTimeout(timeout);

  try {
    await page.goto(LOGIN_URL, { waitUntil: "commit", timeout });
    const certificateButton = page.locator("#botaoCertificadoDigital");
    const tokenInput = page.locator("#token");
    let nextStep;
    if (headless) {
      const auth = await page.waitForFunction(
        () => localStorage.getItem("ifs_auth") || localStorage.getItem("gerid_token"),
        null,
        { polling: 250, timeout: Math.min(timeout, 5000) },
      ).then((handle) => handle.jsonValue()).catch(() => null);
      if (!isReusableAuth(auth)) {
        logger.log("No reusable browser session; reopening Chrome headed for A3 login.");
        await close();
        return loginAndAuthorize({ profile, profileDirectory, secret, otp, timeout, logger });
      }
      nextStep = "authenticated";
    } else {
      nextStep = await Promise.race([
        certificateButton.waitFor({ state: "visible", timeout }).then(() => "certificate"),
        tokenInput.waitFor({ state: "visible", timeout }).then(() => "token"),
        page.waitForURL(
          (url) => url.hostname === "atendimento.inss.gov.br" && url.pathname !== "/auth/login",
          { timeout },
        ).then(() => "authenticated"),
        page.waitForFunction(
          () => Boolean(localStorage.getItem("ifs_auth") || localStorage.getItem("gerid_token")),
          null,
          { polling: 250, timeout },
        ).then(() => "authenticated"),
      ]);
    }

    let freshLogin = false;
    if (nextStep !== "authenticated") {
      if (nextStep === "certificate") {
        logger.log("Select the A3 certificate in the native browser/OS prompt if requested.");
        await certificateButton.click();
      }

      await tokenInput.waitFor({ state: "visible", timeout });
      if (!otp && !secret) {
        throw new Error("Set INSS_GERID_TOTP_SECRET, INSS_GERID_OTP, or pass --qr-image");
      }
      const code = otp ?? totp(secret);
      await tokenInput.fill(code);
      await page.click('input[name="_eventId_submit"]');
      await page.waitForURL((url) => url.hostname === "atendimento.inss.gov.br", { timeout });
      freshLogin = true;
    } else {
      logger.log("Reusing authenticated browser session.");
    }

    let scope = null;
    let role = null;
    const scopeSelect = page.locator("#dtpSelectAbrangencia");
    if (freshLogin) {
      await scopeSelect.waitFor({ state: "visible", timeout });
    } else {
      await scopeSelect.waitFor({ state: "visible", timeout: Math.min(timeout, 5000) }).catch(() => {});
    }

    if (await scopeSelect.isVisible()) {
      scope = await firstOption(page, "#dtpSelectAbrangencia");
      logger.log(`Selected scope: ${scope}`);
      role = await firstOption(page, "#dtpSelectPapel");
      logger.log(`Selected role: ${role}`);
      await page.waitForFunction(() => !document.querySelector("#btnAutorizo")?.disabled);
      await page.click("#btnAutorizo");

      const certificateNotice = page.locator('#btnAutorizo[title="OK"]');
      await certificateNotice.waitFor({
        state: "visible",
        timeout: Math.min(timeout, 10000),
      }).catch(() => {});
      if (await certificateNotice.isVisible()) {
        await certificateNotice.click();
        await certificateNotice.waitFor({ state: "hidden", timeout });
      }
    }
    await page.waitForTimeout(1500);
    const storage = await page.evaluate(() => {
      const authKeys = ["access_token", "gerid_token", "ifs_auth", "ifs_at", "ifs_ts", "ifs_iat"];
      return {
        keys: Object.keys(localStorage).sort(),
        apiBase: localStorage.getItem("base_url"),
        credentials: Object.fromEntries(authKeys.map((key) => [key, localStorage.getItem(key)]).filter(([, value]) => value !== null)),
      };
    });
    return {
      close,
      context,
      page,
      credentials: storage.credentials,
      summary: {
        authenticated: storage.keys.includes("ifs_auth") || storage.keys.includes("gerid_token"),
        url: safeUrl(page.url()),
        scopeSelected: Boolean(scope),
        roleSelected: Boolean(role),
        apiBase: storage.apiBase,
        authKeysPresent: storage.keys.filter((key) => ["access_token", "gerid_token", "ifs_auth", "ifs_at", "ifs_ts", "ifs_iat"].includes(key)),
      },
    };
  } catch (error) {
    await close();
    throw error;
  }
}

async function main() {
  loadEnvFile();
  const args = parseCli();
  if (args["show-secrets"]) console.log("Secret output enabled for this test run.");
  const profile = args.profile ?? process.env.INSS_BROWSER_PROFILE ?? DEFAULT_PROFILE;

  let secret = process.env.INSS_GERID_TOTP_SECRET;
  if (!secret && args["qr-image"]) secret = await qrSecret(args["qr-image"], args.account);

  const timeout = Number(args.timeout) * 1000;
  const profileDirectory = args["profile-directory"] ?? process.env.INSS_BROWSER_PROFILE_DIRECTORY;
  const { close, credentials, summary } = await loginAndAuthorize({
    profile,
    profileDirectory,
    secret,
    otp: process.env.INSS_GERID_OTP,
    timeout,
    headless: args.headless,
  });
  try {
    console.log(JSON.stringify(args["show-secrets"] ? { ...summary, credentials } : summary, null, 2));
    if (args["keep-open"]) {
      console.log("Browser left open (--keep-open). Press Enter here to close it.");
      await new Promise((resolve) => process.stdin.once("data", resolve));
    }
  } finally {
    await close();
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
