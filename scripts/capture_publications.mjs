// Read-only Juridiq publication snapshot. Never marks publications as read.
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { juridiqRequest } from "../integrations/juridiq/session.mjs";

const limit = 100;
const pages = [];
const publications = [];

function pagePayload(payload) {
  const root = payload?.data && !Array.isArray(payload.data) ? payload.data : payload;
  const items = Array.isArray(payload?.data)
    ? payload.data
    : Array.isArray(root) ? root : root?.items ?? root?.results ?? root?.publications ?? [];
  const total = payload?.totalResults ?? root?.totalResults ?? root?.total ?? payload?.total ?? null;
  const totalPages = payload?.totalPages ?? root?.totalPages ?? null;
  return { items, total, totalPages };
}

for (let page = 1; ; page += 1) {
  const response = await juridiqRequest(`/publication?page=${page}&limit=${limit}`);
  const text = await response.text();
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error(`Juridiq publication page ${page} was not JSON (HTTP ${response.status}).`);
  }
  if (!response.ok) throw new Error(`Juridiq publication page ${page} failed with HTTP ${response.status}.`);

  const { items, total, totalPages } = pagePayload(payload);
  pages.push({ page, limit, status: response.status, payload });
  publications.push(...items);
  console.error(`page ${page}: ${items.length} records`);

  const doneByTotalPages = Number.isFinite(totalPages) && page >= totalPages;
  const doneByCount = Number.isFinite(total) && publications.length >= total;
  const doneByShortPage = items.length < limit;
  if (doneByTotalPages || doneByCount || doneByShortPage) break;
}

const reportedTotal = pagePayload(pages[0]?.payload).total;
if (Number.isFinite(reportedTotal) && publications.length !== reportedTotal) {
  throw new Error(`Expected ${reportedTotal} publications, captured ${publications.length}.`);
}

const output = {
  capturedAt: new Date().toISOString(),
  source: "juridiq",
  method: "GET",
  endpoint: "/publication",
  query: { page: 1, limit },
  readOnly: true,
  reportedTotal,
  capturedTotal: publications.length,
  pageCount: pages.length,
  publications,
  rawPages: pages,
};

const destination = path.resolve("data", "juridiq-publications-2026-09-08.json");
await mkdir(path.dirname(destination), { recursive: true });
await writeFile(destination, `${JSON.stringify(output, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ destination, reportedTotal, capturedTotal: publications.length, pageCount: pages.length }));
