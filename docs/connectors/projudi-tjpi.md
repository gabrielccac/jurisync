# Projudi TJPI — exploração pública e plano do adapter `tjpi-publico`

Levantamento ao vivo em **2026-11-05**, read-only, **sem credenciais**, sem
login, sem cadastro e sem enviar formulário com dados pessoais. O objetivo foi
mapear o suficiente para decidir se o Projudi do TJPI cabe no conector
`projudi` do Jurisync e qual adapter separado ele exige.

Sistema: `https://projudi.tjpi.jus.br/projudi` — **Projudi 1.9.8 (CNJ BRASIL)**,
build de **22/11/2011**, Tomcat **6.0.20**, HTML ISO-8859-1, JSP/servlets,
jQuery 1.3.1. Não é o Projudi "servlet novo" do TJGO.

## Resumo

| Capacidade | Situação |
|---|---|
| Login por usuário/senha | **Existe**; form local `POST /projudi/publico/Logon` |
| Login por certificado A1/mTLS | **Não**; o servidor TLS não pede certificado de cliente |
| PDPJ/SSO | **Não observado**; `/LogOnSSO` e `/certificado` dão 404 |
| OTP/MFA | **Não observado** no form; desconhecido depois do login |
| Recuperação de senha | **Existe**; login + data de nascimento, sem captcha/OTP |
| Cadastro de advogado | **Manual antigo**; Portal do Advogado hoje 404; JECC desabilitado para novas distribuições |
| Consulta pública | **Existe, com CAPTCHA**; `GET/POST /projudi/publico/buscas/ProcessosParte` |
| Busca por número/OAB | **Mesmo formulário**; OAB + complemento; resultado não validado por causa do CAPTCHA |
| Detalhe do processo | Rota `listagens/DadosProcesso` **existe**; sem sessão de busca devolve shell vazio |
| Movimentações | **Desconhecido**; provável HTML na página de detalhe |
| Documentos públicos | **Não confirmado**; manual diz que usuário sem login só consulta movimentação |
| Download de documento | Rota `listagens/DownloadArquivo?arquivo=<id>` **existe**; sem sessão devolve 0 bytes |
| Peticionamento | Rota `movimentacao/Peticionar` **existe**; funcionalidade não testada |
| API/MNI | **Não**; `IntercomunicacaoService?WSDL` dá 404 |
| Estado do sistema | **Legado desativado** pelo Provimento Conjunto 68/2022, mantendo consulta e protocolo mínimos |

Conclusão curta: **não é o TJGO**. O adapter `tjpi-publico` precisa de outro
login, outro fluxo de busca e outro parser. O CAPTCHA é o bloqueio principal
para automação pública; o caminho autenticado (login/senha) ainda não foi
validado e é a única esperança de um fluxo sem CAPTCHA.

## 1. Por que “Processo Virtual Nacional - CNJ”

O título **não** indica um front-end nacional novo nem o PDPJ. A própria página
`/projudi/informacoesExtras/explicaProjudi.htm` explica:

> “O Processo Virtual Nacional, proposto pelo CNJ, é resultado da junção dos
> softwares Projudi e e-Proc.”

Ou seja, “Processo Virtual Nacional” é o **nome do projeto do CNJ** que originou
o Projudi. O que está no ar é a distribuição antiga do Projudi, com a marca do
projeto:

- rodapé do login: `versão 1.9.8 (CNJ BRASIL)` / `buildId: 22/11/2011 - 13:42`;
- página pública `ListarVersao` lista a versão **1.9.7 (2009-12-03)** como
  entrada mais recente do histórico — o build de 2011 é posterior ao histórico
  exibido;
- `AssinadorExterno.jar` tem `Last-Modified: 11/11/2011` e classes
  `br.jus.cnj.projudi.*`;
- o JSP de recuperação de senha tem comentário creditando um desenvolvedor do
  **TJMT**, sinal de base compartilhada entre tribunais;
- Tomcat 6.0.20, jQuery 1.3.1, `X-UA-Compatible: IE=EmulateIE7`.

Não há botão, rota ou parâmetro de PDPJ no login. O CSP permite
`frame-ancestors` de `pdpj.jus.br`, mas isso é header genérico do portal, não
integração de autenticação.

## 2. Hosts e URLs

| URL | Situação observada |
|---|---|
| `https://projudi.tjpi.jus.br/` | 200; HTML de 66 bytes com meta refresh para `/projudi` |
| `https://projudi.tjpi.jus.br/projudi/` | 200; tela de login |
| `http://projudi.tjpi.jus.br/projudi/` | 301 para HTTPS |
| `https://projudi.tjpi.jus.br/projudi/publico/Logon` | POST; GET = 405 |
| `https://projudi.tjpi.jus.br/projudi/Logon` | POST; GET = 405 (provável alias) |
| `https://projudi.tjpi.jus.br/projudi/publico/LogonParte` | POST; GET = 405 (parte) |
| `https://projudi.tjpi.jus.br/projudi/publico/EsqueciMinhaSenha` | POST; GET = 405 |
| `https://projudi.tjpi.jus.br/projudi/publico/buscas/ProcessosParte?publico=true` | 200; formulário de busca pública |
| `https://projudi.tjpi.jus.br/projudi/buscas/ProcessosParte` | 200; mesmo HTML da rota acima (provável alias) |
| `https://projudi.tjpi.jus.br/projudi/captcha` | 200; `image/png` |
| `https://projudi.tjpi.jus.br/projudi/listagens/DadosProcesso?numeroProcesso=<id>` | 200; shell vazio sem sessão de busca |
| `https://projudi.tjpi.jus.br/projudi/listagens/DownloadArquivo?arquivo=<id>` | 200; 0 bytes com id de teste, sem sessão |
| `https://projudi.tjpi.jus.br/projudi/listagens/ProcessosParte?tipo=ativosPoloAtivo` | 200; shell vazio |
| `https://projudi.tjpi.jus.br/projudi/parte/frameCentroParte.jsp` | 200; shell da área da parte |
| `https://projudi.tjpi.jus.br/projudi/movimentacao/Peticionar?numeroProcesso=<id>` | 200; shell vazio |
| `https://projudi.tjpi.jus.br/projudi/assinatura/ManipularArquivosAssinados` | 200; usado por POST multipart no JS |
| `https://projudi.tjpi.jus.br/projudi/configuracao/ListarVersao` | 200; histórico de versões público |
| `https://projudi.tjpi.jus.br/projudi/publico/configuracao/ListarVersao` | 200; mesma página |
| `https://projudi.tjpi.jus.br/projudi/interno.jsp?endereco=/projudi/buscas/ProcessosParte` | 500 sem sessão |
| `https://projudi.tjpi.jus.br/projudi/download/AssinadorExterno.jar` | 200; JAR de 3.208.498 bytes |
| `https://projudi.tjpi.jus.br/projudi/publico/relatorio/CertidaoJudicial` | 500; “Erro ocorrido: Indeterminado” |
| `https://projudi.tjpi.jus.br/projudi/LogOnSSO` | 404 |
| `https://projudi.tjpi.jus.br/projudi/certificado` | 404 |
| `https://projudi.tjpi.jus.br/projudi/acoes/DownloadProcesso` | 404 |
| `https://projudi.tjpi.jus.br/projudi/IntercomunicacaoService?WSDL` | 404 |
| `https://www.tjpi.jus.br/portaladvogado/login` | 404 (link do manual antigo) |
| `http://www.tjpi.jus.br/themisconsulta/certidao` | 302 para `/themisconsulta/desativado` |
| `https://projudi.tjpi.jus.br/robots.txt` | 404 (sem política de crawl declarada) |

O host não expõe API JSON, swagger, `/api`, `/mni` ou similar. Tudo indica
servlets/JSP antigos.

## 3. Fluxo de login

### 3.1 Advogado / usuário externo

```text
GET  /projudi/                          -> 200, Set-Cookie JSESSIONID
POST /projudi/publico/Logon             -> 200 (erro) ou 302 (sucesso provável)
     Content-Type: application/x-www-form-urlencoded
     login=<usuário>&senha=<senha>
```

Form observado:

- `name="formLogin"`, `method="post"`, `action="/projudi/publico/Logon"`;
- campos `login` (texto, maxlength 25) e `senha` (password, maxlength 25);
- botão `Entrar` chama `acessarPagina()`, que faz `document.formLogin.submit()`;
- **sem** hidden fields, **sem** CSRF token, **sem** captcha, **sem** OTP;
- `autocomplete="off"` nos dois campos;
- labels “Usuario:” e “Senha:”.

Comportamento testado:

- `GET /projudi/publico/Logon` → **405** (“HTTP method GET is not supported by
  this URL”, Tomcat 6.0.20);
- `POST` com `login=&senha=` → **200**, mesma tela, mensagem
  **“Usuário ou senha inválidos.”**;
- não há redirecionamento, captcha ou bloqueio visível após uma falha;
- o `JSESSIONID` **não rotaciona** na falha; não foi possível testar o sucesso.

O destino do login bem-sucedido não foi observado. A página
`/projudi/PaginaPrincipal.jsp` existe e, sem sessão, devolve a própria tela de
login; é o candidato natural a landing page. **Hipótese**, não validada.

### 3.2 Parte

```text
GET  /projudi/indexParte.jsp            -> 200
POST /projudi/publico/LogonParte        -> 200/302
     tipoDocumento=0|1&cpfCnpj=<CPF/CNPJ>&senha=<senha>
```

- `tipoDocumento=0` para CPF, `1` para CNPJ;
- `senha` maxlength 15;
- **sem** captcha, **sem** certificado, **sem** OTP;
- `GET /projudi/publico/LogonParte` → 405.

Não é o caminho do advogado, mas mostra que a autenticação de parte é
CPF/CNPJ + senha.

### 3.3 Recuperação de senha

```text
GET  /projudi/esqueciMinhaSenha.jsp     -> 200 (popup)
POST /projudi/publico/EsqueciMinhaSenha -> 200
     login=<login>&data_nascimento=<dd/mm/aaaa>
```

Texto da tela: “Será gerada uma nova senha e enviada para o e-mail cadastrado no
sistema.” Não há captcha, pergunta secreta, OTP ou confirmação por e-mail antes
da troca. **Não foi executado** (evita disparo de e-mail/reset de conta).

### 3.4 Certificado digital / A1 / mTLS

**Não há login por certificado neste portal.**

Evidências:

- `openssl s_client` na porta 443: **“No client certificate CA names sent”**;
  o servidor não solicita certificado de cliente no handshake;
- `/projudi/certificado` → 404;
- `/projudi/LogOnSSO` → 404;
- nenhum botão/link “certificado digital” na tela de login;
- o único artefato de certificado é o `AssinadorExterno.jar`, um aplicativo
  Java desktop do CNJ (`br.jus.cnj.projudi.AssinadorProjudi`) com classes
  `CertificadoPanelA1` e `CertificadoPanelA3`.

Ou seja: o artefato indica **A1/A3 para assinatura de
documentos/peticionamento**, não para autenticar no portal. O login continua
sendo usuário/senha.

### 3.5 PDPJ / SSO / OTP / MFA

- `/projudi/LogOnSSO` e `/projudi/publico/LogonSSO` → **404**;
- não há botão “Acessar com PDPJ” nem parâmetro `redirect_uri`/`client_id`;
- não há campo de OTP, token ou código de segurança no form;
- o Provimento Conjunto 68/2022 cita a PDPJ-Br como motivo da desativação dos
  sistemas legados, mas **não foi observada integração SSO** no portal.

Conclusão: a autenticação observada é **fator único, usuário/senha**; não foi
observada implementação de SSO/PDPJ. Se existe segundo fator depois do login,
não é visível sem credenciais.

## 4. Credenciais e credenciamento

### Advogado

- Credencial: **login + senha**.
- Origem documentada no manual antigo: **Portal do Advogado**
  (`http://www.tjpi.jus.br/portaladvogado/login`) ou cadastro presencial no
  **JECC** mais próximo.
- O manual diz que, para cada OAB, o Portal do Advogado mostra o login
  correspondente no Projudi e permite ativar o acesso.
- O formato do login (OAB? CPF? login gerado?) **não é documentado** na tela;
  o campo aceita até 25 caracteres.
- O manual manda levar foto 3x4, RG/CPF, comprovante de residência e carteira
  da OAB; o tribunal emitia “certificado digital” em pen drive.
- O link atual do Portal do Advogado está **404**.
- O aviso em `/projudi/duvidas/configuracoesNavegador.html` informa que os JECC
  foram **desabilitados no Projudi para novas distribuições** e que, desde
  01/07/2022, restaram apenas funções mínimas de **consulta e protocolo de
  peças por agentes externos** (Provimento Conjunto 68/2022).

Consequência: provavelmente **não há cadastro novo** de advogado no Projudi;
quem já tinha acesso mantém o acesso legado. O caminho atual para novos
cadastros é o PJe/PDPJ, não este portal.

### Parte

- CPF/CNPJ + senha de 15 caracteres.
- Sem certificado, sem OTP.
- Cadastro/atendimento presencial no juizado.

### Certificado

- A1/A3 é suportado pelo **assinador externo** para assinar arquivos
  (`.p7s`), não para autenticar.
- Não há mTLS, não há PKCS#11 no login, não há A1 como credencial de sessão.

## 5. Sessão e cookies

Observado sem login:

| Item | Valor |
|---|---|
| Cookie | `JSESSIONID` |
| Path | `/projudi` |
| Secure | sim |
| HttpOnly | **não** |
| SameSite | **ausente** |
| Outros cookies | nenhum (TJGO tinha `WIDPROP` e `dtCookienncenbbl`) |
| Servidor | `Apache-Coyote/1.1`; erros mostram `Apache Tomcat/6.0.20` |
| TLS | TLS 1.2; certificado Sectigo wildcard `*.tjpi.jus.br`; sem HSTS |
| Timeout | countdown de **30 minutos** no HTML; timeout real do servidor não medido |
| Expiração | texto “Sessão Expirada.” no countdown; não testada |

Anomalias:

- A página de busca pública emitiu **dois headers `Set-Cookie: JSESSIONID`** na
  mesma resposta (dois IDs diferentes). O cliente deve ficar com o último; um
  cookie jar simples pode se confundir.
- Em falha de login, o `JSESSIONID` **permanece o mesmo**.
- `PaginaPrincipal.jsp` sem sessão devolve a tela de login (200), não 302.
- `parte/frameCentroParte.jsp` sem sessão devolve shell “Seja bem vindo(a), !”
  com contadores zerados, também 200.
- `interno.jsp?endereco=...` sem sessão devolve **500**, não redirect.

Não foi observado nenhum cookie de “lembrar-me”, CSRF ou affinity.

## 6. Consulta pública

### 6.1 Formulário

```text
GET  /projudi/publico/buscas/ProcessosParte?publico=true   -> 200
POST /projudi/publico/buscas/ProcessosParte?publico=true   -> 200 (resultado ou erro)
```

O `<form>` tem `method="post"` e **sem `action`**, portanto posta na própria
URL. Campos principais:

| Campo | Observação |
|---|---|
| `captcha` | texto; imagem em `/projudi/captcha` |
| `numeroProcesso` | número CNJ ou formato interno; máscara/mod97 no JS |
| `nome` | nome da parte; mínimo dois nomes (validação JS) |
| `sentencaInteira` | checkbox “Busca pelo Nome Completo” |
| `cpfCnpj` | CPF/CNPJ da parte |
| `nomeAdvogado` | nome do advogado |
| `sentencaInteiraAdv` | checkbox |
| `oab` | número da OAB |
| `complementoOAB` | `-1` + `B`, `D`, `E`, `N`, `P` (rótulos não explicados) |
| `tipoParteProcesso` | `-1` + códigos (Autor, Réu, Executado, etc.) |
| `codTipoJustica` | hidden |
| `codVara` | `-1` + códigos das varas/juizados |
| `codFaseProcessual` | `-1`, `1` Conhecimento, `2` Execução |
| `tipoAcao` | `-1` + classes antigas |
| `codAssunto`, `descAssunto`, `secundAssunto` | hidden/dinâmicos |
| `AssuntoPrincipalDef` | hidden |
| `codClasse`, `descClasse`, `secundClasse` | hidden/dinâmicos |
| `ClassePrincipalDef` | hidden |
| `Buscar` | botão `type="image"` |

Regras de UI:

- é obrigatório informar **a imagem (captcha) + pelo menos um campo**;
- nome da parte exige no mínimo dois nomes;
- OAB exige `complementoOAB` diferente de `-1`;
- o botão de busca é `input type="image"` com `name="Buscar"`.

### 6.2 CAPTCHA

- Imagem PNG em `GET /projudi/captcha` (3,6 KB na amostra);
- o `src` inclui `?d=<timestamp>` para refresh;
- é **por sessão**: sem o cookie da página, o POST devolve 500; com o cookie e
  captcha errado, devolve 200 com:
  “As letras foram informadas incorretamente. Por favor, digite as letras que
  aparecem na imagem, diferenciando maiúsculas e minúsculas”;
- o texto é **case-sensitive**;
- não testamos reutilização nem OCR; não é seguro afirmar “single-use” no TJPI,
  embora implementações da mesma base (TJBA) tratem como uso único.

**Implicação:** a consulta pública não é automatizável de forma confiável sem
intervenção humana ou OCR. OCR de captcha é frágil e pode violar termos de uso;
não deve ser o caminho padrão do conector.

### 6.3 Resultado

Não foi possível ver a página de resultado sem resolver o captcha. O que se sabe:

- a busca por número, nome, CPF/CNPJ, advogado e OAB usa o **mesmo POST**;
- não há rota separada de “busca por OAB”;
- pela mesma geração CNJ (TJBA/TJRN), o resultado é uma tabela HTML com link
  para `listagens/DadosProcesso?numeroProcesso=<id interno>`;
- o id do link é **numérico e interno**, não o número CNJ;
- um índice público de terceiros mostra URLs nesse padrão, o que reforça a
  hipótese, mas não foi possível abrir o resultado ao vivo.

**Status:** resultado = **não observado**; estrutura = **hipótese forte**.

### 6.4 Certidão

- “Certidão Judicial Digital” aponta para
  `http://www.tjpi.jus.br/themisconsulta/certidao`, que responde **302** para
  `/themisconsulta/desativado`.
- “Validar Certidão” aponta para
  `/projudi/publico/relatorio/CertidaoJudicial?validaCertidao=true`, que
  responde **500** (“Erro ocorrido: Indeterminado”).
- Ou seja: certidão pública está **quebrada/desativada** nesta instalação.

## 7. Processo, movimentações e documentos

### 7.1 O que foi observado

| Rota | Sem sessão | Interpretação |
|---|---|---|
| `GET /projudi/listagens/DadosProcesso?numeroProcesso=<id>` | 200, shell de 8.710 bytes, sem dados | rota existe; não renderizou dados sem sessão de busca (ou o id de teste não basta) |
| `GET /projudi/listagens/DownloadArquivo?arquivo=<id>` | 200, **0 bytes** (id de teste) | rota existe; não entregou arquivo sem sessão/permissão (ou id inválido) |
| `GET /projudi/listagens/ProcessosParte?tipo=ativosPoloAtivo` | 200, shell de 8.710 bytes | rota existe; exige sessão de parte/advogado |
| `GET /projudi/movimentacao/Peticionar?numeroProcesso=<id>` | 200, shell de 8.710 bytes | rota existe; exige sessão |
| `GET /projudi/parte/frameCentroParte.jsp` | 200, shell de 13.753 bytes | área da parte; sem login mostra contadores zerados |
| `GET /projudi/interno.jsp?endereco=...` | 500 | existe, mas exige sessão |

O shell de 8.710 bytes é o layout vazio do sistema: título “Sistema CNJ
(Processo Judicial Digital - PROJUDI)”, menu, scripts, `Sessão Expirada`, mas
sem tabela de processo. Ele é idêntico para IDs válidos e inválidos sem sessão
de busca, o que impede distinguir “não encontrado” de “sem contexto”.

### 7.2 O que é hipótese

Pela mesma base CNJ (TJBA/TJRN) e pelas rotas observadas:

```text
POST /projudi/publico/buscas/ProcessosParte?publico=true
  captcha=<texto>&numeroProcesso=<CNJ>
  -> HTML com tabela de resultados
  -> link /projudi/listagens/DadosProcesso?numeroProcesso=<id interno>

GET /projudi/listagens/DadosProcesso?numeroProcesso=<id interno>
  -> HTML “Dados do Processo”
  -> cabeçalho (número, classe, assunto, distribuição, segredo)
  -> partes
  -> tabela de movimentações
  -> possíveis links /projudi/listagens/DownloadArquivo?arquivo=<id>
```

Não foi possível confirmar:

- nomes exatos das colunas e seletores;
- se as movimentações aparecem na página de detalhe ou em aba separada;
- se o resultado público mostra links de documento;
- se `DownloadArquivo` exige apenas sessão ou também flags de acesso
  (`AcessoUsuario >= AcessoArquivo`, como no TJGO);
- se o download devolve PDF, HTML ou `.p7s`.

### 7.3 Documentos públicos?

O “Manual do Usuário Externo” do próprio TJPI é explícito:

> “A única atividade permitida para o usuário que não possui login é a consulta
> sobre a movimentação de processos.”

Isso indica que **documentos não são públicos**; no máximo, a consulta pública
mostra movimentações. O `DownloadArquivo` sem sessão devolveu 0 bytes no id de
teste, coerente com essa restrição. Mas não há evidência suficiente para
afirmar que o link sequer aparece no resultado público.

**Status:** documentos públicos = **não suportados/não confirmados**;
documentos autenticados = **desconhecidos**.

### 7.4 Peticionamento

- Rota `GET /projudi/movimentacao/Peticionar?numeroProcesso=<id>` existe.
- O JS `assinador.js` mostra o fluxo de upload/assinatura:
  `POST /projudi/assinatura/ManipularArquivosAssinados` (multipart),
  com Estados `opCode 1/2/2.1/3`, arquivos `.p7s` e assinatura múltipla.
- O Provimento 68/2022 diz que o protocolo de peças por agentes externos foi
  uma das funções mínimas mantidas.
- Não foi testado (exige login e não é read-only).

Para o conector read-only, peticionamento **não** entra no MVP. Só importa
saber que a rota existe e que o assinador é separado.

## 8. Endpoints e JavaScript

### 8.1 Rotas observadas

```text
GET  /projudi/                                       login
POST /projudi/publico/Logon                          login usuário/senha
POST /projudi/Logon                                  provável alias
POST /projudi/publico/LogonParte                     login CPF/CNPJ + senha
GET  /projudi/indexParte.jsp                         form da parte
GET  /projudi/esqueciMinhaSenha.jsp                  popup de recuperação
POST /projudi/publico/EsqueciMinhaSenha              recuperação (login + nascimento)
GET  /projudi/publico/buscas/ProcessosParte          form de busca
POST /projudi/publico/buscas/ProcessosParte          busca (captcha)
GET  /projudi/buscas/ProcessosParte                  provável alias
GET  /projudi/captcha                                imagem PNG
GET  /projudi/listagens/DadosProcesso                detalhe (id interno)
GET  /projudi/listagens/DownloadArquivo              download (arquivo id)
GET  /projudi/listagens/ProcessosParte               lista da parte
GET  /projudi/parte/frameCentroParte.jsp             shell da parte
GET  /projudi/movimentacao/Peticionar                peticionamento
POST /projudi/assinatura/ManipularArquivosAssinados  upload/assinatura
GET  /projudi/configuracao/ListarVersao              histórico de versões
GET  /projudi/publico/relatorio/CertidaoJudicial     certidão (500)
GET  /projudi/download/AssinadorExterno.jar          assinador CNJ
GET  /projudi/dwr/interface/Pesquisa.js              DWR de consulta auxiliar
```

### 8.2 JavaScript

| Arquivo | O que revela |
|---|---|
| `/projudi/scripts/default.js` | máscara/mod97 do número CNJ, `preencheZeros`, `valida_mod97`; sem rotas |
| `/projudi/scripts/assinador.js` | upload multipart, `ManipularArquivosAssinados`, `.p7s`, assinatura múltipla |
| `/projudi/scripts/listaMovimentacoes.js` | checkboxes de movimentação (expedição/citação/intimação); típico de tela interna |
| `/projudi/scripts/ajaxPopulate.js` | selects encadeados (vara/cidade) |
| `/projudi/scripts/criminal/cadastroDadosProcessoCriminal.js` | campos de processo criminal |
| `/projudi/dwr/interface/Pesquisa.js` | DWR `Pesquisa.pesquisaCodigoPK` e `Pesquisa.pesquisaProcedimento` em `/projudi/dwr` |

O DWR é usado para lookup de tabelas processuais (assunto/classe/vara), não
para busca de processo. Não foi testado como API.

### 8.3 Parâmetros

- Detalhe: `numeroProcesso=<id interno numérico>`.
- Download: `arquivo=<id>`.
- Lista da parte: `tipo=ativosPoloAtivo|ativosPoloPassivo|arquivados`.
- Intimações: `tipo=novas|lidaNaoCumprida`.
- Citações: `tipo=novas&ehNotificacao=true`.
- Audiências: `tipo=aguardamRealizacao`.
- Busca pública: campos listados na seção 6.1.

## 9. Diferenças vs TJGO

| Aspecto | TJGO (`tjgo-servlet`) | TJPI (`tjpi-publico`) |
|---|---|---|
| Geração | Projudi “novo”, servlets com JSON | Projudi CNJ 1.9.8 (2011), JSP/HTML |
| Login | `/LogOn` + `/certificado` mTLS | `/publico/Logon`, só usuário/senha |
| A1/mTLS | **Sim**, handshake pede certificado | **Não**; TLS não pede certificado |
| PDPJ | Botão existe, fluxo quebrado | Não existe |
| OTP | Código por e-mail no fallback | Não observado |
| Busca | `/BuscaProcesso`, contexto de sessão | `/publico/buscas/ProcessosParte`, CAPTCHA |
| Detalhe | `/BuscaProcesso` com `Id_Processo` | `/listagens/DadosProcesso?numeroProcesso=<id>` |
| Índice de documentos | JSON `/BuscaProcesso?PaginaAtual=9&AJAX=AJAX` | HTML; sem JSON conhecido |
| Download | `Id_MovimentacaoArquivo` + `hash` | `listagens/DownloadArquivo?arquivo=<id>` |
| Cookies | `JSESSIONID`, `WIDPROP`, `dtCookienncenbbl` | só `JSESSIONID` |
| Encoding | ISO-8859-1 | ISO-8859-1 |
| Estado | Em uso | Legado desativado em 2022, consulta/protocolo mínimos |

Implicações diretas:

1. **Não reutilizar** `certificate.py`/mTLS do TJGO no TJPI.
2. **Não reutilizar** o parser JSON de `BuscaProcesso`.
3. **Não assumir** `MovimentacaoArquivo` nem `Id_MovimentacaoArquivo`.
4. **Novo** tratamento de CAPTCHA e de sessão de busca.
5. **Novo** parser de HTML de resultado/detalhe e de links `DownloadArquivo`.
6. **Novo** modelo de erro: `captcha_required`, `captcha_invalid`,
   `login_failed`, `session_expired`, `access_denied`, `layout_changed`.

## 10. Desenho do adapter `tjpi-publico`

### 10.1 Registro

```python
Portal(
    "tjpi-1g", "projudi", "TJPI", "1g",
    "projudi.tjpi.jus.br", "/projudi", "form_login", "TJPI_1G",
    region="18", justice="8", flavor="tjpi-publico",
)
```

- `wired=False` até smoke live provar login + busca + um processo.
- `region="18"` (TR do TJPI), `justice="8"`.
- Credenciais: `PROJUDI_TJPI_LOGIN` / `PROJUDI_TJPI_PASSWORD`.
- Sem `PJE_A1_P12`/`PJE_A1_PIN` para este portal; A1 não autentica aqui.
- CAPTCHA: `PROJUDI_TJPI_CAPTCHA_MODE=manual|ocr|none` (default `manual`).

### 10.2 Módulos

```text
src/jurisync/connectors/projudi/
  base.py          # sessão, cookies, limites, erros, documento/texto
  tjgo.py          # flavor tjgo-servlet (existente/planejado)
  tjpi.py          # flavor tjpi-publico (novo)
  parsers_tjpi.py  # HTML de busca/detalhe/documentos
  server.py        # tools MCP; escolhe o adapter por portal
```

`base.py` reutilizável:

- `requests.Session`/`httpx.Client` com `verify=True`, timeout 30 s;
- decode ISO-8859-1 (com fallback para UTF-8);
- headers de browser simples, sem fingerprint agressivo;
- rate limit 1 req/s e backoff exponencial em 429/5xx;
- modelo de erro e redaction de cookies/credenciais/números.

`tjpi.py`:

```python
class TjpiPublicClient:
    def login(self, login: str, senha: str) -> None: ...
    def new_captcha(self) -> bytes: ...
    def search(self, process_number: str, captcha: str) -> list[ProcessSummary]: ...
    def open_process(self, internal_id: str) -> ProcessDetail: ...
    def list_documents(self, detail: ProcessDetail) -> list[DocumentRef]: ...
    def download(self, arquivo_id: str) -> DownloadedFile: ...
```

Fluxo público:

```text
GET  /projudi/publico/buscas/ProcessosParte?publico=true
GET  /projudi/captcha                         (mesma sessão)
POST /projudi/publico/buscas/ProcessosParte?publico=true
     captcha + numeroProcesso (ou OAB/nome)
     -> parse tabela
     -> internal_id
GET  /projudi/listagens/DadosProcesso?numeroProcesso=<internal_id>
     -> parse metadados + movimentações
     -> anchors DownloadArquivo?arquivo=<id>
GET  /projudi/listagens/DownloadArquivo?arquivo=<id>
     -> Content-Type/Content-Length; 0 bytes => access_denied
```

Fluxo autenticado (a validar):

```text
GET  /projudi/                           -> JSESSIONID
POST /projudi/publico/Logon              -> 302/landing
GET  /projudi/PaginaPrincipal.jsp        -> área interna
```

Não inventar rota interna de busca antes de validar. Se o login autenticado
ainda exigir CAPTCHA, o adapter deve declarar isso explicitamente e não fingir
automação.

### 10.3 CAPTCHA

Estratégias, em ordem de preferência:

1. **Login autenticado** (se credenciais do advogado estiverem disponíveis e a
   busca interna não pedir captcha). É o único caminho potencialmente
   não-interativo.
2. **Operador no loop**: tool `projudi_captcha` devolve o caminho da imagem;
   o operador digita o texto; a tool de busca recebe `captcha`.
3. **OCR** (opcional, desligado por default): frágil, auditável e com risco de
   violar termos de uso. Não usar em produção sem autorização.
4. **Sem consulta pública**: se nenhum caminho acima for aceitável, o portal
   fica `wired=False` e o Jurisync usa PJe/DataJud para os casos migrados.

O adapter **nunca** deve:

- logar o texto do captcha;
- reutilizar captcha entre buscas;
- tentar N captchas em sequência (brute force);
- contornar bloqueio com proxies/rotação de IP.

### 10.4 Parsers

`parsers_tjpi.py`:

- `parse_search_results(html) -> list[ProcessSummary]`
  - linhas de tabela; número CNJ, partes, vara, distribuição, `internal_id`;
  - ignorar linhas de “nenhum resultado”;
  - falhar alto se não achar a tabela esperada.
- `parse_process(html) -> ProcessDetail`
  - cabeçalho (número, classe, assunto, distribuição, segredo);
  - partes (não persistir em log);
  - movimentações (data, título, complemento, links).
- `parse_document_links(html) -> list[DocumentRef]`
  - anchors `DownloadArquivo?arquivo=<id>`;
  - nome/tipo/assinador, se presentes;
  - retornar lista vazia com motivo `not_exposed` se não houver links.
- `parse_login_error(html) -> str | None`
  - “Usuário ou senha inválidos.”, “Sessão Expirada.”, etc.

Regra de ouro: **não devolver lista vazia silenciosa** quando o layout mudar.
Diferenciar “não há documentos” de “não entendi a página”.

### 10.5 Tools MCP

| Tool | Entrada | Saída |
|---|---|---|
| `projudi_captcha` | `portal` | caminho local da imagem + id de sessão; sem texto |
| `projudi_search` | `portal`, `process_number` ou `oab`, `captcha` | resumo dos resultados |
| `projudi_get_process` | `portal`, `process_number`, `captcha` | metadados + movimentações |
| `projudi_list_documents` | `portal`, `process_number` | documentos expostos (pode ser vazio) |
| `projudi_read_documents` | `portal`, `process_number`, `document_ids` (máx. 5) | texto/status |
| `projudi_download_document` | `portal`, `process_number`, `document_id` | arquivo + metadados |

Todas read-only. Nenhuma tool de peticionamento/assinatura. Não devolver URL
bruta, cookie, `hash` ou id interno na saída pública.

### 10.6 Testes

Offline (sem rede):

- parsing de erro de login;
- parsing de erro de captcha;
- parsing de resultado de busca com 0/1/N processos (fixtures sanitizadas);
- parsing de detalhe com movimentações e sem movimentações;
- parsing de `DownloadArquivo` com 0 bytes, PDF e HTML;
- `access_denied`, `session_expired`, `layout_changed`;
- limite de 5 documentos.

Live (opt-in `-m live`, read-only):

1. login com credencial de teste (se existir);
2. busca de um processo autorizado;
3. leitura de metadados/movimentações;
4. download de 1 documento, se permitido;
5. repetir via MCP stdio;
6. verificar que nenhum dado pessoal foi para log.

## 11. Riscos

- **CAPTCHA**: bloqueia automação pública; OCR é frágil e juridicamente
  sensível. Sem login, o conector não é totalmente não-assistido.
- **Sistema legado desativado**: Provimento 68/2022 desativou Projudi em
  01/07/2022, mantendo consulta/protocolo mínimos. Dados podem estar
  congelados, incompletos ou migrados para o PJe.
- **Migração para PJe**: processos novos não tramitam mais no Projudi;
  números podem existir apenas no PJe. O conector pode encontrar “não
  encontrado” com frequência.
- **HTML de 2011**: sem contrato estável, tabelas e JSP; qualquer mudança
  quebra parser.
- **Autenticação fraca**: senha única, sem MFA observado; sessão sem
  `HttpOnly`/`SameSite`; JSESSIONID não rotaciona em falha; Tomcat 6.0.20
  fora de suporte.
- **Recuperação de senha**: login + data de nascimento, sem captcha/OTP;
  vetor de takeover se a data for conhecida.
- **Sem mTLS**: não é possível reutilizar o fluxo A1 do TJGO; A1/A3 só serve
  para assinatura no app externo.
- **Documentos**: provavelmente não públicos; `DownloadArquivo` sem sessão
  devolve 0 bytes. Não prometer leitura de documentos no MVP.
- **Rate limit/ToS**: não há robots.txt; o tribunal pode bloquear automação.
  Respeitar 1 req/s, backoff e limite de documentos.
- **Dados pessoais**: nunca logar número de processo, partes, conteúdo de
  documento, captcha ou cookie. Redaction obrigatória.
- **Desligamento futuro**: o portal pode sair do ar a qualquer momento; o
  adapter deve falhar de forma explícita e sugerir PJe/DataJud.

## 12. Perguntas abertas

1. O login autenticado de advogado **dispensa o CAPTCHA** na busca? (Teste
   com credencial real.)
2. Qual é o formato do login do advogado: OAB, CPF, login gerado pelo Portal
   do Advogado?
3. O login bem-sucedido redireciona para `/PaginaPrincipal.jsp` ou outra
   landing? Há OTP/MFA depois da senha?
4. Existe rota interna de busca de processo (sem captcha) para advogado? Qual?
5. `DadosProcesso?numeroProcesso=<id>` funciona com id interno + sessão de
   busca? O id é sempre numérico? Há `hash`/`codigoHash`?
6. O resultado público mostra movimentações? Em que tabela/aba?
7. O resultado público mostra links de documento? Se sim, com que restrição?
8. `DownloadArquivo` autoriza por sessão, por flag de acesso ou por hash?
   Devolve PDF/HTML/`.p7s`?
9. O peticionamento ainda funciona após 01/07/2022? Para quais juizados?
10. Ainda é possível cadastrar advogado novo? O Portal do Advogado (404) foi
    substituído por qual fluxo?
11. Qual é o timeout real de sessão no servidor? (O HTML diz 30 min.)
12. Existe MNI/Intercomunicação em outro host? (`IntercomunicacaoService` deu
    404 neste.)
13. O TJPI pretende desligar o Projudi por completo? Há cronograma?
14. O acesso automatizado é permitido pelos termos do tribunal? (Não há
    robots.txt; verificar política de uso.)

## 13. Evidências (status/título, sem dados pessoais)

| # | Requisição | Status | Título/observação |
|---|---|---|---|
| 1 | `GET /projudi/` | 200 | “Processo Virtual Nacional - CNJ”; 22.227 bytes |
| 2 | `GET /` (raiz do host) | 200 | HTML de 66 bytes com meta refresh para `/projudi` |
| 3 | `GET /projudi/publico/Logon` | 405 | Tomcat 6.0.20; GET não suportado |
| 4 | `POST /projudi/publico/Logon` (`login=&senha=`) | 200 | “Usuário ou senha inválidos.” |
| 5 | `GET /projudi/publico/buscas/ProcessosParte?publico=true` | 200 | “BUSCA POR PROCESSOS”; 88.209 bytes; captcha |
| 6 | `GET /projudi/captcha` | 200 | `image/png`; 3.672 bytes |
| 7 | `POST` busca sem sessão/captcha | 500 | “Erro ocorrido: Indeterminado” |
| 8 | `POST` busca com sessão + captcha errado | 200 | “As letras foram informadas incorretamente…” |
| 9 | `GET /projudi/indexParte.jsp` | 200 | form `LogonParte`; CPF/CNPJ + senha |
| 10 | `GET /projudi/esqueciMinhaSenha.jsp` | 200 | form `login` + `data_nascimento` |
| 11 | `GET /projudi/listagens/DadosProcesso?numeroProcesso=<id>` | 200 | shell de 8.710 bytes, sem dados |
| 12 | `GET /projudi/listagens/DownloadArquivo?arquivo=<id>` | 200 | 0 bytes |
| 13 | `GET /projudi/listagens/ProcessosParte?tipo=ativosPoloAtivo` | 200 | shell de 8.710 bytes |
| 14 | `GET /projudi/parte/frameCentroParte.jsp` | 200 | shell “Seja bem vindo(a), !”; 13.753 bytes |
| 15 | `GET /projudi/movimentacao/Peticionar?numeroProcesso=<id>` | 200 | shell de 8.710 bytes |
| 16 | `GET /projudi/configuracao/ListarVersao` | 200 | “Histórico de Versões”; 20.251 bytes |
| 17 | `GET /projudi/download/AssinadorExterno.jar` | 200 | `application/java-archive`; 3.208.498 bytes; `Last-Modified` 11/11/2011 |
| 18 | `GET /projudi/LogOnSSO` | 404 | “Sistema CNJ - Página não encontrada” |
| 19 | `GET /projudi/certificado` | 404 | idem |
| 20 | `GET /projudi/acoes/DownloadProcesso` | 404 | idem |
| 21 | `GET /projudi/IntercomunicacaoService?WSDL` | 404 | idem |
| 22 | `GET /projudi/interno.jsp?endereco=...` | 500 | “Erro ocorrido: Indeterminado” |
| 23 | `GET /projudi/publico/relatorio/CertidaoJudicial` | 500 | “Erro ocorrido: Indeterminado” |
| 24 | `GET http://projudi.tjpi.jus.br/projudi/` | 301 | para HTTPS |
| 25 | `openssl s_client` | — | TLS 1.2; “No client certificate CA names sent” |
| 26 | `GET https://www.tjpi.jus.br/portaladvogado/login` | 404 | link antigo do manual |
| 27 | `GET http://www.tjpi.jus.br/themisconsulta/certidao` | 302 | `/themisconsulta/desativado` |
| 28 | `GET /projudi/robots.txt` | 404 | sem política de crawl |

Observações de headers:

- `Server: Apache-Coyote/1.1`; erros: `Apache Tomcat/6.0.20`;
- `X-Frame-Options: SAMEORIGIN`;
- `Content-Security-Policy: frame-ancestors 'self' https://tjpi.jus.br
  https://*.tjpi.jus.br https://pdpj.jus.br https://*.pdpj.jus.br`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: strict-origin-when-cross-origin`;
- `Cache-Control: no-cache` / `Pragma: no-cache`;
- **sem** `Strict-Transport-Security`;
- `Set-Cookie: JSESSIONID=...; Path=/projudi; Secure` (sem `HttpOnly` e sem
  `SameSite`);
- a página de busca emitiu **dois** `Set-Cookie: JSESSIONID` na mesma resposta.

## 14. Reprodução rápida (somente leitura)

```bash
# login page
curl -sS -D - -o /tmp/tjpi.html \
  'https://projudi.tjpi.jus.br/projudi/'

# confirma que Logon é POST-only
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://projudi.tjpi.jus.br/projudi/publico/Logon'

# busca pública (não resolve captcha; só mostra o form)
curl -sS -o /tmp/tjpi-busca.html \
  'https://projudi.tjpi.jus.br/projudi/publico/buscas/ProcessosParte?publico=true'

# captcha (imagem; não usar OCR sem autorização)
curl -sS -o /tmp/tjpi-captcha.png \
  'https://projudi.tjpi.jus.br/projudi/captcha'

# confirma ausência de mTLS
openssl s_client -connect projudi.tjpi.jus.br:443 \
  -servername projudi.tjpi.jus.br </dev/null 2>&1 | \
  grep -i 'client certificate'
```

Não executar login, recuperação de senha, peticionamento, assinatura ou
download em massa. Não registrar dados pessoais.

## 15. Veredito para o Jurisync

O TJPI **não é um alvo do adapter `tjgo-servlet`**. Ele exige um adapter
próprio, `tjpi-publico`, com:

- login por formulário (sem mTLS/SSO/OTP);
- CAPTCHA obrigatório na consulta pública;
- parser HTML antigo (ISO-8859-1, JSP, Tomcat 6);
- detalhe por id interno e download por id de arquivo;
- documentos provavelmente restritos a sessão autenticada;
- postura conservadora: `wired=False` até smoke live com credencial real.

Se o login autenticado dispensar o CAPTCHA e expuser documentos, o adapter tem
valor. Se não, o TJPI deve ser tratado como **fonte legada de consulta
assistida**, e os processos migrados devem ser lidos do PJe/DataJud. O pior
cenário é prometer um conector “público” que na prática só funciona com
intervenção humana no captcha.
