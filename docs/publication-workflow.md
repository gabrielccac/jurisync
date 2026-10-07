# Publication workflow

Turns unread Juridiq publications into verified Juridiq tasks. The code owns the
mechanics; the calling LLM agent owns the legal judgment, guided by the
customer's skill. There is no embedded LLM.

```python
from jurisync.workflows.publications.pipeline import run_publication_pipeline

result = run_publication_pipeline(agent=agent, profile=PROFILE, state_file="/abs/private/state.json")
```

Customers wrap this; Jean's is `customers.jean.run_publications.run_jean_publications(agent)`.

## Agent contract

Each method receives keyword arguments and returns a plain dict.

| Method | Required | Returns |
|---|---|---|
| `deduplicate(publications, profile)` | yes | `{"groups": [...]}`: every fetched publication in exactly one group, with `primaryPublicationId`, `canonicalActKey`, `reason`, and optional `status: "review"` |
| `assess(publication, lawSuit, context, profile, ...)` | yes | `status`: `sufficient`, `no_action`, `needs_context`, or `review`, plus `reason`, `evidence`, `missingInformation` |
| `enrich(publication, lawSuit, assessment, context, ...)` | for `needs_context` | `{"sources": [{"sourceId", "text"}]}`, for example selected PJe document text |
| `route(publication, assessment, context, lawSuit, existingTasks, templates, profile, ...)` | yes | `tasks` (semantic `type`, `clientName`, `description`, `initialDate`, `obligationKey`, optional `action: "update"` + `existingTaskId`), optional `events`, `classificationRule`, `evidence` |
| `provisional_route(...)` | when an assessment sets `provisional: true` | a route whose task is created first and updated in place after enrichment |

## What the pipeline enforces

- **Grounded evidence.** Every positive decision quotes an exact passage from a
  known source (`publication:<id>` or an enrichment `sourceId`). A missing source
  or a quote not found in it fails that publication.
- **Live templates.** The profile maps semantic task types to template titles;
  titles are resolved against the live account on every run. IDs are never
  copied between accounts.
- **No duplicates.** Existing tasks for the lawsuit are loaded first; an
  explicit `existingTaskId` must belong to that lawsuit; matching
  `obligationKey`/`publicationId` reuses the task.
- **Verified writes.** Every created or updated task is read back before it
  counts.
- **Read last.** Publications are marked read only after their work succeeded,
  in batches of 50. Failures, reviews, and unresolved context stay unread.
- **Resumable state.** Per-publication state is written atomically to the
  caller's absolute `state_file`, keyed by account and profile.

Writes still require `JURIDIQ_ALLOW_WRITES=true`.

## Rules for the agent (carried into customer skills)

1. Assess the publication text first. Attachments, text length, or an "Inteiro
   teor" link do not decide sufficiency. Identify the actual recipient.
2. When context is missing, ask concrete questions, then read only the related
   PJe documents: an explicit reference, the act being answered, examination
   details, or a later act that fulfils the obligation. Do not read every
   same-day movement.
3. Require the exact process. Keep `lawSuitId` and the publication ID separate.
   Availability, publication, signature, and movement dates mean different
   things; date proximity ranks candidates but never proves a match.
4. Surface contradictions instead of merging them. Unavailable, scanned, or
   inaccessible documents leave context unresolved; return `review`.
5. Never invent deadlines, addresses, conclusions, or task IDs. A `sufficient`
   assessment means there is enough context to draft a task, not legal
   certainty.

## Sources are distinct

Juridiq publications, PJe movements, and DJEN communications are different
inputs. A PJe movement is not automatically a publication, and PJe Push lists
watched processes rather than notice text. Preserve each source's ID and
wording.

## Offline evidence tooling

`python -m jurisync.workflows.publications.evidence prepare|validate` builds an
evidence bundle from a saved Juridiq snapshot (plus optional PJe results) and
validates an agent decision against it. It makes no network calls and refuses to
overwrite existing files. Use it to replay reviewed examples before changing
customer rules.

Earlier design notes for Jean's workflow are in [customers/jean/docs](../customers/jean/docs/).
