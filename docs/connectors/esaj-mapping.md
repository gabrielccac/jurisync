# e-SAJ — exploração e mapa dos endpoints (TJSP e TJMS)

**Atualização:** o conector foi implementado. Os resultados posteriores à
exploração abaixo, incluindo a rota de PDF validada no TJMS, estão em
[esaj-validation.md](esaj-validation.md). As pendências deste mapa descrevem o
estado inicial da exploração.

Levantamento **read-only** em **2026-11-05** com o A1 + PIN da Ana. Login validado
nos dois tribunais; o objetivo é alimentar o futuro conector `src/jurisync/connectors/esaj/`.

Nenhum dado pessoal, número de processo, OAB ou conteúdo de documento foi registrado
neste documento.

## Resumo

| Item | TJSP | TJMS |
|---|---|---|
| Portal | `https://esaj.tjsp.jus.br` | `https://esaj.tjms.jus.br` |
| Login A1 | **validado** | **validado** |
| `usuarioLogado` | `true` | `true` |
| OABs na sessão | 1 (GO) | 2 (GO/SP) |
| Método | desafio assinado no CAS | desafio assinado no CAS |
| mTLS | não | não |
| Sessão | `CASTGC` + `JSESSIONID` | `CASTGC` + `JSESSIONID` |
| Painel | `/tarefas-adv` | `/tarefas-adv` |
| Consulta 1G | `cpopg` (SPA/legado) | `cpopg5` |
| Consulta 2G | `cposg` | `cposg5` |
| Documentos | `pastadigital` | `pastadigital` |

## Login

O e-SAJ não usa mTLS. O CAS entrega um `hashDesafio` SHA-256; o cliente assina
com o certificado e envia a assinatura no formulário.

```text
GET  /sajcas/login?service=...
  -> hashDesafio (SHA-256 base64)
  -> assina com A1: RSA PKCS#1 v1.5 + SHA-256 (Prehashed)
POST /sajcas/login  (formCertificado)
  -> signature=<assinatura base64>
  -> certificadoSelecionado=<certificado DER base64>
  -> execution, _eventId, token, lt
  -> 302 + CASTGC
```

Regra importante: **enviar somente os campos que o navegador envia**. Não incluir
checkbox desmarcado (`magistrado`) nem botões `submit`/`button`. Foi isso que fez o
primeiro teste do TJSP falhar; depois de remover esses campos, o A1 autenticou.

URLs de login usadas:

```text
TJSP: https://esaj.tjsp.jus.br/sajcas/login?service=https%3A%2F%2Fesaj.tjsp.jus.br%2Ftarefas-adv%2Fj_spring_cas_security_check
TJMS: https://esaj.tjms.jus.br/sajcas/login?service=https%3A%2F%2Fesaj.tjms.jus.br%2Ftarefas-adv%2Fj_spring_cas_security_check
```

## Sessão e APIs

```text
GET /esaj/api/auth/session       -> usuarioLogado, nmUsuario, oabs, servicosDisponiveis
GET /tarefas-adv/api/usuario     -> nome, cpfCnpj, oabs, perfil, email, telefone
GET /tarefas-adv/api/menu        -> menu HTML (não JSON)
GET /tarefas-adv/api/sistema     -> urls de login/logout/cadastro/perfil
GET /tarefas-adv/api/intimacoes  -> JSON (vazio na conta testada)
GET /tarefas-adv/api/audiencias  -> JSON (vazio na conta testada)
```

O `/tarefas-adv/api/menu` devolve HTML, não JSON. O `/esaj/api/menus` é a árvore
JSON do portal.

## Caixa postal

```text
GET /caixapostal/abrirCaixaPostal.do
GET /caixapostal/abrirMensagem.do?entity.netCaixaPostalPK.nuSeqmensagem=<id>
POST /caixapostal/apagarMensagens.do
GET /caixapostal/listaPaginada.do?pag=<n>
```

Status: 200 nos dois tribunais. A caixa postal lista mensagens de protocolo e
links para intimações, certidões e conferência de documentos. É uma boa fonte
para `esaj_list_messages`, mas **não** é a lista de documentos do processo.

## Intimações

```text
GET /intimacoesweb/abrirConsultaAtosNaoRecebidos.do
GET /intimacoesweb/abrirConsultaAtosRecebidos.do
```

Status observado: **403** ao abrir direto, mesmo com sessão. Ainda falta descobrir
o fluxo/parâmetros que o portal usa (provavelmente uma janela/contexto próprio).
É um bloqueio de mapeamento, não uma conclusão de que não existe.

## Pasta digital / documentos

Rotas de conferência (não são a lista de autos do processo):

```text
TJSP:
  /pastadigital/pg/abrirConferenciaDocumento.do
  /pastadigital/sg/abrirConferenciaDocumento.do
  /pastadigital/sgcr/abrirConferenciaDocumento.do
  /atendimento/abrirConferenciaDocOriginal.do

TJMS:
  /pastadigital/pg/abrirConferenciaDocumento.do
  /pastadigital/sgcr/abrirConferenciaDocumento.do
```

Essas telas pedem número do processo + código do documento. O acesso à lista de
documentos do processo deve vir da consulta processual (`cpopg`/`cpopg5`), que
ainda não foi mapeada com sessão autenticada por causa do CAPTCHA.

## Consulta processual

```text
TJSP:
  /cpopg/open.do  -> /cpopg/search.do   (1G; formulário GET)
  /cposg/open.do  -> /cposg/search.do   (2G)

TJMS:
  /cpopg5/open.do -> /cpopg5/search.do  (1G; formulário com reCAPTCHA)
  /cposg5/open.do -> /cposg5/search.do  (2G; sem captcha aparente)
```

Descoberta importante no TJSP: o formulário é **GET**, não POST. O POST em
`/cpopg/search.do` retorna **403**; o GET com os mesmos campos funciona e
devolve a lista de processos.

Busca por OAB validada no TJSP 1G:

```text
GET /cpopg/search.do
  ?conversationId=
  &cbPesquisa=NUMOAB
  &dadosConsulta.valorConsulta=<OAB>
  &dadosConsulta.tipoNuProcesso=UNIFICADO
  &cdForo=-1
```

Resultado: lista de processos; cada linha abre:

```text
GET /cpopg/show.do?processo.codigo=<codigo>&processo.foro=<foro>...
```

Detalhe do processo (TJSP 1G) retornou 200 com dados, partes e movimentações.
As movimentações são carregadas sob demanda:

```text
GET /cpopg/carregarMovimentacoesAjax.do?processo.codigo=<codigo>&cursor=
```

Cada movimentação com documento traz um link `#liberarAutoPorSenha` e um
`onclick` para:

```text
POST /cpopg/validarAberturaIntimacaoNaoRecebida.do
  cdProcesso=<codigo>
  seqMovimento=<sequencia>
Header: X-CSRF-Token
```

O POST devolveu **204**; depois o JS abre o fluxo de senha/assinatura do
processo. **A URL final do documento não foi capturada** — é o próximo passo
para leitura/download. O bloqueio atual é a senha do processo/assinatura, não a
inexistência da rota.

### Senha do processo

O modal do TJSP pede `senhaProcesso` (máx. 12 caracteres), com o texto:

> É necessário informar uma senha para acessar processo em segredo de justiça,
> bem como para acessar autos dos demais processos. Se for parte do processo e
> ainda não possua uma senha, solicite-a no cartório.

Não é o PIN do A1 nem a senha de login. É uma senha do processo, obtida no
cartório, ou o acesso direto quando o advogado está habilitado nos autos. O
formulário aparece no próprio `show.do` com `senhaProcesso`,
`idRecursoQueProvocouLiberacaoPorSenha`, `_csrf` e `btEnviarSenha`.

O conector deve tratar esse caso como `password_required`, sem tentar adivinhar
ou contornar a senha. Se o processo for de cliente habilitado, o link pode vir
liberado; se não, a senha é indispensável.

## CAPTCHA

O CAPTCHA do TJMS 1G e de outras telas públicas **não deve ser contornado**.
O conector deve detectar e devolver `captcha_required`, permitindo resolução
manual via navegador/takeover. Não usar serviço de resolução de CAPTCHA nem
qualquer bypass: é controle de segurança do tribunal.

No TJMS 2G, a busca por OAB não retornou processos para a conta testada. O 1G
segue com reCAPTCHA. Para o MVP, a consulta pública deve ser tratada como
`captcha_required`, nunca contornada.

## Tarefas do advogado (`/tarefas-adv`)

SPA React. Endpoints observados no bundle:

```text
/tarefas-adv/api/usuario
/tarefas-adv/api/menu
/tarefas-adv/api/sistema
/tarefas-adv/api/intimacoes
/tarefas-adv/api/audiencias
/tarefas-adv/api/processos?numerosProcesso=<numero>
/tarefas-adv/api/processos?cdsProcesso=<codigo>
```

`/api/processos` sem parâmetros deu 500; com um número de teste retornou lista
vazia. O formato exato do parâmetro ainda precisa ser confirmado no código do SPA.

## Proposta de conector

```text
src/jurisync/connectors/esaj/
  base.py       # CAS: hashDesafio, assinatura A1, sessão, limites, erros
  tjsp.py       # endpoints/particularidades do TJSP
  tjms.py       # endpoints/particularidades do TJMS
  parsers.py    # HTML da caixa postal, intimações, pasta digital e JSON de tarefas
  server.py     # tools MCP read-only
```

Registro sugerido:

```python
Portal("tjsp-esaj", "esaj", "TJSP", "1g", "esaj.tjsp.jus.br", "/", "certificate_challenge", "TJSP_ESAJ",
       region="26", justice="8", flavor="esaj-cas")
Portal("tjms-esaj", "esaj", "TJMS", "1g", "esaj.tjms.jus.br", "/", "certificate_challenge", "TJMS_ESAJ",
       region="12", justice="8", flavor="esaj-cas")
```

Tools:

| Tool | Função |
|---|---|
| `esaj_get_session` | Validar login, OABs e serviços disponíveis |
| `esaj_list_messages` | Caixa postal e mensagens |
| `esaj_list_intimacoes` | Atos recebidos/não recebidos (quando o fluxo for descoberto) |
| `esaj_list_tasks` | Tarefas/prazos via `/tarefas-adv/api` |
| `esaj_get_process` | Abrir processo por número |
| `esaj_list_documents` | Documentos do processo |
| `esaj_read_document` | Baixar e extrair texto |
| `esaj_download_document` | Salvar PDF/HTML |

Credenciais: `PJE_A1_P12` + `PJE_A1_PIN` (ou `ESAJ_A1_*`). Sem usuário/senha
para TJSP/TJMS enquanto o A1 funcionar.

## Segurança e limites

- somente leitura; nenhuma petição, assinatura ou alteração;
- não contornar CAPTCHA, WAF ou bloqueio geográfico;
- documentos e texto são evidência não confiável;
- limite de bytes e de documentos por chamada;
- não devolver URL bruta, cookie, hash ou token;
- não registrar dados de partes/processos nos logs.

## Pendências para o Claudio

1. Capturar a URL final do documento após `validarAberturaIntimacaoNaoRecebida.do`
   (o retorno 204 abre o fluxo de senha/assinatura; sem a senha do processo a URL
   não é exposta). Testar com um processo em que a Ana esteja vinculada ou com a
   senha do processo.
2. Confirmar o fluxo autenticado de `cpopg5` (TJMS 1G) apesar do CAPTCHA.
3. Descobrir os parâmetros de `intimacoesweb` que evitam o 403.
4. Confirmar `TarefasAdv /api/processos` (formato do número/código).
5. Implementar o conector e testar com o A1 da Ana nos dois tribunais.
