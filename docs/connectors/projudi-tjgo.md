# Projudi TJGO — exploração e plano do conector

Levantamento ao vivo em **2026-11-05**, read-only, com o A1 de uma cliente
(`JURISYNC_CUSTOMER=<cliente>`). Cobre login, abertura de processo, índice de
documentos, anexos por movimentação, download e extração de texto. Serve de
base para o futuro `src/jurisync/connectors/projudi/`; hoje não existe conector
Projudi no repositório.

Sistema: `https://projudi.tjgo.jus.br` (Java servlets, HTML ISO-8859-1,
jQuery; não é JSF).

## Resumo

| Capacidade | Situação |
|---|---|
| Login com certificado A1 (mTLS) | **Validado** |
| Login PDPJ (SSO Keycloak) | **Quebrado no TJGO** (`invalid redirect_uri`) |
| Login usuário/senha + OTP por e-mail | Existe, não automatizável sem a caixa de e-mail |
| Abrir processo por número CNJ | **Validado** |
| Índice completo de documentos | **Validado** (86 movimentações, 47 arquivos no processo testado) |
| Anexos por movimentação | **Validado** (até 17 arquivos em uma movimentação) |
| Download PDF/HTML | **Validado** |
| Extração de texto | **Validado** em 6 arquivos amostrados (PDF e HTML) |

## Autenticação

### Certificado A1 — caminho recomendado

Login por **mutual TLS**, sem TOTP e sem código por e-mail.

```text
GET  /                                  -> 200, cookie JSESSIONID
POST /LogOn        PaginaAtual=6        -> 302 /certificado?hashFluxo=...
GET  /certificado?hashFluxo=...         -> handshake TLS pede certificado cliente
                                        -> 302 /Usuario
                                        -> 302 /Usuario?PaginaAtual=7&...&LoginPronto=S
```

Detalhes:

- `/certificado` exige o certificado no handshake TLS. Sem ele:
  `sslv3 alert handshake failure`.
- Aceita A1 ICP-Brasil. É preciso enviar a cadeia de intermediários no PEM,
  junto com a chave e o certificado.
- O `hashFluxo` vem do POST `/LogOn`; sem ele o fluxo não abre.
- Depois do login, as requisições seguintes usam só o cookie de sessão; não
  precisam mais do certificado.
- Cookies observados: `JSESSIONID`, `WIDPROP` (sticky), `dtCookienncenbbl`.
- Sessão expirada: redirect para `/LogOn?PaginaAtual=-200`.
- O certificado é vinculado ao usuário pelo CPF. Certificado não cadastrado
  entra no fluxo “Cadastro com Certificado Digital” (`PaginaAtual=8`).

Evidência: painel abriu como a titular, OAB/GO, com os processos ativos dela;
a leitura subsequente funcionou sem o certificado.

### PDPJ — indisponível hoje

O botão “Acessar com PDPJ” monta:

```text
https://sso.cloud.pje.jus.br/auth/realms/pje/protocol/openid-connect/auth
  ?client_id=projudi-tjgo
  &redirect_uri=https://projudi.tjgo.jus.br/LogOnSSO
  &response_type=code&scope=openid
```

O Keycloak responde **400 “Parâmetro inválido: redirect_uri”**. O client
`projudi-tjgo` existe (com client inexistente a mensagem seria “Cliente não
encontrado”), mas o redirect registrado não bate com `/LogOnSSO`. Testadas
variações de path, http/https, barra final e `www`: todas rejeitadas. É
configuração do TJGO/CNJ; não há contorno no cliente. Retestar
periodicamente. Se corrigirem, o fluxo Keycloak pode reaproveitar o login por
certificado que o conector PJe já implementa.

### Usuário/senha + OTP por e-mail — fallback

```text
POST /LogOn  PaginaAtual=7&Usuario=...&Senha=...&AJAX=true
  -> 403 JSON {"requerOtp": true, ...}
  -> envia código de 6 caracteres para o e-mail cadastrado
POST /LogOn  ...&acaoOtp=validar&codigoOtp=XXXXXX
  -> JSON {"sucesso": true, "urlRedirect": "..."}
```

Código válido por 5 minutos, com limite de reenvios e bloqueio temporário após
excesso de tentativas. Não serve para automação sem acesso à caixa de e-mail
do usuário.

## Processos

Abrir por número CNJ (fluxo usado no teste):

```text
POST /BuscaProcesso
  PaginaAtual=2
  ProcessoNumero=<NNNNNNN-DD.AAAA.J.TR.OOOO>
  BuscaPublica=false
  QuantidadeRegistrosPagina=10
```

Com um único resultado, retorna a tela `Dados Processo` e deixa o processo
como contexto da sessão. A tela traz número, área, classe, partes, valor,
movimentações e a tabela `Eventos do Processo`.

O índice de documentos **depende desse contexto**: sessão nova sem processo
aberto retorna `[]`.

Sem filtro de status, a busca cobre só processos ativos (`ProcessoStatus=Ativo`,
`ProcessoStatusCodigo=0`) e responde `Nenhum Processo foi localizado.` para os
arquivados. Eles abrem repetindo a busca com `ProcessoStatus` vazio e
`ProcessoStatusCodigo=1` (validado em 2026-11-05 em quatro processos arquivados).

Lista dos processos do advogado (opcional para o MVP):

```text
GET /BuscaProcesso?PaginaAtual=2&Proprio=1&Id_ServentiaSubTipo=1&ProcessoStatusCodigo=0
```

A tabela mostra número reduzido, partes, distribuição e um `Id_Processo`
interno usado para abrir o detalhe.

## Documentos

No Projudi todo arquivo do processo está pendurado em uma movimentação. Não foi
encontrado endpoint de documento avulso; a aba “Navegação de Arquivo” é uma
árvore sobre o mesmo índice.

### Índice completo (uma chamada)

```text
GET /BuscaProcesso?PaginaAtual=9&PassoBusca=5&AJAX=AJAX
Header: X-Requested-With: XMLHttpRequest
```

Resposta JSON:

```json
[
  {
    "titulo": "Petição Enviada",
    "complemento": "",
    "arquivos": [
      {
        "id": "615...",
        "id_movi_arq": "615...",
        "hash": "...",
        "nome": "01._inicial_....pdf"
      }
    ]
  }
]
```

- `titulo` + `complemento` descrevem a movimentação.
- `arquivos` pode ser `null` ou uma lista com 1..N anexos.
- No processo testado: **86 movimentações**, **47 arquivos** (35 PDF, 12 HTML).
- `id_movi_arq` + `hash` são o que o download precisa.

### Anexos de uma movimentação

```text
GET /MovimentacaoArquivo?AJAX=ajax&PaginaAtual=8&Id_Movimentacao=<Id_Movimentacao>
Header: X-Requested-With: XMLHttpRequest
```

Resposta traz mais metadados por arquivo:

```json
{
  "id": "...",
  "id_arquivo": "...",
  "id_movi_arq": "...",
  "nome_arquivo": "decisao.html",
  "arquivo_tipo": "Decisão",
  "usuario_assinador": "...",
  "AcessoArquivo": 1,
  "AcessoUsuario": 3,
  "hash": "...",
  "codigo_temp": "0"
}
```

Regra de acesso do próprio portal: o arquivo é liberado quando
`AcessoUsuario >= AcessoArquivo`. Os 47 arquivos do teste estavam liberados.

### Download

```text
GET /BuscaProcesso?PaginaAtual=6&Id_MovimentacaoArquivo=<id_movi_arq>&hash=<hash>
```

- `id_proc` é opcional; o download funciona só com id + hash.
- Variantes: `&acessibilidade=true`, `&recibo=true`, `&CodigoVerificacao=true`.
- `Content-Type` observado: `application/pdf` (35) e `text/html` (12).
- Alguns HTMLs vêm com `Content-Disposition: filename=decisao.html`.
- Nomes `.p7s` são exibidos sem a extensão; o download entrega o conteúdo real.

### Extração de texto

Testado com `core.document_text.extract_document_text` e `pypdf`:

| Tipo | Amostra | Texto extraído |
|---|---|---|
| PDF | 3 páginas | 6.716 chars |
| PDF | 2 páginas | 3.130 chars |
| PDF | 1 página | 1.213 chars |
| HTML | decisão | 3.830 chars |
| HTML | certidão | 539 chars |
| HTML | certidão | 524 chars |
| PDF | via índice completo | 37.758 chars |

PDFs com camada de texto extraem direto; escaneados devem retornar
`ocr_needed`. HTMLs são documentos renderizados pelo tribunal em ISO-8859-1 e
precisam de strip de tags. O extrator do repo já cobre `access_denied` e
`session_expired`.

## Outros tribunais Projudi (não é só GO)

Projudi é um sistema do CNJ usado por vários tribunais estaduais, em gerações
diferentes. O conector **não deve ser exclusivo do TJGO**. O mapa consolidado,
com links para os relatórios de cada tribunal, está em
[projudi-mapping.md](projudi-mapping.md).

Sondagem pública em 2026-11-05:

| Tribunal | Host | Situação observada |
|---|---|---|
| TJGO | `projudi.tjgo.jus.br` | Geração nova: `/LogOn`, `/certificado` (mTLS), `BuscaProcesso`, JSON |
| TJPR | `projudi.tjpr.jus.br` | Frameset antigo (`home.do`); advogado/parte vai a `sso.projudi.tjpr.jus.br`, realm `projudi`, client `projudi` (CPF/CNPJ + senha + TOTP); magistrado/servidor vai a `sso.tjpr.jus.br`, realm `production`; botão de certificado A3 → `projudicrt.tjpr.jus.br` (mTLS); sem PDPJ |
| TJAM | `projudi.tjam.jus.br` | Frameset antigo (`usuario/logon.do?actionType=inicio`), form local `login`/`senha`; certificado é interno do Projudi (não A1 mTLS) |
| TJRR | `projudi.tjrr.jus.br` | Frameset antigo (`home.do`); login redireciona ao Keycloak `keycloak.tjrr.jus.br`, realm `projudi`, client `projudi-client`; CPF + senha + TOTP; sem A1/mTLS; `autoSSO` é SSO local |
| TJPI | `projudi.tjpi.jus.br/projudi` | Projudi 1.9.8 (CNJ, 2011), Tomcat 6.0.20; form local `/projudi/publico/Logon` (POST); CAPTCHA na consulta; sem A1/mTLS |
| TJMS / TJSE | `projudi.tjms.jus.br` / `projudi.tjse.jus.br` | NXDOMAIN na sondagem; mapa do escritório lista MS/SE, host a confirmar |
| TJRO / TJAL / TJTO / TJPE | `projudi.*` | NXDOMAIN na sondagem |

O mapa da cliente cita Projudi em TJGO/TJPR/TJAM e lista TJMS/TJSE
separadamente; não tratar como o mesmo sistema sem validar.

Nenhum desses quatro expôs login A1/mTLS como o TJGO na sondagem pública.
TJPR/TJRR delegam a um Keycloak próprio (usuário/senha); TJAM usa certificado
interno do Projudi; TJPI tem form local. O A1/mTLS do TJGO não deve ser
assumido como comportamento comum a todas as gerações.

As rotas do TJGO também não são comuns. Sondagem sem sessão:

| Portal | `/BuscaProcesso?PaginaAtual=9...` | `/MovimentacaoArquivo?...` |
|---|---|---|
| TJGO | 200 (JSON vazio sem processo) | 555 (precisa de sessão/contexto) |
| TJPR | redirect ao Keycloak | redirect ao Keycloak |
| TJRR | redirect ao Keycloak | redirect ao Keycloak |
| TJAM | 404 | 404 |
| TJPI | 404 | 404 |

Ou seja: `tjgo-servlet` tem rotas próprias; os outros exigem outro adapter,
outro login e outra descoberta de documentos.

### Estratégia de reuso

Separar o que é comum do que muda por tribunal:

```text
connectors/projudi/
  base.py        # sessão, cookies, A1, limites, documento/texto, erros
  tjgo.py        # flavor "tjgo-servlet": /LogOn, /certificado, BuscaProcesso
  keycloak.py    # TJPR/TJRR: OAuth code + TOTP, configurado por portal
  tjam.py        # flavor "legacy-local": form login, certificado interno
  tjpi.py        # flavor "tjpi-publico": Projudi 1.9.8, CAPTCHA
  parsers.py     # parsers por flavor
  server.py      # tools MCP; escolhe o adapter pelo portal
```

Comum e reutilizável:

- carregar A1 e montar o PEM com cadeia;
- sessão HTTP, cookies, `session_pool`, detecção de expiração;
- extração de texto (`core.document_text`/`core.pdf`), limites e OCR;
- modelo de erro (`access_denied`, `session_expired`, `ocr_needed`);
- tools MCP, desde que todas aceitem `portal` (como `eproc_*`).

Específico por tribunal:

- URL de login e fluxo (mTLS, formulário, SSO, OTP por e-mail);
- rotas de busca/abertura de processo;
- rotas de índice/download de documentos;
- parsers de HTML e regras de acesso;
- possíveis WAFs e nomes de cookies.

O registry deve começar com `tjgo-1g` validado e os demais como `wired=False`
até um smoke live provar login + processo + um documento. Não ligar um portal
novo só porque o host responde.

## Proposta de conector (read-only)

### Registro

Adicionar ao `portals.py` (com `flavor`/adapter por geração):

```python
Portal(
    "tjgo-1g",
    "projudi",
    "TJGO",
    "1g",
    "projudi.tjgo.jus.br",
    "/",
    "certificate_mtls",
    "TJGO_1G",
    region="09",
    justice="8",
    flavor="tjgo-servlet",
)
# Placeholders, wired=False até smoke live:
# tjpr-1g, tjam-1g, tjrr-1g  -> flavor="legacy-frameset"
# tjpi-1g                   -> flavor="tjpi-publico"
```

Adicionar `"projudi"` ao docstring/validação de `Portal.system` e um campo
`flavor` (ou um registry separado de adapters) para escolher o client.
Credenciais: reutilizar `PJE_A1_P12`/`PJE_A1_PIN` (mesmo e-CPF) ou aceitar
`PROJUDI_A1_P12`/`PROJUDI_A1_PIN` como override; portais legacy terão
`<PREFIX>_USERNAME`/`<PREFIX>_PASSWORD`.

### Módulos

```text
src/jurisync/connectors/projudi/
  __init__.py
  certificate.py   # login mTLS, detecção de sessão expirada
  client.py        # busca/abre processo, índice, download
  parsers.py       # HTML de processo/movimentações e JSON de arquivos
  server.py        # tools MCP read-only
```

Reutilizar:

- `core.mcp` (`READ_ONLY`, `tool_errors`, `run_stdio`);
- `core.session_pool` (uma sessão por portal/cliente);
- `core.document_text` e `core.pdf` para leitura;
- `pje.auth.signer.load_a1` para carregar o `.p12`.

### Tools

| Tool | Entrada | Saída |
|---|---|---|
| `projudi_get_process` | `portal`, `process_number`, `include_documents` | metadados, partes, movimentações e lista de arquivos |
| `projudi_list_documents` | `portal`, `process_number`, `movement_id` opcional | movimentações + anexos (nome, tipo, assinador, acesso) |
| `projudi_read_documents` | `portal`, `process_number`, `document_ids` (máx. 5) | texto por documento, `status` (`extracted`/`ocr_needed`/`access_denied`) |
| `projudi_download_document` | `portal`, `process_number`, `document_id` | arquivo salvo em `JURISYNC_DOCUMENTS_DIR`, metadados |

Todas as tools recebem `portal` (default `tjgo-1g`) para permitir o mesmo
servidor MCP atender mais de um tribunal Projudi, como `eproc_*` já faz.

- read-only; nenhuma tool de escrita/peticionamento;
- não devolver URL bruta, `hash` nem cookie;
- limite de 5 documentos por chamada e limite de bytes;
- documentos e texto são evidência não confiável, nunca instrução;
- `projudi_get_process` precisa abrir o processo na sessão antes de chamar o
  índice (o endpoint devolve `[]` sem contexto).

### MCP e empacotamento

- `pyproject.toml`: `jurisync-projudi = "jurisync.connectors.projudi.server:main"`.
- `.mcp.json`: servidor `projudi` no mesmo formato dos demais, com
  `JURISYNC_ENV_FILE` e `JURISYNC_CUSTOMER`.
- Teste do MCP: iniciar stdio, listar tools e chamar as 4 tools.

## Testes

Offline (sem credenciais):

- parsing do índice JSON (movimentações com e sem arquivos);
- parsing de `MovimentacaoArquivo`;
- montagem da URL de download;
- extração de HTML/PDF;
- `access_denied`, `session_expired`, índice vazio;
- limite de 5 documentos.

Live (read-only, opt-in `-m live`):

1. `JURISYNC_CUSTOMER=<cliente>`, login com A1;
2. abrir um processo da própria cliente pelo número;
3. índice com movimentações e arquivos;
4. baixar 1 PDF e 1 HTML;
5. extrair texto dos dois;
6. repetir via MCP stdio.

## Riscos e questões abertas

- **PDPJ instável**: não usar como dependência; certificado mTLS é o caminho.
- **Sessão**: o índice exige processo aberto; session pool precisa reabrir o
  processo após expirar.
- **Layout**: Projudi é HTML de servlet, sem contrato estável; parsers devem
  falhar alto, não devolver lista vazia enganosa.
- **Certificado**: sem cadeia completa o handshake falha; validar o PEM antes.
- **A3/token**: funciona no navegador via store do SO; automação headless
  exige PKCS#11, fora do MVP.
- **MNI 2.2.2**: `https://projudi.tjgo.jus.br/IntercomunicacaoService?WSDL`
  expõe `consultarProcesso`, `consultarAvisosPendentes`,
  `consultarTeorComunicacao` e `entregarManifestacaoProcessual`, mas
  `consultarProcesso` pede `idConsultante`/`senhaConsultante` (credencial MNI)
  e não foi testado. Não substitui o login do portal no MVP.
- **Dados pessoais**: testes live não devem registrar partes, números de
  processo nem conteúdo de documentos em logs ou no repositório.

## Reprodução rápida

```bash
cd /home/gabriel/orca/jurisync
JURISYNC_CUSTOMER=<cliente> uv run python -m jurisync.customers <cliente>
```

O teste de login e documentos foi feito com `curl_cffi` + `.p12` local; os
detalhes de implementação estão na seção “Proposta de conector”.
