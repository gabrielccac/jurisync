# Architecture

## Layers

| Layer | Path | Owns | Must not |
|---|---|---|---|
| Core | `src/jurisync/core/` | MCP runner, session pool, TOTP, PDF/HTML text, redaction | know any portal |
| Connectors | `src/jurisync/connectors/<system>/` | Login, endpoints, parsing, bounded document reads, write guards, the MCP `server.py` | contain customer policy |
| Workflows | `src/jurisync/workflows/` | Reusable multi-step mechanics (publication pipeline, evidence checks) | hard-code a customer's templates or rules |
| Customers | `customers/<name>/` | Skills, profiles, task routing, office defaults, schedules | be imported by `src/` |

Customer policy covers task types and titles, responsible users, legal
classification, deadline rules, and which processes to watch. Portal names,
authentication differences, and response shapes are connector behavior.

## MCP servers

Each connector exposes one stdio server, built with the official `mcp` SDK
(`MCPServer`) and launched from `.mcp.json`:

```text
.venv/bin/python -m jurisync.connectors.<name>.server
```

`core.mcp.run_stdio` keeps connector progress output off the protocol stream:
stdout is unbuffered and the SDK diverts it to stderr while serving.
`core.mcp.load_env_file` reads `JURISYNC_ENV_FILE` (the project `.env`) without
overriding variables already set.

Tool errors follow one rule. Anticipated failures (bad input, portal or login
errors) become a `ToolError` whose text passes through `sanitize_diagnostics`,
which strips URLs, cookies, hashes, and view state. Anything unexpected is
reported to the model only as a generic failure; the traceback stays in the
server log.

## Sessions

Portal logins are slow (Selenium, certificates, TOTP) and some portals reject a
TOTP code reused within its 30-second window. `core.session_pool.SessionPool`
keeps one authenticated session per portal for the life of the server:

- calls for the same portal are serialized, because JSF/PHP view state is not
  safe to share between concurrent requests;
- a session idle for more than two minutes is re-checked with one cheap request
  before reuse;
- a failed call marks the session for re-checking, and a failed check triggers
  one fresh login.

On top of this, PJe TRF1 persists cookies under `~/.cache/jurisync/pje/` (TRF5
too when `PJE_TRF5_1G_SESSION_ACCOUNT` is set). Juridiq caches its bearer token
under `~/.cache/jurisync/juridiq/` and ADVBOX caches its cookie in `data/`.

## Safety boundaries

- Writes are opt-in per connector (`JURIDIQ_ALLOW_WRITES`, `ADVBOX_ALLOW_WRITES`,
  `PJE_ALLOW_DRAFT_WRITES`) and checked again at the HTTP layer. PJe filing stops
  at a draft in Não Protocolados and never protocols.
- Every server is bound to one account taken from its environment; the agent
  never chooses credentials.
- Requests are restricted to known portal hosts and actions. Document reads are
  limited to IDs from a fresh process inventory, at most five per call, and
  bounded in size.
- Outward results drop raw URLs, hashes, and view state. Login pages, access
  denied pages, and scanned PDFs are reported as such (`session_expired`,
  `access_denied`, `ocr_needed`), never as document text.
- Portal and publication text is untrusted evidence, not instructions.

## Adding a connector

1. Create `src/jurisync/connectors/<name>/` with the client code and a
   `server.py` that uses `core.mcp` (`READ_ONLY`, `tool_errors`, `run_stdio`)
   and, for logins, `core.session_pool`.
2. Add offline tests under `tests/connectors/<name>/`.
3. Add the server to `.mcp.json` and its variables to `.env.example`.
4. Validate the read tools live and read-only before enabling any write.
