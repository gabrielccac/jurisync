# JPe / Themis (TJMG, 2ª instância)

Conector `connectors/jpe`, servidor MCP `jurisync-jpe` (`python -m jurisync.connectors.jpe.server`). Portal
`tjmg-jpe-2g` em `portals.py` (sistema `jpe`, prefixo `TJMG_JPE_2G`, prefixo antigo aceito `TJMG_THEMIS_2G`, CNJ
J=8, TR=13, `guess_by_number=False`). Somente leitura em relação ao portal.

**Estado em 19/11/2026: nenhuma página autenticada foi vista.** O login pelo PDPJ é recusado pelo próprio SSO (abaixo)
e não havia login próprio do JPe configurado. Os parsers das páginas logadas foram escritos a partir das telas do
manual público e casam por texto visível (rótulos de menu, cabeçalhos de coluna, nomes de aba), nunca por id gerado
pelo JSF. Quando a página não tem o formato esperado, a ferramenta responde "not recognized" em vez de adivinhar.
A primeira sessão real deve ser usada para conferir e corrigir `parsers.py` (ver "Validação pendente").

## Arquitetura do portal (visto ao vivo, só GET)

- `https://jpe.tjmg.jus.br/` redireciona para `/rupe/portaljus/intranet/principal.rupe?contexto=jpe` ("Portal do
  Processo Eletrônico", v9.33.11). Páginas `.rupe` em JSF 1.2 + RichFaces 3.3.3 (`A4J.AJAX.Submit`), ViewState no
  servidor (`j_idN`), sessão no cookie `JSESSIONID` (o servidor põe `;jsessionid=` nos links até ver o cookie; o
  conector tira esse parâmetro e confia no cookie). HTML em ISO-8859-1.
- A página de entrada tem três logins:
  1. `formLogin` → `POST /rupe/j_security_check` com `j_username`/`j_password` (o "Usuário" do JPe, criado por
     autocadastro com certificado ICP; **não é o CPF**);
  2. `formLoginCertificado` → `/rupe/loginCertificado` (applet Java ou PJeOffice: não suportado);
  3. botão Jusbr "Login por credencial do PJE": `submitSsoLogin()` envia `formLoginSSO` (`origemURL` vazio) para
     `/rupe/loginSsoKeycloakServlet`, que redireciona para `sso.cloud.pje.jus.br/auth/realms/pje/...` com
     `client_id=jpe-tjmg-2g` e `redirect_uri=https://jpe.tjmg.jus.br/rupe/loginSsoKeycloakServlet/callback`.
  No primeiro acesso de um navegador a página ainda envia sozinha `formCheckSsoUser` (checagem silenciosa,
  `prompt=none`), que recebe a mesma recusa.

## O botão do PDPJ (19/11/2026)

Com navegador real (SeleniumBase, headless), sem digitar credencial nenhuma: tanto a checagem silenciosa quanto o
clique no botão Jusbr terminam na página de erro do SSO, título "PDPJ - Plataforma Digital do Poder Judiciário",
`#kc-error-message` = "Parâmentro inválido: redirect_uri" (HTTP 400). O client `jpe-tjmg-2g` existe (senão a
mensagem seria de client desconhecido) e o link "Voltar para login" dele aponta para `https://pje.tjmg.jus.br/pje`,
mas a lista de redirect URIs dele não aceita o callback do JPe. O erro é do lado do tribunal/CNJ, antes de qualquer
senha. O conector reconhece essa página e levanta `SsoRefused` (um `LoginRejected`: o pool não repete) com a
mensagem "o JPe recusou o login pelo PDPJ do lado do tribunal; use o login próprio do JPe ou peça ao TJMG que corrija
o client jpe-tjmg-2g". Fixture real: `tests/connectors/jpe/fixtures/sso_redirect_uri.html`.

## Configuração (escolhida no `.env`, nunca adivinhada no login)

| Rota | Variáveis | Quando |
|---|---|---|
| Login próprio do JPe | `TJMG_JPE_2G_LOGIN`, `TJMG_JPE_2G_LOGIN_PASSWORD` | quando `_LOGIN` existe |
| PDPJ | `TJMG_JPE_2G_USERNAME` (CPF), `_PASSWORD`, `_2FA_DATA` ou `_2FA_QR`, `_OTP_PROFILE_INDEX`/`_MATCH` | quando não há `_LOGIN` e o USERNAME é um CPF |

`TJMG_JPE_2G_AUTH=jpe|pdpj` força a rota. Todas as variáveis também são lidas com o prefixo `TJMG_THEMIS_2G`. A
senha do login próprio fica numa variável separada (`_LOGIN_PASSWORD`) de propósito: a senha do PDPJ nunca é enviada
ao formulário do JPe por engano. Um USERNAME que não é CPF é erro de configuração (o Login do JPe vai em `_LOGIN`).

No PDPJ o fluxo é o do PJe: formulário `loginForm` ou `kc-form-login` (neste o CPF vai só com dígitos,
`pje.auth.sso.sso_username`), depois `kc-otp-login-form` com o TOTP (espera a próxima janela se faltarem menos de
8 s), depois o callback do JPe. Recusa de senha, senha expirada ou conta bloqueada viram `LoginRejected` com a
mensagem do SSO (`credential_failure`).

## Ferramentas

| Ferramenta | Caminho no portal |
|---|---|
| `jpe_get_counters()` | página inicial: "Total de Comunicações" (Pendentes de Leitura / de Manifestação) e "Total de Processos" (Ativos / Arquivados / Não protocolizados × Polo Ativo / Polo Passivo); painéis recolhidos são abertos pelo próprio cabeçalho |
| `jpe_list_communications(status)` | menu Comunicações > Pendentes de Leitura / Pendentes de Manifestação / Lidas / Manifestadas; grade "Processo CNJ, Processo TJ, Classe, Envio, Órgão Julgador, Destinatário, Urgente" e "N resultado(s)" |
| `jpe_list_processes(query)` | menu Consulta, a lista como ela abre (processos ativos em que a conta é representante, OAB já preenchida) |
| `jpe_get_process(process_number, include_documents)` | linha da Consulta → botão de visualizar → janela "Dados Processo" (abas Dados de Origem, Dados Básicos, Partes, Petições e Documentos, Andamentos) |
| `jpe_read_documents(process_number, document_ids, acknowledgeCiencia)` | aba Petições e Documentos → botão de baixar de cada linha |
| `jpe_download_document(process_number, document_id, acknowledgeCiencia)` | idem; salva em `JURISYNC_DOCUMENTS_DIR` |

## Regras de segurança

- Só o host `jpe.tjmg.jus.br` sob `/rupe/` (e o SSO durante o login). Os menus seguidos são só os da lista
  `parsers.MENU_LABELS`; Cadastro, Peticionamento e Sair nunca.
- Em JSF não dá para filtrar por URL, então o filtro é no controle: `client.click` recusa qualquer controle cujo
  rótulo ou script fale em peticionar, manifestar, cadastrar, assinar, protocolar, enviar, salvar, confirmar,
  justificativa, "Acesso de Terceiros" ou comunicação.
- **Comunicação nunca é aberta.** Pelo manual, abrir os documentos de uma comunicação ou os "dados da comunicação"
  registra a leitura. O conector só lista as grades de comunicações.
- **Ciência:** documento de processo que tem comunicação "Pendente de Leitura" é recusado (`CienciaPending`), e
  também quando essa lista não pôde ser lida inteira (`CienciaUnchecked`), salvo `acknowledgeCiencia=true` com o
  aviso no resultado.
- **Processo de terceiro nunca é aberto.** Só processos da lista inicial da Consulta (os que a conta representa).
  Se a página pedir justificativa de acesso, a chamada para (`ThirdPartyAccess`) sem enviar nada.

## Validação pendente (primeira sessão real)

1. Login (próprio ou PDPJ, quando o TJMG corrigir o client) uma vez, dentro de lock e janela nova de TOTP.
2. Salvar (anonimizados) a página inicial, uma lista de comunicações **Lidas**, a Consulta e a janela de um processo
   sem comunicação pendente, e trocar os `synthetic_*` dos testes por eles.
3. Conferir: onde o login aterrissa (o conector também procura o app dentro de frame/iframe), se os menus são links
   simples ou ações JSF, se as abas do processo são renderizadas no cliente ou por Ajax, onde fica o botão de baixar
   (na aba ou só no "Visualizar Petições e Documentos") e se a grade tem paginação (hoje só a 1ª página é lida e
   `complete=false` avisa).
