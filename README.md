# Jurisync

MCP connectors that let LLM agents read Brazilian court portals (PJe, Eproc,
DJEN, INSS) and work with legal management tools (Juridiq, ADVBOX), plus the
customer workflows and agent skills built on them. Everything is Python.

Connectors supply evidence and mechanics; legal judgment stays with the calling
agent and its customer skill. Writes are off unless explicitly enabled per
connector.

## Layout

```text
src/jurisync/
  portals.py            Registry of every court deployment (system, tribunal, instance, host, login)
  core/                 Shared plumbing
    mcp.py              Server runner, .env loading, safe tool errors, read-only annotations
    session_pool.py     In-memory authenticated sessions, one per portal
    totp.py             Authenticator exports / otpauth URIs / QR images → TOTP codes
    pdf.py              PDF text + metadata (pdf-inspector), safe download paths
    document_text.py    HTML/PDF text with access-denied and expired-session detection
    redaction.py        Strip tokens and raw URLs from results and logs
    http.py             URL guard, request conventions, request logging
    word_to_pdf.py      DOC/DOCX → PDF through headless LibreOffice
  connectors/           One folder per system; each has a server.py MCP entry point
    pje/                TRF1 1G/2G and TRF5 1G: one search flow for every deployment, documents,
                        notifications, TRF5 draft filing; jsf.py holds the shared JSF mechanics
                        (auth/ holds SSO, A1 certificate, session cache)
    eproc/              TRF4 1G sections (SC, RS): notice queues, process, documents (Selenium login)
    djen/               Public DJEN/Comunica search, certidão text, JFPE deadline calendar
    inss/               GERID/PAT through Chrome with the A3 certificate
    juridiq/            Juridiq REST API
    advbox/             ADVBOX web session: customers, lawsuits, activities, files, tasks
  workflows/
    publications/       Juridiq publication → task pipeline and evidence validation
    filing/             Profile-driven filing prep (ADVBOX protocol folder → PJe payload) and its MCP server
customers/
  jean/                 Juridiq publication triage: profile, runner, skill, notes
  victor/               DJEN → ADVBOX routing skill, TRF5 filing prep, TRF1 watcher
cli/                    Diagnostics: juridiq_read, inss_probe, eproc_portal_smoke
tests/                  Offline tests mirroring src/ and customers/
docs/                   Architecture, publication workflow, connector references
.claude/skills/         Links to each customer's skills for Claude Code
```

## Setup

Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

```bash
cp .env.example .env
```

Fill in only the connectors you use; `.env.example` documents every variable.
INSS also needs Google Chrome and the A3 certificate middleware; see
[the INSS guide](docs/connectors/inss-instalacao.md).

Claude Code picks up `.mcp.json` from the project root. Each server runs as
`.venv/bin/python -m jurisync.connectors.<name>.server` and loads `.env` on
startup. Restart Claude Code (or reload MCP servers) after changing `.env`.

## Connectors

`*` marks tools that write (locally or remotely).

| Server | Tools | Login | Writes |
|---|---|---|---|
| `pje` | `pje_get_process`, `pje_list_expediente_counters`, `pje_list_watched_processes`, `pje_draft_inspect`, `pje_search_pending_drafts`, `pje_draft_write`* | TRF1: password + TOTP. TRF5: A1 certificate + TOTP | `PJE_ALLOW_DRAFT_WRITES=true` (drafts only; never protocols) |
| `filing` | `filing_profiles`, `filing_subjects`, `filing_prepare`*, `filing_review`, `filing_finalize`* (every tool that needs one takes a `profile` name; a profile is `customers/<name>/filing-profile.json`) | ADVBOX session | local run files under `JURISYNC_FILING_DIR`; Jev (outside classifier) off unless `useJev` |
| `eproc` | `eproc_list_notices`, `eproc_get_process` (argument `portal`: `trf4-1g-sc`, `trf4-1g-rs`) | Browser login + TOTP, once per portal | none |
| `djen` | `djen_search_communications`, `djen_get_certidao_text`, `djen_calculate_deadline` | public | none |
| `inss-gerid` | `inss_gerid_ensure_session`, `inss_list_notifications`, `inss_search_tasks`, `inss_get_task`, `inss_get_attachment`*, `inss_get_process`*, `inss_read_document` | Chrome + A3 PIN (by the user) + TOTP | local downloads only (`get` tools save the file and return its parsed text) |
| `juridiq` | 18 read tools (publications, lawsuits, movements, events, tasks, templates, users, tags) + 8 write tools* | email + password, bearer token cached | write tools registered only with `JURIDIQ_ALLOW_WRITES=true` |
| `advbox` | 11 read tools (customers, lawsuits, activities, members, workflows, reports, files) + downloads* + `advbox_create_task`*, `advbox_complete_task`*, `advbox_delete_task`* | email + password + approved device | `ADVBOX_ALLOW_WRITES=true` |

Long-running servers keep each portal's authenticated session in memory, so
only the first call logs in; later calls reuse it until it expires.

## Customers

Each customer folder owns its policy: skills, profiles, workflows, and notes.
Shared code never imports from `customers/`.

- **Jean** — `customers.jean.run_publications.run_jean_publications(agent)` runs
  the publication pipeline with Jean's template mapping.
- **Victor** — DJEN → ADVBOX task routing skill; TRF5 initial-filing preparation
  (`python -m jurisync.workflows.filing --help`, profile `victor`); TRF1
  spreadsheet watcher (`python -m customers.victor.workflows.pje1g_watch --help`).

Add a customer by creating `customers/<name>/` with a README, its skills under
`skills/`, and a symlink in `.claude/skills/` for each skill.

## Command line

```bash
.venv/bin/python -m jurisync.connectors.pje.process --portal trf1-1g --process-number 1024853-70.2023.4.01.3400
```

```bash
.venv/bin/python -m jurisync.connectors.eproc.client process --portal trf4-1g-sc --process-number 5000436-79.2026.4.04.7220
```

```bash
.venv/bin/python -m jurisync.connectors.djen.client --numero-oab 14663 --uf-oab PB --inicio 2026-10-01 --fim 2026-10-02 --normalized
```

```bash
.venv/bin/python cli/juridiq_read.py --path /user/me
```

```bash
.venv/bin/python cli/inss_probe.py login
```

## Development

```bash
uv run pytest
```

```bash
uv run ruff check . && uv run ruff format --check .
```

Tests are offline and need no credentials. Live checks against real portals are
manual and read-only.

## Docs

- [Architecture](docs/architecture.md) — boundaries, sessions, safety rules
- [Publication workflow](docs/publication-workflow.md) — the agent contract for Juridiq triage
- [Connector references](docs/connectors/) — endpoints, TRF5 filing, Eproc captures, INSS setup, Tramitação
- [Backlog](docs/backlog.md)
