# Hosted server

One remote MCP server for several offices. A caller (Claude, ChatGPT) signs in with OAuth as a user of one office
(tenant); every tool call then runs with that office's portal credentials, read from an encrypted vault on the server.
Local installs are unchanged: without a tenant, connectors read `os.environ` as before.

Connectors served to offices: PJe, Eproc, DJEN, ADVBOX and Juridiq (`TENANT_READY` in `hosted/app.py`). PJe draft
writes stay off; ADVBOX and Juridiq writes answer only for an office whose credentials allow them.

The office adds one URL in Claude or ChatGPT: `https://<host>/mcp`. It serves the tools of every connector enabled for
that office (tool names start with their system, so `advbox_*` is absent for an office without ADVBOX). The
per-connector URLs (`/pje/mcp`, `/advbox/mcp`, ...) stay for older setups.

## Pieces

| Piece | Path | Role |
|---|---|---|
| Credential scope | `src/jurisync/core/credentials.py` | Where connectors read accounts. Hosted: the tenant's values only, never the server environment. Local: `os.environ`. Files (A1) are bytes in memory. |
| Session pool | `src/jurisync/core/session_pool.py` | One portal login per `(tenant, portal)`. |
| Vault | `src/jurisync/hosted/vault.py` | One AES-256-GCM file per tenant; the tenant id is authenticated data; master key in `JURISYNC_VAULT_KEY`. |
| Store | `src/jurisync/hosted/store.py` | SQLite: tenants, users (scrypt passwords), OAuth clients, codes and tokens (SHA-256 hashes only). |
| OAuth | `src/jurisync/hosted/oauth.py` | The SDK's `OAuthAuthorizationServerProvider`: dynamic client registration, PKCE, a sign-in page, rotating refresh tokens. |
| App | `src/jurisync/hosted/app.py` | OAuth endpoints at the root; `/mcp` and `/<connector>/mcp` behind a bearer token; `TenantGate` opens the tenant's credential scope per request; `Office` lists and calls only the office's connectors and records each call. |
| CLI | `src/jurisync/hosted/cli.py` | `jurisync-hosted`: init, tenants, vault import, users, call log, serve. |

## Request flow

1. The client calls `/mcp` without a token, gets `401` with the protected resource metadata URL, discovers the
   authorization server, registers itself and sends the user to `/authorize`.
2. `/authorize` parks the request and redirects to `/login`. The user signs in with an office e-mail and password; the
   code records the user's tenant.
3. `/token` exchanges the code (PKCE checked by the SDK) for a one-hour access token and a 30-day refresh token.
4. On each MCP request the SDK validates the token; `TenantGate` checks the tenant still exists and the token was
   issued for this resource (a token for `/mcp` works only on `/mcp`, one for `/pje/mcp` only there), then opens
   `credentials.scope(tenant, vault values)`. A per-connector URL answers 403 when the connector is not enabled for the
   tenant; on `/mcp`, `tools/list` shows only the enabled connectors' tools and a call to another one returns an error.
5. The SDK carries that context into the tool call (including the worker thread of a sync tool). The connector logs
   in with the tenant's A1 and TOTP; the session pool keeps that login apart from other tenants.
6. Every tool call, on either URL, writes one row to the store: office, user, connector, tool, ok or error type,
   duration. Arguments and results are never stored; rows older than 90 days are purged.

## Isolation guarantees

- A tenant scope reads only its own values: a name it lacks is empty, so the server's or another office's credentials
  are never used as a fallback.
- Sessions, cookie caches (identity includes the tenant), saved documents (`<documents>/tenants/<id>`) and filing runs
  (`<filings>/tenants/<id>`) are per tenant.
- MCP sessions are bound to the token's principal by the SDK, so one office cannot resume another's session.
- The vault decrypts per request; the A1 certificate is never written to disk on the server.
- Write tools (`pje_draft_write`, `pje_draft_delete`) are removed from the hosted server, on every URL.
- Every login, including the operator's `portal check`, goes through the session pool: at most
  `JURISYNC_MAX_LOGINS` at once, and credentials a portal rejected are not sent again for the cool-down
  (`JURISYNC_LOGIN_COOLDOWN_SECONDS`, 15 min) unless the access is changed.

## Pilot runbook

```bash
jurisync-hosted init                                  # prints a vault key: keep it outside the server
export JURISYNC_VAULT_KEY=...                         # from a secret manager or systemd credential
jurisync-hosted tenant add escritorio-teste --name "Escritório Teste" --customer victor
jurisync-hosted secrets import-bundle escritorio-teste cliente.jurisync --portal trf5-1g --check
jurisync-hosted secrets list escritorio-teste         # names only
jurisync-hosted user add pessoa@escritorio.com.br --tenant escritorio-teste
jurisync-hosted serve --base-url https://mcp.exemplo.com.br --port 8080 --connectors pje eproc djen advbox juridiq
```

The bundle's A1 file is stored as bytes; `*_2FA_QR` images become `*_2FA_DATA` text.
With `--portal`, only the names that portal declares in the customer's `portals.toml` are stored, and missing ones are
reported.

Expose the server over HTTPS (Caddy, nginx, or a Cloudflare Tunnel pointing at `127.0.0.1:8080`); `--base-url` must be
the public address. In Claude or ChatGPT, add a custom connector with `https://mcp.exemplo.com.br/mcp` and sign in on
the page the server opens.

## Registering an office's access

Two ways in, both ending in the same vault entry. Secrets never go through chat, e-mail, messaging, or a terminal on
our side.

| Path | Who | How |
|---|---|---|
| Encrypted bundle | Our team, from credentials set up with the office | `jurisync-setup export` produces a passphrase-sealed file; `jurisync-hosted secrets import-bundle <office> <file> --portal <key> --check` stores it only if the PJe logins get in |
| The office's own page | The office; no one on our team sees the secrets | To build. It signs the office user in, takes login + password + authenticator (text or QR image) per portal, or the A1 file + PIN once for the office, and stores each access only after a test login |

`hosted/access.py` already holds the page's logic: `read_certificate` (opens the A1, reports holder and expiry,
refuses a wrong PIN), `authenticator_accounts`/`single_account_uri` (text or QR image to one `otpauth://totp` URI,
choosing among several accounts), `with_portal`/`without_portal` (per-portal vault entries for each method),
`portal_state` (what to show, never the secrets), `check_login` (a real login in the office's scope) and
`forget_sessions` (drop the office's open login after a change). The store keeps per-portal test results
(`save_check`/`checks`) and the history (`log`/`history`).

Operator commands that take no secrets:

```bash
jurisync-hosted portal list <office>              # A1 holder and expiry; per portal: method, 2FA, last test
jurisync-hosted portal check <office> trf1-1g     # test the stored access again
jurisync-hosted portal remove <office> trf1-1g
jurisync-hosted certificate remove <office>
jurisync-hosted history <office>                  # who changed what; never values
jurisync-hosted calls <office> --limit 50         # tool calls: user, tool, ok/error, duration; never arguments
```

Sign-in pages (OAuth) pause an e-mail after 8 wrong passwords in 15 minutes. `jurisync-hosted user password <e-mail>`
changes a Jurisync user's password and ends that user's sessions and tokens.

## Validation per office

Run for every office before it uses the server for real. Each step names what proves it.

1. **Registration.** We create the office and its admin user. The admin signs in to the web area.
2. **Credentials.** For each system the office uses, "Test and save" logs in and stores only on success; a wrong
   password or PIN, an expired A1 and a court that is down each show a clear message and store nothing.
3. **Overview.** Every system the office set up shows its method and a successful test; the A1 shows holder and
   expiry.
4. **Claude connection.** A user adds `https://<host>/mcp` in Claude (or ChatGPT) and signs in on the Jurisync page.
   Proof: the tool list has only the office's systems, and `jurisync-hosted calls <office>` shows the user's first call.
5. **One read per system, with the office's own account.** PJe panel counters, an Eproc process, a DJEN search
   with the office's OAB, ADVBOX tasks, Juridiq publications. Proof: the numbers match what the office sees in each
   portal when it signs in itself.
6. **Isolation.** A second office calls the same court at the same time: each sees only its own data, and the
   server opens one login per office.
7. **Scheduled routine.** A cloud routine with the office's connector runs with no one connected and produces its
   result. Proof: the routine output and the server log.
8. **Changes take effect.** A new password in the web area is used on the next call; removing a user or changing
   their password stops their Claude connector.


## Known limits of the pilot

- Pending sign-in requests live in memory: run one process. Move them to the store before running several workers.
- PJe draft writes stay off; ADVBOX and Juridiq writes follow the office's `*_ALLOW_WRITES`.
- The master key encrypts every tenant. A KMS or Vault transit key (per-tenant data keys) replaces it in production.
- Portal access comes from the server's IP. GOV.BR and some PJe deployments may challenge datacenter IPs.
- Holding an office's A1 certificate is holding its signature: the contract and LGPD terms must cover it.
