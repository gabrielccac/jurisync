# Hosted server

One remote MCP server for several offices. A caller (Claude, ChatGPT) signs in with OAuth as a user of one office
(tenant); every tool call then runs with that office's portal credentials, read from an encrypted vault on the server.
Local installs are unchanged: without a tenant, connectors read `os.environ` as before.

Connectors served to offices: every connector of `src/jurisync/registry.py` (PJe, Eproc, e-SAJ, Projudi, STJ,
Tucujuris, JPe, DJEN, ADVBOX and Juridiq); `hosted/app.py`, `provision.py`, `cli.py` and `access.py` read that list.
The PJe draft tools (`local_only_tools`) stay on the office's computer; ADVBOX and Juridiq write tools are listed and
answer only for an office whose own grant (`ADVBOX_ALLOW_WRITES`/`JURIDIQ_ALLOW_WRITES` in its vault entry or
`[env].set` of its profile) is `true` (`GRANTED_WRITES`); the server's environment never grants them. A court write
(juntada, protocolo) works the same way: its connector declares `write_grant`/`write_tools` in the registry, and the
tools stay hidden and refused on `/tribunais/mcp` for an office without that grant.

Deployment (VPS install, systemd, HTTPS, backups, smoke test): `deploy/README.md`.

Connectors an office adds in Claude or ChatGPT (`registry.endpoints`, also in each office's `plan.json`):

| Connector | URL | Serves |
|---|---|---|
| Jurisync Tribunais | `https://<host>/tribunais/mcp` | Every court system the office has (PJe incl. PJe-JT, Eproc, e-SAJ, Projudi, STJ, JPe, Tucujuris) and DJEN: the connectors with `group="tribunais"` in the registry. |
| Jurisync ADVBOX | `https://<host>/advbox/mcp` | ADVBOX, for an office that uses it. |
| Jurisync Juridiq | `https://<host>/juridiq/mcp` | Juridiq, for an office that uses it. A future management system (CRM, ...) gets its own `/<name>/mcp` the same way. |

Each URL lists only the systems enabled for the office and, per system, only its portals (tool names start with their
system). `https://<host>/mcp` (every connector at one URL) is **deprecated**: it keeps working for the offices that
added it (Victor), but new offices get the URLs above. The per-system court URLs (`/pje/mcp`, `/eproc/mcp`, ...) also
stay for older setups.

## Pieces

| Piece | Path | Role |
|---|---|---|
| Credential scope | `src/jurisync/core/credentials.py` | Where connectors read accounts. Hosted: the tenant's values only, never the server environment. Local: `os.environ`. Files (A1) are bytes in memory. |
| Session pool | `src/jurisync/core/session_pool.py` | One portal login per `(tenant, portal)`. |
| Vault | `src/jurisync/hosted/vault.py` | One AES-256-GCM file per tenant; the tenant id is authenticated data; master key in `JURISYNC_VAULT_KEY`. |
| Store | `src/jurisync/hosted/store.py` | SQLite: tenants, users (scrypt passwords), OAuth clients, codes and tokens (SHA-256 hashes only). |
| OAuth | `src/jurisync/hosted/oauth.py` | The SDK's `OAuthAuthorizationServerProvider`: dynamic client registration, PKCE, a sign-in page, rotating refresh tokens. |
| App | `src/jurisync/hosted/app.py` | OAuth endpoints at the root; `/tribunais/mcp`, `/<connector>/mcp` and the deprecated `/mcp` behind a bearer token; `TenantGate` opens the tenant's credential scope per request; `Office` lists and calls only the office's connectors and records each call. |
| CLI | `src/jurisync/hosted/cli.py` | `jurisync-hosted`: init, tenants, vault import, users, call log, serve. |
| Provisioning | `src/jurisync/hosted/provision.py` | `provision export` (our machine: plan + sealed bundle per office from `customers/`), `provision apply` (server: tenants, vault, profiles, users), `provision plugin` (office plugin pointing at `/tribunais/mcp` and one URL per management system). |

## Request flow

1. The client calls `/tribunais/mcp` (or another endpoint) without a token, gets `401` with the protected resource metadata URL, discovers the
   authorization server, registers itself and sends the user to `/authorize`. Registration accepts redirect URIs only
   on Claude's and ChatGPT's hosts (https), on localhost, or on an https host listed in `JURISYNC_OAUTH_REDIRECT_HOSTS`
   (comma separated; the sign-in page still warns about those). Any other client is refused, also one registered before.
2. `/authorize` parks the request and redirects to `/login`. The user signs in with an office e-mail and password; the
   code records the user's tenant. A password the operator set (`user add`, `user password`, `provision apply`, and
   every password stored before this rule) must be replaced on that page before the code is issued; any user may also
   tick "Quero trocar minha senha". A new password ends the user's other sign-ins.
3. `/token` exchanges the code (PKCE checked by the SDK) for a one-hour access token and a 30-day refresh token.
4. On each MCP request the SDK validates the token; `TenantGate` checks the tenant still exists and the token was
   issued for this resource (a token for `/tribunais/mcp` works only there, one for `/advbox/mcp` only there), then
   opens `credentials.scope(tenant, vault values)`. A URL answers 403 when none of its connectors is enabled for the
   tenant; `tools/list` shows only the enabled connectors' tools and a call to another one returns an error.
   A tool's `portal` choices are only the portals the office has a login for (the connector's `configured_portals()`
   in the office's scope); a call naming another portal is refused, and a connector with no portal configured is not
   listed for the office. The `portal` field says only "Portal key; see the enum"; the first tool of each system lists
   the office's portals once with their hosts (`Portals: trf5-1g (pje1g.trf5.jus.br), ...`). `/healthz` answers `{"ok": true}` without a token.
5. The SDK carries that context into the tool call (including the worker thread of a sync tool). The connector logs
   in with the tenant's A1 and TOTP; the session pool keeps that login apart from other tenants.
6. Every tool call, on any URL, writes one row to the store: office, user, connector, tool, ok or error type,
   duration. Arguments and results are never stored; rows older than 90 days are purged.

## Isolation guarantees

- A tenant scope reads only its own values: a name it lacks is empty, so the server's or another office's credentials
  are never used as a fallback.
- Sessions, cookie caches (identity includes the tenant), saved documents (`<documents>/tenants/<id>`) and filing runs
  (`<filings>/tenants/<id>`) are per tenant.
- MCP sessions are bound to the token's principal by the SDK, so one office cannot resume another's session.
- The vault decrypts per request; the A1 certificate is never written to disk on the server.
- The PJe draft tools (`pje_draft_write`, `_delete`, `_status`, `_inspect`) are removed from the hosted server, on
  every URL.
- Server-wide settings (`JURISYNC_CAPTCHA_API_KEY`, `JURISYNC_CAPTCHA_MODEL`) are read from the process environment
  or the service's systemd credentials (`credentials.machine`), also inside an office's scope; an office's vault
  cannot set them. The vault key is also read from `$CREDENTIALS_DIRECTORY/JURISYNC_VAULT_KEY`.
- A3 tokens (`*_A3_PIN`, `PJE_AUTH`/`EPROC_AUTH`/`<PREFIX>_AUTH`) and `PJE_ALLOW_DRAFT_WRITES` are dropped on every
  import: they never reach a vault entry.
- `serve` refuses to start while an office's customer profile (`portals.toml`) is missing from
  `JURISYNC_CUSTOMERS_DIR`: the profile pins per-portal methods and settings, and without it the server would try
  another login.
- Every login, including the operator's `portal check`, goes through the session pool: at most
  `JURISYNC_MAX_LOGINS` at once, and credentials a portal rejected are not sent again for the cool-down
  (`JURISYNC_LOGIN_COOLDOWN_SECONDS`, 15 min) unless the access is changed. A call waits at most
  `JURISYNC_LOGIN_WAIT_SECONDS` (5 min) for a login slot and `JURISYNC_SESSION_WAIT_SECONDS` (10 min) for the office's
  previous call on the same portal, then fails with "try again in a few minutes" instead of hanging; an Eproc login
  whose CAPTCHA page does not clear after 3 clicks fails with `[captcha]` and closes its Chrome.
- The event loop serves every office, so the store (SQLite), the vault (read, decrypt, hash) and the profile's portal
  list are read in worker threads: a busy SQLite file or a large vault slows one request, not all of them.
- Each call's scope carries a signature (SHA-256) of the office's credentials as the server reads them. A pooled
  login, an ADVBOX client, a Juridiq token and a cool-down are kept with the signature they were made under: when the
  operator changes the vault from another process (`secrets import-bundle`, `certificate remove`, `portal remove`,
  `provision apply`), the next call drops them and logs in with the new credentials. `ADVBOX_DEVICE_ID`, which the
  server renews itself, is left out of the signature.
- The office's profile wins over its vault: `JURISYNC_CUSTOMER` comes from the tenant and the profile's `[env].set`
  values override a vault value of the same name. `serve`, `portal list`, `portal check` and `import-bundle --check`
  read the same values (`access.tenant_values`).

## Provisioning every office

`customers/<name>/portals.toml` is the source of each office's portals. On our machine:

```bash
uv run jurisync-hosted provision export --out ~/rollout --passphrase-file ~/.config/jurisync/rollout.passphrase \
  --customers-dir ~/orca/jurisync/customers --customer victor fernanda
```

Per office it keeps the portals the server can log in to and says why the others stay out: A3 portals (`*_A3_PIN`),
portals with a declared secret missing in the office's own `.env` (the shared root `.env` is never read), systems not
served. Legacy names (`*_USUARIO`, `*_SENHA`, `*_EXPORT_DATA`) are stored under the standard names; QR images become
`*_2FA_DATA`; the A1 file becomes bytes; the office's own write grants go along; pinned values (`[env].set`) stay in
the profile, which the server reads on every call. Connectors follow
from the kept portals (+ `advbox`/`juridiq` when their login is set, + `djen`). Output: `plan.json` (names, reasons,
users; no values), `<office>.jurisync` sealed with the rollout passphrase (a 0600 file kept apart from the folder,
never printed) and `profiles/<customer>/` (five customer folders are not in the repository).

On the server, `jurisync-admin provision apply <folder> --passphrase-file <file>` creates or updates the tenants,
installs the profiles in `JURISYNC_CUSTOMERS_DIR`, stores each bundle without a login and adds the plan's users with
random passwords written to a 0600 file. An `ADVBOX_DEVICE_ID` already in the vault (the server renews it) is kept over
the bundle's, also on `import-bundle`; `--replace-renewed` takes the bundle's. Switching an office to local deletes
its vault entry, users and login checks. The first login of each portal is `deploy/check-office.sh <office>`, from the
server, one portal at a time.

### Local-only offices

An office that keeps its own install (today barbara and cinthia: their logins use an A3 token on the office's
computer) says so in its committed `customers/<name>/settings.toml`:

```toml
[hosted]
mode = "local"
reason = "A3 — instalação local"
```

`provision export` then plans it with no portal, connector, user or bundle (every portal is listed as skipped with the
reason), and `provision apply` keeps only a record: the tenant exists with `mode = local` and the reason, its vault
entry and users are deleted if an earlier hosted setup left any, and no credentials are imported. `tenant list` and
`portal list` show "só local: <reason>"; `tools/list` is empty for it and any call answers that the office uses its
local install. `user add`, `secrets import-bundle` and `tenant connectors` refuse it. `tenant add <id> --local
"<reason>"` creates one by hand. Removing the `[hosted]` table and running export/apply again makes it hosted.

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
the public address. In Claude or ChatGPT, add a custom connector with `https://mcp.exemplo.com.br/tribunais/mcp` (and
`/advbox/mcp` or `/juridiq/mcp` for an office that uses them) and sign in on the page the server opens.

## Registering an office's access

Two ways in, both ending in the same vault entry. Secrets never go through chat, e-mail, messaging, or a terminal on
our side.

| Path | Who | How |
|---|---|---|
| Encrypted bundle | Our team, from credentials set up with the office | `jurisync-setup export` produces a passphrase-sealed file; `jurisync-hosted secrets import-bundle <office> <file> --portal <key> --check` stores it only if the logins get in |
| The office's own page | The office; no one on our team sees the secrets | To build. It signs the office user in, takes login + password + authenticator (text or QR image) per portal, or the A1 file + PIN once for the office, and stores each access only after a test login |

`hosted/access.py` already holds the page's logic: `read_certificate` (opens the A1, reports holder and expiry,
refuses a wrong PIN), `authenticator_accounts`/`single_account_uri` (text or QR image to one `otpauth://totp` URI,
choosing among several accounts), `with_portal`/`without_portal` (per-portal vault entries for each method),
`portal_state` (what to show, never the secrets), `check_login` (a real login in the office's scope) and
`forget_sessions` (drop the office's open login after a change). The store keeps per-portal test results
(`save_check`/`checks`) and the history (`log`/`history`).

Operator commands that take no secrets:

```bash
jurisync-hosted portal list <office>              # A1 holder and expiry; the office's portals (its profile's, else
                                                  # those in its vault), any system: method, 2FA, last test
jurisync-hosted portal check <office> trf1-1g     # test the stored access again; any portal, advbox or juridiq
jurisync-hosted portal remove <office> tjmg-1g    # any portal
jurisync-hosted certificate remove <office>       # also drops every portal's open login and cookie cache
jurisync-hosted history <office>                  # who changed what; never values
jurisync-hosted calls <office> --limit 50         # tool calls: user, tool, ok/error, duration; never arguments
```

Sign-in pages (OAuth) pause an e-mail after 8 wrong passwords in 15 minutes. `jurisync-hosted user password <e-mail>`
changes a Jurisync user's password and ends that user's sessions and tokens.

## Monitoring API (internal)

`src/jurisync/hosted/monitor.py`, read-only JSON at `/monitor`, served only when the service has `JURISYNC_MONITOR_TOKEN` (systemd
credential; every request sends it as `Authorization: Bearer`). It is the operator's, not an office's: wrong tokens are
throttled per address, and without the variable the paths answer 404.

| Path | Answers |
|---|---|
| `/monitor/status` | The process (since when, address), offices, users signed in, portals by state, open sessions, login blocks, calls of the last 24 h, one line per system |
| `/monitor/portals` | Each portal some office has: reachable now, logins of the last 24 h (with reasons), and per office the last check and last call |
| `/monitor/sessions` | Open portal sessions (idle time, state), logins in cool-down and why, users signed in (client, connector, last call) |
| `/monitor/calls?office=&connector=&portal=&errors=1&limit=100` | The latest tool calls |
| `/monitor/usage?hours=24&office=` | Calls, errors, median and p95 duration per office, system, portal, tool and user; errors and login results by reason |

Reachability is an anonymous GET of each portal's entry page every `JURISYNC_PROBE_SECONDS` (600; `0` off): `no ar`, `recusando` (403/429,
usually a WAF) or `fora do ar`. A portal that answers may still refuse a login, which `logins_24h` shows. Each call row
also keeps the portal key and the login failure reason, and each login a pool tries is one row (`logins`); both are
purged after 90 days. Arguments, results and process numbers are never stored.

## Validation per office

Run for every office before it uses the server for real. Each step names what proves it.

1. **Registration.** We create the office and its admin user. The admin signs in to the web area.
2. **Credentials.** For each system the office uses, "Test and save" logs in and stores only on success; a wrong
   password or PIN, an expired A1 and a court that is down each show a clear message and store nothing.
3. **Overview.** Every system the office set up shows its method and a successful test; the A1 shows holder and
   expiry.
4. **Claude connection.** A user adds `https://<host>/tribunais/mcp` (plus `/advbox/mcp` or `/juridiq/mcp` when the
   office uses them) in Claude (or ChatGPT) and signs in on the Jurisync page. Proof: each tool list has only the
   office's systems, and `jurisync-hosted calls <office>` shows the user's first call.
5. **One read per system, with the office's own account.** PJe panel counters, an Eproc process, a DJEN search
   with the office's OAB, ADVBOX tasks, Juridiq publications. Proof: the numbers match what the office sees in each
   portal when it signs in itself.
6. **Isolation.** A second office calls the same court at the same time: each sees only its own data, and the
   server opens one login per office.
7. **Scheduled routine.** A cloud routine with the office's connector runs with no one connected and produces its
   result. Proof: the routine output and the server log.
8. **Changes take effect.** A new password (web area or `jurisync-admin`) is used on the next call; removing a user
   or changing their password stops their Claude connector.


## Known limits of the pilot

- Pending sign-in requests live in memory: run one process (the systemd unit does). Move them to the store before
  running several workers.
- PJe draft writes stay off; ADVBOX and Juridiq writes follow the office's `*_ALLOW_WRITES`.
- The master key encrypts every tenant. A KMS or Vault transit key (per-tenant data keys) replaces it in production.
- Portal access comes from the server's IP. GOV.BR and some PJe deployments may challenge datacenter IPs.
- Holding an office's A1 certificate is holding its signature: the contract and LGPD terms must cover it.
