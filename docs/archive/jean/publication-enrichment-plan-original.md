# Agent-orchestrated publication context

## Decision and scope

The caller (an LLM agent using the eventual skill/API/MCP) performs semantic
assessment and evidence selection. This package does not instantiate an LLM,
choose a provider, or call OpenRouter. An optional provider can be added by a
caller without changing these data contracts.

Juridiq remains the publication source. PJe supplies joined process history and
documents when the publication is insufficient. This is not a diary reconstruction
algorithm and does not label every PJe movement as a publication.

The current implementation adds a stateful Juridiq-first runner in
`integrations/tools/juridiq/pipeline.mjs`. It polls unread publications, invokes the host
agent for assessment/enrichment/routing, resolves live templates, checks task
obligations, creates and verifies tasks, and records per-publication state.
The runner uses the one account configured for the Juridiq connector. Mutations
remain blocked unless `JURIDIQ_ALLOW_WRITES=true`.

The host agent must load the customer skill and its referenced rules. The runner
rejects missing skill templates and task routes without a
classification rule, evidence, explicit dates, or an obligation key. This keeps
the supplied classification/enrichment guidance as the source of truth while the
code remains responsible for validation and API mechanics. Calendar events and
publication read-state updates remain pending until their exact API contracts are
verified.

## Workflow and responsibility

| Step | Code supplies/checks | Calling agent decides |
| --- | --- | --- |
| Prepare | Exact publication ID, original record, distinct dates, normalized text | Actual act, recipient, whether enough evidence exists |
| Assess | Structured decision contract and quoted-source validation | `sufficient`, `no_action`, `needs_context`, or `review` |
| Locate | Explicit process number/portal and full parsed timeline through existing PJe client | Which act matches, using references, text, recipient, and dates |
| Read | Only selected document IDs from that process; bounded downloads; extraction status | Which referenced/neighboring documents answer the missing questions |
| Reassess | New evidence bundle and validated response, saved locally | Whether gaps/conflicts remain; otherwise return to caller |

The state before an agent response is `awaiting_assessment`, not a completeness
guess. A successful validation establishes schema/source consistency, not legal
correctness or certainty of the match. `sufficient` means enough context for the
next task-drafting step; it does not authorize creating a task.

## Matching rules for the caller

1. Assess the publication first. Attachment presence, text length, or an
   `Inteiro teor` link alone cannot determine sufficiency. `term` is monitoring
   metadata, not a deadline. Identify the actual recipient before recommending work.
2. If context is missing, state concrete questions (for example the referenced
   decision's operative order, or the examination's full address).
3. Require the correct process. Retain `lawsuitId` and publication ID separately.
   Missing/ambiguous process numbers need review, not an arbitrary number extracted
   from a cited judgment. Current PJe adapters support TRF1 only (`.4.01`); this
   does not identify the instance. Use an explicit 1G/2G choice. A caller may try
   the other instance after an unsuccessful lookup, but must retain both outcomes;
   authentication/parser failures are not proof that the process is absent.
4. Compare document references/IDs first when present; then act wording/title,
   recipients and date proximity. Availability, publication, signature and movement
   dates have different meanings. Date proximity ranks candidates, never proves
   correspondence. Keep multiple candidates if the match is ambiguous.
5. Read the relevant joined document. Read a neighbor only for a stated dependency:
   explicit reference, response being requested, examination details, or a later
   act that changes/fulfills the obligation. Do not take every same-day movement.
6. Reassess with quoted evidence. Surface contradictory dates/orders instead of
   combining them silently. Unavailable, scanned or inaccessible documents leave
   context unresolved. No invented deadlines, addresses, conclusions or task IDs.

The historical `reference/triagem-publicacoes-juridiq` skill supplies the triage
intent, not a deterministic date/name join. Its provisional-write workflow is
not enabled here. This increment does not replace all its task-template rules.

## Integration layout

- `integrations/tools/juridiq/publications.py`: pure preparation and response validation plus
  JSON CLI. Reads a saved capture and optional PJe/evidence JSON. Does not log in,
  fetch remotely, or choose a model. The calling agent can use the same functions
  through a future thin API/MCP wrapper.
- `integrations/portals/pje/client.py`: existing `fetch_process(..., scope="all",
  session=session)` supplies metadata. Selective document reads use the same
  supplied authenticated session; callers retain it for the workflow and close
  it afterwards. Selection is by known document IDs, not agent-provided URLs.
- `integrations/portals/pje/documents.py`: bounded selected reads and conservative HTML
  extraction. The client reexports `read_selected_documents`; the CLI offers
  repeated `--document-id` arguments for a one-shot fetch in its owned session.
- `integrations/portals/pje/parsers.py`: shared parser preserves all movement labels in a
  block while retaining the existing first-label compatibility field.
- `tests/`: synthetic offline fixtures for contracts, hostile/unknown references,
  complete/incomplete/no-action/ambiguous assessments and selected-document safety.

Store original captures and generated bundles/decisions under ignored `data/`.
Use explicit output filenames; do not overwrite the source snapshot. Preserve
IDs, source dates, evidence references, extraction status and the decision reason.
The 692-record capture is handled historical data: replay must explicitly include
handled records. It is not an unread production work queue or 692 reviewed labels.

## Boundaries and safeguards

- Publication/document text is untrusted evidence, never tool instructions.
  Give the agent only parsed text/metadata, not JSF forms, cookies or login tokens.
- Validate document IDs against the supplied process result; require supported
  HTTPS download origins/paths and reject redirects rather than leaking a session.
  A future hosted wrapper must keep that process result server-side: client-uploaded
  JSON is not an authorization boundary.
- HTTP failure, login/error HTML, empty extraction and PDF without a configured
  extractor must not be presented as successfully extracted judicial text.
- Require exact source quotations for positive decisions; unknown references and
  quotes absent from the supplied text fail validation. This is grounding, not a
  claim that a quotation proves the agent's interpretation.
- Bound document reads. The caller should bound reassessment rounds (initially two)
  and return `review` when unresolved. No automatic infinite enrichment loop.
- Calendar events, publication read-state updates, legal deadline calculation,
  PDF OCR, MCP transport/auth and a hosted service remain separate work.

## Verification and rollout

Run Python unittest and Node session tests without credentials or network calls.
Exercise preparation against the existing snapshot locally, selecting one known
ID and explicitly including handled data. Reparse the saved PJe HTML to ensure
compound movement labels are retained. Do not call a live account for implementation
tests. Before production use, have the calling agent replay a small reviewed set
and compare complete text, other-party notifications, missing referenced text,
ambiguity and contradictions. Then authorize a separate read-only live acceptance
run with retained sessions. No new production automation is enabled by this patch.

Local evidence check on the saved `1020340-18.2025.4.01.3100` capture: 20 date
buckets, 39 movements, and three compound rows with 4/2/2 labels respectively.
Of its 23 saved document responses, the conservative extractor returns 13 HTML
texts, nine `pdf_extractor_unavailable` records and one `unrecognized_html`.
These are extraction results, not 23 confirmed publication matches or completeness
judgments. No live request was needed for this check.

Final local verification for this increment: 28 Python unittest checks passed;
the Node bearer/session/read-guard test passed. A historical publication was
prepared through the CLI with `--include-handled` and remained
`awaiting_assessment`; this was not a live LLM evaluation or legal classification.

## PDF follow-up proposed by the user

PDF parsing is a real remaining need when a selected context document is one of
the nine PDFs, but is unnecessary for an already sufficient publication or an
HTML act. `pdf-inspector` is a candidate, not an installed dependency in this
increment. Its [Python documentation](https://github.com/firecrawl/pdf-inspector/blob/main/docs/python.md)
describes Windows x64 wheels, in-memory text/Markdown extraction and page-level
OCR indicators. Evaluate it against the nine local PDFs before adopting it.

Proposed route: HTML -> existing extraction; PDF with usable native text -> local
PDF extraction; scanned/mixed/problematic pages -> explicit OCR-needed status or
an authorized local OCR step. Retain document ID, page provenance and extraction
warnings; partial native text is not proof that every page was read. The library's
[OCR runtime](https://github.com/firecrawl/pdf-inspector/blob/main/docs/ocr-runtime.md)
needs additional native libraries/model assets. Do not enable hosted fallback or
upload process documents simply because the parser recommends it.
