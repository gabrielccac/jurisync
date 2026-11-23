# Validação nível 2 (processo, documentos, leitura) nos portais de lista vazia: 23/11/2026

Código: worktree `jurisync-wt-hardening`, branch `chore/hardening` (dea11c3). Importado com `PYTHONPATH=src`, e `jurisync.__file__` aponta para o worktree. `.env` e `customers/` vêm do checkout principal. Nenhum código, teste, `.env` ou `portals.toml` foi alterado, e nada foi commitado.

As ferramentas foram chamadas pela camada MCP (`await server.call_tool(...)`), com os servidores montados como no `main()`:
- PJe: `configured_portals()` → `offer_portals` → `sessions.only`.
- Eproc: `configured_portals()` → `sessions.only` → `registry.get("eproc").load()`.
- DJEN: `registry.get("djen").load()`.

Os 13 portais pedidos estão em `configured_portals()` das contas usadas. No `tjsp-2g`, a conta escolhida foi o **denis**: ele tem o portal configurado, entrou em 23/11 e a lista estava vazia. A anaflavia não tem `tjsp-2g`, e a herica já tinha processos nele.

Regras seguidas:
- **Um login por portal e conta.** O 1º login de cada portal foi a própria ferramenta de lista. Os portais da mesma conta rodaram em sequência, com 40 s entre um e outro, e as quatro contas rodaram em paralelo (14h08). Não houve recusa, CAPTCHA nem segunda tentativa.
- **DJEN antes de qualquer login.** O DJEN não pede autenticação.
- **Só leitura.** `acknowledgeCiencia=false` foi passado de forma explícita. Nenhuma intimação pendente foi aberta e nada foi escrito.
- **No PJe**, o documento lido nunca é de expediente.
- **No Eproc**, o documento só é lido quando o `eproc_get_process` sem documentos devolve `ciencia.estado="nenhuma"`.
- Neste relatório não há texto de documento nem nome de parte. O log guarda no máximo 60 caracteres de cada texto.

Legenda: ✅ ok · ⚠️ ressalva ou não exercido · ❌ falha.

## Resumo

| Conta | Portal | Lista (nova) | Fonte do número | `get_process` | Documento lido | Resultado |
|---|---|---|---|---|---|---|
| fernanda | `trf5-2g` (Turmas Recursais) | `pje_list_processes`: 0, completa | DJEN público (TRF5, host pje2g) | ✅ 129 docs, 12 exp. | ✅ Acórdão `extracted`, `nenhuma` | ✅ |
| fernanda | `trf5-tru` (2º Grau/TRU) | `pje_list_processes`: 0, completa | DJEN público (TRF5, host pjett) | ✅ 50 docs, 16 exp. | ✅ Acórdão `extracted`, `nenhuma` | ✅ |
| fernanda | `tjmg-2g` (Eproc) | `eproc_list_processes` (maxPages=20): 0, completa | DJEN público (host eproc2g.tjmg) | ✅ 2 processos abertos, `nenhuma` | ⚠️ não lido (roteiro) | ⚠️ |
| fernanda | `tjms-eproc-1g` | 0, completa | DJEN público (host eproc1g.tjms) | ✅ 3 processos abertos, `nenhuma` | ⚠️ não lido (roteiro) | ⚠️ |
| fernanda | `trf6-2g` | 0, completa (19,4 s) | DJEN público (host eproc2g.trf6) | ✅ 3 processos abertos, `nenhuma` | ⚠️ não lido (roteiro) | ⚠️ |
| herica | `trf2-2g` | 0, completa | DJEN público (host eproc.trf2) | ✅ | ✅ Acórdão `extracted`, `nenhuma` | ✅ |
| herica | `tjpr-2g` | 0, completa | nenhuma (o DJEN não tem comunicação do eproc2g.tjpr em 120 dias) | — | — | ⚠️ sem número |
| herica | `tnu` | 0, completa | DJEN público (CJF, host eproctnu) | ✅ 108 eventos, 26 docs | ✅ Acórdão `extracted`, `nenhuma` | ✅ |
| denis | `trt7-2g` | `pje_list_processes`: **3**, completa | **lista própria** (Acervo) | ✅ 60 docs, 0 exp. | ✅ Acórdão `extracted`, `nenhuma` | ✅ |
| denis | `tjsp-2g` | 0, completa | DJEN público (host eproc2g.tjsp) | ✅ | ✅ Acórdão `extracted`, `nenhuma` | ✅ |
| max | `trt20-2g` | `pje_list_processes`: **2**, completa | **lista própria** (Acervo) | ✅ 227 docs, 20 exp. (0 pendentes) | ❌ Acórdão `http_error`, sem `failure` | ❌ (ver falha 1) |
| max | `tjrj-eproc-2g` | 0, completa | DJEN público (host eproc2g.tjrj) | ✅ 2 processos abertos, `nenhuma` | ⚠️ não lido (roteiro) | ⚠️ |
| max | `trf5-legacy` (PJe 1.x) | `pje_list_processes` recusado, como documentado | DJEN público (números antigos do TRF5 2G) | ⚠️ 3 × "Process not found in the PJe 1.x search" | — | ⚠️ sem processo visível |

**Lista do advogado:** o `pje_list_processes`, que é novo, achou processos onde a caixa e o Push estavam vazios em 23/11: 3 no `trt7-2g` e 2 no `trt20-2g`. Nos outros 10 portais em que roda, todas as listas vieram vazias e completas:
- no PJe, `complete=true` e 0 jurisdições;
- no Eproc, `totalItems=0` e `complete=true`, com 1 página lida.

Na conta do advogado, o DJEN (OAB/UF, por tribunal, de 26/07 a 23/11) não tem nenhuma comunicação no host destes portais:
- fernanda SC 35675: TRF5 só no `trf5-1g`, TRF6 só no `trf6-1g`, TJMG só no `tjmg-1g`;
- herica GO 22729 e SP 527617: TJPR só no `tjpr-projudi-1g`;
- denis CE 29046: TJSP só no `tjsp-1g`;
- max BA 32060: nenhuma comunicação.

Por isso a fonte foi o passo 3: processo público do mesmo tribunal e da mesma instância, achado no DJEN sem o filtro de advogado, pelo host do link ou pelo órgão (Turma ou Gabinete de desembargador, no TRT).

## Falhas do nosso código

1. **❌ PJe-JT (KZ), `pje_read_documents` com `http_error` e sem `failure` (`trt20-2g`, max).**
   - Caso: processo 0000185-40.2023.5.20.0005 (Acervo do advogado), documento 9365013 "Acórdão". Ele não é de expediente, e a ciência deu `nenhuma`.
   - Resultado: `extractionStatus="http_error"`, 0 caracteres, `failure` ausente, 3 req, 0,9 s.
   - Causa no código: `kz.read_documents` (`src/jurisync/connectors/pje/kz.py`, por volta da linha 844) marca `http_error` quando `fetch_document` volta com `content=None` (status ≠ 200), mas não preenche `failure`. Só fica `status` (o código HTTP, que meu roteiro não registrou).
   - O caminho clássico (`documents.py:218`) preenche `failure=_http_failure(...)`. A descrição da ferramenta promete "for one not read a failure saying why".
   - Por que o portal recusou ficou sem resposta: tentei repetir com a sessão salva e o login novo bloqueado, mas não havia sessão salva do KZ. Nenhum login foi feito nessa tentativa.
2. Nenhuma outra falha. As recusas abaixo são do desenho:
   - `pje_list_processes` no `trf5-legacy`: "trf5-legacy is a PJe 1.x panel; the Acervo listing covers PJe 2.x and the PJe-JT panels", em 0,0 s e com 0 req, como a descrição avisa.
   - "Process not found in the PJe 1.x search": a mensagem é clara, mas sai sem `[motivo]` (`no-tag`).

## Lacunas por erro do meu roteiro (pela regra de um login, não repeti)

**Eproc `tjmg-2g`, `tjms-eproc-1g`, `trf6-2g` e `tjrj-eproc-2g`: o documento não foi lido.**
- Herdei do roteiro da herica uma trava que pula todo processo com evento "AGUARD. ABERTURA" na linha do tempo.
- Em processo de terceiros esse evento é a intimação das partes, não da conta. O conector devolveu `ciencia.estado="nenhuma"` nos 10 processos abertos (2 + 3 + 3 + 2), ou seja, a leitura era segura.
- O Eproc não guarda sessão em disco, então ler o documento agora exige um 2º login em cada um desses 4 portais.
- O que foi validado neles:
  - a lista paginada (`maxPages=20`);
  - o `eproc_get_process` em processo de terceiro, com eventos, inventário e `ciencia`, de 1,5 a 7,3 s. A 1ª chamada fez 8 req e as seguintes 3 req: a sessão foi reaproveitada.

## Detalhe por portal

### fernanda (PJe com A1 + TOTP; Eproc)

- **`trf5-2g`:**
  - `pje_list_processes`: 3,5 s, 10 req (login), 0 itens, `complete=true`.
  - DJEN público TRF5 de 1ª Relatoria TR/SE, host `pje2g.trf5.jus.br`.
  - `pje_get_process` 0012814-39.2024.4.05.8500: 10,2 s, 8 req, 129 docs, 16 eventos, 12 expedientes `complete=true` (3 pendentes das partes).
  - `pje_read_documents`: Acórdão 30913737, fora dos expedientes, `extracted`, 2.000 caracteres (textLimit), 2 req, `ciencia=nenhuma`.
- **`trf5-tru`:**
  - `pje_list_processes`: 4,6 s, 0 itens, `complete=true`.
  - DJEN público TRF5 de Gabinete de desembargador, host `pjett.trf5.jus.br`.
  - `pje_get_process` 0003579-66.2026.4.05.0000: 10,8 s, 50 docs, 16 expedientes `complete=true`.
  - Leitura: Acórdão 14119705, `extracted`, 2 req, `nenhuma`.
- **`tjmg-2g`:**
  - Lista: 4,8 s, 0, completa.
  - DJEN público TJMG pelo host `eproc2g.tjmg.jus.br`: só 2 comunicações em 3 semanas, do "1º Núcleo – Especializado".
  - `eproc_get_process` 5258603-82.2023.8.13.0024 (42 eventos, 6 docs) e 5204136-90.2022.8.13.0024 (27 eventos, 3 docs): `nenhuma`.
  - Documento: ⚠️ roteiro.
- **`tjms-eproc-1g`:**
  - Lista: 3,5 s, 0, completa.
  - DJEN público TJMS, host `eproc1g.tjms.jus.br`, varas de Corumbá.
  - 3 processos abertos (6 a 9 eventos, 1 doc cada), `nenhuma`.
  - Documento: ⚠️ roteiro.
- **`trf6-2g`:**
  - Lista: 19,4 s (login pelo Chrome), 0, completa.
  - DJEN público TRF6, host `eproc2g.trf6.jus.br`.
  - 3 processos 6xxxxxx-xx.20xx.4.06.0000 abertos (43 a 62 eventos), `nenhuma`.
  - Documento: ⚠️ roteiro.

### herica (Eproc)

- **`trf2-2g`:**
  - Lista: 3,3 s, 0, completa.
  - DJEN público TRF2, host `eproc.trf2.jus.br`. O 1º candidato foi pulado pela trava do roteiro.
  - 5014548-39.2026.4.02.0000: 36 eventos, 7 docs, `nenhuma`.
  - Leitura (`scope=page` + 1 id): Acórdão, `extracted`, 6.262 caracteres, 5 req, `nenhuma`.
- **`tjpr-2g`:**
  - Lista: 9,4 s, 0, completa.
  - Sem número. No DJEN, "eproc2g" e "eproc2g.tjpr" deram 0 resultados de 01/10 a 23/11. A varredura de 600 comunicações do TJPR com "eproc" (10 a 23/11) só trouxe `eproc1g.tjpr` e `projudi`.
  - O 2º grau do TJPR ainda publica pelo Projudi. Não chamei `get_process` com número de outro sistema.
- **`tnu`:**
  - Lista: 5,5 s, 16 req, 0, completa.
  - DJEN público, tribunal `CJF`, host `eproctnu.cjf.jus.br`. O filtro `TNU` deu HTTP 500 ou 429 no DJEN.
  - 5009038-33.2023.4.04.7004: 108 eventos (2 páginas), 26 docs, `nenhuma`.
  - Leitura: Acórdão, `extracted`, 3.378 caracteres, 6 req, `nenhuma`.

### denis

- **`trt7-2g`:**
  - `pje_list_processes`: 3,6 s, 16 req, **3 processos** em 2 jurisdições, `complete=true`. Em 23/11 o Push e a caixa estavam vazios.
  - `pje_get_process` 0000918-95.2026.5.07.0026: 1,3 s, 60 docs, 0 expedientes.
  - Leitura: "Acórdão TST" (documento juntado), `extracted`, 3 req, `nenhuma`.
- **`tjsp-2g`:**
  - Lista: 12,2 s, 0, completa.
  - DJEN público TJSP, host `eproc2g.tjsp.jus.br`.
  - 1054260-80.2024.8.26.0224: 46 eventos, 5 docs, `nenhuma`.
  - Leitura: Acórdão, `extracted`, 2.866 caracteres, 5 req, `nenhuma`.

### max

- **`trf5-legacy` (PJe 1.x, A1 sem TOTP).** Tempo da caixa, pedido nesta rodada:

  | Chamada | Tempo | req |
  |---|---|---|
  | `pje_list_inbox` padrão, 1ª (com o login A1) | **14,8 s** | 6 |
  | `pje_list_inbox` padrão, 2ª (sessão pronta) | **12,1 s** | 3 |
  | 23/11 antes da correção (caixa, sessão pronta) | 49,0 s | — |

  A correção está confirmada ao vivo. A caixa para no balde "Intimações / Pendentes" (índice 2, 0 itens) e não abre RPV/PRC nem Pauta: de 49 s caiu para cerca de 12 s, como o esperado de cerca de 11 s.
  - `pje_list_processes`: recusado, como documentado.
  - `pje_get_process` com 3 números antigos do 2º grau do TRF5, achados no DJEN e hoje no pjett: 0802341-91.2017, 0804155-70.2019 e 0805039-70.2017.4.05.0000. Os três deram "Process not found in the PJe 1.x search (the account may not see it)", em cerca de 3 s e 2 req cada.
  - O DJEN não tem nenhum link para `pje.trf5.jus.br` em 120 dias: as buscas pelo host só trazem `pjett` e `pje1g`. O detalhe do processo e a leitura de documento no PJe 1.x continuam sem validação ao vivo.
- **`trt20-2g`:**
  - `pje_list_processes`: 12,7 s, 15 req, **2 processos**, `complete=true`.
  - `pje_get_process` 0000185-40.2023.5.20.0005: 2,4 s, 227 docs, 20 expedientes, 0 pendentes.
  - Leitura: ❌ `http_error` sem `failure` (falha 1).
- **`tjrj-eproc-2g`:**
  - Lista: 5,2 s, 16 req, 0, completa.
  - DJEN público TJRJ, host `eproc2g.tjrj.jus.br` (3ª Câmara de Direito Público e Plantão).
  - 2 processos abertos (29 e 8 eventos, 1 doc cada), `nenhuma`.
  - Documento: ⚠️ roteiro.

## Observações

- **DJEN:** em rajada, a busca pública (sem advogado) leva HTTP 429. O filtro `tribunal=TNU` deu HTTP 500, e as comunicações da TNU saem com `tribunal=CJF`.
- **DJEN, campo `portal`:** o mapeamento pelo host do link funcionou em todos os portais testados (`pje2g`/`pjett`/`eproc2g.*`/`eproctnu`). No PJe-JT o host é o mesmo para 1º e 2º graus, e a comunicação de 2º grau sai como `trt7-1g`/`trt20-1g`. Só o órgão (Turma, Gab. Des.) mostra que é 2º grau.
- **Bloco `ciencia`:**
  - Todo `pje_read_documents` e toda leitura do Eproc devolveram `ciencia.estado="nenhuma"`.
  - Todo `eproc_get_process` sem documentos devolveu `nenhuma`, com `motivo=None`.
  - Nenhum `extracted_ocr` apareceu.

## Pendências (cada uma pede novo login, com autorização)

- Ler 1 documento nos 4 Eproc que ficaram pelo roteiro: `tjmg-2g`, `tjms-eproc-1g` e `trf6-2g` (fernanda), e `tjrj-eproc-2g` (max). Os números estão acima.
- `trt20-2g` (max): ver o status HTTP do Acórdão 9365013, ou ler outro documento. Corrigir o `failure` ausente no KZ.
- `trf5-legacy`: falta um processo que a conta veja (do próprio advogado) para validar o detalhe e o documento.
- `tjpr-2g`: falta um processo no eproc 2º grau do TJPR.

Scripts e logs (sem segredos): `/tmp/claude-1000/-home-gabriel-orca-jurisync/9c86fc6a-8f2f-4b15-beb8-88573e49d6f7/scratchpad/fullval/level2/`. Lá estão `run2.py`, `cands.json`, `{fernanda,herica,denis,max}.log`, `results.jsonl`, `djen_*.log` e `trt20_followup.log`.
