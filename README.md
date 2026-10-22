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
    pje/                TRF1 1G/2G, TRF3 1G/2G, TRF5 1G/2G and TRU, TJAP 1G, TJPE 1G/2G: one search flow for every deployment, documents,
                        Expedientes panel inboxes (inbox.py), Push registry, TRF5 draft filing; jsf.py holds the shared JSF mechanics
                        (auth/ holds SSO, A1 certificate, session cache)
    eproc/              TRF4 (1G SC/RS/PR, 2G), TRF6 and TJRS: notice queues, process, documents (A1 certificate or Selenium login)
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
  elisandra/            DJEN → eproc intimações skill (TRF4 SC/RS, TJRS), task drafts for approval
  wesley/               PJe (TRF1, TRF3, TRF5) and Eproc (TRF4) access on the A1 certificate
plugins/                Claude Code plugins per customer (skills + server registrations), with .claude-plugin/ marketplace
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

## Installing on another machine (Claude and Codex)

Needs [`uv`](https://docs.astral.sh/uv/) and read access to the (private) GitHub repository.

```bash
uv tool install git+https://github.com/gabrielccac/jurisync
```

This installs one command per server (`jurisync-pje`, `jurisync-advbox`, `jurisync-djen`, `jurisync-eproc`,
`jurisync-juridiq`, `jurisync-inss`, `jurisync-filing`), `jurisync-http` and `jurisync-setup`.

**Skill + MCPs as a Claude Code plugin.** After the `uv tool install` above, one command installs the customer's
skills and registers its MCP servers, and `/plugin update` brings every later change:

```text
/plugin install victor --marketplace gabrielccac/jurisync
```

The plugin only carries the skills and the server registrations; the logins still come from the bundle below.
Import it with `--register none` (or `desktop`) so the servers are not registered twice.

**Customer skills from GitHub, without the plugin.** `jurisync-setup skills --customer <name>` clones the
repository into `~/jurisync-src` (or fast-forwards it) on the branch the package was installed from, links each
`customers/<name>/skills/<skill>` into `~/.claude/skills` for Claude Code, and writes `<skill>.zip` to Downloads
for upload in Cowork, only when the skill changed. Running it again is the update; it stops instead of overwriting
when the clone has local edits or commits.

**Customer credentials without editing files.** The operator exports a customer's logins into one encrypted
bundle and the customer's machine imports it:

```bash
jurisync-setup export --customer elisandra --out elisandra.jurisync   # prints a one-use passphrase
jurisync-setup import elisandra.jurisync                              # passphrase is typed on a local page
```

The bundle carries the portal logins, authenticator exports, certificate files (A1 `.p12`) and the variables
declared in the customer's `portals.toml` (`[env]` for ADVBOX, CPF and API keys). `import` writes
`~/.config/jurisync/.env` and `~/.config/jurisync/files/` with owner-only permissions, tests each login
(`eproc`, `pje`, `advbox`) and registers the servers with Claude Desktop and Claude Code. Send the file and the
passphrase over different channels.

**Where the credentials go.** One shared file, `~/.config/jurisync/.env`, in the same `KEY=value` format as
`.env.example`. Servers read it when they start, so restart them after editing. Variables already set in the
environment win over the file; `JURISYNC_ENV_FILE` points to a different file.

**Claude** (stdio): register the commands, with no credentials in the command:

```bash
claude mcp add pje -- jurisync-pje
claude mcp add advbox -- jurisync-advbox
```

**Codex CLI** (stdio, local, same commands as Claude):

```bash
codex mcp add pje -- jurisync-pje
codex mcp add advbox -- jurisync-advbox
```

This writes `[mcp_servers.pje]` and `[mcp_servers.advbox]` to `~/.codex/config.toml`. The servers start in about
1.3 s, inside Codex's default 10 s startup limit (`startup_timeout_sec` raises it).

**ChatGPT app** (web/desktop; only for when Codex or Claude cannot be used): its custom connectors reach only
public HTTPS, not local programs, so serve the servers over HTTP from one process and expose it through a tunnel.

```bash
jurisync-http pje advbox            # http://127.0.0.1:8000/pje/mcp and /advbox/mcp
cloudflared tunnel --url http://127.0.0.1:8000
```

In ChatGPT's developer mode, add a custom connector per server with the tunnel URL plus `/pje/mcp` (and
`/advbox/mcp`), with no authentication. **This is for local tests only**: anyone with the tunnel URL can call
the tools with the machine's credentials, so stop the tunnel when you finish. One server alone also runs with
`jurisync-pje --http --port 8000`. Mutable state (ADVBOX session, downloads) lives in `~/.local/share/jurisync/`
for an installed package and beside the code in a checkout.

## Connectors

`*` marks tools that write (locally or remotely).

| Server | Tools | Login | Writes |
|---|---|---|---|
| `pje` | `pje_get_process`, `pje_list_expediente_counters`, `pje_list_inbox`, `pje_list_watched_processes`, `pje_draft_inspect`, `pje_search_pending_drafts`, `pje_draft_write`* | TRF1/TRF3/TJAP: password + TOTP by default, or the A1 certificate with `<PREFIX>_CERT=true` (e.g. `PJE_1G_CERT=true`; the SSO asks for TOTP only when it requires one). TRF5 (1G and 2G): A1 certificate + TOTP | `PJE_ALLOW_DRAFT_WRITES=true` (drafts only; never protocols) |
| `filing` | `filing_profiles`, `filing_subjects`, `filing_prepare`*, `filing_review`, `filing_finalize`* (every tool that needs one takes a `profile` name; a profile is `customers/<name>/filing-profile.json`) | ADVBOX session | local run files under `JURISYNC_FILING_DIR`; Jev (outside classifier) off unless `useJev` |
| `eproc` | `eproc_list_notices`, `eproc_list_processes`, `eproc_get_process` (argument `portal`: `trf4-1g-sc`, `trf4-1g-rs`, `trf4-1g-pr`, `trf4-2g`, `tjrs-1g`; also `trf6-1g`/`trf6-2g` when configured) | Browser login + TOTP, or the A1 certificate over mutual TLS with `<PREFIX>_CERT=true` (TRF4); once per portal | none |
| `djen` | `djen_search_communications`, `djen_get_certidao_text`, `djen_calculate_deadline` | public | none |
| `inss-gerid` | `inss_gerid_ensure_session`, `inss_list_notifications`, `inss_search_tasks`, `inss_get_task`, `inss_get_attachment`*, `inss_get_process`*, `inss_read_document` | Chrome + A3 PIN (by the user) + TOTP | local downloads only (`get` tools save the file and return its parsed text) |
| `juridiq` | 18 read tools (publications, lawsuits, movements, events, tasks, templates, users, tags) + 8 write tools* | email + password, bearer token cached | write tools registered only with `JURIDIQ_ALLOW_WRITES=true` |
| `advbox` | 12 read tools (customers, lawsuits, activities, intimações via `advbox_list_publications`, members, workflows, reports, files) + downloads* + `advbox_create_task`*, `advbox_complete_task`*, `advbox_delete_task`* | email + password + approved device | `ADVBOX_ALLOW_WRITES=true` |

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
.venv/bin/python -m jurisync.connectors.eproc.client processes --portal trf6-1g
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
