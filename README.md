# Jurisync

MCP connectors that let LLM agents read Brazilian court portals (PJe, Eproc,
DJEN) and work with legal management tools (Juridiq, ADVBOX), plus the
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
    http.py             URL guard, redirect follower, request conventions, request logging
    certificate.py      A1 certificate: load the .p12 (file or vault bytes), PEM pair for mutual TLS
    documents.py        Document-read limits (5 per call, 10 MB), id validation, bounded streaming
    documents_dir.py    Shared documents folder: saved files and saved subfolders (tenant-scoped)
    runs.py             Where a filing run lives, for the filing workflow and the portal that files it
    word_to_pdf.py      DOC/DOCX → PDF through headless LibreOffice
  connectors/           One folder per system; each has a server.py MCP entry point
    projudi/            TJGO 1G (A1 over mutual TLS): process, document index, download. base.py holds the shared plumbing;
                        one adapter per Projudi generation (tjgo.py validated; keycloak.py TJPR/TJRR, tjam.py, tjpi.py are
                        placeholders for portals registered with wired=False)
    stj/                STJ Central do Processo Eletrônico (A1 over mutual TLS, REST): processes, process, documents, DJe
    pje/                TRF1 1G/2G, TRF3 1G/2G, TRF5 1G, 2G (Turmas Recursais), TRU (2º Grau e TRU, pjett) and the PJe 1.x host (trf5-legacy), TJAP 1G, TJPE 1G/2G: one search flow for every deployment, documents,
                        Expedientes panel inboxes (inbox.py), Push registry, TRF5 draft filing; jsf.py holds the shared JSF mechanics
                        (auth/ holds SSO, A1 certificate, session cache)
    eproc/              TRF4 (1G SC/RS/PR, 2G), TRF6, TRF2 (2G, 1G RJ/ES) and TJRS: notice queues, process, documents (A1 certificate or Selenium login)
    djen/               Public DJEN/Comunica search, certidão text, JFPE deadline calendar
    juridiq/            Juridiq REST API
    advbox/             ADVBOX web session: customers, lawsuits, activities, files, tasks
  workflows/
    publications/       Juridiq publication → task pipeline and evidence validation
    filing/             Profile-driven filing prep (a folder of pulled documents → payload) and its MCP server; where the
                        documents come from is each office's connector tools, never this module. The portal side of a
                        filing lives in the portal's connector (pje/filing for TRF5)
  install/              setup, credential bundles, skills sync, local HTTP serving
customers/
  jean/                 Juridiq publication triage: profile, runner, skill, notes
  victor/               DJEN → ADVBOX routing skill, TRF5 filing prep, TRF1 watcher
  elisandra/            DJEN → eproc intimações skill (TRF4 SC/RS, TJRS), task drafts for approval
  wesley/               PJe (TRF1, TRF3, TRF5) and Eproc (TRF4) access on the A1 certificate
cli/                    Diagnostics: juridiq_read, eproc_portal_smoke
tests/                  Offline tests mirroring src/ and customers/
docs/                   Architecture, publication workflow, connector references
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

```bash
cp .mcp.json.example .mcp.json
```

Claude Code picks up `.mcp.json` from the project root. The file is gitignored, so each machine keeps its own copy;
the versioned `.mcp.json.example` runs every server as `uv run --project <checkout> python -m <module>`, which
works on Windows, Linux and macOS, and loads `.env` on startup. To work as one office, add
`JURISYNC_CUSTOMER=<name>` to `.env` (or to the environment): every server then reads `customers/<name>/.env` and
`customers/<name>/portals.toml`. Restart Claude Code (or reload MCP servers) after changing `.env`.

## Installing on another machine (Claude and Codex)

Needs [`uv`](https://docs.astral.sh/uv/) and read access to the (private) GitHub repository.

```bash
uv tool install git+https://github.com/gabrielccac/jurisync
```

This installs one command per server (`jurisync-pje`, `jurisync-advbox`, `jurisync-djen`, `jurisync-eproc`,
`jurisync-juridiq`, `jurisync-filing`), `jurisync-http` and `jurisync-setup`.

**Customer skills from GitHub.** `jurisync-setup skills --customer <name>` clones the
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
cloudflared tunnel --url http://127.0.0.1:8000         # prints https://<name>.trycloudflare.com
jurisync-http pje advbox --public-host <name>.trycloudflare.com
```

`jurisync-http` answers only to this machine (`Host`/`Origin` checked against DNS rebinding) and to the host given in
`--public-host`. With `--public-host` (or `--host` set to a LAN address; 0.0.0.0 needs `--public-host`) every URL
starts with a secret path, printed
at start: `https://<name>.trycloudflare.com/<secret>/pje/mcp`. Add one custom connector per server in ChatGPT's
developer mode with that URL, with no authentication; set `JURISYNC_HTTP_SECRET` (24+ letters, digits, `-` or `_`) to
keep the same URL across restarts. **This is for local tests only**: whoever has the full URL calls the tools with
the machine's credentials, so keep it private and stop the tunnel when you finish. One server alone also runs with
`jurisync-pje --http --port 8000` (same `--public-host`). Mutable state (ADVBOX session, downloads) lives in `~/.local/share/jurisync/`
for an installed package and beside the code in a checkout.

## Connectors

`*` marks tools that write (locally or remotely).

| Server | Tools | Login | Writes |
|---|---|---|---|
| `pje` | `pje_get_process`, `pje_get_counters`, `pje_list_intimacoes`, `pje_list_watched_processes`, `pje_list_processes`, `pje_get_draft`, `pje_search_pending_drafts`, `pje_fill_draft`* | The `method` the customer's `portals.toml` declares for the portal: `certificate_totp` (A1 certificate; the SSO asks for TOTP only when it requires one) or `password_totp` (password + TOTP); the A3 token with `<PREFIX>_AUTH=a3` or `PJE_AUTH=a3`. Without a customer profile, the registry's default: password + TOTP (A1 with `<PREFIX>_CERT=true`), A1 + TOTP on TRF5 and the TRTs | `PJE_ALLOW_DRAFT_WRITES=true` (drafts only; never protocols) |
| `filing` | `filing_profiles`, `filing_subjects`, `filing_prepare`*, `filing_review`, `filing_finalize`* (every tool that needs one takes a `profile` name; a profile is `customers/<name>/filing-profile.json`) | none: it prepares a folder of documents the agent pulled with the office's own connector (ADVBOX: `advbox_save_folder`) | local run files under `JURISYNC_FILING_DIR`; Jev (outside classifier) off unless `useJev` |
| `eproc` | `eproc_list_intimacoes`, `eproc_list_processes`, `eproc_get_process` (argument `portal`: `trf4-1g-sc`, `trf4-1g-rs`, `trf4-1g-pr`, `trf4-2g`, `trf2-2g`, `trf2-1g-rj`, `trf2-1g-es`, `tjrs-1g`; also `trf6-1g`/`trf6-2g` when configured) | The declared `method`: `browser_totp` (browser login + TOTP) or `certificate_totp`/`certificate_mtls` (A1 certificate); the A3 token with `<PREFIX>_AUTH=a3` or `EPROC_AUTH=a3`. Without a profile, browser login unless `<PREFIX>_CERT=true`; once per portal | none |
| `projudi` | `projudi_get_process`, `projudi_list_documents`, `projudi_read_documents`, `projudi_download_document` (argument `portal`, default `tjgo-1g`) | A1 certificate over mutual TLS (`PJE_A1_P12`/`PJE_A1_PIN`, or `PROJUDI_A1_*`) | none |
| `stj` | `stj_get_counters`, `stj_list_intimacoes`, `stj_list_processes`, `stj_get_process`, `stj_read_documents`, `stj_download_document`*, `stj_list_publicacoes` (STJ notices come from `djen`, tribunal STJ) | A1 e-CPF over mutual TLS, no TOTP (`STJ_A1_P12`/`STJ_A1_PIN`, else `PJE_A1_*`); the lawyer must already be registered in the CPE | none toward the STJ (endpoints that write are refused) |
| `djen` | `djen_search_comunicacoes`, `djen_read_certidao`, `djen_calculate_deadline` | public | none |
| `juridiq` | 18 read tools (publications, lawsuits, movements, events, tasks, templates, users, tags) + 8 write tools* | email + password, bearer token cached | write tools registered only with `JURIDIQ_ALLOW_WRITES=true` |
| `advbox` | 12 read tools (customers, lawsuits, activities, intimações via `advbox_list_publicacoes`, members, workflows, reports, files) + downloads* + `advbox_save_folder`* (a folder into the shared documents folder, for filing) + `advbox_create_task`*, `advbox_complete_task`*, `advbox_delete_task`* | email + password + approved device | `ADVBOX_ALLOW_WRITES=true` |

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

Add a customer by copying `customers/_template/` to `customers/<name>/` and following its README: `portals.toml`
(login method and variable names per portal; the declared `method` is the login the connectors use, whatever else the
`.env` holds), `settings.toml`, `.env.example` and a README. `jurisync-setup
check-customer <name>` checks the folder without reading secrets, and the test suite runs the same check on every
folder. Skills go under `skills/`; `jurisync-setup skills --customer <name>` installs them for Claude Code.

## Command line

```bash
.venv/bin/python -m jurisync.connectors.pje.process --portal trf1-1g --process-number 1000001-00.2023.4.01.3400
```

```bash
.venv/bin/python -m jurisync.connectors.eproc.client processes --portal trf6-1g
.venv/bin/python -m jurisync.connectors.eproc.client process --portal trf4-1g-sc --process-number 5000002-00.2026.4.04.7220
```

```bash
.venv/bin/python -m jurisync.connectors.djen.client --numero-oab 14663 --uf-oab PB --inicio 2026-10-01 --fim 2026-10-02 --normalized
```

```bash
.venv/bin/python cli/juridiq_read.py --path /user/me
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
- [Connector references](docs/connectors/) — endpoints, TRF5 filing, Eproc captures, Tramitação
- [Roadmap](docs/roadmap.md)
