# Sync layer and cleanup plan (draft, 2026-11-09)

Goal: clients (Claude through MCP, the HTTP API, scheduled routines) stop hitting the court portals on every
question. Our own backend syncs each office on a schedule, stores the result, answers from the store with an
"as of" time, and goes to the portal live only when a read needs it or the caller asks for `refresh`.
All data comes from our own connectors (no DataJud, MNI or third-party providers). The code-quality fixes of
`docs/review-2026-11.md` are folded into the same phases. e-SAJ stays as it is.

## Layers

```
clients        MCP servers · HTTP API · routines/webhooks
read layer     answers from the store (asOf, freshness), refresh=true → live call through the service
sync engine    jobs per office: schedule + triggers, one queue per (office, portal) session, budgets
store          per-office data: communications, expedientes, processes, movements, tasks, sync runs, changes
services       typed operations per connector (no MCP imports) — also used live by the read layer
connectors     portal mechanics (login, JSF/REST/HTML, parsers) — what exists today
```

## What is synced, and what stays live

| Data | Source | How | Default frequency |
|---|---|---|---|
| Communications | DJEN saved searches | Incremental by date window, dedupe by id | Hourly |
| Expedientes (inbox) | PJe, PJe-KZ, Eproc, e-SAJ panels | Counters first (1 request); open only buckets whose counts changed; incremental by expediente id | 3×/day + before each routine |
| Process (metadata, parties, timeline, expediente tab) | Portal of the process | Only the office's **active** processes (those with a communication, expediente or open deadline in the last months), never the whole history; triggered by a new communication/expediente, daily while a deadline is open; `scope=latest` | On trigger |
| Push / watched processes | PJe | Full list, diffed | Daily |

Stays live, never automatic:
- Opening an act or a document of an expediente without ciência (would register ciência). Document text is
  cached only after ciência exists or for documents not linked to an expediente.
- Every write (ADVBOX/Juridiq tasks, filing).
- `refresh=true` on any read.

ADVBOX and Juridiq are not synced: offices use them mostly to create and update tasks, not for recurring reads.
They stay live, with a short per-office cache only for reference data (members, workflows, task types).

## Store

Entities: office, portal account, communication, expediente, process, movement, document reference, task,
sync run (start, end, requests, result, error), change event (what is new since the last read).

Decision needed: **SQLite per office** (no new infrastructure, isolation by file, encrypted with the office key,
fits the single server today) or **Postgres** (one database, needed when we run several servers). Recommendation:
SQLite per office now, behind a repository interface, Postgres when we scale out.

## Sync engine rules

- One worker queue per (office, portal): the same session the live reads use; JSF view state stays serialized.
- Budgets per court (requests/min) and a global cap on browser logins; cool-down after a rejected login.
- Jobs are idempotent and resumable; every run is recorded (duration, requests, errors) — this is also the
  "última sincronização" of the per-office dashboard drafted in Doop.
- Change events feed notifications ("new intimação", "deadline in N days") and the routines.

## Reads (MCP and API)

- Default answer from the store with `asOf` and `stale` flags; `refresh=true` for a live call that also updates
  the store.
- MCP tools become thin: list inbox, get process, list communications, search tasks read the store; read
  documents/act and writes stay live through the services.

## Phases

| Phase | Content | Done when |
|---|---|---|
| 0 | Safety fixes of the review (ciência gate, login cool-down, shared `.env`, ADVBOX dedupe/cleanup, DJEN 2027 calendar) and quick performance wins | Tests + live checks pass |
| 1 | Foundations: typed errors, settings vs credentials, connector registry, `service.py` for DJEN and PJe reads | MCP unchanged for users, services callable without MCP |
| 2 | Store + sync engine + DJEN sync; DJEN MCP tools read the store | Victor's daily communications served from the store with asOf |
| 3 | PJe/PJe-KZ inbox and process sync with triggers; Eproc next | Inbox and process reads in < 1 s from the store; portal requests per day measured |
| 4 | Change events, notifications, the per-office connections dashboard | Dashboard shows last sync/test per connection |
| 5 | HTTP API on the same services and store | Same answer through MCP and API |

## Reference check: juscraper

`jtrecenti/juscraper` covers public consultation (jurisprudence `cjsg/cjpg`, public process lookup
`cpopg/cposg`) for many courts, without lawyer login. The exception is its JusBR scraper, which uses a JWT from
the PDPJ SSO session (`sso.cloud.pje.jus.br`, the same SSO our PJe login uses) to query processes and download
documents nationally. Worth comparing: their per-court coverage list and the download/parse split (raw HTML
kept, parsed later), which fits the sync store. It does not cover logged-in inboxes, expedientes or ciência.
