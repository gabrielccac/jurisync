# TRF5 PJe: draft filing flow observed in mitmproxy

Scope: one browser exploration on 2026-10-05 using mock case data. The focused,
private capture is `pje-protocol-exploration-20261005-112726.mitm` in
`~/.local/share/jurisync/captures/` (231 PJe flows). Flow numbers below refer to
that snapshot. No final **Protocolar** action was sent.

## Request sequence

| Stage | Observed requests | State created or changed |
| --- | --- | --- |
| Open a draft | `GET /pje/Processo/cadastrar.seam`; Ajax `POST` to the same path for `areaDireitoCombo`, `jurisdicaoCombo`, and `classeJudicialCombo`; then `processoTrfForm:incluiProcessoButton` (flows 82–86). | The response to flow 86 names `/pje/Processo/update.seam?idProcesso=...`; the browser opens it in flow 87. This is the draft ID, before protocol submission. |
| Add an assunto | `POST /pje/Processo/update.seam` with `r_processoAssuntoListSearchForm` search fields, then a selected result from `r_processoAssuntoListList` (93–94). | The selected subject is rendered in the draft. |
| Add parties | `tabPartes` loads the party panel (96). CPF lookup and `btnConfirmarCadastro` precede `formInserirParteProcesso:btnInserirParteProcesso` with party and address fields (102–109). Attorney lookup by CPF/OAB and confirmation precede `formInserirRepresentanteProcesso:btnInserirRepresentante` (112–125). Public bodies are looked up and inserted for the passive pole and other participants (129–165). | Registration/lookup and association with the draft are separate actions. The capture also shows a representation-type selection before association. |
| Inspect characteristics | `processoPrioridadeProcesso` opens the panel (167); priority selector Ajax events follow (168–169). | The panel exposes free legal aid, injunction, claim value, secrecy, and priority fields. The capture contains no POST for `salvaCaracteristicaProcessoButton`, `formAddPrioridadeProcesso:save`, or `frmSegredoSig:grvSegredo`, so persistence of those fields was not observed. |
| Add the main document | `novoAnexo` opens the document panel (170). RichFaces uploads a PDF with multipart `POST /pje/Processo/update.seam` carrying `uploadDocumentoPrincipalDecoration:uploadDocumentoPrincipal:file` and `javax.faces.ViewState` (176, 186). | The first temporary PDF was removed (`btnExcluirPdf`, 183) and another was uploaded. The panel has document type, description, optional number, confidentiality, and PDF/text-editor controls. |
| Add supporting documents | `acaoAjaxAdicionarProcessoDocumento` sends `jsonProcessoDocumento`, whose nested `array` contains file `nome`, `tamanho`, and `mime` (191, 199). Each file is then sent as multipart `file[]` to `POST /pje/seam/resource/upload?cid=...` (196, 204). The JSON responses contain `requisicao: true` and `arquivos[].upload: true`. Per-row `tipoDoc`, `descDoc`, `numeroDoc`, and `ordem` are submitted with `commandLinkGravar` (198, 206). | Two supporting PDFs completed upload. The later information panel displays the replacement main PDF plus both supporting PDFs (208). |
| Review and find the draft | `informativo` loads the case/document summary (208). `GET /pje/Processo/ConsultaProcessoNaoProtocolado/listView.seam` and its search POST follow (228–229). | The draft ID from flow 86 appears in the unfiled-process search response. |

## Fields and dependencies for a draft builder

- **Classification:** submit area of law, jurisdiction, then judicial class in that order; each selection refreshes the available choices before `incluiProcessoButton` creates the draft.
- **Subject:** search by the `r_processoAssuntoListSearchForm` fields, then select a row from `r_processoAssuntoListList`. A search alone does not add the subject.
- **Parties:** choose a pole or participant role, look up the person or public body, confirm or select the result, fill any requested personal/address details, then use the relevant insert action. Representatives additionally require a representation type and a separate association with the party. The capture includes an active individual, attorney and representative, a passive public body, and other public-body participants; other person types have not been explored.
- **Characteristics:** the UI offers free legal aid, injunction, claim value, secrecy, and priority. Only priority selector changes were captured. Treat these as unverified until their save requests and review output are observed.
- **Main document:** `formularioUpload` carries `cbTDDecoration:cbTD` (document type), `ipDescDecoration:ipDesc` (description), `ipNroDecoration:ipNro` (optional number), and the PDF upload control. The observed main PDF is sent through the RichFaces multipart request to `update.seam`.
- **Supporting documents:** register each file through `acaoAjaxAdicionarProcessoDocumento`/`jsonProcessoDocumento`, upload bytes to `/pje/seam/resource/upload?cid=...`, set its row's `tipoDoc`, `descDoc`, `numeroDoc`, and `ordem`, then invoke that row's `commandLinkGravar`. The row prefix (`j_id1073` in this capture) is generated by the page. Upload success alone is not the row-save action.

## Values selected in this exploration

These are observations from one mock filing, not universal defaults. Personal
identifiers, names, dates, descriptions, and file paths are omitted.

| Field | Observed selection or submitted value |
| --- | --- |
| Portal | TRF5 1G/JEF. |
| Area / jurisdiction / class | `DIREITO PREVIDENCIÁRIO` / `PE / Petrolina` / `PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)`. The form option values were `229`, `30`, and `94`, respectively; match labels/codes within the current select rather than treating those values as global identifiers. |
| Subject | Searched for `Assistencial`, then selected `Pessoa com Deficiência (11946)` under `DIREITO ASSISTENCIAL (12734) → Benefício Assistencial (Art. 203,V CF/88) (6114)`. |
| Active pole | An individual (`tipoPessoa=F`) found by CPF. The submitted detail form contained name, mother's name, birth date, and sex `F`; social name, father's name, ethnicity, marital status, education, nationality, and profession were blank/unselected. Address fields in the final insert request were blank. Party secrecy and homeless flags were `false`. |
| Active-pole representation | One `ADVOGADO` (looked up by CPF/OAB) and one `REPRESENTANTE` (looked up by CPF), selected through separate representation and association actions. |
| Passive pole | Public body `INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS`, CNPJ `29.979.036/0001-40`, selected from two `inss` lookup results. |
| Other participants | `CEAB-DJ INSS`, CNPJ `29.979.036/0014-65`, as `ÓRGÃO DE CUMPRIMENTO`; `MINISTERIO PUBLICO DA UNIAO`, CNPJ `26.989.715/0054-14`, as `FISCAL DA LEI`. The latter lookup returned multiple bodies with the same name, so CNPJ is needed to identify the selected row. |
| Characteristics | Review displayed claim value `0,00`, secrecy `NÃO`, free legal aid `NÃO`, and injunction `NÃO`. Priority selector changed from `Pessoa com deficiência` to `Juízo 100% Digital`, but no priority save request was captured. |
| Main document | PDF mode, type `Petição inicial`; description present, optional number blank. A first PDF was removed and a replacement main PDF appeared in review. |
| Supporting documents | Two PDFs, types `Carta de Indeferimento` and `Documento Comprobatório`, with descriptions, blank optional numbers, and order `1` and `2`. Each had a successful upload and row-save request. |

Most interaction uses Ajax `POST /pje/Processo/update.seam` with the current
`javax.faces.ViewState`, `AJAXREQUEST`, and often `ajaxSingle`. Component IDs
such as `j_id1073` change with the rendered view and should be read from each
response. Cookies, the draft `idProcesso`, and the upload `cid` connect the
steps. Periodic `HEAD /pje/Processo/update.seam` responses return HTTP 202.

## Review boundary

The information panel in flow 208 includes `formBotoesAcao:btnProtocolar`.
Its browser handler would Ajax-submit `formBotoesAcao` to
`/pje/Processo/update.seam` and then open a protocol/distribution result page.
No request containing that button was captured, so its payload, validation,
signing requirements, response, and result page remain unverified.

The draft implementation should stop after loading the information panel and
return a review summary: draft ID, court/class/subject, parties and roles,
characteristics, and document names/types/order. Final protocol submission
belongs in a separate action after explicit user confirmation; it is outside
the observed flow and this implementation scope.

## Direct-request verification on 2026-10-05

Draft `4167920` was created through direct requests by selecting the captured
area, jurisdiction, and class, then submitting **Incluir**. Subject `11946` was
added to that same draft. The browser later removed its author, and direct
requests added the author back using CPF `12433377617`. Reloading the Partes
tab showed the author. Direct requests then associated the captured attorney,
INSS in the passive pole, CEAB-DJ INSS as **ÓRGÃO DE CUMPRIMENTO**, and
Ministério Público da União as **FISCAL DA LEI**. A subsequent unified fill run
found every item already present and found the draft in **Não Protocolados**.
The old template's separate **REPRESENTANTE** entry was omitted after review
of the actual draft. No address, document, or protocol action was submitted by
the script.

The live portal had advanced from version `2.11.2.0-38` in the first capture
to `2.11.2.0-39` during this verification. A fresh browser capture showed that
the CPF input mask sends `124.333.776-17`; sending unmasked digits caused the
person-confirmation request to redirect to `errorUnexpected.seam`. After
applying the mask, that confirmation succeeded. The browser also opened the
**Endereços** tab before **Inserir Parte**. This tab is optional for address
entry; the direct request path opens it to use the rendered insert form but
leaves all address fields empty. PJe's immediate insert Ajax response omits the
active-party list, so verify the insertion by reopening the draft and Partes tab.

The `filing` CLI has `create-fill` for a new draft and `fill` for an existing
checkpoint. Both fill paths use one authenticated session for all steps and
save the draft ID before continuing.

## Characteristics capture on draft 4167920

The focused private file `pje-characteristics-manual-20261005.mitm` records a
later browser pass. Its flows 37–39 open **Características**, select **Juízo
100% Digital** (current option value `412`), and click **Incluir** through
`formAddPrioridadeProcesso:save`. A direct reload confirms that priority is
listed on the draft. The main form instead uses
`formAdicionarCaracteristicasProcesso:salvaCaracteristicaProcessoButton` for
**Justiça gratuita**, **Tutela/liminar**, and **Valor da causa**; that button was
not submitted in the capture. On a direct reload, claim value is blank and both
radio fields are `false`. Secrecy has a separate `frmSegredoSig:grvSegredo`
action. Priority and main characteristic values therefore have separate save
actions. The direct-request method later saved **Valor da causa = R$ 5.000,00**,
**Justiça gratuita = Sim**, and **Tutela/liminar = Não**; reopening the form
confirmed all three. The browser then removed **Juízo 100% Digital**, and the
direct-request method selected and included it again. A final unified fill run
confirmed the saved values and priority without duplicating any party or
priority, and found the draft in **Não Protocolados**. The script still sends
no address, document, secrecy-change, or protocol action.

An older browser tab later appeared to show Justiça gratuita = Não and a blank
claim value. The proxy capture shows its `GET /pje/Processo/update.seam` was
redirected to login at 17:12:54 UTC, after which that stale page submitted a
main-form Save request with `false` and an empty claim value. A separate fresh
certificate login still read `true` and `R$ 5.000,00` from draft `4167920`.
Reopen the draft after reauthentication before judging the saved values.

## Document upload verification on draft 4167920

The focused private capture `pje-documents-manual-20261005.mitm` records the
browser adding one main PDF and two supporting PDFs, classifying and saving
each supporting row, then removing all three. In this later PJe version,
**Petição inicial** has option value `217`, **Documento Comprobatório** `162`,
and **Carta de Indeferimento** `138`. These are current form options, not stable
API identifiers. The first support file was classified as Documento
Comprobatório; the second as Carta de Indeferimento.

The main PDF upload uses the `raTipoDocPrincipal=PDF` mode, a RichFaces
progress request, multipart `update.seam` upload, another progress request,
the file-upload completion action, and `gravarDocumentoPdf`. A fresh draft
view can open in HTML mode even when a PDF was saved, so the editor's missing
filename is not evidence of failure. The **Informativo** document grid is the
reliable readback. The **Não Protocolados** link supplies the current draft
conversation URL for document actions.

The direct-request implementation in the `filing` package uploaded the main petition
PDF to draft `4167920`; its first verification falsely reported failure
because the editor reopened in HTML mode. The Informativo grid confirmed the
PDF was saved. Two synthetic support PDFs were then uploaded for the first
direct trial and removed. The exact two browser-captured support PDFs were
subsequently uploaded by direct request, classified as **Documento
Comprobatório** and **Carta de Indeferimento**, and confirmed in a fresh
authenticated readback. The draft remains in **Não Protocolados**. The method
first registers each support file through
`jsonProcessoDocumento`, selects the row's document type, posts `file[]` to
`/pje/seam/resource/upload?cid=...`, invokes the row's type refresh and save
actions, then checks the Informativo grid. The private payload names the local
file paths; `create-fill`, `fill`, and the `documents` CLI stage now use them
when `documents` is present. No `btnProtocolar` action is implemented or sent.
