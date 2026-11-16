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
| `tnu` | Denis (senha + TOTP, navegador no SSO do TRF4) | ✅ (só depois da correção 1) | vazias | vazio | não testado: conta sem processo; DJEN da OAB CE 29046 sem TNU em 12 semanas e `siglaTribunal=TNU` responde HTTP 500 | não testado | igual (painel `painel_advogado_listar`) |
| `trf2-1g-rj` | Hérica (A1 + TOTP, `TRF2_1G_RJ_CERT=true` só no ambiente do processo) | ✅ | OK (3 prazo aberto, 5 decurso) | OK (21) | OK (74 eventos, 36 docs, 3 partes) | OK: HTML 12.800 car., PDF 15.536 car. | igual ao TRF4 |
| `trf2-1g-es` | Hérica (A1 + TOTP) | ✅ | OK (2 decurso) | OK (6) | OK (15 eventos, 20 docs) | OK: PDF 48.071 car., HTML (tipo `HTML` maiúsculo, segredo) 670 car. | igual |
| `trf2-2g` | Hérica (A1 + TOTP) | ✅ | vazias | vazio | OK em processo público do 2G (7 eventos); número de 1G dava erro → corrigido (correção 2) | OK: HTML 2.291 car. | diferente: busca rápida de número que a instância não tem redireciona a `processo_consultar` |
| `trf4-1g-rs` | Wesley (A1), Hérica (A1), Priscila (senha), Fernanda (A1) | ✅ nas 4 contas | OK (Hérica 4 prazo aberto/4 decurso; Priscila 1/1; Fernanda 2; Wesley vazias) | OK (Hérica 6, Priscila 2, Fernanda 8, Wesley vazio) | ⛔ `processo_selecionar` do JFRS responde 503 "Service Unavailable" depois de 38–51 s (ou estoura o timeout de 30 s), em 4 processos e 3 contas | não testado (depende do get_process) | busca rápida igual (302 → `processo_selecionar`); a página do processo é que cai no servidor |
| `trf4-1g-pr` | Wesley (A1); Hérica (A1) | ✅ | OK (Hérica 4 prazo aberto, 1 pendente, 1 decurso; Wesley vazias) | OK (Hérica 15; Wesley vazio) | OK (53 eventos, 34 docs) | OK: PDF 1.754 car., HTML 1.095 car. | igual ao TRF4 SC |
| `trf4-2g` | Wesley (A1); Hérica (A1) | ✅ | vazias nas duas contas | OK (Hérica 6; Wesley vazio) | OK (4 eventos, 2 docs) | OK: 2 PDF (um com tipo `PDF` maiúsculo), 1.852 e 3.718 car. | igual |
| `trf6-2g` | Wesley (senha + TOTP, navegador) | ✅ | vazias | vazio | OK em processo público do 2G (24 eventos); número de 1G → `processo_not_found` (correção 2) | OK: HTML 3.143 car. | igual ao TRF2 2G na busca de número de outra instância |

Logins completos nesta rodada (um por portal por conta): Denis `tnu`; Wesley `trf4-1g-rs`, `trf4-1g-pr`, `trf4-2g`,
`trf6-2g`; Hérica `trf2-1g-rj`, `trf2-1g-es`, `trf2-2g`, `trf4-1g-rs`, `trf4-1g-pr`, `trf4-2g`; Fernanda `trf2-1g-rj`,
`trf4-1g-rs`; Priscila `trf4-1g-rs`. Depois da queda do `trf4-1g-rs` da Hérica o pool tentou entrar de novo sozinho e
parou na página de login (nenhuma credencial enviada). A sessão da Fernanda no `trf2-1g-rj` entrou (o pouso em
`pessoa_alterar&avisoCadastrarEmailSenha=true` foi contornado pelo link do painel), mas a leitura falhou por erro do
script de teste; não foi refeita.

## Correções

1. `110c9ea` — **TNU e seções do TRF4 por senha saíam pela rota clássica HTTP.** A página Keycloak do
   `eproc-sso.trf4.jus.br` (usada pela TNU e pelo TRF4) traz um `frm-login` oculto com `txtUsuario` e `pwdSenha`.
   `http_login` via esses nomes, achava que era o formulário clássico e mandaria a senha para o `login_cert` do portal
   (que depois pede A3). Agora só contam campos visíveis; a página Keycloak cai em `NotClassicLogin` antes de enviar
   qualquer coisa e o login segue pelo navegador. Teste offline com a página Keycloak sanitizada.
2. `b23d6bf` — **Busca rápida de número que a instância não tem (TRF2 2G, TRF6 2G).** O portal responde 302 para
   `processo_consultar` (formulário Consulta Processual); o cliente levantava "Unexpected eproc read action". Agora o
   redirecionamento é reconhecido (sem abrir a página) e vira `process_not_found`, como o caso de número divergente.

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

## Pendências

- `tnu`: `eproc_get_process`/documento sem teste — a conta do Denis não tem processo na TNU e o DJEN não devolve
  comunicações da TNU para a OAB dele.
- `trf4-1g-rs`: `eproc_get_process` cai no 503 do servidor do JFRS (`processo_selecionar`), em todas as contas e
  processos tentados; a busca rápida e as filas respondem normalmente. Retestar em outro horário.
- Contas do Wesley no TRF4 (RS, PR, 2G) e no TRF6 2G estão vazias (filas e relatório); as utils foram validadas com
  outras contas ou com processo público.
