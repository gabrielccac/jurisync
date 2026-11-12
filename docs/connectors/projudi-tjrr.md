# Projudi TJRR — exploração pública e plano do adapter

Levantamento **read-only** em **2026-11-05**, sem credenciais, sem cadastro e
sem enviar formulários de autenticação. Cobre o portal
`https://projudi.tjrr.jus.br/projudi/`, o Keycloak
`https://keycloak.tjrr.jus.br` e a consulta pública
`https://consultaprojudi.tjrr.jus.br`. Serve de base para o futuro conector
`projudi`, complementando `projudi-tjgo.md` e `projudi-tjpr.md`.

Nenhum número de processo, parte, OAB, CPF, cookie ou conteúdo de documento
foi registrado neste relatório. Onde havia risco de dado pessoal, a coleta foi
interrompida e o comportamento foi descrito apenas pela estrutura da página.

## Resumo

| Capacidade | Situação |
|---|---|
| Login por CPF + senha no Keycloak | **Observado** (formulário público; não autenticado) |
| MFA/TOTP obrigatório | **Documentado pelo tribunal**; etapa pós-senha não observada sem credencial |
| Login por certificado A1/mTLS no TJRR | **Não utilizável** no portal atual; rota legada aponta para o TJPR e falha |
| Login PDPJ | **Não observado**; `autoSSO` é SSO local, não PDPJ |
| `autoSSO=true` | **Observado**; apenas redireciona para `tjpr/autenticacao.do` |
| Frameset `home.do` | **Observado** |
| Sessão por cookie | **Observado** (pré-login) |
| Consulta pública por número/OAB | **Formulário e API observados** na SPA; busca não executada por causa do captcha |
| Movimentações e documentos públicos | **Endpoints e modelo observados** na SPA; acesso não executado por causa do captcha |
| Abrir processo e ler documentos na sessão autenticada | **Desconhecido**; exige credencial |
| Credenciamento de advogado | **Manual**, via OAB-RR/TJRR; não há auto-registro |
| Bloqueio geográfico | **Documentado**; IP no exterior exige liberação excepcional |

## Hosts e URLs

| Host | Papel | Observação |
|---|---|---|
| `projudi.tjrr.jus.br` | Aplicação Projudi | nginx, HTTP/2, HTML `ISO-8859-1`, release `v5.12.33.1`; `/projudi/` é frameset |
| `keycloak.tjrr.jus.br` | Keycloak do TJRR | realm `projudi`, client `projudi-client`; caminho sem `/auth` (provável Keycloak 17+) |
| `consultaprojudi.tjrr.jus.br` | Consulta pública | SPA React/Vite + API Spring Boot `/consilium-api` |
| `www.tjrr.jus.br` | Portal institucional | aponta para a SPA e para a página de acesso automatizado |
| `transparencia.tjrr.jus.br` | Dados abertos | exportações/HTML e referência ao DataJud |
| `tre2.tjrr.jus.br` | Homologação | aparece indexado com stack traces; **não usar** como fonte nem para testes |

O HTTP em `projudi.tjrr.jus.br` responde `301` para HTTPS. O TLS dos três hosts
principais **não pede certificado de cliente** (`No client certificate CA names
sent`); TLS 1.3.

## Fluxo de login

### 1. Portal e frameset

```text
GET https://projudi.tjrr.jus.br/projudi/
  -> 200 text/html;charset=ISO-8859-1
  -> Set-Cookie: JSESSIONID=...; Path=/projudi; Secure; HttpOnly
  -> frameset: topFrame vazio + mainFrame home.do?r=<random>
GET /projudi/home.do
  -> 200, título "Projudi - Processo Eletrônico do Judiciário de Roraima"
  -> rodapé "release v5.12.33.1"
```

O frameset é montado por JavaScript (`document.writeln`), com
`topFrame` e `mainFrame`. O `home.do` carrega o cabeçalho em
`/projudi/cabecalho-oid.jsp` e exibe a tela “Acesso ao Sistema” com **uma única
opção visível: “Acesso via SSO”**. Não há formulário local de usuário/senha.

### 2. Redirecionamento para o Keycloak

```text
GET /projudi/tjpr/autenticacao.do
  -> 302
     https://keycloak.tjrr.jus.br/realms/projudi/protocol/openid-connect/auth
       ?response_type=code
       &client_id=projudi-client
       &redirect_uri=https%3A%2F%2Fprojudi.tjrr.jus.br%2Fprojudi%2Ftjpr%2Fautenticacao.do
       &state=<uuid>
       &login=true
       &scope=openid
  -> Set-Cookie: OAuth_Token_Request_State=<uuid>; Version=1; HttpOnly
```

O `redirect_uri` é o próprio `autenticacao.do`. O `state` é um UUID e o cookie
`OAuth_Token_Request_State` guarda o mesmo valor antes do redirect.

Rotas legadas equivalentes, todas observadas com `302` para o mesmo Keycloak:

```text
GET /projudi/usuario/logon.do?actionType=inicio
GET /projudi/logon.do
GET /projudi/LogOn
```

A rota canônica usada pela home é `tjpr/autenticacao.do`; `usuario/logon.do`
continua funcionando como entrada e como keepalive de sessão.

### 3. Formulário Keycloak

A página responde `200` com título **“Entrar em PROJUDI TJRR”** e tema próprio
“Autentikey”. Campos observados:

| Campo | Detalhe |
|---|---|
| `username` | label **CPF**; `type=text`; autocomplete off |
| `password` | label **Senha**; `type=password` |
| `rememberMe` | checkbox “Mantenha-me conectado” |
| `credentialId` | hidden, vazio na primeira etapa; usado nas etapas seguintes |
| `login` | botão “Entrar” |

A ação do form é absoluta e inclui os parâmetros de fluxo:

```text
POST https://keycloak.tjrr.jus.br/realms/projudi/login-actions/authenticate
  ?session_code=<...>&execution=<...>&client_id=projudi-client&tab_id=<...>
```

Não há CSRF token visível, captcha, botão de gov.br, botão de certificado,
botão de PDPJ nem link de auto-registro. Há apenas “Esqueceu sua senha?”.

### 4. Pós-login — desconhecido

O Keycloak devolve `?code=...&state=...` para
`/projudi/tjpr/autenticacao.do`. O backend do Projudi troca o code (client
provável confidencial), cria a sessão JSP e redireciona para a área interna.
**A rota exata do redirect pós-callback, os cookies gravados e o marcador de
“logado” não foram observados** sem credencial.

Um callback sem `code`, com `error=access_denied`, responde **400** com a página
“Requisição inválida” do Projudi; não há tratamento amigável de cancelamento.

### 5. Certificado digital e mTLS

O portal **não oferece** login por certificado na tela inicial. Existe uma rota
legada:

```text
GET /projudi/token/autenticacaoCertificado.do?actionType=inicioComCertificado
  -> 302 https://projudicrt.tjpr.jus.br/projudi/token/autenticacaoCertificado.do?_tj=<token>
  -> sem certificado: 302 https://projudicrt.tjpr.jus.br/projudi/erros/erro500Crypto.jsp
  -> 200 "Conteúdo inacessível. O endereço que você tentou acessar não é válido para esta sessão."
```

Ou seja: a rota de certificado do TJRR aponta para o **serviço de certificado do
TJPR** (`projudicrt.tjpr.jus.br`), com um `_tj` gerado no TJRR. O TJPR pede
certificado de cliente (TLS 1.2, `RSA sign`; a lista de CAs não foi
inspecionada), mas rejeita a sessão/token do TJRR. Não há
`projudicrt.tjrr.jus.br` (NXDOMAIN). Na prática,
**o login por certificado do TJRR está quebrado/misconfigurado** e não é
caminho para o conector.

O certificado continua relevante para **assinatura dentro do sistema**, não
para autenticação:

- o manual manda emitir um certificado pelo menu
  `OUTROS > MEUS DADOS > LISTAR CERTIFICADO > NOVO`;
- a senha do certificado é usada no ato da assinatura;
- manuais antigos (herdados do TJPR) mencionam assinatura com arquivo `.p12`
  (tipicamente A1) ou cartão/token (A3) + PIN;
- o “novo assinador” do TJRR (2025) dispensa applet Java, mas continua sendo
  assinatura, não login.

Não foi possível confirmar se o TJRR aceita um A1 ICP-Brasil externo para
assinatura ou apenas o certificado emitido pelo próprio sistema. Para o
conector read-only, isso é fora de escopo.

### 6. PDPJ

Não há botão “Acessar com PDPJ” na home nem na tela do Keycloak. O
`autoSSO=true` leva ao Keycloak **do TJRR**, não ao
`sso.cloud.pje.jus.br`. Uma sondagem pública do SSO nacional com o ID
previsível `projudi-tjrr` respondeu **400 “Cliente não encontrado”**; pode
existir outro client_id, mas não há botão nem redirect público. Um trecho
público indexado do ambiente de homologação mostra o método
`UsuarioLogado.podeAcessarPDPJ()` no código do Projudi, então existe um caminho
de PDPJ para alguns perfis, mas **não foi observado como login público** e não
deve ser usado no MVP.

### 7. `autoSSO=true`

O frameset repassa o parâmetro para o `mainFrame`:

```text
/projudi/?autoSSO=true
  -> home.do?r=<random>&autoSSO=true
  -> home.do emite:
     parent.frames[0].location = '/projudi/cabecalho-oid.jsp';
     location.href = '/projudi/tjpr/autenticacao.do?_tj=<token>';
  -> 302 Keycloak
```

Mudanças observadas: apenas o redirecionamento automático para o SSO local.
Não muda o realm, o client, o `redirect_uri`, nem aponta para PDPJ. É um atalho
de um clique, não um fluxo de autenticação diferente. Como o `autenticacao.do`
inclui `login=true`, chamadas repetidas podem forçar nova autenticação; não usar
`autoSSO` como keepalive.

## Keycloak

### Realm e client

| Item | Valor observado |
|---|---|
| Issuer | `https://keycloak.tjrr.jus.br/realms/projudi` |
| Realm | `projudi` |
| Client ID | `projudi-client` |
| Redirect URI | `https://projudi.tjrr.jus.br/projudi/tjpr/autenticacao.do` (e outras rotas `/projudi/*`) |
| Response type | `code` |
| Scope | `openid` |
| `login` | `true` |
| PKCE / nonce | não observados na URL de autorização |
| Tema | customizado TJRR (“Autentikey”), PatternFly |
| Versão | provável Keycloak 17+ (caminho `/realms` sem `/auth`); exata não exposta |

O Keycloak aceitou `redirect_uri` para várias rotas do mesmo host
(`/projudi/tjpr/autenticacao.do`, `/projudi/usuario/logon.do`,
`/projudi/logon.do`, `/projudi/LogOn`, até uma rota inexistente). Isso sugere
wildcard `https://projudi.tjrr.jus.br/projudi/*`. O conector deve usar sempre a
rota canônica.

### Endpoints públicos

| Endpoint | Status | Observação |
|---|---|---|
| `/realms/projudi/.well-known/openid-configuration` | 200 | metadata OIDC |
| `/realms/projudi/protocol/openid-connect/auth` | 200 | formulário |
| `/realms/projudi/protocol/openid-connect/token` | não chamado | troca de code no backend |
| `/realms/projudi/protocol/openid-connect/userinfo` | não chamado | — |
| `/realms/projudi/protocol/openid-connect/certs` | não chamado | JWKS |
| `/realms/projudi/protocol/openid-connect/logout` | não chamado | logout |
| `/realms/projudi/login-actions/authenticate` | ação do form | recebe CPF/senha e depois OTP |
| `/realms/projudi/login-actions/reset-credentials` | 200 | recuperação por CPF |
| `/realms/projudi/account` | não testado | conta do usuário |

O `.well-known` anuncia `authorization_code`, `implicit`, `refresh_token`,
`password`, `client_credentials`, `device_code` e CIBA; também
`tls_client_auth` entre os métodos de autenticação de **client**. Isso não
significa que o usuário possa usar password grant, device code ou certificado:
o fluxo exposto na tela é authorization code + formulário + OTP.

Scopes customizados observados: `usercpf` e `tjrr_lotacao`. Claims padrão:
`sub`, `preferred_username`, `name`, `email`, `acr`, entre outros. O CPF
provavelmente chega em `preferred_username` ou no scope `usercpf`; confirmar
com credencial.

### Tipos de autenticação expostos

| Tipo | Situação |
|---|---|
| Usuário/senha (CPF) | **Sim**, formulário Keycloak |
| TOTP (Google Authenticator) | **Sim**, obrigatório após o primeiro acesso; 6 dígitos/30 s |
| OTP por e-mail/SMS | **Não observado**; e-mail só é usado para link de reset |
| Certificado X.509 de usuário | **Não exposto** no `projudi-client`; `tls_client_auth` é auth de client |
| gov.br / IdP social | **Não observado** |
| PDPJ | **Não observado** |
| Auto-registro | **Não**; não há link e o fluxo de registration não é oferecido |

### Recuperação de senha e primeiro acesso

```text
GET /realms/projudi/login-actions/reset-credentials?client_id=projudi-client&tab_id=<...>
  -> 200, campo username (label CPF)
```

O endpoint de auto-registro do Keycloak responde **400 “Cadastro não é
permitido”**, confirmando que não há cadastro público. O envio do formulário
de reset não foi testado. O manual do tribunal descreve:

1. na tela de login, informar o CPF e clicar em “Esqueceu a senha?”;
2. o sistema envia uma senha/link para o e-mail cadastrado no formulário de
   adesão;
3. o usuário cria a senha e entra com CPF + senha;
4. no primeiro acesso, o sistema pede a configuração do autenticador (QR code);
5. nos acessos seguintes, informar CPF, senha e “Código de uso único” (TOTP).

Se o perfil estiver bloqueado, o link de reset **não é enviado**. O bloqueio
ocorre após 5 tentativas de senha, 5 códigos TOTP errados ou mais de 5 acessos
simultâneos; o desbloqueio automático é de 5 minutos, e depois disso é preciso
acionar a central. O reset de MFA (perda de celular) é manual, pela central.

## Sessão e cookies

### Pré-login

| Cookie | Onde | Atributos observados | Papel |
|---|---|---|---|
| `JSESSIONID` | `projudi.tjrr.jus.br` | `Path=/projudi; Secure; HttpOnly` | sessão JSP |
| `OAuth_Token_Request_State` | `projudi.tjrr.jus.br` | `Version=1; HttpOnly` (sem `Secure`/`SameSite` visíveis) | state do OIDC |
| `AUTH_SESSION_ID` | `keycloak.tjrr.jus.br` | `Path=/realms/projudi/; SameSite=None; Secure; HttpOnly` | sessão Keycloak |
| `AUTH_SESSION_ID_LEGACY` | Keycloak | idem | compatibilidade |
| `KC_RESTART` | Keycloak | `Path=/realms/projudi/; Secure; HttpOnly` | restauração do fluxo OIDC |
| `projudiContCookie` | app | JavaScript, não HttpOnly | contador de abas/sessões; irrelevante para HTTP puro |

Depois do login, os cookies exatos **não foram observados**. É provável que o
Keycloak grave `KEYCLOAK_IDENTITY`/`KEYCLOAK_SESSION` no host dele e o Projudi
grave apenas o `JSESSIONID`; não assumir que o cookie do Keycloak sozinho
autentica no app.

### Timeout e keepalive

O `js/projudi.js` define timeout padrão de **60 minutos** e renova a sessão
pouco antes do vencimento com:

```text
GET /projudi/usuario/logon.do?actionType=realimentarSessao
```

O timeout real do servidor pode ser diferente. O conector deve tratar
`302` para o Keycloak ou a página “A sessao expirou” como sessão expirada e
refazer o login (o que exige TOTP).

### Limite de sessões

O manual informa bloqueio com **mais de 5 acessos simultâneos**. O frameset
também usa o cookie `projudiContCookie` para contar abas e, ao fechar a última,
dispara `usuario/logon.do?actionType=logout&forcarLogout=true`. O conector deve
usar **uma sessão por cliente/portal**, com lock distribuído, e não paralelizar
login.

## Consulta pública

### SPA

`GET https://consultaprojudi.tjrr.jus.br/` responde `200` com uma SPA
React/Vite (`/assets/index-*.js`), título **“Consulta Processual Pública”**.
O `consultaPublica.jsp` do Projudi é só um iframe para essa SPA.

Fluxo do front-end:

```text
/  -> /captcha (Tencent TJNCaptcha)
   -> token = "<randstr>:<ticket>"
   -> /app/consulta
   -> /app/consulta/:numeroUnico/:grau
```

O token do captcha fica em estado React (memória); recarregar a página perde o
token. O header enviado à API é `Authorization: <randstr>:<ticket>`, **não** é
Bearer/JWT.

### API

Base: `https://consultaprojudi.tjrr.jus.br/consilium-api` (Spring Boot).

| Rota | Método | Uso |
|---|---|---|
| `/processos` | GET | busca 1º grau |
| `/recursos` | GET | busca 2º grau |
| `/processos/{numeroUnico}` | GET | detalhe 1º grau |
| `/recursos/{numeroUnico}` | GET | detalhe 2º grau |
| `/processos/{numeroUnico}/movimentacoes?page=&size=` | GET | movimentações 1º grau |
| `/recursos/{numeroUnico}/movimentacoes?page=&size=` | GET | movimentações 2º grau |
| `/projudi/movimentacao-arquivo/{id}` | GET | download do arquivo (PDF/HTML) |
| `/comarcas` | GET | combo de comarcas |
| `/varas` | GET | combo de varas |

Sem `Authorization` válido, as rotas testadas respondem **401** com:

```json
{"errors":["Captcha expirado. Resolva o captcha novamente."]}
```

O mesmo ocorre para `/comarcas`, `/varas` e para o download de arquivo. O
front-end trata `429` como “Muitas requisições em pouco tempo”.

### Formulário de busca

Campos observados no bundle e no HTML:

| Campo | Observação |
|---|---|
| `numeroUnico` | número CNJ mascarado `0000000-00.0000.0.00.0000` |
| `recurso` | checkbox “2º Grau” (boolean) |
| `comarca` | id da comarca |
| `vara` | id da unidade |
| `nomeParte` | nome da parte |
| `cpfCnpj` | CPF/CNPJ |
| `nomeAdvogado` | nome do advogado |
| `oabNumero` | número da OAB |
| `oabCategoria` | categoria (texto) |
| `oabUF` | UF da OAB |
| `page` / `rows` | paginação; `rows` vira `size` |

A busca exige **pelo menos um filtro**; o formulário vazio não chama a API. O
formulário e o builder da API preveem consulta por OAB (número, categoria e
UF), mas **nenhuma busca real foi executada** por causa do captcha.

### Modelo de dados da SPA (pelo bundle)

A UI mapeia, na lista, por processo:

- `numeroUnico`
- `classe`
- `assunto`
- `recurso` (1º/2º grau)
- `versusDescricao`
- `ultimaMovimentacaoDescricao`

No detalhe, a UI mapeia:

- `numeroUnico`, `assunto`, `classe`, `comarca`, `vara`, `dataDistribuicao`;
- `orgaoJulgador` (2º grau);
- `polosAtivos` / `polosPassivos`, com `descricao` e `advogados`;
- `movimentacoes` (primeira página) e endpoint paginado à parte.

A UI espera, por movimentação, `descricao`, `sequencial`, `visibilidadeexterna`
e uma lista `arquivos`/`anexos` com `id` e `descricao`. O download é feito por
`/projudi/movimentacao-arquivo/{id}`; o retorno é binário e a UI detecta
`%PDF` vs HTML pelos primeiros bytes. O tamanho da página de movimentações é
20. Nada disso foi confirmado com uma resposta real, pois o captcha bloqueia a
busca.

### Páginas legadas ainda públicas

| Rota | Status | Observação |
|---|---|---|
| `/projudi/consultaPublica.jsp` | 200 | iframe da SPA |
| `/projudi/processo/consultaPublicaProcessoAptoJulgamento.do?actionType=iniciar` | 200 | formulário “Processos Aptos a Julgamento”; reCAPTCHA v2 |
| `/projudi/processo/consultaPublicaProcessosPronunciamentosJudiciais.do?actionType=iniciar` | 200 | “Processos Aguardando Análise” (art. 153 CPC); reCAPTCHA v2 |
| `/projudi/processo/validacaoDocumentos.do?_tj=...` | 200 | “Consulta via Chave de Validação”; campo `idDocumento`; Tencent captcha |
| `/projudi/processo/consultaPublicaProcesso.do` | 404 | rota antiga de consulta não existe mais |
| `/projudi_consulta/...` | 404 | contexto do TJPR não existe no TJRR |

As páginas de “aptos” e “pronunciamentos” usam `POST` com
`actionType=pesquisar` e campos `instancia`, `codTribunal`, `codComarca`,
`codAreaVara`, `codAreaRecursal`, `codVara`, `codTurma`,
`loginCodificadoJuiz`, `pageSize`, `pageNumber`, `sortColumn`, `sortOrder`.
Não foram submetidas.

### Captcha

Há dois sistemas:

- **reCAPTCHA v2** nas páginas legadas de aptos/pronunciamentos;
- **Tencent TJNCaptcha** na validação de documentos e na SPA/API
  (`https://ca.turing.captcha.qcloud.com/TJNCaptcha-global.js`).

Sem resolver o captcha, não há resultado. Isso inviabiliza a consulta pública
como fallback automatizado do conector. Um serviço de resolução de captcha
seria frágil, contrariaria o espírito do portal e não deve ser usado no MVP.

## Processo, movimentações e documentos

### Observado

- O app autenticado é o Projudi legado: Struts, HTML `ISO-8859-1`, frameset e
  tokens `_tj` por sessão/contexto. O stack trace público do ambiente de
  homologação também cita Tiles, mas isso não foi confirmado na produção.
- Praticamente todos os links e forms internos carregam
  `_tj=<48 hex>` e, às vezes, `;jsessionid=...` na URL.
- `_tj` é **por sessão e por contexto de navegação**; não pode ser hardcoded
  nem reaproveitado entre sessões/páginas. Sem `_tj` a página responde “A
  sessao expirou” (observado). Token de outra sessão deve responder “Conteúdo
  inacessível”, comportamento já visto no TJPR, mas **não testado no TJRR**.
- O bundle público `js/projudi.js` contém rotas de apoio (seleção de classe,
  assunto, tipo de documento, tipo de movimento, relatórios), mas **não** as
  rotas de abrir processo/listar movimentações/baixar documento.
- Os manuais oficiais mostram a interface do advogado:
  `Buscas > Busca Processual Simples`, `Ações 1º Grau > Cadastrar Nova Ação`,
  botão `Peticionar` na tela do processo.
- A SPA de consulta pública oferece endpoints de movimentações e arquivos, mas
  atrás de captcha; o acesso real não foi executado.

### Não observado (exige credencial)

- URL e parâmetros exatos da busca/abertura de processo na sessão autenticada.
- Se a busca aceita o número CNJ direto e abre o processo, ou se passa por uma
  lista de resultados.
- Estrutura da página do processo: abas, tabela de movimentações, partes,
  documentos.
- Se os documentos ficam embutidos na movimentação, em uma aba “Documentos” ou
  em uma árvore “Navegação de Arquivo”.
- Rota de download, `Content-Type`, nomes/IDs e regra de acesso
  (`AcessoUsuario`, `visibilidadeexterna`, processos em segredo).
- Se há paginação/limite no índice de movimentações.
- Se o processo precisa ser “aberto” na sessão antes de listar documentos
  (como no TJGO).

### Hipóteses (não tratar como fato)

- O Projudi legado costuma usar `/projudi/processo/` com nomes como
  `consultaProcesso.do`, `abrirProcesso.do`, `dadosProcesso.do`,
  `movimentacao.do`, `documento.do`, `arquivo.do` e parâmetros
  `Id_Processo`, `Id_Movimentacao`, `Id_Arquivo`.
- O download provavelmente é um link opaco com `_tj`, não uma URL montável com
  `id` + `hash` como no TJGO.
- O 2º grau provavelmente usa rotas `processo/recursal/...` e/ou parâmetro
  `recurso`, mas isso não foi confirmado.
- Documentos provavelmente estão pendurados em movimentações, mas o TJRR pode
  usar uma árvore própria e IDs diferentes.

**Nenhuma dessas hipóteses foi validada.** Rotas `/projudi/*` não liberadas
explicitamente — inclusive inexistentes — redirecionam ao SSO; portanto,
status `302` não prova existência de rota. Só uma sessão autenticada permite
mapear.

## Endpoints e JS

### Rotas observadas

| Rota | Método | Status | Nota |
|---|---|---|---|
| `/projudi/` | GET | 200 | frameset |
| `/projudi/home.do` | GET | 200 | tela “Acesso via SSO” |
| `/projudi/home.do?autoSSO=true` | GET | 200 | redireciona ao SSO via JS |
| `/projudi/cabecalho-oid.jsp` | GET | 200 | cabeçalho do frameset |
| `/projudi/tjpr/autenticacao.do` | GET | 302 | Keycloak |
| `/projudi/usuario/logon.do?actionType=inicio` | GET | 302 | Keycloak |
| `/projudi/logon.do` | GET | 302 | Keycloak |
| `/projudi/LogOn` | GET | 302 | Keycloak |
| `/projudi/token/autenticacaoCertificado.do?actionType=inicioComCertificado` | GET | 302 | aponta para `projudicrt.tjpr.jus.br`; falha |
| `/projudi/usuario/logon.do?actionType=realimentarSessao` | GET | — | keepalive no JS |
| `/projudi/consultaPublica.jsp` | GET | 200 | iframe da SPA |
| `/projudi/informacoesAdvogado.do` | GET | 200 | exige `jsessionid` + `_tj`; senão “A sessao expirou” |
| `/projudi/manuaisPublico.do` | GET | 200 | PDFs; exige `_tj` |
| `/projudi/noticias.jsp` | GET | 200 | notícias/release |
| `/projudi/processo/validacaoDocumentos.do` | GET | 200 | exige `_tj` |
| `/projudi/processo/consultaPublicaProcessoAptoJulgamento.do?actionType=iniciar` | GET | 200 | reCAPTCHA |
| `/projudi/processo/consultaPublicaProcessosPronunciamentosJudiciais.do?actionType=iniciar` | GET | 200 | reCAPTCHA |
| `/projudi/ajaxUtils.do` | GET | 200 | combos AJAX; `_tj` por elemento |
| `/projudi/SessionExpired` | GET | 302 | protegida; usada pelo JS de sessão expirada |

### Rotas de apoio no `js/projudi.js` (observadas no bundle)

```text
/projudi/ajaxUtils.do?actionType=ajaxHintAssunto
/projudi/ajaxUtils.do?actionType=ajaxHintClasseProcessual
/projudi/processo/assunto.do?actionType=ajaxHint
/projudi/processo/assunto.do?actionType=pesquisar
/projudi/processo/assunto.do?actionType=pesquisarMultiplo
/projudi/processo/classeProcessual.do?actionType=ajaxHint
/projudi/processo/classeProcessual.do?actionType=pesquisar
/projudi/processo/classeProcessual.do?actionType=pesquisarMultiplo
/projudi/processo/classeProcessual.do?actionType=pesquisarClassesProcessuaisRecursais
/projudi/processo/tipoDocumento.do?actionType=filtrarArvorePelaDescricao
/projudi/processo/tipoMovimento.do?actionType=pesquisar
/projudi/processo/tipoMovimentoMagistrado.do?actionType=pesquisar
/projudi/processo/assuntoDemandaJudicialRepetitiva.do?actionType=selecionar
/projudi/processo/recursal/materia.do?actionType=pesquisarSelecao
/projudi/processo/fazenda/cda.do?actionType=iniciarCadastro
/projudi/processo/criminal/delegaciaCelepar.do?actionType=pesquisar
/projudi/judiciario/especializacao2Grau.do?actionType=pesquisar
/projudi/administracao/relatorio.do?actionType=visualizar&idRelatorio=
/projudi/usuario/logon.do?actionType=realimentarSessao
```

São rotas compiladas no bundle, úteis para entender o padrão
`actionType`/`_tj`, mas **não são** as rotas de processo/documento e podem ser
código morto.

### Rotas de apoio da SPA

```text
GET /consilium-api/processos?<filtros>&page=0&size=20
GET /consilium-api/recursos?<filtros>&page=0&size=20
GET /consilium-api/processos/{numeroUnico}
GET /consilium-api/recursos/{numeroUnico}
GET /consilium-api/processos/{numeroUnico}/movimentacoes?page=0&size=20
GET /consilium-api/recursos/{numeroUnico}/movimentacoes?page=0&size=20
GET /consilium-api/projudi/movimentacao-arquivo/{id}
GET /consilium-api/comarcas
GET /consilium-api/varas
```

Todas exigem `Authorization: <randstr>:<ticket>` do Tencent captcha.

## Credenciamento

Não há auto-registro. O advogado precisa:

1. preencher o **Termo de Adesão** em PDF;
2. anexar cópia da credencial da OAB;
3. assinar o formulário com certificado digital, firma reconhecida ou gov.br;
4. enviar para a OAB-RR (advogados da seccional RR) ou para o TJRR (demais
   seccionais);
5. receber/definir a senha inicial pelo “Esqueceu a senha?”;
6. configurar o TOTP no primeiro login.

O credenciamento é manual e depende de aprovação. O conector não pode criar
conta, recuperar senha nem resetar MFA sozinho.

### Bloqueio geográfico

O TJRR adota restrições a acesso originado de IPs no exterior. Há um formulário específico
de “acesso excepcional em caso de bloqueio geográfico”, assinado com gov.br ou
certificado digital e enviado à central; a liberação é temporária e vinculada
ao IP/período informados. **O host do conector precisa estar no Brasil ou ter
essa liberação.** O ambiente usado nesta exploração conseguiu acesso, mas isso
não garante que o host de produção do Jurisync esteja liberado.

### A1/A3

| Uso | Situação |
|---|---|
| Login por A1/mTLS no TJRR | **Não**; hosts do TJRR não pedem certificado de cliente |
| Login por A3 | **Não observado**; rota legada aponta para o TJPR e falha |
| Assinatura de documentos | **Sim**, com certificado/senha; o manual manda emitir pelo sistema; aceitação de A1/A3 externo não confirmada |
| Assinatura do termo de adesão | Aceita certificado digital, firma reconhecida ou gov.br |

Para o conector **read-only**, nenhum certificado é necessário. Não reutilizar
`PJE_A1_P12` para o TJRR.

## Diferenças vs TJGO

| Aspecto | TJGO | TJRR |
|---|---|---|
| Geração | Projudi “novo” (servlets, JSON) | Projudi legado (Struts, frameset, HTML) |
| Login principal | A1/mTLS em `/certificado` | Keycloak OIDC com CPF + senha + TOTP |
| Certificado | A1 mTLS, sem TOTP | Sem mTLS no TJRR; rota legada aponta para o TJPR e falha |
| PDPJ | Botão existe, mas redirect inválido | Não há botão; `autoSSO` é SSO local |
| MFA | OTP por e-mail no fallback | TOTP obrigatório (Google Authenticator) |
| Token de navegação | `JSESSIONID` basta | `_tj` por sessão/contexto em links e forms |
| Busca de processo | `POST /BuscaProcesso` | Desconhecida na sessão; SPA pública separada |
| Índice de documentos | JSON `BuscaProcesso?PaginaAtual=9&PassoBusca=5` | Desconhecido; provavelmente HTML por movimentação |
| Download | `BuscaProcesso?PaginaAtual=6&Id_MovimentacaoArquivo=&hash=` | Desconhecido; link opaco com `_tj` |
| Consulta pública | Integrada ao app | SPA React + API `/consilium-api` + captcha |
| Sessão | `JSESSIONID`, `WIDPROP`, `dtCookienncenbbl` | `JSESSIONID`, `OAuth_Token_Request_State`, cookies Keycloak |
| Erro de sessão | redirect `/LogOn?PaginaAtual=-200` | 302 Keycloak, “A sessao expirou”, “Conteúdo inacessível” |
| Encoding | ISO-8859-1 | ISO-8859-1 |
| Release | Geração nova | `v5.12.33.1` |

## Desenho do adapter

### Flavor e registro

O TJRR não é `tjgo-servlet` nem o `legacy-frameset` puro (TJAM). É
**legacy + Keycloak + TOTP + `_tj`**. Proposta:

```python
Portal(
    "tjrr-1g", "projudi", "TJRR", "1g",
    "projudi.tjrr.jus.br", "/projudi/", "password_totp", "TJRR_1G",
    region="23", justice="8", wired=False, guess_by_number=False,
    flavor="legacy-keycloak",
)
```

Ajustar também `Portal.login_url` para usar
`tjpr/autenticacao.do` no flavor `legacy-keycloak` (hoje o default é
`usuario/logon.do?actionType=inicio`, que também redireciona, mas é menos
canônico).

### Módulos sugeridos

```text
src/jurisync/connectors/projudi/
  base.py              # sessão, cookies, erros, texto/PDF, limites (comum)
  tjgo.py              # flavor tjgo-servlet (já validado)
  legacy_keycloak.py   # login Keycloak/TOTP + navegação com _tj
  tjrr.py              # config/parsers específicos do TJRR
  parsers_tjrr.py      # HTML de processo/movimentações/documentos + forms
  server.py            # tools MCP; escolhe o adapter pelo portal
```

Se o login Keycloak for extraído para `core/keycloak.py`, ele serve para TJRR,
TJPR e futuros portais. Reutilizar `core.totp` (`parse_accounts`, `totp`,
`next_code`) e o precedente de `connectors/eproc/auth.py`, que já preenche
`#username`, `#password`, `#kc-login` e `#otp` e copia cookies para
`curl_cffi`.

### Autenticação

Ordem de preferência:

1. **HTTP puro com `curl_cffi`** — o formulário Keycloak é HTML simples, sem
   JS obrigatório, sem captcha e sem CSRF visível. Fluxo:
   - `GET /projudi/` para obter `JSESSIONID`;
   - `GET /projudi/tjpr/autenticacao.do`, seguir o 302 até a página de login;
   - parsear a `action` absoluta do form e os hidden `session_code`,
     `execution`, `client_id`, `tab_id`;
   - `POST` com `username` (CPF), `password`, `login=Entrar`;
   - se vier a página de OTP (`#kc-otp-login-form`, `#otp`), postar o TOTP;
   - se vier a página de configuração (`QR code`), abortar com
     `mfa_setup_required`;
   - seguir o callback com `code`/`state`, preservando o cookie
     `OAuth_Token_Request_State`;
   - validar a sessão com uma página protegida e detectar redirect ao Keycloak.
2. **SeleniumBase** — fallback se o Keycloak passar a exigir JS, device
   confirmation ou captcha. Reutilizar os seletores de `eproc/auth.py` e copiar
   o cookie jar para `curl_cffi`.
3. **Certificado/mTLS** — não suportado no TJRR; não implementar.
4. **Consulta pública** — não suportada para automação por causa do captcha.

O `login=true` na URL de autorização pode forçar nova autenticação. O conector
deve iniciar o fluxo uma vez e reutilizar a sessão; não chamar
`autenticacao.do` a cada operação.

### Configuração

```text
TJRR_1G_USERNAME=<CPF, só dígitos>
TJRR_1G_PASSWORD=<senha>
TJRR_1G_2FA_DATA=<otpauth://... ou base32>
TJRR_1G_2FA_QR=<caminho do QR, alternativa>
# Opcional para bootstrap manual:
TJRR_1G_SESSION_COOKIES=<JSON do cookie jar>
```

Não usar `PJE_A1_P12`/`PJE_A1_PIN` para o TJRR. O segredo TOTP é tão sensível
quanto a senha: guardar em secret manager, nunca no repositório, nunca em log.
Se o cliente não puder fornecer o segredo, o conector não consegue operar
desacompanhado; oferecer apenas bootstrap manual de cookies, com validade curta.

### Sessão

- Uma sessão por cliente/portal, com lock distribuído; nunca dois logins
  simultâneos para o mesmo CPF (limite de 5 acessos).
- Guardar o cookie jar completo (Projudi + Keycloak) em memória ou storage
  criptografado; nunca logar `Set-Cookie`.
- Renovar com `usuario/logon.do?actionType=realimentarSessao` antes do timeout
  (padrão 60 min) ou antes de cada operação, com intervalo mínimo.
- Se a resposta for `302` para o Keycloak, “A sessao expirou” ou
  “Conteúdo inacessível”, refazer login e reabrir o processo; o contexto de
  processo provavelmente se perde.
- Tratar `400` no callback como falha de login; não repetir em loop.

### Navegação e `_tj`

O adapter deve ser um **crawler de links/forms**, não um cliente REST:

```text
login -> home.do
  -> extrair links com _tj
  -> seguir "Buscas" / "Busca Processual Simples"
  -> preencher o form com o número CNJ (action + _tj exatos)
  -> se houver lista, escolher o número exato
  -> abrir processo
  -> parsear cabeçalho, partes, movimentações
  -> para cada movimentação, parsear documentos/links
  -> download: GET no href exato (com _tj), sem montar URL
```

Regras:

- extrair `_tj` e `jsessionid` da página atual; não reutilizar entre páginas;
- se a página mudar, reextrair os tokens;
- se vier “Conteúdo inacessível”, voltar um passo e refazer a navegação;
- não paralelizar navegação na mesma sessão;
- intervalo mínimo de 1–2 s entre requisições e backoff em `429`/`5xx`;
- HTML em `ISO-8859-1`; decodificar antes de parsear/strip.

### Dados e documentos

- `document_id` exposto ao MCP deve ser **opaco** (hash/índice interno), nunca
  `_tj`, `jsessionid` ou URL bruta.
- Manter em memória o mapa `document_id -> href` da sessão.
- Download devolve bytes + `Content-Type` + nome sugerido; extração de texto
  reutiliza `core.document_text`/`core.pdf`.
- PDFs escaneados → `ocr_needed`; HTMLs → strip de tags.
- Processos em segredo/restritos → `access_denied`, sem tentar contornar.
- Se o TJRR exigir “abrir processo” antes de listar documentos (como o TJGO),
  guardar o contexto por processo e reabrir após re-login.

### Tools MCP

| Tool | Entrada | Saída |
|---|---|---|
| `projudi_get_process` | `portal`, `process_number`, `include_documents` | metadados, partes, movimentações e lista de documentos |
| `projudi_list_documents` | `portal`, `process_number`, `movement_id` opcional | movimentações + documentos |
| `projudi_read_documents` | `portal`, `process_number`, `document_ids` (máx. 5) | texto + `status` (`extracted`/`ocr_needed`/`access_denied`) |
| `projudi_download_document` | `portal`, `process_number`, `document_id` | arquivo em `JURISYNC_DOCUMENTS_DIR` + metadados |

Todas read-only; nenhuma tool de peticionamento/assinatura. Todas aceitam
`portal` para o mesmo servidor atender TJGO, TJPR e TJRR.

### Erros

| Sinal | Significado | Ação |
|---|---|---|
| Página de login Keycloak com erro | credencial inválida | `invalid_credentials`, não repetir |
| Página de OTP com erro | TOTP inválido/expirado | `mfa_invalid`, tentar próxima janela uma vez |
| Página de QR/configuração | MFA ainda não configurado | `mfa_setup_required`, pedir ação humana |
| “Account is locked” / bloqueio | 5 tentativas ou 5 sessões | `account_locked`, parar e acionar suporte |
| `302` para Keycloak em página interna | sessão expirada | `session_expired`, re-login |
| “A sessao expirou” | sessão/token expirado | re-login e reabrir processo |
| “Conteúdo inacessível” | `_tj` inválido para a sessão | voltar um passo e reextrair |
| `400` “Requisição inválida” no callback | code/state inválido | `login_failed` |
| `401` “Captcha expirado” na SPA | captcha ausente/expirado | `captcha_required` (não usar na automação) |
| `429` | rate limit | backoff exponencial |

### Testes

Offline (sem credenciais):

- parsing do frameset/home e da página de login Keycloak;
- parsing do form de reset e da página de OTP (fixtures redigidas);
- parsing de `_tj`/`jsessionid` em links e forms;
- detecção de “A sessao expirou”/“Conteúdo inacessível”;
- parsing das páginas públicas e da API de consulta (erro 401/429);
- extração de texto de PDF/HTML.

Live (opt-in, read-only):

1. login com CPF/senha/TOTP;
2. abrir um processo do próprio cliente pelo número;
3. listar movimentações e documentos;
4. baixar 1 PDF e 1 HTML;
5. extrair texto dos dois;
6. repetir via MCP stdio.

## Riscos

1. **TOTP é o bloqueio principal**: sem o segredo, não há automação; com
   segredo, ele precisa de custódia forte. O cliente pode não querer fornecê-lo.
2. **Bloqueio de conta**: 5 erros de senha/OTP ou 5 sessões simultâneas
   bloqueiam por 5 minutos; repetir pode exigir suporte. Implementar lock e
   circuit breaker.
3. **Sem auto-registro/recuperação automatizável**: onboarding e reset são
   manuais (OAB/TJRR, e-mail, suporte).
4. **Geo-bloqueio**: IP fora do Brasil pode ser bloqueado; o host precisa estar
   no Brasil ou ter liberação excepcional.
5. **`_tj` frágil**: token por sessão/contexto; qualquer parser rígido quebra.
   Nunca montar URL de `.do` na mão.
6. **HTML de Struts**: sem contrato estável; parsers devem falhar alto, não
   devolver lista vazia enganosa.
7. **Sessão curta**: 60 min de idle; re-login exige TOTP e reabertura de
   processo. Sessões longas podem ser derrubadas pelo tribunal.
8. **Captcha**: consulta pública e páginas legadas não são automatizáveis sem
   solver; não usar como fallback.
9. **PDPJ/certificado**: caminhos documentados, mas quebrados; não prometer ao
   cliente.
10. **LGPD/segredo de justiça**: processos e documentos são dados sensíveis;
    read-only, sem log de conteúdo, com `access_denied` para restritos.
11. **Redirect URI amplo no Keycloak**: aceita várias rotas `/projudi/*`
    (sugere wildcard); é uma observação de segurança do tribunal, não algo a
    explorar.
12. **Ambiente de homologação indexado**: `tre2.tjrr.jus.br` expõe stack traces
    (KeycloakOIDCFilter, `podeAcessarPDPJ`); não usar como fonte nem para testes.
13. **Mudanças do tribunal**: tema Keycloak, fluxo de MFA e release podem mudar
    sem aviso; monitorar e retestar.
14. **Assinatura**: o conector é read-only; não implementar assinatura,
    peticionamento ou qualquer escrita.

## Perguntas abertas

1. Qual é a rota exata do redirect pós-callback e qual marcador confirma
   “logado” (nome no cabeçalho, link “Sair”, cookie)?
2. O campo de OTP é `otp` e o `credentialId` é obrigatório? Há `client_data`?
3. O TOTP pode ser provisionado para automação (segredo exportável) ou só via
   QR code no primeiro login?
4. Existe conta de serviço/API key para escritórios, ou apenas a conta pessoal
   do advogado?
5. Quais são as rotas/parâmetros de busca, abertura, movimentações e documentos
   na sessão autenticada?
6. O processo precisa ser “aberto” na sessão antes de listar documentos?
7. Como funcionam processos em segredo, acesso excepcional e
   `visibilidadeexterna`?
8. O 2º grau usa rotas `recursal` separadas ou o mesmo fluxo com parâmetro?
9. Qual é o timeout real do servidor (além dos 60 min do JS)?
10. O TJRR aceita A1 ICP-Brasil externo para assinatura ou só o certificado
    emitido pelo sistema?
11. O PDPJ está habilitado para algum perfil? `podeAcessarPDPJ` é usado?
12. A rota de certificado continuará apontando para o TJPR ou será corrigida
    para um host do TJRR?
13. A SPA de consulta pública permite reutilizar o token de captcha por quanto
    tempo? (Não usar na automação; apenas entender.)
14. Há WebAuthn/passkey para alguns usuários? O formulário público só mostra
    senha.

## Evidências

Coletadas em 2026-11-05, sem credenciais. Os paths foram truncados para não
incluir `_tj`, `jsessionid`, `state` ou cookies.

| Requisição | Status | Título/evidência |
|---|---|---|
| `GET https://projudi.tjrr.jus.br/projudi/` | 200 | frameset; `JSESSIONID`; título “Projudi - Processo Eletrônico do Judiciário de Roraima” |
| `GET /projudi/home.do` | 200 | “Acesso ao Sistema”; “Acesso via SSO”; release `v5.12.33.1` |
| `GET /projudi/home.do?autoSSO=true` | 200 | JS redireciona para `/projudi/tjpr/autenticacao.do` |
| `GET /projudi/tjpr/autenticacao.do` | 302 | `keycloak.tjrr.jus.br/realms/projudi/protocol/openid-connect/auth`; client `projudi-client`; `state` UUID |
| `GET /projudi/usuario/logon.do?actionType=inicio` | 302 | mesmo Keycloak; `redirect_uri` preserva o path |
| `GET /projudi/logon.do` | 302 | mesmo Keycloak |
| `GET /projudi/LogOn` | 302 | mesmo Keycloak |
| `GET keycloak.../realms/projudi/.well-known/openid-configuration` | 200 | issuer, endpoints, grants, scopes `usercpf`/`tjrr_lotacao` |
| `GET keycloak.../realms/projudi/protocol/openid-connect/auth?...` | 200 | “Entrar em PROJUDI TJRR”; campos CPF/senha/rememberMe |
| `GET keycloak.../realms/projudi/login-actions/reset-credentials?...` | 200 | campo `username` label CPF |
| `GET keycloak.../realms/projudi/login-actions/registration?...` | 400 | “Cadastro não é permitido” |
| `GET https://sso.cloud.pje.jus.br/auth/realms/pje/...&client_id=projudi-tjrr...` | 400 | “Entrar em PDPJ” / “Cliente não encontrado” |
| `GET /projudi/consultaPublica.jsp` | 200 | iframe `https://consultaprojudi.tjrr.jus.br` |
| `GET https://consultaprojudi.tjrr.jus.br/` | 200 | “Consulta Processual Pública”; bundle Vite |
| `GET /consilium-api/processos?page=0&size=1` | 401 | `{"errors":["Captcha expirado..."]}` |
| `GET /consilium-api/comarcas` | 401 | captcha obrigatório |
| `GET /consilium-api/projudi/movimentacao-arquivo/0` | 401 | captcha obrigatório |
| `GET /projudi/processo/consultaPublicaProcessoAptoJulgamento.do?actionType=iniciar` | 200 | form; reCAPTCHA v2 |
| `GET /projudi/processo/consultaPublicaProcessosPronunciamentosJudiciais.do?actionType=iniciar` | 200 | form; reCAPTCHA v2 |
| `GET /projudi/processo/validacaoDocumentos.do?_tj=...` | 200 | campo `idDocumento`; Tencent captcha |
| `GET /projudi/informacoesAdvogado.do;jsessionid=...?_tj=...` | 200 | “Informações aos Advogados”; termo de adesão; manuais |
| `GET /projudi/manuaisPublico.do?...` | 200 | PDF “MFA” e “Primeiro Acesso” |
| `GET /projudi/noticias.jsp` | 200 | MFA obrigatório; Exportatool; novo assinador |
| `GET /projudi/token/autenticacaoCertificado.do?actionType=inicioComCertificado` | 302 | `projudicrt.tjpr.jus.br`; depois `erro500Crypto.jsp` |
| `GET projudicrt.tjpr.jus.br/...erro500Crypto.jsp` | 200 | “Conteúdo inacessível... não é válido para esta sessão” |
| `openssl s_client` em `projudi/keycloak/consultaprojudi` | — | `No client certificate CA names sent` |
| `openssl s_client` em `projudicrt.tjpr.jus.br` | — | pede certificado cliente; TLS 1.2; `RSA sign` |
| `GET /projudi/zzz-nonexistent-xyz.do` | 302 | catch-all do filtro Keycloak; não serve para descobrir rotas |
| `GET /projudi/processo/consultaPublicaProcesso.do` | 404 | rota antiga ausente |
| `GET /projudi_consulta/...` | 404 | contexto do TJPR ausente |
| `GET http://projudi.tjrr.jus.br/projudi/home.do` | 301 | redirect para HTTPS |

## Reprodução read-only

```bash
# Frameset e home
curl -sS -D - -o /dev/null https://projudi.tjrr.jus.br/projudi/
curl -sS -D - -o /dev/null https://projudi.tjrr.jus.br/projudi/home.do

# Início do SSO (não autentica)
curl -sS -D - -o /dev/null https://projudi.tjrr.jus.br/projudi/tjpr/autenticacao.do

# Metadata do Keycloak
curl -sS https://keycloak.tjrr.jus.br/realms/projudi/.well-known/openid-configuration

# Consulta pública sem captcha (retorna 401)
curl -sS -D - https://consultaprojudi.tjrr.jus.br/consilium-api/processos?page=0&size=1

# Certificado legado (mostra o redirect para o TJPR; não autentica)
curl -sS -D - -o /dev/null 'https://projudi.tjrr.jus.br/projudi/token/autenticacaoCertificado.do?actionType=inicioComCertificado'
```

Não executar login, não resolver captcha, não enviar formulários com
credenciais e não peticionar. O próximo passo só faz sentido com uma conta de
teste autorizada e um smoke live read-only.
