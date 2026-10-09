---
name: victor-pje-filing
description: Prepare Victor's Advbox 00-PROTOCOLAR files and create or review a TRF5 PJe 1G process draft through the PJe MCP. Use for initial-filing preparation and Não Protocolados drafts; never for final Protocolar submission.
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
   Graziele is the attorney added to the author; the logged-in Victor is not
   the `attorney` payload entry. Her CPF comes from the private
   `VICTOR_PJE_GRAZIELE_CPF` setting. This office always includes
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
   PJe MCP's configured `JURISYNC_FILING_DIR`. Use a root writable in the
   current ChatGPT workspace; the agent and MCP must use the same root. Do not
   assume the fallback `~/.local/share/jurisync/filings` is writable.
   Write reviewed facts to a private JSON file, then run
   `.venv/bin/python -m jurisync.workflows.filing prepare --profile victor
   --customer <id> --facts <facts.json> --out <run-directory>` from the repo root
   (it loads the repo `.env` itself).
   Do not create a separate `documents.json`; this script downloads and
   classifies the files, then writes `review.json` and, when ready, `payload.json`.
   Treat both files as script outputs; do not write or replace them manually.
   `review.json` must contain a `files` array for the `finalize` command.
   Read `review.json`: inspect every file, suggested PJe type, fallback,
   duplicate, and size/split flag. If needed, use the same module's `finalize`
   command with `--review` and `--overrides` to resolve selections. Continue only when it emits
   `ready: true` and `payload.json` exists.
3. Show the user the reviewed facts, selected subjects, address status,
   document list and classifications, and unresolved limitations. Use
   `pje_draft_inspect` with `stage: classification` and the run ID to check
   current PJe choices. Search
   **Não Protocolados** with `pje_search_pending_drafts` using the author's CPF
   before creating a new draft; show possible existing drafts for review.
4. When the user has asked to create the draft, call `pje_draft_write` with
   `stage: create-fill` and the same run ID. Draft writes require
   `PJE_ALLOW_DRAFT_WRITES=true` in the PJe MCP process. The adapter writes
   `checkpoint.json` immediately after **Incluir**. If filling later fails,
   inspect that checkpoint and the live draft. Correct case facts and
   regenerate `payload.json` with `finalize --review <run-directory>/review.json
   --facts <facts.json>` rather than editing it by hand. Resume with the
   targeted `pje_draft_write` stage (`characteristics`, then `documents`, for
   a characteristics failure); do not repeat `create` or `create-fill`.
5. Call `pje_draft_inspect` with `stage: pending` to confirm the draft appears
   in **Não Protocolados**. Return the draft ID/URL and report which fields and
   PDFs were actually saved. Have the user review the draft in PJe before any
   final filing. There is no Protocolar tool in this workflow.

The filing-run root must be the same for the script and PJe MCP process.

## Without a shell: the generic `filing` tools

A chat session that only has MCP tools cannot run the `prepare` command in a terminal. The `filing` MCP does the same
work and never takes file paths. It is not Victor-specific: every filing tool takes a `profile` name, and **this skill
always passes `profile: "victor"`**. Steps 1, 3, 4 and 5 above are unchanged; replace step 2 with:

1. `filing_profiles` lists the available profiles (confirm `victor` is there). `filing_subjects` with
   `profile: "victor"` (and optionally the lawsuit ID or CNJ) returns the reference subject codes and the README record
   of what was filed for that exact case. They are candidates for the user to confirm, never defaults.
2. `filing_prepare` with `profile: "victor"`, a new `runId`, the ADVBOX customer ID and the reviewed facts. Facts may
   be partial: missing ones come back as review items, and `candidates` lists the claim values and CPFs the petition
   states. Document text is not sent to Jev unless `useJev` is true.
3. Resolve every item in `review`: each file's `id` is the key for `overrides` (`{role, type}`, or `{role: "skip"}`
   for a duplicate such as a `.docx` copy of the petition). `filing_review` shows the run again.
4. `filing_finalize` with `profile: "victor"` and `overrides` writes `payload.json` only when the checks pass (one main
   petition; the claim value and CPF appear in the petition). Show its summary to the user before creating the draft.
5. `pje_draft_write` with `create-fill` starts the fill in the background. Follow it with `pje_draft_status`, and use
   `checkPje: true` to confirm the draft in PJe. `pje_draft_delete` removes a draft by number (it asks first).

The filing root (`JURISYNC_FILING_DIR`) is shared by both servers, so the same `runId` works in each.
