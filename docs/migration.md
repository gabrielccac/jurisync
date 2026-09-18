# Consolidation record — 2026-09-08

Original projects remain intact. Source files were gathered with credentials
removed; raw customer exports, spreadsheets, logs, cookie stores and downloaded
documents were not copied into source.

| Original source | Consolidated location |
| --- | --- |
| Pipeline Juridiq/juridiq_session.mjs | integrations/juridiq/session.mjs; root re-export retained |
| Pipeline Juridiq/pje_adapter.py | integrations/pje/client.py; root compatibility entry retained |
| ChatGPT/General/pje2g_login_test.py | integrations/pje/auth.py; embedded credentials and redundant CLI removed |
| ChatGPT/General/pje1g_pipeline_test.py | integrations/pje/trf1_1g.py |
| ChatGPT/General/pje2g_process_test.py | integrations/pje/trf1_2g.py; includes shared parsing/download functions |
| ChatGPT/General/pje2g_pipeline_test.py | Common client covers CLI; historical snapshot not retained |
| ChatGPT/General/pje1g_watch_poll.py | integrations/pje/watch_1g.py |
| ChatGPT/General/pje_notifications_test.py | integrations/pje/notifications.py |
| ChatGPT/General/benchmark_pje1g_sheet.py | Historical snapshot not retained |
| Desktop/Juridiq/*.js and extract_sheet_processes.py | Historical snapshots not retained; tokens removed |
| Desktop/Juridiq/automacao-juridiq-resumo.md | Historical snapshot not retained |
| Downloads/triagem-publicacoes-juridiq.skill | .claude/skills/juridiq-publication-triage/ (skill and references) |

Absolute source roots:
- C:/Users/Gabriel/Documents/Pipeline Juridiq
- C:/Users/Gabriel/Documents/ChatGPT/General
- C:/Users/Gabriel/Desktop/Juridiq
- C:/Users/Gabriel/Downloads/triagem-publicacoes-juridiq.skill

Historical code snapshots are not part of the active pipeline. The old
spreadsheet rules were deterministic alert rules, not the pending publication
LLM classifier.

Small behavior changes at the integration boundary:
- Juridiq default cache is account-specific when email is configured.
- Shared Juridiq helper blocks business writes unless explicitly enabled.
- Requests reject a different API origin and redirects.
- PJe credentials are environment-based, separate for 1G and 2G.
- Common PJe retrieval permits session reuse; no external-project sys.path import.
- PJe output identifies movements and labels context assessment as not analyzed.
- Document retrieval requires explicit fetch_documents, including when a download
  directory is supplied. CLI output uses movement_candidates.

Earlier create/list probes were largely ad hoc commands. docs/endpoints.md
distinguishes source-backed mappings from conversation-reported observations.
The new offline tests cover the consolidation; they do not recreate those
missing live-test artifacts or certify the entire API.

## Consolidation validation

- Created a project-local .venv and installed requirements.txt successfully.
- Five Python unittest checks passed for routing, validation, credentials and parsing.
- One Node test passed covering session reuse, refresh, read guard and account cache separation.
- Python compilation, CLI help, Node syntax and root compatibility imports passed.
- No embedded JWT was found in the consolidated source/reference files.
- No live Juridiq/PJe queries, account writes, or polling jobs ran during this work.
