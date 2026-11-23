# Fernanda (Gonçalves Guimarães): validação completa em 23/11/2026

Código testado: worktree `jurisync-wt-hardening`, branch `chore/hardening` (d492951). Importado com `PYTHONPATH=src` e `jurisync.__file__` conferido no worktree. Configuração e segredos lidos do checkout principal (`JURISYNC_ENV_FILE`, `JURISYNC_CUSTOMERS_DIR`, `JURISYNC_CUSTOMER=fernanda`). Nenhum arquivo de código alterado, nada commitado.

As ferramentas foram chamadas pela camada MCP (`await server.call_tool(nome, argumentos)`), com os servidores montados como no `main()`. No PJe: `configured_portals()` → `offer_portals` → `sessions.only`. No Eproc: `configured_portals()` → `sessions.only` → `create_server(keys)`. Para contar as requisições HTTP de cada chamada, `curl_cffi` e `requests` foram envolvidos.

`configured_portals()` oferece **41 portais**: 22 PJe e 19 Eproc. Nenhum e-SAJ, Projudi, STJ, Tucujuris ou JPe aparece. A lacuna 3 da regressão (e-SAJ e `tjgo-1g` oferecidos só por causa do A1) não aparece mais para esta cliente.

Regras seguidas:
- Um login por portal, com todas as ferramentas do portal no mesmo processo Python.
- PJe (PDPJ) em sequência, com 40 s entre os portais. Eproc em outra sequência, com 35 s entre os portais.
- Nunca `acknowledgeCiencia=true`: o valor `false` foi passado de forma explícita.
- Nenhuma intimação pendente aberta, nenhuma escrita, nenhuma minuta.
- Nenhum segredo, código, cookie, texto de documento ou nome de parte neste relatório.

## Resultado

- **41 de 41 portais entraram** no primeiro login, sem nenhuma recusa, CAPTCHA ou bloqueio.
- **Nenhuma falha do código.** Todas as ferramentas chamadas responderam ✅.
- Houve 2 lacunas, ambas por erro do meu roteiro. Pela regra de um login só, não repeti:
  - **`trf1-1g`**: o `pje_read_expediente_ato` não foi exercido. O roteiro procurava o campo interno `atoPath`, mas a ferramenta devolve `temAto`, então ele não achou nenhum expediente elegível.
  - **`trf4-1g-rs`**: o documento não foi lido. Chamei `eproc_get_process` com `scope=latest` e `documentIds`, e o conector recusou com `Eproc request failed: document_ids requires scope=page`. A recusa é correta, mas a descrição da ferramenta não diz isso (ver observações). Nos outros portais li com `scope=page`.
- 5 portais não têm nenhum processo: `trf5-2g`, `trf5-tru`, `tjmg-2g`, `tjms-eproc-1g` e `trf6-2g`, os mesmos de 19/11. Neles foram exercidas só as listas.

## Regressões em relação a 19/11

**Nenhuma.** Os 41 portais que entravam em 19/11 continuam entrando, e todos os documentos lidos foram extraídos.

Melhoras confirmadas:
- **`trf6-1g`**: em 23/11, no teste de regressão, o login pelo Chrome falhou. Agora entrou, com avisos, 6 processos, processo e documento `extracted` (15.972 caracteres).
- **Reuso no PJe-JT (KZ)**: em 23/11 a leitura de documento logo depois do processo fazia 7 requisições. Agora faz 3 em todos os TRT e no TST.
- **Lista paginada do Eproc**: `tjsc-1g` (500 linhas) e `trf4-1g-sc` (50 linhas) vêm com `complete=false`.
- **Push vazio**: vem como lista completa com zero processos (`trf5-2g`, `trf5-tru`, `tjrj-1g` e os TRT sem Push).

## PJe (22)

Legenda:
- **Contadores**: buckets do painel com itens.
- **Caixa**: `pje_list_inbox` com `maxPages=1`, no bucket padrão (Pendentes no PJe, Meus Expedientes no PJe-JT). Quando o bucket padrão estava vazio, li também um bucket com itens, só para achar um processo.
- **Ato**: `pje_read_expediente_ato` num expediente com ciência já registrada.
- **Recusa**: `pje_read_expediente_ato` num expediente pendente, com `acknowledgeCiencia=false`. Deve sair com `ciencia.estado="pendente"`, `ato.error` preenchido e nada aberto.
- **req**: requisições HTTP da chamada.

| Portal | Login + contadores | Caixa | Push | `pje_get_process` | `pje_read_documents` | Ato | Recusa |
|---|---|---|---|---|---|---|---|
| `trf1-1g` | ✅ 2,5 s · Pend. 4, Ciência Judiciário 4 | ✅ 4 linhas (completa) | ✅ 20 (completa) | ✅ 1027808-06.2026.4.01.4100: 16 docs, 3 exp., 4,3 s, 6 req (2ª leitura: 0,9 s, **1 req**) | ✅ Petição inicial `extracted`, 2 req, `nenhuma` | ⚠️ não exercido (roteiro) | — sem pendente |
| `trf3-1g` | ✅ · Pend. 3, Judiciário 2, Sem prazo 1, Respondidos 4 | ✅ 3 linhas, 3 caixas | ✅ 35 | ✅ 5000653-37.2025.4.03.6137: 39 docs, 15 exp., 8,0 s, 7 req | ✅ Petição inicial `extracted`, 2 req, `nenhuma` | ✅ Ato Ordinatório, 1.077 chars, 2 req, `nenhuma` | ✅ `pendente`, recusado, 1 req |
| `trf3-2g` | ✅ · tudo 0 | ✅ 0 | ✅ 1 | ✅ 5000820-98.2026.4.03.6305: 52 docs, 2 exp. | ✅ Petição inicial `extracted`, 2 req | ✅ Decisão monocrática, 2 req, `nenhuma` | — |
| `trf5-1g` | ✅ · Pend. 3, Judiciário 2, Sem prazo 1 | ✅ 3 linhas, 2 caixas | ✅ 12 | ✅ 0066630-10.2026.4.05.8000: 28 docs, 3 exp., 6,7 s | ✅ Petição inicial `extracted`, 2 req | ✅ Despacho, 870 chars, `nenhuma` | — |
| `trf5-2g` | ✅ · tudo 0 | ✅ 0 | ✅ 0 (completa) | — sem processo | — | — | — |
| `trf5-tru` | ✅ · tudo 0 | ✅ 0 | ✅ 0 (completa) | — sem processo | — | — | — |
| `tjrj-1g` | ✅ · Pend. 2, Judiciário 1, Sem prazo 1 | ✅ 2 linhas | ✅ 0 (completa) | ✅ 0800999-69.2026.8.19.0044: 20 docs, 8 exp. | ⚠️ Petição Inicial: `short_html`, 15 chars (capa HTML; ver observações) | ✅ Intimação, 1.968 chars, 1 anexo, `nenhuma` | ✅ `pendente`, recusado, 1 req |
| `trt1-1g` | ✅ · Acervo 23, ME 4, Arquivados 7 | ✅ 4 linhas, 2 caixas | ✅ 30 | ✅ 0101627-48.2026.5.01.0013: 19 docs, 2 exp., 6 req | ✅ Petição Inicial `extracted`, **3 req** (eram 7) | ✅ Intimação, 3 req, `nenhuma` | ✅ `pendente`, recusado |
| `trt1-2g` | ✅ · Acervo 1 | ✅ ME 0; Acervo 1 | ✅ 1 | ✅ 0100159-52.2026.5.01.0012: 127 docs, 0 exp. | ✅ Petição Inicial `extracted`, 3 req | — processo sem expediente | — |
| `trt2-1g` | ✅ · Acervo 16, ME 6, Arq. 7 | ✅ 6 linhas, 5 caixas | ✅ 0 | ✅ 1002001-49.2026.5.02.0421: 23 docs, 4 exp. | ✅ `extracted`, 3 req | ✅ Intimação, 3 req | ✅ `pendente`, recusado |
| `trt2-2g` | ✅ · Acervo 1, Baixados 1 | ✅ ME 0; Acervo 1 | ✅ 0 | ✅ 1000294-64.2026.5.02.0221: 107 docs, 0 exp. | ✅ `extracted`, 3 req | — processo sem expediente | — |
| `trt3-1g` | ✅ · Acervo 13, ME 2, Arq. 8 | ✅ 2 linhas | ✅ 0 | ✅ 0010367-54.2026.5.03.0102: 66 docs, 13 exp. | ✅ `extracted`, 3 req | ✅ Intimação, 3 req | ✅ `pendente`, recusado |
| `trt3-2g` | ✅ · Acervo 2, Baixados 1 | ✅ ME 0; Acervo 2 | ✅ 0 | ✅ 2 processos (0 e 4 exp.); 0011380-46.2025.5.03.0095 | ✅ `extracted`, 3 req (em 19/11 o documento lido precisou de OCR) | ✅ Intimação, 3 req | — |
| `trt4-1g` | ✅ · Acervo 21, ME 7, Arq. 1 | ✅ 7 linhas, 4 caixas | ✅ 0 | ✅ 0021199-63.2026.5.04.0541: 48 docs, 11 exp. | ✅ `extracted`, 3 req | ✅ Intimação, 3 req | ✅ `pendente`, recusado |
| `trt9-1g` | ✅ · Acervo 20, ME 4, Arq. 10 | ✅ 4 linhas, 3 caixas | ✅ 31 | ✅ 0001520-90.2025.5.09.0018: 81 docs, 19 exp. | ✅ `extracted`, 3 req | ✅ Intimação, 3 req | ✅ `pendente`, recusado |
| `trt12-1g` | ✅ · Acervo 160, ME 34, Arq. 144 | ✅ 34 linhas, 15 caixas, 8,1 s, 55 req | ✅ 100 de 292 (`hasMore`, limit 100) | ✅ 0000206-58.2026.5.12.0029: 243 docs, 53 exp. (30 mostrados, `truncated`) | ✅ `extracted`, 5 req | ✅ Intimação, 5 req | ✅ `pendente`, recusado |
| `trt12-2g` | ✅ · Acervo 13, ME 3, Baixados 32 | ✅ 4 linhas | ✅ 28 | ✅ 0000263-40.2026.5.12.0041: 125 docs, 5 exp. | ✅ `extracted`, 3 req | ✅ Intimação, 3 req | — |
| `trt15-1g` | ✅ · Acervo 36, ME 5, Arq. 5 | ✅ 5 linhas, 4 caixas | ✅ 42 | ✅ 0013135-93.2026.5.15.0109: 43 docs, 2 exp. | ✅ `extracted`, 3 req | ✅ Intimação, 3 req | ✅ `pendente`, recusado |
| `trt15-2g` | ✅ · Acervo 1, Baixados 3 | ✅ ME 0; Acervo 1 | ✅ 4 | ✅ 2 processos (0 e 2 exp.); 0010824-51.2026.5.15.0038 | ✅ `extracted`, 3 req (em 19/11 o documento lido precisou de OCR) | ✅ Intimação, 3 req | — |
| `trt18-1g` | ✅ · Acervo 1, Arq. 2 | ✅ ME 0; Acervo 1 | ✅ 3 | ✅ 0000371-73.2026.5.18.0018: 28 docs, 4 exp. | ✅ `extracted`, 3 req | ✅ Intimação, 3 req | — |
| `trt18-2g` | ✅ · só Baixados 1 | ✅ ME 0; Baixados 1 | ✅ 0 | ✅ 0002225-66.2025.5.18.0009: 47 docs, 2 exp. | ✅ `extracted`, 3 req | ✅ Intimação, 3 req | — |
| `tst` | ✅ · Acervo 3, ME 1, Baixados 4 | ✅ 1 linha | ✅ 7 | ✅ 0000353-82.2025.5.12.0041: 140 docs, 2 exp. | ✅ `extracted`, 3 req | ✅ Intimação, 3 req | — |

Todos os `pje_read_documents` devolveram o bloco `ciencia` com `estado="nenhuma"` e nenhuma intimação. Todos os atos lidos (ciência já registrada) devolveram `ciencia.estado="nenhuma"` e o quadro do expediente. As 9 recusas devolveram `estado="pendente"`, `ato.error` ("ciência not yet registered for…") e 0 caracteres de texto: o ato não foi requisitado. Não apareceu nenhum `extracted_ocr`, porque nenhum documento escolhido era escaneado.

### Memória da busca do PJe (requisições poupadas)

| Caminho | 1ª busca do processo | Leitura de documento em seguida | Ato em seguida | Recusa no mesmo processo |
|---|---|---|---|---|
| PJe clássico (TRF1/3/5, TJRJ) | 6 a 7 req | 2 req (aba Expedientes + download) | 2 req | 1 req |
| PJe-JT (KZ: TRT, TST) | 6 req | 3 req (**eram 7** em 23/11 na regressão) | 3 req | 2 req |
| Repetir `pje_get_process` (`trf1-1g`) | 6 req, 4,3 s | — | — | 1 req, 0,9 s |

A recusa num processo ainda não buscado custou 6 req (`trt1-1g`, `trt12-1g`, `trt15-1g`), como uma busca nova.

## Eproc (19)

Todas as filas foram lidas: `pending`, `open_deadlines`, `pending_urgent`, `pending_agreement`, `art334`, `expired_deadline` e `hearing_notice`. Os números são as linhas de cada fila, e todas vieram `complete=true`. Para ler o documento, escolhi um processo que não estava em nenhuma fila pendente, que voltou com `ciencia.estado="nenhuma"` no `scope=latest` e que não tinha marca de "aguardando abertura".

| Portal | Login + `pending` | Outras filas (prazo aberto / decurso / pauta; urgente, acordo e 334 = 0) | `eproc_list_processes` | `get_process` latest | Documento (`scope=page` + 1 id) |
|---|---|---|---|---|---|
| `tjsc-1g` | ✅ 14,4 s · 18 linhas (14 processos) | 202 / 0 / 2 | ✅ 500, `complete=false` (843 em 19/11) | ✅ 0301787-15.2017.8.24.0175: 195 docs, 10 req | ✅ `extracted` 5.647 chars, `nenhuma` |
| `tjsc-2g` | ✅ · 3 | 24 / 1 / 9 | ✅ 122 | ✅ 5044632-35.2025.8.24.0930 | ✅ `extracted` 2.364, `nenhuma` |
| `trf4-1g-sc` | ✅ · 6 | 33 / 0 / 7 | ✅ 50, `complete=false` (159 em 19/11) | ✅ 5000837-20.2026.4.04.7207: 40 docs | ✅ `extracted` 24.145, `nenhuma` |
| `trf4-1g-rs` | ✅ 4,6 s · 1 | 2 / 0 / 0 | ✅ 8 | ✅ 5004215-72.2026.4.04.7113: 22 docs, `nenhuma` | ⚠️ não lido (roteiro: `scope=latest` com `documentIds`, recusado pelo conector) |
| `trf4-1g-pr` | ✅ · 0 | 1 / 0 / 0 | ✅ 15 | ✅ 5014514-59.2026.4.04.7000 | ✅ `extracted` 26.008, `nenhuma` |
| `trf4-2g` | ✅ · 0 | 1 / 0 / 0 | ✅ 34 (32 em 19/11) | ✅ 5327592-03.2026.4.04.9666 | ✅ `extracted` 1.848, `nenhuma` |
| `tjrs-1g` | ✅ · 0 | 12 / 1 / 0 | ✅ 32 | ✅ 5000259-44.2011.8.21.0007: 196 docs | ✅ `extracted` 20.296, `nenhuma` (em 19/11 o documento lido precisou de OCR) |
| `tjsp-1g` | ✅ · 1 | 5 / 0 / 0 | ✅ 14 | ✅ 1003662-31.2024.8.26.0125: 145 docs | ✅ `extracted` 12.344, `nenhuma` |
| `tjsp-2g` | ✅ · 0 | 0 / 0 / 0 | ✅ 3 | ✅ 4113673-92.2026.8.26.0000 | ✅ `extracted` 15.409, `nenhuma` |
| `tjrj-eproc-1g` | ✅ · 0 | 0 / 0 / 0 | ✅ 5 | ✅ 3160426-19.2026.8.19.0001 | ✅ `extracted` 13.688, `nenhuma` |
| `tjpr-1g` | ✅ · 0 | 0 / 1 / 0 | ✅ 1 | ✅ 6000352-45.2026.8.16.0090 | ✅ `extracted` 26.475, `nenhuma` |
| `trf2-1g-rj` | ✅ · 2 | 1 / 3 / 0 | ✅ 10 | ✅ 5019952-94.2026.4.02.5101 | ✅ `extracted` 25.184, `nenhuma` |
| `trf2-1g-es` | ✅ · 0 | 2 / 3 / 0 | ✅ 3 | ✅ 5035228-77.2026.4.02.5001 | ✅ `extracted` 24.883, `nenhuma` |
| `trf2-2g` | ✅ · 0 | 0 / 0 / 0 | ✅ 2 | ✅ 5182110-78.2026.4.02.9666 | ✅ `extracted` 1.788, `nenhuma` |
| `tjmg-1g` | ✅ · 0 | 2 / 0 / 0 | ✅ 3 | ✅ 1001424-71.2026.8.13.0241 | ✅ `extracted` 1.096, `nenhuma` |
| `tjmg-2g` | ✅ · 0 | 0 / 0 / 0 | ✅ 0 | — sem processo | — |
| `tjms-eproc-1g` | ✅ · 0 | 0 / 0 / 0 | ✅ 0 | — sem processo | — |
| `trf6-1g` | ✅ 18,5 s (Chrome) · 0 | 3 / 0 / 0 | ✅ 6 | ✅ 6004121-74.2026.4.06.3808 | ✅ `extracted` 15.972, `nenhuma` |
| `trf6-2g` | ✅ 9,9 s · 0 | 0 / 0 / 0 | ✅ 0 | — sem processo | — |

Tempos típicos: o login com a fila `pending` leva de 3 a 18 s (11 a 18 req). Cada outra fila leva 0,5 a 5 s (2 req). A lista de processos leva 1 a 7,5 s (1 req). O processo com `latest` leva 2 a 9 s (8 a 11 req), e a leitura do documento leva 1,5 a 7,5 s (5 a 8 req).

## Observações para o código (nenhuma é falha)

1. **`eproc_get_process` com `scope=latest` e `documentIds`:** o conector recusa com `document_ids requires scope=page`, sem `[motivo]`. A descrição da ferramenta não avisa que `documentIds` exige `scope=page`, e um agente pode cair nisso como eu caí. O certo seria dizer isso na descrição do campo, ou aceitar `latest` lendo o documento do inventário.
2. **TJRJ PJe, Petição Inicial 312771020:** `short_html` com 15 caracteres. É uma capa HTML com o texto real em anexo. O status está certo, mas o agente fica sem o conteúdo. Em 19/11 o documento lido no `tjrj-1g` foi outro.
3. **`pje_get_process` no `trt12-1g`:** `expedientes.total=53` e 30 itens, com `truncated=true` e `complete=true`. Está coerente (o limite é 30), mas a trava de ciência só vê os pendentes entre os 30 itens devolvidos ao agente. A leitura em si usa a lista inteira.
4. **A recusa de ato pendente devolve o nome da parte** no `ato.error` ("ciência not yet registered for: <id> (<tipo>, <PARTE>, …)"). Isso é esperado para o agente, mas convém lembrar nos relatórios.

Logs brutos (sem segredos): `/tmp/claude-1000/-home-gabriel-orca-jurisync/9c86fc6a-8f2f-4b15-beb8-88573e49d6f7/scratchpad/fullval/fernanda/{pje_1,pje_2,eproc_1,eproc_2}.log`.
