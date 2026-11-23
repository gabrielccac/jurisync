# ADVBOX: lawsuit stage (Fase / Etapa) and the 2026-11-23 live validation

How the ADVBOX lawsuit screen changes a lawsuit's stage, how the connector reproduces it
(`advbox_update_lawsuit_stage`, and `stageId` on `advbox_create_task`), and the live run of the whole connector on
Victor's office on 2026-11-23. Ids of the office's own stages are quoted because the tools need them; no customer data.

## Two fields: stage (`steps_id`) and sub-stage (`stages_id`)

`GET /lawsuits/<lawsuitId>/edit` (read-only) holds one form, `<form method="POST" action=".../lawsuits/<id>"
class="lawsuit-data">` with hidden `_method=PUT` and `_token`. The stage is two selects:

| field | screen label | options | notes |
| --- | --- | --- | --- |
| `steps_id` (class `list-stages no-trigger`) | **Fase** | 9 office-wide stages: 9 Marketing, 1 Negociação, 6 Consultoria, 2 Administrativo, 3 Judicial, 4 Recursal, 5 Execução/cobrança, 7 Rh/financeiro, 8 Arquivamento | the same ids and labels as `advbox_task_filters` `stages` (the Tarefas filter) |
| `stages_id` (class `response-stages`) | **Etapa** | the sub-stages of the selected stage (e.g. Judicial: 8144 ANÁLISE/PETIÇÃO INICIAL, 7092 AJUIZADO/EM ANDAMENTO, …) | may be empty (`data-allow-clear`) |

The lawsuit report (`advbox_lawsuit_report`) shows the sub-stage as `info.Fase` and logs every change in the task
history as `Alterou o processo para a fase <STAGE>/<SUB-STAGE>`.

When the stage select changes, the screen (`app.js`, handler on `select.list-stages`) loads the sub-stages with
`POST /stages/<stageId>` (CSRF header, no body) → JSON list `[{id, steps_id, stage, workflows_id, is_active, …}]`,
replaces the options and so selects the first one. A sub-stage with `workflows_id` set is linked to a workflow: the
kanban move offers "Sim, atribuir tarefas!" for it. The connector reports it as `workflowId` and warns; it never
starts the workflow.

## How the screen saves: the whole form

There is no stage-only endpoint on this screen. "Atualizar dados do processo" submits the `lawsuit-data` form with the
generic popup handler: `$.ajax({type: POST, url: form.action, data: form.serialize()})`, i.e. **every field** of the
lawsuit (parties, group, type, numbers, dates, fees, responsible, stage, sub-stage) as form-encoded pairs. The answer
is JSON (`url`, `autotab`, `update_stage`, …). Other places that move a lawsuit, not used by the connector:

- CRM kanban: `POST kanban/toggle` with `id`, `page=process`, `origin=_<subStage>`, `destiny=_<subStage>`,
  `sorted[]` (board order). Moves between sub-stage boards of one stage and may prompt to start a workflow.
- Task form: a task template that belongs to another stage shows "changing-step" with hidden `steps_id` and a
  `stages_id` select, sent with `POST /posts`. The connector's create body keeps `steps_id` empty and moves the
  lawsuit afterwards instead (below), so the move is confirmed and reported separately.

## Connector

- `parse_form` serializes a form like jQuery's `serialize` (enabled named inputs; checkboxes/radios only when
  checked; the selected options of each select, a single select without a selection sends its first option; textareas
  without the leading newline). Unchecked `send_sms` / `send_email` (notify the client) are therefore never sent.
  `parse_lawsuit_form` adds the current stage/sub-stage and the options; `build_lawsuit_stage_body` replaces only
  `steps_id` and `stages_id` (the session adds its own `_token`).
- `advbox_update_lawsuit_stage(lawsuitId, stageId, subStageId="", dryRun=false)` (write, `ADVBOX_ALLOW_WRITES`,
  listed in `registry.py` `write_tools`):
  1. under the office lock, read the form; refuse a `stageId` the form does not offer and a `subStageId` that
     `POST /stages/<stageId>` does not list (inactive entries left out) — before anything is sent;
  2. without `subStageId`: a new stage gets its first sub-stage (what the screen selects, `subStageDefaulted: true`,
     options returned), the same stage keeps the current one; no change → nothing is sent;
  3. `POST /lawsuits/<id>` with the full form; a 4xx → "the lawsuit stage was not changed"; a timeout/5xx is never
     replayed;
  4. the form is read again: the new stage must be there, otherwise `UnconfirmedWriteError` ("NOT confirmed");
     every other field is compared with the form before the save and any difference is a warning.
  Returns `previous` and `new` `{stageId, stage, subStageId, subStage}`; calling again with `previous` undoes it.
- `advbox_create_task(..., stageId, subStageId)`: the stage is validated (a dry run of the above) before the task is
  written; after the task exists the lawsuit is moved and the result carries `stage` (previous/new). A failed move
  never hides the new `postId`: it is reported in `stage.error` and `warnings`.
- 413: ADVBOX answered `413` to `GET /lawsuits/<id>/edit` after about eight opens of the same lawsuit in a few
  seconds, and 200 again shortly after. The form read waits 20 s and retries (4 tries), so the check after a save
  is not lost to it.
- `advbox_delete_task` / `advbox_complete_task` on a **completed** task: ADVBOX answers 403 to its edit form and
  refuses the delete itself (`Você não tem permissão para excluir esta tarefa.`). The tools now say so and send
  nothing. The screen reopens a task with `POST /posts/status/<id>` `field=completed&value=0` (used once, by hand,
  in the cleanup below); the connector does not expose it.

## Live run, Victor's office, 2026-11-23 (stored session, no login)

Reads, through the MCP layer (`server.call_tool`), all ok:

| tool | arguments | result | time |
| --- | --- | --- | --- |
| ensure_session | – | authenticated | 1.3 s |
| list_members | – | 75 users, 10 groups | 0.4 s |
| task_filters | – | 298 task types, 9 stages, 14 groups, 177 lawsuit types | 0.4 s |
| list_workflows | – | 43 | 0.3 s |
| workflow_tasks | 7128 | 7 steps | 1.8 s |
| list_customers | page 1, size 5 | 5 of 26 976 | 1.6 s |
| list_lawsuits | created 2026-11-01..2026-11-23, desc | 5 of 747 | 1.8 s |
| lawsuit_report | test lawsuit, caps | 26 tasks matched, 5 returned | 1.2 s |
| list_lawsuit_partners | test lawsuit | 0 | 0.5 s |
| search_tasks | 2026-11-17..23, one responsible, stage 3 | 21 | 0.7 s |
| activity_details | one task of that list | ok (lawsuit, CNJ, parties) | 0.3 s |
| list_publications | 2026-11-20..23, size 5 | 5 of 131 | 0.4 s |
| list_files | one customer, root / one folder | folder tree with counts / 4 files | 0.3 / 0.5 s |
| download_file | one 70 KB PDF | 3 pages, text extracted; local copy deleted | 0.7 s |

Writes, each undone right away, on the office's own test record (customer `TESTE` 7470517, lawsuit 6725946, created
2024 as a test), with the integration user (CONTROLADORIA, 216759) as the only responsible:

- folder `Jurisync teste 20261123150820` created → one 603-byte PDF uploaded (fileId 43698403, `listed: true`) →
  file and folder deleted with the requests of `advbox-folders-upload.md` §4 → customer root empty.
- task 303177323 created (DILIGÊNCIA, 2026-12-15, one attachment) → `advbox_update_task` → replacement 303177366 →
  `advbox_complete_task` confirmed → `advbox_delete_task` **failed** (403 on the completed task's form; a direct delete
  was refused with "Você não tem permissão para excluir esta tarefa.") → reopened with `posts/status` value 0 →
  `advbox_delete_task` ok → `search_tasks` pending and completed (Nov–Dec) find nothing.
- stage: Negociação/1 - ATENDIMENTO (1/7090) → Judicial/ANÁLISE/PETIÇÃO INICIAL (3/8144), confirmed by the form and
  the report → back to 1/7090, confirmed, and every other form field identical to before.
- `advbox_create_task` with `stageId=3, subStageId=8144`: task 303181131 created and the lawsuit moved (previous 1/7090,
  new 3/8144) → stage back to 1/7090 (fields identical) → task deleted; no pending or completed test task left.

Left behind: nothing but the lawsuit history lines ADVBOX writes itself (four "Alterou o processo para a fase …"
entries and the deleted tasks' log on the test lawsuit).
