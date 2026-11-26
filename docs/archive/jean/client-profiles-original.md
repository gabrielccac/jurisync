# Client profiles and authentication

The pipeline mechanics are shared. Client-specific policy is supplied as a
profile to `runPublicationPipeline({ profile })`.

## What belongs in a profile

```json
{
  "id": "client-slug",
  "language": "pt-BR",
  "skillReferences": ["path/to/base-skill.md", "path/to/classification.md"],
  "taskTemplates": {
    "manifestacao": "Manifestação - (Nome Cliente)",
    "pericia_medica": "Comunicação Perícia Médica Federal - (Nome Cliente)"
  },
  "requiredTaskTypes": ["manifestacao", "pericia_medica"],
  "classification": {
    "rulesReference": "path/to/classificacao.md",
    "unknownAction": "review_and_leave_unread",
    "useProvisionalTasks": true
  },
  "eventPolicy": {
    "enabled": true,
    "duplicateCheck": "indirect_task_process_date"
  }
}
```

The template title is resolved against the live Juridiq account on every run.
Do not copy template, user, column or tag IDs between clients: those values are
account-scoped and can change. The agent receives the live template objects and
the profile together, then returns the semantic task type. The runner resolves
that type to the configured live title and builds the API payload from the live
model.

The following remain common across clients:

- unread polling and pagination;
- publication normalization and evidence preservation;
- act-level deduplication;
- lawsuit resolution;
- PJe process/movement/document enrichment;
- existing-task checks and idempotency;
- task verification and read-state finalization;
- error, ambiguity and retry handling.

The following are client policy and should be overridden in the profile or its
referenced skill files:

- publication type to task type mapping;
- no-action rules;
- provisional fallback model;
- deadline interpretation and working-day rules;
- task title conventions and responsible users;
- event titles, responsible users and calendar rules;
- PJe portal/court special cases.

## Current client

`integrations/tools/juridiq/profiles.mjs` exports `DEFAULT_PROFILE`, which preserves
the current client's 17 task-template mappings and references the existing
classification/PJe rules. It is the default for compatibility, not a global
assumption that future tenants should inherit unchanged.

## Credential flow

Credentials are runtime secrets. They must not be placed in a skill, profile,
task description, evidence object or LLM prompt.

For the current local integration:

```text
JURIDIQ_EMAIL / JURIDIQ_PASSWORD       configured Juridiq account
PJE_1G_USERNAME / PJE_1G_PASSWORD / PJE_1G_2FA_DATA
PJE_2G_USERNAME / PJE_2G_PASSWORD / PJE_2G_2FA_DATA
```

Node loads the populated `.env` before startup. `session.mjs` logs in only when
the account session is missing or stale, caches the bearer session separately
per account/email, and refreshes once after a 401. It does not log in for every
request. Python PJe callers reuse their authenticated session when the caller
passes it through the workflow.

For a multi-client product, the host service should resolve a tenant ID to a
secret-manager record and start each enabled connector with that tenant's
credentials. The LLM receives only the tenant/profile identifier and tool
results; it never selects an account at tool-call time.

Write access is disabled by default. Juridiq mutation tools are registered only
when the connector starts with `JURIDIQ_ALLOW_WRITES=true`; the HTTP client also
enforces that guard. Production use should retain per-tenant capabilities, audit
logging and the same read-before-write checks.
