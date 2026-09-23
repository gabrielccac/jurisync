#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/server";
import { StdioServerTransport } from "@modelcontextprotocol/server/stdio";
import { z } from "zod/v4";

import { ensureAdvboxSession, listCustomers, listLawsuits } from "../integrations/advbox/client.mjs";

const READ_ONLY = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: true,
};

function result(value) {
  return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }], structuredContent: value };
}

async function safe(handler) {
  try {
    return result(await handler());
  } catch (error) {
    return { isError: true, content: [{ type: "text", text: error instanceof Error ? error.message : String(error) }] };
  }
}

const pagination = {
  page: z.number().int().min(1).default(1),
  pageSize: z.number().int().min(1).max(100).default(50),
  search: z.string().max(255).default(""),
};

const server = new McpServer({ name: "advbox", version: "1.0.0" });

server.registerTool("advbox_ensure_session", {
  title: "Ensure ADVBOX session",
  description: "Authenticate headlessly in ADVBOX using the configured trusted device. Read-only.",
  annotations: READ_ONLY,
}, async () => safe(() => ensureAdvboxSession()));

server.registerTool("advbox_list_customers", {
  title: "List ADVBOX customers",
  description: "Search and paginate customers in ADVBOX. Read-only.",
  annotations: READ_ONLY,
  inputSchema: z.object(pagination),
}, async (input) => safe(() => listCustomers(input)));

server.registerTool("advbox_list_lawsuits", {
  title: "List ADVBOX lawsuits",
  description: "Search and paginate lawsuits in ADVBOX. Read-only.",
  annotations: READ_ONLY,
  inputSchema: z.object(pagination),
}, async (input) => safe(() => listLawsuits(input)));

await server.connect(new StdioServerTransport());
