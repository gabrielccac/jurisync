import { chromium } from "playwright";

const baseUrl = "https://app.advbox.com.br";
const defaultSteps = [9, 1, 6, 2, 3, 4, 5, 7];

const tables = {
  customers: {
    columns: ["", "name", "", "city", "created_at"],
    names: ["selection", "name", "phone", "cityState", "createdAt"],
    sortable: [false, true, false, true, true],
    sortColumn: 1,
  },
  lawsuits: {
    columns: ["names_order", "type", "process_number", "lawsuits.created_at", "", "date_last_movement"],
    names: ["parties", "actionType", "processNumber", "createdAt", "lastMovement", "movementDate"],
    sortable: [true, true, true, true, false, true],
    sortColumn: 0,
  },
};

function required(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`Set ${name} in .env`);
  return value;
}

function positiveInteger(value, name, maximum) {
  if (!Number.isInteger(value) || value < 1 || value > maximum) {
    throw new TypeError(`${name} must be between 1 and ${maximum}`);
  }
}

function tablePath(kind, { page = 1, pageSize = 50, search = "" } = {}) {
  positiveInteger(page, "page", Number.MAX_SAFE_INTEGER);
  positiveInteger(pageSize, "pageSize", 100);
  const table = tables[kind];
  const query = new URLSearchParams({
    sEcho: "1",
    iColumns: String(table.columns.length),
    sColumns: table.columns.join(","),
    iDisplayStart: String((page - 1) * pageSize),
    iDisplayLength: String(pageSize),
    sSearch: search,
    bRegex: "false",
    iSortCol_0: String(table.sortColumn),
    sSortDir_0: "asc",
    iSortingCols: "1",
    search,
    report_url: `report/${kind}`,
  });
  table.columns.forEach((_column, index) => {
    query.set(`mDataProp_${index}`, String(index));
    query.set(`sSearch_${index}`, "");
    query.set(`bRegex_${index}`, "false");
    query.set(`bSearchable_${index}`, "true");
    query.set(`bSortable_${index}`, String(table.sortable[index]));
  });
  if (kind === "customers") {
    for (const name of ["type", "state", "city", "partner_id", "date_from", "date_until"]) query.set(name, "");
  } else {
    query.set("captured", "all");
    for (const step of defaultSteps) query.append("steps_id[]", String(step));
    for (const name of ["customer", "customer_type", "systems_id", "partner_id", "contingency", "date_from", "date_until"]) query.set(name, "");
  }
  return `/content/${kind}?${query}`;
}

export function customersPath(options) {
  return tablePath("customers", options);
}

export function lawsuitsPath(options) {
  return tablePath("lawsuits", options);
}

function decodeCell(value) {
  return String(value ?? "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;|&#160;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;|&#34;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/\s+/g, " ")
    .trim();
}

export function normalizeTable(payload, names) {
  let data = payload?.data ?? [];
  if (typeof data === "string") data = JSON.parse(data);
  if (!Array.isArray(data)) throw new Error("ADVBOX table response did not contain an array");
  return {
    total: Number(payload.iTotalRecords ?? 0),
    filteredTotal: Number(payload.iTotalDisplayRecords ?? 0),
    totalPages: Number(payload.iTotalPage ?? 0),
    records: data.map((row) => {
      const record = Object.fromEntries(names.map((name, index) => [name, decodeCell(row[index])]));
      const links = [...new Set(row.flatMap((cell) => [...String(cell ?? "").matchAll(/href=["']([^"']+)["']/gi)].map((match) => match[1])))];
      if (links.length) record.links = links;
      return record;
    }),
  };
}

async function login() {
  const email = required("ADVBOX_EMAIL");
  const password = required("ADVBOX_PASSWORD");
  const device = required("ADVBOX_DEVICE_ID");
  const timeout = Number(process.env.ADVBOX_TIMEOUT_SECONDS ?? 60) * 1000;
  const browser = await chromium.launch({
    channel: process.env.ADVBOX_BROWSER_CHANNEL ?? "chrome",
    headless: process.env.ADVBOX_HEADLESS !== "false",
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(timeout);
  try {
    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded", timeout });
    await page.locator('input[name="email"]').fill(email);
    await page.locator('input[name="password"]').fill(password);
    await page.locator('input[name="_device"]').evaluate((input, value) => { input.value = value; }, device);
    const form = page.locator('form:has(input[name="email"])');
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded", timeout }),
      form.evaluate((element) => element.submit()),
    ]);
    const pathname = new URL(page.url()).pathname;
    if (pathname.startsWith("/user/2fa")) {
      throw new Error("ADVBOX requires device verification; approve this machine in ADVBOX and update ADVBOX_DEVICE_ID");
    }
    if (pathname === "/login") throw new Error("ADVBOX login failed; verify ADVBOX_EMAIL and ADVBOX_PASSWORD");
    return { browser, page };
  } catch (error) {
    await browser.close();
    throw error;
  }
}

async function usingSession(handler) {
  const current = await login();
  try {
    return await handler(current.page);
  } finally {
    await current.browser.close();
  }
}

async function request(page, requestPath) {
  if (!requestPath.startsWith("/content/")) throw new Error("ADVBOX request path must start with /content/");
  const response = await page.evaluate(async (path) => {
    const result = await fetch(path, {
      credentials: "include",
      headers: { Accept: "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest" },
    });
    return { status: result.status, url: result.url, contentType: result.headers.get("content-type") ?? "", body: await result.text() };
  }, requestPath);
  if (response.status < 200 || response.status >= 300) {
    throw new Error(`ADVBOX GET ${requestPath.split("?")[0]} failed (${response.status})`);
  }
  if (!response.contentType.includes("json")) {
    throw new Error(`ADVBOX session expired while requesting ${requestPath.split("?")[0]}`);
  }
  return JSON.parse(response.body);
}

export async function ensureAdvboxSession() {
  return usingSession((page) => ({ authenticated: true, url: page.url() }));
}

export async function listCustomers(options = {}) {
  return usingSession(async (page) => normalizeTable(
    await request(page, customersPath(options)),
    tables.customers.names,
  ));
}

export async function listLawsuits(options = {}) {
  return usingSession(async (page) => normalizeTable(
    await request(page, lawsuitsPath(options)),
    tables.lawsuits.names,
  ));
}
