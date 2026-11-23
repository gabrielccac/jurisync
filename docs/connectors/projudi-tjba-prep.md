# Projudi TJBA — exploração

Fase 1 (2026-11-23, manhã): só leituras públicas (GET) de `/`, `/projudi/`, `PaginaPrincipal.jsp`, `Cabecalho.jsp`,
`scripts/captcha/tcentCaptcha.js`, `scripts/default.js` e dos comunicados 301 e 303.

Fase 2 (2026-11-23, tarde): com autorização do usuário, o TCaptcha passou a ser **resolvido** automaticamente
(`core/tencent_captcha.py`, como o AWS WAF do TJES e a imagem do TJTO) e houve **2 logins, um por conta, ambos recusados
pelo portal**. Nenhuma sessão autenticada foi obtida.

## Veredito

| Portal | Login | Leituras autenticadas | Geração | Tentativas de login |
|---|---|---|---|---|
| `tjba-projudi-1g` (`projudi.tjba.jus.br/projudi/`, `wired=False`; `tjba-1g`/`tjba-2g` são PJe) | Form local `login` + `senha` + `token` (resposta do **TCaptcha**, resolvido), `POST /projudi/Logon` | **não observadas**: as duas contas foram recusadas | Projudi CNJ 1.x de servlets (linha do TJPI), flavor novo `cnj-servlet` | **2** (thaile 1, max 1) |

**Bloqueio: senhas.** O CAPTCHA não é mais o bloqueio: foi aceito pelo Tencent em todas as tentativas. O portal recusou
as duas contas:

- **thaile**: `alert('Senha Expirada! Gentileza resetar sua senha através do link "Não consegue acessar o sistema?"')`
  — é a redefinição obrigatória de 19–20/10/2026. A advogada precisa pedir a senha nova em "Não consegue acessar o
  sistema?" (login + e-mail), entrar à mão, trocar em "Outros › Modificar Senha" e atualizar `TJBA_PROJUDI_PASSWORD`.
  O conector não troca senha. `TJBA_PROJUDI_2FA_DATA` está **vazio** no `.env` dela (a variável existe sem valor).
- **max**: `alert('Usuário ou senha inválidos.')` — usuário ou senha errados (a senha guardada tem 7 caracteres; a
  redefinição exige 8+, então é provavelmente a senha anterior a 20/10). Não houve nova tentativa.

Com uma senha válida, a fase 3 é: um login, mapear a mesa (menus, listas de citações/intimações, pendências,
processos, audiências), busca, processo, movimentações, arquivos e um download fora de intimação pendente, e só então
`wired=True`.

## CAPTCHA (Tencent TCaptcha) — resolvido, observado ao vivo

- Widget: `TCaptcha-global.js`, app id `189907471`, opção `enableAged` (botão "Modo simples", não usado). O iframe
  `#tcaptcha_iframe_dy` (`global.turing.captcha.gtimg.com/template/drag_ele_global.html`) mostrou **sempre o slider**
  "Deslize para completar o quebra-cabeça".
- Slider: fundo 672×390 (`cap_union_new_getcapbysig?img_index=1`) com o buraco escurecido de borda clara; sprite
  682×620 (`img_index=0`) com a peça (recorte 120×120 indicado pelo `background-position`/`background-size` do
  elemento da peça) e a trilha. Exibição em 340 px (escala ~1,976). A peça segue o controle 1:1.
- Solução: o contorno (alfa) da peça é comparado às bordas do fundo na linha da peça (PIL, ~0,2 s); o modelo de visão
  (OpenRouter, `JURISYNC_CAPTCHA_API_KEY`) só desempata quando o casamento local é fraco ou depois de uma recusa;
  arraste com aceleração e pequenas variações. O callback devolve `{"ret":0,"ticket":"tr03…","randstr","appid"}`
  (o JSON vai inteiro em `token`).
- **Taxa: 9/9 resolvidos no primeiro quebra-cabeça** (1 protótipo, 6 em bancada sem login, 2 nos logins), sem
  precisar do modelo. Quebra-cabeças de clique/seleção não apareceram; o solver tem um caminho por modelo de visão
  para eles, não validado.
- O "ticket de contingência" (`terror_1001_…`) do script da página nunca é usado: o solver recusa esse ticket e
  qualquer `errorCode`.
- Os dois POSTs chegaram à verificação da senha (mensagens diferentes por conta), então o ticket foi aceito também no
  servidor, ou a senha é checada antes dele; uma recusa só do CAPTCHA pelo portal não foi vista.

## Login (observado ao vivo)

- O navegador abre `PaginaPrincipal.jsp`, troca `onSubmit` da página por uma captura (o navegador não envia nada),
  chama `captchaShow()` e entrega a resposta + cookies (`JSESSIONID`, `ADC_CONN_*`, `ADC_REQ_*`, `FADC_PROJUDI`).
- O conector faz um único `POST /projudi/Logon` com `login`, `senha`, `token` (sem `tipoUsuario`), `Referer` da
  página de login.
- Recusa: **200** com a própria tela de login e, depois do `</body>`, `<script>alert('…');</script>`. Mensagens vistas:
  "Usuário ou senha inválidos." → `credentials_refused`; "Senha Expirada! …" → `password_expired`. Outras palavras
  mapeadas por hipótese: bloqueio/desativação → `blocked`; mensagem de captcha → `captcha` (não é recusa de senha);
  qualquer outra → `LoginRejected("unexpected")`, para não reenviar a senha.
- Sucesso, segundo fator e troca forçada de senha: **não vistos**. Uma página com dois campos de senha para por
  `password_expired`, um campo de código para por `unexpected`; nada é preenchido.

## Tela de login (fase 1, público)

- `GET https://projudi.tjba.jus.br/` → 302 → 302 → `/projudi/`: um **frameset** (`Cabecalho.jsp` em cima,
  `PaginaPrincipal.jsp` no meio). ISO-8859-1. Versão no rodapé: "10/11/2026 19:05".
- `form#formLogin` (`name=formLogin`, `method=post`, `action=/projudi/Logon`, `onsubmit="return false;"`):
  - `login` (text, maxlength 25), `senha` (password, maxlength 25), `token` (hidden, vazio), checkbox `tipoUsuario`
    ("Sou parte do processo").
  - `submeter('acesso')`: com `tipoUsuario` marcado, o campo `login` vira `cpfCnpj` e o action vira
    `/projudi/LogonParte`; senão `/projudi/Logon`. Depois chama **`captchaShow()`, sempre** (não há caminho sem
    CAPTCHA).
  - `tcentCaptcha.js` (`data-captcha-id="189907471"`, script `https://ca.turing.captcha.qcloud.com/TCaptcha-global.js`):
    `captcha.show()`; quando `res.ret == 0`, `onSubmit(JSON.stringify(res))` põe o JSON (`ticket`, `randstr`) em
    `token` e envia o form. O script tem um "ticket de contingência" (`terror_1001_<appid>_<epoch>`) para quando o JS do
    Tencent não carrega. **Forjar esse ticket seria contornar o CAPTCHA: proibido**, mesmo que o servidor aceite.
- Sem campo de código TOTP na tela. Segundo fator depois do POST: ainda não observado (nenhum login passou). A senha
  recusada foi observada na fase 2 (acima).
- Links da tela: `EsqueciSenha` ("Não consegue acessar o sistema?"), `AutoCadastraAdvogado` (primeiro acesso).
- Consulta pública: `form#formConsultaPublica` `POST /projudi/buscas/ProcessosParte` (`numeroProcesso`, `captcha`)
  com imagem `/projudi/captcha.jpg`: **CAPTCHA também**, não serve de alternativa automática. "Consulta Avançada" →
  `/projudi/interno.jsp?endereco=/projudi/buscas/ProcessosParte`.
- Serviços públicos (sem CAPTCHA, não usados): `POST /projudi/AcessoPublico` (`codigoHash`, código individual de acesso
  ao teor), `POST /projudi/ValidarArquivoDocumento` (`codigoHashDoc`), `POST /projudi/ValidarAlvara`,
  `POST /projudi/VerLotacaoAudiencia`. Nenhum dá a mesa do advogado.

## Comunicados 301/303 (19–20/10/2026): redefinição obrigatória de senhas

"Devido a uma intervenção técnica, os usuários precisam redefinir as senhas de acesso e de assinatura": pedir nova senha
em "Não consegue acessar o sistema?" (login + e-mail), entrar com a senha do e-mail **digitada à mão**, trocar em
"Outros › Modificar Senha" (8+ caracteres), revogar o certificado em "Outros › Listar Meus Certificados" e criar outro em
"Outros › Criar Identidade Digital". Consequências:

- uma senha guardada antes de 20/10/2026 pode não valer mais, e o primeiro acesso depois da redefinição pode cair numa
  troca obrigatória de senha: confirmar com as clientes que já redefiniram (o conector não troca senha);
- o menu autenticado tem ao menos "Outros" com "Modificar Senha", "Listar Meus Certificados" e "Criar Identidade
  Digital" (rotas de perfil: proibidas ao conector).

## O que ainda não pôde ser observado (sem sessão)

Mesa após o login e menu (citações/intimações, pendências, processos, audiências), busca e abertura de processo,
movimentações, lista de arquivos, download e tipo de conteúdo, sinais de expiração da sessão. Hipótese pela linha CNJ 1.x
(rotas conhecidas do Projudi CNJ 1.x):

| Hipótese | Rota |
|---|---|
| busca do advogado | `interno.jsp?endereco=/projudi/buscas/ProcessosParte`, `POST /projudi/buscas/ProcessosParte` |
| listas da mesa | `listagens/ProcessosParte?tipo=...`, listas de intimações/citações em `listagens/...` |
| processo | `listagens/DadosProcesso?numeroProcesso=<id interno>` |
| arquivo | `listagens/DownloadArquivo?arquivo=<id>` |
| sessão caída | no TJPI `interno.jsp` sem sessão dá 500, não redirect; a confirmar no TJBA |

Regra que continua valendo: abrir uma intimação no Projudi registra a leitura. Documentos ficam bloqueados para processos
com intimação aguardando leitura, como no TJAM.

## Geração e reaproveitamento

- **Não é `legacy-local` (TJAM)**: o TJAM usa Struts (`usuario/logon.do`, `tck = md5(sal+login)`, `_tj` em todo link,
  menu `add_menu(...)`). O TJBA usa servlets sem `.do` (`/projudi/Logon`, `buscas/ProcessosParte`, `listagens/...`,
  `interno.jsp?endereco=`), como o TJPI.
- **Não é `legacy-keycloak`** (sem SSO) nem **`tjgo-servlet`** (sem mTLS/A1; os servlets do TJGO são outra base).
- **É a geração Projudi CNJ 1.x** (`formLogin` → `Logon`/`LogonParte`, consulta pública com
  `captcha.jpg`), mais nova e com TCaptcha no login.
- De `legacy.py` servem: `session_expired`/`has_human_verification` (incluir "TencentCaptcha"/"TCaptcha" no padrão),
  `hidden_fields`, `page_links` + `FORBIDDEN_LINK`, `strip_path_params`, `DocumentsBlocked`, `_frame_pages`. O login
  e os parsers seriam próprios.

## Próximos passos

1. thaile: redefinir a senha no portal (fluxo do comunicado 301) e atualizar `TJBA_PROJUDI_PASSWORD`; dizer se o
   portal pediu autenticador (hoje `TJBA_PROJUDI_2FA_DATA` está vazio).
2. max: conferir `TJBA_PROJUDI_USERNAME`/`_PASSWORD` (provavelmente senha anterior à redefinição).
3. Com uma senha válida: um login (o adaptador `connectors/projudi/tjba.py` já faz CAPTCHA + POST + classificação),
   mapear a mesa e as listas, reescrever `refresh` e as leituras sobre as páginas reais, testar um documento fora de
   intimação pendente e então `wired=True`.
