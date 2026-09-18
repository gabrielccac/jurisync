# Pipeline boundaries and next steps

## Sources and context

Juridiq publications and PJe movements are different inputs.
A registered Juridiq process may have publications available through /publication.
PJe process history supplies movement entries and associated documents.
PJe Push lists watched processes; Expedientes is another portal view and must not
be silently treated as the same publication feed.

Supported design:
1. Poll Juridiq publications for registered processes, or poll an explicit set of
   PJe processes when direct monitoring is needed.
2. Preserve the raw source object, source ID, process number, portal/instance,
   date and original text. Do not fabricate a publication ID for a PJe movement.
3. The future classifier evaluates whether the act requires a task and whether
   evidence is sufficient. Attachment presence alone cannot decide either.
4. If context is missing, match the process and act to PJe movement/document
   metadata, retrieve the appropriate document and extract its text. Other
   movements may be needed to determine whether the action was already fulfilled.
5. Produce a task draft with source evidence and deduplication identifiers.
6. Apply authorized Juridiq task/event changes after validation.

The end-to-end runner now connects the complete Juridiq workflow through the calling
LLM agent: `integrations/juridiq/pipeline.mjs` polls unread publications, requires a
complete deduplication result, requests assessment and PJe enrichment callbacks,
resolves the selected client's live task templates, checks existing tasks, creates or
updates tasks, creates confirmed events, marks successful publications read, and
persists processing state. The account is selected by the caller. The default client
policy is in `integrations/juridiq/profiles.mjs`; tenant-specific mappings are
data passed to the same runner. See [the implementation plan](publication-enrichment-plan.md).
The current movement candidates keep needsContext as a legacy attachment hint,
with hasDocuments and contextAssessment="not_analyzed" to make its limits clear.
needsContext=false does not establish completeness or mean no task is needed.

## Skill reference

`.claude/skills/juridiq-publication-triage` contains the copyable skill package.
It provides procedural classification/task-template instructions, including a
provisional task flow followed by PJe enrichment and an update to that same task.
It is historical reference, not automatically executed project policy. The v2
orchestration contract is in `references/orchestration-contract.md` inside that package.
Its default workflow starts with unread Juridiq publications and stops when none
exist; a direct PJe watcher needs an explicit movement-input workflow.

The skill's calendar operation is create_event in Juridiq, not an external
calendar integration. Read-only event listing and event detail are now available
for direct duplicate checks before any test-account creation.
Its browser/direct-parser guidance is being adapted to the request-based portal
integrations. Its account-specific template IDs must not be reused across accounts.

## Polling status

TRF1 1G/2G process search and document retrieval: imported behind a common client.
1G watcher: imported; one-shot by default, cookie reuse and local baseline state.
It first saves only a latest-movement marker. On the first change it can emit
historical entries with baseline_incomplete=true. Do not automatically create
tasks from that output. It also hashes document URLs, which may change between
sessions; stable movement deduplication needs validation before enabling writes.
2G dedicated watcher: not yet implemented; 2G direct process retrieval is available.
No recurring jobs were started.

## Order of remaining work

1. Capture real Juridiq publications in read-only mode and save exact response
   schemas/pagination evidence under data.
2. Match several publications to 1G/2G movements and documents; record examples
   of complete text, missing text, ambiguous matches, and unavailable context.
3. Evaluate the agent-orchestrated enrichment using reviewed examples. Local
   preparation/validation and selective joined-document HTML extraction are now
   available; PDF extraction/OCR and production semantic evaluation remain pending.
4. Recover missing endpoint evidence and implement only the Juridiq operations
   required by those drafts. The current read-only additions cover lawsuit
   movements, publication detail, events, task history and task comments.
   Test writes on the test account and retrieve results.
5. Add polling orchestration and stable deduplication once both input paths work.

Keep Python for the existing PJe protocol implementation and Node for the existing
Juridiq auth/client. JSON is the current boundary. No service, queue, database,
browser, LLM SDK, or scheduler is required just to consolidate this code.

## Publication mapping investigation

This is the concrete read-only investigation to run before implementing the
publication classifier. It answers what a Juridiq publication represents, where
its missing context lives, and how an existing task is linked to it.

1. Fetch Juridiq `/publication` pages with `isHandled=false`, preserving each
   raw object, publication ID, process number, `lawSuitId`, source/diary, dates,
   text/teor, read state, and any document references. Do not mark anything read.
2. For every publication with a process number, query Juridiq `/lawSuit` and
   require an exact process-number match. Save the selected lawsuit metadata and
   its movement history from `get_lawSuit_movements_by_lawSuitId` when available.
3. Search that process directly in both PJe instances when the publication or
   lawsuit identifies the instance. Compare dates, movement text, document IDs,
   document titles, and the latest movement. Keep unmatched and ambiguous cases;
   they show where synchronization or diary timing differs.
4. For publications with short/missing teor or attached documents, retrieve the
   referenced PJe document and extract its text. Record the evidence chain:
   publication → process/lawSuit → PJe movement → document → extracted text.
   This is the actual source of `needsContext`; an attachment flag only schedules
   this lookup and cannot by itself decide whether a task is required.
5. List Juridiq tasks using the tested task list endpoints and retrieve details for
   each candidate. Compare task process number, lawsuit, title, description,
   dates, tags, and creation/update time against publication IDs, process numbers,
   and movement dates. Record exact matches, likely matches, tasks with no matching
   publication, and publications with no task.
6. Build a small reviewed fixture with at least: a complete publication that maps
   directly to a task, a publication requiring a PJe document, a publication that
   produces no task, a provisional task later updated, and an ambiguous/unmatched
   record. Use this fixture for classifier and deduplication tests.

The investigation output should be JSON snapshots and a mapping table, for example:

| publicationId | processNumber | lawSuitId | PJe portal | movement/document | taskId | context status |
| --- | --- | --- | --- | --- | --- | --- |
| Juridiq ID | exact number | exact/none | 1G/2G/unknown | IDs and extracted text | exact/none | complete/needed/unavailable |

Only after these comparisons should the pipeline decide whether to poll Juridiq,
poll PJe directly, or use Juridiq for discovery and PJe for enrichment. The first
run must remain read-only and should save raw responses without changing read
states or tasks.
