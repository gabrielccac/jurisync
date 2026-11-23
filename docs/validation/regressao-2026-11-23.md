# Regressão ao vivo do `chore/hardening` (23/11/2026)

Código testado: worktree `jurisync-wt-hardening`, branch `chore/hardening` (0e5f779), importado com `PYTHONPATH=src` (`jurisync.__file__` conferido no worktree em cada script). Configuração e segredos lidos do checkout principal (`JURISYNC_ENV_FILE`, `JURISYNC_CUSTOMERS_DIR`, `JURISYNC_CUSTOMER`). Nenhum arquivo de código alterado, nada commitado.

Cada ferramenta foi chamada pela camada MCP (`await server.call_tool(nome, argumentos)`), com o servidor montado como no `main()`: `configured_portals()` → `offer_portals` (PJe) → `sessions.only`. Isso passa por `sessions.run`, pelo enum de portais, pelo bloco `ciencia` e pelas mensagens de erro com `[motivo]`.

Regras seguidas: só leitura; nunca `acknowledgeCiencia=true`; nenhum ato de expediente aberto; no máximo um login por portal e conta (tudo de um portal num só processo Python); PJe da mesma conta PDPJ com 40 s de intervalo; sem nova tentativa depois de falha de login. Nenhum segredo, cookie, código ou PIN foi impresso.

Contagem de requisições: `curl_cffi.requests.Session.request` foi envolvido para contar as requisições de cada chamada.

## Regressões e falhas novas

Nenhuma falha atribuível ao código novo foi confirmada. Duas leituras que funcionavam antes falharam hoje. Em ambas o caminho de código que falhou não mudou nesta branch. Por regra, os dois portais pararam na falha.

| Portal | Antes | Agora | Análise |
|---|---|---|---|
| fernanda · `trf6-1g` (Eproc, navegador) | ✅ 19/11 (login, avisos, 6 processos, documento) | ❌ `Eproc request failed: TRF6 eproc login page did not open in the browser (bot challenge or portal down)`. O erro veio **sem** etiqueta `[motivo]` | A falha acontece em `certificate._browser_login_page`, que não mudou em relação à `main`. O Chrome headless não chegou à página do SSO com o certificado, e nenhuma credencial foi enviada. Um GET público (`curl_cffi`) em `eproc1g.trf6.jus.br/eproc/` devolve 200, mas sem o redirecionamento ao SSO. O provável é ambiente ou portal (bloqueio anti-robô), não regressão. Não houve nova tentativa. **Lacuna do código novo:** esse `RuntimeError` não tem `reason`, então a ferramenta não mostra `[portal_unavailable]` nem `[captcha]`. |
| anaflavia · `tjgo-1g` (Projudi A1) | ✅ 17/11: 5847109-96.2026.8.09.0051 aberto (24 mov., 30 arquivos) | ✅ login, ✅ `projudi_list_intimations` (Diário 81, pendências do portal 0, completa), ✅ `projudi_list_processes` (12 categorias, completa). ❌ `projudi_get_process`: `Projudi request failed: Process not found or not accessible to this account` | `tjgo.open_process` e a busca não mudaram (o diff do `tjgo.py` só acrescenta motivos de login). O DJEN mostra comunicações desse processo em 16/11, então ele existe. Pode ter mudado de acesso (segredo ou redistribuição), ou a sequência listas → busca pode ter afetado a sessão. Isso não foi confirmado: a confirmação pediria um 2º login. Na próxima rodada, abrir um processo tirado de `projudi_list_processes`. |

## Resumo por portal

Legenda: req = requisições HTTP da chamada. "Reuso" compara `pje_get_process` e `pje_read_documents` seguidos no mesmo processo.

| Cliente · portal | Antes | Login | Contadores / caixa / Push | Processo | Documento | Ciência | Observações |
|---|---|---|---|---|---|---|---|
| fernanda · `trf1-1g` (PJe) | ✅ 19/11 | ✅ | 4/0/0/4/0/0/0; caixa 4 linhas; Push 20 (completa) | ✅ 1096425-27.2025.4.01.3300: 43 docs, 1 exp. (0 abertos), 5,0 s, 7 req | ✅ Petição inicial, `extracted` | `nenhuma` | **Reuso ✅**: a leitura fez 2 req (aba Expedientes + download), sem nova busca |
| fernanda · `trt1-1g` (PJe-JT/KZ) | ✅ 19/11 | ✅ | 23/4/7; caixa 4 linhas; Push 30 (20 lidos, `hasMore`) | ✅ 0101627-48.2026.5.01.0013: 19 docs, 2 exp. abertos, 6 req | ✅ Petição Inicial, `extracted` (a trava não a ligou aos expedientes abertos) | `nenhuma` | **Sem reuso**: a leitura fez 7 req. No KZ, `search_process` vai para `kz.search_process` antes da memória (`_recall`). É limitação, não regressão |
| fernanda · `tjsc-1g` (Eproc A1) | ✅ 19/11 | ✅ | pendentes 18 linhas (14 processos), urgente/acordo/334 0, prazo aberto 202 | ✅ lista 500 com `complete=false` (correção da legenda "1 a 500" confirmada); 0301787-15.2017.8.24.0175: 261 ev., 195 docs | ✅ `extracted`, 5.647 chars | `nenhuma` (processo fora das filas pendentes, sem "AGUARD. ABERTURA") | |
| fernanda · `trf6-1g` (Eproc navegador) | ✅ 19/11 | ❌ ver acima | — | — | — | — | Parou na falha |
| natalia · `tjmg-pje-1g` (PJe) | ✅ 19/11 (caixa vazia) | ✅ | 11/0/1/10/4/0/6; **caixa 11 linhas em 5 caixas** (em 19/11 vinha vazia); Push 20 (`complete=false`) | ✅ 5004935-56.2020.8.13.0261: 109 docs, 16 exp. (0 abertos), 9 req | ✅ Petição Inicial 1025579842: `short_html`, 29 chars (capa HTML; em 19/11 o documento lido foi outro, 1025694800) | `nenhuma` | **Reuso ✅**: 4 req na leitura, contra 9 na busca |
| natalia · `tjmg-1g` (Eproc) | ✅ 19/11 | ✅ | pendentes 7 linhas (6 processos), prazo aberto 20 | ✅ lista 50 (`complete=false`); 5001823-52.2023.8.13.0042: 136 ev., 103 docs | ✅ `extracted`, 4.773 chars | `nenhuma` | |
| anaflavia · `tjes-1g` (PJe + WAF) | ✅ 17/11 | ✅ CAPTCHA do WAF resolvido (login e contadores em 22 s) | todos 0; caixa 0; Push 3 | ✅ 5008810-33.2026.8.08.0012: 48 docs, 10 exp. (0 abertos), 7 req | ✅ Petição Inicial, `extracted` | `nenhuma` | **Reuso ✅**: 2 req na leitura |
| anaflavia · `tjgo-1g` (Projudi) | ✅ 17/11 | ✅ | intimações: Diário 81, portal 0 (completa); processos: 12 categorias | ❌ ver acima | — | — | Parou na falha |
| anaflavia · `stj` | ✅ 17/11 | ✅ A1 mTLS | 2 ativos, 3 encerrados, 0 vistas; caixa: 1 intimação DJEN em 30 dias; lista 5 | ✅ AREsp 3436926: 4 partes, 3 fases, 255 docs | ✅ doc 40, `extracted`, 44.615 chars (igual a 17/11) | `nenhuma` | |
| anaflavia · `tjms-esaj` (e-SAJ) | ✅ 17/11 | ✅ `usuarioLogado`, 2 OABs | intimações 0; caixa postal 5 | ✅ 0800497-21.2026.8.12.0014: 2 partes, 50 mov., 42 docs | ✅ 244887146, `extracted`, 34.137 chars | `nenhuma` | |
| max · `stj` | ✅ 19/11 | ✅ A1 mTLS | 1 ativo, 6 encerrados; caixa 0; lista 7 | não testado | não testado | — | Erro meu: passei "3465302" sem a classe. Não houve 2º login (regra) |
| juliana · `tjmg-pje-1g` (Push) | ❌ 23/11 (main): `Could not uniquely identify the PJe Push process grid` | ✅ | **Push ✅ 20** (`complete=false`) | — | — | — | **Correção do Push confirmada** |
| juliana · `tjes-1g` (Push) | ❌ 23/11 (main): mesmo erro | ✅ WAF resolvido | **Push ✅ 7** (completa) | — | — | — | **Correção do Push confirmada** |
| juliana · `tjba-1g` (doc 507664773) | ❌ 23/11 (main): `request_failed`, 0 chars, sem motivo | ✅ | — | ✅ 8020841-42.2025.8.05.0080: 18 docs, 6 exp. (0 abertos), 24,7 s, 19 req | ❌ `request_failed` de novo. **Agora com `failure: "request failed: Timeout"`** (32,6 s) | `nenhuma` | O campo `failure` funciona e mostra a causa: o TJBA não serve o arquivo a tempo. **Reuso ✅**: 2 req na leitura |

## Serviços

| Serviço | Resultado |
|---|---|
| DJEN `djen_search_communications` (OAB/PE 42543, 16 a 23/11) | ✅ 22 comunicações, 10 itens na página |
| DJEN `djen_list_saved_searches` / `djen_search_saved` (victor, TRF5, 16 a 23/11) | ✅ 3 buscas salvas: PE 42543 = 0, PB 14663 = 418, PE 48647 = 415 |
| Juridiq (allan; `.env` com `JURIDIQ_EMAIL`: allan, patricia, priscila, thaile, wesley e o `.env` raiz) | ✅ `juridiq_list_users`, `juridiq_list_pending_publications` (5, 2 páginas), `juridiq_list_lawsuits`, `juridiq_list_tasks`. Nenhuma escrita |
| ADVBOX (victor) | **Pulado.** O cookie guardado (`data/advbox-<hash>.json` do checkout principal) venceu há ~115 h, então seria preciso um login novo, que pode mandar código de aparelho ao escritório. O worktree não tem cache próprio (`STATE_DIR` = raiz do repositório) |

## Pulado e por quê

- **natalia · `tjmg-jpe-2g` (JPe):** o conector foi validado só offline (merge 462e838). Sem resultado ao vivo anterior para comparar, um 1º login fica fora do escopo de uma regressão.
- **natalia / max · e-SAJ e STJ:** natalia não tem nenhum dos dois no `portals.toml`. O e-SAJ foi coberto pela anaflavia (`tjms-esaj`), porque no `tjsp-esaj` do max a leitura de documento nunca tinha sido validada. O `stj` do max foi testado só até a lista (ver tabela).
- **ADVBOX:** ver acima.
- **Segunda tentativa em `trf6-1g`, `tjgo-1g` e no processo do max no STJ:** proibida pela regra de um login por portal e conta.

## Observações para o código

1. **Erro de login sem `[motivo]`:** `eproc/certificate.py::_browser_login_page` levanta `RuntimeError` ("login page did not open in the browser"). `tool_errors` só põe a etiqueta quando a exceção tem `reason`, por isso esse caso sai sem motivo. O certo seria um `LoginFailed(..., reason="portal_unavailable")` (ou `captcha`).
2. **Sem reuso no PJe-JT (KZ):** `search.search_process` desvia para `kz.search_process` antes de `_recall`. Então `pje_read_documents` refaz a busca no TRT (7 req, contra 2 no PJe clássico).
3. **`configured_portals()` amplo para A1:** com só o A1, o e-SAJ e o Projudi aparecem configurados para quem não os tem no `portals.toml`. A fernanda e o max recebem os 4 e-SAJ e o `tjgo-1g`. O enum oferece portais que o escritório não usa.
4. **TJBA 1G:** o download da petição 507664773 estoura o tempo (`failure: request failed: Timeout`). É do portal, não regressão. Investigar o tempo limite do download ou um caminho alternativo.
