# Validação e-SAJ / Projudi — 16/11/2026

Branch `val/esaj-projudi`. Somente leitura; nenhuma ciência, cadastro, troca de senha ou perfil padrão.
Nenhum `.env`/`portals.toml` alterado. Nenhum segredo, cookie ou token nos arquivos do repositório.

## Resumo

| Portal | Cliente | Login | Utils | Status | Logins nesta rodada |
|---|---|---|---|---|---|
| e-SAJ `tjam-esaj` | Hérica | ✅ A1 (CAS) | sessão ✅, intimações ✅ (0), audiências ✅ (0), petições ✅ (0), caixa postal ✅ (1), get_process ⚠ (ver abaixo), documentos não exercitados | ✅ login + utils | 1 |
| Projudi `tjam-1g` | Thaile | ✅ CPF + senha (sem 2FA) | 4 tools MCP ✅ (get_process, list_documents, read_documents, download_document) | ✅ login + utils, ligado | **2** + 1 na parte 2 |
| Projudi `tjpr-projudi-1g` | Hérica | ⛔ A1 recusado por política (só A3); falta senha do Projudi PR | — | ⛔ falta senha | 0 |
| Projudi `tjrr-1g` | — | ⛔ ninguém tem senha | — | ⛔ falta senha | 0 |
| Projudi `tjpi-projudi-1g` | — | ⛔ ninguém tem login/senha | — | ⛔ falta login e senha | 0 |
| Projudi `tjgo-1g` | Hérica | ✅ A1 + serventia `TJGO_1G_USER_PROFILE` | get_process ✅, list_documents ✅, read_documents ✅ | ✅ login + utils | 1 |

## e-SAJ TJAM (`tjam-esaj`)

**Causa do login intermitente** (exploração pública, sem credencial): o painel `tarefas-adv/` do TJAM manda ao CAS
o serviço `https://consultasaj.tjam.jus.br/tarefas-adv//j_spring_cas_security_check` (**duas barras**). O conector
pedia o serviço padrão (uma barra); o CAS emitia o ticket, mas o painel validava com a sua grafia, recusava e caía em
`tarefas-adv/erroDesconhecido.do;jsessionid=...` (o `;` era então recusado pelo guard). No 1º login de 13/11 o CAS
desviou antes para `esajperfil/abrirConfirmacaoDados.do`, por isso funcionou. Além disso, `esaj/api/auth/session`
não existe no TJAM (404): o `refresh` do pool sempre falhava, então toda chamada após 2 min ociosa fazia **novo login**
— o que produziu o 2º login de 13/11.

Correções (commit `2ff247c`):
- o login chega à página do CAS **pelo painel** (`tarefas-adv/`), herdando o `service` que o próprio tribunal usa;
  sem isso, cai no serviço padrão (TJSP/TJMS/TJAL: mesmo `service`, comportamento igual);
- `upgrade_scheme` também tira `;jsessionid=` final de redirecionamentos no host do portal;
- `refresh` e `esaj_get_session` usam `sajcas/verificarLogin.js` (`usuarioLogadoNoCasServer`) quando a API dá 404
  (TJAM devolve `usuarioLogado: true, sessionSource: "sajcas"`, sem OABs/serviços);
- `load_process` passou a conhecer o adaptador `tjam` (antes: `KeyError`);
- número que a consulta não conhece → erro `not_found` em vez de "No unique process detail".

Validação ao vivo (1 login, pelas tools do servidor MCP): `esaj_get_session` ✅ (2×, sem relogin), `esaj_list_tasks`
intimações/audiências/petições ✅ (0 itens — igual ao 13/11), `esaj_list_messages` ✅ (1 item).
`esaj_get_process`: os processos da Hérica no TJAM (DJEN, 2025–2026, vara cível da capital) **não estão no e-SAJ**
(acervo antigo): a consulta pública responde "Não existem informações disponíveis"; busca pública por nome do
advogado também vazia. Ou seja, a Hérica não tem processo no e-SAJ TJAM. O parser de processo foi validado numa
página `cpopg/show.do` pública do TJAM (campos, 2 partes, 1.123 movimentos — layout igual ao TJSP). Documentos não
exercitados por falta de processo da conta. Diferença em relação a TJSP/TJMS: sem API de sessão, `service` com `//`.

## Projudi TJAM (`tjam-1g`)

Cliente: **Thaile** (`TJAM_PROJUDI_USERNAME`/`TJAM_PROJUDI_PASSWORD`; `TJAM_PROJUDI_2FA_DATA` existe mas está vazia —
o portal não pediu 2FA). Login local (`login` + `senha` + `tck` MD5) aceito → `usuario/postLogon.do` (frameset com
menu `add_menu(...)`) → mesa do advogado. Observado ao vivo e implementado (commit `e4a5357`):

| Util | Resultado |
|---|---|
| sessão (`mesaAdvogado.do?actionType=listaInicio`) | ✅ (link de logout) |
| agenda de intimações / citações (listagem) | ✅ 0 / 0 aguardando ciência |
| busca por número (menu "Processos 1º Grau", número só com dígitos) | ✅ |
| processo (`processo.do`): campos, 133 movimentos | ✅ |
| partes (aba `tabPartes`) — só papel, nome, advogados (sem CPF/endereço) | ✅ 4 partes |
| arquivos de uma movimentação (`movimentacaoArquivoDocumento.do`, 0,09 s/POST) | ✅ |
| download `arquivo.do` (processo sem intimação pendente) | ✅ PDF 55 KB, texto extraído |

Segurança de documento: antes de baixar, o adaptador lê a agenda "Aguardando Ciência"; se o processo estiver lá (ou a
agenda não puder ser lida inteira) → `DocumentsBlocked`.

**Incidente (2 logins):** ao rodar as tools MCP sobre a sessão já aberta, toda página logada foi lida como "sessão
expirada": o leitor legado procurava `"a sessão expirou"` e todas as páginas têm `"Sua sessão expirou"` no script do
diálogo de timeout. O `SessionPool` respondeu com um novo login automático (2º login, aceito) e falhou de novo pelo mesmo
motivo. Corrigido offline (só texto visível, frase inteira) com teste. **Pendente:** repetir as 4 tools MCP do
`tjam-1g` numa próxima rodada (1 login) — os mesmos caminhos já foram exercitados ao vivo manualmente nesta rodada.

## Projudi TJPR (`tjpr-projudi-1g`) — conclusão

- **O certificado só serve se for A3.** O botão "Certificado Digital" é só um link
  (`token/autenticacaoCertificado.do?actionType=inicioComCertificado`) para `projudicrt.tjpr.jus.br`, que pede o
  certificado no handshake TLS e decide no servidor. Sem certificado → `ERROA3=600`; com o A1 da Hérica → `ERROA3=601`
  "Certificado utilizado não é A3". Não há JS/applet nessa decisão: o A1 traz a política ICP-Brasil
  `2.16.76.1.2.1.38` (família `2.16.76.1.2.1.*` = A1); A3 é `2.16.76.1.2.3.*`. A política é assinada pela AC — não há
  como contornar. O cadastro de advogado pelo site (`usuario/advogadoCertificado.do`) também exige A3.
- **Existe login com senha + código.** A home do Projudi PR diz: "Advogados ... Use o CPF ou CNPJ e senha do
  Projudi", com autenticação em dois fatores obrigatória desde 13/09/2021 (Google Authenticator; o A3 dispensa o 2FA).
  É o Keycloak `sso.projudi.tjpr.jus.br/auth/realms/projudi` (client `projudi`): formulário só CPF/CNPJ + senha, sem
  CAPTCHA, sem provedor alternativo (PDPJ/x509). `connectors/projudi/keycloak.py` já implementa esse fluxo (simulado).
- O botão "single sign-on" é a intranet do TJPR (magistrados/servidores), não serve a advogados.
- A Hérica já tem o perfil TOTP **"PROJUDI PR"** nos QRs (QR 1 índice 6 / QR 3 índice 2): forte indício de conta
  ativa com 2FA cadastrado.

**O que a cliente precisa fornecer:** a **senha do Projudi PR** (a do Projudi, não da intranet). Variáveis:
`TJPR_PROJUDI_1G_USERNAME` (CPF), `TJPR_PROJUDI_1G_PASSWORD`, `TJPR_PROJUDI_1G_2FA_QR` (QR_Herica_1) e
`TJPR_PROJUDI_1G_OTP_PROFILE_MATCH=PROJUDI PR`. Se não lembrar a senha: "Esqueci a senha" do próprio Keycloak (e-mail
cadastrado). Se não tiver conta: cadastro exige A3 ou atendimento OAB-PR (projudi@oabpr.org.br). Nenhuma tentativa com
credencial nesta rodada: o único caminho ainda não testado (senha) não tem senha disponível.

## Projudi TJRR (`tjrr-1g`) e TJPI (`tjpi-projudi-1g`) — exploração pública

- **TJRR:** `tjpr/autenticacao.do` → Keycloak "Autentikey" `keycloak.tjrr.jus.br/realms/projudi` (client
  `projudi-client`): CPF + senha, "Mantenha-me conectado", sem CAPTCHA; o 2FA não aparece antes da senha. Sem login por
  certificado. Credenciais: só a Hérica tem `TJRR_PROJUDI_2FA_QR` e `TJRR_PROJUDI_OTP_PROFILE_MATCH`.
  **Falta:** `TJRR_PROJUDI_USERNAME` (CPF) e `TJRR_PROJUDI_PASSWORD`.
- **TJPI:** Projudi 1.9.8 (CNJ, 2011): formulário `formLogin` em `/projudi/` → `POST /projudi/publico/Logon` com
  `login`, `senha`, `entrar`; sem CAPTCHA nem 2FA visíveis na página; links "Cadastro de Advogado" e "Esqueci minha
  senha". Nenhum cliente tem variáveis do Projudi PI (a Ana Flávia tem só `TJPI_1G_2FA_DATA`/`TJPI_2G_2FA_DATA`, do PJe).
  **Falta:** `TJPI_PROJUDI_1G_USERNAME` e `TJPI_PROJUDI_1G_PASSWORD` de quem atua no Projudi PI; o adaptador
  `tjpi-publico` ainda não existe.

## Parte 2 (mesmo dia)

### `tjam-1g` — tools MCP de ponta a ponta (Thaile, 1 login, 2º login bloqueado no script)

Processo da conta, sem intimação pendente na agenda:

| Tool | Resultado |
|---|---|
| `projudi_get_process` (com documentos) | ✅ 3 campos, 4 partes, 133 movimentos, 153 arquivos (~9 s: 1 POST por movimentação com arquivo) |
| `projudi_list_documents` (todas / `movement_id=130`) | ✅ 92 movimentações, 153 arquivos / `130001` (`130.1`) |
| `projudi_read_documents` (`130001`) | ✅ PDF 55 KB, `extracted` |
| `projudi_download_document` (`130001`) | ✅ salvo (arquivo removido depois) |

Nenhuma falsa expiração; sem relogin. Diferença em relação ao TJGO: o índice é montado por movimentação (sem JSON único),
então `list_documents` com `movement_id` também carrega o índice inteiro (~8 s).

### `tjgo-1g` — seleção de serventia (Hérica, 1 login)

O A1 é aceito e cai em `/Usuario?PaginaAtual=9&hashFluxo=...` "Serventias Disponíveis": 21 serventias, uma por
seccional (`Ordem dos Advogados do Brasil XX - XX / Advogados - OAB/Matrícula: <n>-<N|S>-XX`), cada uma um link
`Usuario?PaginaAtual=7&a1=...`. Não há opção de "padrão" na tela (só o diálogo de senha do assinador). Seguido o link
de `GO22729` → painel (`Usuario?PaginaAtual=7...`, sem `LoginPronto=S`, sem a lista).

Correção (commit `9f1325e`): `TJGO_1G_USER_PROFILE` = OAB (`GO22729`, `go 22.729`, `22729GO`) ou palavra do rótulo,
como no eproc; sem ela, uma única serventia de advogado é usada; várias → `LoginRejected` com a lista de rótulos.
Com 21 serventias, **a Hérica precisa de `TJGO_1G_USER_PROFILE=GO22729`** no `.env` (a OAB principal, `-N-`; as
outras são suplementares `-S-`). Nesta validação o valor foi passado só no processo; nenhum arquivo do cliente foi
alterado.

Utils (processo achado no DJEN, OAB GO 22.729, TJGO): `projudi_get_process` ✅ (62 movimentos, 2 partes, 44 arquivos),
`projudi_list_documents` ✅ (21 movimentações com arquivo), `projudi_read_documents` ✅ (uma petição juntada, HTML,
`extracted_unnumbered`). Comportamento igual ao TJGO já validado, exceto o passo da serventia.

## Verificação

- `uv run pytest -q --ignore=tests/live` verde; `ruff check` e `ruff format --check` limpos.
- Testes novos: e-SAJ (serviço do painel, fallback, `;jsessionid`, sessão via CAS, get_process TJAM, `not_found`);
  Projudi TJAM (busca por dígitos, movimentos, partes sem documento, índice/arquivo, bloqueio por intimação pendente,
  script de timeout não é expiração) — páginas sintéticas com a estrutura real.
