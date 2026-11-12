# Projudi TJPR — exploração e plano do conector

Levantamento **público e read-only** em **2026-11-05**, sem credenciais, sem
login, sem cadastro e sem submissão de formulário autenticado. Foram feitas
apenas requisições GET espaçadas, com timeout, a páginas públicas. Nenhum
número de processo, parte, nome ou conteúdo de documento foi registrado.

Este documento complementa `projudi-tjgo.md`: o TJGO é a referência de
arquitetura (core + adapter por flavor), mas **o TJPR não usa as mesmas rotas,
não usa JSON e não tem login A1/mTLS validado**.

## Resumo

| Capacidade | Situação em 2026-11-05 |
|---|---|
| Portal no ar | **Observado**: `PROJUDI v25.0.28` (release 25.0.28.68), JSP/WildFly, frameset |
| Login usuário/senha | **Observado**: CPF/CNPJ + senha no Keycloak externo |
| 2FA | **Observado**: TOTP de 6 dígitos (Google Authenticator); enforcement não validado |
| Login por certificado | **Observado**: botão “Certificado Digital (A3)” → `projudicrt` com mTLS; sucesso não validado |
| Login A1/mTLS | **Hipótese forte**: o endpoint TLS aceita cadeias ICP-Brasil; o portal anuncia só A3; validar |
| Login PDPJ | **Client existe** no SSO nacional, mas sem botão no portal e com `redirect_uri` não registrado |
| SSO institucional | **Observado**: magistrados/servidores usam `sso.tjpr.jus.br` realm `production`; não é o caminho do advogado externo |
| Consulta pública | **Observada**, mas **todas as buscas observadas têm reCAPTCHA Enterprise** |
| Abrir processo | **Não observado** sem credencial |
| Índice de movimentações | **Não observado** sem credencial |
| Documentos/download | **Não observado**; não há JSON público equivalente ao TJGO |
| API/MNI | **Não observado**; rotas internas são indistinguíveis de 404 por causa do filtro Keycloak |

## Correções em relação ao contexto inicial

O contexto recebido dizia “login redireciona para `sso.tjpr.jus.br`, realm
`production`, client `projudi`; usuário/senha, sem botão de certificado/PDPJ”.
A exploração mostrou três caminhos distintos:

1. **Advogados/partes** → `autenticacao.do` → **`sso.projudi.tjpr.jus.br`**,
   realm **`projudi`**, client **`projudi`**. É o Keycloak com CPF/CNPJ.
2. **Magistrados/servidores** → `tjpr/autenticacao.do` → `projudi2` →
   **`sso.tjpr.jus.br`**, realm **`production`**, client **`projudi`**. É o SSO
   da intranet, com campo “Informe seu usuário”.
3. **Certificado digital** → **existe botão** “Certificado Digital” no portal,
   apontando para `/projudi/token/autenticacaoCertificado.do` e redirecionando
   ao host **`projudicrt.tjpr.jus.br`**, que faz **mTLS de verdade**.

O botão de PDPJ **não existe**; há apenas um link para o marketplace
`https://www.jus.br/servicos/` no cabeçalho.

## Hosts e URLs

| Host | Papel | Observação |
|---|---|---|
| `projudi.tjpr.jus.br` | aplicação principal (externo) | `/projudi/` frameset; `home.do`; quase tudo atrás do Keycloak |
| `consulta.tjpr.jus.br` | consulta pública | `projudi_consulta`; redireciona de `projudi.tjpr.jus.br/projudi_consulta/` |
| `sso.projudi.tjpr.jus.br` | Keycloak do advogado/parte | realm `projudi`, client `projudi`, tema `projudi` |
| `sso.tjpr.jus.br` | Keycloak institucional | realm `production` (interno) e realm `projudi` (alias/legado) |
| `projudicrt.tjpr.jus.br` | ingress de certificado | pede certificado cliente no handshake TLS; CAs ICP-Brasil |
| `projudi2.tjpr.jus.br` | aplicação interna | mesmo app, pod `interno`; SSO institucional |
| `portal.tjpr.jus.br` | portal antigo/manuais | citado nos manuais; assinador via JNLP |
| `sso.cloud.pje.jus.br` | PDPJ nacional | client `projudi-tjpr` existe; redirect não registrado; sem botão |

O app roda em **WildFly** (comentário `PathServer: /opt/wildfly/...` nas
páginas), com `x-powered-by: JSP/2.3`, HTTP/2, HSTS e HTML em
`ISO-8859-1`. A versão exibida no rodapé é **PROJUDI v25.0.28**.

## Fluxo de login

### 1. Portal e frameset

```text
GET https://projudi.tjpr.jus.br/projudi/
  -> 200 text/html;charset=ISO-8859-1 (1.943 bytes)
  -> frameset: topFrame vazio + mainFrame home.do?r=<random>
GET https://projudi.tjpr.jus.br/projudi/home.do
  -> 200 (16.169 bytes)
  -> tela “Acesso ao Sistema” com três opções
```

A tela inicial oferece:

- **Magistrados, Servidores, Estagiários e demais usuários que possuem conta
  do TJPR** — “Use o mesmo login e senha da intranet do TJPR” (single sign-on).
- **Advogados, Partes, Membros do MP, Peritos e demais usuários externos** —
  “Use o CPF ou CNPJ e senha do Projudi”.
- **Certificado Digital** — “Acesso para todos os usuários que possuem
  certificado digital A3 ICP-Brasil. Exige a utilização da senha do cartão
  (PIN)”.

O rodapé da home informa `PROJUDI v25.0.28` / `release 25.0.28.68`.

### 2. Advogados/partes — Keycloak externo

```text
GET /projudi/autenticacao.do
  -> 302
     https://sso.projudi.tjpr.jus.br/auth/realms/projudi/protocol/openid-connect/auth
       ?response_type=code
       &client_id=projudi
       &redirect_uri=https%3A%2F%2Fprojudi.tjpr.jus.br%2Fprojudi%2Fautenticacao.do
       &state=<uuid>
       &login=true
       &scope=openid
  -> 200 “Entrar em Projudi”
```

O formulário Keycloak tem:

| Campo | Detalhe |
|---|---|
| `username` | label **CPF/CNPJ**; `pattern` aceita 11 dígitos ou 12 alfanuméricos + 2 dígitos; `maxlength=14`; aceita CNPJ alfanumérico |
| `password` | senha do Projudi |
| `rememberMe` | checkbox “Mantenha-me conectado” |
| `credentialId` | hidden, usado nos fluxos seguintes (OTP) |
| ação | `POST https://sso.projudi.tjpr.jus.br/auth/realms/projudi/login-actions/authenticate?...` com `session_code`, `execution`, `client_id`, `tab_id`, `client_data` |

Links/recursos observados:

- “Esqueceu sua senha?” → `/auth/realms/projudi/login-actions/reset-credentials`
  (pede CPF/CNPJ ou e-mail e envia instruções).
- **Não há** botão de certificado, gov.br, PDPJ ou IdP social na página do
  Keycloak.
- `kc_idp_hint=govbr` foi ignorado (voltou a mesma tela), então **gov.br não
  está exposto**.
- O realm tem `otp.js` e a página de OTP usa `#kc-otp-login-form` e `#otp`
  (6 dígitos). O site de softwares requeridos lista **Google Authenticator**
  (Android/iOS), confirmando **TOTP**, não OTP por e-mail.
- `GET /auth/realms/projudi/login-actions/registration?...` responde
  **400 “Cadastro não é permitido”**: **não há auto-registro no Keycloak**.

O `redirect_uri` é o próprio `autenticacao.do`; depois do login o Keycloak
devolve `?code=...&state=...` e o app troca o code no servidor
(provavelmente client confidencial), criando a sessão JSP. O cookie
`OAuth_Token_Request_State` é gravado pelo app antes do redirect e precisa
sobreviver ao retorno.

A rota legada `usuario/logon.do` continua no bundle e é usada para keepalive
(`?actionType=realimentarSessao`), mas **não é mais o formulário de login**:
sem sessão, ela redireciona ao SSO. O formulário de usuário/senha fica no
Keycloak.

### 3. Magistrados/servidores — SSO institucional

```text
GET /projudi/tjpr/autenticacao.do
  -> 302 https://projudi2.tjpr.jus.br/projudi/tjpr/autenticacao.do?actionType=login
  -> 302 https://sso.tjpr.jus.br/auth/realms/production/protocol/openid-connect/auth
       ?response_type=code&client_id=projudi
       &redirect_uri=https%3A%2F%2Fprojudi2.tjpr.jus.br%2Fprojudi%2Ftjpr%2Fautenticacao.do%3FactionType%3Dlogin
       &state=<uuid>&login=true&scope=openid
  -> 200 “Entrar em TJPR”, label “Informe seu usuário”
```

Esse caminho é o **SSO da intranet** e não serve para advogado externo. A
página do realm `production` não exibiu “Esqueceu sua senha?” (o usuário
interno provavelmente usa outro fluxo). O realm `projudi` também existe em
`sso.tjpr.jus.br`, mas o caminho externo atual usa `sso.projudi.tjpr.jus.br`.

**Importante:** qualquer rota `/projudi/*` não liberada explicitamente
redireciona para `sso.tjpr.jus.br` realm `production`, **inclusive URLs
inexistentes**. Testes com `/projudi/zzz-nao-existe-abc123.do` e
`/projudi/BuscaProcessoXYZ` retornaram o mesmo 302. Portanto **não é possível
descobrir rotas internas por status 200/302/404** sem sessão.

### 4. Certificado digital — `projudicrt` (mTLS)

```text
GET /projudi/token/autenticacaoCertificado.do?actionType=inicioComCertificado
  -> 302 https://projudicrt.tjpr.jus.br/projudi/token/autenticacaoCertificado.do
           ?actionType=inicioComCertificado
           &URL=https%3A%2F%2Fprojudi.tjpr.jus.br%2Fprojudi%2F
  -> handshake TLS em projudicrt pede certificado cliente
  -> sem certificado: 302
       https://projudi.tjpr.jus.br/projudi/token/autenticacaoCertificado.do
         ?actionType=erroInicioComCertificado&ERROA3=600
  -> 200 “Certificado não encontrado ou o navegador está com uma sessão inválida...”
```

O `openssl s_client` em `projudicrt.tjpr.jus.br:443` mostra:

- TLSv1.2, `ECDHE-RSA-AES128-GCM-SHA256`;
- `Client Certificate Types: RSA sign`;
- **`Acceptable client certificate CA names`** com dezenas de ACs ICP-Brasil
  (AC SOLUTI, AC Certisign, AC VALID, AC RFB, AC DIGITALSIGN, etc.).

Nem `projudi.tjpr.jus.br`, nem `consulta.tjpr.jus.br`, nem
`sso.projudi.tjpr.jus.br` pedem certificado no handshake; **só o host
`projudicrt` faz mTLS**.

Ou seja, **existe um endpoint mTLS real** (X.509 client auth, não um campo
de formulário), diferente do Keycloak. O portal anuncia “A3 ICP-Brasil” e
“senha do cartão (PIN)”, mas o TLS não distingue A1 de A3: ambos são
certificados ICP-Brasil com chave RSA. A dúvida é se o app valida a
**política A3** no nível aplicacional. Isso precisa ser testado com um A1
real; **não foi testado**.

O fluxo de sucesso (qual parâmetro volta para `projudi.tjpr.jus.br`, se o app
cria `JSESSIONID` ou um token de uso único) **não é observável sem
certificado**. O único código de erro observado é `ERROA3=600`.

O manual do advogado confirma que o **assinador** aceita certificado em
arquivo `.p12` (tipicamente **A1**) ou cartão/token (**A3**), com a senha da
chave privada. Isso é assinatura de documento, não login.

### 5. PDPJ

- Não há botão “Acessar com PDPJ” no portal; o cabeçalho tem um link para o
  marketplace `https://www.jus.br/servicos/`.
- No SSO nacional, o client **`projudi-tjpr` existe**: com redirect
  `https://projudi.tjpr.jus.br/projudi/LogOnSSO` o Keycloak responde
  **400 “Parâmetro inválido: redirect_uri”**; com client inexistente responde
  “Cliente não encontrado”.
- Testadas também variações óbvias (`autenticacao.do`, `loginSSO.do`): todas
  rejeitadas. O `redirect_uri` registrado é desconhecido.
- Conclusão: **não foi observado caminho funcional de PDPJ** no TJPR hoje.
  Pode ser retestado, mas não serve de caminho para o MVP.

### 6. Cadastro e recuperação de senha

- **Cadastro de advogado:** `GET /projudi/usuario/advogadoCertificado.do?actionType=carregar`
  → 200, campo **CPF** e botão “Recuperar Dados da OAB Nacional”. O portal
  anuncia o cadastro com **certificado A3 ICP-Brasil**; sem A3, a orientação é
  procurar a **OAB-PR** (telefone/e-mail).
- **Cadastro de parte:** `/projudi/token/cadastroParteCertificado.do?actionType=cadastrarParteCertificadoDigital`
  (A3 ICP-Brasil).
- **Cadastro de PJ:** mesmo fluxo de parte, com certificado A3 de PJ.
- **Keycloak:** auto-registro **desabilitado** (“Cadastro não é permitido”).
- **Recuperação de senha:** Keycloak `reset-credentials`, com CPF/CNPJ ou
  e-mail; envia instruções. Não automatizável sem acesso à caixa de e-mail.

## Credenciais necessárias

| Perfil | Como entra | Como se cadastra | 2FA | Certificado |
|---|---|---|---|---|
| Advogado | CPF + senha do Projudi | A3 ICP-Brasil em `advogadoCertificado.do`; sem A3, OAB-PR | TOTP (Google Authenticator) — enforcement a validar | A3 para login/cadastro; `.p12` (tipicamente A1) para assinar |
| Parte | CPF/CNPJ + senha | A3 em `cadastroParteCertificado.do` | idem | idem |
| Pessoa jurídica | CNPJ + senha | A3 de PJ | idem | idem |
| Magistrado/servidor | usuário da intranet + SSO TJPR | conta institucional | política interna | interno |
| MP/perito | CPF/CNPJ + senha | fluxo externo | idem | idem |

Observações:

- Não foi observado “certificado interno” nem CA própria; o TLS de
  `projudicrt` só aceita CAs ICP-Brasil.
- O certificado em arquivo `.p12` é documentado para **assinatura**, não para
  login. O login por certificado anunciado é A3.
- A senha do Projudi parece distinta da senha da intranet. O SSO institucional
  (`production`) não é o caminho do advogado externo.

## Sessão, cookies e o token `_tj`

### Cookies observados (pré-login)

| Cookie | Onde | Atributos | Papel |
|---|---|---|---|
| `projudi-route` | `projudi.tjpr.jus.br` | `Path=/projudi; Secure; HttpOnly` | sticky/roteamento entre pods |
| `JSESSIONID` | `projudi.tjpr.jus.br` | `Path=/projudi; Secure; HttpOnly`; sufixo `.projudi-externo-*`, `.projudi-interno-*` ou `.projudi-consulta-*` | sessão JSP |
| `OAuth_Token_Request_State` | `projudi.tjpr.jus.br` | `Version=1; Secure; HttpOnly` | state do OIDC, gravado antes do redirect |
| `INGRESSCOOKIE` | SSO | `Path=/auth/...; Secure; HttpOnly` | afinidade do ingress |
| `AUTH_SESSION_ID` | SSO | `Path=/auth/realms/projudi/; Secure; HttpOnly; SameSite=None` | sessão Keycloak |
| `KC_AUTH_SESSION_HASH` | SSO | `Max-Age=60; Secure; SameSite=None` | verificação de sessão |
| `KC_RESTART` | SSO | `Secure; HttpOnly; SameSite=None` | restauração do fluxo |
| `projudiContCookie` | app | JS, não HttpOnly | contador de sessões; irrelevante |

Depois do login, os cookies exatos **não foram observados**. É provável que o
SSO grave `KEYCLOAK_IDENTITY`/`KEYCLOAK_SESSION` no host do Keycloak e o app
grave apenas o `JSESSIONID`; isso precisa ser confirmado com credencial. Não
assumir que o cookie do Keycloak basta para chamar o app.

### O token `_tj` — o ponto mais importante

Praticamente todos os links e formulários internos carregam um parâmetro
`_tj=<48 hex>`:

```text
/projudi/informacoesAdvogado.do;jsessionid=<...>?_tj=<48 hex>
/projudi/processo/validacaoDocumentos.do?_tj=<48 hex>
```

Comportamento observado:

| Situação | Resposta |
|---|---|
| `_tj` válido, mesma sessão | 200, página normal |
| `_tj` de outra sessão (ou expirado) | 200, **“Conteúdo inacessível. O endereço que você tentou acessar não é válido para esta sessão.”** |
| Sem `_tj` / token expirado | 200, **“A sessao expirou”** (redireciona para `SessionExpired`); observado na consulta pública |
| Rota protegida sem sessão | 302 para `sso.tjpr.jus.br` realm `production` |

O `_tj` é **efêmero e aparentemente vinculado à sessão/contexto de navegação**
(vários links da mesma área compartilham o mesmo token; outros links usam
outro). Ele **não pode ser hardcoded nem reaproveitado entre sessões**.
Consequência direta para o adapter:

> **Nunca montar URL de `.do` na mão.** É preciso navegar como o browser:
> extrair o `href`/`action` exato (com `_tj` e `jsessionid`) da página atual e
> seguir esse link. Se a página for recarregada, os tokens mudam.

Isso é o oposto do TJGO, onde as rotas são estáveis e o `JSESSIONID` basta.

## Consulta pública

O link “Consulta Pública” da home aponta para
`http://projudi.tjpr.jus.br/projudi_consulta/`, que responde **302** para
**`https://consulta.tjpr.jus.br/projudi_consulta/`**. O app é o mesmo
`PROJUDI v25.0.28`, com frameset `cabecalho.jsp` + `consultaPublica.do`.

### Formulário

```text
POST /projudi_consulta/processo/consultaPublica.do?_tj=<token>
```

O HTML inicial declara `action="...?actionType=pesquisar"`, mas o JavaScript
`onSubmit()` troca a ação para `...?_tj=<token>` (o `actionType` desaparece;
o token parece carregar a ação). Campos observados:

| Campo | Valores |
|---|---|
| `temCaptcha` | `true` |
| `processoPageSize` | `20` |
| `processoPageNumber` | `1` |
| `processoSortColumn` | `p.numeroUnico` |
| `processoSortOrder` | `asc` |
| `opcaoConsultaPublica` | `1` = Primeira Instância, `2` = Segunda Instância |
| `flagNumeroUnico` | `true` = Número Único, `false` = Número Antigo |
| `numeroProcesso` | número CNJ ou antigo |
| `codTribunal` | `1` (TJPR, disabled) |
| `codComarca`, `codVara`, `tipoCompetencia`, `turma` | filtros de 1º grau |
| `nomeParte`, `cpfCnpj` | busca por parte |
| `loginAdvogado` (hidden), `nomeAdvogado` | busca por advogado |
| `oab`, `oabComplemento`, `oabUF` | busca por OAB |
| `g-recaptcha-response` | token do reCAPTCHA Enterprise |

### CAPTCHA

Todas as buscas públicas observadas usam **reCAPTCHA Enterprise**:

- site key: `6Lel5ngsAAAAAGd7_7JAws7FlFzamPK7-2kS8b8V`;
- ações observadas:
  - `actre_consulta_publica`
  - `actre_consulta_precedentes`
  - `actre_processos_aptos_julgamento`
  - `actre_processos_pronunciamentos_judiciais`
  - `actre_validacao_documentos`

Sem resolver o captcha (o que não é aceitável no conector), **não é possível
obter resultado de busca pública**. Portanto a consulta pública **não serve
como fallback automatizado** para o MVP.

O HTML da consulta também carrega `keycloak.js` e três hidden inputs:
`keycloakAuthServerUrl=https://sso.tjpr.jus.br/auth`,
`keycloakTjprRealm=production`, `keycloakClientId=projudi-ui`. Não há botão
de login que use isso; o client `projudi-ui` existe no realm `production`
(com o redirect testado, responde “Parâmetro inválido: redirect_uri”). É
provável que seja uma integração dormente/planejada, não um caminho atual.

### Rotas públicas observadas

| Rota | Status | Conteúdo |
|---|---|---|
| `/projudi_consulta/` | 200 | frameset |
| `/projudi_consulta/processo/consultaPublica.do?_tj=...` | 200 | formulário de busca |
| `/projudi_consulta/processo/consultaPublica.do?actionType=listarPjeProjudi` | 200 | tabela “Processos Importados do PJe” (≈7401 linhas, 683 KB, uma página só) |
| `/projudi_consulta/processo/consultaPublicaSessoes.do?actionType=pesquisar` | 200 | sessões do dia (sem captcha) |
| `/projudi_consulta/processo/consultaPublicaPrecedentes.do?actionType=iniciar` | 200 | formulário de precedentes (captcha) |
| `/projudi_consulta/processo/consultaPublicaProcessoAptoJulgamento.do?actionType=iniciar` | 200 | processos aptos a julgamento (captcha) |
| `/projudi_consulta/processo/consultaPublicaProcessosPronunciamentosJudiciais.do?actionType=iniciar` | 200 | processos aguardando análise (captcha) |
| `/projudi/processo/validacaoDocumentos.do?_tj=...` | 200 | validação por chave (`idDocumento`; captcha) |

A tabela do PJe tem apenas o mapeamento número PJe → número Projudi, sem
links e sem paginação. **Não armazenar** essa lista no conector; se for útil
para resolver numeração antiga, consultar sob demanda e descartar.

### AJAX

O app usa **ajaxtags/prototype**, não JSON moderno. O autocomplete de número
de processo responde **XML**:

```xml
<?xml version="1.0" encoding="ISO-8859-1" ?>
<ajax-response><response></response></ajax-response>
```

Uma consulta com prefixo inexistente devolveu exatamente esse XML vazio, sem
captcha. O endpoint é
`/projudi_consulta/processo/consultaPublica.do?_tj=<token>&numeroProcesso=...`.
Ele serve para sugerir números; **não usar para enumerar processos**.

Outros AJAX observados no HTML, todos no mesmo estilo XML/options:
`consultaPublica.do?actionType=ajaxListaComarcasAtivas`,
`consultaPublicaPrecedentes.do?actionType=ajaxBuscaSituacoesPrecedente` e
`informacoesAdvogado.do?actionType=ajaxUpdateCompetencias`.

## Processo, movimentações e documentos

### O que foi observado

- O app é o mesmo `PROJUDI v25.0.28` (visto na home pública), HTML/ISO-8859-1,
  sem endpoint JSON público equivalente ao `BuscaProcesso?PaginaAtual=9` do
  TJGO.
- Os manuais oficiais mostram a interface do advogado:
  - menu **“Buscas” → “Busca Processual Simples”**;
  - menu **“Ações 1º Grau” → “Cadastrar Nova Ação”** (7 passos);
  - na tela do processo, botão **“Peticionar”**;
  - assinatura com **applet Java / Assinador TJPR**, usando `.p12`
    (tipicamente A1) ou cartão/token (A3) + PIN.
- O bundle `js/projudi.js` (120 KB) contém rotas de apoio:
  `/usuario/logon.do?actionType=realimentarSessao`,
  `/processo/tipoDocumento.do?actionType=filtrarArvorePelaDescricao`,
  `/processo/classeProcessual.do?actionType=pesquisar`,
  `/processo/tipoMovimento.do?actionType=pesquisar`,
  `/processo/assunto.do?actionType=pesquisar`,
  `/processo/recursal/materia.do`,
  `/processo/fazenda/cda.do`,
  `/processo/criminal/delegaciaCelepar.do`,
  `/judiciario/especializacao2Grau.do`,
  `/administracao/relatorio.do`,
  `/ajaxUtils.do?actionType=ajaxHintClasseProcessual`.
  São rotas compiladas no bundle, mas **não são** as rotas de processo/
  documento e podem ser código morto.
- Não foi encontrada nenhuma referência a `autoSSO` no HTML/JS público. O
  único mecanismo de manutenção de sessão observado é
  `/projudi/usuario/logon.do?actionType=realimentarSessao` (sem sessão,
  redireciona ao Keycloak).

### O que não foi observado (exige credencial)

- Se as rotas estilo TJGO (`/projudi/BuscaProcesso`,
  `/projudi/MovimentacaoArquivo`) existem no TJPR. Elas responderam 302 para
  o SSO, mas **qualquer** rota inexistente responde igual; não há evidência.
- URL e parâmetros exatos da busca de processo.
- Se a busca aceita número CNJ direto e abre o processo, ou se passa por uma
  lista de resultados.
- Estrutura da página do processo: abas, tabela de movimentações, partes,
  documentos.
- Se os documentos ficam embutidos na linha da movimentação, em uma aba
  “Documentos” ou em uma árvore “Navegação de Arquivo”.
- Nomes/IDs de arquivo, `hash`, `Id_MovimentacaoArquivo`, `Id_Arquivo`.
- Rota de download e `Content-Type` entregue.
- Regra de acesso do advogado (processos em que está cadastrado) e quais
  documentos são públicos.
- Se há tela de seleção de perfil/área após o login; o bundle
  `js/postLogon/perfil.js` sugere um POST com `_cpfCNPJ`, `grupoID` e o
  parâmetro de login para escolher o perfil ativo (advogado/parte/assessorado).
- Se há paginação/limite no índice de movimentações.

### Hipóteses (não tratar como fato)

- O Projudi legado costuma ter rotas em `/projudi/processo/` com nomes como
  `consultaProcesso.do`, `movimentacao.do`, `arquivo.do` e parâmetros
  `Id_Processo`, `Id_Movimentacao`, `Id_Arquivo`. **Nada disso foi confirmado
  no TJPR.**
- Os documentos provavelmente estão pendurados em movimentações, como no
  TJGO, mas o TJPR pode usar uma árvore própria e IDs diferentes.
- O download provavelmente é um link opaco com `_tj`, não uma URL montável
  com `id` + `hash`. **Não assumir `Id_MovimentacaoArquivo` + `hash`.**
- O acesso público a documentos deve ser limitado (decisões/despachos), mas
  não foi possível confirmar por causa do captcha.

## Endpoints e JS

### Rotas públicas do app principal

| Rota | Status | Título/nota |
|---|---|---|
| `/projudi/` | 200 | frameset, 1.943 bytes |
| `/projudi/home.do` | 200 | “Acesso ao Sistema”, 16.169 bytes |
| `/projudi/autenticacao.do` | 302 | Keycloak `sso.projudi` realm `projudi` |
| `/projudi/tjpr/autenticacao.do` | 302 | `projudi2` → `sso.tjpr` realm `production` |
| `/projudi/token/autenticacaoCertificado.do?actionType=inicioComCertificado` | 302 | `projudicrt` mTLS |
| `/projudi/token/cadastroParteCertificado.do?actionType=cadastrarParteCertificadoDigital` | link | cadastro de parte com A3 |
| `/projudi/usuario/advogadoCertificado.do?actionType=carregar` | 200 | cadastro de advogado, campo CPF |
| `/projudi/usuario/logon.do` | 302 | catch-all → `sso.tjpr` production |
| `/projudi/BuscaProcesso`, `/projudi/MovimentacaoArquivo` | 302 | catch-all; mesmo tratamento de rota inexistente; **não provam existência** |
| `/projudi/processo/validacaoDocumentos.do?_tj=...` | 200 | validação por chave |
| `/projudi/informacoesAdvogado.do?_tj=...` | 200 | informações/manuais |
| `/projudi/softwaresRequeridos.jsp` | 200 | Java, Google Authenticator, Assinador |
| `/projudi/legislacao.jsp`, `/projudi/noticias.jsp`, `/projudi/indisponibilidades.jsp` | link | páginas estáticas |
| `/projudi/cabecalho-oid.jsp` | 200 | cabeçalho pós-login; link marketplace PDPJ |
| `/projudi/zzz-nao-existe-abc123.do` | 302 | **catch-all**; não serve para descobrir rotas |

### Keycloak

| Endpoint | Observação |
|---|---|
| `https://sso.projudi.tjpr.jus.br/auth/realms/projudi/.well-known/openid-configuration` | 200; issuer `.../realms/projudi`; `authorization_code`, `password`, `device_code`, CIBA; PKCE `plain`/`S256` |
| `https://sso.tjpr.jus.br/auth/realms/production/.well-known/openid-configuration` | 200; issuer `.../realms/production` |
| `https://sso.tjpr.jus.br/auth/realms/projudi/.well-known/openid-configuration` | 200; issuer `.../realms/projudi` (host alternativo) |
| `/auth/realms/projudi/protocol/openid-connect/auth` | 200, formulário CPF/CNPJ + senha |
| `/auth/realms/projudi/login-actions/reset-credentials` | 200, recuperação de senha |
| `/auth/realms/projudi/login-actions/registration` | 400 “Cadastro não é permitido” |
| `/auth/realms/production/protocol/openid-connect/auth` | 200, “Entrar em TJPR” / “Informe seu usuário” |

### Assinaturas de erro

| Sinal | Significado provável | Ação do adapter |
|---|---|---|
| 302 para `sso.*` | sem sessão / sessão expirada | refazer login |
| 200 “A sessao expirou” | sessão/token expirado | refazer login e reabrir processo |
| 200 “Conteúdo inacessível...” | `_tj` inválido para a sessão | voltar à página anterior e reextrair o link |
| 400 “Cadastro não é permitido” | auto-registro Keycloak desligado | não tentar cadastrar |
| 400 “Parâmetro inválido: redirect_uri” | client existe, redirect não registrado | não usar PDPJ |
| 400 “Cliente não encontrado” | client não existe | não usar |

## Diferenças vs TJGO

| Aspecto | TJGO | TJPR |
|---|---|---|
| Geração | Projudi “novo” (servlets, JSON) | Projudi legado JSP/WildFly, frameset, HTML |
| Login principal | A1/mTLS em `/certificado`, validado | Keycloak CPF/CNPJ + senha + TOTP |
| Certificado | A1/mTLS, sem TOTP | A3 anunciado; `projudicrt` mTLS com CAs ICP-Brasil; A1 a validar |
| SSO | PDPJ quebrado (`invalid redirect_uri`) | Keycloak próprio (`sso.projudi` / `sso.tjpr`); PDPJ sem botão |
| Token de navegação | não há; `JSESSIONID` basta | `_tj` por sessão/contexto em links e forms |
| Busca de processo | `POST /BuscaProcesso` com `PaginaAtual=2` | rota desconhecida; navegação por menu/link |
| Índice de documentos | JSON `BuscaProcesso?PaginaAtual=9&PassoBusca=5` | desconhecido; HTML, provavelmente por movimentação |
| Download | `BuscaProcesso?PaginaAtual=6&Id_MovimentacaoArquivo=&hash=` | desconhecido; link opaco com `_tj` |
| Formato de dados | JSON + HTML | HTML/XML (ajaxtags), sem JSON observado |
| Consulta pública | não foi o foco do teste TJGO; sem captcha no fluxo autenticado | reCAPTCHA Enterprise em toda busca pública |
| Encoding | ISO-8859-1 | ISO-8859-1 |
| Sessão | `JSESSIONID`, `WIDPROP`, `dtCookienncenbbl` | `JSESSIONID`, `projudi-route`, cookies Keycloak |
| Erro de sessão | redirect `/LogOn?PaginaAtual=-200` | 302 Keycloak / “A sessao expirou” / “Conteúdo inacessível” |
| Automação | HTTP + A1 | browser para Keycloak/TOTP + HTTP com `_tj` (ou browser completo) |

## Desenho do adapter

### Flavor e registro

O TJPR não é `tjgo-servlet` nem o `legacy-frameset` puro (TJAM/TJRR): é
**legacy + Keycloak + token `_tj`**. O registry atual já tem:

```python
Portal(
    "tjpr-projudi-1g",
    "projudi",
    "TJPR",
    "1g",
    "projudi.tjpr.jus.br",
    "/projudi/",
    "password",
    "TJPR_PROJUDI_1G",
    region="16",
    justice="8",
    wired=False,
    guess_by_number=False,
    flavor="legacy-frameset",
)
```

Proposta: **manter a chave `tjpr-projudi-1g`** e ajustar o flavor/auth, sem
duplicar o portal:

```python
Portal(
    "tjpr-projudi-1g",
    "projudi",
    "TJPR",
    "1g",
    "projudi.tjpr.jus.br",
    "/projudi/",
    "password_totp",
    "TJPR_PROJUDI_1G",
    region="16",
    justice="8",
    wired=False,
    guess_by_number=False,
    flavor="legacy-keycloak",  # ou legacy-frameset + config de SSO/_tj
)
# 2º grau usa o mesmo host/app (opcaoConsultaPublica=2); não criar sessão separada
# sem necessidade. Um portal "tjpr" com parâmetro degree é preferível a duplicar.
```

`password_totp` é a descrição mais fiel do login externo; se o registry
preferir separar “login por browser” de “login por HTTP”, `browser_totp`
também serve. O importante é não registrar `password` puro: há TOTP no fluxo.

Credenciais (prefixo do portal existente):

```text
TJPR_PROJUDI_1G_USERNAME=...       # CPF/CNPJ só dígitos
TJPR_PROJUDI_1G_PASSWORD=...
TJPR_PROJUDI_1G_2FA_DATA=...       # otpauth://, export do Google Authenticator ou base32
TJPR_PROJUDI_1G_2FA_QR=...         # alternativa: caminho da imagem do QR
TJPR_PROJUDI_1G_A1_P12=...         # opcional, se o mTLS com A1 for validado
TJPR_PROJUDI_1G_A1_PIN=...
# fallback: reutilizar PJE_A1_P12/PJE_A1_PIN se for o mesmo e-CPF
```

Reutilizar `core.totp` (`parse_accounts`, `totp`, `next_code`) e o precedente
de `connectors/eproc/auth.py`, que já faz login Keycloak com SeleniumBase,
preenche `#username`/`#password`/`#kc-login`/`#otp` e copia os cookies para
uma sessão `curl_cffi`. Os seletores do Keycloak do TJPR são os mesmos.
Reutilizar também `core.session_pool` para uma sessão por cliente/portal.

### Módulos sugeridos

```text
src/jurisync/connectors/projudi/
  base.py             # sessão, cookies, erros, texto/PDF, limites (comum)
  tjgo.py             # flavor tjgo-servlet (já validado)
  legacy_keycloak.py  # login Keycloak/TOTP + navegação com _tj
  tjpr.py             # config/parsers específicos do TJPR
  parsers_tjpr.py     # HTML de processo/movimentações/documentos + XML ajax
  server.py           # tools MCP; escolhe o adapter pelo portal
```

Se o login Keycloak for extraído para `core/keycloak.py`, ele serve para
TJPR, TJRR e futuros portais; não duplicar a lógica de OTP.

### Login

Ordem de preferência:

1. **A1/mTLS em `projudicrt`** — se validado, é o caminho mais simples:
   carregar `.p12` + cadeia, abrir
   `/projudi/token/autenticacaoCertificado.do?actionType=inicioComCertificado`,
   seguir o redirect e capturar a sessão. Sem senha e sem TOTP.
2. **Keycloak + senha + TOTP** — via SeleniumBase:
   - abrir `https://projudi.tjpr.jus.br/projudi/autenticacao.do`;
   - preencher `#username` (CPF/CNPJ) e `#password`;
   - clicar `#kc-login`;
   - se aparecer `#otp`, preencher o TOTP de `core.totp`; se o código for
     rejeitado por replay, esperar a próxima janela e usar `next_code`;
   - aguardar a URL pós-login (`home.do` ou área do advogado);
   - copiar o cookie jar completo para `curl_cffi`.
3. **SSO institucional** — não suportado; exige conta da intranet.
4. **Consulta pública** — não suportada para automação por causa do captcha.

### Navegação e dados

O adapter deve ser um **crawler de links**, não um cliente REST:

```text
login -> home.do
  -> extrair links com _tj
  -> seguir “Buscas” / “Busca Processual Simples”
  -> preencher form com número CNJ (action + _tj exatos)
  -> se houver lista, escolher o número exato
  -> abrir processo
  -> parsear cabeçalho, partes, movimentações
  -> para cada movimentação, parsear documentos/links
  -> download: GET no href exato (com _tj), sem montar URL
```

Regras:

- Guardar `_tj` e `jsessionid` **apenas em memória**, nunca em log.
- Links vêm com `;jsessionid=<...>` no path, além do cookie. Usar o href
  exato (com o mesmo `jsessionid`) ou remover o path param de forma
  consistente; nunca misturar o cookie de uma sessão com o `jsessionid` de
  outra.
- Se a página mudar, reextrair tokens; preferir o href exato em vez de
  reaproveitar um token de outra página.
- Se vier “Conteúdo inacessível”, voltar um passo e refazer a navegação.
- Se vier “A sessao expirou” ou 302 para o SSO, refazer login e reabrir o
  processo (o contexto de processo provavelmente se perde).
- Não paralelizar navegação na mesma sessão: o `_tj` e o contexto de processo
  são stateful.
- Limitar a 1 sessão por cliente/portal e a 1 requisição por vez, com
  intervalo e backoff.

### Modelo de documento

- `document_id` exposto ao MCP deve ser **opaco** (hash/índice interno), nunca
  o `_tj`, o `jsessionid` ou a URL bruta.
- O adapter mantém um mapa `document_id -> href` na sessão.
- Download devolve bytes + `Content-Type` + nome sugerido; a extração de
  texto reutiliza `core.document_text`/`core.pdf`.
- HTML do TJPR é ISO-8859-1; decodificar antes de strip de tags.
- PDFs escaneados → `ocr_needed`.

### Tools MCP

| Tool | Entrada | Saída |
|---|---|---|
| `projudi_get_process` | `portal`, `process_number`, `include_documents` | metadados, partes, movimentações e lista de documentos |
| `projudi_list_documents` | `portal`, `process_number`, `movement_id` opcional | movimentações + documentos (tipo, data, nome, acesso) |
| `projudi_read_documents` | `portal`, `process_number`, `document_ids` (máx. 5) | texto por documento + `status` (`extracted`/`ocr_needed`/`access_denied`) |
| `projudi_download_document` | `portal`, `process_number`, `document_id` | arquivo em `JURISYNC_DOCUMENTS_DIR` + metadados |

Todas read-only; nenhuma tool de peticionamento/assinatura. Todas aceitam
`portal` para o mesmo servidor atender TJGO e TJPR.

## Riscos

1. **`_tj` é frágil por natureza**: token por sessão/contexto, gerado no HTML.
   Qualquer mudança de layout ou de fluxo quebra o crawler. Parsers devem
   falhar alto, não devolver lista vazia.
2. **Keycloak + TOTP é stateful e sensível a replay**: logins frequentes podem
   esgotar a janela de 30 s, gerar alerta ou bloqueio. Serializar logins e
   cachear sessão.
3. **A1 pode não ser aceito no login**: o TLS aceita ICP-Brasil, mas o app
   pode exigir política A3. O caminho A1 é hipótese até teste real.
4. **A3/token não é automatizável** sem PKCS#11/CSP do fabricante; o A1 é o
   caminho mais plausível para mTLS headless.
5. **reCAPTCHA Enterprise** inviabiliza a consulta pública como fallback.
   Não tentar resolver captcha no conector.
6. **Sem JSON/API**: todo o dado é HTML/XML de servlet; o custo de parsing e
   a chance de regressão são altos.
7. **Sticky routing**: `projudi-route` e o sufixo do `JSESSIONID` importam;
   descartar cookies ou trocar de pod perde a sessão.
8. **Sessão expira e o processo se perde**: o adapter precisa reabrir o
   processo após cada re-login.
9. **LGPD**: processos, partes e documentos são dados sensíveis. Não logar
   números, nomes, conteúdo ou URLs com `_tj`.
10. **Termos de uso/rate limit**: o portal pode bloquear automação agressiva.
    Uma sessão, intervalos, backoff e limite de documentos por chamada.
11. **2FA pode ser obrigatório**: se o TOTP for exigido e o cliente não
    fornecer o segredo, não há automação possível sem intervenção humana.
12. **PDPJ pode mudar**: o client existe; se o TJPR registrar o redirect
    correto, o caminho pode reaproveitar o login PDPJ no futuro.

## Perguntas abertas

1. Qual a URL exata da área logada após o Keycloak (`home.do`,
   `usuario/home.do`, outra)?
2. Há seleção de perfil/área após o login? Quais campos (`_cpfCNPJ`,
   `grupoID`, `login`) e qual o form/action?
3. A busca de processo aceita número CNJ direto? Qual o form/action e os
   campos?
4. O processo abre por `Id_Processo`? Há lista intermediária?
5. Como os documentos aparecem: inline na movimentação, aba ou árvore?
6. Qual o padrão de URL/IDs de documento e download? Existe `hash`?
7. O download exige `_tj` novo por arquivo? É one-time?
8. Qual o tempo de vida da sessão e do `_tj`? `rememberMe` dura quanto?
9. O TOTP é obrigatório para todos os advogados ou opt-in?
10. O A1 é aceito no login por `projudicrt` ou só A3?
11. O certificado em `.p12` é aceito para assinar (o manual mostra) e o
    assinador roda headless?
12. A consulta pública mostra documentos? Quais?
13. Existe MNI/API atrás do login? O WSDL está protegido?
14. O 2º grau usa o mesmo app/sessão ou um fluxo separado?
15. Há política de bloqueio por tentativas de senha/OTP?
16. O `projudi-ui` (client no realm `production`) é usado por algum login
    futuro da consulta pública?

## Evidências

Requisições GET públicas, sem credenciais. Títulos e status apenas; nenhum
número de processo, parte ou conteúdo de documento. Não foi observado
bloqueio/WAF nas requisições espaçadas; a única barreira observada foi o
reCAPTCHA nas buscas públicas.

| # | URL | Status | Título/nota |
|---|---|---|---|
| 1 | `https://projudi.tjpr.jus.br/projudi/` | 200 | frameset; `PROJUDI v25.0.28`; 1.943 bytes |
| 2 | `https://projudi.tjpr.jus.br/projudi/home.do` | 200 | “Acesso ao Sistema”; 16.169 bytes |
| 3 | `https://projudi.tjpr.jus.br/projudi/autenticacao.do` | 302 | Keycloak `sso.projudi.tjpr.jus.br`, realm `projudi`, client `projudi` |
| 4 | `https://sso.projudi.tjpr.jus.br/auth/realms/projudi/protocol/openid-connect/auth?...` | 200 | “Entrar em Projudi”; form CPF/CNPJ + senha |
| 5 | `https://sso.projudi.tjpr.jus.br/auth/realms/projudi/.well-known/openid-configuration` | 200 | issuer `.../realms/projudi`; 6.886 bytes |
| 6 | `https://projudi.tjpr.jus.br/projudi/tjpr/autenticacao.do` | 302 | `projudi2` → `sso.tjpr.jus.br` realm `production` |
| 7 | `https://sso.tjpr.jus.br/auth/realms/production/protocol/openid-connect/auth?...` | 200 | “Entrar em TJPR”; label “Informe seu usuário” |
| 8 | `https://projudi.tjpr.jus.br/projudi/token/autenticacaoCertificado.do?actionType=inicioComCertificado` | 302 | `projudicrt` mTLS |
| 9 | `https://projudicrt.tjpr.jus.br/projudi/token/autenticacaoCertificado.do?...` (sem cert) | 302 | volta para `erroInicioComCertificado&ERROA3=600` |
| 10 | `https://projudi.tjpr.jus.br/projudi/token/autenticacaoCertificado.do?actionType=erroInicioComCertificado&ERROA3=600` | 200 | “Certificado não encontrado...” |
| 11 | `openssl s_client -connect projudicrt.tjpr.jus.br:443` | — | pede cert cliente; CAs ICP-Brasil; TLSv1.2; RSA sign |
| 12 | `https://projudi.tjpr.jus.br/projudi/usuario/advogadoCertificado.do?actionType=carregar` | 200 | “Cadastro de Advogado”; campo CPF; 20.963 bytes |
| 13 | `https://projudi.tjpr.jus.br/projudi/softwaresRequeridos.jsp` | 200 | Java, Google Authenticator, Assinador TJPR |
| 14 | `https://projudi.tjpr.jus.br/projudi/informacoesAdvogado.do?_tj=...` | 200 | manuais e FAQ; 28.045 bytes |
| 15 | `https://projudi.tjpr.jus.br/projudi/informacoesAdvogado.do` (sem `_tj`) | 302 → 200 | Keycloak `sso.tjpr` production (“Entrar em TJPR”) |
| 16 | `https://projudi.tjpr.jus.br/projudi/zzz-nao-existe-abc123.do` | 302 | catch-all; não revela se a rota existe |
| 17 | `https://projudi.tjpr.jus.br/projudi_consulta/` | 302 | redireciona para `consulta.tjpr.jus.br` |
| 18 | `https://consulta.tjpr.jus.br/projudi_consulta/` | 200 | frameset; `cabecalho.jsp` + `consultaPublica.do` |
| 19 | `https://consulta.tjpr.jus.br/projudi_consulta/processo/consultaPublica.do?_tj=...` | 200 | formulário de busca; 43.895 bytes |
| 20 | `https://consulta.tjpr.jus.br/projudi_consulta/processo/consultaPublica.do` (sem `_tj`) | 200 | “A sessao expirou”; 564 bytes |
| 21 | `.../consultaPublica.do?actionType=listarPjeProjudi` | 200 | tabela PJe; 683.925 bytes; ≈7401 linhas |
| 22 | `.../consultaPublicaSessoes.do?actionType=pesquisar` | 200 | sessões do dia; 27.128 bytes |
| 23 | `.../consultaPublicaPrecedentes.do?actionType=iniciar` | 200 | formulário; captcha; 20.853 bytes |
| 24 | `.../consultaPublicaProcessoAptoJulgamento.do?actionType=iniciar` | 200 | formulário; captcha; 26.529 bytes |
| 25 | `.../consultaPublicaProcessosPronunciamentosJudiciais.do?actionType=iniciar` | 200 | formulário; captcha; 25.104 bytes |
| 26 | `https://projudi.tjpr.jus.br/projudi/processo/validacaoDocumentos.do?_tj=...` | 200 | campo `idDocumento`; captcha; 17.705 bytes |
| 27 | `https://sso.projudi.tjpr.jus.br/auth/realms/projudi/login-actions/registration?...` | 400 | “Cadastro não é permitido” |
| 28 | `https://sso.projudi.tjpr.jus.br/auth/realms/projudi/login-actions/reset-credentials?...` | 200 | “Esqueceu sua senha?”; CPF/CNPJ ou e-mail |
| 29 | `https://sso.cloud.pje.jus.br/auth/realms/pje/...client_id=projudi-tjpr...` | 400 | “Parâmetro inválido: redirect_uri” → client existe |
| 30 | `https://sso.cloud.pje.jus.br/auth/realms/pje/...client_id=projudi...` | 400 | “Cliente não encontrado” |
| 31 | `https://sso.projudi.tjpr.jus.br/auth/realms/projudi/...&kc_idp_hint=govbr` | 200 | mesma tela; gov.br não exposto |

## Reprodução rápida (sem credenciais)

```bash
# Portal e frameset
curl -sSI https://projudi.tjpr.jus.br/projudi/
curl -sS  https://projudi.tjpr.jus.br/projudi/home.do | iconv -f ISO-8859-1 -t UTF-8

# Redirecionamento para o Keycloak externo (sem seguir)
curl -sS -D- -o /dev/null https://projudi.tjpr.jus.br/projudi/autenticacao.do

# Metadados do realm
curl -sS https://sso.projudi.tjpr.jus.br/auth/realms/projudi/.well-known/openid-configuration | jq .

# mTLS do certificado (não envia certificado)
openssl s_client -connect projudicrt.tjpr.jus.br:443 \
  -servername projudicrt.tjpr.jus.br </dev/null 2>&1 | \
  sed -n '/Acceptable client certificate CA names/,/Client Certificate Types/p'

# Consulta pública (precisa do _tj extraído do frameset)
curl -sS -L https://consulta.tjpr.jus.br/projudi_consulta/ | \
  iconv -f ISO-8859-1 -t UTF-8 | grep -o 'consultaPublica.do[^"]*'
```

## O que falta validar com credenciais reais

Checklist mínimo para ligar `wired=True`:

1. Login Keycloak com CPF/CNPJ + senha + TOTP; registrar URL/título da área
   logada e cookies pós-login (sem logar valores sensíveis).
2. Confirmar se o TOTP é exigido para o usuário de teste e qual o tempo de
   vida da sessão/`rememberMe`.
3. Abrir “Buscas → Busca Processual Simples”; registrar action, campos e
   `_tj`; buscar um processo conhecido pelo número CNJ.
4. Verificar se o resultado abre direto ou passa por lista; registrar a URL
   do processo (com `_tj`) e a estrutura da página.
5. Mapear movimentações: colunas, ordenação, paginação, “Eventos”.
6. Mapear documentos: onde aparecem, quantos por movimentação, nomes/tipos,
   IDs internos e regra de acesso.
7. Baixar 1 PDF e 1 HTML; registrar `Content-Type`,
   `Content-Disposition` e se o `_tj` é de uso único.
8. Testar expiração: limpar cookie/aguardar; confirmar “A sessao expirou” e
   o fluxo de re-login + reabertura do processo.
9. Testar acesso a processo em que o advogado **não** está cadastrado;
   confirmar `access_denied`.
10. Testar o 2º grau (`opcaoConsultaPublica=2` / instância) e verificar se
    usa a mesma sessão.
11. Testar A1/mTLS em `projudicrt` com um `.p12` real e cadeia completa;
    confirmar se o app aceita A1 ou só A3.
12. Verificar se existe MNI/WSDL atrás do login e se ele aceita credencial
    própria; não substitui o login do portal no MVP.
13. Medir rate limit/bloqueio com poucas requisições espaçadas; definir
    orçamento de chamadas.
14. Rodar o MCP stdio com as 4 tools e conferir que nenhum `_tj`, cookie,
    senha ou número de processo aparece em log/erro.
