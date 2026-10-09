# Backlog

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
