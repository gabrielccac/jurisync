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
