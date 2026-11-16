# Project-level review (2026-11-10): credentials, architecture, code shape

Three read-only passes (credential model, architecture and portal load, code quality) after the Phase 0 work in
`implementation-plan.md`. This document refines Phases 1–2 there; it does not replace them.

## 1. Credentials are keyed by portal, not by account

The root cause of the "empty username/password for a certificate office" and the copied secrets: every value hangs
off a portal's env prefix (`PJE_TRF3_1G_PASSWORD`), and nothing models the real thing, an **account** shared by many
portals. One PDPJ login serves every PJe; one A1 serves everything; one Eproc account often serves a court family.

Observed:

- The same PDPJ TOTP is stored under 21 prefixes (anaflavia), 7 (victor), 5+5 `_DATA`/`_QR` (patricia); cinthia
  holds one user/password/TOTP three times (TRF1, TRF5, TRF3).
- The login method is inferred at runtime, three different ways: certificate-registered PJe portals switch to the
  password flow only without `PJE_A1_P12` (`pje/auth/sessions.py:19-35`); password-registered ones need
  `<PREFIX>_CERT=true` (pinned 4–8 times per office in `portals.toml`); Eproc is `_CERT` opt-in and always demands a
  TOTP while the PJe certificate flow makes it optional.
- `method` in `portals.toml` is never read by a connector; `secrets` lists restate what the registry knows.
- A certificate-only PJe session is cached only when `<PREFIX>_SESSION_ACCOUNT` is set, so offices add a placeholder
  (or a username that is never read) to avoid a login per restart.
- `jurisync-setup --customer X` raises on the first PJe portal (`install/setup.py:146-153`, Eproc only); `collect`
  always asks for user/password, even for a certificate portal.
- The vault stores the same flat per-prefix names, so hosted inherits the duplication. Fixed today: the import
  dropped `*_OTP_PROFILE_MATCH/INDEX` and `*_SESSION_ACCOUNT` (`hosted/cli.py`, `SETTING_SUFFIXES`).
- The A1 is resolved three ways (`core/certificate.py`, `projudi/base.py:59`, hard-coded `PJE_A1_*` in PJe and
  Eproc); only e-SAJ/Projudi honour a per-system override.

Target model: **accounts, then portals.**

```toml
# customers/cinthia/portals.toml (names only; values in .env / vault as ACCT_<ID>_<FIELD>)
[accounts.pdpj]   # ACCT_PDPJ_USERNAME, ACCT_PDPJ_PASSWORD, ACCT_PDPJ_TOTP
kind = "pdpj"
portals = ["trf1-1g", "trf3-1g", "trf5-1g"]

[accounts.trf6]   # ACCT_TRF6_USERNAME (UF+OAB), ACCT_TRF6_PASSWORD, ACCT_TRF6_TOTP
kind = "eproc"
portals = ["trf6-1g", "trf6-2g"]

# certificate office
[accounts.a1]     # ACCT_A1_P12, ACCT_A1_PIN
kind = "a1"
portals = ["trf1-*", "trf3-*", "trf5-*", "tjpe-*", "tjsp-esaj"]
[accounts.pdpj]   # ACCT_PDPJ_TOTP only
kind = "pdpj"
portals = ["trf1-*", "trf3-*", "trf5-*", "tjpe-*"]
```

- The registry declares what each portal **accepts** (`sso="pdpj"`, `methods=("certificate", "password")`); the
  method follows from the accounts linked. If both are linked, a `prefer` chosen at setup decides (no runtime guess).
- TOTP and its profile match belong to the account; the session cache key is account id (+ certificate
  fingerprint), so no `SESSION_ACCOUNT`.
- One resolver in `core/credentials` (`resolve(portal, field)`): account variable → old `<PREFIX>_<FIELD>` → legacy
  names. Old `.env` files keep working; `jurisync-setup migrate` finds identical values across prefixes by hash and
  proposes accounts; `_CERT=true` reads as "prefer certificate" until removed.
- The same schema for local `.env`, `portals.toml`, the vault and the setup prompts; error messages name the
  account ("TRF3 uses your PDPJ account; ACCT_PDPJ_TOTP is missing").

Effort 4–6 days, medium risk (every login route): `customers check` and one login per office before and after.

## 2. Portal load and the service layer

The hypothesis "MCP calls the portal directly, so too many calls" is partly right. A service layer by itself removes
no request; the load comes from repeated reads, panel fan-out and sessions that die with each stdio process.

| Tool | Portal requests | Cause |
|---|---|---|
| `pje_get_process` | 6–7 (~10 s) | search GET+POST, detail, timeline, Expedientes tab; no cache |
| `pje_read_documents` / `_ato` | 7–8 | reruns the whole search for the ciência gate, even right after `get_process` |
| `pje_list_inbox` (all regions) | ~43 | 13 jurisdictions × 14 inboxes |
| `eproc_get_process` + documents | 2 + 4 queue scans + docs | ciência notice rescans 4 queues per call |
| new local stdio process (Eproc/e-SAJ/Projudi) | full Chrome login + TOTP (15–66 s) | sessions in memory only |
| ADVBOX / Juridiq / DJEN | 1–2 | cheap APIs; not a problem |

Target (one process; no Celery/Redis, no microservices):

```
adapters: MCP tools, HTTP API     thin: validate -> service -> typed result
read layer                        store first (asOf, stale); refresh=true goes live
services/<connector>              typed; split Listing (safe) | Opening (acts/docs, ciência) | Writes
scheduler                         jobs per (tenant, portal) on the SessionPool key; login budget; no retry on LoginRejected
store                             SQLite per office: communications, expedientes, processes, movements, sync_runs, changes
connectors                        today's clients/parsers/auth
```

- Sync: DJEN hourly (trailing 5–7 days); PJe/Eproc counters every 2–4 h, opening a bucket only when its count
  changed; active processes on a trigger. Live with TTL: process id/token/detail 10–15 min (about half the PJe
  requests), the ciência read ~2 min (invalidated after any opening), ADVBOX members/workflows ~1 h. Always live:
  writes, openings, `refresh=true`.
- Ciência by construction: the scheduler only gets the Listing service; it has no handle to open an act or document.
- Local install: one long-lived `jurisync serve` daemon (from `install/serve.py`) owns sessions, store and scheduler
  and serves MCP on localhost; this removes the Chrome login per conversation. Stdio stays as a fallback.
- Hosted: the same single process (Starlette + MCP mounts + API + scheduler). Split into API + sticky workers only
  with a second machine (needs Postgres and OAuth state in the store).
- Bypasses to close first: hosted `check_login` (`hosted/access.py:192`) and filing `cert_login`
  (`pje/filing/run.py:151`) log in outside the pool (no cool-down, no semaphore); the DJEN saved-search loop exists
  twice (CLI with OAB variants, server without).

## 3. Code shape

27.1k lines in 132 modules; 29 functions over 80 lines (`juridiq/server.py:217 create_server` 361,
`pje/search.py:123` 163, `pje/documents.py:21` 144, `eproc/client.py:492` 132).

| # | Cleanup | Lines | Risk |
|---|---|---|---|
| 1 | `core/keycloak.py`: one form/OTP/x509 step (3 copies today: `eproc/certificate.py:104`, `pje/auth/certificate.py:233`, `pje/auth/sso.py:80`); `sso` redirects through `follow_redirects` with the host check | −150 | med (live login) |
| 2 | One A1 loader by prefix; drop `signer` wrappers and `projudi._credential_names` | −45 | low |
| 3 | CNJ helpers in `portals` replacing 11 regex copies | −35 | low |
| 4 | `core/text.py`, `core/dates.py` (whitespace, HTML→text, BR↔ISO dates, São Paulo zone) | −45 | low |
| 5 | `ConnectorSpec.check()/forget()`; delete `setup.check_*` and `access.check/forget` branches; add e-SAJ/Projudi | −120 | low-med |
| 6 | Delete the old DJEN `client.main` (duplicate of `djen/cli.py`); fix `README.md:195` | −170 | low |
| 7 | Break import cycles (`esaj/errors.py`, `eproc/http.py`, `pje/saving.py`); imports to module top | ~0 | low |
| 8 | One pooled-call helper for the three server `call` wrappers; module-level tools instead of `create_server` closures | −60 | low |
| 9 | Dead code (`core/mcp.is_secret_name`, `pje/jsf.parse_partial_response`); 23 direct `os.environ` reads behind `credentials.env` (hosted cannot override them today); hosts out of connectors into `portals` | −40 | low |
| 10 | `tests/conftest.py` with one fake session/response; stop patching private `_login`/`_read_json` | −300 (tests) | low |

The largest single target, ~700 lines of parallel KZ/JSF parsers in PJe, is the Phase 2 `Backend`; agree its
interface during Phase 1.

## 4. Order

1. **Accounts model** (section 1) with the backward-compatible resolver and `migrate`; setup that handles PJe.
2. **Foundations** from Phase 1 plus cleanups 1–10 above; route every login through the pool.
3. **PJe read service + TTL caches**: the largest measured saving, no sync needed.
4. **DJEN pilot of store + scheduler**, then PJe/Eproc counter-driven sync.
5. **Local `jurisync serve` daemon**; HTTP API only when the dashboard consumes it.

Not doing: syncing ADVBOX/Juridiq or whole histories, caching document text before ciência, a generic ORM, tool
renames without the matching skill updates.
