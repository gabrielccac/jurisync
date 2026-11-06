# Performance of remote tool calls

Notes from the first hosted test (2026-10-30, Victor, server in Nuremberg), plus measured connector changes.
The options below remain proposals unless explicitly recorded as implemented.

## Implemented: shared Eproc password login (2026-11-04)

The login used to wait for each alternate field separately. On TRF6's legacy form, absent SSO selectors cost
20 s for `#username`, 20 s for `#password`, and 10 s for `#otp`. The shared `_first_element` helper now polls
all alternatives within one timeout, retaining visibility checks, alert handling, and stale-field retries.

One fresh Wesley/TRF6 1G login before and after the change took **66.676 s → 15.244 s** (77% less time).
Both runs confirmed the authenticated panel and session cookie; no process was opened. Other portals have
not been benchmarked live for this change.

This code is shared by password logins for all 20 registered Eproc portals, through the CLI, local/HTTP MCP,
and hosted access checks. Eproc certificate login and the HTTP-based PJe, ADVBOX, and Juridiq logins do not
use this selector helper. Long-running Eproc MCP servers already reuse sessions per tenant and portal.
Running processes must restart to load the change; separately installed packages and hosted deployments must
also receive the updated code. This repository change alone does not update those installations.

## Measured

Server log of `pje_list_expediente_counters(trf5-1g, regions=true)` followed by `pje_list_inbox(trf5-1g,
expedidoDe=expedidoAte=2026-10-30)`:

- Certificate login (A1 + TOTP): about 2 s. Not the bottleneck.
- 79 POSTs to the PJe panel (`advogado.seam`) for both calls, sequential, about 0.7 s each from Germany to TRF5.
- Counters: open the panel, open each bucket, expand each of the 13 jurisdictions, one request each.
- Inbox list: open the bucket, expand the 13 jurisdictions again, then for each of the 14 inboxes open it and apply
  the date filter (2 requests per inbox), including inboxes the counters already showed empty (`RPV`, 0).
- Panel requests cannot run in parallel on one session: JSF view state belongs to the session
  (`core.session_pool` serializes them on purpose).
- Server load stayed near zero (load 0.03): the time is spent waiting on the portal and the network.

## Test round: every PJe tool (2026-10-30, Victor, TRF5 1G, hosted server)

"Total" is measured on the caller's side and includes the client, the MCP transport and the model's own turn; a call
with a single portal request still took 12 s, so about 10 to 13 s of every total happens outside the server.
"Portal" is the span from the first to the last request the server made to the PJe. Every call reused the pooled
session: no login was needed in this round. Mean gap between consecutive PJe requests: 1.29 s (139 samples).

| # | Tool and arguments | Total | Portal | PJe requests | Notes |
|---|---|---|---|---|---|
| 1 | `pje_list_expediente_counters` | 12.4 s | under 1 s | 1 | One panel page |
| 2 | `pje_list_expediente_counters` regions=true | 103.5 s | 74.4 s | 62 | Expands 13 jurisdictions in each of the 6 non-empty buckets |
| 3 | `pje_list_inbox` regions=["Serra Talhada"], today | 22.7 s | 8.4 s | 7 | Filtered: 2 inboxes |
| 12 | `pje_list_inbox` all jurisdictions, today | 59.8 s | 43.3 s | 43 | 14 inboxes, 13 of them empty for the date |
| 4 | `pje_get_process` scope=latest | 21.9 s | 9.1 s | 6 | |
| 5 | `pje_get_process` scope=page | 23.4 s | 10.2 s | 7 | 28 movements, 48 documents, 10 expedientes |
| 6 | `pje_read_documents` 1 document | 26.4 s | 11.2 s | 7 | Returned only the HTML cover ("Laudo Pericial"); the content is in the attachments |
| 7 | `pje_read_documents` 2 PDFs (2.4 MB + 0.56 MB) | 38.2 s | 11.0 s | 7 | Text extracted; the extra time is download and PDF extraction |
| 8 | `pje_read_expediente_ato` closed expediente | 27.0 s | 12.5 s | 8 | |
| 9 | `pje_list_watched_processes` | 55.5 s | not logged | not logged | 9,053 watched processes, 1.36 million characters: too large for the model to read |
| 10 | `pje_search_pending_drafts` by name | 19.4 s | not logged | not logged | |
| 11 | `pje_draft_status`, `pje_draft_inspect` | 19.5 s | none | 0 | No filing run on the server: errors, as expected |

### Findings from this round

- Reading one process or document costs 6 to 8 portal requests, about 10 s, even when the caller already knows the
  process: the process search, the process page and the Expedientes tab are fetched again on every call.
- `pje_read_documents` and `pje_read_expediente_ato` read the Expedientes tab to check pending ciência before reading;
  that check is needed, but its result can be reused for the same process within a call sequence.
- The counters with regions are the most expensive call (62 requests): each bucket's tree is expanded even when the
  caller wants one bucket.
- `pje_list_watched_processes` returns everything (9,053 rows). It needs pagination or a filter (process number, date).
- `pje_draft_inspect` returned a server file path in its error message: diagnostics must strip local paths.
- `pje_draft_status` and `pje_draft_inspect` only work with a filing run on the same machine; filing is not hosted, so
  they should be removed from the hosted server like the draft writes.
- The Push, pending-draft and filing requests do not appear in the server log: their HTTP calls bypass the request
  logging, so their timing cannot be measured yet.

## Options by layer

Two products will use this: the MCP (tools for Claude) and a separate HTTP API. Most of the cost is in the connector
layer, which both share; the MCP layer decides how much data reaches the model and how long it waits in silence.

### Connector layer (shared by the MCP and the API)

| Change | Expected effect |
|---|---|
| Cache the process id and the process page per office and process for a few minutes | `pje_get_process`, `pje_read_documents` and `pje_read_expediente_ato` on the same process skip the search and the page: about half of their 6 to 8 requests |
| Reuse the Expedientes tab read within that window for the ciência check | One request less per document or act read |
| Counters: expand regions only for the requested bucket | 62 requests become about 14 for one bucket |
| Inbox: skip inboxes whose count is zero in the bucket | Fewer requests when no date filter is used |
| Push registry: page and filter it | From 1.36 million characters to a page the caller asks for |
| Log every portal request (Push and filing included) with its duration | Complete timing data |
| Strip local paths from error diagnostics | No server details in errors |

### MCP layer

| Change | Expected effect |
|---|---|
| Progress notifications from long tools ("inbox 5 of 14") | The caller sees progress instead of a silent wait |
| Output budgets and compact mode on large results (process timeline, document inventory, Push) | Answers fit in the model's context; less to read |
| Remove tools that cannot work remotely (`pje_draft_status`, `pje_draft_inspect`) | Fewer useless tools for the model to consider |
| Tool descriptions that steer to the cheap call (filter by region, `scope=latest`) | The model picks the fast path by default |

### HTTP API layer

| Change | Expected effect |
|---|---|
| Asynchronous jobs for fan-out reads (start, then poll or receive a webhook) | No request held open for a minute |
| Pagination and field selection on every list | Clients fetch only what they show |
| `ETag` / "as of" time on cached answers, `refresh=true` to force a live read | Clients know how fresh the data is |
| Per-office rate limits that match the portals' tolerance | Fewer blocks from the courts |

### Earlier options (first round)

#### Code (small, low risk)

| Change | Where | Expected effect |
|---|---|---|
| Skip inboxes whose count is zero when no filter depends on them | `pje.inbox.read_inbox` | 2 requests saved per empty inbox |
| Reuse the expanded jurisdiction tree for a few minutes on the pooled session | PJe panel | Up to 13 requests saved on the next panel call |
| Short per-office cache (2 to 5 min) for repeated reads: counters, ADVBOX members and workflows, court lists | all connectors | Repeated reads in one conversation return at once |
| Run saved DJEN searches in parallel (stateless public API) | `djen_search_saved` | N searches in the time of one |
| Progress notifications from long tools ("inbox 5 of 14") | PJe, ADVBOX | No speed-up, but the caller sees progress instead of a silent wait |
| Timing per tool call and per portal request in the server log | all | Data to choose what to optimize |
| Leaner results (field selection, compact mode) | PJe, ADVBOX | Less text for the model to read; faster and cheaper answers |

#### Infrastructure

| Change | Expected effect |
|---|---|
| Server in Brazil | Part of the 0.7 to 1.3 s per request is the Germany-Brazil round trip; also removes the need for the DJEN bridge |
| Warm session before scheduled routines (log in a few minutes ahead and keep the session alive) | The routine's first call skips the login and a possible wait for the next TOTP window (up to 30 s) |
| DJEN bridge with one minimum instance on Cloud Run | Removes the 1 to 2 s cold start, for a small fixed cost |

#### Architecture (largest effect, for many offices)

Background sync per office: a server job reads the PJe panel, DJEN and ADVBOX on a schedule (hourly, and just
before each routine), incrementally by expediente / communication id, into a local store. Tools answer from the store
at once with an "as of" time; a live read happens only when asked (`refresh=true`).

- Gains: sub-second answers, less load on the portals (lower risk of being blocked), and the base for new-expediente
  alerts (already in the backlog under "PJe Expedientes and Push").
- Costs: one more moving part, and data a few minutes old, acceptable for daily routines.

## Suggested order

1. Complete request logging with durations (Push and filing included) and strip paths from diagnostics.
2. Process cache (id, page, Expedientes tab) and per-bucket counters: the largest measured savings.
3. Push pagination and output budgets, so every tool's answer fits the model's context.
4. Progress notifications in the MCP; asynchronous jobs and pagination in the HTTP API.
5. Skip empty inboxes, parallel DJEN searches.
6. Then a Brazilian server and, with more offices, the background sync.
