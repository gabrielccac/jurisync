# TRF5 PJe: prepare a process draft

`src/jurisync/connectors/pje/filing/` creates and fills a TRF5 1G PJe draft by
direct HTTP requests. It stops with the draft in **Não Protocolados**, ready for
review and manual **Protocolar**. The request captures and implementation
findings are in [the flow notes](pje-trf5-filing-flow.md).
Victor's Advbox preparation, document routing, and office defaults belong in
`customers/victor/workflows/`; this PJe adapter consumes the resulting explicit
payload. See [Victor's workflow guide](../../customers/victor/README.md) for the
Advbox preparation command, Jev review, and private case-facts format.

## How the package is organised and how re-runs stay fast

```text
src/jurisync/connectors/pje/filing/
  transport.py        HTTP, multipart and JSF postbacks (counts every request)
  classification.py   Dados iniciais: the classification form and Incluir
  subjects.py         assuntos          author.py       author party and address
  parties.py          attorney, public bodies    characteristics.py   legal aid, claim value, priority
  documents.py        main petition and supporting PDFs     pending.py   Não Protocolados lookups
  fill.py             the phases below      checkpoint.py   ledger and atomic checkpoint writes
  timeline.py         per-step timing       payload.py / names.py / parsing.py   validation and helpers
  run.py              stages and the command line (client.py composes the sections into DraftClient)
  approval.py         approval.json: the recorded approval every payload write stage requires
  store.py            RunStore: the only reader and writer of a run's files (LocalRunStore today)
```

`fill` and `create-fill` work in four phases: subjects, parties, characteristics, documents. Each phase reads the
draft once, acts only on what is missing, and writes the phase and a fingerprint of the payload slice it used into
`checkpoint.json` (`completed`). A re-run skips phases whose fingerprint still matches and only confirms the draft is
listed in **Não Protocolados**. Changing the payload for one phase reruns only that phase. `--verify-all` ignores the
record and re-checks everything; use it when something may have changed in PJe by hand.

Documents are listed once before uploading and once after the last upload; the upload responses themselves confirm
that PJe registered each row and accepted its bytes. Every step is recorded in `filing-steps.json` next to the
checkpoint: run ID, step, document or party, status, seconds, and PJe requests (each request is counted once).

## Codex desktop chat and MCP

The PJe MCP exposes `pje_draft_inspect` for current classification choices,
subject choices, and **Não Protocolados** checks. `pje_draft_write` exposes the
CLI's create/fill stages and requires `PJE_ALLOW_DRAFT_WRITES=true` in the MCP
process. Neither tool exposes **Protocolar**.
`pje_search_pending_drafts` searches the first **Não Protocolados** results
page by party name, CPF, or both, without requiring a filing run.

The inspection and write tools take a `runId` and open that run's store,
`$JURISYNC_FILING_DIR/<runId>` (default:
`~/.local/share/jurisync/filings/<runId>`; see the self-contained run section below). Prepare Victor's reviewed payload
with `--out` set to that same directory. Keep one unique run ID per draft;
after **Incluir**, reuse its checkpoint and never call `create` again for that
run. The desktop workflow is described in
[Victor's filing skill](../../customers/victor/skills/victor-pje-filing/SKILL.md).
The local Codex desktop setup already registers this PJe MCP server. Ordinary
ChatGPT chat needs a separate local plugin connection to use these tools.

## Payload

The current working mock payload is private at
`~/.local/share/jurisync/pje-draft-payload.json`. Its shape is:

```json
{
  "portal": "trf5-1g",
  "classification": {
    "area": "DIREITO PREVIDENCIÁRIO",
    "jurisdiction": "PE / Petrolina",
    "judicialClass": "PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)"
  },
  "subjects": [
    {"search": "Assistencial", "code": "11946", "name": "Pessoa com Deficiência"}
  ],
  "authorCpf": "<CPF do autor>",
  "author": {"name": "<nome esperado no cadastro do PJe>", "address": {"cep": "<8 dígitos>", "numero": "<número>"}},
  "attorney": {
    "cpf": "<CPF da advogada>",
    "name": "GRAZIELE ALVES DA SILVA",
    "oabNumber": "48647",
    "oabState": "PE"
  },
  "passive": {
    "search": "inss",
    "cnpj": "29979036000140",
    "name": "INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS"
  },
  "otherParticipants": [
    {
      "role": "ÓRGÃO DE CUMPRIMENTO",
      "search": "ceab",
      "cnpj": "29979036001465",
      "name": "CEAB-DJ INSS"
    },
    {
      "role": "FISCAL DA LEI",
      "search": "ministério público",
      "cnpj": "26989715005414",
      "name": "MINISTERIO PUBLICO DA UNIAO"
    }
  ],
  "characteristics": {
    "claimValue": "5.000,00",
    "legalAid": true,
    "injunction": false,
    "priority": "Juízo 100% Digital"
  },
  "documents": {
    "main": {
      "path": "documents/01-PETICAO INICIAL.pdf",
      "type": "Petição inicial",
      "description": "Petição inicial",
      "sha256": "<sha256 do PDF aprovado>"
    },
    "supporting": [
      {
        "path": "documents/02-ANEXO.pdf",
        "type": "Documento Comprobatório",
        "description": "Anexo",
        "sha256": "<sha256 do PDF aprovado>"
      }
    ]
  }
}
```

The example identifiers and classifications reflect the captured mock case;
replace them for each real matter. Document paths are relative to the run folder and must be inside its
`documents/`; the connector resolves them through the run's store. The live TRF5 upload panel shows PDF files up to 10.0 MB; this
client uses a conservative 10,000,000-byte limit per file. DOC/DOCX sources
must be converted before this adapter receives them. `number` is an optional
document field; supporting `description` defaults to the filename stem.
Victor's split supporting PDFs set `number` to the volume fraction (for example,
`1/2`) and repeat it in the description.
`claimValue` uses Brazilian currency format, and `legalAid` and `injunction`
are JSON booleans. Omit `characteristics` or `documents` only when intentionally
stopping before those stages.

### How selections are resolved

| Item | Payload search input used now | Result selected or checked | Other lookup fields shown by PJe |
| --- | --- | --- | --- |
| Area, jurisdiction, class | Exact displayed labels | Script reads each option's current value after the preceding selection refreshes the list. The captured jurisdiction value was `30`; it is not stored in the payload. | No alternate lookup implemented. |
| Subjects | Each `subjects[].search` text | Each code and name must match one result row; subjects are added in array order. | Code/name are result identifiers; searching directly by code has not been verified. |
| Author | `authorCpf`; optional `author.address.cep` | `author.name` must match the person PJe returns; the CPF is masked for the form. A unique registered address with the requested CEP is reused, otherwise PJe's CEP suggestion is selected and `author.address.numero` is required. | The name is a returned identity field, not a second search mode in this implementation. |
| Attorney | `attorney.cpf` | Returned `attorney.name`; associated row must contain the CPF, name, and `attorney.oabState` + `attorney.oabNumber`. | The attorney form also shows OAB number and UF inputs. Searching by OAB instead of CPF is not implemented or verified. |
| INSS and other public bodies | `search` name text | Select the unique row with `cnpj`, then associate it with the chosen pole or `role`. | The name search may return multiple rows; CNPJ identifies the intended result. Direct CNPJ search has not been verified. |
| Priority and document types | Exact displayed labels | Script reads current select values from PJe. | Numeric option values from captures are not stable payload values. |

PJe's benefit choice in this flow is the **Assunto** lookup. The integration can
now list its current choices without adding one to a draft:

```bash
.venv/bin/python -m jurisync.connectors.pje.filing \
  --stage subject-options \
  --run ~/.local/share/jurisync/filings/<runId> \
  --search "Incapacidade Temporária" --page 1
```

The response contains each result's exact code, leaf name, full subject path,
and the total result count. Search again with `--page 2` when needed. A broad
term may return other legal areas, and PJe omits subjects already attached to
the queried draft. Check the intended area and exact code before selection.
Historical TRF5 filings can have several associated Assuntos. Supply one or
more distinct entries in `subjects`; each is looked up and verified on the
draft. The older singular `subject` form remains accepted. Resolve the full
subject set from the petition and case review before filling a real draft.

The logged-in certificate user is not an `attorney` payload entry. The script
explicitly associates Graziele by CPF; it does not add or verify Victor as a
separate representative. Review the actual Partes tab before protocol.

## Integridade: hash, aprovação e nomes (Fase 1)

O que vai para o PJe tem de ser exatamente o que o advogado revisou. Por isso:

- **Hash no payload.** Cada documento em `documents.main` e `documents.supporting[]` leva `sha256`, preenchido pelo
  prepare/finalize a partir do `uploadSha256` do `review.json`. `validate_fill_payload` reconfere todos os arquivos
  antes do login, e cada upload relê o arquivo, reconfere o hash e envia exatamente esses bytes. Divergência → erro
  `Document changed since approval` (documento mudou desde a aprovação). Payloads antigos, sem `sha256`, são recusados
  com a instrução de rodar `filing_finalize` de novo; se o run já tem rascunho (o finalize recusa runs com
  `checkpoint.json`), é preciso um novo `runId`.
- **Aprovação registrada.** `filing_approve(runId)` (ou `python -m jurisync.workflows.filing approve --run RUN_DIR`)
  grava `approval.json` (0600, escrita atômica) com o horário, o sha256 do `payload.json` e o sha256 de cada documento,
  depois de reconferir os arquivos. Todo prepare e todo finalize apagam o payload anterior e a aprovação: o prepare
  ainda grava `payload.json` quando não há itens de revisão, mas sem aprovação. `pje_draft_write` e o CLI do conector
  (`--stage` de qualquer etapa que escreve conteúdo do payload; `delete-draft` não usa payload) recusam antes do
  login se não houver aprovação ou se ela não bater com o payload atual. A ferramenta só registra o "sim" do usuário;
  a skill instrui o agente a chamá-la apenas depois de uma confirmação explícita.
- **Upload por conteúdo, não por nome.** O PJe lista os documentos do rascunho só pelo nome do arquivo. Antes de cada
  envio, o run grava em `checkpoint.json` → `uploads` o par `sha256 → {name, type}`. Um nome já presente no rascunho
  só conta como "já enviado" se o registro desse nome tiver o sha256 do documento aprovado. Mesmo nome com hash
  diferente (arquivo alterado depois do envio) ou sem registro algum → erro explícito, sem reenviar e sem pular em
  silêncio. **Decisão: falhar, não substituir.** As capturas mostram que o navegador remove documentos do rascunho,
  mas a requisição de remoção de um anexo já gravado não foi mapeada, e para a petição principal o PJe troca o PDF
  ao receber outro, porém a releitura do Informativo só mostra o nome, então não há como provar que a versão nova
  ficou. O usuário remove o documento antigo no PJe (ou começa um novo `runId`) e roda `fill` de novo.
  `pje_draft_status` com `checkPje: true` informa em `contentNotConfirmed` os documentos cujo conteúdo no rascunho não
  é confirmado pelo registro, e nesse caso a conclusão não é `verified`.
- **Nomes únicos.** A limpeza do nome (sem acentos, sem o sufixo aleatório do ADVBOX) pode juntar dois arquivos
  diferentes num só nome: `A_1111.pdf` e `Ã_2222.pdf` → `A.pdf`. Desde a Fase 2 cada arquivo da montagem ganha o
  número da sua posição (`02-A.pdf`, `03-A.pdf`), o que já os separa; `CanonicalNames` continua como defesa e, se dois
  nomes ainda coincidirem (comparados como o PJe compara, sem acento e sem diferenciar maiúsculas), acrescenta um
  sufixo curto do hash.
- **Overrides restritos.** Em `filing_finalize`, `overrides` só aceita `role` (`main`, `supporting`, `skip`), `type`
  e `description`, todos texto não vazio; qualquer outra chave (como `path` ou `uploadSha256`) é erro.

## Montagem autocontida e RunStore (Fase 2)

Um run é uma pasta autocontida. Nada no `review.json`, no `payload.json` ou no `manifest.json` guarda caminho
absoluto: copiada para outra pasta (ou, no futuro, para um bucket), ela continua válida, inclusive a aprovação.

```text
<runId>/
  facts.json  inbox.json  review.json  payload.json  approval.json  manifest.json  checkpoint.json  filing-steps.json
  inbox/...                                                                        a entrada: cópia da pasta recebida
  documents/01-PETICAO INICIAL.pdf  02-PROCURACAO.pdf  03-LAUDOS-vol-01.pdf ...   a montagem final
  work/converted/  work/split/                                                     área de trabalho do prepare
```

- **`documents/` é a montagem.** O finalize (e o prepare, quando não há pendências) copia para `documents/` só os
  arquivos escolhidos, na ordem do protocolo: a petição inicial é `01`, depois os anexos na ordem da pasta, cada
  volume de PDF dividido com seu próprio número (`03-LAUDOS-vol-01.pdf`). O número da pasta do escritório
  (`10-PETIÇÃO INICIAL`) dá lugar ao nosso. Cada arquivo só é copiado depois de conferir o `uploadSha256` da revisão.
  Todo prepare e todo finalize refazem a montagem do zero (apagam `documents/`, `manifest.json`, `payload.json` e
  `approval.json`). As antigas pastas `files/`, `converted/`, `split/` e `upload/` deram lugar a `work/` + `documents/`.
- **`manifest.json`** lista cada arquivo montado: caminho, sha256, tamanho, papel, tipo, descrição, volume e de qual
  linha da revisão veio (`reviewId`, `sourceName`), mais o hash do arquivo de entrada (`inputSha256`), como ele foi
  derivado (`derivedBy`) e a origem (Fase 3).
- **Caminhos relativos no payload.** Cada documento é `documents/NN-nome.pdf` + `sha256`; o conector resolve pelo
  store do run. **Decisão: payload antigo com caminho absoluto é recusado**, com a mensagem de rodar `filing_finalize`
  de novo (ou usar novo `runId`, se já há rascunho). Desde a Fase 1 todo payload real já precisava ser refeito por
  causa do `sha256`, e um caminho absoluto aponta para fora da montagem aprovada. O `status` continua lendo esses
  runs (sem comparar fases). Caminhos fora de `documents/` ou com `..` também são recusados.
- **Run com rascunho.** Depois do `checkpoint.json`, o prepare recusa e o finalize do CLI só aceita mudar fatos do
  caso: se a montagem resultante for diferente da que já está no payload, ele recusa sem tocar em `documents/`
  (o `filing_finalize` do MCP continua recusando qualquer mudança). A aprovação é sempre exigida de novo.
- **RunStore** (`connectors/pje/filing/store.py`) é a única porta de leitura e escrita do run, para prepare,
  finalize, approve, o conector, o status e a linha do tempo: `exists`, `read_bytes`, `read_json`, `write_bytes`,
  `write_json` (atômicos, 0600, pastas 0700), `delete`, `delete_tree`, `list`, mais `local_path` (um arquivo local com
  o conteúdo, para leitores de PDF e uploads) e `workspace` (pasta local para ferramentas que escrevem arquivos:
  download, conversão, divisão) e `name_of` (caminho local → nome do run; fora do run é recusado). Os nomes são
  relativos e validados. `open_run(runId)` devolve hoje um `LocalRunStore`; uma versão em nuvem (S3/GCS) implementa
  os mesmos métodos, com `local_path`/`workspace` como cache local, sem mudar prepare, finalize ou o conector.

## Entrada única: inbox/ e sources.json (Fase 3)

O jurisync não busca documentos por conta própria. Quem os obtém é o agente, pelos conectores (Drive, GERID,
ADVBOX...), numa pasta local; o jurisync recebe essa pasta e cuida de preparar e protocolar. A única busca embutida é a
da pasta `00-PROTOCOLAR` do ADVBOX, mantida por compatibilidade, e ela agora só preenche a mesma entrada.

- **Entrada.** `filing_prepare` com `sourceFolder` (caminho absoluto da pasta) ou `customerId` (ADVBOX), nunca os
  dois; no CLI, `prepare --from PASTA` ou `prepare --customer ID`. A pasta é copiada (com subpastas) para
  `<run>/inbox/`; arquivos ocultos (`.DS_Store`) são ignorados, links simbólicos são recusados, no máximo 200 arquivos.
  O caminho local da pasta não é gravado em lugar nenhum, só os caminhos dentro dela.
- **Hash na entrada.** `inbox.json` registra, por arquivo, o SHA-256 dos bytes copiados, tamanho, horário da cópia e
  origem. O prepare lê **só** a `inbox/` e reconfere esses hashes antes de começar.
- **Origem.** `declared` quando o `sources.json` da pasta descreve o arquivo (o jurisync registra o que o agente
  declarou, sem poder verificar); `local` quando o arquivo não aparece no `sources.json`; `fetched` quando o próprio
  jurisync baixou (ADVBOX: id do arquivo, pasta, nome original e data de envio).
- **Esquema do `sources.json` (versão 1)**, escrito pelo agente na raiz da pasta:

  ```json
  {
    "version": 1,
    "files": [
      {
        "file": "laudos/laudo-caps.pdf",
        "origin": "drive",
        "originId": "1AbC...",
        "originPath": "Clientes/Maria/Laudos",
        "originalName": "Laudo CAPS.pdf",
        "modifiedAt": "2026-10-01T12:00:00Z",
        "link": "https://drive.google.com/file/d/1AbC..."
      }
    ]
  }
  ```

  `file` (caminho dentro da pasta) e `origin` (texto livre: `drive`, `gerid`, `advbox`, `local`...) são obrigatórios;
  os demais são opcionais. É recusado: versão diferente de 1, campo desconhecido, `file` absoluto ou com `..`, o mesmo
  arquivo descrito duas vezes, entrada para arquivo que não está na pasta, `link` que não seja http(s), JSON inválido.
  A validação acontece antes de qualquer cópia.
- **Imagem → PDF.** JPG, JPEG e PNG viram PDF de uma página com o Pillow (já dependência): orientação EXIF corrigida,
  transparência sobre fundo branco e, se passar de 10 MB, a imagem é reduzida em etapas (uma imagem é uma página, não
  se divide). **HEIC:** o Pillow não lê HEIC sozinho; sem o pacote opcional `pillow-heif` (não adicionado, por ser
  dependência nativa pesada) o arquivo vai para revisão pedindo que seja exportado como JPG ou PDF. Com `pillow-heif`
  instalado, a conversão é automática. Word continua sendo convertido como antes; PDFs acima de 10 MB continuam
  divididos em volumes.
- **Rastreio pelo hash.** Cada linha da revisão guarda o hash de entrada (`sha256`), a origem e como foi derivada
  (`copy`, `word-to-pdf`, `image-to-pdf`); volumes de PDF dividido guardam `inputSha256`. O `manifest.json` liga cada
  arquivo de `documents/` (seu `sha256`, que é o do payload, da aprovação e do registro de upload no checkpoint) ao
  `inputSha256` e à origem. A cadeia é: `inbox.json` → `review.json` → `manifest.json` → `payload.json` →
  `approval.json` → `checkpoint.json` (`uploads`).
- **Auditoria.** `filing_prepare`/`filing_review` mostram a origem de cada arquivo, e `filing_finalize` a de cada
  documento do payload, sem caminho local nem hash.

## Run and review

Certificate and 2FA settings come from the environment: `PJE_A1_P12`,
`PJE_A1_PIN`, and `PJE_TRF5_2FA_DATA` or `PJE_TRF5_2FA_QR`. Keep them out of
the JSON payload. With those values loaded:

```bash
.venv/bin/python -m jurisync.connectors.pje.filing \
  --stage create-fill \
  --run ~/.local/share/jurisync/filings/<runId>
```

`--run` is the run folder: its `payload.json`, `approval.json`, `checkpoint.json` and `documents/` are used from there
(the old `--payload`/`--checkpoint` options are gone). The payload must have been approved first (see the integrity
section above), otherwise every stage that writes payload content stops before logging in. Use a new run for each new
draft. **Incluir** creates the draft; the script saves its ID and URL to the run's checkpoint immediately. A later
failure does not erase that draft. Resume the same draft with `--stage fill` and the same run. `--stage documents` only handles the
PDFs on an existing draft. These stages authenticate once per run and keep the
same session for that run.

The sequence is:

1. Select area, jurisdiction, then class, and press **Incluir** to create the draft.
2. Search and associate each subject, then the author, attorney, passive INSS, CEAB, and Ministério Público.
3. Save Justiça gratuita, tutela/liminar, and valor da causa; separately include Juízo 100% Digital when requested. Set `priority` to `false` or omit it to leave the priority list unchanged; `"NÃO"` is not a PJe option label.
4. Upload and save the single main petition PDF, then upload, classify, and save each supporting PDF. Read the **Informativo** document grid back to confirm them.
5. Confirm the draft appears in **Não Protocolados**. Open it in PJe and review all fields and PDFs. Submit **Protocolar** manually when the real case is ready.

The individual direct-request actions and readbacks were exercised on mock
draft `4167920`, including the documents. A full `create-fill` run from an
empty new draft through documents has not yet been exercised in one invocation.
The final protocol request and its signing or result flow have not been mapped.

## Historical document types in Minhas petições

The authenticated **Painel do Advogado** is
`/pje/Painel/painel_usuario/advogado.seam`. Its `tabPeticoes` tab opens
**Minhas petições**. The `pedidoPeticaoForm` can filter by CNJ process number
and inclusion/juntada dates. Results show process number, document description,
PJe document type, and document/receipt links. PJe permits at most **12 months**
between search dates. A read-only search on 2026-10-05 used
05/10/2025–05/10/2026 and one authenticated session for ten process-number
queries. The grid displays at most 16 rows per page.

| Process | Rows seen / reported | Relevant saved types |
| --- | ---: | --- |
| `0005394-06.2026.4.05.8308` | 16 / 18 | Laudos → Laudo Médico; PA → Processo Administrativo; quesitos and procuração → Outros Documentos |
| `0009696-30.2025.4.05.8303` | 16 / 32 | Later identity/residence bundle and certificates → Outros Documentos; initial package is beyond the visible page |
| `0011038-42.2026.4.05.8303` | 12 / 12 | Comunicado, laudos, quesitos → Documento Comprobatório; PA → Processo Administrativo |
| `0004131-48.2026.4.05.8304` | 15 / 15 | Comunicado and laudos → Documento Comprobatório; PA → Processo Administrativo |
| `0004131-36.2026.4.05.8308` | 16 / 16 | Comunicado → Carta de Concessão; comunicado de cessação → Carta de Indeferimento; laudos → Laudo Médico; contrato de honorários → Documento Comprobatório |
| `0007999-71.2025.4.05.8303` | 16 / 18 | PA → Documento Comprobatório; procuração → Outros Documentos |
| `0012409-41.2026.4.05.8303` | 10 / 10 | Comunicado → Carta de Indeferimento; contrato e procuração → Instrumento de Procuração; PA → Processo Administrativo |
| `0006822-23.2026.4.05.8308` | 14 / 14 | Comunicado and laudos → Documento Comprobatório; procuração → Instrumento de Procuração; PA → Processo Administrativo |
| `0003232-87.2025.4.05.8303` | 11 / 11 | Comunicado and laudos → Documento Comprobatório; procuração → Outros Documentos |
| `0001748-37.2025.4.05.8303` | 14 / 14 | Comunicado and laudos → Documento Comprobatório; procuração e contrato → Outros Documentos |

For a **new initial petition** payload, use the stable filename rules first
and inspect content when the type remains uncertain:

| Source document | Proposed PJe type | Review condition |
| --- | --- | --- |
| Main petition | Petição inicial | Exactly one main petition. |
| Valor da causa / RMI calculation | Planilha | A later calculation petition used Planilha de Cálculo; keep that separate from the initial value sheet. |
| Author's RG or identity card | Documento de Identificação | A family member's or representative's ID in a combined proof bundle may be Documento Comprobatório. |
| Procuração; contract | Outros Documentos | Matches the collaborator's current instruction. Historical filings also used Instrumento de Procuração for powers of attorney and Documento Comprobatório for a fee contract. |
| Quesitos | Outros Documentos | One historical filing used Documento Comprobatório; retain the collaborator's requested default. |
| Administrative process / PA | Processo Administrativo | Prefer the specific type; one historical case used Documento Comprobatório. |
| Comunicado de decisão | Carta de Indeferimento or Carta de Concessão | Read the decision outcome: explicit denial → Indeferimento; explicit grant → Concessão. A cessation or refusal to extend an existing benefit needs review. Historical generic labels are not reliable evidence of the outcome. |
| Medical reports and exams | Laudo Médico or Documento Comprobatório | Choose from the actual content and the intended filing convention; both occur in initial packages. |
| CNIS/CTPS, residence, rural/family/LOAS evidence | Documento Comprobatório | The general fallback applies to supporting evidence of this kind. |

### What the decision letters actually say

The eight available PDFs behind the visible **Comunicado de decisão** rows
were inspected on 2026-10-05, including rendered pages where text extraction
failed. Personal names and benefit numbers are omitted here.

| Process | PDF content | Saved PJe type | Content-based route |
| --- | --- | --- | --- |
| `0011038-42.2026.4.05.8303` | Medical review did not recognize incapacity; an existing benefit was not extended | Documento Comprobatório | Review as non-extension/cessation |
| `0004131-48.2026.4.05.8304` | BPC right not recognized; request denied | Documento Comprobatório | Carta de Indeferimento |
| `0004131-36.2026.4.05.8308` (decision) | Explicit *Deferimento do Pedido* and recognized right to benefit | Carta de Concessão | Carta de Concessão |
| `0004131-36.2026.4.05.8308` (cessation) | Rehabilitation completed; existing benefit ended | Carta de Indeferimento | Review as cessation; it is not an initial denial |
| `0012409-41.2026.4.05.8303` | Explicit *Indeferimento do Pedido* | Carta de Indeferimento | Carta de Indeferimento |
| `0006822-23.2026.4.05.8308` | Explicit *NEGADO* | Documento Comprobatório | Carta de Indeferimento |
| `0003232-87.2025.4.05.8303` | Explicit *Indeferimento do Pedido* | Documento Comprobatório | Carta de Indeferimento |
| `0001748-37.2025.4.05.8303` | Explicit *NEGADO* | Documento Comprobatório | Carta de Indeferimento |

Thus, the title *Comunicado de decisão* does not determine the type, but the
decision text usually does: five of eight are direct denials, one is a grant,
and two concern ending or not extending an existing benefit. Four direct
denials were historically saved as Documento Comprobatório, so the saved PJe
type is a record of user choice, not a dependable semantic label. For a new
draft, extract the operative decision language and route clear denials and
grants by outcome. Victor's preparation workflow flags cessation/non-extension,
illegible pages, mixed decisions, and missing operative language and assigns
`Outros Documentos` as the upload fallback. Review the flagged list before
manual protocol.

Do not route a document only because its name contains a keyword: later
**Manifestação**, **Petição (outras)**, **Réplica**, and appeal filings in these
same processes use procedural types, and combined PDFs may need a different
type from their individual components. The shared PJe script accepts an
explicit `type` per PDF and checks that PJe offers that label.

### Advbox filename coverage

A review of ten `00-PROTOCOLAR` client folders found many stable filename
patterns (`VALOR DA CAUSA`, `DOCUMENTO DE IDENTIFICAÇÃO`, `CNIS E CTPS`,
`PROCURAÇÃO`, `QUESITOS`, `PA`) plus these cases requiring another step:

| Actual filename pattern | Handling before PJe upload |
| --- | --- |
| `COMUNICADO DE DECISÃO` / `COMUNICAÇÃO DE DECISÃO` | Read the decision outcome; the filename does not say grant, denial, or cessation. |
| `INFBEN`, `DESPACHO`, process-number-only PDF | Inspect content and decide whether the file belongs in the initial filing. |
| `LAUDO INSS`, `LAUDO MÉDICO ADMINISTRATIVO`, `DOCUMENTOS MEDICOS`, `LAUDOS MÉDCIOS E RECEITUARIOS` | Recognize spelling and wording variants, then inspect where the medical-document type is uncertain. |
| `FORMULARIO LOAS E CADUNICO`, `DOCUMENTOS DO GRUPO FAMILIAR`, `COMPROVANTE DE REQUERIMENTO` | Treat as supporting evidence after confirming its role in the case. |
| Two rural-proof or CNIS PDFs; petition in both DOCX and PDF | Keep distinct evidence when relevant; compare possible duplicate or alternate petition versions before choosing files. |
| `PETIÇÃO.doc`, `PETIÇÃO INICIAL.docx` | Identify the main petition and convert the selected version to PDF before upload. |

Numeric prefixes, generated suffixes, `(ATUAL)`, accents, capitalization, and
extra spaces can be normalized for filename matching. A specific known match
may bypass content classification; unknown, mixed, or conflicting matches go
to content inspection, then Victor's flagged `Outros Documentos` fallback if
their type remains uncertain.

**Minhas petições** provides historical submissions associated with the logged-in
lawyer; it is not a verified inventory of every document in a process. The
current `filing` package reads the new draft's **Informativo** document grid but
does not yet expose a historical **Minhas petições** search command. For a new
draft, review each Advbox file, choose its PJe type, and provide a PDF path in
the payload. A protocol folder can also contain editable petition drafts,
older process PDFs, or duplicate files.

## Author address capture

`author.address` is optional. The direct request adapter opens **Endereços**
for the author and compares registered rows by CEP. If a row with that CEP is
already selected, it continues. If another row is selected, it selects the
matching row, saves **Atualizar Parte**, then reopens the editor to verify the
selected CEP. If no row matches, it uses PJe's CEP suggestion, fills the
required `numero` and optional `complemento`, clicks **Incluir**, saves the
party, and rereads the selected CEP. A CEP with multiple or no suggestions
requires manual review. No address in the payload leaves the optional PJe
address flow unchanged.

The focused browser capture
`~/.local/share/jurisync/captures/pje-address-manual-20261006-150811.mitm`
maps an existing author's address edit on PJe 1G:

1. **Adicionar endereço** renders the existing address grid and the CEP
   autocomplete. Entering a CEP sends an Ajax lookup; selecting a returned
   suggestion sends a second Ajax request with its row selection and fills
   state, city, neighborhood, and street. Typing the CEP alone does not fill
   those fields. The separate **Pesquisar endereço** tab returned no result in
   this capture.
2. Supply the remaining fields, including number, and click **Incluir**
   (`cadastroPartePessoaEnderecobtnGravarEndereco`). PJe returned "Registro
   inserido com sucesso" and changed the grid from one address to two. The
   newly added address appeared marked **Usar no processo**.
3. Each address row has its own **Usar no processo** Ajax action. Choosing the
   older row changed the selected icon to that row; choosing the new row
   changed it back. Row indexes and JSF component IDs are generated by PJe,
   so an implementation must find the row by its rendered address, not by its
   index.
4. The final **Atualizar Parte** request
   (`formInserirParteProcesso:btnAtualizarParte`) saved the party update, and
   the party list was rendered again. The capture ends after reloading the
   process page, before reopening the address grid; persistence of the final
   selection after a fresh load has not been independently read back.

The captured address is urban; rural addresses, "sem número", and CEPs
without suggestions still need their own observed paths before automation.
