# Juridiq integration

This directory is the canonical home for Juridiq-specific code:

- `session.mjs` — bearer login, account-scoped session cache, and mutation guard.
- `client.mjs` — named HTTP operations and response normalization.
- `pipeline.mjs` — agent-orchestrated publication-to-task workflow.
- `profiles.mjs` — default client policy and task-template mapping.
- `publications.py` — local publication/evidence preparation and decision validation.
- `tasks.py` — skill task-type/template mapping and payload validation.

The former top-level `integrations/pipeline.mjs`, `integrations/publications.py`, and
`integrations/tasks.py` remain thin compatibility entry points for older imports.
PJe code belongs in `integrations/pje`; it is called by the host agent's enrichment
callback and never receives Juridiq credentials.

Every operation receives an explicit `operational` or `test` account. Credentials
are runtime configuration, never skill/profile data: use the corresponding
credential pair for the selected account. Writes additionally require
`JURIDIQ_ALLOW_WRITES=true` and confirmation at the MCP tool boundary. The
session is cached and reused until expiry or 401.
