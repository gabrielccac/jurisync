# Validação Eproc federal — 16/11/2026

Grupo `eproc-federal`: TNU, TRF2 (RJ, ES, 2G), TRF4 (RS, PR, 2G) e TRF6 2G. Tudo pelas tools do servidor MCP do eproc
(`create_server` + `call_tool`, sessão reaproveitada pelo pool), só leitura. Nenhuma ciência, nenhum "liberar
dispositivo", nenhuma escolha de perfil padrão. Em todos os portais as filas pendentes de ciência estavam vazias para o
processo lido; os documentos lidos são petições, despachos antigos ou de processo público em que o advogado não é parte.

Legenda: OK = leu e o conteúdo bate com a página; vazio = a página do portal diz que não há registro
(`hdnInfraNroItens=0` / "Nenhum registro encontrado"), não é falha de parser.

## Resultado por portal

| Portal | Cliente (login) | Login | Filas (7) | list_processes | get_process | Leitura de documento | Igual a portal validado? |
|---|---|---|---|---|---|---|---|
| `tnu` | Denis (senha + TOTP, navegador); Hérica (A1 + TOTP Eproc/TRF4, 2ª rodada) | ✅ nas duas contas (Denis só depois da correção 1; Hérica só depois da correção 3) | vazias nas duas | vazio nas duas | OK em processo público da TNU achado no DJEN (`siglaTribunal=CJF`, órgão "PRESIDÊNCIA - TURMA NACIONAL"): 3 eventos, 1 doc | OK: HTML 2.634 car. | igual (painel `painel_advogado_listar`) |
| `trf2-1g-rj` | Hérica (A1 + TOTP, `TRF2_1G_RJ_CERT=true` só no ambiente do processo) | ✅ | OK (3 prazo aberto, 5 decurso) | OK (21) | OK (74 eventos, 36 docs, 3 partes) | OK: HTML 12.800 car., PDF 15.536 car. | igual ao TRF4 |
| `trf2-1g-es` | Hérica (A1 + TOTP) | ✅ | OK (2 decurso) | OK (6) | OK (15 eventos, 20 docs) | OK: PDF 48.071 car., HTML (tipo `HTML` maiúsculo, segredo) 670 car. | igual |
| `trf2-2g` | Hérica (A1 + TOTP) | ✅ | vazias | vazio | OK em processo público do 2G (7 eventos); número de 1G dava erro → corrigido (correção 2) | OK: HTML 2.291 car. | diferente: busca rápida de número que a instância não tem redireciona a `processo_consultar` |
| `trf4-1g-rs` | Wesley, Hérica, Priscila, Fernanda (1ª rodada); Elisandra (senha + TOTP, 2ª rodada) | ✅ nas 5 contas | OK (Elisandra 29 prazo aberto, 16 pendentes, 5 urgentes, 1 decurso, 6 pauta) | OK (Elisandra 50) | OK na 2ª rodada (16h41): 84 eventos, 38 docs, 5 partes, processo fora das filas de ciência. Na 1ª rodada (~15h–16h) `processo_selecionar` dava 503 depois de 38–51 s em 4 processos e 3 contas | OK: PDF 1.791 car., HTML 23.072 car. | igual ao TRF4 PR/SC |
| `trf4-1g-pr` | Wesley (A1); Hérica (A1) | ✅ | OK (Hérica 4 prazo aberto, 1 pendente, 1 decurso; Wesley vazias) | OK (Hérica 15; Wesley vazio) | OK (53 eventos, 34 docs) | OK: PDF 1.754 car., HTML 1.095 car. | igual ao TRF4 SC |
| `trf4-2g` | Wesley (A1); Hérica (A1) | ✅ | vazias nas duas contas | OK (Hérica 6; Wesley vazio) | OK (4 eventos, 2 docs) | OK: 2 PDF (um com tipo `PDF` maiúsculo), 1.852 e 3.718 car. | igual |
| `trf6-2g` | Wesley (senha + TOTP, navegador) | ✅ | vazias | vazio | OK em processo público do 2G (24 eventos); número de 1G → `processo_not_found` (correção 2) | OK: HTML 3.143 car. | igual ao TRF2 2G na busca de número de outra instância |

Logins completos na 1ª rodada (um por portal por conta): Denis `tnu`; Wesley `trf4-1g-rs`, `trf4-1g-pr`, `trf4-2g`,
`trf6-2g`; Hérica `trf2-1g-rj`, `trf2-1g-es`, `trf2-2g`, `trf4-1g-rs`, `trf4-1g-pr`, `trf4-2g`; Fernanda `trf2-1g-rj`,
`trf4-1g-rs`; Priscila `trf4-1g-rs`. Depois da queda do `trf4-1g-rs` da Hérica o pool tentou entrar de novo sozinho e
parou na página de login (nenhuma credencial enviada). A sessão da Fernanda no `trf2-1g-rj` entrou (o pouso em
`pessoa_alterar&avisoCadastrarEmailSenha=true` foi contornado pelo link do painel), mas a leitura falhou por erro do
script de teste; não foi refeita. 2ª rodada: Elisandra `trf4-1g-rs`, Hérica `tnu` (um cada).

## Correções

1. `110c9ea` — **TNU e seções do TRF4 por senha saíam pela rota clássica HTTP.** A página Keycloak do
   `eproc-sso.trf4.jus.br` (usada pela TNU e pelo TRF4) traz um `frm-login` oculto com `txtUsuario` e `pwdSenha`.
   `http_login` via esses nomes, achava que era o formulário clássico e mandaria a senha para o `login_cert` do portal
   (que depois pede A3). Agora só contam campos visíveis; a página Keycloak cai em `NotClassicLogin` antes de enviar
   qualquer coisa e o login segue pelo navegador. Teste offline com a página Keycloak sanitizada.
2. `b23d6bf` — **Busca rápida de número que a instância não tem (TRF2 2G, TRF6 2G).** O portal responde 302 para
   `processo_consultar` (formulário Consulta Processual); o cliente levantava "Unexpected eproc read action". Agora o
   redirecionamento é reconhecido (sem abrir a página) e vira `process_not_found`, como o caso de número divergente.

3. `cab0743` — **TNU com A1.** A TNU manda o login ao realm `eproc` do TRF4 (`eproc-sso.trf4.jus.br`, certificado em
   `eproc-sso-certificado.trf4.jus.br`, mesmo `client_id` e broker do TRF4). A checagem de redirecionamento só aceitava
   SSO sob `cjf.jus.br`, então o `cert_login` parava. `SSO_PARENTS` ganhou `cjf.jus.br → trf4.jus.br`; teste atualizado.

`uv run pytest -q --ignore=tests/live` e `ruff check`/`ruff format --check` passam.

## TRF6 com A1 + TOTP

O login do TRF6 hoje não é o formulário clássico: `eproc{1,2}g.trf6.jus.br/eproc/` manda ao Keycloak
`sso-eproc.trf6.jus.br` (usuário/senha visíveis e botão `kc-login-certificate` para `certificado-sso-eproc`). Por isso
`_classic_login` e `_local_certificate_login` não se aplicam (não há `frmLogin`/`destino_v2_cert`). O caminho Keycloak do
`cert_login` também não entra: a primeira página (`/eproc/` e `externo_controlador.php?acao=SSO/login`) devolve o desafio
JavaScript do WAF ao HTTP puro, e o conector para com "eproc login page has no certificate option" antes de apresentar
o certificado (teste com `TRF6_CERT=true` da Fernanda; nada enviado). Mesmo copiando todos os cookies de um navegador
anônimo, o HTTP puro continua recebendo o desafio na entrada. Depois de um login por navegador, porém, as leituras por
HTTP puro funcionam (Wesley, todas as tools acima). Para A1 no TRF6 faltaria um login híbrido: navegador até a página do
SSO e o certificado apresentado ao `certificado-sso-eproc` (hoje só por proxy local, como no teste de 13/11).
Recomendação: no TRF6 usar senha + TOTP (`browser_totp`), que funciona.

## 2ª rodada (16/11, tarde)

- `trf4-1g-rs`: às 16h40, sem login, o JFRS respondia normal (raiz e consulta pública em ~0,5 s). Escolhida a
  Elisandra (OAB 61216/RS, 150 comunicações do TRF4 em 3 semanas no DJEN, quase todas da JFRS); um login. O conector já
  lê os nomes antigos dela (`TRF4_1G_RS_USUARIO`/`_SENHA`/`_EXPORT_DATA` via `LEGACY_NAMES`). `processo_selecionar`
  respondeu em 0,8 s: o 503 da 1ª rodada foi instabilidade do portal (~15h–16h de 16/11), não do conector.
  A Eliane não foi usada.
- `tnu`: a página de login da TNU é o mesmo Keycloak do TRF4 (realm `eproc`, `client_id=cnj.jus.br`, broker
  `sso.cloud.pje.jus.br/.../broker/trf4`) e oferece o certificado (`eproc-sso-certificado.trf4.jus.br`). Logo a conta
  de advogado do TRF4 serve na TNU: a Hérica entrou com A1 + o perfil `Eproc/TRF4` do autenticador (nenhum cliente além
  do Denis tem um perfil `Eproc/TNU`). No DJEN a TNU publica como `siglaTribunal=CJF` (órgão "PRESIDÊNCIA - TURMA
  NACIONAL"; `TNU` dá HTTP 500). Nenhum cliente com OAB conhecida (Hérica GO 22729, Fernanda SC 35675, Elisandra RS
  61216, Denis CE 29046) tem comunicação CJF em 6 meses; por isso get_process e documento foram validados em processo
  público da TNU, em que a Hérica não é parte.

Variáveis para o setup (não editei `.env`):
- Hérica (e qualquer advogado com A1 no TRF4) na TNU: `TNU_CERT=true`, `TNU_2FA_QR` = o mesmo QR do TRF4
  (`TRF4_2G_2FA_QR`) e `TNU_OTP_PROFILE_MATCH` = o mesmo do TRF4 (`Eproc/TRF4`). Em `portals.toml`:
  `[tnu] method = "certificate_totp"`, `secrets = ["TNU_CERT", "TNU_2FA_DATA|TNU_2FA_QR"]`.
- Hérica no TRF2: `TRF2_1G_RJ_CERT=true`, `TRF2_1G_ES_CERT=true`, `TRF2_2G_CERT=true` (usados só em memória nesta rodada).
- Elisandra: nada a mudar (nomes antigos são lidos).

## Pendências

- `tnu`: nenhum cliente com processo próprio na TNU encontrado; leitura validada só em processo público. Wesley e
  Allan (A1 no TRF4) não têm OAB registrada nos docs do cliente, então não foram procurados no DJEN.
- Contas do Wesley no TRF4 (RS, PR, 2G) e no TRF6 2G estão vazias (filas e relatório); as utils foram validadas com
  outras contas ou com processo público.
