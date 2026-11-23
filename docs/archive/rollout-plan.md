> Arquivado em 23/11/2026: substituído por docs/roadmap.md.

# Rollout plan: remote MCP for every office + service layer (2026-11-10)

One plan for the next deployment. It orders the work in `implementation-plan.md` (Phases 1–3) and
`architecture-review-2026-11-10.md` around two goals:

1. **Simple connection.** An office adds one URL in Claude (or ChatGPT), signs in once, and has every system it
   uses. No install, no `.env`, no terminal on the office's side.
2. **Fewer portal calls.** A service layer with caches and background sync, so repeated and fan-out reads stop
   hitting the portals.

The hosted server is the long-lived process that owns sessions; it removes the "Chrome login per conversation"
of the local stdio install by itself. Local installs keep working, but stop being the default for new offices.

## Where we are

- Hosted mode exists (`hosted/`): OAuth with browser-bound sign-in, encrypted vault per office, session pool per
  (office, portal), `TenantGate`. Mounted connectors: PJe, Eproc, DJEN, ADVBOX, Juridiq, one URL each
  (`/pje/mcp`, `/eproc/mcp`, …). e-SAJ, Projudi and filing are not tenant-ready.
- Credentials enter only through our bundle import (`jurisync-setup export` → `secrets import-bundle`). The office
  page is designed (`hosted/access.py` has the logic) but not built.
- No cache or store between tools and portals (measured in the review: `pje_get_process` 6–7 requests,
  `pje_read_documents` repeats them, the all-region inbox ~43).
- Pilot limits still open: pending sign-ins in memory (one process), one master key, datacenter IP, A1 custody terms.

## Phase A: one URL per office (1–1.5 weeks)

| Item | Done when |
|---|---|
| `/mcp` endpoint that serves the tools of every connector enabled for the office (names are already prefixed, so no collisions); per-connector URLs stay for old setups | Claude and ChatGPT list the office's tools from one connector; an office without ADVBOX does not see `advbox_*` |
| Tool annotations and per-office write grants (`*_ALLOW_WRITES` from the vault) on that endpoint; one audit log line per tool call (office, user, tool, ok/error, duration; no arguments with personal data) | A write tool answers only for a granted office; `jurisync-hosted history` shows calls |
| e-SAJ and Projudi read through `credentials.env` (no direct `os.environ`), tenant paths for their caches; then added to `TENANT_READY` | Both pass the isolation test of `hosted.md` step 6 |
| Every login goes through the pool: hosted `check_login` and filing `cert_login` stop bypassing cool-down and semaphore | Wrong password at one portal never produces a second attempt within the cool-down |
| Live checks held from Phase 0: Claude and ChatGPT sign-in through `/login`, `iss`, token `resource` (then `JURISYNC_REQUIRE_TOKEN_RESOURCE=1`) | Logged once per client |

## Phase B: accounts credential model + office page (1.5–2 weeks)

| Item | Done when |
|---|---|
| Accounts model (`architecture-review-2026-11-10.md` §1): `pdpj`, `a1`, `eproc:<family>`, `advbox`, `juridiq`; the registry says what each portal accepts; method follows from the accounts; old prefixes still read | A certificate-only office has no username/password/`_CERT`/`SESSION_ACCOUNT` entries; cinthia's PDPJ account is stored once |
| `jurisync-setup migrate` and a vault migration: identical values across prefixes (compared by hash) become one account | All 13 customer folders migrate; `customers check` and one login per office give the same result before and after |
| Office page: the office admin signs in, adds each account once (login + password + authenticator text or QR, or the A1 + PIN), picks the TOTP profile at that moment, and "Test and save" logs in before storing | `hosted.md` validation steps 1–3 pass for a pilot office with nobody on our side seeing a secret |
| Portal status per office: last successful login, cool-down, expired password / device approval / A1 expiry shown with what to do | Today's cases (TRF6 user format, ADVBOX device approval, TJPE expired password) appear as clear messages |

## Phase C: service layer and caches (2–2.5 weeks)

Tools become thin adapters over `services/<connector>`; typed results become the MCP output schemas.

| Item | Done when |
|---|---|
| Foundations: `core/errors.py`, `registry.py`/`ConnectorSpec`, `core/keycloak.py` (one form/OTP/x509 flow), one A1 loader, CNJ/text/date helpers, `tests/conftest.py` | Cleanups 1–10 of the review merged; no behaviour change in the test suite |
| PJe read service split into Listing / Opening / Writes; TTL caches: process id+token+detail 10–15 min, Expedientes read for the ciência gate ~2 min (dropped after any opening) | `pje_get_process` then `pje_read_documents` on the same process: second call ≤ 2 portal requests instead of 7–8 |
| Eproc: pending-queue scan cached ~2 min; process resolution cached | `eproc_get_process` with documents no longer rescans 4 queues per call |
| ADVBOX reference data (members, workflows, steps) cached ~1 h; download/OCR outside the office lock | `advbox_search_tasks` by text: 1 request |
| One DJEN saved-search path for CLI and MCP (OAB variants on both) | Same results from `djen_search_saved` and the CLI |
| Per-call request budget logged (portal requests, logins, cache hits) | A dashboard query shows requests per tool before/after |

## Phase D: store and background sync (2–3 weeks)

| Item | Done when |
|---|---|
| SQLite store per office behind a repository: communications, expedientes counters, processes, movements, sync runs, change events | Schema migrations run on start; deleting an office deletes its store |
| Scheduler inside the server, jobs per (office, portal) on the pool key, own login budget, never retries `LoginRejected` | Two offices sync in parallel; a rejected login pauses only that job |
| DJEN pilot: hourly, trailing 5–7 days, dedupe by id; `djen_search_saved` answers from the store with `asOf`, `refresh=true` goes live | Routine runs read the store; DJEN requests drop to the hourly job |
| PJe/Eproc counters every 2–4 h and before routines; a bucket is listed only when its count changed; active processes refreshed on a trigger | `pje_list_inbox` answers from the store in under a second; ~43 requests only when counts moved |
| Ciência by construction: the scheduler receives only the Listing service; document text is stored only after the office opened it | A test proves sync code has no path to `fetch_ato`/`read_documents`/Eproc documents |

## Phase E: operations and dashboard (in parallel with D, then ongoing)

- Production hardening: pending sign-ins and sessions metadata in the store; per-office data keys (KMS or Vault
  transit); backups of store and vault; health endpoint; OpenTelemetry with the office on each span.
- Server IP: a Brazilian egress for portals that challenge datacenter IPs (measure per portal in Phase A).
- Eproc on the server runs headless Chrome only for login; cap concurrent browsers (memory) through the login
  semaphore.
- Per-office connections page (last sync, last test, what needs action) on the same services; the HTTP API is built
  only for this consumer.
- Contract/LGPD terms for holding A1 certificates and portal passwords before the first non-pilot office.

## Office onboarding, end state

1. We create the office and its admin (one command).
2. The admin opens the office page, adds each account once, tests and saves.
3. Each lawyer adds `https://<host>/mcp` as a connector in Claude or ChatGPT and signs in.
4. Skills and routines for the office are installed as a plugin; they call the same tools.

## Order and what not to do

A → B → C (foundations first, then PJe caches) → D, with E alongside D. A and B make the simple connection
possible; C gives the largest measured saving without sync; D removes the remaining fan-out.

Not doing: microservices, Celery/Redis, a generic ORM, syncing ADVBOX/Juridiq or whole histories, caching document
text before ciência, an HTTP API before the dashboard needs it, tool renames without the matching skill updates.
