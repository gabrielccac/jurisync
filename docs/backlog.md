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

The MCP currently exposes only Expedientes counters and the read-only Push
watched-process registry. JSF partial-response parsing advances ViewState and
form fragments for the internal/CLI Expedientes expansion path, while unknown
controls fail closed. Do not expose expanded notice rows or replay Push pages
until an authenticated capture proves the actions are side-effect free and the
portal's page contract is stable.
