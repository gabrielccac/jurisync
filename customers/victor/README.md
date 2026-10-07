# Victor workflows

This directory holds Victor-specific workflows and policy in the Jurisync
repository. The shared ADVBOX and PJe connectors live under `src/jurisync/connectors/`.

## DJEN to ADVBOX tasks

The customer POP rules live in
`skills/victor-hugo-task-routing/SKILL.md` with its three reference tables.
The intended scheduled agent uses the DJEN MCP to fetch communication pages for
Victor's OABs, applies those rules (reading PJe only when needed), resolves the
ADVBOX lawsuit, checks existing activities, creates the required tasks through
the ADVBOX MCP, and verifies each creation. It records a communication as handled
only after a verified task creation or an explicit no-task decision. The
scheduled agent and its processing ledger are not implemented yet.

The standalone DJEN `--poll` command only discovers new or cancelled
communications. It saves their IDs as seen immediately after a complete fetch;
that state is not proof that the POP decision or ADVBOX task creation happened.
The scheduled task workflow must not use this seen state as its completion marker.

For a JFPE notice with an explicit procedural period in days, calculate the
judicial due date from DJEN availability before creating its ADVBOX task:

```bash
.venv/bin/python -m jurisync.connectors.djen.deadlines --available 2026-10-06 --days 10
```

The same calculator is exposed as `djen_calculate_deadline` in the DJEN MCP.
It returns publication, first counted day, all counted business days and
`advboxDeadline`. Its sourced JFPE calendar currently covers 25 Sep through
19 Dec 2026 and rejects dates outside that window. The agent must check that
the extracted period applies to the represented party; calendar days,
conditional periods and missing periods require separate handling.

## PJe filing preparation

For local Codex desktop use, `skills/victor-pje-filing/SKILL.md` guides the
preparation and PJe MCP stages. The skill is installed through a symlink in
`~/.agents/skills`; it does not submit **Protocolar**.

The existing TRF1 spreadsheet watcher runs from the repository root with
`python -m customers.victor.workflows.pje1g_watch --help`.

`workflows/prepare_pje_filing.py` prepares a TRF5 draft payload from an Advbox
`00-PROTOCOLAR` folder. It downloads files, converts DOC/DOCX files to PDF,
classifies clear filenames, reads decision letters, and asks TypeSafe AI Jev
about ambiguous readable documents. Uncertain supporting files fall back to
`Outros Documentos` and appear in `review.json` under `fallbacks` with the
reason and any proposed type. They are included in `payload.json` once the
case facts and actual upload blockers are resolved. The PJe
adapter under `src/jurisync/connectors/pje/filing/` receives that explicit
payload and never invokes customer policy or Jev. Clear filenames bypass PDF
text extraction; the selected petition is inspected separately to validate
case facts.

Provide `VICTOR_PJE_GRAZIELE_CPF` and, for ambiguous documents,
`OPENROUTER_API_KEY` in the private environment. The OpenRouter Decisions API
calls TypeSafe Jev 1.13. A direct `TYPESAFE_API_KEY` is an alternative. The
request sends the filename and at most 12,000 extracted text characters; it
does not send the original PDF. Missing keys or illegible supporting documents
use the same flagged fallback type.

Case facts are explicit because jurisdiction, class, subject, author, and claim
value can change by matter. A private `case-facts.json` has this shape:

```json
{
  "authorCpf": "<11 digits>",
  "author": {"name": "<PJe registry name>", "address": {"cep": "<8 digits>", "numero": "<number>"}},
  "classification": {
    "jurisdiction": "PE / Petrolina",
    "judicialClass": "PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)"
  },
  "subjects": [{"search": "Assistencial", "code": "11946", "name": "Pessoa com Deficiência"}],
  "characteristics": {"claimValue": "5.000,00"}
}
```

The workflow fills Victor's verified office choices: previdenciário area,
Graziele's name and OAB, INSS, CEAB, Ministério Público, Justiça gratuita,
no injunction, and Juízo 100% Digital. The attorney CPF is private. A petition's
stated claim value and formatted CPFs are extracted as candidates and checked
against the supplied claim value and author CPF.
The Victor workflow always sets the exact PJe priority label
`Juízo 100% Digital`. It ignores `characteristics.priority` in case facts.
Always verify the case facts against source documents before running PJe.
`author.address` is optional. A matching CEP reuses the registered PJe address;
otherwise the CEP lookup must return one suggestion and `numero` is required
to add the address. The adapter rereads the selected address after saving.

```bash
.venv/bin/python -m customers.victor.workflows.prepare_pje_filing prepare \
  --customer 6540591 --facts /private/case-facts.json --out /private/run
```

Read `/private/run/review.json`, especially `fallbacks`: these files will be
uploaded as `Outros Documentos` unless you override their type or skip them.
Create a private `overrides.json` keyed by the file's `id` if needed. An
override can set `{"role":"supporting","type":"Documento Comprobatório"}`
or `{"role":"skip"}`. If the folder has two petitions, select one as `main`
and skip the other. Then finalize using the downloaded files:

```bash
.venv/bin/python -m customers.victor.workflows.prepare_pje_filing finalize \
  --review /private/run/review.json --facts /private/case-facts.json \
  --overrides /private/overrides.json
```

The finalizer checks PDF hashes and validates the selected PJe document types.
Word conversion is available as an importable function and a command:

```bash
python3 -m jurisync.core.word_to_pdf input.docx output.pdf
```

The service image must include `libreoffice-writer` and the fonts used by the
source documents; a Debian/Ubuntu image can install `fonts-liberation` and
`fonts-crosextra-carlito` as common replacements. The converter runs LibreOffice
headlessly with a separate temporary profile for each call, so concurrent
service requests do not share an office session. For DOCX, it keeps
cached DATE/TIME field text (such as the petition's original date) instead of
recalculating it on conversion day. Supporting PDFs over 10,000,000
bytes are split without re-rendering into numbered volumes; the review report
records each part's page range and hash. Confirm the page ranges before
finalization; each volume keeps the original document type. Its volume fraction
appears in both the description and PJe's optional `Número` field.
Signed, encrypted, corrupt, or single-page oversized PDFs stay blocked for
manual handling. An oversized main petition also stays blocked because this
flow saves one main PDF. Files are never omitted for size; `role: "skip"` is an
explicit reviewer choice. Duplicate files and uncertain main petitions still
need explicit resolution.
Use `payload.json` with the shared PJe CLI and a new checkpoint, as described
in `docs/connectors/pje-trf5-filing.md`. This workflow prepares data only; it does
not create or protocol a process.

## Advbox action to PJe subject

PJe uses an **Assunto** lookup for the benefit in the captured draft flow.
Its current choices can be listed with the shared CLI's `subject-options`
stage. The candidate code table verified on 2026-10-05 now lives in
`skills/victor-pje-filing/references/subjects.md`; read it alongside this
historic comparison. The Advbox `Ação` label alone is not a final filing
decision.

`BPC deficiente` code `11946` was already attached to the mock draft and is
recorded in its earlier capture; PJe excludes attached subjects from search
results on that draft. `Auxílio-acidente` returns five selectable subjects
for the type of incapacity. A search for `Restabelecimento` also returns
`6178` under **Pedidos Genéricos Relativos aos Benefícios em Espécie**; the
Advbox label does not say whether the underlying temporary-incapacity benefit
is rural or urban. The petition must guide whether to select the generic
restabelecimento subject, a specific benefit subject, or both. The payload
accepts an ordered `subjects` array; the older singular `subject` remains
accepted. The preparation script requires an explicit reviewed choice in case
facts; it does not automatically apply this candidate table.

### Historic subject comparison and Jev pilot

On 2026-10-05, the saved **Assuntos** were read from PJe for the ten previously
matched filings. These are the subjects actually selected, not a statement that
every selection is the best choice for a new case:

| Advbox `Ação` | PJe process | Saved subject codes |
| --- | --- | --- |
| BPC deficiente | `0005394-06.2026.4.05.8308`, `0009696-30.2025.4.05.8303`, `0004131-48.2026.4.05.8304` | `11946` in each |
| Restabelecimento de incapacidade temporária | `0011038-42.2026.4.05.8303` | `6095`, `6101`, `6109`, `6178` |
| Restabelecimento de incapacidade temporária | `0004131-36.2026.4.05.8308` | `6101`, `6109`, `6178` |
| Pensão por morte rural | `0007999-71.2025.4.05.8303` | `6104` (general Pensão por Morte) |
| Aposentadoria por idade rural | `0012409-41.2026.4.05.8303` | `6098` |
| Incapacidade temporária urbana | `0006822-23.2026.4.05.8308` | `6095`, `6101`, `6109` |
| Incapacidade temporária rural | `0001748-37.2025.4.05.8303` | `6095`, `6101`, `6109` |
| Advbox lawsuit match remains provisional | `0003232-87.2025.4.05.8303` | `6095`, `6101`, `6109` |

The two restabelecimento petitions explicitly request restoration and an
alternative retirement if permanent incapacity is found. They use different
subject sets despite similar requests. The rural pension and rural/urban
incapacity filings use general subjects even though more specific PJe choices
exist. One urban-labelled incapacity petition describes rural incapacity, so
the Advbox action cannot be treated as ground truth.

For the two historical restabelecimento examples, reviewed `subjects` could
carry `6178` Restabelecimento, `6101` Auxílio por Incapacidade Temporária,
`6095` Aposentadoria por Invalidez when requested in the alternative, and any
other code actually approved for the case. The Advbox lawsuit's `Ação` names
the main benefit, while task history and the petition can explain restoration,
rural status, or alternative requests. Neither the lawsuit label nor task
history consistently explains why `6109` was filed, or why two similar cases
have different sets. That code needs an explicit reviewer decision.

Another live comparison, Advbox task `279422924` / lawsuit `17519594`, is
already filed in PJe as `0012250-98.2026.4.05.8303` with `6101` and `6109`.
The Advbox action and triage note identify a rural temporary-incapacity claim.
The petition's requests also seek auxílio-acidente if the injury only reduces
working capacity and invalidity retirement if incapacity is permanent. Those
alternative requests explain why multiple Assuntos may be relevant, but do
not establish a consistent rule for selecting exactly `6101` and `6109`:
`6095` was also an alternative request and was not attached. Review the
petition's pedidos and office policy before filling the ordered subject list.
Its historically filed selection can be represented in case facts as:

```json
"subjects": [
  {"search": "Incapacidade Temporária", "code": "6101", "name": "Auxílio por Incapacidade Temporária"},
  {"search": "Auxílio-acidente", "code": "6109", "name": "Incapacidade Laborativa Permanente"}
]
```

This records the historic selection for comparison; it is not a default for
every temporary-incapacity lawsuit.

A read-only Jev pilot asked separate yes/no questions for candidate codes,
using the Advbox action and redacted petition request paragraphs. It gave
0.93–0.98 to the correct code in each of the five single-subject filings.
In the four multi-subject filings, it identified restoration (`6178`) and
temporary incapacity (`6101`) strongly where explicit, but scored `6109`
only 0.56–0.69 and suggested `6095` in one case where PJe had not selected it.
No simple confidence cutoff reproduced those historical sets. Use Jev to
propose evidence-backed subjects for review; resolve office policy for
multiple subjects, general versus specific codes, and alternative claims
before selecting them automatically. The private read-only comparison is at
`~/.local/share/jurisync/pje-benefit-validation-20261005/`.

## Validation on 2026-10-05

- Ten live Advbox `00-PROTOCOLAR` folders were listed. Their filenames covered
  the known rules and the expected review cases (`INFBEN`, `DESPACHO`, prior
  process numbers, medical bundles, and decision letters). One folder uses a
  trailing space in its folder name; lookup handles it.
- A full read-only preparation of an eight-file folder classified the decision
  as Carta de Indeferimento and routed the other seven PDFs by filename. It
  withheld the PJe payload because case facts and the private attorney CPF
  were not supplied.
- An eleven-file folder converted a DOCX petition to PDF and flagged a cessation
  letter, an unfamiliar despacho, and a medical PDF without readable text.
- Of eight previously captured real PJe decision PDFs, content extraction
  routed four denials and one grant. Two cessation/non-extension letters and
  one unreadable decision remained for review. Live OpenRouter Jev calls
  classified one readable individual medical report as Laudo Médico at 0.94
  confidence. An unfamiliar despacho scored 0.63, and the unreadable decision
  produced no usable type. Both now use the flagged fallback if submitted;
  medical bundles do too because extraction may cover only part of them.

### Comparison with historic PJe submissions

The ten PJe process numbers saved in `docs/connectors/pje-trf5-filing.md` were
searched in Advbox. Nine resolved to exact lawsuit records and customer folders.
The tenth (`0003232-87.2025.4.05.8303`) was absent from Advbox lawsuit search,
but the folder for customer `766433` contains that process-number PDF and the
same initial document set, so it is a provisional match. The downloaded files
and review reports are private under
`~/.local/share/jurisync/pje-historic-validation-20261005/`.

| PJe process | Advbox customer | What the comparison showed |
| --- | ---: | --- |
| `0005394-06.2026.4.05.8308` | `10472352` | Uses `00-PROTOCOLAR JUD`; typo `PETÇÃO` is now recognized. Visible PJe rows agree on routine evidence types. PA and medical PDF exceed the current 10 MB upload limit. The PJe first petition and decision are beyond the visible 16-row page. |
| `0009696-30.2025.4.05.8303` | `7409301` | Folder has 14 files. The initial PJe submission is beyond the visible 16 of 32 rows, so its types cannot be checked from this capture. Two PDFs exceed 10 MB; medical text is unreadable. |
| `0011038-42.2026.4.05.8303` | `514261` | Routine types match. The decision is a non-extension, so it stays in review; historically it was Documento Comprobatório. The medical PDF is unreadable. Quesitos is now routed to Outros Documentos per the current office rule, while this filing used Documento Comprobatório. |
| `0004131-48.2026.4.05.8304` | `11623406` | Ten of 12 folder files match saved PJe types. An explicit denial now routes to Carta de Indeferimento though it was filed as Documento Comprobatório; the medical PDF is unreadable. |
| `0004131-36.2026.4.05.8308` | `14705660` | Grant and routine types match. The cessation letter stays in review although historically filed as Carta de Indeferimento. The administrative process needs splitting into volumes; two identical cessation files appear in Advbox. |
| `0007999-71.2025.4.05.8303` | `9739179` | The unlabelled `PENSÃO POR MORTE.docx` is proposed as the main petition but held for confirmation. Death/dependency evidence and representative ID now route as Documento Comprobatório, matching the filing. The oversized administrative process was historically filed as Documento Comprobatório. |
| `0012409-41.2026.4.05.8303` | `18107570` | Routine types match. The captured decision text is insufficient for automatic classification, despite historical Carta de Indeferimento. Contract plus procuração is now Outros Documentos per office policy; this filing used Instrumento de Procuração. Both PDF and DOCX petition versions need selection. |
| `0006822-23.2026.4.05.8308` | `9800697` | Routine types match. The explicit denial now routes to Carta de Indeferimento; the filing used Documento Comprobatório. The medical PDF is unreadable, and historic procuração used Instrumento de Procuração. |
| `0001748-37.2025.4.05.8303` | `4020488` | Routine types match. The explicit denial now routes to Carta de Indeferimento rather than historical Documento Comprobatório. The medical bundle is unreadable; the administrative medical report stays in review because this filing used Documento Comprobatório despite Jev proposing Laudo Médico. |
| `0003232-87.2025.4.05.8303` | `766433` (provisional) | The folder's initial files match PJe descriptions. Routine types match. The explicit denial now routes to Carta de Indeferimento rather than historical Documento Comprobatório; the medical PDF is unreadable. |

The comparison above records the original classification analysis. Under the
current workflow, uncertain supporting files use flagged `Outros Documentos`.
The historical comparison predates automatic splitting. The current
preparation splits eligible unsigned supporting PDFs into reviewed volumes;
it still does not infer unreadable pages.
