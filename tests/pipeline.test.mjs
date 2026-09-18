import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

test("pipeline assesses, routes, creates and verifies a test-account task", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "juridiq-pipeline-"));
  const previous = { ...process.env };
  const originalFetch = globalThis.fetch;
  process.env.JURIDIQ_EMAIL = "pipeline-read@example.invalid";
  process.env.JURIDIQ_PASSWORD = "read-only";
  process.env.TEST_JURIDIQ_EMAIL = "pipeline-test@example.invalid";
  process.env.TEST_JURIDIQ_PASSWORD = "test-only";
  process.env.JURIDIQ_ALLOW_WRITES = "true";
  process.env.JURIDIQ_API_BASE_URL = "https://pipeline.example.invalid";
  process.env.JURIDIQ_LOGIN_URL = "https://pipeline.example.invalid/auth/login";
  process.env.JURIDIQ_SESSION_FILE = path.join(directory, "read.json");
  process.env.TEST_JURIDIQ_SESSION_FILE = path.join(directory, "test.json");
  const templateTitle = "Manifestação - (Nome Cliente)";
  globalThis.fetch = async (url, options = {}) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/auth/login") {
      const body = JSON.parse(options.body);
      return Response.json({ token: `token-${body.email}` });
    }
    if (parsed.pathname === "/publication") {
      return Response.json(parsed.searchParams.get("page") === "1" ? {
        data: [{ id: "pub-1", processNumber: "1024853-70.2023.4.01.3400", lawSuitId: "law-1", content: "publication", readAt: null }],
      } : { data: [] });
    }
    if (parsed.pathname === "/task/task-templates") {
      const titles = [
        "Réplica BPC - (Nome Cliente)", "Manifestação Aceitação Acordo - (Nome Cliente)", templateTitle,
        "Comunicação Perícia Médica Federal - (Nome Cliente)", "Comunicação Perícia Social - (Nome do Cliente)",
        "Remarcação Perícia Judicial - (Nome Cliente) ", "Impugnação ao Laudo - (Nome Cliente)",
        "Manifestação Laudo Favorável - (Nome do Cliente) ", "Comunicação Sentença Favorável - (Nome Cliente)",
        "Comunicação Acórdão Favorável - (Nome Cliente)", "Expedição RPV - (Nome Cliente)",
        "Recurso Inominado BPC - (Nome Cliente)", "Apelação BPC - (Nome do Cliente)", "Apelação MS - (Nome Cliente)",
        "Embargos de Declaração - (Nome Cliente)", "Inicial BPC - (Nome Cliente)", "Protocolar Inicial BPC - (Nome Cliente)",
      ];
      return Response.json({ data: titles.map((title, index) => ({ id: `tpl-${index}`, title, columnId: "col-1", priority: "Baixa", officeId: "office-1", responsibles: [{ id: "user-1" }], tags: [{ id: "tag-1" }] })) });
    }
    if (parsed.pathname === "/task/kanban") return Response.json({ data: [] });
    if (parsed.pathname === "/task/" && options.method !== "POST") return Response.json({ data: [] });
    if (parsed.pathname === "/task/" && options.method === "POST") {
      assert.match(options.headers.get("authorization"), /pipeline-test/);
      return Response.json({ id: "created-1" }, { status: 201 });
    }
    if (parsed.pathname === "/task/created-1") {
      assert.match(options.headers.get("authorization"), /pipeline-test/);
      return Response.json({ id: "created-1" });
    }
    if (parsed.pathname === "/publication/read-movements-many" && options.method === "PATCH") {
      assert.deepEqual(JSON.parse(options.body), { ids: ["pub-1"] });
      return Response.json({ ok: true });
    }
    throw new Error(`unexpected ${options.method ?? "GET"} ${parsed.pathname}`);
  };
  try {
    const { runPublicationPipeline } = await import("../integrations/juridiq/pipeline.mjs?pipeline-test");
    const result = await runPublicationPipeline({
      limit: 1,
      readAccount: "test",
      writeAccount: "test",
      stateFile: path.join(directory, "state.json"),
      agent: {
        async deduplicate({ publications }) {
          return { groups: publications.map((publication) => ({
            canonicalActKey: `${publication.processNumber}|${publication.id}`,
            publicationIds: [publication.id],
            primaryPublicationId: publication.id,
            duplicatePublicationIds: [],
            status: "process",
            reason: "single publication",
          })) };
        },
        async assess({ publication }) {
          return { publicationId: publication.id, status: "sufficient", reason: "publication is clear", evidence: [{ sourceId: "publication:pub-1", quote: "publication" }], missingInformation: [] };
        },
        async route({ publication }) {
          return { publicationId: publication.id, status: "sufficient", classificationRule: "generic deadline directed to claimant", evidence: [{ sourceId: "publication:pub-1", quote: "publication" }], tasks: [{ type: "manifestacao", clientName: "Cliente", description: "Responder a publicação.", initialDate: "2026-09-08", obligationKey: "pub-1:manifestacao" }] };
        },
      },
    });
    assert.equal(result.created.length, 1);
    assert.deepEqual(result.readMarked, ["pub-1"]);
    assert.equal(result.errors.length, 0);
  } finally {
    globalThis.fetch = originalFetch;
    for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
    Object.assign(process.env, previous);
    await rm(directory, { recursive: true, force: true });
  }
});
