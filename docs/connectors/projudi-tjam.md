# Projudi TJAM — exploração pública e plano do adapter

Levantamento read-only em **2026-11-05**, sem credenciais, no portal
`https://projudi.tjam.jus.br/projudi/`. Nenhum login foi concluído, nenhuma
conta foi criada e nenhum formulário foi enviado com dados reais. Os únicos
POSTs feitos foram com campos vazios, para observar validação e mensagens de
erro. O objetivo é alimentar o futuro conector `projudi` do Jurisync com um
adapter próprio para o flavor legado do TJAM, sem reutilizar as rotas do TJGO.

Sistema observado: **PROJUDI v2026.1.11 (release 2026.1.11.2-TJAM)**, Java
servlets, HTML ISO-8859-1, frameset (`topFrame` + `mainFrame`), jQuery antigo
e uma camada SAJ na consulta pública. O portal fica atrás de **F5 BIG-IP ASM**
(cookies `TS*`, página “Request Rejected”, captcha).

**Em uma frase:** o TJAM é um Projudi legado com login local por senha e
certificado interno; não aceita A1/A3 em mTLS, e a consulta pública está atrás
de F5/reCAPTCHA.

## Resumo

| Capacidade | Situação em 2026-11-05 |
|---|---|
| Portal no ar, frameset legado | **Confirmado** |
| Login local `login`/`senha` | **Observado**; não autenticado |
| Login por certificado A1/A3 em mTLS | **Não observado**; TLS não pede certificado de cliente |
| Certificado digital interno do Projudi | **Confirmado** por FAQ, aviso no portal e tutorial |
| A1 “baixável” para assinatura externa | **Existe manual**; escopo exato não validado |
| A3 para assinatura offline (P7Z/P7S) | **Existe manual**; não serve para login |
| Registro de advogado por CPF + OAB | **Observado**; formulário público |
| Consulta pública | **Rotas observadas**, mas bloqueadas por F5/reCAPTCHA no nosso ambiente |
| Busca de processo autenticada | **Não mapeada** sem credenciais |
| Índice JSON de documentos como no TJGO | **Não observado**; provavelmente não existe |
| Download de documento por `arquivo.do?id=` | **Rota observada**; protegida por sessão |
| PDPJ SSO | **Rota `actionType=sso` existe**, sem entrada pública; não validado |
| MNI / webservice | **Não investigado**; não substitui o login no MVP |

## Método e limites

- Requisições espaçadas (3–5 s), `curl`/Python `requests`, timeout de 25–60 s.
- HTML lido como ISO-8859-1, com conversão para UTF-8 apenas para análise.
- Nenhuma credencial, CPF, OAB, número de processo ou dado de parte foi usado.
- POSTs vazios: login, recuperação de senha, cadastro de advogado (CPF vazio) e
  validação de documento (chave vazia).
- Snapshots históricos do Wayback Machine foram usados apenas para entender o
  formulário da consulta pública (2020 e 2023); a página atual não foi acessada
  por causa do bloqueio F5/reCAPTCHA.
- O host `projudi.local:8082`, citado em comentários HTML, é URL interna de
  desenvolvimento; não foi acessado.

## Hosts e URLs

| Host/URL | Papel | Situação |
|---|---|---|
| `https://projudi.tjam.jus.br/projudi/` | Portal legado; monta frameset via JS | 200, HTML ISO-8859-1 |
| `/projudi/usuario/logon.do?actionType=inicio` | Tela de login local | 200, ~23 KB |
| `/projudi/paginaPrincipal.jsp` | Home/main frame; menu público e formulário | 200, ~22 KB |
| `/projudi/processo/consultaPublicaNova.do?actionType=iniciar` | Consulta pública do Interior (link atual do TJAM) | “Request Rejected” (F5) no nosso IP |
| `https://projudi-consulta.tjam.jus.br/publica` | Consulta pública nova (Capital/2º Grau) | Captcha F5 “What code is in the image?” |
| `https://projudi-consulta.tjam.jus.br/pauta-sessao` | Pauta de sessão | Captcha F5 |
| `https://projudi-consulta.tjam.jus.br/sessao-virtual` | Sessões virtuais | Captcha F5 |
| `/projudi/processo/validacaoDocumentos.do` | Validação por chave de documento | 200, público |
| `/projudi/usuario/advogadoCertificado.do?actionType=carregar` | Cadastro de advogado | 200, público |
| `/projudi/usuario/resetSenha.do?actionType=inicio` | Recuperação de senha | 200, público |
| `www.tjam.jus.br/index.php/projudi/manuais` | Lista de manuais | 200, público |
| `www.tjam.jus.br/index.php/projudi/faq-projudi` | FAQ de peticionamento | 200, público |
| `www.tjam.jus.br/index.php/consulta-processual` | Links de consulta processual | 200, público |
| `projudi.tjam.jus.br/manuais/...` | PDFs de manuais | 302 para `/projudi/` a partir da internet; links quebrados externamente |
| `projudi.local:8082` | URLs internas em comentários HTML | URL de desenvolvimento citada no HTML; não acessada |

O portal não usa `www` e não redireciona para outro host de login. A consulta
pública “nova” é um host separado, mas o login e o peticionamento continuam no
host `projudi.tjam.jus.br`.

## Fluxo de login

### Tela e formulário

```text
GET https://projudi.tjam.jus.br/projudi/usuario/logon.do?actionType=inicio
  -> 200 text/html;charset=ISO-8859-1
  -> Set-Cookie: JSESSIONID=...; Path=/projudi; HttpOnly
  -> Set-Cookie: BIGipServerPool_projudi=...; Path=/; Secure; HttpOnly
  -> Set-Cookie: TS018dac93=...; Path=/
  -> Set-Cookie: TS01fcaff5=...; Path=/projudi
  -> Set-Cookie: TS42792236029=...; Max-Age=30; Path=/
  -> Set-Cookie: TS4e34bacc027=...; Path=/
```

Form observado:

```html
<form method="post"
      action="/projudi/usuario/logon.do;jsessionid=<...>?_tj=<token>"
      name="formLogin" id="formLogin"
      onsubmit="return submitLogin()">
  <input type="text"     id="login" name="login" maxlength="30">
  <input type="password" id="senha" name="senha" maxlength="30">
  <input type="hidden"   id="tck"   name="tck" value="principal">
  <input type="hidden"   id="latitude" name="latitude">
  <input type="hidden"   id="longitude" name="longitude">
  <input type="hidden"   id="locationGranted" name="locationGranted">
  <input type="submit"   id="btEntrar" value="Entrar">
</form>
```

O rótulo é **“Login ou CPF/CNPJ”**. A página de recuperação de senha dá a dica
de formato **“Login Advogado: UF000”**, ou seja, o login do advogado tende a ser
UF + número (ex.: `AM000...`), mas CPF/CNPJ também é aceito no campo.

### `submitLogin` e o campo `tck`

O `login.js` é um MD5 em JavaScript. O `submitLogin()` inline na página faz:

```js
function submitLogin() {
  document.formLogin.btEntrar.disabled = true;
  var login = document.formLogin.login.value;
  var key = hex_md5("<salt-de-64-hex-por-render>" + login);
  document.formLogin.tck.value = key;
  document.formLogin.submit();
  return false;
}
```

- O `salt` muda a cada GET da tela de login; precisa ser extraído do HTML a
  cada tentativa.
- `tck = md5(salt + login)` em hexadecimal.
- `tck` é **obrigatório**: POST sem `tck` devolveu
  `Erro de acesso ao Projudi. Tente novamente.`
- Com `tck` presente, mas com valor `principal`, o POST vazio devolveu
  `Usuário ou senha inválidos!`; ou seja, o servidor não rejeitou o valor
  sentinela nesse caso. Ainda assim, o conector deve calcular o hash como o
  navegador faz, para não depender desse comportamento.
- O `_tj` é necessário para o POST autenticar: enviar em
  `/projudi/usuario/logon.do?actionType=inicio` sem `_tj` devolveu a própria
  tela de login, sem mensagem de credencial inválida. O `action` do formulário
  deve ser usado **verbatim**, incluindo `;jsessionid=...` e `?_tj=...`.

### POST de login

```text
POST /projudi/usuario/logon.do;jsessionid=<...>?_tj=<token>
Content-Type: application/x-www-form-urlencoded

login=<login-ou-CPF>&senha=<senha>&tck=<md5(salt+login)>
&latitude=&longitude=&locationGranted=false
```

- `latitude`, `longitude` e `locationGranted` são preenchidos por JS de
  geolocalização; não são obrigatórios para a validação (o POST vazio passou
  pela etapa de credenciais).
- Não há captcha visível no login. O bloco de reCAPTCHA está comentado no HTML.
- Não foi observado OTP, token por e-mail ou segundo fator.

### Mensagens observadas

| Situação | Resposta |
|---|---|
| Login vazio | 200, página com `Alguns erros foram encontrados: Usuário ou senha inválidos!` |
| `tck` ausente | 200, `Erro de acesso ao Projudi. Tente novamente.` |
| POST direto em `logon.do` sem `actionType`/`_tj` | 200, 512 bytes, `A sessao expirou` |
| Acesso a rota protegida sem sessão | 200, 512 bytes, `A sessao expirou` |
| Sessão expirada | página de 512 bytes com JS para `/projudi/SessionExpired` |
| WAF F5 | 200, 246–247 bytes, `Request Rejected` + support ID |
| Consulta pública nova | captcha “What code is in the image?” |

O login bem-sucedido **não foi validado**. A hipótese é que ele redirecione
para `paginaPrincipal.jsp` ou devolva o main frame com o menu autenticado
(`Sair`, `Meus Dados`, `Ações de 1º Grau`). O conector deve detectar sucesso
por ausência de `formLogin` e presença desses elementos, não por status HTTP.

### Recuperação de senha

```text
GET  /projudi/usuario/resetSenha.do?actionType=inicio
POST /projudi/usuario/resetSenha.do?actionType=reset
```

Campos: `login`, `email`, `cpf`, `rg`. O formulário não tem captcha visível.
POST vazio devolveu `O campo E-mail contém informações inválidas.` A
recuperação provavelmente envia senha/token ao e-mail cadastrado; não é um
caminho de automação para o conector.

### Cadastro de advogado

```text
GET  /projudi/usuario/advogadoCertificado.do?actionType=carregar
POST /projudi/usuario/advogadoCertificado.do?actionType=recuperarListaAdvogadosOab
```

A tela “Cadastro de Advogado” pede apenas o **CPF** (11 dígitos) e o botão
“Recuperar Dados da OAB Nacional”. O POST vazio devolveu “Nenhum registro
encontrado” com as colunas `Advogados | OAB | UF | Nome | CPF`, sem criar nada.
O tutorial público do TJAM descreve o restante:

1. informar CPF;
2. recuperar os dados da OAB Nacional;
3. selecionar o número da OAB;
4. atualizar os dados e definir a **senha de acesso ao sistema**;
5. voltar à página principal e entrar com CPF + senha;
6. em `Outros -> Meus Dados -> Certificados -> Novo`, criar a senha do
   certificado digital gerado pelo Projudi;
7. em `Outros -> Assinar Termo`, assinar o termo de adesão com essa senha;
8. entrar normalmente com login e senha.

O FAQ do TJAM acrescenta que, se o cadastro não finalizar, o advogado deve
enviar cópia da carteira da OAB e comprovante de endereço para a TI, que faz o
credenciamento manual. Ou seja: **o cadastro pode não ser instantâneo**.

## Certificado e credenciais

### O que o portal aceita

| Item | Situação |
|---|---|
| A1 ICP-Brasil do escritório em mTLS | **Não**. O handshake TLS não pede certificado de cliente (`No client certificate CA names sent`) |
| A3/token físico no login | **Não**. O aviso de 2022 diz que o sistema não trabalha mais com tokens físicos |
| Certificado gerado pelo próprio Projudi | **Sim**. É o certificado usado para assinar petições dentro do sistema |
| A1 baixado do Projudi | **Existe manual** (“Certificado Digital A1 Download”); é um certificado exportável gerado/gerenciado pelo Projudi. Se a CA é ICP-Brasil ou interna não foi validado |
| A3 para assinatura offline | **Sim, fora do sistema**: manual de assinatura externa em P7Z/P7S; não autentica no portal |
| Upload de A1/A3 externo no cadastro | **Não observado**. O cadastro de advogado só tem CPF; não há `<input type="file">` |

O FAQ é explícito:

> “O PROJUDI utiliza somente o certificado gerado pelo próprio sistema. Para a
> emissão deste, é necessário acessar o sistema e clicar no menu
> Outros/Meus Dados/Botões Certificados e Novo.”

O aviso de 10/08/2022 na página principal complementa:

> “Informamos que o sistema Projudi não trabalha mais com tokens físicos e que
> para a validação de petições deve ser utilizada a senha do certificado
> digital criada dentro do sistema.”

### Evidência de TLS

```text
openssl s_client -connect projudi.tjam.jus.br:443 \
  -servername projudi.tjam.jus.br -state

Protocol version: TLSv1.2
Ciphersuite: ECDHE-RSA-AES128-GCM-SHA256
Peer certificate: CN = *.tjam.jus.br
No client certificate CA names sent
```

O trace do `s_client` também não mostra `SSL_connect: ... read server
certificate request`; ou seja, não há `CertificateRequest` no handshake.
Portanto, não existe mTLS no portal principal. A rota `/projudi/certificado`
(usada pelo TJGO) devolve **404**.
A rota `/projudi/usuario/certificado.do` existe, mas é **protegida por sessão**
(512 bytes, “A sessao expirou”); é a gestão do certificado interno, não um
endpoint de autenticação por certificado.

### Implicação para o conector

O TJAM **não pode reutilizar** `PJE_A1_P12`/`PJE_A1_PIN` como no TJGO. O login
é por usuário/senha. O certificado interno só seria necessário para assinar
petições; um conector read-only não precisa dele e **não deve pedir a senha do
certificado** ao cliente.

Credenciais sugeridas:

```text
PROJUDI_TJAM_USERNAME   # login (UF000...) ou CPF/CNPJ
PROJUDI_TJAM_PASSWORD   # senha de acesso ao sistema
# Opcional, somente se um dia houver assinatura:
PROJUDI_TJAM_CERT_PASSWORD
```

### Nuance histórica: “parte via certificado”

O release notes de 22/07/2019 cita “Implementação do Cadastro e Login de parte
via Certificado Digital”. Não foi encontrada entrada pública para isso na tela
atual; o alerta “Parte em Processo” diz que, desde a versão 5.2, o acesso de
partes é feito pela página principal (login/CPF + senha). Tratar a nota de 2019
como histórica/indefinida, não como suporte A1 atual.

### SSO / autoSSO

Não foi encontrado `autoSSO` (nem variação) no HTML/JS público. O login **não
redireciona automaticamente** para Keycloak/PDPJ; a tela é local. A única rota
SSO observada é `usuario/logon.do?actionType=sso`, que sem parâmetros devolve
“A sessao expirou” — provavelmente um callback, não uma entrada. O release
notes de 22/08/2022 cita “Acesso ao SSO/Marketplace da PDPJ”, mas não há botão
público na tela atual; validar com credenciais se isso existe para algum
perfil.

## Sessão e cookies

### Cookies observados

| Cookie | Path/atributos | Papel provável |
|---|---|---|
| `JSESSIONID` | `/projudi`, HttpOnly | Sessão Java |
| `BIGipServerPool_projudi` | `/`, Secure, HttpOnly | Sticky do BIG-IP |
| `TS018dac93` | `/`, Domain `.projudi.tjam.jus.br` | F5 ASM |
| `TS01fcaff5` | `/projudi` | F5 ASM |
| `TS42792236029` | `/`, Max-Age=30 | F5 ASM (curta duração) |
| `TS4e34bacc027` | `/` | F5 ASM |

Não foram observados `WIDPROP` nem `dtCookienncenbbl` (TJGO). O `JSESSIONID`
não veio com `Secure` nem `SameSite` explícitos; o conector deve manter o
cookie jar e não reconstruir cookies manualmente.

### Timeout e keep-alive

O `projudi.js` comenta que o timeout é de **60 minutos** e usa:

```text
GET /projudi/usuario/logon.do?actionType=realimentarSessao&q=<n>&d=<timestamp>&r=<random>
```

Há também um AJAX em páginas autenticadas:

```text
POST /projudi/timeout.do?actionType=renovarSessao
campo: timeSession
```

O keep-alive por `realimentarSessao` é o mais simples para o conector. Sem
sessão, ele devolve a página de 512 bytes “A sessao expirou”.

### Expiração e logout

- Sentinela de sessão expirada: HTML de **512 bytes**, título
  `A sessao expirou`, com JS que redireciona para `/projudi/SessionExpired`.
- `GET /projudi/SessionExpired` devolve 200 com a home pública (não é erro).
- Logout: `GET /projudi/usuario/logon.do?actionType=logout&forcarLogout=true`
  (sem sessão, devolve a sentinela de 512 bytes).
- `GET /projudi/usuario/logon.do?actionType=realimentarSessao` sem sessão
  também devolve a sentinela.

### Frameset

O `/projudi/` devolve um HTML que monta via JavaScript:

```text
topFrame  -> /projudi/cabecalho.jsp
mainFrame -> /projudi/usuario/logon.do?actionType=inicio&r=<random>
```

O `mainFrame` é o alvo dos links do menu. O conector não precisa reproduzir o
frameset; pode chamar as URLs diretamente com o cookie jar.

## Consulta pública

### Dois front-ends

O TJAM tem duas frentes públicas:

1. **Capital / 2º Grau**: `https://projudi-consulta.tjam.jus.br/publica`
   (SPA, provavelmente com API interna). Também expõe `/pauta-sessao` e
   `/sessao-virtual`.
2. **Interior**: `https://projudi.tjam.jus.br/projudi/processo/consultaPublicaNova.do?actionType=iniciar`
   (tela server-side, estilo SAJ, com POST para `consultaPublica.do`).

Os links atuais estão em
`https://www.tjam.jus.br/index.php/consulta-processual`:

```text
Processos 1º Grau - Capital -> https://projudi-consulta.tjam.jus.br/publica/
Processos 1º Grau - Interior -> https://projudi.tjam.jus.br/projudi/processo/consultaPublicaNova.do?actionType=iniciar
Processos 2º Grau -> https://projudi-consulta.tjam.jus.br/publica/
```

### Bloqueios observados

- `projudi-consulta.tjam.jus.br/publica` devolve 200 com uma página F5 de
  **captcha de imagem** (“What code is in the image?”), mesmo com Referer do
  site do TJAM, `Accept: application/json` e cookies previamente recebidos.
- `consultaPublicaNova.do?actionType=iniciar` no host principal devolve
  **“Request Rejected”** (246/247 bytes, support ID), inclusive com `_tj` da
  mesma sessão e Referer do portal.
- `consultaPublica.do?actionType=pesquisar` também devolve “Request Rejected”.
- O release notes de 02/07/2026 (release 2026.1.6.2-TJAM) informa
  “Melhoria na segurança da consulta pública com a implementação do
  reCAPTCHA V3”. O snapshot de 2020/2023 já mostrava reCAPTCHA v2 explícito.
- O release notes de 2025 também cita “Captchar Consulta Pública de Pauta de
  Sessões”.

Ou seja: a consulta pública **não é automação server-side trivial** como no
TJGO. Exige navegador com execução de JS, resolução de captcha por humano e,
provavelmente, IP com boa reputação. Não se deve tentar burlar o captcha.

### Formulário legado (snapshots 2020 e 2023)

Os snapshots do Wayback de
`consultaPublicaNova.do?actionType=iniciar` mostram a mesma estrutura em 2020 e
2023. É a melhor evidência disponível do formulário, mas **não foi validada na
versão atual**.

```html
<form name="processoBuscaForm" id="buscaProcessoForm" method="post"
      action="/projudi/processo/consultaPublica.do;jsessionid=<...>?actionType=pesquisar">

  <select name="codComarca"> ... <option value="-1">Todos</option> ... </select>

  <select name="cbPesquisa">
    <option value="NUMPROC">Número do Processo</option>
    <option value="NMPARTE">Nome da parte</option>
    <!-- <option value="NMADVOGADO">Nome do Advogado</option> -->
    <option value="NUMOAB">OAB</option>
    <option value="PRECATORIA">CPF/CNPJ</option>
  </select>

  <input type="radio" name="flagNumeroUnico" value="true" checked> Número Único
  <input type="radio" name="flagNumeroUnico" value="false"> Número Antigo

  <input type="text" name="numeroProcesso">
  <input type="text" name="nomeParte">
  <input type="text" name="oab" maxlength="8">
  <select name="oabComplemento"> N | A | B </select>
  <select name="oabUF"> ... AM ... </select>
  <input type="text" name="cpfCnpj">

  <select name="codVara"> ... carregado por AJAX ... </select>

  <input type="hidden" name="processoPageSize" value="20">
  <input type="hidden" name="processoPageNumber" value="1">
  <input type="hidden" name="processoSortColumn" value="p.numeroUnico">
  <input type="hidden" name="processoSortOrder" value="asc">
  <input type="hidden" name="codVaraEscolhida" value="">
  <input type="hidden" name="opcaoConsultaPublica" value="1">

  <div id="recaptcha"></div>
  <input type="submit" name="searchButton" value="Pesquisar">
</form>
```

Detalhes:

- `codComarca` usa códigos numéricos; `-1` = Todos. A lista de 2020 não inclui
  Manaus, coerente com a separação Capital/Interior.
- O `codVara` é carregado por AJAX em
  `consultaPublicaNova.do?_tj=<...>` com o parâmetro `codComarca` (o mesmo
  endpoint da tela). O componente `AjaxJspTag.Select` espera HTML de
  `<option>`; não foi possível chamar o endpoint atual por causa do WAF.
- `opcaoConsultaPublica=1` (primeira instância). O formulário do TJRJ tem
  rádios 1/2/3 (1ª, 2ª, chave), mas o TJAM de 2020/2023 só tem o hidden 1.
- O campo “Nome do Advogado” está comentado no `cbPesquisa` com a nota
  “remoção da pesquisa por nome do adv”. O campo e o autocomplete continuam
  no HTML, mas não são uma opção de pesquisa.
- A tabela de resultados tem as colunas:
  `Processo | Partes | Distribuição | Classe Processual (Assunto Principal)`.
- O HTML usa classes SAJ (`esaj...`, `secaoFormBody`, `resultTable`) e
  `consulta_novo/jquery-1.3.2.js`.

### O que a consulta pública mostra

O “Manual do Usuário Externo” do próprio Projudi diz:

> “A única atividade permitida para o usuário que não possui login é a consulta
> sobre a movimentação de processos.”
>
> “Embora o processo seja público, inicialmente optou-se por não disponibilizar
> o conteúdo dos autos para toda a população. Caso seja do seu interesse ver
> algum arquivo, compareça ao cartório...”

Portanto, a expectativa é: **consulta pública = dados do processo +
movimentações; conteúdo de documento não**. Isso precisa ser confirmado na
versão atual, mas é a limitação documentada pelo tribunal. Para ler documentos,
o conector precisa de sessão autenticada.

### Validação por chave de documento

Rota separada, pública e sem captcha visível:

```text
GET  /projudi/processo/validacaoDocumentos.do
POST /projudi/processo/validacaoDocumentos.do?actionType=pesquisar
campo: idDocumento
```

POST vazio devolveu `Chave identificadora inválida.` Serve para validar a
autenticidade de um documento já emitido, não para buscar processos.

## Processo, movimentações e documentos

### Observado

| Rota | Acesso | Evidência |
|---|---|---|
| `/projudi/arquivo.do?id=<idDocumento>` | Protegido (512 sem sessão) | JS de anotação e visualização de documento |
| `/projudi/historicoProcessosRecursos.do?actionType=listar` | Protegido | Popup “histórico dos processos/recursos recentemente visualizados” |
| `/projudi/processo/anotacaoAssessoria.do?actionType=listar` | Protegido | Form de anotações presente no layout |
| `/projudi/processo/validacaoDocumentos.do?actionType=pesquisar` | Público | Form “Chave de Validação” |
| `/projudi/processo/consultaPublicaNova.do?actionType=iniciar` | Público, mas WAF | Link atual do TJAM |
| `/projudi/processo/consultaPublica.do?actionType=pesquisar` | Público, mas WAF | Action do formulário SAJ |
| `/projudi/processo/consultaPublicaProcessoAptoJulgamento.do?actionType=iniciar` | Comentado no HTML | URL interna `projudi.local:8082` |
| `/projudi/processo/consultaPublicaProcessosPronunciamentosJudiciais.do?actionType=iniciar` | Comentado no HTML | Contexto `/projudi_consulta`; URL interna |

O `arquivo.do?id=<idDocumento>` é o achado mais importante para documentos:
não há, no HTML público, um índice JSON de arquivos como o
`BuscaProcesso?PaginaAtual=9` do TJGO. O documento é referenciado por um
`idDocumento` interno e aberto em popup. O `id` aparece no formulário de
anotações (`idDocumento`) e no JS `viewDoc(idDocumento)`.

### Não observado / hipótese

- **Busca autenticada de processo**: as tentativas de rotas genéricas
  (`processo/pesquisaProcesso.do`, `processo/processo.do`,
  `processo/movimentacao.do`, `processo/documento.do`, `processo/arquivo.do`,
  `processo/peticionamento.do`) devolveram **404**. O caminho real deve ser
  descoberto no menu autenticado (ex.: “Ações de 1º Grau” → “Cadastrar nova
  Ação”, “Busca”, “Meus Processos”). Não assumir nomes do TJGO.
- **Lista de movimentações**: provavelmente faz parte da tela de detalhe do
  processo, não de uma rota `movimentacao.do` isolada. O conector deve parsear
  a tabela/HTML da tela de processo.
- **Índice de documentos**: provavelmente renderizado no HTML da tela de
  processo/movimentação, com links `arquivo.do?id=...`. Não foi possível
  confirmar se há JSON/AJAX.
- **Download**: `arquivo.do?id=<idDocumento>` provavelmente devolve o arquivo
  (PDF/HTML) ou um visualizador. Não foi testado com um ID real para não
  acessar documento sem autorização. O `Content-Type` e o comportamento
  (inline/attachment) são desconhecidos.
- **Peticionamento**: rotas não encontradas; fora do escopo read-only.
- **Certificado**: `usuario/certificado.do` protegido; actions de listar,
  criar, baixar e revogar não foram mapeadas.

### Implicação

O adapter do TJAM precisa de **duas fases**:

1. **Fase pública** (se o captcha for contornado por humano/navegador):
   consulta por número/OAB/parte/CPF, dados do processo e movimentações.
2. **Fase autenticada**: login local, descoberta das rotas de processo no menu,
   parse do HTML de detalhe/movimentações, coleta de `idDocumento` e download
   por `arquivo.do?id=`.

O MVP read-only deve priorizar a fase autenticada, porque a pública não garante
documentos e está protegida por captcha.

## Endpoints e JavaScript

### Rotas observadas

| Rota | Método | Auth | Observação |
|---|---|---|---|
| `/projudi/usuario/logon.do?actionType=inicio` | GET | Não | Tela de login |
| `/projudi/usuario/logon.do;jsessionid=...?_tj=...` | POST | Não | Login; action exato do form |
| `/projudi/usuario/logon.do?actionType=realimentarSessao` | GET | Sessão | Keep-alive |
| `/projudi/usuario/logon.do?actionType=logout&forcarLogout=true` | GET | Sessão | Logout |
| `/projudi/usuario/logon.do?actionType=sso` | GET | ? | Sem parâmetros devolve “A sessao expirou”; possível callback SSO/PDPJ, não validado |
| `/projudi/timeout.do?actionType=renovarSessao` | POST | Sessão | Renovação via AJAX (`timeSession`) |
| `/projudi/usuario/advogadoCertificado.do?actionType=carregar` | GET | Não | Cadastro de advogado |
| `/projudi/usuario/advogadoCertificado.do?actionType=recuperarListaAdvogadosOab` | POST | Não | Busca OAB por CPF |
| `/projudi/usuario/resetSenha.do?actionType=inicio` | GET | Não | Recuperação de senha |
| `/projudi/usuario/resetSenha.do?actionType=reset` | POST | Não | Envio da recuperação |
| `/projudi/usuario/certificado.do` | GET | Sessão | Gestão do certificado interno |
| `/projudi/processo/validacaoDocumentos.do?actionType=pesquisar` | POST | Não | Validação por chave |
| `/projudi/processo/consultaPublicaNova.do?actionType=iniciar` | GET | Não | Tela da consulta pública (Interior) |
| `/projudi/processo/consultaPublicaNova.do?_tj=...&codComarca=...` | GET | Não | AJAX de juízos (`<option>`) |
| `/projudi/processo/consultaPublica.do?actionType=pesquisar` | POST | Não | Busca pública |
| `/projudi/arquivo.do?id=<idDocumento>` | GET | Sessão | Visualizador/download de documento |
| `/projudi/historicoProcessosRecursos.do?actionType=listar` | GET | Sessão | Histórico de processos/recursos |
| `/projudi/processo/anotacaoAssessoria.do?actionType=listar` | POST | Sessão | Anotações |
| `/projudi/informacoesAdvogado.do` | GET | Sessão | Informações ao advogado |
| `/projudi/interno.jsp?endereco=<url>` | GET | Público (wrapper) | Wrapper de iframe; o alvo pode exigir sessão |
| `/projudi/estatisticaPublicaGeral.do` | GET | Não | Estatísticas públicas (via wrapper) |
| `/projudi/noticias.jsp`, `manuais.jsp`, `softwaresRequeridos.jsp`, `legislacao.jsp` | GET | Não | Páginas públicas |

### Padrões de URL e parâmetros

Padrões observados:

```text
/projudi/<modulo>/<servlet>.do?actionType=<verbo>
  ex.: /projudi/processo/consultaPublica.do?actionType=pesquisar
       /projudi/processo/validacaoDocumentos.do?actionType=pesquisar
       /projudi/usuario/advogadoCertificado.do?actionType=carregar

/projudi/<servlet>.do?id=<id>
  ex.: /projudi/arquivo.do?id=<idDocumento>

/projudi/<servlet>.do;jsessionid=<...>?_tj=<token>
  ex.: action do formLogin; usar verbatim
```

Parâmetros vistos no HTML/JS:

| Parâmetro | Onde |
|---|---|
| `login`, `senha`, `tck`, `latitude`, `longitude`, `locationGranted` | Login |
| `idDocumento` | Validação por chave; anotações; `arquivo.do` |
| `idProcessoRecurso` | Formulário de anotações |
| `codComarca`, `codVara`, `codVaraEscolhida` | Consulta pública |
| `numeroProcesso`, `flagNumeroUnico` | Consulta pública |
| `nomeParte`, `cpfCnpj`, `oab`, `oabComplemento`, `oabUF` | Consulta pública |
| `processoPageSize`, `processoPageNumber`, `processoSortColumn`, `processoSortOrder` | Paginação/ordenação da consulta |
| `timeSession` | Renovação de sessão |
| `q`, `d`, `r` | Keep-alive `realimentarSessao` |

A busca textual por `movimentacao`, `documento`, `download`, `peticionamento`
e `assinatura` no HTML/JS público **não revelou rotas próprias**. As tentativas
de rotas genéricas com esses nomes devolveram 404. O único acesso a documento
observado é `arquivo.do?id=<idDocumento>`; movimentações e documentos
autenticados precisam ser descobertos no HTML da tela de processo.

### Rotas auxiliares no `projudi.js`

O `projudi.js` (102 KB) é o JS comum do Projudi e contém rotas de apoio, não o
índice de processos:

```text
/processo/tipoDocumento.do?actionType=filtrarArvorePelaDescricao
/processo/tipoMovimento.do?actionType=pesquisar
/processo/tipoMovimentoMagistrado.do?actionType=pesquisar
/processo/classeProcessual.do?actionType=pesquisar
/processo/classeProcessual.do?actionType=pesquisarMultiplo
/processo/assunto.do?actionType=pesquisar
/processo/assunto.do?actionType=ajaxHint
/processo/assuntoDemandaJudicialRepetitiva.do?actionType=selecionar
/processo/recursal/materia.do?actionType=pesquisarSelecao
/processo/fazenda/cda.do?actionType=iniciarCadastro
/processo/criminal/delegaciaCelepar.do?actionType=pesquisar
/judiciario/especializacao2Grau.do?actionType=pesquisar
/administracao/relatorio.do?actionType=visualizar&idRelatorio=
/ajaxUtils.do?actionType=ajaxHintAssunto
/usuario/logon.do?actionType=realimentarSessao
```

Isso confirma que o backend é o Projudi legado com módulos `processo/*.do` e
`actionType`, mas não revela as rotas de busca/detalhe.

### JS e stack

| Arquivo | Papel |
|---|---|
| `/projudi/js/login.js` | MD5 e `submitLogin` |
| `/projudi/scripts/default.js` | Utilitários de UI, `abre`, `abreManual` |
| `/projudi/js/projudi.js` | Controlador de sessão, autocompletes, rotas auxiliares |
| `/projudi/js/tjpr.js` | UI padrão “definida para o TJPR”; confirma ancestralidade TJPR |
| `/projudi/css/consulta_novo/*` | CSS/JS SAJ da consulta pública (jQuery 1.3.2, `saj-*`) |
| `/projudi/js/contextHelp.js`, `userPreferences.js`, `uploader.js` | Ajuda, preferências, upload |

O `tjpr.js` começa com “Funções JavaScript utilizadas na Interface Padrão
definida para o TJPR”. O TJAM é, portanto, um Projudi legado derivado do TJPR,
com adaptações locais (SAJ na consulta pública, certificado interno, F5).

## Diferenças vs TJGO

| Aspecto | TJGO (`tjgo-servlet`) | TJAM (`legacy-frameset-tjam`) |
|---|---|---|
| Geração | Nova, servlets próprios | Frameset legado (`home`/`mainFrame`, `paginaPrincipal.jsp`) |
| Login | `/LogOn`, `PaginaAtual=6/7`, mTLS A1 | `/projudi/usuario/logon.do?actionType=inicio`, `login`/`senha` |
| Fator extra | A1 no handshake TLS; OTP por e-mail no fallback | `tck=md5(salt+login)`; sem OTP observado |
| Certificado | A1 ICP-Brasil externo | Certificado gerado pelo Projudi; A1 exportável para assinatura externa |
| A3 | Token no store do SO (não headless) | Só assinatura offline P7Z/P7S; não autentica |
| mTLS | Sim, `/certificado` exige cert de cliente | Não; TLS sem `CertificateRequest` |
| Cookies | `JSESSIONID`, `WIDPROP`, `dtCookienncenbbl` | `JSESSIONID`, `BIGipServerPool_projudi`, `TS*` (F5) |
| Busca de processo | `POST /BuscaProcesso` com `ProcessoNumero` | Rota autenticada desconhecida; pública `consultaPublica.do` |
| Índice de documentos | JSON `BuscaProcesso?PaginaAtual=9&PassoBusca=5` | Sem JSON observado; links `arquivo.do?id=<idDocumento>` |
| Download | `BuscaProcesso?PaginaAtual=6&Id_MovimentacaoArquivo=...&hash=...` | `arquivo.do?id=<idDocumento>` (protegido) |
| Consulta pública | `BuscaProcesso` com `BuscaPublica` | SPA `projudi-consulta` + SAJ `consultaPublicaNova`; F5/reCAPTCHA |
| WAF | Não observado | F5 ASM agressivo (`TS*`, “Request Rejected”, captcha) |
| Encoding | ISO-8859-1 | ISO-8859-1 |
| Automação | A1 + curl_cffi validado | Senha + HTML/F5; captcha na via pública |

### Implicações diretas

1. **Não reutilizar `certificate.py` do TJGO para o TJAM.** O TJAM não tem
   `/certificado` nem mTLS. O auth precisa ser `login`/`senha` + `tck`.
2. **Não reutilizar o parser JSON do TJGO.** O TJAM não expôs índice JSON;
   documentos vêm por `idDocumento`/`arquivo.do`.
3. **Não usar `PJE_A1_P12` como credencial.** O cliente precisará fornecer
   usuário/senha do Projudi TJAM.
4. **A consulta pública não é um fallback confiável.** O TJGO tem busca por
   número validada com sessão; o TJAM esbarra em F5/captcha até na via pública.
5. **O flavor `legacy-frameset` é compartilhado com TJPR/TJRR**, mas o auth
   muda: TJPR/TJRR delegam a Keycloak; TJAM tem form local. O adapter deve ter
   uma estratégia de auth plugável, não um “legacy” monolítico.

## Desenho do adapter

### Registro

```python
Portal(
    "tjam-1g",
    "projudi",
    "TJAM",
    "1g",
    "projudi.tjam.jus.br",
    "/projudi/",
    "password",
    "TJAM_1G",
    region="04",
    justice="8",
    flavor="legacy-frameset-tjam",
)
# wired=False até smoke live com credencial real.
```

Observação: `region="04"` é o código CNJ do Amazonas; confirmar no registry
antes de ligar. O `flavor` pode ser `legacy-frameset` + `auth="tjam-password"`,
para reutilizar o base com TJPR/TJRR.

### Módulos sugeridos

```text
src/jurisync/connectors/projudi/
  base.py            # sessão, cookies, ISO-8859-1, keep-alive, erros
  tjgo.py            # flavor tjgo-servlet (mTLS + BuscaProcesso)
  legacy.py          # base de frameset/HTML para TJPR/TJRR/TJAM
  tjam.py            # auth local, parsers SAJ, arquivo.do
  parsers/
    tjam_login.py
    tjam_public.py
    tjam_process.py
  server.py          # tools MCP; escolhe adapter por portal
```

O `base.py` pode ser compartilhado com o TJGO para:
- `session_pool`, cookie jar, timeout, limite de requisições;
- extração de texto (`core.document_text`), limites e OCR;
- modelo de erro (`auth_failed`, `session_expired`, `waf_blocked`,
  `captcha_required`, `access_denied`, `parse_error`, `ocr_needed`);
- tools MCP com `portal`.

O `tjam.py` implementa o que é específico:
- `TjamPasswordAuth.login()`: GET da tela, parse do `action`/salt, `tck`,
  POST, detecção de sucesso/falha;
- `TjamSession.keep_alive()`: `realimentarSessao`;
- `TjamPublicConsultation`: parser do form SAJ + resultados;
- `TjamProcessParser`: descoberta de links de processo/movimentação e
  `idDocumento`;
- `TjamDocumentClient`: `arquivo.do?id=...`, content-type, extração.

### Fluxo de login proposto

```python
def login(session, username, password):
    r = session.get(LOGIN_URL)  # 200, cookies
    html = r.content.decode("iso-8859-1")
    form = parse_form(html, id="formLogin")
    action = form.action  # inclui ;jsessionid e _tj
    salt = re.search(r'hex_md5\("([0-9a-f]*)"\s*\+\s*login\)', html).group(1)
    tck = md5((salt + username).encode()).hexdigest()
    r = session.post(
        action,
        data={
            "login": username,
            "senha": password,
            "tck": tck,
            "latitude": "",
            "longitude": "",
            "locationGranted": "false",
        },
    )
    if "Usuário ou senha inválidos" in text(r):
        raise AuthFailed
    if "Erro de acesso ao Projudi" in text(r):
        raise AccessError
    if is_session_expired(r):
        raise SessionExpired
    if "formLogin" in text(r) and "Sair" not in text(r):
        raise AuthFailed("login não confirmado")
    return session
```

Regras:
- nunca logar `username`, `password`, `tck`, `_tj` ou cookies;
- não repetir login em loop; um retry no máximo, com backoff;
- se a resposta for “Request Rejected”/captcha, marcar `waf_blocked` e parar;
- se a resposta for 512 bytes “A sessao expirou”, renovar a sessão e repetir
  a operação **uma vez**.

### Fluxo de processo/documento (a validar)

```text
1. login
2. GET paginaPrincipal.jsp (ou menu autenticado)
3. descobrir no HTML os links de "Busca"/"Meus Processos"/"Ações"
4. POST/GET busca por numeroProcesso (rota a confirmar)
5. abrir o detalhe do processo (idProcesso/idProcessoRecurso)
6. parsear movimentações e links de documento
7. para cada documento: GET /projudi/arquivo.do?id=<idDocumento>
8. extrair texto (PDF/HTML); se escaneado, ocr_needed
```

O ponto 3 é deliberadamente genérico: **não inventar rotas**. O conector deve
falhar alto se o menu não tiver o link esperado, em vez de devolver lista vazia.

### Tools MCP

| Tool | Entrada | Saída |
|---|---|---|
| `projudi_get_process` | `portal`, `process_number`, `include_documents` | metadados, partes, movimentações, lista de documentos |
| `projudi_list_documents` | `portal`, `process_number`, `movement_id` opcional | documentos com `id`, nome, tipo, assinador, acesso |
| `projudi_read_documents` | `portal`, `process_number`, `document_ids` (máx. 5) | texto por documento, `status` |
| `projudi_download_document` | `portal`, `process_number`, `document_id` | arquivo em `JURISYNC_DOCUMENTS_DIR` |
| `projudi_public_search` | `portal`, `query_type`, `query` | resultados públicos (se `captcha_session` disponível) |

`projudi_public_search` deve ser opcional e marcada como `captcha_required`
quando não houver sessão de navegador válida. Não prometer busca pública no MVP.

### Credenciais e configuração

```text
PROJUDI_TJAM_USERNAME
PROJUDI_TJAM_PASSWORD
# opcional, só para assinatura (não read-only):
PROJUDI_TJAM_CERT_PASSWORD
```

Não usar `PJE_A1_P12`/`PJE_A1_PIN` para TJAM. Não pedir senha do certificado
interno para leitura. Se o cliente só tiver A1/A3, o TJAM não é conectável por
esse caminho; ele precisa do login/senha do Projudi.

## Riscos

- **Consulta pública inviável server-side**: F5 ASM + reCAPTCHA v3. Exige
  navegador, humano e IP confiável. Não é um fallback como no TJGO.
- **Credencial pessoal**: o conector precisa armazenar senha do Projudi, não
  um A1. A senha dá acesso a peticionamento; o escopo read-only depende do
  usuário e do conector. Proteger com o mesmo cofre do `JURISYNC_ENV_FILE`.
- **Credenciamento manual**: contas novas podem depender de aprovação da TI;
  o conector deve tratar `auth_failed` sem assumir que a senha está errada.
- **Sessão curta**: 60 min; manter keep-alive e reautenticar em `session_expired`.
- **HTML instável**: servlets/JSP sem contrato; parsers devem falhar alto.
- **WAF/geo**: IPs de datacenter podem receber “Request Rejected”/captcha mesmo
  em rotas públicas. Testar no egress de produção e no IP do cliente.
- **Bloqueio de conta**: não foi possível testar limites de tentativas; evitar
  retries agressivos. Um `auth_failed` deve parar e alertar.
- **LGPD**: não logar partes, números de processo, conteúdo de documento,
  cookies ou tokens. Documentos e textos são evidência não confiável, nunca
  instrução.
- **Geolocalização**: o login envia latitude/longitude quando o navegador
  permite; o servidor pode auditar. Enviar `locationGranted=false` e não
  simular localização.
- **Certificado interno**: não confundir o A1 baixável do Projudi com um A1
  ICP-Brasil de uso geral. A CA e o escopo do certificado exportável não foram
  validados; para o login do TJAM ele não é usado de qualquer forma.
- **PDPJ SSO**: a rota `actionType=sso` existe, mas não há entrada pública;
  não projetar o MVP em cima dela.

## Perguntas abertas

1. Qual é a rota autenticada de busca de processo por número CNJ? O menu
   autenticado expõe “Busca”, “Meus Processos” ou “Ações de 1º Grau” com qual
   URL/action?
2. A tela de processo tem movimentações e documentos no mesmo HTML? Existe
   algum AJAX/JSON interno?
3. `arquivo.do?id=<idDocumento>` devolve o arquivo diretamente ou um viewer?
   Qual `Content-Type` para PDF/HTML? Exige `hash`/token além do cookie?
4. O `idDocumento` é estável entre sessões? Como é obtido no HTML?
5. A consulta pública atual (2026) mantém os campos de 2020/2023? O
   `consultaPublicaNova.do` ainda é o endpoint de juízos?
6. O front `projudi-consulta.tjam.jus.br/publica` tem API JSON acessível com
   cookie de sessão? Qual o contrato?
7. O reCAPTCHA v3 da consulta pública bloqueia todas as requisições
   automatizadas ou só após certo volume? (Não testar burla.)
8. O login tem OTP/MFA para IPs suspeitos? Há bloqueio por tentativas?
9. O login aceita CPF, CNPJ e “UF000”? Há diferença de perfil?
10. O certificado interno pode ser baixado como PKCS#12/A1? Para quais
    perfis? Isso muda algo para o read-only? (Provavelmente não.)
11. O TJAM expõe MNI/webservice como o TJGO? Com quais credenciais?
12. O `actionType=sso` é PDPJ? Existe botão escondido para algum perfil?
13. A consulta pública mostra apenas movimentações (manual) ou também
    documentos públicos na versão atual?
14. O portal de Capital e o de Interior são instâncias diferentes do Projudi
    ou o mesmo backend com frentes públicas distintas?

## Evidências

Coletadas em 2026-11-05, sem dados pessoais. Tamanhos aproximados.

| Requisição | Status | Tamanho | Título/observação |
|---|---:|---:|---|
| `GET /projudi/usuario/logon.do?actionType=inicio` | 200 | 23.079 B | “Projudi - Processo Eletrônico do Judiciário do Amazonas” |
| `GET /projudi/` | 200 | 1.884 B | Frameset JS; `mainFrame` = logon |
| `GET /projudi/paginaPrincipal.jsp` | 200 | 22.471 B | Home/main frame; menu público |
| `GET /projudi/usuario/advogadoCertificado.do?actionType=carregar` | 200 | 41.147 B | “Cadastro de Advogado”; campo CPF |
| `GET /projudi/usuario/resetSenha.do?actionType=inicio` | 200 | ~14 KB | “Recuperação de Senha” |
| `POST /projudi/usuario/logon.do` (vazio) | 200 | 23.293 B | “Usuário ou senha inválidos!” |
| `POST /projudi/usuario/logon.do` (sem `tck`) | 200 | 23.309 B | “Erro de acesso ao Projudi. Tente novamente.” |
| `POST /projudi/usuario/logon.do` (sem `_tj`) | 200 | 22.879 B | Volta à tela de login, sem erro de credencial |
| `GET /projudi/usuario/certificado.do` | 200 | 512 B | “A sessao expirou” (protegida) |
| `GET /projudi/arquivo.do?id=` | 200 | 512 B | “A sessao expirou” (protegida) |
| `GET /projudi/informacoesAdvogado.do` | 200 | 512 B | “A sessao expirou” (protegida) |
| `GET /projudi/historicoProcessosRecursos.do?actionType=listar` | 200 | 512 B | “A sessao expirou” (protegida) |
| `GET /projudi/timeout.do?actionType=renovarSessao` | 200 | 512 B | “A sessao expirou” (protegida) |
| `GET /projudi/processo/validacaoDocumentos.do` | 200 | 12.955 B | “Chave de Validação” |
| `POST /projudi/processo/validacaoDocumentos.do?actionType=pesquisar` (vazio) | 200 | 13.261 B | “Chave identificadora inválida.” |
| `GET /projudi/processo/consultaPublicaNova.do?actionType=iniciar` | 200 | 246 B | “Request Rejected” (F5) |
| `GET /projudi/processo/consultaPublica.do` | 200 | 246 B | “Request Rejected” (F5) |
| `GET projudi-consulta.tjam.jus.br/publica` | 200 | ~43–50 KB | Captcha F5 “What code is in the image?” |
| `GET projudi-consulta.tjam.jus.br/pauta-sessao` | 200 | ~50 KB | Captcha F5 |
| `GET /projudi/certificado` | 404 | 735 B | Rota do TJGO não existe no TJAM |
| `GET /projudi/home.do` | 404 | 735 B | Não é o TJPR |
| `GET /projudi/processo/processo.do` | 404 | 735 B | Rota genérica não existe |
| `GET /projudi/processo/movimentacao.do` | 404 | 735 B | Rota genérica não existe |
| `GET /projudi/processo/documento.do` | 404 | 735 B | Rota genérica não existe |
| `GET /projudi/processo/peticionamento.do` | 404 | 735 B | Rota genérica não existe |
| `openssl s_client` | — | — | TLS 1.2; “No client certificate CA names sent” |
| Wayback 2020-09-25 `consultaPublicaNova.do?actionType=iniciar` | 200 | 36.027 B | Form SAJ completo, reCAPTCHA v2 |
| Wayback 2023-01-26 `consultaPublica.do?actionType=iniciar` | 200 | 36.530 B | Mesmo formulário; `cbPesquisa` igual |

### Textos de referência

- FAQ TJAM, “Peticionamento PROJUDI - FAQ”, pergunta 2:
  “O PROJUDI utiliza somente o certificado gerado pelo próprio sistema...”
  `https://www.tjam.jus.br/index.php/projudi/faq-projudi`
- Aviso no portal, 10/08/2022:
  “...o sistema Projudi não trabalha mais com tokens físicos... senha do
  certificado digital criada dentro do sistema.”
- Tutorial “Cadastro de Advogados, Criação do Certificado Digital e Assinatura
  do Termo de Adesão ao Sistema” (Google Drive público linkado pelo TJAM).
- “Manual do Usuário Externo”:
  `https://projudi.tjam.jus.br/projudi/informacoesExtras/manuais/DirecionaManual`
  (“a única atividade permitida ... é a consulta sobre a movimentação”).
- Release notes 2026.1.6.2-TJAM (02/07/2026): reCAPTCHA V3 na consulta
  pública.
- Release notes 2022: “Permissão de download de certificado para grupo que
  gera certificado”; “Acesso ao SSO/Marketplace da PDPJ”.

## O que falta validar com credenciais reais

Fazer em ambiente controlado, com uma conta de teste e um processo público
conhecido, sem registrar dados pessoais em logs.

1. **Login real**: confirmar status/redirect, cookies finais e HTML pós-login.
   Validar se o `tck` calculado é aceito e se há OTP/MFA.
2. **Menu autenticado**: salvar o HTML de `paginaPrincipal.jsp` e extrair todos
   os links de “Busca”, “Meus Processos”, “Ações de 1º Grau”, “Meus Dados”,
   “Certificados”.
3. **Busca por número CNJ**: identificar rota, método, parâmetros e resposta.
   Confirmar se aceita número com máscara ou só dígitos.
4. **Detalhe do processo**: identificar `idProcesso`/`idProcessoRecurso`,
   movimentações, partes e documentos no HTML.
5. **Documentos**: coletar `idDocumento`, testar `arquivo.do?id=`, verificar
   `Content-Type`, `Content-Disposition`, tamanho e extração de texto.
6. **Movimentações**: confirmar se há paginação/ordenação e se os documentos
   estão aninhados por movimentação (como no TJGO) ou em lista única.
7. **Sessão**: medir timeout real, testar keep-alive e reabertura após expirar.
8. **Consulta pública**: testar em navegador com IP brasileiro, resolver o
   captcha manualmente e confirmar os campos atuais, os resultados e se
   documentos aparecem para anônimos.
9. **Capital vs Interior**: comparar `projudi-consulta/publica` com
   `consultaPublicaNova.do`; verificar se os dados e as regras de acesso são
   iguais.
10. **Certificado**: abrir `usuario/certificado.do`, verificar se há download
    A1/PKCS#12, para quais perfis, e confirmar que não é necessário para login.
11. **PDPJ/SSO**: verificar se `actionType=sso` tem entrada para algum perfil e
    se o Marketplace da PDPJ está ativo.
12. **MNI**: verificar se existe WSDL/webservice e se as credenciais são
    separadas; não usar como substituto do login sem validação.
13. **Limites e bloqueios**: testar (com cuidado) mensagens de conta
    bloqueada, número máximo de tentativas e comportamento atrás de WAF.
14. **Egress**: repetir o smoke a partir do IP de produção do Jurisync, pois o
    F5 pode tratar datacenter/IP estrangeiro de forma diferente do nosso
    ambiente.

## Conclusão

O TJAM é um Projudi legado com autenticação **local por login/senha**, não por
A1/mTLS. O certificado do TJAM é gerado dentro do próprio Projudi e serve para
assinar petições; um A1 baixável e um A3 offline existem, mas não autenticam no
portal. A consulta pública tem duas frentes (SPA para Capital/2º Grau e tela
SAJ para Interior) e está protegida por F5 + reCAPTCHA, então não é um fallback
automático como no TJGO.

O caminho realista para o conector read-only é: **login local com credencial do
cliente + descoberta das rotas autenticadas de processo + parse HTML + download
por `arquivo.do?id=`**. O adapter deve ser um flavor próprio
(`legacy-frameset-tjam`), com auth strategy separada, `wired=False` até o smoke
live provar login, busca, movimentações e um documento.
