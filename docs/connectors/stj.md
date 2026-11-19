# STJ: conector da Central do Processo Eletrônico (CPE)

Servidor MCP `stj` (`jurisync-stj`, `python -m jurisync.connectors.stj.server`). Lê os processos do advogado no STJ,
os dados do processo e os documentos. É somente leitura em relação ao STJ.

## Login

- Usa o certificado A1 (e-CPF) por mTLS: `POST https://cpe-auth.web.stj.jus.br/api/auth/cert` com corpo `{}`. O
  certificado vai no handshake TLS e não há 2FA.
- Credenciais: `STJ_A1_P12`/`STJ_A1_PIN` quando o escritório tem um A1 só para o STJ; senão, o A1 compartilhado
  `PJE_A1_P12`/`PJE_A1_PIN` (`core.certificate.credential_names("STJ")`). No modo hospedado, os mesmos nomes vêm do
  cofre do escritório.
- A resposta traz um JWT (`token`) com `exp` de cerca de 4 h. As chamadas seguintes mandam o JWT cru no cabeçalho
  `authorization`. Ele fica só em memória, no `SessionPool`: o pool faz login de novo quando faltam menos de 10 min
  para o `exp` ou quando a API responde 401.
- **Advogado não cadastrado**: antes do login, o conector pergunta `GET /api/credenciado/isCredenciado/{cpf}`. Esse
  endpoint é público e o CPF sai do CN do e-CPF. Se a resposta não for `true`, ou se o login devolver
  `novoCredenciado: true`, o conector levanta `StjNotRegistered` com uma mensagem clara e para. Não segue para o
  cadastro.
- Um A1 sem CPF no CN (e-CNPJ) é recusado antes de qualquer requisição.
- O login por CPF+senha (reCAPTCHA + TOTP/e-mail) e o gov.br não foram implementados.

## Tools

| Tool | Endpoint(s) CPE | Observação |
|---|---|---|
| `stj_list_processes(status, page, page_size)` | `POST /api/advogados/processosFiltro` `{page, qtd, tramitando: S\|N\|T}` | Processos do advogado ("Meus processos"). O POST só filtra |
| `stj_get_process(process, include_documents)` | `getNumeroRegistroByNumeroUnico` / `getNumeroRegistroBySiglaClasseNumero` → `/processo/{reg}`, `/partesadvogados`, `/faseprocessoext`, `/indiceprocesso` | Aceita CNJ, registro STJ (`2026/0393422-3`) ou classe e número (`AREsp 3415877`). Quando um CNJ tem vários registros, usa o mais recente e lista os outros em `relatedRecords`. Não devolve CPF/CNPJ das partes |
| `stj_read_documents(process, document_ids)` | `/pecas/{reg}/getPecaAutorizacao?indLocal=P&seq={sequencialPeca}` → `/visualizador/externo/processos/{reg}/peca?autorizacao&token` | 1 a 5 ids, todos do índice recém-lido. A autorização da peça nunca sai do cliente. Devolve o texto via `core.document_text` |
| `stj_download_document(process, document_id)` | idem | Salva o arquivo em `JURISYNC_DOCUMENTS_DIR` como `STJ_<registro>_<id>.pdf` |
| `stj_list_publications(oab, page, page_size)` | `/api/advogados/minhasOABs/{cpf}` → `/api/advogados/{OAB}/publicacoes` | DJe próprio do STJ, que é legado (casos antigos) |
| `stj_get_counters()` | `/api/advogados/{OAB}/qtdprocessotramitacao`, `processosInteresse`, `/api/mensagens/proxima`, `/api/processo/{registro}/vistasEmAberto` | Resumo: processos ativos/encerrados por OAB, interesses, processos com vista aberta, mensagem não lida do CPE. Intimações não entram (vão pelo DJEN) |
| `stj_list_inbox(days, include_text)` | vistas em aberto (`values` = `seq` das partes que o advogado representa) + DJEN `siglaTribunal=STJ` | Caixa: vistas abertas, processos ativos com última fase e intimações do DJEN na janela. Nada é aberto nem recebe ciência; a ciência antecipada segue recusada |

As **intimações** do STJ saem no DJEN (`djen_search_communications` com `tribunal=STJ`). O `djen.resolve_portal`
reconhece links de `processo.`, `ww2.` e `cpe.web.stj.jus.br` e o CNJ J=3 TR=00 como portal `stj`.

## Somente leitura

`client.require_read` roda antes de qualquer requisição e só deixa passar:

- `GET` em `/api/...`;
- `POST` somente em `/api/advogados/processosFiltro`.

Qualquer caminho que case com escrita é recusado com `PermissionError`, seja qual for o método: ciência antecipada
(`/cienciaAntecipada/*`, inclusive a lista), petição/peticionamento, `incluir*`, cadastro e conta (`signup`,
`register`, `reset*`, `credenciado/*` exceto `isCredenciado`/`comPerfis`), favoritos, interesse, anotações, marcar
mensagem como lida, chave de acesso, Sisbajud e `auth/*` exceto `auth/cert`. Os testes em
`tests/connectors/stj/test_stj.py` cobrem a lista e confirmam que nenhuma tool manda uma requisição fora dela.

Ler peças não registra ciência: o STJ intima pelo DJEN, e a ciência antecipada é um POST à parte, que o conector recusa.

## Registro

- Portal: `portals.py` `stj` (host `stj.jus.br`, `certificate_mtls`, prefixo `STJ`, `wired=True`).
- `pyproject` (`jurisync-stj`), `.mcp.json` (`stj`), `install/serve.py`, `install/setup.py` e
  `hosted/app.py` (`TENANT_READY`).

## Validação

Feita ao vivo em 16/11/2026 com a anaflavia: veja `docs/validation/stj-2026-11-16.md`.
