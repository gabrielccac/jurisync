# Endpoint inventory and evidence

Consolidated 2026-09-09. This is an inventory of recovered source code plus a
read-only audit of the public Juridiq reference. The public sitemap currently
lists 143 operations (59 GET, 32 POST, 27 PATCH, 25 DELETE); this is not a newly
executed certification of every route or a complete SDK.
Earlier ad hoc create/list test commands were not saved as a test suite here.
No missing request payload or response schema has been invented.

## Juridiq

Client: src/jurisync/connectors/juridiq/api.py (bearer session cache and named operations).
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
| DELETE /lawSuit/ | Process deletion | Body is `{ "id": ["law-suit-id"] }`; wrapper deletes only an explicitly supplied ID and exists only when the connector write capability is enabled. |
| GET /user/with-pending-points?type=all | Responsible users | Recovered task-preparation script; optional start/end, returns payload.data. |
| POST /task/ | Task creation | `title`, `priority`, `columnId`, and `initialDate` are required. `columnId`, `responsiblesId`, `tags`, `personIds`, and `validatorsId` use account UUIDs; the MCP rejects `responsibleIds`, unknown fields, and invalid IDs before the request. |
| PATCH /task/:id | Task update | Implemented with the documented task fields; available only when the connector write capability is enabled. |
| DELETE /task/ | Task deletion | Body is `{ "id": ["task-id"] }`; wrapper deletes only an explicitly supplied ID and exists only when writes are enabled. |
| POST /event/ | Calendar event creation | Verified route and strict MCP schema; available only when writes are enabled. |
| PATCH /publication/read-movements-many | Mark publications read | Verified route/schema; available only when writes are enabled. |
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
The create/update/delete wrappers live in `src/jurisync/connectors/juridiq/api.py` and
are registered only with the connector-level `JURIDIQ_ALLOW_WRITES=true` opt-in.

## INSS PAT / GERID

Observed 2026-09-22 in the sanitized Chrome HAR at
`scripts/atendimento.inss.gov.br.har`. All captured business requests returned
HTTP 200. GERID performs authentication; these business routes are served by
PAT APIs under `https://atendimento.inss.gov.br/apis/`.

The HAR intentionally omits authorization and cookie values. Direct use of the
notification route is separately verified with `Authorization: Bearer <ifs_auth>`;
do not assume every other route has been independently verified outside the
authenticated browser until each receives a direct-request check.

| Method / path | Input | Observed result |
| --- | --- | --- |
| POST `/requerimentosPortalApi/requerimento/ec/notificacao?first=0` | `{tokenReCAPTCHA}` | Notifications; verified by the Playwright test script, not present in this HAR capture. |
| POST `/requerimentosPortalApi/requerimento/ec/tarefa/consulta?first={offset}&pageSize={size}` | Body fields: `cpf`, `nome`, `protocolo`, `status`, `tokenReCAPTCHA`, `dataAtualizacaoInicial`, `dataAtualizacaoFinal`, `situacaoTarefa` | `{tarefas, quantidadeTotalTarefa, imagemCaptcha}`. Each task includes protocol, status, requester, service, creation/update dates and unit. |
| GET `/requerimentosPortalApi/requerimento/ec/tarefa/{protocolo}` | Protocol returned by search | Full task detail, including internal `id`, service, interested parties, comments, attachments, representatives, additional fields, appointments and available-action flags. |
| GET `/consolidadorECApi/pap/metadados?protocolo={protocolo}&sistema=PAT&cpf={cpf}&nome={nome}&async=false` | Protocol plus CPF/name from the active GERID JWT | JSON containing the consolidated process PDF as Base64 in `relatorio`. |
| GET `/requerimentosPortalApi/requerimento/cadastro/servico/{servicoId}/gruposcamposadicionais` | `idServico` from task detail | Additional-field groups; empty array in this sample. |
| GET `/arquivosPortalApi/tarefa/{protocolo}/anexo/{anexoId}` | Protocol plus `anexos[].id` or `anexosIniciais[].id` | Raw Base64 file body. The sample begins with the PDF signature `JVBER` despite an `application/json` content type; filename is supplied by `Content-Disposition`. |
| GET `/tarefasApi/tarefas/{protocolo}/analise-documental` | Protocol | `{definicoesAnexos}`. |
| GET `/seguradoEspecialApi/tarefa/consultar-por-protocolo/{protocolo}` | Protocol | Internal task ID used by the remaining Segurado Especial routes. |
| GET `/seguradoEspecialApi/segurado/consultar-por-tarefa/{tarefaId}` | Internal task ID | Insured-person record and address fields. |
| GET `/seguradoEspecialApi/segurado/consultar-dados-do-interessado/{tarefaId}` | Internal task ID | CPF, name and birth date. |
| GET `/seguradoEspecialApi/auto-declaracao/listar/{tarefaId}` | Internal task ID | Rural-activity declaration periods. |
| GET `/seguradoEspecialApi/tarefa/consultar-adiar-preenchimento-de-autodeclaracao/{tarefaId}` | Internal task ID | Boolean. |
| GET `/seguradoEspecialApi/tarefa/consultar-categorias-atividade-rural/{tarefaId}` | Internal task ID | Indigenous, rural employee and rural producer flags. |
| GET `/seguradoEspecialApi/tarefa/verificar-preenchimento-de-autodeclaracao-concluido/{tarefaId}` | Internal task ID | Boolean. |

Identifier flow:

1. Search returns `tarefas[].protocolo`.
2. Task detail uses that protocol and returns the internal task `id`,
   `idServico`, and attachment IDs.
3. Segurado Especial routes generally use the internal task ID.
4. File download and document analysis use the protocol.

The search and notification POSTs require reCAPTCHA tokens generated for their
respective frontend actions. No refresh-token endpoint was observed. Exact
write routes were not captured and have not been inferred from permission names.
PAT rejects empty strings for the two update-date fields. The MCP mirrors the
captured UI default of today through 180 days earlier at midnight in Sao Paulo;
explicit `YYYY-MM-DD` or ISO timestamps override that range.

## PJe TRF1: 1G and 2G

Hosts: pje1g.trf1.jus.br and pje2g.trf1.jus.br.
Shared SSO host: sso.cloud.pje.jus.br.
Authentication is session cookies obtained through username/password + TOTP.

| Request | Implementation |
| --- | --- |
| GET /pje/login.seam; follow SSO redirect | auth.login_session |
| POST discovered loginForm action | auth.login_session |
| POST discovered kc-otp-login-form action | auth.login_session |
| GET/POST /pje/Processo/ConsultaProcesso/listView.seam | trf1_1g.search_process / trf1_2g.search_process / trf5_1g.search_process |
| GET /pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam | Selected process ID + fresh ca value from JSF response |
| GET selected documento/download/TRF1/... link | `pje.documents.read_selected_documents` using the authenticated session and fresh inventory |
| GET /pje/QuadroAviso/listViewQuadroAvisoMensagem.seam | Imported 1G watcher session health probe |
| GET /pje/Painel/painel_usuario/advogado.seam | Imported notification exploration |
| GET /pje/Push/listView.seam?iframe=true | Watched-process registry; not publication text |

The PJe MCP exposes `pje_list_expediente_counters` for bucket counts only and
`pje_list_watched_processes` for the Push registry and observed completeness
metadata. Neither tool expands Expedientes, replays an unverified page action,
acknowledges notices, changes Push registrations, or returns session-bearing
URLs. Expedientes expansion remains CLI/internal until live evidence proves that
reading it has no acknowledgment or other side effect.

Search form fields: javax.faces.ViewState and the six fPP:numeroProcesso fields
(numeroSequencial, numeroDigitoVerificador, Ano, ramoJustica,
respectivoTribunal, NumeroOrgaoJustica). Controls are discovered from the live
form/AJAX metadata; generated `j_id` values are not part of the contract.
Selection controls and ca tokens are extracted from responses. Document routes
come from the returned detail HTML and are validated before selected reads.

`scope=page` returns the first timeline page, `scope=latest` returns its newest
date bucket, and `scope=all` merges the bounded pages returned by the search layer.
Selected document reads accept one to five inventory IDs, reuse the authenticated
session inside `fetch_process`, and return bounded HTML/PDF text or explicit
statuses such as `ocr_needed`, `access_denied`, and `session_expired`. URLs and
hashes are omitted from outward results.

Both implementations were ported from the user's previously tested General
project. Offline checks verify integration and parsing; no live revalidation was
performed during consolidation. Expedientes expansion remains exploratory and may
be partial; it is not included in the default process-retrieval or MCP path.

## Eproc JFSC and JFRS

One `eproc` MCP server (`jurisync.connectors.eproc.server`) serves every
configured portal (`trf4-1g-sc`, `trf4-1g-rs`) through the tools' `portal` argument. Authentication is a Selenium/TOTP browser login;
the complete authenticated cookie jar is transferred to the read client and is
not persisted by the MCP layer. Requests and document links are allowlisted to
the selected host (`eproc.jfsc.jus.br` or `eproc.jfrs.jus.br`). Credentials and
OTP profiles are independently configured as `JFSC_*` and `JFRS_*` variables (legacy `JRFS_*` is still read).

| MCP tool | Python command | Purpose and limits |
| --- | --- | --- |
| `eproc_list_notices` | `queue --portal jfsc\|jfrs --queue open_deadlines\|pending` | Read one observed citation/intimation queue; no mark-read or export action. |
| `eproc_get_process` | `process --portal jfsc\|jfrs --process-number ... --scope latest\|page` | Read process metadata, parties, events, pagination and document inventory. |
| `eproc_get_process` with `documentIds` | Same command plus repeated `--document-id` | Read up to five IDs already present in the fresh inventory; HTML/PDF extraction is bounded and reports unresolved states. |

Requests are restricted to discovered same-host read URLs. The connector does
not expose arbitrary URLs, writes, filing submission, queue mutation or export.
The portal's observed multi-page, restricted-document, session-expiry and OCR
cases remain explicit diagnostics/deferred coverage rather than inferred data.
