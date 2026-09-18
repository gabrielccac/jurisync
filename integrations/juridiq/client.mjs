// Named Juridiq operations used by the publication pipeline.
// Account selection is explicit; session.mjs applies the write guard.

import { juridiqRequest } from "./session.mjs";

function payloadData(payload) {
  if (Array.isArray(payload?.data)) return payload.data;
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.results)) return payload.results;
  return [];
}

function addQuery(query, name, value) {
  if (value === undefined || value === null || value === "") return;
  if (Array.isArray(value)) {
    query.set(name, value.map(String).join(","));
    return;
  }
  query.set(name, String(value));
}

async function jsonRequest(path, options = {}) {
  const response = await juridiqRequest(path, options);
  const text = await response.text();
  let payload;
  try { payload = text ? JSON.parse(text) : null; }
  catch (error) {
    if (response.ok) return text;
    throw new Error(`Juridiq returned non-JSON for ${path}: ${error.message}`);
  }
  if (!response.ok) {
    const details = payload == null ? "" : `: ${typeof payload === "string" ? payload : JSON.stringify(payload)}`;
    const error = new Error(`Juridiq ${options.method ?? "GET"} ${path} failed (${response.status})${details}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

export async function listPublicationPage({
  page = 1,
  limit = 50,
  unreadOnly = true,
  isHandled,
  orderBy,
  keySearch,
  courtOrDiary,
  monitoredTermId,
  start,
  end,
  account = "operational",
} = {}) {
  const query = new URLSearchParams({ page: String(page), limit: String(limit) });
  const handled = isHandled ?? (unreadOnly ? false : undefined);
  addQuery(query, "orderBy", orderBy);
  addQuery(query, "keySearch", keySearch);
  addQuery(query, "courtOrDiary", courtOrDiary);
  addQuery(query, "isHandled", handled);
  addQuery(query, "monitoredTermId", monitoredTermId);
  addQuery(query, "start", start);
  addQuery(query, "end", end);
  return jsonRequest(`/publication?${query}`, { account });
}

export async function listPublications(options = {}) {
  const { unreadOnly = true, isHandled, ...request } = options;
  const payload = await listPublicationPage({ unreadOnly, isHandled, ...request });
  const records = payloadData(payload);
  const filterUnread = isHandled === undefined ? unreadOnly : isHandled === false;
  return filterUnread ? records.filter((item) => item?.readAt == null && item?.isHandled !== true) : records;
}

export async function listAllUnreadPublications({ limit = 50, account = "operational" } = {}) {
  const publications = [];
  for (let page = 1; ; page += 1) {
    const batch = await listPublications({ page, limit, unreadOnly: true, account });
    publications.push(...batch);
    if (batch.length < limit) break;
  }
  const ids = new Set();
  for (const publication of publications) {
    if (!publication?.id || ids.has(publication.id)) throw new Error("Juridiq publication pagination returned duplicate/invalid IDs.");
    ids.add(publication.id);
  }
  return publications;
}

export async function listAllPublications({ limit = 100, account = "operational" } = {}) {
  const publications = [];
  for (let page = 1; ; page += 1) {
    const batch = await listPublications({ page, limit, unreadOnly: false, account });
    publications.push(...batch);
    if (batch.length < limit) break;
  }
  const ids = new Set();
  for (const publication of publications) {
    if (!publication?.id || ids.has(publication.id)) throw new Error("Juridiq publication pagination returned duplicate/invalid IDs.");
    ids.add(publication.id);
  }
  return publications;
}

export async function listTaskTemplates({ page = 1, limit = 100, account = "operational" } = {}) {
  const payload = await jsonRequest(`/task/task-templates?page=${page}&limit=${limit}`, { account });
  return payloadData(payload);
}

export async function findLawSuitsByProcess(processNumber, { page = 1, limit = 50, account = "operational" } = {}) {
  if (typeof processNumber !== "string" || !processNumber.trim()) throw new TypeError("processNumber is required");
  const query = new URLSearchParams({ page: String(page), limit: String(limit), processNumber: processNumber.trim() });
  return payloadData(await jsonRequest(`/lawSuit?${query}`, { account }));
}

export async function listLawSuits({ page = 1, limit = 50, account = "operational" } = {}) {
  const query = new URLSearchParams({ page: String(page), limit: String(limit) });
  return payloadData(await jsonRequest(`/lawSuit?${query}`, { account }));
}

export async function listAllLawSuits({ limit = 100, account = "operational" } = {}) {
  const lawsuits = [];
  for (let page = 1; ; page += 1) {
    const batch = await listLawSuits({ page, limit, account });
    lawsuits.push(...batch);
    if (batch.length < limit) break;
  }
  const ids = new Set();
  for (const lawsuit of lawsuits) {
    if (!lawsuit?.id || ids.has(lawsuit.id)) throw new Error("Juridiq lawsuit pagination returned duplicate/invalid IDs.");
    ids.add(lawsuit.id);
  }
  return lawsuits;
}

export async function findExactLawSuit(processNumber, options = {}) {
  const matches = await findLawSuitsByProcess(processNumber, options);
  const exact = matches.filter((item) => item?.processNumber === processNumber);
  if (exact.length > 1) throw new Error(`Juridiq returned multiple exact lawSuit matches for ${processNumber}.`);
  return exact[0] ?? null;
}

export async function getCurrentUser({ account = "operational" } = {}) {
  return jsonRequest("/user/me", { account });
}

export async function listUsers({ page = 1, limit = 100, account = "operational" } = {}) {
  return payloadData(await jsonRequest(`/user/?page=${page}&limit=${limit}`, { account }));
}

export async function listTags({ page = 1, limit = 100, account = "operational" } = {}) {
  return payloadData(await jsonRequest(`/tag/?page=${page}&limit=${limit}`, { account }));
}

export async function listTaskColumns({ page = 1, limit = 100, account = "operational" } = {}) {
  const payload = await jsonRequest(`/tasks-columns/?page=${page}&limit=${limit}&isSettings=true`, { account });
  if (Array.isArray(payload?.data)) return payload.data;
  if (Array.isArray(payload?.columns)) return payload.columns;
  if (payload?.columns && typeof payload.columns === "object") return Object.values(payload.columns);
  return [];
}

export async function getLawSuit(lawSuitId, { account = "operational" } = {}) {
  if (typeof lawSuitId !== "string" || !lawSuitId) throw new TypeError("lawSuitId is required");
  return jsonRequest(`/lawSuit/${encodeURIComponent(lawSuitId)}`, { account });
}

export async function listLawSuitMovements(lawSuitId, { tagId, content, instance, account = "operational" } = {}) {
  if (typeof lawSuitId !== "string" || !lawSuitId) throw new TypeError("lawSuitId is required");
  const query = new URLSearchParams();
  addQuery(query, "tagId", tagId);
  addQuery(query, "content", content);
  addQuery(query, "instance", instance);
  const suffix = query.toString() ? `?${query}` : "";
  return jsonRequest(`/lawSuit/movements/${encodeURIComponent(lawSuitId)}${suffix}`, { account });
}

export async function createLawSuit(payload, { account = "test" } = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new TypeError("lawSuit payload must be an object");
  return jsonRequest("/lawSuit/", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    account,
  });
}

export async function updateLawSuit(lawSuitId, patch, { account = "operational" } = {}) {
  if (typeof lawSuitId !== "string" || !lawSuitId) throw new TypeError("lawSuitId is required");
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) throw new TypeError("lawSuit patch must be an object");
  return jsonRequest(`/lawSuit/${encodeURIComponent(lawSuitId)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
    account,
  });
}

export async function deleteLawSuit(lawSuitId, { account = "test" } = {}) {
  if (typeof lawSuitId !== "string" || !lawSuitId) throw new TypeError("lawSuitId is required");
  return jsonRequest("/lawSuit/", {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ id: [lawSuitId] }),
    account,
  });
}

function taskQuery({
  page = 1,
  limit = 30,
  lawSuitId,
  title,
  responsible,
  priority,
  isHome,
  orderBy,
  orderByField,
  isUniqueResponsible,
  isArchived,
  isPrivate,
  start,
  end,
  tag,
  dateMethod,
  personId,
  points,
  parentTaskId,
  taskColumnId,
  folderId,
  boardId,
  taskKanbanColumnIds,
  status,
} = {}) {
  const query = new URLSearchParams({ page: String(page), limit: String(limit) });
  for (const [name, value] of Object.entries({
    title, responsible, priority, isHome, orderBy, orderByField, isUniqueResponsible,
    isArchived, isPrivate, start, end, tag, dateMethod, personId, points, parentTaskId,
    taskColumnId, folderId, boardId, taskKanbanColumnIds: taskKanbanColumnIds ?? undefined,
    status, lawSuit: lawSuitId,
  })) addQuery(query, name, value);
  return query;
}

export async function listTasks(options = {}) {
  return payloadData(await jsonRequest(`/task/?${taskQuery(options)}`, { account: options.account ?? "operational" }));
}

export async function listKanbanTasks(options = {}) {
  return jsonRequest(`/task/kanban?${taskQuery(options)}`, { account: options.account ?? "operational" });
}

export async function getTask(taskId, { account = "operational" } = {}) {
  if (typeof taskId !== "string" || !taskId) throw new TypeError("taskId is required");
  return jsonRequest(`/task/${encodeURIComponent(taskId)}`, { account });
}

export async function getPublication(publicationId, { account = "operational" } = {}) {
  if (typeof publicationId !== "string" || !publicationId) throw new TypeError("publicationId is required");
  return jsonRequest(`/publication/${encodeURIComponent(publicationId)}`, { account });
}

export async function listEvents({
  responsible,
  responsiblesMetrics,
  personId,
  lawSuitId,
  dateType,
  start,
  end,
  statusFilter,
  fullQuery,
  includeFutureCount,
  tag,
  itemType,
  taskPriority,
  taskTag,
  orderBy,
  orderByField,
  page = 1,
  limit = 50,
  account = "operational",
} = {}) {
  const query = new URLSearchParams({ page: String(page), limit: String(limit) });
  for (const [name, value] of Object.entries({
    responsible, responsiblesMetrics, personId, lawSuitId, dateType, start, end, statusFilter,
    fullQuery, includeFutureCount, tag, itemType, taskPriority, taskTag, orderBy, orderByField,
  })) addQuery(query, name, value);
  return jsonRequest(`/event/?${query}`, { account });
}

export async function getEvent(eventId, { account = "operational" } = {}) {
  if (typeof eventId !== "string" || !eventId) throw new TypeError("eventId is required");
  return jsonRequest(`/event/${encodeURIComponent(eventId)}`, { account });
}

export async function getTaskHistory(taskId, { account = "operational" } = {}) {
  if (typeof taskId !== "string" || !taskId) throw new TypeError("taskId is required");
  return jsonRequest(`/history/${encodeURIComponent(taskId)}`, { account });
}

export async function listTaskComments(taskId, { account = "operational" } = {}) {
  if (typeof taskId !== "string" || !taskId) throw new TypeError("taskId is required");
  return jsonRequest(`/comment/?taskId=${encodeURIComponent(taskId)}`, { account });
}

export async function createTask(payload, { account = "operational" } = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new TypeError("task payload must be an object");
  return jsonRequest("/task/", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    account,
  });
}

export async function deleteTask(taskId, { account = "operational" } = {}) {
  if (typeof taskId !== "string" || !taskId) throw new TypeError("taskId is required");
  return jsonRequest("/task/", {
    method: "DELETE",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ id: [taskId] }),
    account,
  });
}

export async function updateTask(taskId, patch, { account = "operational" } = {}) {
  if (typeof taskId !== "string" || !taskId) throw new TypeError("taskId is required");
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) throw new TypeError("task patch must be an object");
  return jsonRequest(`/task/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
    account,
  });
}

export async function createEvent(payload, { account = "operational" } = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new TypeError("event payload must be an object");
  return jsonRequest("/event/", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    account,
  });
}

export async function markPublicationsRead(ids, { account = "operational" } = {}) {
  if (!Array.isArray(ids) || ids.length === 0 || ids.some((id) => typeof id !== "string" || !id)) {
    throw new TypeError("publication IDs must be a nonempty array of strings");
  }
  return jsonRequest("/publication/read-movements-many", {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ids }),
    account,
  });
}
