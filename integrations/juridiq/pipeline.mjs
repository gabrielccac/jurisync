import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import {
  createEvent,
  createTask,
  findExactLawSuit,
  getTask,
  listAllUnreadPublications,
  listTaskTemplates,
  listTasks,
  markPublicationsRead,
  updateTask,
} from "./client.mjs";
import { DEFAULT_PROFILE } from "./profiles.mjs";

export const TASK_TEMPLATE_TITLES = DEFAULT_PROFILE.taskTemplates;

export const REQUIRED_SKILL_REFERENCES = [
  ...DEFAULT_PROFILE.skillReferences,
];

export const AGENT_CONTRACT = {
  readReferences: REQUIRED_SKILL_REFERENCES,
  assessmentStatuses: ["sufficient", "no_action", "needs_context", "review"],
  enrichment: "Use the trusted PJe adapter only for concrete missing information; match process, act, recipient, dates and referenced documents, and keep ambiguity unresolved.",
  routing: "Apply classificacao.md, including recipient, court/rito, MS exceptions, provisional/unknown-laudo handling, paired communication/RPV tasks, and initial/protocolar-initial alternatives.",
  taskRequirements: "Each task needs a supported type, obligationKey, clientName, source-grounded description, explicit dates, personIds, and routing evidence.",
  deduplication: "Return complete publication groups. Group only the same process, act/providence and date; select the richest publication as primary and leave low-confidence groups for review.",
  existingTasks: "Use tasks filtered by lawsuit. Match semantic providence/obligation, not title alone. Manual and previous-run tasks count.",
  contextResult: "Return evidence with source IDs, exact quotes, relation to the publication, and confidence. Do not convert an ambiguous movement into a fact.",
  events: "Return only confirmed events with date and time. Check Juridiq events with juridiq_list_events by lawsuit/date before creating; report any remaining ambiguity explicitly.",
  finalization: "Only completed, duplicate, existing-task and valid no-action outcomes may be marked read. Errors and ambiguity remain unread.",
};

function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}

function dateString(value, field) {
  requireValue(typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value), `${field} must be YYYY-MM-DD`);
  const parsed = new Date(`${value}T00:00:00Z`);
  requireValue(!Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value, `${field} is invalid`);
  return value;
}

function templateMap(templates, profile) {
  const map = new Map();
  for (const template of templates) {
    requireValue(template && typeof template.title === "string" && template.title, "template title is required");
    requireValue(!map.has(template.title), `duplicate template title: ${template.title}`);
    map.set(template.title, template);
  }
  const requiredTypes = profile.requiredTaskTypes ?? Object.keys(profile.taskTemplates);
  for (const type of requiredTypes) {
    const title = profile.taskTemplates[type];
    requireValue(typeof title === "string" && title, `profile template title is missing: ${type}`);
    requireValue(map.has(title), `required skill template not found: ${title}`);
  }
  return map;
}

function ids(template, field) {
  const value = template[field] ?? [];
  requireValue(Array.isArray(value), `template ${field} must be an array`);
  return value.map((item) => {
    const id = typeof item === "string" ? item : item?.id;
    requireValue(typeof id === "string" && id, `template ${field} contains an invalid ID`);
    return id;
  });
}

function renderTitle(templateTitle, clientName) {
  requireValue(typeof clientName === "string" && clientName.trim(), "clientName is required");
  return templateTitle.replaceAll("(Nome do Cliente)", clientName.trim()).replaceAll("(Nome Cliente)", clientName.trim());
}

function normalizeTasks(payload) {
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload?.data) ? payload.data : [];
}

function normalizePublication(publication) {
  return {
    ...publication,
    text: publication?.text ?? publication?.content ?? publication?.teor ?? publication?.body ?? "",
    availabilityDate: publication?.availabilityDate ?? publication?.publicationDate ?? null,
  };
}

function sourceDescription(publication, inputDescription) {
  const processNumber = publication.processNumber ?? "processo não identificado";
  return `  Fonte: publicação ${publication.id}; processo ${processNumber}.\n${inputDescription.trim()} `;
}

function buildPayload(publication, task, template) {
  const title = renderTitle(template.title, task.clientName);
  requireValue(typeof task.obligationKey === "string" && task.obligationKey.trim(), "obligationKey is required");
  requireValue(typeof task.description === "string" && task.description.trim(), "task description is required");
  const payload = {
    title,
    priority: template.priority,
    columnId: template.columnId,
    initialDate: dateString(task.initialDate, "initialDate"),
    description: sourceDescription(publication, task.description),
    lawSuitId: task.lawSuitId,
    responsiblesId: ids(template, "responsibles"),
    officeId: template.officeId,
    tags: ids(template, "tags"),
    personIds: task.personIds ?? [],
  };
  requireValue(typeof payload.lawSuitId === "string" && payload.lawSuitId, "lawSuitId is required");
  requireValue(Array.isArray(payload.personIds) && payload.personIds.every((id) => typeof id === "string" && id), "personIds must be IDs");
  if (task.finalDate !== undefined) payload.finalDate = dateString(task.finalDate, "finalDate");
  for (const field of ["initialHour", "finalHour"]) {
    if (task[field] !== undefined) requireValue(typeof task[field] === "string" && task[field].trim(), `${field} is invalid`);
    if (task[field] !== undefined) payload[field] = task[field];
  }
  for (const field of ["points", "estimatedTimeMinutes", "createSheet"]) {
    if (field in template) payload[field] = template[field];
  }
  return { title, obligationKey: task.obligationKey, payload };
}

function existingTaskId(existingTasks, lawSuitId, publicationId, obligationKey, explicitIds = []) {
  for (const task of existingTasks) {
    const taskLawSuitId = task.lawSuitId ?? task.lawSuit?.id ?? task.lawsuitId;
    if (taskLawSuitId !== lawSuitId) continue;
    if (explicitIds.includes(task.id)) return task.id;
    if (task.publicationId === publicationId || task.obligationKey === obligationKey) return task.id;
  }
  return null;
}

function normalizeTaskPatch(payload) {
  const { officeId, ...patch } = payload;
  return patch;
}

async function saveState(file, state) {
  await mkdir(path.dirname(file), { recursive: true });
  const temp = `${file}.tmp-${process.pid}`;
  await writeFile(temp, `${JSON.stringify(state, null, 2)}\n`, "utf8");
  await rename(temp, file);
}

async function loadState(file) {
  try { return JSON.parse(await readFile(file, "utf8")); }
  catch (error) { if (error.code === "ENOENT") return { publications: {}, events: {} }; throw error; }
}

function checkEvidence(evidence, available, label) {
  requireValue(Array.isArray(evidence), `${label} evidence must be an array`);
  const text = JSON.stringify(available);
  for (const item of evidence) {
    requireValue(item && typeof item.sourceId === "string" && item.sourceId.trim(), `${label} evidence sourceId is required`);
    requireValue(typeof item.quote === "string" && item.quote.trim(), `${label} evidence quote is required`);
    requireValue(text.includes(item.quote), `${label} evidence quote is not present in supplied sources`);
  }
}

function checkAssessment(assessment, publicationId, publication, context) {
  requireValue(assessment && assessment.publicationId === publicationId, "assessment publicationId does not match");
  requireValue(["sufficient", "no_action", "needs_context", "review"].includes(assessment.status), "invalid assessment status");
  requireValue(typeof assessment.reason === "string" && assessment.reason.trim(), "assessment reason is required");
  requireValue(Array.isArray(assessment.evidence) && Array.isArray(assessment.missingInformation), "assessment evidence/missingInformation must be arrays");
  checkEvidence(assessment.evidence, { publication, context }, "assessment");
  if (["sufficient", "no_action"].includes(assessment.status)) {
    requireValue(assessment.missingInformation.length === 0, `${assessment.status} cannot have missing information`);
    requireValue(assessment.evidence.length > 0, `${assessment.status} requires evidence`);
  }
  return assessment;
}

function checkDeduplication(decision, publications) {
  requireValue(decision && Array.isArray(decision.groups), "deduplicate must return groups");
  const byId = new Map(publications.map((publication) => [publication.id, publication]));
  const seen = new Set();
  const groups = [];
  for (const group of decision.groups) {
    requireValue(group && Array.isArray(group.publicationIds) && group.publicationIds.length > 0, "dedup group publicationIds are required");
    requireValue(typeof group.primaryPublicationId === "string" && group.publicationIds.includes(group.primaryPublicationId), "dedup primaryPublicationId must belong to group");
    requireValue(typeof group.canonicalActKey === "string" && group.canonicalActKey.trim(), "dedup canonicalActKey is required");
    requireValue(typeof group.reason === "string" && group.reason.trim(), "dedup reason is required");
    for (const id of group.publicationIds) {
      requireValue(byId.has(id), `dedup references unknown publication: ${id}`);
      requireValue(!seen.has(id), `publication appears in multiple dedup groups: ${id}`);
      seen.add(id);
    }
    const duplicates = group.duplicatePublicationIds ?? group.publicationIds.filter((id) => id !== group.primaryPublicationId);
    requireValue(Array.isArray(duplicates), "dedup duplicatePublicationIds must be an array");
    requireValue(new Set(duplicates).size === duplicates.length, "dedup duplicatePublicationIds contains duplicates");
    requireValue(duplicates.every((id) => group.publicationIds.includes(id) && id !== group.primaryPublicationId), "dedup duplicate ID is invalid");
    groups.push({ ...group, duplicatePublicationIds: duplicates, status: group.status ?? "process" });
  }
  requireValue(seen.size === publications.length, "dedup must account for every fetched publication");
  return groups;
}

function checkRouting(route, publicationId, publication, context, assessment) {
  requireValue(route?.publicationId === publicationId, "routing publicationId does not match");
  requireValue(["sufficient", "no_action"].includes(route.status), "routing must be sufficient or no_action");
  requireValue(Array.isArray(route.tasks), "routing tasks must be an array");
  requireValue(route.status === "no_action" ? route.tasks.length === 0 : route.tasks.length > 0, "routing task count conflicts with status");
  requireValue(Array.isArray(route.events ?? []), "routing events must be an array");
  if (route.status === "sufficient") {
    requireValue(Array.isArray(route.evidence) && route.evidence.length > 0, "routing requires evidence");
    requireValue(typeof route.classificationRule === "string" && route.classificationRule.trim(), "routing must name the skill classification rule");
    checkEvidence(route.evidence, { publication, context, assessment }, "routing");
  }
  const obligationKeys = new Set();
  for (const task of route.tasks) {
    requireValue(task && typeof task.obligationKey === "string" && task.obligationKey.trim(), "each task requires an obligationKey");
    requireValue(!obligationKeys.has(task.obligationKey), `duplicate obligationKey: ${task.obligationKey}`);
    obligationKeys.add(task.obligationKey);
    requireValue(["create", "update"].includes(task.action ?? "create"), "task action must be create or update");
  }
  return route;
}

function checkEvent(event, publication, context, assessment, route, lawSuitId) {
  requireValue(event && typeof event.eventKey === "string" && event.eventKey.trim(), "eventKey is required");
  requireValue(["create", "skip_existing"].includes(event.action ?? "create"), "event action must be create or skip_existing");
  requireValue(typeof event.reason === "string" && event.reason.trim(), "event reason is required");
  checkEvidence(event.evidence, { publication, context, assessment, route }, "event");
  if (event.action === "skip_existing") return event;
  const payload = event.payload;
  requireValue(payload && typeof payload === "object" && !Array.isArray(payload), "event payload is required");
  requireValue(typeof payload.title === "string" && payload.title.trim(), "event title is required");
  requireValue(typeof payload.date === "string", "event date is required");
  dateString(payload.date, "event.date");
  for (const field of ["initialHour", "finalHour"]) {
    requireValue(typeof payload[field] === "string" && /^\d{2}:\d{2}$/.test(payload[field]), `event ${field} must be HH:MM`);
  }
  requireValue(Array.isArray(payload.responsibleIds), "event responsibleIds must be an array");
  requireValue(payload.responsibleIds.every((id) => typeof id === "string" && id), "event responsibleIds must contain IDs");
  if (payload.lawSuitId !== undefined) requireValue(payload.lawSuitId === lawSuitId, "event lawSuitId does not match");
  return event;
}

function taskCacheEntry(task, lawSuitId, publicationId, obligationKey) {
  return { ...task, id: task.id, lawSuitId, publicationId, obligationKey };
}

async function executeRoute({ route, publication, lawSuit, context, templates, existing, publicationId, account, result, taskCache, profile }) {
  const handledTaskIds = [];
  for (const task of route.tasks) {
    const expectedTitle = profile.taskTemplates[task.type];
    requireValue(expectedTitle, `unsupported task type: ${task.type}`);
    const template = templates.get(expectedTitle);
    requireValue(template, `live task template not found: ${expectedTitle}`);
    const draft = buildPayload(publication, { ...task, lawSuitId: lawSuit.id }, template);
    const existingId = task.existingTaskId ?? existingTaskId(existing, lawSuit.id, publicationId, draft.obligationKey, route.existingTaskIds ?? []);
    const action = task.action ?? "create";
    if (action === "update") {
      requireValue(existingId, `update requested but no existing task was identified for ${draft.obligationKey}`);
      await updateTask(existingId, normalizeTaskPatch(draft.payload), { account });
      const verified = await getTask(existingId, { account });
      requireValue((verified?.id ?? verified?.data?.id) === existingId, "updated task could not be verified");
      result.updated.push({ publicationId, taskId: existingId, obligationKey: draft.obligationKey });
      handledTaskIds.push(existingId);
      const index = existing.findIndex((item) => item.id === existingId);
      const updatedEntry = taskCacheEntry({ ...verified, ...draft.payload }, lawSuit.id, publicationId, draft.obligationKey);
      if (index >= 0) existing[index] = updatedEntry;
      else existing.push(updatedEntry);
      continue;
    }
    if (existingId) {
      result.existing.push({ publicationId, taskId: existingId, obligationKey: draft.obligationKey });
      handledTaskIds.push(existingId);
      continue;
    }
    const created = await createTask(draft.payload, { account });
    const createdId = created?.id ?? created?.data?.id;
    requireValue(typeof createdId === "string" && createdId, "create_task did not return a task ID");
    const verified = await getTask(createdId, { account });
    requireValue((verified?.id ?? verified?.data?.id) === createdId, "created task could not be verified");
    result.created.push({ publicationId, taskId: createdId, obligationKey: draft.obligationKey });
    handledTaskIds.push(createdId);
    existing.push(taskCacheEntry({ ...verified, ...draft.payload }, lawSuit.id, publicationId, draft.obligationKey));
  }
  taskCache.set(lawSuit.id, existing);
  return handledTaskIds;
}

async function loadExistingTasks(lawSuitId, account, taskCache) {
  if (!taskCache.has(lawSuitId)) taskCache.set(lawSuitId, normalizeTasks(await listTasks({ lawSuitId, account })));
  return taskCache.get(lawSuitId);
}

function addPendingRead(pendingRead, state, ids, metadata) {
  for (const id of ids) {
    pendingRead.set(id, metadata);
    state.publications[id] = { ...state.publications[id], ...metadata, status: "pending_read", updatedAt: new Date().toISOString() };
  }
}

async function markPendingRead(pendingRead, state, stateFile, account, result) {
  const ids = [...pendingRead.keys()];
  for (let index = 0; index < ids.length; index += 50) {
    const batch = ids.slice(index, index + 50);
    try {
      await markPublicationsRead(batch, { account });
      for (const id of batch) {
        state.publications[id] = { ...state.publications[id], status: "completed", markedRead: true, updatedAt: new Date().toISOString() };
        result.readMarked.push(id);
      }
      await saveState(stateFile, state);
    } catch (error) {
      for (const id of batch) result.readPending.push({ publicationId: id, reason: error.message });
      result.errors.push({ publicationIds: batch, stage: "mark_read", error: error.message });
      await saveState(stateFile, state);
    }
  }
}

export async function runPublicationPipeline({
  agent,
  stateFile = path.resolve("data", "juridiq-pipeline-state.json"),
  limit = 50,
  readAccount = "test",
  writeAccount = "test",
  maxContextRounds = 1,
  profile = DEFAULT_PROFILE,
} = {}) {
  requireValue(agent && typeof agent.assess === "function" && typeof agent.route === "function" && typeof agent.deduplicate === "function", "agent.assess, agent.deduplicate and agent.route are required");
  requireValue(readAccount === writeAccount, "readAccount and writeAccount must match; Juridiq IDs are account-scoped");
  const state = await loadState(stateFile);
  const publications = (await listAllUnreadPublications({ limit, account: readAccount })).map(normalizePublication);
  const result = { fetched: publications.length, skipped: 0, reviewed: [], noAction: [], created: [], updated: [], existing: [], duplicates: [], eventsCreated: [], eventsSkipped: [], readMarked: [], readPending: [], errors: [] };
  if (publications.length === 0) return result;
  requireValue(profile && profile.taskTemplates && typeof profile.taskTemplates === "object", "profile.taskTemplates is required");
  const templates = templateMap(await listTaskTemplates({ limit: 100, account: readAccount }), profile);
  const groups = checkDeduplication(await agent.deduplicate({ publications, profile }), publications);
  const byId = new Map(publications.map((publication) => [publication.id, publication]));
  const pendingRead = new Map();
  const taskCache = new Map();
  const eventKeys = new Set(Object.values(state.events ?? {}).filter((item) => item?.status === "completed").map((item) => item.eventKey));

  for (const group of groups) {
    const primaryId = group.primaryPublicationId;
    const publication = byId.get(primaryId);
    const groupIds = group.publicationIds;
    if (group.status === "review") {
      for (const id of groupIds) {
        state.publications[id] = { status: "review", reason: group.reason, updatedAt: new Date().toISOString() };
        result.reviewed.push({ publicationId: id, status: "review", reason: group.reason });
      }
      await saveState(stateFile, state);
      continue;
    }
    if (groupIds.every((id) => state.publications[id]?.status === "completed" && state.publications[id]?.markedRead === true)) {
      result.skipped += groupIds.length;
      continue;
    }
    try {
      let lawSuit = publication.lawSuitId ? { id: publication.lawSuitId, processNumber: publication.processNumber } : null;
      if (!lawSuit && publication.processNumber) lawSuit = await findExactLawSuit(publication.processNumber, { account: readAccount });
      let context = null;
      let assessment = checkAssessment(await agent.assess({ publication, lawSuit, context, profile }), primaryId, publication, context);
      let provisionalTaskIds = [];
      if (assessment.status === "needs_context" && assessment.provisional === true) {
        requireValue(lawSuit?.id, "a provisional task requires an exact Juridiq lawSuit");
        requireValue(typeof agent.provisionalRoute === "function", "agent.provisionalRoute is required when assessment.provisional is true");
        const existing = await loadExistingTasks(lawSuit.id, readAccount, taskCache);
        const provisionalRoute = checkRouting(await agent.provisionalRoute({ publication, assessment, lawSuit, existingTasks: existing, templates: [...templates.values()], profile }), primaryId, publication, context, assessment);
        provisionalTaskIds = await executeRoute({ route: provisionalRoute, publication, lawSuit, context, templates, existing, publicationId: primaryId, account: writeAccount, result, taskCache, profile });
      }
      for (let round = 0; assessment.status === "needs_context" && round < maxContextRounds; round += 1) {
        requireValue(typeof agent.enrich === "function", "agent.enrich is required for needs_context");
        context = await agent.enrich({ publication, lawSuit, assessment, context, provisionalTaskIds, profile });
        assessment = checkAssessment(await agent.assess({ publication, lawSuit, assessment, context, profile }), primaryId, publication, context);
      }
      if (["needs_context", "review"].includes(assessment.status)) {
        for (const id of groupIds) {
          state.publications[id] = { status: "review", reason: assessment.reason, updatedAt: new Date().toISOString() };
          result.reviewed.push({ publicationId: id, status: assessment.status, reason: assessment.reason });
        }
        await saveState(stateFile, state);
        continue;
      }
      if (assessment.status === "no_action") {
        result.noAction.push(primaryId);
        for (const duplicateId of group.duplicatePublicationIds) result.duplicates.push({ publicationId: duplicateId, primaryPublicationId: primaryId });
        addPendingRead(pendingRead, state, groupIds, { outcome: "no_action", primaryPublicationId: primaryId });
        continue;
      }
      requireValue(lawSuit?.id, "exact Juridiq lawSuit was not found");
      const existing = await loadExistingTasks(lawSuit.id, readAccount, taskCache);
      const route = checkRouting(await agent.route({ publication, assessment, context, lawSuit, existingTasks: existing, provisionalTaskIds, templates: [...templates.values()], profile }), primaryId, publication, context, assessment);
      if (route.status === "no_action") {
        result.noAction.push(primaryId);
        for (const duplicateId of group.duplicatePublicationIds) result.duplicates.push({ publicationId: duplicateId, primaryPublicationId: primaryId });
        addPendingRead(pendingRead, state, groupIds, { outcome: "no_action", primaryPublicationId: primaryId });
        continue;
      }
      if (provisionalTaskIds.length > 0) {
        requireValue(route.tasks.some((task) => task.action === "update" && provisionalTaskIds.includes(task.existingTaskId)), "a provisional task must be updated in place after enrichment");
      }
      await executeRoute({ route, publication, lawSuit, context, templates, existing, publicationId: primaryId, account: writeAccount, result, taskCache, profile });
      for (const event of route.events ?? []) {
        checkEvent(event, publication, context, assessment, route, lawSuit.id);
        if ((event.action ?? "create") === "skip_existing" || eventKeys.has(event.eventKey)) {
          result.eventsSkipped.push({ publicationId: primaryId, eventKey: event.eventKey, reason: event.reason });
          continue;
        }
        const created = await createEvent(event.payload, { account: writeAccount });
        const eventId = created?.id ?? created?.data?.id ?? null;
        eventKeys.add(event.eventKey);
        state.events[event.eventKey] = { status: "completed", eventKey: event.eventKey, eventId, publicationId: primaryId, updatedAt: new Date().toISOString() };
        result.eventsCreated.push({ publicationId: primaryId, eventKey: event.eventKey, eventId, verification: "indirect_only" });
      }
      for (const duplicateId of group.duplicatePublicationIds) result.duplicates.push({ publicationId: duplicateId, primaryPublicationId: primaryId });
      addPendingRead(pendingRead, state, groupIds, { outcome: "processed", primaryPublicationId: primaryId });
    } catch (error) {
      state.publications[primaryId] = { status: "error", error: error.message, updatedAt: new Date().toISOString() };
      for (const duplicateId of group.duplicatePublicationIds) state.publications[duplicateId] = { status: "error", error: `primary publication failed: ${error.message}`, primaryPublicationId: primaryId, updatedAt: new Date().toISOString() };
      result.errors.push({ publicationId: primaryId, error: error.message });
      await saveState(stateFile, state);
    }
    await saveState(stateFile, state);
  }
  await markPendingRead(pendingRead, state, stateFile, writeAccount, result);
  return result;
}
