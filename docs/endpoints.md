# Endpoint inventory and evidence

Consolidated 2026-09-09. This is an inventory of recovered source code plus a
read-only audit of the public Juridiq reference. The public sitemap currently
lists 143 operations (59 GET, 32 POST, 27 PATCH, 25 DELETE); this is not a newly
executed certification of every route or a complete SDK.
Earlier ad hoc create/list test commands were not saved as a test suite here.
No missing request payload or response schema has been invented.

## Juridiq

Shared helper: integrations/juridiq/session.mjs.
Named operations: integrations/juridiq/client.mjs.
Login defaults to https://www.api.juridiq.com.br/auth/login.
API calls default to https://api.juridiq.com.br.
Older Desktop scripts used https://www.api.juridiq.com.br for data calls too;
preserve that distinction when comparing results, rather than assuming hosts are aliases.
Authentication: Authorization: Bearer <cached token>; no API key.

| Method / path | Purpose | Evidence and current limitation |
| --- | --- | --- |
| POST /auth/login | Email/password -> token | Implemented in shared helper; earlier successful login reported. Offline cache/refresh checks included. |
| GET /user/me | Current user/account | Earlier successful test reported in conversation. |
| POST /generic-tables | Task table query | Exact body recovered from generic-tables.js and check_sheet_task_mapping.js. Earlier populated task list reported. This POST is a read query. |
| GET /task/:id | Task details and linked lawSuit | Implemented in recovered check_sheet_task_mapping.js; accesses task fields at response root. |
| GET /task/ | Task collection | Implemented with the documented filters for process, dates, status, archive state, responsible, tags and columns. |
| GET /task/kanban | Kanban task list | Implemented as `listKanbanTasks`; returns tasks grouped by Kanban status and supports board-oriented filters. |
| GET /history/:id | Task/entity history | Implemented as `getTaskHistory`; returns actions, dates, users and Kanban transitions. |
| GET /comment/?taskId=... | Task comments | Implemented as `listTaskComments`; returns comments and replies. |
| GET /task/task-templates?page=1&limit=100 | Task templates/models | **Live read-only verification 2026-09-08:** HTTP 200, 33 records, one page. Raw response saved as `data/juridiq-task-templates-2026-09-08.json` (ignored). |
| GET /lawSuit?page=1&limit=10&processNumber=... | Process lookup | Implemented in recovered task-preparation script; payload.data, then exact processNumber match. |
| POST /lawSuit/ | Process creation | Implemented as `createLawSuit`; `serviceStep`, `responsibleIds`, and `persons` are required. `responsibleIds`/`tags` use UUID arrays and `persons[].personOrigin` is exactly `Cliente` or `Requerida`. The MCP validates the documented fields and rejects extras. |
| PATCH /lawSuit/:id | Process update | Implemented as `updateLawSuit`; the MCP validates the documented fields and rejects extras. |
| DELETE /lawSuit/ | Process deletion | Body is `{ "id": ["law-suit-id"] }`; wrapper deletes only an explicitly supplied ID and MCP requires explicit account selection, confirmation and the global write opt-in. |
| GET /user/with-pending-points?type=all | Responsible users | Recovered task-preparation script; optional start/end, returns payload.data. |
| POST /task/ | Task creation | `title`, `priority`, `columnId`, and `initialDate` are required. `columnId`, `responsiblesId`, `tags`, `personIds`, and `validatorsId` use account UUIDs; the MCP rejects `responsibleIds`, unknown fields, and invalid IDs before the request. |
| PATCH /task/:id | Task update | Implemented with the documented task fields; MCP requires explicit account selection, confirmation and the global write opt-in. |
| DELETE /task/ | Task deletion | Body is `{ "id": ["task-id"] }`; wrapper deletes only an explicitly supplied ID and MCP requires explicit account selection, confirmation and the global write opt-in. |
| POST /event/ | Calendar event creation | Verified route and schema; MCP requires explicit account selection, confirmation and the global write opt-in. |
| PATCH /publication/read-movements-many | Mark publications read | Verified route/schema; MCP requires explicit account selection, confirmation and the global write opt-in. |
| GET /publication?page=1&limit=50 | Publications | Implemented with native search, diary, handled-state, monitored-term and date filters; response metadata is preserved by the MCP tool. |
| GET /publication/:id | Publication detail | Implemented as `getPublication`; returns content, diary, dates, process and read state. |
| GET /lawSuit/movements/:lawSuitId | Juridiq lawsuit movements | Implemented as `listLawSuitMovements`; returns content, date, instance and tag details for publication comparison. |
| GET /event/ | Calendar event list | Implemented with process/person/responsible/date/status/type/tag filters. |
| GET /event/:id | Calendar event detail | Implemented as `getEvent`; returns date, hours, linked process/person and responsibles. |

The earlier discussion also covered tags, task templates,
publication handling, files, and calendar/events. Their complete internal paths,
payloads, and response fixtures were not recovered in the code inventory. They
remain mapping work; a skill connector operation name is not an HTTP route.

Public documentation is a comparison reference, not proof a route works with
internal bearer authentication:
- https://juridiqsoftware.mintlify.app/api-reference/tasks/listar-tarefas-no-kanban
- https://juridiqsoftware.mintlify.app/api-reference/files/listar-arquivos-de-um-processo

### Recovered task query shape

```json
{
  "module": "task",
  "genericTableId": "",
  "pagination": { "page": 1, "limit": 30 },
  "filters": {
    "folderId": "<account folder>",
    "boardId": "<account board>",
    "isArchived": false,
    "responsible": ["<optional user filter>"]
  }
}
```

Read page/limit deliberately. The old mapping script reads payload.data for rows
and obtains task details separately for lawSuit.processNumber, responsibles,
tags, taskKanbanColumn, and dates. Account IDs are not portable.

### Recovered task creation draft

The historical script built these fields:
officeId, description, title, status, columnId, priority, initialDate, finalDate,
lawSuitId, tags, responsiblesId, personIds, mentionedUserIds.
It uses JSON.stringify(html) for description and a seven-day administrative
due date. That historical workflow is not a legal-deadline calculator and must
not supply default deadlines for semantic publication processing.

The original skill separately reports a description truncation workaround.
Neither encoding convention is adopted as a universal rule here; verify with
create + retrieve on the explicitly selected account when implementing task writes.

### Live task-template response and mapping

The verified route is `GET /task/task-templates?page=1&limit=100` and returns:

```json
{
  "data": [
    {
      "id": "template UUID",
      "title": "exact title with placeholder",
      "description": "HTML template or null",
      "points": 0,
      "columnId": "column UUID",
      "priority": "Baixa|Alta|...",
      "daysForDue": 5,
      "daysForStart": 1,
      "createSheet": false,
      "estimatedTimeMinutes": 0,
      "officeId": "office UUID",
      "isBusinessDay": false,
      "responsibles": [{"id": "user UUID", "name": "...", "email": "..."}],
      "subtasks": [],
      "tags": [{"id": "tag UUID", "name": "...", "color": "..."}]
    }
  ],
  "totalResults": 33,
  "totalPages": 1
}
```

The skill's semantic result is a task type/title; it is not itself a template
UUID. The live mapping captured for this account is:

| Skill task type | Exact live template | Template ID |
| --- | --- | --- |
| Réplica BPC | `Réplica BPC - (Nome Cliente)` | `db526927-0814-4a90-97f6-d1119dcae719` |
| Aceitação de acordo | `Manifestação Aceitação Acordo - (Nome Cliente)` | `88c830ab-23e8-461b-998b-d04b7a70a549` |
| Manifestação genérica | `Manifestação - (Nome Cliente)` | `be6f1dd5-70c7-4f4e-8281-a7705b5032c6` |
| Perícia médica | `Comunicação Perícia Médica Federal - (Nome Cliente)` | `7df71237-53d5-4075-8ab5-674a8950483e` |
| Perícia social | `Comunicação Perícia Social - (Nome do Cliente)` | `dd9641cf-37bd-4a88-93d8-f20b13a1e4b6` |
| Remarcação judicial | `Remarcação Perícia Judicial - (Nome Cliente) ` | `6f9a132d-43e8-42f7-8e6b-ea23c77a4c19` |
| Impugnação de laudo | `Impugnação ao Laudo - (Nome Cliente)` | `0a28a288-2025-4dc3-afaf-50736f9ec974` |
| Laudo favorável | `Manifestação Laudo Favorável - (Nome do Cliente) ` | `efc1e62e-b1fb-4aa5-9a6c-64042b085ebf` |
| Sentença favorável | `Comunicação Sentença Favorável - (Nome do Cliente)` | `0623307c-3696-4f3e-b15e-663f4c127ba9` |
| Acórdão favorável | `Comunicação Acórdão Favorável - (Nome do Cliente)` | `cbdd0122-97af-4bf9-bf7a-f2db556b1068` |
| Expedição RPV | `Expedição RPV - (Nome Cliente)` | `0c2c0eba-baee-4dd1-9c1d-8d64d325180f` |
| Recurso inominado | `Recurso Inominado BPC - (Nome Cliente)` | `e4826d68-8357-4550-9db8-255d3f6e71db` |
| Apelação BPC | `Apelação BPC - (Nome do Cliente)` | `1cfa67f0-d424-469b-a30f-58b5c38d90ef` |
| Apelação MS | `Apelação MS - (Nome Cliente)` | `ee5df941-ac77-4644-a04f-1ed94676f0a3` |
| Embargos de declaração | `Embargos de Declaração - (Nome Cliente)` | `01becef6-33b4-47ee-9882-1af815640fed` |

The title punctuation, placeholder form, and trailing spaces are part of the
live values. A writer should select the exact live title, substitute only the
client placeholder, and copy `columnId`, `priority`, responsible IDs and tag IDs
from that same record. `daysForDue`/`daysForStart` are defaults, not proof of a
legal deadline. Several other templates were returned but are administrative or
document-flow templates and are not selected by the publication classification
unless the skill is explicitly changed.

The response's `responsibles` and `tags` are expanded objects; the task-create
shape expects their ID arrays (`responsiblesId` and `tags`). The template
description is an HTML starting point, while the publication-specific description
must add grounded act/process/deadline details. `officeId` must also be retained.
The create/update/delete wrappers live in `integrations/juridiq/client.mjs` and
require explicit account selection, confirmation and the global write opt-in.

## PJe TRF1: 1G and 2G

Hosts: pje1g.trf1.jus.br and pje2g.trf1.jus.br.
Shared SSO host: sso.cloud.pje.jus.br.
Authentication is session cookies obtained through username/password + TOTP.

| Request | Implementation |
| --- | --- |
| GET /pje/login.seam; follow SSO redirect | auth.login_session |
| POST discovered loginForm action | auth.login_session |
| POST discovered kc-otp-login-form action | auth.login_session |
| GET/POST /pje/Processo/ConsultaProcesso/listView.seam | trf1_1g.search_process_1g / trf1_2g.search_process |
| GET /pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam | Selected process ID + fresh ca value from JSF response |
| GET discovered documento/download/TRF1/... link | trf1_2g.download_documents using authenticated session |
| GET /pje/QuadroAviso/listViewQuadroAvisoMensagem.seam | Imported 1G watcher session health probe |
| GET /pje/Painel/painel_usuario/advogado.seam | Imported notification exploration |
| GET /pje/Push/listView.seam?iframe=true | Watched-process registry; not publication text |

Search form fields: javax.faces.ViewState and the six fPP:numeroProcesso fields
(numeroSequencial, numeroDigitoVerificador, Ano, ramoJustica,
respectivoTribunal, NumeroOrgaoJustica). Source retains the previously observed
JSF action IDs; portal changes can invalidate them.

1G uses initialization fPP:j_id550 and search fPP:j_id494.
2G uses search fPP:j_id495. Selection controls and ca tokens are extracted from
responses. Document URLs come from the returned detail HTML.

Both implementations were ported from the user's previously tested General
project. Offline checks verify integration and parsing; no live revalidation was
performed during consolidation. Expedientes expansion remains exploratory and may
be partial; it is not included in the default process-retrieval path.
