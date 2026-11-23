# Validação completa ao vivo — natalia, max, denis, anaflavia (23/11/2026)

Código: worktree `chore/hardening` (d492951), chamado pela camada MCP como um agente faria. Os servidores foram montados
como no `main()` de cada conector: `configured_portals()`, `offer_portals`/`sessions.only` e `call_tool`. Tudo foi
somente leitura, com `acknowledgeCiencia=false` explícito em toda leitura. Nenhuma intimação pendente foi aberta e
nenhuma escrita foi feita. Os logins seguiram a regra de um por portal e conta (com uma exceção, explicada abaixo);
os do mesmo autenticador PDPJ rodaram em sequência, com 40 s ou mais entre eles. As quatro contas rodaram em paralelo,
porque são autenticadores diferentes. Nenhum segredo, cookie ou texto de documento foi registrado (no log os textos
estão cortados em 80 caracteres). Scripts e logs: `scratchpad/fullval/others/`.

Legenda: ✅ ok · ⚠️ funcionou com ressalva, ou não deu para testar · ❌ falhou · — não se aplica.

## Regressões e falhas do nosso código

**Nenhuma regressão.** Todas as utilidades que funcionavam antes continuam funcionando, e duas falhas antigas agora
passam:
- **tjgo-1g (anaflavia):** `projudi_get_process` voltou a abrir processos (2 de 2). A correção `_clear_search` da
  causa-raiz está confirmada ao vivo.
- **tjmg-pje-1g (natalia):** a caixa veio com 11 linhas em 5 caixas, `complete=true`. Em 19/11 ela vinha vazia.
- **trf5-legacy (max):** contadores (19 abas) e caixa ("Intimações / Pendentes", 0) passaram a responder. Em 19/11 o
  conector dizia "not supported". Cada chamada leva cerca de 50 s.

Ressalvas do nosso código (nada foi corrigido):
1. **Push do PJe "clássico" com mais de uma página (`pje_list_watched_processes`), em tjmg-pje-tr e tjmg-pje-1g:**
   o conector lê só a 1ª página. O portal informa `total_pages` 4 e 10 e `has_next_page=true`, e
   `pagination.complete=false`. Mesmo assim a ferramenta devolve `total=20` e `hasMore=false`, e no tjmg-pje-tr ainda
   `results_count=0` com 20 linhas. Um agente que olhe só `total`/`hasMore` conclui que há 20 processos no Push. A causa
   está em `push._window`, que calcula `total = max(results_count or 0, len(items))` sem considerar as páginas que
   faltam. O problema já era conhecido como "cosmético" em 17/11, mas o `hasMore=false` engana.
2. **JPe (`tjmg-jpe-2g`, natalia):** o login pelo PDPJ recusou do lado do tribunal, antes de qualquer credencial
   (SSO HTTP 400, `redirect_uri` inválido para o client `jpe-tjmg-2g`, igual a 19/11). A mensagem é clara, mas o motivo
   sai como `[unexpected]`. Nenhuma outra utilidade do JPe pôde ser testada, porque a conta não tem o login próprio do
   JPe (`TJMG_JPE_2G_LOGIN`).
3. **tjba-2g:** a "Petição Inicial" (37611012) voltou `short_html` com 9 caracteres. É a capa HTML: o PDF da inicial
   é outro documento, como já acontecia no tjpi-1g em 17/11. Não é erro, mas a escolha "pelo título" engana o agente.

## natalia

| Portal | Login | contadores | caixa (maxPages=1) | Push | get_process | read_documents | read_expediente_ato |
|---|---|---|---|---|---|---|---|
| trt6-1g | ✅ 5,4 s | ✅ Acervo 1, Arquivados 1 | ✅ 0, completa | ✅ 0 | ✅ 0000613-80.2026.5.06.0401: 31 docs, 3 exp. (0 pendentes) | ✅ Manifestação, `extracted`, 823 car., ciência `nenhuma` | ✅ exp. 18806495 (ciência já dada): texto lido, `nenhuma` |
| trt10-1g | ✅ | ✅ Acervo 3, Meus Exp. 2, Arquivados 3 | ✅ 2 linhas, completa | ✅ 0 | ✅ 0000724-87.2025.5.10.0013: 135 docs, 61 exp. (0 pendentes) | ✅ Certidão, `extracted`, 575 car., `nenhuma` | ✅ exp. 11477916, `nenhuma` |
| trt17-1g | ✅ | ✅ Acervo 3, Meus Exp. 1 | ✅ 1, completa | ✅ 3 | ✅ 0001395-59.2025.5.17.0161: 64 docs, 10 exp. | ✅ Certidão DJEN, `extracted`, 339 car., `nenhuma` | ✅ exp. 8837675, 962 car., `nenhuma` |
| trt19-1g | ✅ | ✅ Acervo 1, Arquivados 4 | ✅ 0 | ✅ 0 | ✅ 0000216-14.2026.5.19.0060: 94 docs, 14 exp. | ✅ Despacho, `extracted`, `nenhuma` | ✅ exp. 4089132, `nenhuma` |
| trt24-1g | ✅ | ✅ Acervo 1, Arquivados 4 | ✅ 0 | ✅ 0 | ✅ 0024351-16.2026.5.24.0072: 86 docs, 32 exp. | ✅ Despacho, `extracted`, `nenhuma` | ✅ exp. 6265531, `nenhuma` |
| tjmg-pje-tr | ✅ | ✅ 7 buckets (pendentes 2) | ✅ 2 linhas, completa | ⚠️ 20 linhas de 4 páginas, `hasMore=false` (ressalva 1) | ✅ 5006706-93.2025.8.13.0261: 84 docs, 5 exp. | ✅ Certidão de julgamento, `extracted`, 1.172 car., `nenhuma` | ✅ exp. 97406601, `nenhuma` |
| tjmg-pje-1g | ✅ | ✅ pendentes 11, ciência Judiciário 10, prazo findo 4, respondidos 6 | ✅ 11 linhas em 5 caixas, completa (19,2 s) | ⚠️ 20 de 10 páginas, `hasMore=false` (ressalva 1) | ✅ 5004935-56.2020.8.13.0261: 109 docs, 16 exp. | ✅ Despacho, `extracted`, 678 car., `nenhuma` | ✅ exp. 3005043178, `nenhuma` |
| tjmg-jpe-2g (JPe) | ❌ `[unexpected]` SSO 400 `redirect_uri` (lado do TJMG, nenhuma credencial enviada) | — | — | — | — | — | — |
| trt5-1g | ⚠️ não validado | | | | | | |

**trt5-1g (natalia):** foi gasto **1 login acidental**, às 12h31, num teste de validação de argumentos do meu script. O
`pje_get_process` com um número de outro tribunal faz o login antes de recusar o número. O login foi aceito e nada
foi lido. Para não fazer um 2º login, o portal ficou fora. O sistema PJe-JT/TRT5 foi coberto pela conta do max (abaixo).

## max

| Portal | Login | contadores | caixa | Push | get_process | read_documents | read_expediente_ato |
|---|---|---|---|---|---|---|---|
| trt5-1g | ✅ | ✅ Acervo 1.051, Meus Exp. 160, Arquivados 1.112 | ✅ 17 caixas na 1ª página (`complete=false`, esperado com maxPages=1) | ✅ 100 de 2.111 (`hasMore=true`) | ✅ 0001912-67.2026.5.05.0192: 12 docs, 0 exp. | ✅ Certidão de Distribuição, `extracted`, `nenhuma` | ⚠️ não testado: o processo aberto não tinha expediente |
| trt5-2g | ✅ | ✅ Acervo 354, Meus Exp. 28, Arquivados 668 | ✅ 9 caixas, completa | ✅ 100 de 401 | ✅ 0000441-61.2025.5.05.0641: 93 docs, 0 exp. | ✅ Certidão, `extracted`, 358 car., `nenhuma` | ⚠️ idem |
| trt20-1g | ✅ | ✅ Acervo 21, Meus Exp. 1, Arquivados 4 | ✅ 1, completa | ✅ 25 | ✅ 0001132-89.2026.5.20.0005: 23 docs | ✅ Habilitação, `extracted`, 967 car., `nenhuma` | ⚠️ idem |
| trt20-2g | ✅ | ✅ Baixados/Arquivados 2 | ✅ 0 | ✅ 0 | ⚠️ sem processo (acervo e Push vazios) | ⚠️ | ⚠️ |
| tjba-2g | ✅ | ✅ 8 buckets (pendentes 80) | ✅ 2 caixas (1ª página) | ✅ 100 de 418 | ✅ 8019223-67.2022.8.05.0080: 84 docs, 2 exp. (0 pendentes) | ⚠️ Petição Inicial = capa `short_html`, 9 car. (ressalva 3), `nenhuma` | ✅ exp. 5261561 (ciência já dada), `nenhuma` |
| trf5-legacy (PJe 1.x) | ✅ A1 | ✅ 19 abas, todas 0 (52 s) | ✅ "Intimações / Pendentes": 0 (49 s) | ✅ 0 | ⚠️ sem processo | ⚠️ | ⚠️ |
| tjrj-eproc-2g | ✅ | `eproc_list_notices`: ✅ 7 filas, todas 0 | — | `eproc_list_processes`: ✅ 0 (`filters` null) | ⚠️ sem processo | ⚠️ | — |
| stj | ✅ A1 | `stj_get_counters`: ✅ 1 ativo, 6 encerrados | `stj_list_inbox`: ✅ 0 vistas, 0 intimações em 30 dias | `stj_list_publications`: ✅ 9; `stj_list_processes`: ✅ 7 | ✅ AREsp 3465302 (CNJ 8000372-97.2016.8.05.0109) | ✅ doc 3 Petição Inicial, `extracted`, 18.686 car., `nenhuma` | — |
| tjsp-esaj | ✅ A1 | `get_session`: ✅ `usuarioLogado`, 1 OAB | `list_messages`: ✅ 3; `list_tasks`: ✅ intimações 0, audiências 0, petições 0 | — | ✅ 1070215-24.2013.8.26.0100 (1.000 mov., truncado, 21 s) e 1504934-63.2021.8.26.0624 (186 mov.) | ⚠️ `list_documents`: `password_required` nos 2 (conta não vinculada aos autos, igual a 19/11). `read_document` não testado | — |

Foi a 1ª vez que `stj_get_process` e `stj_read_documents` funcionaram na conta do max (em 23/11 a manhã tinha parado
na lista).

## denis

| Portal | Login | Filas / contadores | caixa | lista / Push | get_process | documento | ato |
|---|---|---|---|---|---|---|---|
| trt7-1g | ✅ | ✅ Acervo 4, Meus Exp. 1, Arquivados 59 | ✅ 1, completa | ✅ Push 51 (completo) | ✅ 0001840-39.2026.5.07.0026: 14 docs, 2 exp. | ✅ Certidão de arquivamento, `extracted`, 465 car., `nenhuma` | ✅ exp. 9557696, `nenhuma` |
| trt7-2g | ✅ | ✅ Acervo 2, Baixados 1 | ✅ 0 | ✅ Push 0 | ⚠️ sem número (Push e caixa vazios; não há ferramenta de acervo) | ⚠️ | ⚠️ |
| tst | ❌ `[not_registered]` "the SSO accepted the certificate and the TOTP code, but this user is not registered in this PJe (cadastro.seam)… Retrying will not help." | — | — | — | — | — | — |
| tnu (eproc) | ✅ (28 s, navegador) | ✅ 7 filas, todas 0 | — | ✅ 0 processos (`filters` null) | ⚠️ sem processo | ⚠️ | — |
| tjsp-1g (eproc) | ✅ | ✅ 7 filas, todas 0 | — | ✅ 1 processo (`complete=true`) | ✅ 4006734-80.2026.8.26.0229: 11 eventos, 10 docs, ciência `nenhuma` | ⚠️ não lido, **por erro do meu script**: os ids do inventário do eproc têm 30 dígitos e o meu filtro só aceitava até 20. O conector devolveu o inventário corretamente | — |
| tjsp-2g (eproc) | ✅ | ✅ 7 filas, todas 0 | — | ✅ 0 (`filters` null) | ⚠️ sem processo | ⚠️ | — |

O tjsc-1g e o tjsc-2g ficaram fora: o último registro (13/11) é de TOTP recusado ("Código autenticador inválido.").
O tjto também ficou fora, pelo formato do usuário. O tst foi tentado uma vez: o novo código classifica o resultado
como `[not_registered]` e não repete o login (era o bug 5 de 17/11).

## anaflavia

| Portal | Login | Filas / contadores | caixa / intimações | lista / Push | get_process | documentos | leitura / ato |
|---|---|---|---|---|---|---|---|
| tjpi-1g | ✅ | ✅ 7 buckets, todos 0 | ✅ 0 | ✅ Push 2 | ✅ 0805201-11.2026.8.18.0176: 68 docs, 16 exp. (0 pendentes) | ✅ Procuração, `extracted`, `nenhuma` | ✅ exp. 18696018, `nenhuma` |
| tjpe-2g | ❌ `[not_registered]` (cadastro.seam), sem 2ª tentativa | — | — | — | — | — | — |
| tjpi-2g | ❌ `[not_registered]` (cadastro.seam), sem 2ª tentativa | — | — | — | — | — | — |
| stj | ✅ A1 | `get_counters`: ✅ 2 ativos, 3 encerrados | `list_inbox`: ✅ 0 vistas, 1 intimação DJEN em 30 dias | `list_publications`: ✅ 4; `list_processes`: ✅ 5 | ✅ AREsp 3436926 | ✅ doc 7 Decisão, `extracted`, 13.190 car., `nenhuma` | — |
| tjgo-1g (Projudi) | ✅ A1 | `list_intimations`: ✅ Diário 83, pendências do portal 0, completa | — | `list_processes`: ✅ 12 categorias, completa | ✅ 5747771-52.2026.8.09.0051 (42 mov., 73 docs) e 5566917-63.2026.8.09.0051 (61 mov., 44 docs). **Corrigido desde a regressão** | `list_documents`: ✅ 24 e 23 movimentos com arquivos | ⚠️ `read_documents` não testado, **por erro do meu script**: os ids de arquivo do Projudi têm 27 dígitos e passaram do mesmo filtro de 20 |
| tjms-esaj | ✅ A1 | `get_session`: ✅ 2 OABs | `list_messages`: ✅ 5; `list_tasks`: ✅ intimações 0, audiências 0, petições 2 | — | ✅ 0800497-21.2026.8.12.0014 | `list_documents`: ✅ 42 | ✅ `read_document` 244887146, PDF 3,3 MB, `extracted`, 34.137 car., `nenhuma` |

O tjrj-2g ficou fora (sem cadastro). O tjpe-2g e o tjpi-2g foram tentados uma vez cada, porque o registro de 17/11
era de cadastro, não de credencial recusada. A mensagem agora é a correta, `[not_registered]`, sem a 2ª tentativa que
gastava outro TOTP. Isso confirma ao vivo a correção do bug 5 (`ssoCadastroCallback`).

## Logins

Um por portal e conta, exceto o login acidental do trt5-1g da natalia (ver acima). Não houve 2ª tentativa depois de
recusa, nem CAPTCHA. Logins PDPJ feitos em sequência por conta: natalia 12h32→12h38, max 12h33→12h36, denis
12h33→12h34, anaflavia 12h33→12h34, todos com 40 s ou mais entre um e outro.

## Pendências para fechar a cobertura (cada uma pede um novo login)

- denis `tjsp-1g` (eproc): ler 1 documento de 4006734-80.2026.8.26.0229.
- anaflavia `tjgo-1g`: `projudi_read_documents` em 5747771-52.2026.8.09.0051.
- max `tjsp-esaj`: ler documento quando houver processo vinculado à conta.
- `pje_read_expediente_ato` no TRT5 e no TRT20: abrir um processo que tenha expediente com ciência já dada.
- natalia `trt5-1g`: a rodada completa.
- Clientes: TST (denis), TJPE 2G e TJPI 2G (anaflavia) precisam do cadastro do advogado no tribunal. O JPe da natalia
  precisa do login próprio do JPe, ou de o TJMG corrigir o client.
