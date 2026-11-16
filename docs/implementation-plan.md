# Implementation plan after the second review (2026-11-09)

Delivery order for the next deployment (remote MCP per office, service layer): `rollout-plan.md`.

Second read-only review of the whole package (PJe; Eproc/e-SAJ/Projudi; ADVBOX/Juridiq/DJEN; core/hosted/install;
workflows and cross-cutting) plus a web survey of current MCP guidance. It replaces section 5 of
`review-2026-11.md`; the first review's IDs keep their meaning. New IDs: `PN` PJe, `EN` Eproc/e-SAJ/Projudi,
`AN/JN/DN` ADVBOX/Juridiq/DJEN, `CN` core/hosted/install, `WN` workflows, `M` MCP guidance.
Baseline: 659 tests pass, ruff clean.

## What changed since the first review

- Every first-review finding is still open, except C1 and C5 (partly fixed, uncommitted, untested), C17 (mostly
  fixed) and J2 (execution gated per tenant; registration still global).
- E13 was wrong: only Eproc logs in with a browser. The real duplication is the Keycloak x509/OTP flow and A1
  loading between Eproc, PJe and Projudi.
- The workflows package had never been reviewed.

## New findings that matter most

| ID | Sev | Problem |
|---|---|---|
| PN1 | high | KZ expedientes endpoint is paginated (20/page); only page 1 is read, so a pending item beyond it is invisible to the ciência gate |
| PN2 | high | The gate matches by `documentId` only: a pending expediente without one, and attachments of a pending act, pass |
| EN1 | high | Eproc has no ciência gate at all: `eproc_get_process(documentIds)` opens any document (portal behaviour not confirmed live) |
| EN2 | high? | e-SAJ gate accepts any 200/204 without reading the body (only 204 was ever captured) |
| PN3 | high | E1 applies to PJe too: a rejected TOTP/password is retried on every tool call, two attempts each |
| CN1 | high | Hosted sign-in: open client registration + `/login` not bound to the browser that started it + no client shown = a phishing link yields a 30-day token for the office |
| CN2 | high | C1 residue: the shared `.env` still provides write flags (`*_ALLOW_WRITES`), `*_CERT`, login URLs; `setup export` still fills secrets from it |
| D1 | high | Already failing: 30-day terms cannot be computed from today, 15-day from 26/11; no recess, no 2027; JFPE used for every office |
| DN1 | high | MCP saved searches send the OAB as typed; the 7 variants are only used by the CLI, so "14663-A" publications are missed |
| AN1 | high | `update_task` raises after the replacement exists: the caller loses the new `postId` and the old task is gone |
| WN1 | high | A failed second `filing_prepare`/`finalize` leaves the old `payload.json`; `pje_draft_write` then files the stale payload |
| WN2 | high | `money("20.000")` reads 20.000 as twenty: the claim value prints 1000x smaller |
| M1 | high | `TenantGate` accepts a token with no `resource` on every connector (spec: must validate audience) |
| EN3 | med | TNU session refresh always fails, so every call after 120 s is a full Chrome login + TOTP |
| EN4 | med | Eproc SSO host check is `"sso" in host`, which matches `processo*.jus.br`; the A1 is presented there |
| CN3 | med | scrypt on the event loop in `/login`, throttle by e-mail only, unbounded pending requests and clients |
| CN5 | med | Session pool key ignores the credential version; removing a tenant leaves cookies and documents on disk |
| CN8 | med | Hosted blocks writes by a list of two tool names; no tool-call audit |
| CN9 | med | Sanitizer misses tokens, cookies, CPFs, relative paths and eats ordinary words (`ca` pattern) |
| CN10 | med | Local `--http` mode has no authentication and rebinding protection is off |
| AN2 | med | Composite ADVBOX writes release the office lock between steps: two edits of one task create two replacements |
| AN3 | med | ADVBOX file tools keep the old account's session after a credential change |
| WN3 | med | `filing_finalize` overrides can replace a document's `path` and hash |
| WN5 | med | Claim-value and CPF checks are skipped when the petition yields no candidates |
| M2 | med | `pje_read_expediente_ato` and the download tools are annotated read-only; clients skip confirmation |

Smaller items (PN4–PN12, EN5–EN12, AN4–AN9, JN1–JN2, DN2–DN4, CN4, CN6, CN7, CN11–CN15, WN4, WN6–WN17) are in
the phases below by ID.

## Phase 0 — safety and quick wins (implemented 2026-11-09, uncommitted, not yet validated live)

Status: all six slices and the second pass are in the working tree; tests pass, ruff clean. Nothing was run
against a portal. Before deploy: the live checklists below. Still open from this phase: docs drift
(`README.md`, `architecture.md`, `hosted.md`, `docs/connectors/*`), `[djen] court` per office in `settings.toml`,
moving the per-office names out of the root `.env`, `EVENT_LINK_CONFIRMED` for Eproc, ADVBOX/Juridiq login
cool-down (they do not use `SessionPool`).

Owner decisions (2026-11-09, applied): write flags stay shared from the root `.env`; the TOTP account is mapped
once at setup and stored as `*_OTP_PROFILE_MATCH`; Eproc opens the requested documents without a ciência gate (TJSP
included), and the result carries a `ciencia` notice (intimação pending or not verifiable) that the agent relays to the
lawyer; a
petition without a stated claim value or CPF passes (only a differing one blocks).

Live checks: (1) Claude and ChatGPT sign-in through the new browser-bound `/login`, `iss` accepted, no 421 through
Caddy; (2) PJe: a process with zero expedientes still reads documents (classic tab renders an empty table), KZ
expedientes `pagina` parameter; (3) Eproc: an empty pending queue carries a total, TJSP 1G exposes pending state,
SSO hosts of TJRJ/TJMG/TJPR/TJTO/TRF2/TRF6/TNU pass the stricter check; (4) e-SAJ: status/body of
`validarAberturaIntimacaoNaoRecebida.do` for a non-pending and a pending document, reCAPTCHA score with a fresh
Chrome profile; (5) ADVBOX: `dedupeKey` search, staged-file delete, completion confirmation on a test task.

Original split (six parallel owners, disjoint files):

No tool is renamed. Arguments are removed or added only where a safety fix needs it; the affected skills are
updated by the same owner.

| Owner | Files | Items |
|---|---|---|
| Core | `core/mcp.py`, `core/session_pool.py`, `core/redaction.py`, `install/`, `scripts/`, `.gitignore`, `tests/core` | CN2 (allowlist from the shared file; export refuses shared secrets), CN9, CN11, CN12, CN13; session pool: `LoginRejected` + cool-down (E1), login semaphore (E2), idle TTL/LRU (C3), no stale mark on input errors (PN5 part) |
| Hosted | `hosted/`, `tests/hosted` | CN1 (browser-bound login, client name and redirect host shown), CN3, `iss` on the redirect, host/origin allowlist, dead web-session code |
| PJe | `connectors/pje/`, `tests/connectors/pje`, PJe skills | P1, P2, PN1, PN2 (gate fails closed), PN6, PN7, P3, PN8, P6, P7, P8, P4, M2, fixture anonymization, dead code |
| Eproc/e-SAJ/Projudi | their three dirs and tests | EN1 (gate with `acknowledgeCiencia`), EN2, EN3, EN4, EN6, EN7, EN5+E3 (temporary Chrome profile), C14, M2, dead code (`esaj_list_intimacoes`, placeholders) |
| ADVBOX/Juridiq/DJEN | their three dirs and tests | D1 (2027 base calendar, recess, `court`, horizon error), DN1, DN3, DN4, D2, A1, A2, AN1, AN2, A3, AN7, AN3, AN4, AN6, AN8, AN9, JN1, JN2, J3, dead code |
| Workflows | `workflows/`, `tests/workflows` | WN1, WN2, WN3, WN5, WN7, WN8, WN9, WN10, WN11, WN13 |

Second pass after the six finish (one owner): PJe and Eproc raise `LoginRejected`/`SessionExpired` and use
`SessionPool.run` (PN3, PN5, EN9); full test run; docs drift (`README.md`, `architecture.md`, `hosted.md`).

Held for a decision or a live check, not done in Phase 0:

- M1 audience fail-closed and a redirect-host allowlist: need a check of what Claude and ChatGPT send today.
- CN10: a token on local `--http` changes how installed offices connect.
- Splitting the root `.env`: operator action on a secrets file.
- EN1/EN2: the gate goes in now; the portal's real behaviour still needs one live confirmation each.

## Phase 1 — shared base (1–2 weeks)

Refined by `architecture-review-2026-11-10.md`: the accounts credential model comes first, then these items.


- `core/errors.py` (typed, stable codes) replacing `success: False` dicts and message matching (C9, P10, E8).
- `registry.py` + `ConnectorSpec`; serve, hosted, setup, `.mcp.json`, the login check and the hosted write gate
  derive from it (C7, CN8, CN14). Write tools mounted only with a per-tenant grant; every tool call audited (M).
- `core/storage` (tenant root, 0700/0600, quota, TTL sweep, optional encryption): documents, runs, session caches
  (C10, CN6, P20, AN5). Base for the sync store.
- `core/text.py`, `core/dates.py` (BRT "today"), CNJ helpers in `portals.py`, `core/state.py` atomic JSON,
  one A1 loader, one Keycloak x509/OTP flow, `tests/conftest.py` with shared fakes.
- Caches: PJe process id/token (P5), ADVBOX reference data with TTL (X1), ADVBOX download/OCR outside the lock
  (X2), e-SAJ/Projudi process resolution (EN8). Lock timeout and per-tenant concurrency cap (CN7, PN4).
- Vault key out of the environment, key id, `rekey`; credential version in the pool key; tenant removal deletes
  its files (CN4, CN5).
- Tool surface of review section 3, shipped per connector with the skills: one argument vocabulary
  (`processNumber`, `documentIds`, ISO dates, `page/pageSize`), one list shape, total response budget and a
  `concise|detailed` mode, PJe write tools on a separate `pje-filing` server.
- Publications pipeline: one decision validator, persisted task ids (WN4, WN6).

## Phase 2 — service layer, store and sync

`service.py` per connector with typed results (they become the MCP output schemas and the HTTP API models);
PJe JSF/KZ behind one `Backend`; store interface (SQLite per office first); DJEN sync as the pilot with a
trailing 5–7 day window (DN2); then PJe and Eproc inboxes and active processes, as in `sync-plan.md`.

## Phase 3 — HTTP API, dashboard, operations

HTTP routes on the same services; per-office connections dashboard (last sync/test per connection); OpenTelemetry
export with the tenant on each span; cooperative cancellation in long loops; auth state in the store so several
workers can run; usability evals before each tool rename. Watch: Client ID Metadata Documents (DCR is deprecated
in the 2026-07-28 spec), the Tasks extension for OCR/filing, read/write scopes.
