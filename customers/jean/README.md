# Jean

Juridiq publication triage for Jean's office (BPC/previdenciário).

| Path | Purpose |
|---|---|
| `skills/juridiq-publication-triage/` | Classification rules and PJe enrichment guidance (linked into `.claude/skills/`) |
| `profile.py` | Semantic task type → live Juridiq template title mapping |
| `run_publications.py` | `run_jean_publications(agent)` — runs the shared publication pipeline |
| `docs/` | Original pipeline/endpoint notes kept from the former `jurisync-jean` project |
| `.env.example` | Credentials this customer needs (Juridiq, PJe TRF1) |

The agent passed to `run_jean_publications` implements `deduplicate`, `assess`
and `route` (plus `enrich`/`provisional_route` when used); see
`src/jurisync/workflows/publications/pipeline.py`. Workflow state is written to
`data/customers/jean/`, keyed by the Juridiq account, and is git-ignored.

## Protocolo de iniciais BPC no PJe TRF1

Perfil de filing `jean` (`filing-profile.json`) e skill `skills/office-pje-trf1-filing`. Os documentos vêm da pasta do
cliente no Google Drive pelo exportador `jurisync-gdrive` (`drive_export_filing_folder` → `pje_filing_prepare` → ... → `pje_draft_create`, tudo por MCP, também no Claude Chat); a
configuração da conta de serviço (variável `GOOGLE_SERVICE_ACCOUNT_FILE`, padrão
`~/.config/jurisync/google-service-account.json`) está em `docs/connectors/gdrive.md`.
