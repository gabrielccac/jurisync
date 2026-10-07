# Tramitação Inteligente

Brief reference for the management-tool integration. Tramitação belongs to the
`tools` group, separate from the PJe/Eproc public-portal adapters.

## Current decision

Use Tramitação's native OAuth MCP for agent access. Do not duplicate its full
REST surface in a local MCP unless a concrete capability gap appears.

Keep `TRAMITACAO_API_KEY` available for backend jobs that need deterministic
REST access, downloads, synchronization, or webhooks. It is not required by the
native MCP connector.

Authoritative references:

- [REST catalog](https://planilha.tramitacaointeligente.com.br/api/docs.json)
- [Layered endpoint documentation](https://planilha.tramitacaointeligente.com.br/api/docs)
- [Native MCP guide](https://planilha.tramitacaointeligente.com.br/paginas/conectores-mcp-claude-e-chat-gpt)
- MCP endpoint: `https://planilha.tramitacaointeligente.com.br/mcp`

## Relevant capability groups

The public REST catalog currently documents 85 operations covering clients,
users, activities, notes, processes, parties, tags, publications, files,
document generation, signatures, and webhooks.

For our agent workflows, map those capabilities into a small number of
task-oriented operations rather than exposing every endpoint:

- `search_process`: CNJ, client, party, lawyer, court, tag, status, and date filters.
- `get_process_context`: process details, parties, linked clients, tags, and status.
- `get_process_activity`: paginated imported movements.
- `get_publications`: publications by process, CNJ, party, lawyer, date, or status.
- `get_client_context`: client data, linked processes, notes, activities, and files.
- `get_process_documents`: identify files first, then download/read selected documents.
- `create_or_update_record`: explicit, opt-in write workflow.

The native MCP advertises additional product capabilities, including agenda,
document editor, jurisprudence, finances, e-mails, signatures, recordings,
and analyses. Exact tool names and schemas must be checked after customer
authorization; this file intentionally does not copy them.

## Source routing

- Use Tramitação for internal management records and account-owned workflow data.
- Use PJe/Eproc for public-portal searches, court events, and portal documents.
- Use the CNJ process number as the primary cross-source correlation key.
- Prefer read-only operations; require explicit user intent before mutations.
- Fetch document bytes only after identifying the relevant document.

Typical orchestration:

```text
search → resolve process → fetch context → fetch movements/publications
      → fetch selected documents only when needed
```

## Customer-alignment questions

Before creating the skills, confirm:

1. Which customer workflows are actually needed: process monitoring, client
   management, agenda, notes, publications, files, signatures, or finance?
2. Which actions are read-only and which writes should be enabled?
3. Should the agent read full document contents or only list/download metadata?
4. Are Tramitação and PJe/Eproc expected to be reconciled in one answer?
5. Are webhooks/background synchronization required, or is interactive MCP access enough?

Do not add guessed routes, credentials, or a raw endpoint-by-endpoint wrapper.
