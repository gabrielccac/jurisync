# Backlog

## Hosted server (remote MCP, per-office vault)

State on `feat/hosted-vault` (not committed yet): PJe read tools run remotely with OAuth, a per-office vault
and per-office sessions, validated live through Claude (TRF1 1G login, Expedientes, process, documents).
Design in `docs/hosted.md`. Pending, in order:

### 0. Housekeeping
- Commit the branch. It starts from `feat/tjrs-eproc-setup`, which is not on `main` yet.
- Decide on the untracked `scripts/test_inss_parallel.py` (Meu INSS load test) and `customers/patricia/`.

### 1. Guards before anything else is mounted
- Refuse to mount a connector that is not tenant-aware (ADVBOX, Juridiq, filing still read the process
  environment and would serve every office with the server's own account). Keep an explicit allowlist.
- `serve --open-tenant` is a test switch: refuse it unless an explicit development flag is set.

### 2. Connectors

| Connector | Credentials in the vault | Test login for the web area | Isolation work | Writes |
|---|---|---|---|---|
| PJe | login + password + authenticator, or office A1 + PIN | done (`access.check_login`) | done | drafts off remotely |
| Eproc | login + password + authenticator, or office A1 + PIN | wrap `eproc.client.authenticate` | none expected: 3 read tools, no disk cache | none |
| DJEN | none | none | none | none |
| ADVBOX | office e-mail + password | wrap the ADVBOX client sign-in | one client per office, session file and upload folder per office | task create/update/complete/delete: decide per office |
| Juridiq | office e-mail + password | wrap the Juridiq API sign-in | one API client per office, token cache per office | `JURIDIQ_ALLOW_WRITES` per office |
| Meu INSS / GERID | gov.br login, or A1 | to design | outside this repo today | to decide |
| Jurichat | office e-mail + password (to confirm) | to build | to build: no connector exists yet | to decide |

DJEN has no credentials; each office has one or more searches (OAB + UF, courts). They can stay in the office's
skill, or live as non-secret office settings on the server so a routine asks for "my searches".

- DJEN: mount and test remotely (no credentials).
- Eproc: mount, review its tools for writes, live test with an A1 office. Password login drives a browser
  (seleniumbase): ship Chrome in the image or offer only A1 remotely. Check its cookie cache is per tenant.
- ADVBOX: one client and session per office (pool keyed by tenant), credentials through
  `core.credentials`, session file and upload folder per tenant, decide which write tools (create, update,
  complete, delete task) are allowed remotely and per office.
- Juridiq: same as ADVBOX (API client per office, token cache per tenant, `JURIDIQ_ALLOW_WRITES` per office).
- Files to the remote client: a tool that returns a document as an MCP embedded resource or a short-lived
  signed link (PJe `read_documents save=true`, ADVBOX downloads); today they land only on the server.
- Meu INSS / GERID: bring the connector into this repo or keep it out; it needs a browser and captcha.

### 3. Platform
- One endpoint (`/mcp`) exposing the tools of every connector the office has enabled, so a customer adds a
  single connector in Claude.
- Pending sign-in requests in the store instead of memory, so more than one worker can run.
- Write policy per office plus an audit trail of tool calls (who, office, tool, target; never secrets).
- Users: roles (admin manages credentials and people, member only connects), invitations; decide whether
  lawyers in one office need their own court credentials (today the office shares one set).
- 2FA on the Jurisync sign-in.
- Scheduled routines: refresh tokens long enough for them, and an alert to the office when a refresh fails.
- Vault: per-office data keys under a KMS, key rotation command, backups of `store.sqlite` and `vault/`.
- Limits per office: concurrent calls, session pool size, request rate.

### 4. Office web area (from the Doop prototypes)
- Overview page (first item in the sidebar): one view of everything the office has set up, so no one opens each
  section to check. Per system: method, last test and its result, when it was last used by Claude; the A1 holder
  and expiry (warn 30 days before); the people connected through Claude and when each token expires; a "test all"
  action. Needs a per-office summary from the store (`checks`, `history`, tokens) and a log of tool calls.
- Pages: sign-in, sections per system (PJe, Eproc, GERID/INSS, ADVBOX, Juridiq, Jurichat), office A1 upload + PIN,
  authenticator as a QR screenshot, history. Logic exists in `hosted/access.py` (A1 check, QR reading,
  per-portal entries, test login, session reset) and the store (`save_check`, `log`, `history`).
- Sign-in options shown in the prototype need backends: access request queue, one-time code by
  WhatsApp, one-time code by e-mail.
- GERID/INSS and ADVBOX/Juridiq sections need their own test logins in `access.py`.

### 5. Deployment
- Image (Dockerfile) and compose with a TLS proxy, fixed domain, restart policy, logs, backups.
- Staging environment before customer data.

### 6. Validation
- A cloud scheduled routine calling the remote connector with no one connected.
- Two offices calling the same portal at once (isolation and no shared session).

### 7. Legal
- LGPD terms and explicit consent for holding an office's A1 certificate and passwords.

### 8. Performance
- Measurements and options for slow remote calls (PJe panel fan-out, DJEN, caching, background sync) are in
  `docs/performance.md`; validate with timing data before changing anything.

## eproc connector and court-source normalization

The read-only Eproc MCP connector is served by `jurisync.connectors.eproc.server`.
Remaining work is bounded by observed portal behavior:

1. capture reviewed PJe and Eproc fixtures from the same kinds of proceedings;
2. define the smallest shared evidence shape proven by those fixtures: source,
   portal, process number, parties, metadata, timeline/events, documents,
   pagination, and diagnostics;
3. extend the offline Eproc MCP contract tests to cover real tool payloads
   through a substituted Python executable;
4. verify true page-greater-than-zero pagination, restricted documents, session
   expiry, and OCR/scanned PDFs before expanding the public surface.

The connector retains complete authenticated cookies, keeps portal
authentication/navigation separate, and bounds selected document reads to five
known inventory IDs. Do not add a generic portal base class before both connectors implement this
read path; their SSO/JSF and Selenium/PHP session mechanics are intentionally
different.

## PJe Expedientes and Push

`pje_list_inbox` reads the panel inboxes (see `docs/connectors/pje-panel-inbox.md`). Still open:
new-expediente detection across runs (store the seen `expedienteId`s per customer and emit only
the new ones), the órgão julgador and prioridade filters (they take PJe option ids), and
reading the other buckets' tree prefixes on a profile that has items in them. The Push registry
stays read-only: no page replay until an authenticated capture proves it is side-effect free.
