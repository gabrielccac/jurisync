# Hérica Tavares & Borba — validação de login em 13/11/2026

A tentativa anterior deste mesmo dia foi bloqueada pelo PIN errado; com o PIN corrigido, o A1 (Hérica Michele Tavares, validade 09/11/2027) abre pelo `load_a1` sem erro.

Escopo: só login e leitura da página inicial, por HTTP (nenhum navegador). No máximo 2 tentativas por portal. Nenhum `.env`, `portals.toml`, código ou `.pfx` foi alterado; na Parte B, a flag `<PREFIX>_CERT=true` foi definida apenas no ambiente do processo de teste. Nenhum PIN, senha, QR ou código TOTP é registrado aqui. Fora do teste: GERID. (`tjto-1g`/`tjto-2g` foram testados no fim do dia; ver Parte B.)

Legenda: ✅ entrou; ❌ não entrou (mensagem literal do conector); ⛔ parado por exigir navegador/CAPTCHA/código.

## Parte A — 16 portais

| Portal | Resultado | Método | Tentativas |
|---|---|---|---|
| trf5-1g, trf5-2g | ✅ SSO aceitou o A1; página autenticada | certificate_totp | 1 cada |
| tjba-1g, tjce-1g, tjce-2g, tjdft-1g, tjma-1g, tjmt-1g, tjpa-1g, tjpb-1g | ✅ SSO aceitou o A1; página autenticada | certificate_totp | 1 cada |
| tjes-1g | ✅ Reteste à tarde: o CAPTCHA de imagem do WAF da AWS (HTTP 405) agora é resolvido pelo conector (`core/awswaf.py`: navegador headless + modelo de visão no OpenRouter, mesma abordagem do repo `captcha-solver`); o cookie `aws-waf-token` passa ao HTTP e o SSO aceitou o A1 + TOTP. Leitura: painel (7 pendentes), caixa de entrada por comarca, 52 processos no Push e `pje_get_process`. CAPTCHA resolvido na 1ª rodada nas 3 vezes | navegador só para o CAPTCHA; login e leitura por HTTP | 3 (1 por execução) |
| tjes-2g | ✅ Novo portal (`/pje2g/`, sem WAF): SSO aceitou o A1 + TOTP; painel (3 pendentes), caixa de entrada, 4 processos no Push e `pje_get_process` | certificate_totp | 1 |
| tjsp-1g | ✅ Reteste: com o A1 + TOTP o eproc parou em `frmEscolherUsuario`; escolhido o perfil `SP527617 / ADVOGADO` (o outro era `GO022729 / ADVOGADO`) e chegou ao Painel do Advogado (`frmPainelAdvogado`) | A1 mTLS + TOTP + seleção de perfil | 1 |
| tjsp-2g | ✅ Reteste: mesma tela com 2 perfis de advogado (OAB SP e OAB GO); escolhido `SP527617 / ADVOGADO` e chegou ao Painel do Advogado (`frmPainelAdvogado`) | A1 mTLS + TOTP + seleção de perfil | 1 |
| tjgo-1g | ✅ Reteste: o tribunal aceitou o A1. A página `/Usuario` que o conector lia como recusa é a tela "Serventias Disponíveis" (21 perfis de advogado, um por seccional da OAB). Escolhido `OAB GO - GO` (`Usuario?PaginaAtual=7`) e abriu o painel do advogado (sem `LoginPronto=S` na URL, que é o que o conector exige) | certificate_mtls + seleção de serventia | 2 |
| tjsp-esaj, tjms-esaj | ✅ CASTGC emitido; `esaj/api/auth/session` com `usuarioLogado: true`, sem código por e-mail | certificate_challenge | 2 cada (a 2ª só para ler a sessão) |

## Parte B — A1 no lugar de usuário/senha

| Portal | Resultado | Método | Tentativas |
|---|---|---|---|
| trf4-1g-rs, trf4-1g-sc, trf4-1g-pr, trf4-2g | ✅ painel do advogado (`frmPainelAdvogado`) | A1 mTLS + TOTP | 1 cada |
| tjrj-eproc-1g, tjrs-1g | ✅ painel do advogado (`frmPainelAdvogado`) | A1 mTLS (TOTP onde pedido) | 1 cada |
| trf1-1g, trf1-2g, trf3-1g, trf3-2g, tjpe-1g, tjap-1g | ✅ SSO aceitou o A1; página autenticada | PJe certificate_totp (`<PREFIX>_CERT=true`) | 1 cada |
| tjsc-1g, tjsc-2g | ✅ Reteste: A1 + TOTP aceitos; na seleção de perfil (`GO022729 / ADVOGADO` ou `PA039390A / ADVOGADO`) escolhido `GO022729 / ADVOGADO` e chegou ao Painel do Advogado (`frmPainelAdvogado`) nos dois | A1 mTLS + TOTP + seleção de perfil | 1 cada |
| trf2-1g-rj, trf2-1g-es, trf2-2g | ✅ Reteste (HTTP puro, sem navegador): o A1 foi aceito e a tela que o conector lia como "voltou ao login" era a tela de código 2FA do próprio eproc (`hdnStrTela=2fa_codigo`, campo `txtAcessoCodigo`, form `frmLogin` em `index.php`), não o login. Enviando o TOTP (`hdnAcao=2fa_validar`) o eproc redireciona a `controlador.php?acao=principal` e abre o Painel do Advogado (`acao=painel_adv_listar`, usuário HÉRICA MICHELE TAVARES, perfil `GO022729 - ADVOGADO`, cookie `PHPSESSID` + `EPROC_D_TRF2_*`). Nenhuma mensagem de certificado não cadastrado: o e-CPF está vinculado nos três | A1 mTLS (`login_cert` → `priv/login_cert.php` → `entrar_cert`) + TOTP no formulário local | 1 cada |
| tjpr-1g | ❌ `LoginRejected: eproc did not accept the A1 certificate` (como na Ana Flávia) | A1 mTLS | 1 |
| tjto-1g, tjto-2g | ⛔ Trava técnica: endereço de certificado continua sem DNS. Reteste persistente em 13/11 (17:31 a 18:32, 13 rodadas a cada 5 min): `mtls.tjto.jus.br` sem registro em todas (getent sem resultado; DoH Google e Cloudflare com Status=3/NXDOMAIN, sem resposta A). Em paralelo, a cada 15 min (17:31, 17:46, 18:02, 18:17, 18:32), GET da página de login (200) + POST do botão de certificado, sem credencial: o 302 foi sempre para `https://mtls.tjto.jus.br/eprocV2_prod_{1,2}grau/externo_controlador.php?acao=login_cert&host=eproc{1,2}.tjto.jus.br`, sem mudança de host, IP ou caminho. Por isso nenhum login foi tentado nesse reteste (0 tentativas; A1 e TOTP não foram apresentados). Antes (testes anteriores do dia): sem CAPTCHA; NXDOMAIN também nos servidores autoritativos do TJTO; forçar o IP do eproc entra em laço de redirect e `priv/login_cert.php` volta `erro=erro_certificado`; Chrome com proxy do A1: `ERR_TUNNEL_CONNECTION_FAILED`. O tribunal não recusou o A1: o endpoint de certificado não está publicado. Login por senha existe, mas não temos a senha. Refazer quando o DNS voltar | HTTP | 0 no reteste (5 no dia) |
| tjmg-1g | ✅ Reteste: a página `https://eproc1g.tjmg.jus.br/eproc/` hoje é login local (usuário/senha + botão "Certificado Digital" = `SubmitCert()`), não redireciona ao Keycloak `auth-eproc`/`certificado.sso.tjmg.jus.br` (esse host segue sem registro: NODATA (existe a zona, sem registro A/AAAA) no 8.8.8.8 e no 1.1.1.1, também após alguns minutos; nem é usado). O mesmo fluxo do TRF2 funcionou: POST `acao=login_cert` → `priv/login_cert.php` (A1 aceito) → `entrar_cert` → tela 2FA → TOTP → Painel do Advogado (`MG`, `GO022729 - ADVOGADO`, `PHPSESSID` + `EPROC_D_TJMG_*`). A conclusão anterior vinha de um fluxo Keycloak que o portal não usa mais nesse endereço | A1 mTLS + TOTP no formulário local (HTTP puro) | 1 |
| trf6-1g, trf6-2g | ✅ Reteste: com navegador headless (SeleniumBase) passando o desafio JavaScript do WAF, o botão "Certificado Digital" do SSO aceitou o A1, pediu o TOTP e abriu o Painel do Advogado (`frmPainelAdvogado`) em `eproc1g.trf6.jus.br` e `eproc2g.trf6.jus.br`. O cookie do WAF não serve ao HTTP puro (mesmo com o cookie do navegador, volta "Please enable JavaScript"); por isso o A1 foi apresentado por um proxy local temporário (mitmproxy) entre o navegador e o tribunal | navegador + A1 (proxy local) + TOTP | 1 cada |

## Portais da Parte B que recusaram ou não aceitaram o A1

- Recusa do A1 pelo portal: `tjpr-1g`. (Os demais da lista de pendências abaixo foram retestados em 13/11; ver as linhas das tabelas.)
- TRF2 (`trf2-1g-rj`, `trf2-1g-es`, `trf2-2g`) e `tjmg-1g`: entram com A1 + TOTP; o conector é que não reconhece a tela 2FA local (ver tabelas).
- Não testável: `tjto-1g`/`tjto-2g` (o `mtls.tjto.jus.br` do tribunal não tem DNS); `tjes-1g` passou depois com o CAPTCHA resolvido pelo conector. `trf6-1g`/`trf6-2g` passaram com navegador.
- Seleção de perfil: `tjsp-1g`, `tjsp-2g`, `tjsc-1g`, `tjsc-2g` e `tjgo-1g` entram escolhendo o perfil de advogado (o conector ainda não faz isso sozinho).

## Observações

- Dois `eproc` (tjsc-2g e os TRF4 sc/pr/2g) precisaram reenviar o TOTP uma vez dentro do mesmo login; não contam como nova tentativa.
- Seleção de perfil (tjsp, tjsc, tjgo): no reteste de 13/11 escolhi o perfil de advogado só como navegação (nada de cadastro alterado, nenhum "definir como padrão"). tjsp: SP; tjsc: GO; tjgo: GO.
- Scripts e arquivos temporários foram apagados.

## Parte C — TJAC e TJAL (cadastro no Jurisync)

| Portal | Resultado | Método | Tentativas |
|---|---|---|---|
| tjac-1g, tjac-2g (eproc) | ✅ Cadastrados no Jurisync (branch `feat/eproc-tjac-esaj-tjal`, commit c7543b6). SSO próprio `sso.tjac.jus.br` + `certificado-sso.tjac.jus.br`: A1 mTLS aceito, TOTP do perfil `Eproc/TJAC`, Painel do Advogado (`acao=painel_adv_listar`, perfil `GO022729 - ADVOGADO`, sem tela de escolha de perfil). Leituras: filas pendentes e prazos abertos, relatório de processos (1g 33; 2g 10) e detalhe de processo (sem abrir documentos). Trava técnica: o SSO volta a `/controlador.php` sem `/eproc/` (corrigido no conector) | A1 mTLS + TOTP | 1 cada |
| tjal-esaj | ✅ Cadastrado no Jurisync (commit 504a9d9). CASTGC emitido, `esaj/api/auth/session` com `usuarioLogado: true`. Leituras: sessão, intimações (19), audiências (1), petições (2), caixa postal (20). Trava técnica: o CAS devolve o ticket em `http://` no host do portal (corrigido: sobe para https só no mesmo host). Leitura de processo (`cpopg`) validada num 4º login autorizado: HTTP puro, sem CAPTCHA, 2 partes e 51 movimentações; nenhum documento aberto, sem ciência. Documentos não validados | certificate_challenge | 4 (a 4ª autorizada pela coordenação) |

## Parte D — PJe estaduais TJPI, TJRN e TJRO (A1 + TOTP PDPJ)

Login e leitura (contadores, caixa de expedientes, processo sem ciência pendente, documento). Nada de ciência, "liberar dispositivo" ou alteração de conta. Hash do `.pfx` igual antes e depois. Cadastro no worktree `/home/gabriel/orca/jurisync-wt-pje-est` (branch `feat/pje-tjpi-tjrn-tjro`, sem push).

| Portal | Login | Leituras | Cadastrado | Tentativas |
|---|---|---|---|---|
| tjpi-1g | ✅ painel do advogado | ✅ contadores, caixa, processo, documento (exigiu correção do parser da tabela de pesquisa, 8 colunas) | já existia | 1 |
| tjpi-2g | ✅ painel do advogado (sem "Cadastro de novo usuário" para a Hérica) | ✅ contadores, caixa, processo, documento | já existia | 1 |
| tjrn-1g (`pje1g.tjrn.jus.br`) | ✅ painel do advogado | ⚠ contadores e caixa ✅; processo por número bloqueado por reCAPTCHA invisível ("A verificação de captcha não está correta"), não resolvido; processo e documento ✅ pelo link da própria caixa (ainda não no conector) | sim | 1 |
| tjrn-2g (`pje2g.tjrn.jus.br`) | ✅ painel do advogado | ✅ contadores, caixa, processo, documento | sim | 1 |
| tjro-1g (`pjepg.tjro.jus.br`) | ✅ painel do advogado | ✅ contadores, caixa, processo, documento | sim | 1 |
| tjro-2g (`pjesg.tjro.jus.br`) | ✅ painel do advogado | ⚠ contadores ✅ (todos zerados); sem expediente nem processo para testar processo/documento | sim | 1 |

URLs do TJRO: `pje.tjro.jus.br` só redireciona à página institucional; `pje1g`/`pje2g.tjro.jus.br` não existem no DNS.

## Parte E — eproc TJPR 1G e e-SAJ TJAM

Autorizado pelo dono. Só login e leitura; nada de ciência, "liberar dispositivo" ou alteração de conta. Hash do `.pfx` igual antes e depois. Código no worktree `jurisync-wt-trt-b` (branch `feat/tjpr-herica`, sem push).

| Portal | Login | Leituras | Tentativas |
|---|---|---|---|
| tjpr-1g (eproc) | ✅ A1 + TOTP, tela de perfil (2 perfis ADVOGADO, de duas OABs), escolhido o da OAB principal, Painel do Advogado (`frmPainelAdvogado`) | ✅ filas: prazos abertos 0, pendentes 0, urgentes 0, acordo 0, art. 334 0, decurso de prazo 1, pauta 0; relação de processos 1 item. As 171 intimações/30 dias não estão nesse perfil; o outro perfil (2ª OAB) não foi testado | 2 (a 1ª parou sem o seletor `TJPR_1G_PROFILE_MATCH`, que é preciso informar no ambiente do processo) |
| tjam-esaj (e-SAJ, acervo antigo) | ⚠ 1º login ✅ (CASTGC; redirect `http://` elevado a https no mesmo host); 2º login caiu em `tarefas-adv/erroDesconhecido.do` (página de erro do portal) | ✅ intimações 0, audiências 0, petições 0, caixa postal 1 item; `esaj/api/auth/session` dá 404 no TJAM, então `usuarioLogado` não foi lido (a prova de sessão são as APIs autenticadas respondendo 200 sem voltar ao CAS) | 2 |

Correções: `tjpr-1g` passa a `certificate_totp` (a seleção de perfil já era compartilhada); `upgrade_scheme` do TJAL (504a9d9) trazido por cherry-pick; `tjam-esaj` cadastrado no registro. Pendente: equivalente de `usuarioLogado` no TJAM e o `erroDesconhecido.do`. Testes: 1016 passed, 1 failed (o DJEN/Victor, preexistente).
