---
name: victor-pje-filing
description: Export Victor's Advbox 00-PROTOCOLAR files as a filing input folder, prepare it, and create or review a TRF5 PJe 1G process draft through the PJe MCP. Use for initial-filing preparation and Não Protocolados drafts; never for final Protocolar submission.
---

# Victor: Advbox to PJe draft

Prepare an initial filing for `trf5-1g` only. Victor's office defaults live in
`customers/victor/filing-profile.json` (profile `victor`); the generic filing tools
(`src/jurisync/workflows/filing`) and the PJe adapter do the work. Neither path submits
**Protocolar**. Use `victor-sop-intimacoes` for DJEN-to-Advbox tasks instead.

1. Resolve the intended Advbox customer and lawsuit. Gather the author's CPF
   and PJe registry name, jurisdiction, judicial class, and claim value from
   the lawsuit and petition. For Assuntos, read
   [references/subjects.md](references/subjects.md) and check the repository's
   `/home/gabriel/orca/jurisync/customers/victor/README.md` for this exact lawsuit ID or CNJ number before
   choosing the ordered `subjects` list. The Advbox `Ação` and codes filed in
   other lawsuits are candidates, not defaults. PJe's `subject-options` lookup
   requires an existing draft; use it after `stage: create` if an advance
   check is needed. The `create-fill` path itself matches each code and name
   against PJe's lookup before adding it. Ask for missing or conflicting
   facts; never substitute another case's mock values.
   Victor files as the user logged in to PJe, so he is on every draft already; never list him. Graziele is
   the extra attorney: put her in the case facts as
   `"attorneys": [{"cpf": "<her CPF>", "name": "GRAZIELE ALVES DA SILVA", "oabNumber": "48647", "oabState": "PE"}]`.
   Her CPF is not stored in the repository or the environment: take it from this conversation, or ask the user once.
   Leave `attorneys` out only if the user says she is not on this case.
   If the author is a minor or acts through someone (mother, father, guardian, curator, attorney-in-fact), which the
   petition usually states, add `author.representative` with `cpf`, `name` and `kind` (`mae`, `pai`, `tutor`,
   `curador`, `procurador` or `outro`), and `author.birthDate` when known. Prepare holds the run for review when the
   petition or the birth date point to a representative and none is given, or when the representative's CPF is not in
   the petition; if the petition only seems to say so and the user confirms the author acts in person, set
   `author.selfRepresented: true`. The fill cannot yet add the representative in PJe: `pje_draft_status` lists it under
   `manualSteps`, and you must tell the user to add it in the Partes tab before protocol. This office always includes
   `Juízo 100% Digital`; the workflow sets that priority regardless of
   any priority text in case facts. Do not ask for or edit that field.
   Compare any address in the petition with the Advbox customer record. If
   they agree, include it as optional `author.address` in case facts, with
   `cep` and `numero` when available. The adapter reuses an existing PJe
   address with the same CEP, or adds one through PJe's CEP lookup and confirms
   the selected CEP after saving the party. Surface conflicting addresses for
   user review. A missing address does not block a draft while PJe accepts it
   as optional.
2. Choose a new alphanumeric `runId` and a private run directory under the
   PJe MCP's configured `JURISYNC_FILING_DIR` (runs live in its `runs/`, default
   `~/.local/share/jurisync/filing/runs`). Use a root writable in the current
   workspace; the agent and MCP must use the same root.
   Write reviewed facts to a private JSON file, then, from the repo root, export the ADVBOX folder and prepare it
   (two separate steps: the filing never talks to ADVBOX):
   `.venv/bin/python -m jurisync.connectors.advbox.filing_folder --customer <id> --out <input-folder>` and
   `.venv/bin/python -m jurisync.workflows.filing prepare --profile victor
   --from <input-folder> --facts <facts.json> --out <run-directory>`.
   Do not create a separate `documents.json`; the prepare step copies the folder into the run's `inbox/` and
   classifies the files, then writes `review.json` and, when ready, `payload.json` plus the assembled PDFs in
   `documents/` (numbered in filing order) and `manifest.json`. Paths in them are relative to the run folder.
   Treat all of these as script outputs; do not write, rename or replace them manually.
   `review.json` must contain a `files` array for the `finalize` command.
   Read `review.json`: inspect every file, suggested PJe type, fallback,
   duplicate, and size/split flag. If needed, use the same module's `finalize`
   command with `--run <run-directory>` and `--overrides` to resolve selections (`overrides` accepts only `role`, `type` and
   `description`). Continue only when it emits `ready: true` and `payload.json` exists.
   A `payload.json` is never approved by itself, even when prepare wrote it with no review items.
3. Show the user the reviewed facts, selected subjects, address status,
   document list and classifications, and unresolved limitations. Use
   `pje_draft_options` with `stage: classification` and the run ID to check
   current PJe choices. Search
   **Não Protocolados** with `pje_draft_search` using the author's CPF
   before creating a new draft; show possible existing drafts for review.
   Only after the user explicitly approves that summary, record it with
   `.venv/bin/python -m jurisync.workflows.filing approve --run <run-directory>` (or `pje_filing_approve`).
   Never approve on your own. Any later prepare or finalize drops the approval; show the new summary and ask again.
4. When the user has asked to create the draft, call `pje_draft_create` and the same run ID. Draft writes require
   `PJE_ALLOW_DRAFT_WRITES=true` in the PJe MCP process and refuses without an approval that matches the current
   payload and files ("Document changed since approval" means a file changed: finalize and ask again). The adapter writes
   `checkpoint.json` immediately after **Incluir**. If filling later fails,
   inspect that checkpoint and the live draft. Correct case facts and
   regenerate `payload.json` with `finalize --run <run-directory>
   --facts <facts.json>` rather than editing it by hand, then show it and get a new approval. With a draft in place
   only case facts may change; a different document selection needs a new `runId`. Resume with the
   targeted `pje_draft_fill` stage (`characteristics`, then `documents`, for
   a characteristics failure); do not repeat `create` or `create-fill`.
   If fill stops because a document on the draft has other content than the approved file (or no upload record),
   it was not uploaded again: ask the user to remove that document from the draft in PJe, then run `fill` again.
5. Call `pje_draft_options` with `stage: pending` to confirm the draft appears
   in **Não Protocolados**. Return the draft ID/URL and report which fields and
   PDFs were actually saved. Have the user review the draft in PJe before any
   final filing. There is no Protocolar tool in this workflow.

The filing-run root must be the same for the script and PJe MCP process.

## Without a shell: the generic `filing` tools

A chat session that only has MCP tools cannot run the `prepare` command in a terminal. The `filing` MCP does the same
work and never takes file paths. It is not Victor-specific: every filing tool takes a `profile` name, and **this skill
always passes `profile: "victor"`**. Steps 1, 3, 4 and 5 above are unchanged; replace step 2 with:

1. `pje_filing_profiles` lists the available profiles (confirm `victor` is there). `pje_filing_subjects` with
   `profile: "victor"` (and optionally the lawsuit ID or CNJ) returns the reference subject codes and the README record
   of what was filed for that exact case. They are candidates for the user to confirm, never defaults.
2. Get the input folder, then call `pje_filing_prepare` with `profile: "victor"`, a new `runId`, the reviewed facts and
   `sourceFolder` (the folder's absolute path). For Victor's usual case, `advbox_export_filing_folder` with the ADVBOX
   `customerId` downloads the `00 - PROTOCOLAR` folder and returns `sourceFolder` (its `sources.json` is already
   written). For documents you gather yourself through the Drive, GERID or other connectors, save them in one local
   folder and write a `sources.json` at its root
   (`{"version": 1, "files": [{"file": "<path in the folder>", "origin": "drive|gerid|advbox|...", "originId",
   "originPath", "originalName", "modifiedAt", "link"}]}`; only `file` and `origin` are required) so every file's
   origin is on record; files it leaves out are recorded as `local`. No symlinks. Photos (JPG, PNG) become PDFs; a HEIC
   comes back for review (export it as JPG or PDF). Show each file's `origin` to the user in the review. Facts may
   be partial: missing ones come back as review items, and `candidates` lists the claim values and CPFs the petition
   states. Document text is not sent to Jev unless `useJev` is true.
3. Resolve every item in `review`: each file's `id` is the key for `overrides` (`{role, type, description}` and nothing
   else, or `{role: "skip"}` for a duplicate such as a `.docx` copy of the petition). `pje_filing_review` shows the run
   again, including whether the current payload is `approved`.
4. `pje_filing_build` with `profile: "victor"` and `overrides` writes `payload.json` only when the checks pass (one main
   petition; the claim value and CPF appear in the petition). Show its summary to the user. Only when they explicitly
   approve it, call `pje_filing_approve` with the `runId`; never on your own. A new build needs a new approval.
5. `pje_draft_create` (refused without that approval) starts the fill in the background. Follow it with `pje_draft_status`, and use
   `checkPje: true` to confirm the draft in PJe; `pje_draft_fill` continues a draft that stopped (it skips what is
   done). `pje_draft_delete` removes a draft by number (it asks first).
6. After the user protocols (or drops) the draft, `pje_filing_cleanup` with the `runId` removes the run and its input
   folder. A run whose draft is still in Não Protocolados is kept unless the user explicitly confirms.

The filing root (`JURISYNC_FILING_DIR`) is shared by both servers, so the same `runId` works in each. Old tool names
(`filing_prepare`, `filing_finalize`, `pje_draft_write`...) still work for a while; use the new ones.
