#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/server";
import { StdioServerTransport } from "@modelcontextprotocol/server/stdio";
import { z } from "zod/v4";

import {
  downloadAttachment,
  downloadProcess,
  ensureGeridSession,
  getTask,
  listNotifications,
  searchTasks,
} from "../integrations/inss/client.mjs";

const READ_ONLY = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: true,
};

const LOCAL_WRITE = {
  readOnlyHint: false,
  destructiveHint: false,
  idempotentHint: false,
  openWorldHint: true,
};

function result(value) {
  return {
    content: [{ type: "text", text: JSON.stringify(value, null, 2) }],
    structuredContent: value,
  };
}

function fileResult(file) {
  const { base64, markdown, ...metadata } = file;
  return {
    content: [
      { type: "text", text: JSON.stringify(metadata, null, 2) },
      ...(markdown ? [{ type: "text", text: `Extracted PDF Markdown:\n\n${markdown}` }] : []),
    ],
    structuredContent: {
      ...metadata,
      textReturned: Boolean(markdown),
    },
  };
}

async function safe(handler) {
  try {
    return result(await handler());
  } catch (error) {
    return {
      isError: true,
      content: [{ type: "text", text: error instanceof Error ? error.message : String(error) }],
    };
  }
}

const server = new McpServer({ name: "inss-gerid", version: "1.0.0" });

server.registerTool("inss_gerid_ensure_session", {
  title: "Ensure INSS GERID session",
  description: "Reuse a valid INSS PAT session headlessly or open Chrome for the user's A3/PIN login when required. Read-only.",
  annotations: READ_ONLY,
}, async () => safe(() => ensureGeridSession()));

server.registerTool("inss_list_notifications", {
  title: "List INSS PAT notifications",
  description: "List notifications from the authenticated INSS PAT account. Read-only.",
  annotations: READ_ONLY,
  inputSchema: z.object({ first: z.number().int().min(0).default(0) }),
}, async (input) => safe(() => listNotifications(input)));

server.registerTool("inss_search_tasks", {
  title: "Search INSS PAT tasks",
  description: "Search and paginate INSS PAT tasks with the portal's supported filters. Read-only.",
  annotations: READ_ONLY,
  inputSchema: z.object({
    page: z.number().int().min(1).default(1),
    pageSize: z.number().int().min(1).max(100).default(20),
    cpf: z.string().optional(),
    nome: z.string().optional(),
    protocolo: z.string().optional(),
    status: z.string().optional(),
    dataAtualizacaoInicial: z.string().optional(),
    dataAtualizacaoFinal: z.string().optional(),
    situacaoTarefa: z.string().optional(),
  }),
}, async (input) => safe(() => searchTasks(input)));

server.registerTool("inss_get_task", {
  title: "Get INSS PAT task",
  description: "Retrieve complete INSS PAT task details by protocol. Read-only.",
  annotations: READ_ONLY,
  inputSchema: z.object({ protocolo: z.string().regex(/^\d+$/) }),
}, async ({ protocolo }) => safe(() => getTask(protocolo)));

server.registerTool("inss_download_attachment", {
  title: "Download INSS PAT attachment",
  description: "Download one attachment listed by inss_get_task using its task protocol and attachment ID. Saves a new local file under downloaded_files/inss without overwriting existing files.",
  annotations: LOCAL_WRITE,
  inputSchema: z.object({
    protocolo: z.string().regex(/^\d+$/),
    anexoId: z.string().regex(/^\d+$/),
    filename: z.string().min(1).max(255).optional(),
  }),
}, async ({ protocolo, anexoId, filename }) => {
  try {
    return fileResult(await downloadAttachment(protocolo, anexoId, { filename }));
  } catch (error) {
    return {
      isError: true,
      content: [{ type: "text", text: error instanceof Error ? error.message : String(error) }],
    };
  }
});

server.registerTool("inss_download_process", {
  title: "Download consolidated INSS PAT process",
  description: "Generate and download the complete consolidated process report as a PDF. CPF and name are read from the active GERID credential. Saves a new local file under downloaded_files/inss without overwriting existing files.",
  annotations: LOCAL_WRITE,
  inputSchema: z.object({
    protocolo: z.string().regex(/^\d+$/),
    filename: z.string().min(1).max(255).optional(),
  }),
}, async ({ protocolo, filename }) => {
  try {
    return fileResult(await downloadProcess(protocolo, { filename }));
  } catch (error) {
    return {
      isError: true,
      content: [{ type: "text", text: error instanceof Error ? error.message : String(error) }],
    };
  }
});

await server.connect(new StdioServerTransport());
