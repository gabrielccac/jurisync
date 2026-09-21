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
const PROJECT_DIR = process.cwd();
export const DEFAULT_PROFILE = path.join(PROJECT_DIR, "data", "inss-browser-profile");
const LOGIN_URL = "https://atendimento.inss.gov.br/auth/login?loginwith=gerid";
const PYTHON = process.env.PYTHON ?? (process.platform === "win32" ? ".venv\\Scripts\\python.exe" : ".venv/bin/python");
const DEFAULT_CDP_PORT = 9338;

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
      "keep-open": { type: "boolean", default: false },
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
  await page.waitForFunction((css) => [...document.querySelectorAll(`${css} option`)].some((option) => option.value), selector);
  const value = await page.locator(`${selector} option`).evaluateAll((options) => options.find((option) => option.value)?.value);
  if (!value) throw new Error(`No selectable option found for ${selector}`);
  await page.selectOption(selector, value);
  return value;
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

async function launchChrome(profile, profileDirectory, timeout) {
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
  if (profileDirectory) args.push(`--profile-directory=${profileDirectory}`);
  args.push("about:blank");
  const child = spawn(chromeExecutable(), args, { stdio: "ignore", windowsHide: false });
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) break;
    } catch {}
    if (child.exitCode !== null) throw new Error("Chrome exited before its CDP endpoint became available");
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
      child.kill();
    }
  };
  return { context, page, close };
}

export async function loginAndAuthorize({ profile, profileDirectory, secret, otp, timeout = 120000 }) {
  const { context, page, close } = await launchChrome(profile, profileDirectory, timeout);
  page.setDefaultTimeout(timeout);

  try {
    await page.goto(LOGIN_URL, { waitUntil: "domcontentloaded", timeout });
    const currentUrl = new URL(page.url());
    const alreadyAtendimento = currentUrl.hostname === "atendimento.inss.gov.br" && currentUrl.pathname !== "/auth/login";
    if (!alreadyAtendimento) {
      if (await page.locator("#botaoCertificadoDigital").count()) {
        console.log("Select the A3 certificate in the native browser/OS prompt if requested.");
        await page.click("#botaoCertificadoDigital");
      }

      if (await page.locator("#token").count() === 0) {
        await page.waitForSelector("#token", { timeout });
      }
      const code = otp ?? totp(secret);
      await page.fill("#token", code);
      await page.click('input[name="_eventId_submit"]');
      await page.waitForURL((url) => url.hostname === "atendimento.inss.gov.br", { timeout });
    }

    let scope = null;
    let role = null;
    if (await page.locator("#dtpSelectAbrangencia").count()) {
      scope = await firstOption(page, "#dtpSelectAbrangencia");
      role = await firstOption(page, "#dtpSelectPapel");
      await page.waitForFunction(() => !document.querySelector("#btnAutorizo")?.disabled);
      await page.click("#btnAutorizo");
    }
    await page.waitForTimeout(1500);
    const storage = await page.evaluate(() => ({
      keys: Object.keys(localStorage).sort(),
      apiBase: localStorage.getItem("base_url"),
    }));
    return {
      context,
      page,
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
  const profile = args.profile ?? process.env.INSS_BROWSER_PROFILE ?? DEFAULT_PROFILE;

  let secret = process.env.INSS_GERID_TOTP_SECRET;
  if (!secret && args["qr-image"]) secret = await qrSecret(args["qr-image"], args.account);
  if (!secret && !process.env.INSS_GERID_OTP) {
    throw new Error("Set INSS_GERID_TOTP_SECRET, INSS_GERID_OTP, or pass --qr-image");
  }

  const timeout = Number(args.timeout) * 1000;
  const profileDirectory = args["profile-directory"] ?? process.env.INSS_BROWSER_PROFILE_DIRECTORY;
  const { close, summary } = await loginAndAuthorize({ profile, profileDirectory, secret, otp: process.env.INSS_GERID_OTP, timeout });
  try {
    console.log(JSON.stringify(summary, null, 2));
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
