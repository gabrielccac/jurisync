# Pipeline Juridiq

One workspace for Juridiq authentication/API calls and TRF1 PJe 1G/2G retrieval.
Publication context is assessed by the calling LLM agent. This project supplies
read-only evidence preparation, PJe retrieval, and grounded-response validation.

## Layout

```text
integrations/
  juridiq/
    publications.py       Local evidence bundles and decision validation
    tasks.py              Skill task routing and payload preparation
    pipeline.mjs          End-to-end agent-orchestrated publication pipeline
    client.mjs            Named Juridiq publication/template/lawsuit/task operations
    profiles.mjs          Default client policy and task-template mapping
    session.mjs            Shared bearer authentication and request guard
  pje/
    auth.py              Shared SSO/TOTP login
    trf1_1g.py            First-instance JSF process search
    trf1_2g.py            Second-instance search + shared timeline/document parsing
    client.py            Common process retrieval CLI/function for 1G and 2G
    documents.py         Bounded selected-document reads and HTML text extraction
    watch_1g.py           Imported first-instance process watcher
    notifications.py     Imported 1G/2G Expedientes and Push exploration
scripts/
  juridiq_read.mjs        Print an API response using the shared session
tests/                   Offline checks; no account credentials required
docs/
  endpoints.md           Routes, request shapes, and evidence status
  pipeline.md            Data sources and remaining implementation stages
  migration.md           Original files and where they now live
  reference/            Original triage skill and its supporting references
data/                    Ignored private responses, documents, polling state
.env.example             Empty configuration template
requirements.txt         PJe dependencies from the previous environment
package.json             Node commands and local MCP dependencies
pje_adapter.py           Compatibility entry point
juridiq_session.mjs      Compatibility re-export
```

The active code has no import dependency on the Desktop/Juridiq or ChatGPT/General
projects. Those original projects were left intact. The two root compatibility
files let existing commands/imports keep working while using the consolidated code.

## Setup

Use Python 3.12+ and Node 22+. From this project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Fill a local .env using .env.example or export the variables in your shell.
Node supports --env-file=.env. Python uses environment variables exported in the
shell; it does not automatically load .env.

PJe supports independent PJE_1G_USERNAME/PASSWORD/2FA_DATA and
PJE_2G_USERNAME/PASSWORD/2FA_DATA settings. Shared PJE_USERNAME/PASSWORD/2FA_DATA
are fallbacks. These implementations use username/password + TOTP SSO; they do
not implement A3 hardware-certificate authentication.

Juridiq uses JURIDIQ_EMAIL/JURIDIQ_PASSWORD only when a session is missing,
expired, or rejected with 401. Its default cache is keyed by the configured email
under LocalAppData/Juridiq. If no email is configured, it can reuse the original
bearer-session.json cache. A custom JURIDIQ_SESSION_FILE must be distinct per account.
Set credentials before starting Node/importing the helper.

Writes are disabled by default. Each operation explicitly selects `operational`
or `test`, which determines the credential pair and tenant. Writes additionally
require `JURIDIQ_ALLOW_WRITES=true` and confirmation at the MCP boundary.
Login and POST /generic-tables are allowed in read mode. The read CLI exposes only
GET and the generic-tables query POST.

## Commands

These portal commands use live credentials only when explicitly run:

```powershell
.\.venv\Scripts\python.exe -m integrations.pje.client --portal 1g --process-number 1024853-70.2023.4.01.3400 --scope page
.\.venv\Scripts\python.exe -m integrations.pje.client --portal 2g --process-number 1016572-38.2026.4.01.0000 --scope latest
node --env-file=.env scripts/juridiq_read.mjs --path "/user/me"
```

Add --fetch-documents --download-dir data/documents to a PJe command to save raw
document responses. Inspect the returned HTTP status/content type: a .bin file is
not proof of a usable PDF. For selected context, use --scope page and repeat
--document-id ID (maximum five). This returns extracted HTML text or an explicit
unresolved extraction status under attachments. PDF extraction/OCR is not implemented.

The PJe CLI writes JSON to stdout and request diagnostics to stderr. It returns
movement_candidates; these are not confirmed diary/Juridiq publications. The
result also includes the search row, process details, parties, event origin/type,
document inventory and pagination metadata. `page` selects the first HTML
timeline page and is the default; `latest` selects the latest date bucket, not
necessarily a single movement. `all` is retained as a compatibility alias for
`page` because the portal uses infinite-scroll pagination and is not fully loaded
by one detail response.
Call fetch_process(..., session=session) to reuse an authenticated PJe session
across multiple searches; otherwise a call logs in and closes its own session.
Call read_selected_documents(process_result, [document_id], session=session) from
integrations.pje.client to reuse that session for evidence selected by the agent.
It accepts only known IDs and the supported joined-document download route, never
arbitrary agent-supplied URLs. Do not accept caller-supplied process results in a
hosted wrapper; retain the adapter result on the trusted side.

## Agent-orchestrated publication context

See [the detailed plan](docs/publication-enrichment-plan.md) for matching rules,
states, safety boundaries and rollout. The caller assesses the publication first,
requests PJe context only for identified gaps, then submits a source-quoted decision.
There is no embedded LLM or OpenRouter dependency. The local MCP server is the
agent integration surface; automatic operational-account writes remain disabled.

Prepare one local historical publication (the saved batch is already handled):

```powershell
.\.venv\Scripts\python.exe -m integrations.juridiq.publications prepare --snapshot data/juridiq-publications-2026-09-08.json --publication-id f8f625ca-5e48-4ae8-92ea-9978dd97e04a --include-handled --output data/publication-assessment.json
```

Add --process with a saved PJe client result to supply movement/document candidates,
and --documents with a JSON array of selected-document read results to supply text.
The preparation and validation commands are local-only. They do not call a model
or log into either service. Existing output files are not overwritten.
Keep rawPublication as a local archive; give the calling agent the parsed
publication/process/timeline/documents/sources fields and agentInstructions.

After the caller produces a decision matching the bundle's contract:

```powershell
.\.venv\Scripts\python.exe -m integrations.juridiq.publications validate --bundle data/publication-assessment.json --decision data/agent-decision.json --output data/validated-decision.json
```

Validation checks IDs, states and exact source quotes; it does not establish legal
correctness. A sufficient-context result is input for later task drafting, not
permission to write. Juridiq bearer reuse and its read guard remain unchanged.

## Full publication pipeline

The local MCP server in `scripts/mcp_server.mjs` exposes read tools for pending
and filterable publications, lawsuits and their movements, events, tasks and
their history/comments, templates and PJe context. It
also exposes explicitly test-scoped mutation tools. `integrations/juridiq/pipeline.mjs`
exposes `runPublicationPipeline`. The host agent supplies
`deduplicate`, `assess`, `enrich` (selected PJe context when incomplete), and `route`
(the skill's task type, description, dates, evidence, and `obligationKey`). The runner
polls unread publications, resolves live templates from the supplied client profile,
deduplicates acts, checks existing tasks, creates or updates tasks, handles confirmed
events, marks only successful publications read, and persists per-publication state.
All reads and writes in one run use the same account because Juridiq lawsuit, template,
and task IDs are account-scoped. `DEFAULT_PROFILE` preserves the current client's
mapping; another client supplies the same profile shape with its own rules and template
titles. The runtime account is selected per operation; it is not part of the profile.

The opt-in live write test discovers the test account's own office, current user,
and board column, then creates a minimal temporary task with no tags or template
dependency. It retrieves the task, updates it, retrieves it again, and deletes only
the returned ID. `lawSuitId: null` remains supported for an unlinked-task test; set
`TEST_JURIDIQ_LAWSUIT_ID` to exercise a linked task. No operational-account IDs are
copied into the test payload:

```powershell
$env:RUN_JURIDIQ_WRITE_TEST = "true"
$env:JURIDIQ_ALLOW_WRITES = "true"
node --env-file=.env scripts/test_juridiq_write.mjs
```

PDF documents remain explicitly unresolved in the PJe adapter. The suggested
`pdf-inspector` should be added only for selected PDF evidence, preserving page
provenance and OCR-needed statuses; it is not needed for HTML or complete
publications.

The imported 1G watcher is available via:

```powershell
.\.venv\Scripts\python.exe -m integrations.pje.watch_1g --sheet-json data/watched_processes_1g.json
```

Its input is an array of objects with a "Número do processo" field. It defaults to
one run and writes state/cookies under data. See docs/pipeline.md for its baseline
limitations. A dedicated 2G watcher has not been implemented.

## Offline validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
node --test tests/juridiq_session.test.mjs
```

Checks cover both portal routes, session reuse, input validation, timeline/document
parsing, separate portal credentials, existing notification parsers, bearer reuse,
one 401 refresh, no login retry on an API 429, account cache separation, and the
Juridiq read guard. Consolidation checks do not establish fresh live portal/API
compatibility. See docs/endpoints.md for the distinction.
