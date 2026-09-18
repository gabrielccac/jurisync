import { execFile } from "node:child_process";
import { access } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { McpServer } from "@modelcontextprotocol/server";
import { StdioServerTransport } from "@modelcontextprotocol/server/stdio";
import { z } from "zod/v4";
import {
  createEvent,
  createLawSuit,
  createTask,
  deleteLawSuit,
  deleteTask,
  findLawSuitsByProcess,
  getEvent,
  getLawSuit,
  getPublication,
  getTask,
  getTaskHistory,
  listAllUnreadPublications,
  listAllLawSuits,
  listEvents,
  listKanbanTasks,
  listLawSuitMovements,
  listTaskComments,
  listPublications,
  listPublicationPage,
  listTaskColumns,
  listTaskTemplates,
  listTasks,
  listTags,
  listUsers,
  markPublicationsRead,
  updateLawSuit,
  updateTask,
} from "../integrations/juridiq/client.mjs";
import { AGENT_CONTRACT } from "../integrations/juridiq/pipeline.mjs";
import { DEFAULT_PROFILE } from "../integrations/juridiq/profiles.mjs";

const execFileAsync = promisify(execFile);
const projectDir = path.resolve(
  process.env.CLAUDE_PROJECT_DIR ?? path.join(path.dirname(fileURLToPath(import.meta.url)), ".."),
);

function result(value) {
  return {
    content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
    structuredContent: value,
  };
}

function errorResult(error) {
  return {
    isError: true,
    content: [{ type: "text", text: error instanceof Error ? error.message : String(error) }],
  };
}

async function safe(handler) {
  try {
    return result(await handler());
  } catch (error) {
    return errorResult(error);
  }
}

const accountSchema = z.enum(["operational", "test"]).default("operational");

function selectedAccount(account) {
  if (account !== "operational" && account !== "test") throw new Error("account must be operational or test");
  return account;
}

const READ_ANNOTATIONS = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: true,
};

const ADDITIVE_WRITE_ANNOTATIONS = {
  readOnlyHint: false,
  destructiveHint: false,
  idempotentHint: false,
  openWorldHint: true,
};

const DESTRUCTIVE_WRITE_ANNOTATIONS = {
  readOnlyHint: false,
  destructiveHint: true,
  idempotentHint: false,
  openWorldHint: true,
};

const taskListShape = {
  page: z.number().int().min(1).default(1),
  limit: z.number().int().min(1).max(100).default(30),
  lawSuitId: z.string().min(1).optional(),
  title: z.string().min(1).optional(),
  responsible: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  priority: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  isHome: z.boolean().optional(),
  orderBy: z.enum(["asc", "desc"]).optional(),
  orderByField: z.string().min(1).optional(),
  isUniqueResponsible: z.boolean().optional(),
  isArchived: z.boolean().optional(),
  isPrivate: z.boolean().optional(),
  start: z.string().min(1).optional(),
  end: z.string().min(1).optional(),
  tag: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  dateMethod: z.enum(["all", "initialDate", "finalDate", "completedAt", "subtaskFinalDate"]).optional(),
  personId: z.string().min(1).optional(),
  points: z.union([z.string().min(1), z.number()]).optional(),
  parentTaskId: z.string().min(1).optional(),
  taskColumnId: z.string().min(1).optional(),
  folderId: z.string().min(1).optional(),
  boardId: z.string().min(1).optional(),
  taskKanbanColumnIds: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  status: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
};

const eventListShape = {
  responsible: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  responsiblesMetrics: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  personId: z.string().min(1).optional(),
  lawSuitId: z.string().min(1).optional(),
  dateType: z.enum(["initialDate", "finalDate", "all"]).optional(),
  start: z.string().min(1).optional(),
  end: z.string().min(1).optional(),
  statusFilter: z.enum(["all", "completed", "pending"]).default("all"),
  fullQuery: z.boolean().optional(),
  includeFutureCount: z.boolean().optional(),
  tag: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  itemType: z.enum(["all", "tasks", "events", "audiences", "google"]).default("all"),
  taskPriority: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  taskTag: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  orderBy: z.enum(["asc", "desc"]).optional(),
  orderByField: z.string().min(1).optional(),
  page: z.number().int().min(1).default(1),
  limit: z.number().int().min(1).max(200).default(50),
  account: accountSchema,
};

const uuidSchema = z.string().uuid();
const lawSuitPersonSchema = z.object({
  id: uuidSchema.optional(),
  name: z.string(),
  personOrigin: z.enum(["Cliente", "Requerida"]),
  document: z.string().nullable().optional(),
}).strict();
const customFieldSchema = z.object({
  fieldId: uuidSchema,
  value: z.union([
    z.string().max(10000),
    z.number(),
    z.array(z.string().max(100)).max(100),
    z.null(),
  ]),
}).strict();
const lawSuitActionSchema = z.union([
  z.object({ id: uuidSchema.optional(), name: z.string() }).strict(),
  z.literal(""),
]);
const lawSuitCreateSchema = z.object({
  processNumber: z.string().optional(),
  serviceStep: z.string(),
  responsibleIds: z.array(uuidSchema),
  persons: z.array(lawSuitPersonSchema),
  officeId: uuidSchema.optional(),
  valueOfCause: z.string().optional(),
  valueOfFees: z.string().optional(),
  percentageOfFees: z.string().optional(),
  requerimentDate: z.string().optional(),
  protocolNumber: z.string().optional(),
  annotations: z.string().optional(),
  originatingProcess: z.string().optional(),
  actionGroup: lawSuitActionSchema.optional(),
  typeAction: lawSuitActionSchema.optional(),
  currentInstance: z.number().int().min(1).max(3).optional(),
  instance: z.number().int().min(1).max(3).optional(),
  court: z.string().optional(),
  tags: z.array(uuidSchema).optional(),
  degreeOfRisk: z.union([uuidSchema, z.literal("")]).optional(),
  isPrivate: z.boolean().optional(),
  isSecret: z.boolean().optional(),
  isProcessAdministrative: z.boolean().optional(),
  linkedLawSuitId: z.array(uuidSchema).optional(),
  processUrl: z.string().optional(),
  courtBranch: z.string().optional(),
  precatoryNumbers: z.array(z.string()).optional(),
  status: z.string().optional(),
  publicationId: z.string().optional(),
  createdByAutomation: z.boolean().optional(),
  author: z.string().optional(),
  defendant: z.string().optional(),
  periciaResult: z.string().optional(),
  periciaExpertName: z.string().optional(),
  periciaExpertType: z.string().optional(),
  condemnationAmount: z.string().optional(),
  periciaExpertHonoraries: z.string().optional(),
  valueHonorariesSuccession: z.string().optional(),
  petition: z.string().optional(),
  valuePetition: z.string().optional(),
  corretor: z.string().optional(),
  code: z.string().optional(),
  consultingId: uuidSchema.optional(),
  customFields: z.array(customFieldSchema).max(100).optional(),
}).strict();
const lawSuitUpdateSchema = z.object({
  serviceStep: z.string().optional(),
  responsibleIds: z.array(uuidSchema).optional(),
  officeId: uuidSchema.optional(),
  processNumber: z.string().nullable().optional(),
  valueOfCause: z.string().nullable().optional(),
  valueOfFees: z.string().nullable().optional(),
  percentageOfFees: z.string().nullable().optional(),
  requerimentDate: z.string().nullable().optional(),
  protocolNumber: z.string().nullable().optional(),
  annotations: z.string().optional(),
  originatingProcess: z.string().optional(),
  persons: z.array(lawSuitPersonSchema).optional(),
  actionGroup: lawSuitActionSchema.optional(),
  typeAction: lawSuitActionSchema.optional(),
  degreeOfRiskId: z.union([uuidSchema, z.literal(""), z.null()]).optional(),
  tags: z.array(uuidSchema).optional(),
  court: z.string().optional(),
  currentInstance: z.number().int().min(1).max(3).nullable().optional(),
  instance: z.number().int().min(1).max(3).nullable().optional(),
  courtLawSuitDate: z.string().optional(),
  status: z.string().optional(),
  linkedLawSuitId: z.array(uuidSchema).optional(),
  isPrivate: z.boolean().optional(),
  isSecret: z.boolean().optional(),
  processUrl: z.string().optional(),
  courtBranch: z.string().optional(),
  precatoryNumbers: z.array(z.string()).optional(),
  author: z.string().optional(),
  defendant: z.string().optional(),
  periciaResult: z.string().optional(),
  periciaExpertName: z.string().optional(),
  periciaExpertType: z.string().optional(),
  condemnationAmount: z.string().optional(),
  periciaExpertHonoraries: z.string().optional(),
  valueHonorariesSuccession: z.string().optional(),
  petition: z.string().optional(),
  valuePetition: z.string().optional(),
  corretor: z.string().optional(),
  code: z.string().optional(),
  consultingColumnId: z.union([uuidSchema, z.null()]).optional(),
  consultingId: z.union([uuidSchema, z.null()]).optional(),
  customFields: z.array(customFieldSchema).max(100).optional(),
}).strict();
const taskCreateSchema = z.object({
  title: z.string().min(1),
  priority: z.string().min(1),
  columnId: uuidSchema,
  initialDate: z.string(),
  description: z.unknown().optional(),
  finalDate: z.string().optional(),
  initialHour: z.string().optional(),
  finalHour: z.string().optional(),
  lawSuitId: z.union([uuidSchema, z.null()]).optional(),
  responsiblesId: z.array(uuidSchema).optional(),
  officeId: uuidSchema.optional(),
  isPrivate: z.boolean().optional(),
  tags: z.array(uuidSchema).optional(),
  points: z.number().optional(),
  needValidation: z.boolean().optional(),
  isValidated: z.boolean().optional(),
  validatorsId: z.array(uuidSchema).optional(),
  personIds: z.array(uuidSchema).optional(),
  estimatedTimeMinutes: z.number().int().min(0).optional(),
  createSheet: z.boolean().optional(),
  consultingId: z.union([uuidSchema, z.null()]).optional(),
  customFields: z.array(customFieldSchema).max(100).optional(),
}).strict();
const taskUpdateSchema = z.object({
  title: z.string().min(1).optional(),
  priority: z.string().min(1).optional(),
  status: z.string().min(1).optional(),
  initialDate: z.string().optional(),
  lawSuitId: z.union([uuidSchema, z.null()]).optional(),
  responsiblesId: z.array(uuidSchema).optional(),
  isArchived: z.boolean().optional(),
  isPrivate: z.boolean().optional(),
  officeId: uuidSchema.optional(),
  description: z.unknown().optional(),
  finalDate: z.string().nullable().optional(),
  initialHour: z.string().nullable().optional(),
  finalHour: z.string().nullable().optional(),
  currentIndex: z.number().optional(),
  tags: z.array(uuidSchema).optional(),
  points: z.number().optional(),
  needValidation: z.boolean().optional(),
  isValidated: z.boolean().optional(),
  validatorsId: z.array(uuidSchema).optional(),
  personIds: z.array(uuidSchema).optional(),
  estimatedTimeMinutes: z.number().int().min(0).optional(),
  columnId: uuidSchema.optional(),
  consultingId: z.union([uuidSchema, z.null()]).optional(),
  customFields: z.array(customFieldSchema).max(100).optional(),
}).strict();

async function pjeProcess({ processNumber, portal, scope, documentIds = [] }) {
  const python = path.join(projectDir, ".venv", "Scripts", "python.exe");
  try { await access(python); } catch { /* Fall back to PATH for non-Windows installs. */ }
  const executable = await access(python).then(() => python).catch(() => "python");
  const portals = portal === "both" ? ["1g", "2g"] : [portal];
  const attempts = [];
  for (const selectedPortal of portals) {
    const args = ["-m", "integrations.pje.client", "--portal", selectedPortal, "--process-number", processNumber, "--scope", scope];
    for (const documentId of documentIds) args.push("--document-id", documentId);
    try {
      const { stdout, stderr } = await execFileAsync(executable, args, { cwd: projectDir, env: process.env, maxBuffer: 20 * 1024 * 1024 });
      const parsed = JSON.parse(stdout);
      return { ...parsed, diagnostics: stderr.trim() || undefined, attempts: [...attempts, { portal: selectedPortal, success: true }] };
    } catch (error) {
      attempts.push({ portal: selectedPortal, success: false, error: error.message });
      if (portal !== "both") throw error;
    }
  }
  throw new Error(`PJe process was not found in the requested portals: ${JSON.stringify(attempts)}`);
}

const server = new McpServer({ name: "pipeline-juridiq", version: "1.0.0" });

server.registerTool("juridiq_get_pipeline_contract", {
  title: "Get Juridiq pipeline contract",
  description: "Return the orchestration contract and current client's profile. No network request.",
  annotations: READ_ANNOTATIONS,
}, async () => result({ contract: AGENT_CONTRACT, profile: DEFAULT_PROFILE }));

server.registerTool("juridiq_list_pending_publications", {
  title: "List pending Juridiq publications",
  description: "List unread Juridiq publications for the explicitly selected account. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ limit: z.number().int().min(1).max(100).default(50), account: accountSchema }),
}, async ({ limit, account }) => safe(() => listAllUnreadPublications({ limit, account })));

server.registerTool("juridiq_list_publications", {
  title: "List Juridiq publications",
  description: "List Juridiq publications using the API's native filters. To find recent records, request an appropriate date range or order and let the agent interpret the returned dates. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({
    page: z.number().int().min(1).default(1),
    limit: z.number().int().min(1).max(100).default(50),
    orderBy: z.enum(["asc", "desc"]).optional(),
    keySearch: z.string().min(1).optional(),
    courtOrDiary: z.string().min(1).max(200).optional(),
    isHandled: z.boolean().optional(),
    monitoredTermId: z.string().min(1).optional(),
    start: z.string().min(1).optional(),
    end: z.string().min(1).optional(),
    account: accountSchema,
  }),
}, async (input) => safe(() => listPublicationPage({ ...input, unreadOnly: false })));

server.registerTool("juridiq_find_lawsuit", {
  title: "Find Juridiq lawsuit",
  description: "Find lawsuits by exact process number. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ processNumber: z.string().min(1), account: accountSchema }),
}, async ({ processNumber, account }) => safe(() => findLawSuitsByProcess(processNumber, { account })));

server.registerTool("juridiq_list_lawsuits", {
  title: "List Juridiq lawsuits",
  description: "List all lawsuits available to the explicitly selected account with pagination. Read-only. The API response determines which records are open; do not assume an open-status filter that was not returned.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ limit: z.number().int().min(1).max(100).default(50), account: accountSchema }),
}, async ({ limit, account }) => safe(() => listAllLawSuits({ limit, account })));

server.registerTool("juridiq_get_lawsuit", {
  title: "Get Juridiq lawsuit",
  description: "Retrieve a Juridiq lawsuit by ID. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ lawSuitId: z.string().min(1), account: accountSchema }),
}, async ({ lawSuitId, account }) => safe(() => getLawSuit(lawSuitId, { account })));

server.registerTool("juridiq_create_lawsuit", {
  title: "Create Juridiq lawsuit",
  description: "Create a lawsuit in the explicitly selected Juridiq account. Required payload fields are serviceStep, responsibleIds (UUIDs), and persons with name/personOrigin. personOrigin must be Cliente or Requerida. Requires confirmation and JURIDIQ_ALLOW_WRITES=true.",
  annotations: ADDITIVE_WRITE_ANNOTATIONS,
  inputSchema: z.object({ payload: lawSuitCreateSchema, account: accountSchema, confirmation: z.literal("CONFIRM_JURIDIQ_WRITE") }),
}, async ({ payload, account }) => safe(() => createLawSuit(payload, { account: selectedAccount(account) })));

server.registerTool("juridiq_update_lawsuit", {
  title: "Update Juridiq lawsuit",
  description: "Update a lawsuit in the explicitly selected Juridiq account. Requires confirmation and JURIDIQ_ALLOW_WRITES=true.",
  annotations: DESTRUCTIVE_WRITE_ANNOTATIONS,
  inputSchema: z.object({ lawSuitId: uuidSchema, patch: lawSuitUpdateSchema, account: accountSchema, confirmation: z.literal("CONFIRM_JURIDIQ_WRITE") }),
}, async ({ lawSuitId, patch, account }) => safe(() => updateLawSuit(lawSuitId, patch, { account: selectedAccount(account) })));

server.registerTool("juridiq_delete_lawsuit", {
  title: "Delete Juridiq lawsuit",
  description: "Delete only the explicitly supplied lawsuit in the selected Juridiq account. Requires confirmation and JURIDIQ_ALLOW_WRITES=true.",
  annotations: DESTRUCTIVE_WRITE_ANNOTATIONS,
  inputSchema: z.object({ lawSuitId: uuidSchema, account: accountSchema, confirmation: z.literal("CONFIRM_JURIDIQ_DELETE") }),
}, async ({ lawSuitId, account }) => safe(() => deleteLawSuit(lawSuitId, { account: selectedAccount(account) })));

server.registerTool("juridiq_list_lawsuit_movements", {
  title: "List Juridiq lawsuit movements",
  description: "List movements attached to a Juridiq lawsuit, including content, date, instance and tags. Read-only; useful for comparing a publication with its Juridiq process movement.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({
    lawSuitId: z.string().min(1),
    tagId: z.string().min(1).optional(),
    content: z.string().min(1).optional(),
    instance: z.number().int().min(1).optional(),
    account: accountSchema,
  }),
}, async ({ lawSuitId, account, ...filters }) => safe(() => listLawSuitMovements(lawSuitId, { ...filters, account })));

server.registerTool("juridiq_get_publication", {
  title: "Get Juridiq publication",
  description: "Retrieve the complete details of one Juridiq publication by ID, including content, dates, process number, official diary and read state. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ publicationId: z.string().min(1), account: accountSchema }),
}, async ({ publicationId, account }) => safe(() => getPublication(publicationId, { account })));

server.registerTool("juridiq_list_events", {
  title: "List Juridiq calendar events",
  description: "List calendar items/events with process, person, responsible, date-range, completion, type and tag filters. Use lawSuitId to inspect events related to a process before creating anything. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object(eventListShape),
}, async (input) => safe(() => listEvents(input)));

server.registerTool("juridiq_get_event", {
  title: "Get Juridiq event",
  description: "Retrieve one calendar event by ID, including date, hours, linked process/person and responsibles. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ eventId: z.string().min(1), account: accountSchema }),
}, async ({ eventId, account }) => safe(() => getEvent(eventId, { account })));

server.registerTool("juridiq_list_task_templates", {
  title: "List Juridiq task templates",
  description: "List live task templates for the explicitly selected account. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ limit: z.number().int().min(1).max(100).default(100), account: accountSchema }),
}, async ({ limit, account }) => safe(() => listTaskTemplates({ limit, account })));

server.registerTool("juridiq_list_users", {
  title: "List Juridiq users",
  description: "List users in the configured operational office. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ limit: z.number().int().min(1).max(100).default(100), account: accountSchema }),
}, async ({ limit, account }) => safe(() => listUsers({ limit, account })));

server.registerTool("juridiq_list_tags", {
  title: "List Juridiq tags",
  description: "List tags in the configured operational office. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ limit: z.number().int().min(1).max(100).default(100), account: accountSchema }),
}, async ({ limit, account }) => safe(() => listTags({ limit, account })));

server.registerTool("juridiq_list_task_columns", {
  title: "List Juridiq task columns",
  description: "List task board columns in the configured operational office. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ limit: z.number().int().min(1).max(100).default(100), account: accountSchema }),
}, async ({ limit, account }) => safe(() => listTaskColumns({ limit, account })));

server.registerTool("juridiq_list_tasks", {
  title: "List Juridiq tasks",
  description: "List tasks using the API's filters. For publication deduplication, filter by lawSuitId and compare the semantic obligation, not only the task title. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ ...taskListShape, account: accountSchema }),
}, async (input) => safe(() => listTasks(input)));

server.registerTool("juridiq_list_tasks_kanban", {
  title: "List Juridiq Kanban tasks",
  description: "List tasks grouped by Kanban status. Use for board-oriented questions; use juridiq_list_tasks for filterable task records and deduplication. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ ...taskListShape, account: accountSchema }),
}, async (input) => safe(() => listKanbanTasks(input)));

server.registerTool("juridiq_get_task", {
  title: "Get Juridiq task",
  description: "Retrieve a task by ID. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ taskId: z.string().min(1), account: accountSchema }),
}, async ({ taskId, account }) => safe(() => getTask(taskId, { account })));

server.registerTool("juridiq_get_task_history", {
  title: "Get Juridiq task history",
  description: "Retrieve the history of a task, including actions, dates, users and Kanban transitions. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ taskId: z.string().min(1), account: accountSchema }),
}, async ({ taskId, account }) => safe(() => getTaskHistory(taskId, { account })));

server.registerTool("juridiq_list_task_comments", {
  title: "List Juridiq task comments",
  description: "List comments and replies for a task. Use to inspect human notes before deciding whether a publication is already covered. Read-only.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({ taskId: z.string().min(1), account: accountSchema }),
}, async ({ taskId, account }) => safe(() => listTaskComments(taskId, { account })));

server.registerTool("pje_get_process", {
  title: "Get PJe process context",
  description: "Retrieve a PJe process search result, process details, parties, the first timeline page, document inventory and optionally selected document text. Use both to fall back from 1G to 2G. scope=latest returns only the newest date bucket; scope=page is the default first HTML page; scope=all is a legacy alias for page. This returns movements, not confirmed publications.",
  annotations: READ_ANNOTATIONS,
  inputSchema: z.object({
    processNumber: z.string().min(1),
    portal: z.enum(["1g", "2g", "both"]).default("both"),
    scope: z.enum(["latest", "page", "all"]).default("page"),
    documentIds: z.array(z.string().min(1)).max(5).default([]),
  }),
}, async (input) => safe(() => pjeProcess(input)));

server.registerTool("juridiq_create_task", {
  title: "Create Juridiq task",
  description: "Create a task in the explicitly selected Juridiq account. Required payload fields are title, priority, columnId (a Kanban column UUID from juridiq_list_task_columns), and initialDate. Use responsiblesId, not responsibleIds. Requires confirmation and JURIDIQ_ALLOW_WRITES=true.",
  annotations: ADDITIVE_WRITE_ANNOTATIONS,
  inputSchema: z.object({ payload: taskCreateSchema, account: accountSchema, confirmation: z.literal("CONFIRM_JURIDIQ_WRITE") }),
}, async ({ payload, account }) => safe(() => createTask(payload, { account: selectedAccount(account) })));

server.registerTool("juridiq_update_task", {
  title: "Update Juridiq task",
  description: "Update a task in the explicitly selected Juridiq account. Requires confirmation and JURIDIQ_ALLOW_WRITES=true.",
  annotations: DESTRUCTIVE_WRITE_ANNOTATIONS,
  inputSchema: z.object({ taskId: uuidSchema, patch: taskUpdateSchema, account: accountSchema, confirmation: z.literal("CONFIRM_JURIDIQ_WRITE") }),
}, async ({ taskId, patch, account }) => safe(() => updateTask(taskId, patch, { account: selectedAccount(account) })));

server.registerTool("juridiq_delete_task", {
  title: "Delete Juridiq task",
  description: "Delete only the explicitly supplied task in the selected Juridiq account. Requires confirmation and JURIDIQ_ALLOW_WRITES=true.",
  annotations: DESTRUCTIVE_WRITE_ANNOTATIONS,
  inputSchema: z.object({ taskId: uuidSchema, account: accountSchema, confirmation: z.literal("CONFIRM_JURIDIQ_DELETE") }),
}, async ({ taskId, account }) => safe(() => deleteTask(taskId, { account: selectedAccount(account) })));

server.registerTool("juridiq_create_event", {
  title: "Create Juridiq event",
  description: "Create a calendar event in the explicitly selected Juridiq account. Requires confirmation and JURIDIQ_ALLOW_WRITES=true.",
  annotations: ADDITIVE_WRITE_ANNOTATIONS,
  inputSchema: z.object({ payload: z.record(z.string(), z.unknown()), account: accountSchema, confirmation: z.literal("CONFIRM_JURIDIQ_WRITE") }),
}, async ({ payload, account }) => safe(() => createEvent(payload, { account: selectedAccount(account) })));

server.registerTool("juridiq_mark_publications_read", {
  title: "Mark Juridiq publications read",
  description: "Mark explicitly supplied publication IDs as read in the selected Juridiq account. Requires confirmation and JURIDIQ_ALLOW_WRITES=true.",
  annotations: DESTRUCTIVE_WRITE_ANNOTATIONS,
  inputSchema: z.object({ publicationIds: z.array(z.string().min(1)).min(1).max(50), account: accountSchema, confirmation: z.literal("CONFIRM_JURIDIQ_MARK_READ") }),
}, async ({ publicationIds, account }) => safe(() => markPublicationsRead(publicationIds, { account: selectedAccount(account) })));

const transport = new StdioServerTransport();
await server.connect(transport);
