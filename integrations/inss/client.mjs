import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { loadEnvFile, loginAndAuthorize, qrSecret } from "../../scripts/inss_gerid_playwright.mjs";

const projectDir = path.resolve(
  process.env.CLAUDE_PROJECT_DIR
    ?? path.join(path.dirname(fileURLToPath(import.meta.url)), "..", ".."),
);
const tasksUrl = "https://atendimento.inss.gov.br/tarefas";
const siteKey = process.env.INSS_RECAPTCHA_SITE_KEY ?? "6LfA-t8oAAAAAAMYMMHDthsSTek1eOYMwyjJcvoK";
const recaptchaAction = process.env.INSS_RECAPTCHA_ACTION ?? "buscaRequerimentos";
const logger = { log: (...args) => console.error(...args), warn: (...args) => console.error(...args) };
let secretPromise;
let sessionQueue = Promise.resolve();
let pdfInspectorPromise;

async function pdfInspector() {
  pdfInspectorPromise ??= (async () => {
    const module = await import("@firecrawl/pdf-inspector-wasm");
    const entry = import.meta.resolve("@firecrawl/pdf-inspector-wasm");
    const wasm = await readFile(new URL("pdf_inspector_wasm_bg.wasm", entry));
    await module.default({ module_or_path: wasm });
    return module;
  })();
  return pdfInspectorPromise;
}

async function geridSecret() {
  if (process.env.INSS_GERID_TOTP_SECRET) return process.env.INSS_GERID_TOTP_SECRET;
  const image = path.resolve(process.env.INSS_GERID_QR_IMAGE ?? path.join(projectDir, "QR_gerid.png"));
  if (!existsSync(image)) return undefined;
  secretPromise ??= qrSecret(image, process.env.INSS_GERID_TOTP_ACCOUNT ?? "1");
  return secretPromise;
}

async function session() {
  loadEnvFile();
  return loginAndAuthorize({
    profile: path.resolve(process.env.INSS_BROWSER_PROFILE ?? path.join(projectDir, "data", "inss-browser-profile")),
    profileDirectory: process.env.INSS_BROWSER_PROFILE_DIRECTORY,
    secret: await geridSecret(),
    otp: process.env.INSS_GERID_OTP,
    timeout: Number(process.env.INSS_GERID_TIMEOUT_SECONDS ?? 180) * 1000,
    headless: true,
    logger,
  });
}

function usingSession(handler) {
  // ponytail: one local A3 profile supports one browser; add per-profile queues only for multi-account support.
  const run = sessionQueue.then(async () => {
    const current = await session();
    try {
      await current.page.goto(tasksUrl, { waitUntil: "domcontentloaded" });
      return await handler(current.page, current.summary);
    } finally {
      await current.close();
    }
  });
  sessionQueue = run.catch(() => {});
  return run;
}

async function token(page) {
  await page.waitForFunction(() => {
    const api = window.grecaptcha?.enterprise ?? window.grecaptcha;
    return typeof api?.execute === "function";
  });
  return page.evaluate(async ({ action, key }) => {
    const api = window.grecaptcha?.enterprise ?? window.grecaptcha;
    return api.execute(key, { action });
  }, { action: recaptchaAction, key: siteKey });
}

async function request(page, apiPath, { method = "GET", body } = {}) {
  if (!apiPath.startsWith("/apis/")) throw new Error("INSS API path must start with /apis/");
  const result = await page.evaluate(async ({ apiPath, body, method }) => {
    const auth = localStorage.getItem("ifs_auth") ?? localStorage.getItem("gerid_token");
    if (!auth) return { status: 401, payload: { error: "GERID credential missing" } };
    const response = await fetch(apiPath, {
      method,
      credentials: "include",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${auth}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const text = await response.text();
    let payload;
    try { payload = text ? JSON.parse(text) : null; }
    catch { payload = text; }
    return { status: response.status, payload };
  }, { apiPath, body, method });
  if (result.status < 200 || result.status >= 300) {
    throw new Error(`INSS ${method} ${apiPath} failed (${result.status}): ${JSON.stringify(result.payload)}`);
  }
  return result.payload;
}

export function attachmentFilename(contentDisposition, fallback) {
  const encoded = contentDisposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plain = contentDisposition?.match(/filename="?([^";]+)"?/i)?.[1];
  let name = fallback;
  const raw = contentDisposition && !contentDisposition.includes(";") ? contentDisposition : null;
  try { name = decodeURIComponent(encoded ?? plain ?? raw ?? fallback); } catch {}
  name = path.basename(name).replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").trim();
  return name && name !== "." && name !== ".." ? name : fallback;
}

export function attachmentMimeType(bytes, filename) {
  if (bytes.subarray(0, 5).toString() === "%PDF-") return "application/pdf";
  if (bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) return "image/png";
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "image/jpeg";
  const extension = path.extname(filename).toLowerCase();
  return ({ ".txt": "text/plain", ".csv": "text/csv", ".json": "application/json" })[extension]
    ?? "application/octet-stream";
}

async function availablePath(directory, filename) {
  const extension = path.extname(filename);
  const stem = path.basename(filename, extension);
  let candidate = path.join(directory, filename);
  for (let index = 1; existsSync(candidate); index += 1) {
    candidate = path.join(directory, `${stem}-${index}${extension}`);
  }
  return candidate;
}

export async function extractPdfMetadata(bytes, maxTextChars = Number(process.env.INSS_MAX_PDF_TEXT_CHARS ?? 500_000)) {
  const limit = Number.isSafeInteger(maxTextChars) && maxTextChars > 0 ? maxTextChars : 500_000;
  const { processPdf } = await pdfInspector();
  const parsed = processPdf(bytes, { profile: "compact", includePageMarkers: true });
  const fullMarkdown = parsed.markdown ?? "";
  return {
    markdown: fullMarkdown.slice(0, limit),
    textCharacters: fullMarkdown.length,
    textTruncated: fullMarkdown.length > limit,
    pdfType: parsed.pdfType,
    pageCount: parsed.pageCount,
    pagesNeedingOcr: parsed.pagesNeedingOcr,
    hasEncodingIssues: parsed.hasEncodingIssues,
  };
}

async function saveBase64Download(base64, requestedName, fallback, expectedMimeType) {
  if (typeof base64 !== "string" || !base64.trim()) throw new Error("INSS download response was empty or invalid Base64");
  const bytes = Buffer.from(base64.replace(/\s+/g, ""), "base64");
  const maxBytes = Number(process.env.INSS_MAX_DOWNLOAD_BYTES ?? 50 * 1024 * 1024);
  if (!bytes.length) throw new Error("INSS download response was empty or invalid Base64");
  if (bytes.length > maxBytes) throw new Error(`INSS download exceeds the ${maxBytes}-byte download limit`);
  const outputName = attachmentFilename(requestedName, fallback);
  const mimeType = attachmentMimeType(bytes, outputName);
  if (expectedMimeType && mimeType !== expectedMimeType) throw new Error(`INSS download was not ${expectedMimeType}`);
  const directory = path.join(projectDir, "downloaded_files", "inss");
  await mkdir(directory, { recursive: true });
  const outputPath = await availablePath(directory, outputName);
  await writeFile(outputPath, bytes, { flag: "wx" });
  let pdf = {};
  if (mimeType === "application/pdf") {
    try {
      pdf = await extractPdfMetadata(bytes);
    } catch (error) {
      pdf = { pdfExtractionError: error instanceof Error ? error.message : String(error) };
    }
  }
  return {
    path: outputPath,
    filename: path.basename(outputPath),
    bytes: bytes.length,
    mimeType,
    base64: bytes.toString("base64"),
    ...pdf,
  };
}

export function processReportRequest(protocolo, auth) {
  if (!/^\d+$/.test(protocolo)) throw new TypeError("protocolo must contain only digits");
  let claims;
  try { claims = JSON.parse(Buffer.from(auth.split(".")[1], "base64url").toString()); }
  catch { throw new Error("GERID credential is not a readable JWT"); }
  const cpf = String(claims.cpf ?? claims.user_name ?? "").replace(/\D/g, "").padStart(11, "0");
  const nome = String(claims.nome ?? "").trim();
  if (!/^\d{11}$/.test(cpf) || !nome) throw new Error("GERID credential does not contain CPF and name");
  return `/apis/consolidadorECApi/pap/metadados?${new URLSearchParams({
    protocolo: String(protocolo),
    sistema: "PAT",
    cpf,
    nome,
    async: "false",
  })}`;
}

export async function ensureGeridSession() {
  return usingSession(async (_page, summary) => summary);
}

export async function listNotifications({ first = 0 } = {}) {
  return usingSession(async (page) => request(
    page,
    `/apis/requerimentosPortalApi/requerimento/ec/notificacao?first=${first}`,
    { method: "POST", body: { tokenReCAPTCHA: await token(page) } },
  ));
}

function saoPauloDate(date) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Sao_Paulo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date).map(({ type, value }) => [type, value]));
  return `${parts.year}-${parts.month}-${parts.day}T03:00:00.000Z`;
}

function searchDate(value, fallback) {
  if (!value) return saoPauloDate(fallback);
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return `${value}T03:00:00.000Z`;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) throw new TypeError(`Invalid INSS search date: ${value}`);
  return parsed.toISOString();
}

export function taskSearchRequest(
  { page = 1, pageSize = 20, ...filters } = {},
  tokenReCAPTCHA = "",
  now = new Date(),
) {
  if (!Number.isInteger(page) || page < 1) throw new TypeError("page must be a positive integer");
  if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) throw new TypeError("pageSize must be between 1 and 100");
  const first = (page - 1) * pageSize;
  const initial = new Date(now);
  initial.setUTCDate(initial.getUTCDate() - 180);
  return {
    path: `/apis/requerimentosPortalApi/requerimento/ec/tarefa/consulta?first=${first}&pageSize=${pageSize}`,
    body: {
      cpf: filters.cpf ?? "",
      nome: filters.nome ?? "",
      protocolo: filters.protocolo ?? "",
      status: filters.status ?? "",
      tokenReCAPTCHA,
      dataAtualizacaoInicial: searchDate(filters.dataAtualizacaoInicial, initial),
      dataAtualizacaoFinal: searchDate(filters.dataAtualizacaoFinal, now),
      situacaoTarefa: filters.situacaoTarefa ?? "",
    },
  };
}

export async function searchTasks(options = {}) {
  return usingSession(async (browserPage) => {
    const query = taskSearchRequest(options, await token(browserPage));
    return request(browserPage, query.path, { method: "POST", body: query.body });
  });
}

export async function getTask(protocolo) {
  if (!/^\d+$/.test(protocolo)) throw new TypeError("protocolo must contain only digits");
  return usingSession((page) => request(
    page,
    `/apis/requerimentosPortalApi/requerimento/ec/tarefa/${encodeURIComponent(protocolo)}`,
  ));
}

export async function downloadAttachment(protocolo, anexoId, { filename } = {}) {
  if (!/^\d+$/.test(protocolo)) throw new TypeError("protocolo must contain only digits");
  if (!/^\d+$/.test(anexoId)) throw new TypeError("anexoId must contain only digits");
  return usingSession(async (page) => {
    const apiPath = `/apis/arquivosPortalApi/tarefa/${encodeURIComponent(protocolo)}/anexo/${encodeURIComponent(anexoId)}`;
    const response = await page.evaluate(async (requestPath) => {
      const auth = localStorage.getItem("ifs_auth") ?? localStorage.getItem("gerid_token");
      if (!auth) return { status: 401, body: "", disposition: null };
      const result = await fetch(requestPath, {
        credentials: "include",
        headers: { Accept: "application/json", Authorization: `Bearer ${auth}` },
      });
      return {
        status: result.status,
        body: await result.text(),
        disposition: result.headers.get("content-disposition"),
      };
    }, apiPath);
    if (response.status < 200 || response.status >= 300) {
      throw new Error(`INSS GET ${apiPath} failed (${response.status})`);
    }
    const fallback = `${protocolo}-${anexoId}.bin`;
    return saveBase64Download(response.body, filename ?? response.disposition, fallback);
  });
}

export async function downloadProcess(protocolo, { filename } = {}) {
  return usingSession(async (page) => {
    const auth = await page.evaluate(() => localStorage.getItem("ifs_auth") ?? localStorage.getItem("gerid_token"));
    if (!auth) throw new Error("GERID credential missing");
    const payload = await request(page, processReportRequest(protocolo, auth));
    const file = await saveBase64Download(
      payload?.relatorio,
      filename,
      `${protocolo}-processo.pdf`,
      "application/pdf",
    );
    return {
      ...file,
      protocolo: payload.protocolo ?? Number(protocolo),
      permitidoEmissaoSemConfirmacao: payload.permitidoEmissaoSemConfirmacao ?? null,
    };
  });
}
