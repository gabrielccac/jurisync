# PJe lawyer panel: Expedientes inboxes

Observed in mitmproxy on TRF5 1G (2026-10-09, PJe 2.11.2.0-39) and walked live on TRF1 1G.
Code: `src/jurisync/connectors/pje/inbox.py`. Tools: `pje_list_expediente_counters`,
`pje_list_inbox`.

## Shape

All requests are `POST /pje/Painel/painel_usuario/advogado.seam`, RichFaces `A4J.AJAX.Submit`
with `AJAXREQUEST=<containerId>`, the control (`ctl=ctl`, `ajaxSingle=ctl`), `autoScroll`,
`javax.faces.ViewState` and `AJAX:EVENTS_COUNT=1`. The control, form and container come from the
element's own `onclick`/`rich:onexpand` (`a4j_submit`), never from hard-coded `j_idNNN`, which
change between PJe versions.

| Step | Control | Body extras | Returns |
| --- | --- | --- | --- |
| Bucket | `formAbaExpediente:listaAgrSitExp:{n}:j_id…` (the link in `linhaN1`) | none | the bucket's tree |
| Region | `…:{tree}:{key}::j_id…` (`rich:onexpand`) | every `*NodeExpanded` flag of the tree, the target set to `true` | the region's children |
| Inbox | `….cxExItem` (form = its own node id) | none | page 1 of `formExpedientes:tbExpedientes` |
| Page | `formExpedientes:tbExpedientes:sc…` = page number | the `formExpedientes` filter and header fields | that page (40 rows) |
| Search | `formExpedientes:btPesq`, container from its `onclick` | the filter fields filled in | the inbox filtered, paged as above |

## Buckets are fixed, regions are not

The seven buckets are the same on every login (TRF1 and TRF5 alike). Under a bucket the tree
depends on the profile: TRF5 has one branch per jurisdiction (`PE / Serra Talhada`, ...), each
with a `Caixa de entrada` and, in Serra Talhada, a second inbox `RPV`; TRF1 has a single
`Seção Judiciária do Distrito Federal` with `Caixa de entrada`. The reader expands branches
recursively (up to six levels) until it finds `cxExItem` links, so new boxes need no code.

On TRF5 the bucket "Pendentes" (871) equals "Apenas pendentes de ciência" + "Ciência dada pelo
destinatário" + "Ciência dada pelo Judiciário" + "Sem prazo", and its regions add up to the same
total, so reading bucket 0 covers the new communications.

## Search filters

`itNE` expediente number, `itCL` classe, `itAS` assunto, `itDestPend` recipient, `itIMF`
document, `itOAB`; the CNJ number is split into `numeroProcesso:{numeroSequencial,
numeroDigitoVerificador,Ano,ramoJustica,respectivoTribunal,NumeroOrgaoJustica}`; the two
`j_idNNNInputDate` fields are "Criação do expediente" (expedition date), `dd/mm/yyyy`, found by
position. `soOrd` is the **órgão julgador** filter and `itPR` the priority filter (option ids),
not sorting. The search runs inside the open inbox only.

## Never posted

Each row also has `btnTomarCiência` (starts the deadline), `Responder`, the select checkbox,
reminders, history and the toolbar's `distribuirExpedientes`. `check_control` allows only the
five shapes above and refuses these by name. The row's "ver detalhes" link opens the autos with a
per-process `ca` token; `pje_get_process` reaches the same page by process number, so the token
is not stored (rows keep the internal `processId` only).

## Reading notes

- Notice wording is either `O sistema registrou ciência em …` or `<name> tomou ciência em …`
  (also accepted by `expedientes.NOTICE`). A row with no notice text and a filled
  `btnTomarCiencia` span is pending (`cienciaPendente`).
- The list can grow while its pages are read (a new expediente shifts the pages). Rows are merged
  by `expedienteId`; `changedWhileReading` is true when more rows than the reported total arrived.
- TRF5 Serra Talhada, 626 rows: 16 pages, about 70 s. Filtering by expedition date first is much
  cheaper for a daily check.
- Reading a pending communication's act (`pje_read_expediente_ato`) or its document
  (`pje_read_documents`) may register the party's ciência and start the deadline, so only those
  need permission: they are refused unless the caller passes `acknowledgeCiencia=true` after the
  user's explicit yes. Everything else is read by default. If the Expedientes tab cannot be read
  the check is skipped and `pje_read_documents` returns `cienciaChecked: false`. Whether PJe
  really registers ciência on such a read has not been tested; the gate assumes it does.
- Per customer: `customers/<name>/settings.toml` with `[pje] ciencia_on_read = "allow"` makes the
  two tools read pending communications by default (`"ask"` is the default). An explicit
  `acknowledgeCiencia` in the call always wins. Whenever a pending communication is read, the result
  carries `ciencia.pendenteAoLer` (expedienteId, tipo, parte, prazoDias, limite) and an `aviso`, so
  the skill can tell the user in chat and write it into the task.
