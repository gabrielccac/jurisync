# Hérica: validação completa em 23/11/2026 (login e todas as utilidades de leitura)

Código: `chore/hardening` em `/home/gabriel/orca/jurisync-wt-hardening` (`jurisync.__file__` = `.../jurisync-wt-hardening/src/jurisync/__init__.py`). `.env` e `customers/` do checkout principal. As ferramentas foram chamadas pela camada MCP, como um agente faria: `registry.get(nome).load()` e depois `await server.call_tool(...)`. No PJe, `offer_portals` e `sessions.only` foram feitos como no `main()`. Portais: tudo o que `configured_portals()` oferece para a Hérica, ou seja, 25 PJe, 19 Eproc, 4 e-SAJ e 1 Projudi (49 no total). Nada foi alterado em código, testes, `.env` ou `portals.toml`.

Tudo foi só leitura. Nenhum `acknowledgeCiencia`. Nenhuma intimação pendente foi aberta: no PJe, o ato só foi aberto em expediente que já tinha data de ciência. No Eproc, o harness excluiu os processos das 4 filas pendentes e os que tinham evento "AGUARD. ABERTURA". No e-SAJ e no Projudi, a trava do próprio conector ficou ligada. Os três PDFs que o `esaj_download_document` gravou na pasta compartilhada foram apagados logo em seguida. Fiz no máximo 1 login por portal. Os PJe rodaram em sequência, com 40 s entre um e outro. Nenhuma nova tentativa depois de recusa.

Legenda: ✅ funcionou · ⚠️ funcionou com ressalva · ❌ falhou · — não se aplica ou não exercido (o motivo está na observação)

## Regressões e falhas (primeiro)

1. **❌ PJe TJMT 1G, login recusado no TOTP (regressão: em 17/11 era ✅).**
   - Erro: `PJe request failed [otp_refused]: tjmt-1g login did not reach an authenticated PJe page (twice; check the TOTP secret and the certificate)`.
   - Foram 18 pedidos em 16,8 s. A sessão salva não valia mais, e este foi o único PJe da rodada que precisou de login novo.
   - O código (`auth/certificate.py::cert_login`) envia o OTP, espera a próxima janela TOTP e envia de novo, **dentro da mesma chamada**. Por isso aparece "twice": foram dois OTPs para um só pedido de login.
   - Não tentei de novo. Depois disso nenhuma utilidade do TJMT 1G foi exercida.
   - O TJMT 2G, que veio logo depois, funcionou, mas com sessão salva (9 pedidos). Por isso ele não prova que o segredo TOTP está certo.
   - Pela mensagem não dá para separar duas causas: OTP realmente recusado, ou página final que o conector não reconhece como autenticada (o "otp_refused" sai de `RuntimeError`, sem olhar o que a página dizia). Para saber, é preciso uma tentativa assistida, fora desta rodada.
2. **⚠️ e-SAJ TJAM, `esaj_list_documents` devolve 0 documentos sem erro.**
   - O caso é 0547731-07.2024.8.04.0001, único processo da conta.
   - Em 17/11 o resultado era `flow_required`, e na causa-raiz de hoje `password_required` (o "Visualizar autos" só abre o pedido de senha).
   - Agora a chamada sai com `success` e `items=[]`, em 0,8 s.
   - Hipótese: o visualizador abriu (`var requestScope` presente) e nenhum nó tinha `cdDocumento` numérico. Assim, `parsers.viewer_documents` devolve uma lista vazia em vez de recusar. Para quem usa, parece "processo sem documentos".
   - Por isso `esaj_read_document` não foi exercido no TJAM.
3. Nenhuma outra falha do conector. Os 4 avisos de `esaj_download_document` no `results.jsonl` vieram do meu harness, que procurou `savedAs` como caminho absoluto quando é um nome de arquivo. Os 3 arquivos estavam na pasta compartilhada com o tamanho certo e foram apagados. Na tabela eles aparecem como ✅.

## Comparação com 17/11 (o que melhorou)

| Portal | 17/11 | 23/11 |
|---|---|---|
| tjpa-1g (PJe) | ❌ Push "Could not uniquely identify the PJe Push process grid"; ❌ Expedientes `complete=false`, nenhum documento legível | ✅ Push 276; ✅ expedientes 16/16 `complete=true`; ✅ documento `extracted` |
| tjmt-2g (PJe) | ❌ Petição Inicial 335162368 com `http_error` 404 | ✅ mesma peça `extracted` |
| tjac-2g (Eproc) | ❌ `PydanticSerializationError … surrogates not allowed` (doc 8217…906) | ✅ mesmo documento `extracted`, 1.621 caracteres |
| tjsp-1g, tjsp-2g (Eproc) | ⚠️ só entravam com variáveis definidas no processo | ✅ entram com a configuração do escritório |
| tjrj-eproc-1g | ❌ rede/TLS no login | ✅ cadeia completa |
| tjsp-esaj | ❌ intimações com timeout de 30 s; ❌ documentos `flow_required` | ✅ intimações em 4,8 s; ✅ documentos listados, lidos e baixados |
| tjal-esaj | ❌ documentos `flow_required` | ✅ 96 documentos, leitura `extracted` |
| tjgo-1g (Projudi) | não havia lista de intimações nem de processos | ✅ as duas ferramentas novas; leitura por OCR (`extracted_ocr`) |
| tjmt-1g (PJe) | ✅ | ❌ login (regressão, item 1) |

## Portais pulados

- Nenhum portal oferecido foi pulado por recusa.
- O `tjpr-1g` teve o A1 recusado em 13/11, mas a validação mais recente (17/11) foi ✅. Por isso entrou na rodada, e passou.
- `trf6-1g`/`trf6-2g` (falta senha) e `tjto-1g`/`tjto-2g` (sem DNS de mTLS) não estão em `configured_portals()` da Hérica e não foram oferecidos. Nenhum login foi tentado neles.

## PJe (25 portais)

Colunas: contadores (`pje_list_expediente_counters`), caixa (`pje_list_inbox`, maxPages=1), Push (`pje_list_watched_processes`, limit 20), processo (`pje_get_process`, o 1º do Push), documento (`pje_read_documents`, textLimit 2000, fora dos expedientes) e ato (`pje_read_expediente_ato` de um expediente que já tinha ciência).

- Em todo documento e todo ato lido, o bloco `ciencia` veio `nenhuma`.
- Login: 23 portais reaproveitaram a sessão salva (9 ou 10 pedidos na 1ª chamada). O tjes-1g fez 13 pedidos em 17 s, renovação com o WAF. O tjmt-1g tentou login novo e falhou.

| Portal | Contadores | Caixa | Push | Processo | Documento | Ato | Observação |
|---|---|---|---|---|---|---|---|
| trf1-1g | ✅ 14/1/0/13/2/0/9 (2,3 s) | ✅ 14 linhas, 1 pendente, 11 caixas (7,4 s) | ✅ 494 (hasMore) | ✅ 1008138-17.2018.4.01.3500, 58 docs, 16 exp. completos (6,7 s) | ✅ Petição inicial `extracted_unnumbered` | ✅ 136607418 (Intimação PRU) | |
| trf1-2g | ✅ 6/1/0/4/0/1/0 | ✅ 6 linhas, 4 caixas | ✅ 208 | ✅ 1012057-77.2019.4.01.9999, 25 docs, 7 exp. | ⚠️ Petição inicial `short_html`, 8 caracteres (capa HTML, como em 17/11) | ✅ 30773818 | |
| trf3-1g | ✅ 12/1/0/9/0/2/3/0 | ✅ 12 linhas, 9 caixas | ✅ 137 | ✅ 5006320-98.2025.4.03.6332, 21 docs | ✅ Petição inicial `extracted` | ✅ 49039350 (Sentença) | |
| trf3-2g | ✅ 1/0/0/1/0/0/0/0 | ✅ 1 linha | ✅ 9 (completo) | ✅ 5028456-22.2024.4.03.6301, 79 docs, 5 exp. | ✅ Petição Inicial (ID de origem) `extracted` | ✅ 13034381 (Acórdão) | |
| trf5-1g | ✅ 4/1/0/3/2/0/0 | ✅ 4 linhas, 4 caixas | ✅ 217 | ✅ 0002702-38.2024.4.05.8200 (10,2 s) | ✅ Petição inicial `extracted` | ✅ 10413895 (Sentença) | |
| trf5-2g | ✅ 3/1/0/1/0/1/0 | ✅ 3 linhas, 1 pendente | ✅ 4 | ✅ 0000136-76.2024.4.05.9830, 14 exp. | ✅ Petição inicial `extracted` | ✅ 1044708 (Acórdão) | |
| tjap-1g | ✅ todos 0 | ✅ 0 linhas | ✅ 2 | ✅ 6001094-06.2026.8.03.0002, 63 docs, 14 exp. (8 pendentes) | ✅ Petição Inicial `extracted` (fora dos pendentes) | ✅ 15860005 | |
| tjba-1g | ✅ 67/21/1/20/30/4/15/16 | ✅ 67 linhas, 3 pendentes, 23 caixas (25,3 s) | ✅ 377 | ✅ 8000094-23.2026.8.05.0020 | ✅ Petição Inicial `extracted` | ✅ 69221700 | |
| tjce-1g | ✅ 7/0/1/5/1/1/1 | ✅ 7 linhas, 5 caixas | ✅ 155 | ✅ 3004082-88.2025.8.06.0071, 63 docs | ✅ Petição Inicial `extracted` | ✅ 11520345 | |
| tjce-2g | ✅ 5/0/0/3/0/2/0 | ✅ 5 linhas | ✅ 3 | ✅ 3022521-69.2025.8.06.0000 | ⚠️ Petição Inicial `short_html`, 38 caracteres (capa) | ✅ 2161854 | |
| tjdft-1g | ✅ 5/1/0/3/1/1/1 | ✅ 5 linhas, 3 caixas | ✅ 56 | ✅ 0705101-80.2020.8.07.0009, 13 exp. | ⚠️ Petição Inicial `short_html`, 26 caracteres (capa) | ✅ 11323762 (Decisão) | |
| tjes-1g | ✅ 6/3/0/3/0/0/5 (17,3 s; WAF) | ✅ 6 linhas, 3 pendentes, 5 caixas | ✅ 52 | ✅ 5000308-30.2025.8.08.0016, 7 exp. (1 pendente) | ✅ Petição Inicial `extracted` | ✅ 13955296 (Intimação - Diário) | |
| tjes-2g | ✅ 3/0/0/3/0/0/0 | ✅ 3 linhas | ✅ 4 | ✅ 5012491-47.2026.8.08.0000 | ⚠️ Petição Inicial `short_html`, 1 caractere | ✅ 1398622 | outro processo, não o de 17/11 |
| tjma-1g | ✅ 8/0/0/4/1/4/0/0 | ✅ 8 linhas, 4 caixas | ✅ 129 | ✅ 0800982-20.2024.8.10.0055, 16 exp. | ✅ Petição Inicial `extracted` | ✅ 33977717 | |
| tjmt-1g | ❌ `[otp_refused]` (16,8 s, 18 pedidos) | — | — | — | — | — | ver regressão 1 |
| tjmt-2g | ✅ 9/1/0/2/0/6/0/8 | ✅ 9 linhas, 1 pendente | ✅ 31 | ✅ 1002023-93.2025.8.11.9005, 16 exp. (5 pendentes) | ✅ Petição Inicial 335162368 `extracted` (era 404) | ✅ 56790076 (Acórdão) | |
| tjpa-1g | ✅ 49/7/5/21/0/16/6 | ✅ 49 linhas, 2 pendentes, 18 caixas (13,9 s) | ✅ 276 (era ❌) | ✅ 0802092-18.2021.8.14.0065, 193 docs, 16 exp. completos | ✅ "Documento de Comprovação (04 INICIAL…)" `extracted` | ✅ 29213270 (Decisão) | bug 1 de 17/11 corrigido |
| tjpb-1g | ✅ 10/0/0/9/3/0/4 | ✅ 10 linhas, 8 caixas | ✅ 88 | ✅ 0800708-91.2024.8.15.0451 | ✅ Petição Inicial `extracted` | ✅ 18317077 (Mandado) | |
| tjpe-1g | ✅ 38/1/0/34/3/3/7 | ✅ 38 linhas, 9 caixas | ✅ 208 | ✅ 0001110-44.2025.8.17.3450 | ✅ Petição Inicial (Outras) `extracted` | ✅ 36225160 | |
| tjpi-1g | ✅ 2/0/0/2/0/0/1 | ✅ 2 linhas | ✅ 126 | ✅ 0801515-70.2026.8.18.0027, 0 expedientes | ✅ Petição Inicial `extracted` | — processo sem expediente | |
| tjpi-2g | ✅ 9/0/0/9/3/0/0 | ✅ 9 linhas | ✅ 34 | ✅ 0800467-38.2025.8.18.0051, 4 exp. (2 pendentes) | ✅ Petição Inicial (ID de origem) `extracted` | ✅ 4005432 | |
| tjrn-1g | ✅ 28/5/0/21/1/2/13 | ✅ 28 linhas, 5 pendentes | ✅ 178 | ✅ 0819622-80.2025.8.20.5124 (22,4 s, 31 pedidos: rota do Acervo) | ✅ Petição Inicial `extracted` | ✅ 28328549 | |
| tjrn-2g | ✅ 4/0/0/4/1/0/1 | ✅ 4 linhas | ✅ 35 | ✅ 0828611-32.2025.8.20.5106, 120 docs (13,8 s) | ✅ Petição Inicial (ID de origem) `extracted` | ✅ 4981176 | |
| tjro-1g | ✅ 2/0/0/2/0/0/1 | ✅ 2 linhas | ✅ 35 | ✅ 7040600-15.2024.8.22.0001 | ✅ PETIÇÃO INICIAL `extracted` | ✅ 33396570 (Decisão) | |
| tjro-2g | ✅ todos 0 | ✅ 0 linhas | ✅ 13 | ✅ 7025532-88.2025.8.22.0001 | ✅ PETIÇÃO INICIAL (ID de origem) `extracted` | ✅ 2741156 | |

`short_html` = a "Petição inicial" do PJe é uma capa HTML curta, e a peça está nos anexos. É o mesmo comportamento de 17/11, não é erro de extração.

## Eproc (19 portais)

- Filas: `eproc_list_notices` foi chamado uma vez para cada uma das 7 filas (pending, pending_urgent, pending_agreement, art334, open_deadlines, expired_deadline, hearing_notice). As 133 chamadas foram ✅, todas com `complete=true`. Os números entre parênteses são pendentes / prazo aberto / decurso / pauta.
- Lista: `eproc_list_processes`.
- Processo e documento: `eproc_get_process` sem documento e depois com `documentIds` de 1 documento de um processo fora das filas pendentes. Em toda leitura o bloco `ciencia` veio `nenhuma`.

| Portal | Filas (7) | Lista | Processo | Documento | Observação |
|---|---|---|---|---|---|
| trf2-1g-rj | ✅ (3/2/4/0) | ✅ 18 | ✅ 5000602-66.2026.4.02.5119, 28 ev., 23 docs | ✅ Petição inicial, 8.896 caracteres | |
| trf2-1g-es | ✅ (0/1/2/0) | ✅ 7 | ✅ 5000692-37.2026.4.02.5002 | ✅ Petição inicial, 10.557 | |
| trf2-2g | ✅ (todas 0) | ✅ 0 | — sem processo | — | igual a 17/11 |
| trf4-1g-rs | ✅ (0/4/4/0) | ✅ 6 | ✅ 5000923-07.2026.4.04.7137 | ✅ Petição inicial, 38.464 | |
| trf4-1g-sc | ✅ (0/5/5/0) | ✅ 13 | ✅ 5007263-25.2024.4.04.7205, 174 ev. | ✅ Petição inicial, 28.075 | |
| trf4-1g-pr | ✅ (1/3/2/0) | ✅ 14 | ✅ 5008575-51.2024.4.04.7006 | ✅ Petição inicial, 8.012 | |
| trf4-2g | ✅ (todas 0) | ✅ 6 | ✅ 5318421-56.2025.4.04.9666 | ✅ Petição inicial (segredo), 1.852 | |
| tjsp-1g | ✅ (13/77/91/0) | ⚠️ 50, `complete=false` | ✅ 4003442-15.2025.8.26.0038, 59 ev., 51 docs | ✅ Petição inicial, 31.032 | login com a configuração do escritório (17/11: só com variáveis no processo). O 1º candidato (4003440-45) tinha evento "AGUARD. ABERTURA" e `ciencia=nenhuma`, e foi pulado pelo harness |
| tjsp-2g | ✅ (1/23/21/13) | ⚠️ 50, `complete=false` | ✅ 4000268-76.2025.8.26.0691 | ✅ Petição, 8.720 | |
| tjsc-1g | ✅ (5/41/31/0) | ✅ 196 | ✅ 5014937-79.2025.8.24.0075 | ✅ Petição inicial, 35.802 | |
| tjsc-2g | ✅ (1/21/4/4) | ✅ 56 | ✅ 5001336-30.2026.8.24.0088 | ✅ Informação, 2.069 | |
| tjrj-eproc-1g | ✅ (6/24/50/0) | ⚠️ 50, `complete=false` | ✅ 3023388-96.2025.8.19.0001 | ✅ Petição inicial, 6.804 (27,3 s) | 17/11: ❌ rede |
| tjrs-1g | ✅ (6/26/21/0) | ⚠️ 50, `complete=false` | ✅ 5004129-87.2026.8.21.0002 | ✅ Petição inicial, 35.422 | |
| tjmg-1g | ✅ (4/58/50/0) | ⚠️ 50, `complete=false` | ✅ 1000002-27.2025.8.13.0005, 106 ev. (13,7 s) | ✅ Emenda à inicial, 2.245 | |
| tjpr-1g | ✅ (todas 0) | ✅ 1 | ✅ 6000139-92.2026.8.16.0137 | ✅ Petição inicial, 20.909 | A1 aceito de novo |
| tjpr-2g | ✅ (todas 0) | ✅ 0 | — sem processo | — | |
| tjac-1g | ✅ (2/4/1/0) | ✅ 34 | ✅ 5000191-24.2026.8.01.0008 | ✅ Petição inicial, 30.822 | |
| tjac-2g | ✅ (0/0/0/4) | ✅ 10 | ✅ 5008950-32.2025.8.01.0001 | ✅ Petição (o doc que quebrava em 17/11), 1.621 | bug de surrogate corrigido |
| tnu | ✅ (todas 0) | ✅ 0 | — sem processo | — | |

⚠️ na lista: nos portais com mais de 50 processos, `eproc_list_processes` devolve os 50 primeiros com `complete=false`. Em 17/11 também vieram 50. É o limite declarado da ferramenta, não erro.

## e-SAJ (4 portais)

Colunas: sessão (`esaj_get_session`), mensagens (`esaj_list_messages`), intimações / audiências / petições (`esaj_list_tasks`, petições em AGUARDANDO_ASSINATURA e CONCLUIDA), processo (`esaj_get_process`), lista de documentos (`esaj_list_documents`), leitura (`esaj_read_document`) e download (`esaj_download_document`).

- As intimações do painel não trazem o número do processo, só o código. Por isso a exclusão dos processos com intimação pendente ficou com a trava do conector (`ensure_no_pending_intimation`), que liberou os 3 documentos lidos.
- Em toda leitura o bloco `ciencia` veio `nenhuma`.

| Portal | Sessão | Mensagens | Intimações | Audiências | Petições | Processo | Lista docs | Leitura | Download |
|---|---|---|---|---|---|---|---|---|---|
| tjsp-esaj | ✅ 24 OABs (1,4 s) | ✅ 20 | ✅ 3, todas sem ciência (4,8 s; antes timeout) | ✅ 0 | ✅ 4 / 4 | ✅ 1000722-27.2026.8.26.0383, 4 mov. | ✅ 13 | ✅ Petição (Outras), 288 KB, 24.129 caracteres | ✅ 288 KB (apagado) |
| tjms-esaj | ✅ 24 OABs | ✅ 20 | ✅ 20, todas sem ciência | ✅ 3 | ✅ 6 / 64 | ✅ 0800912-95.2026.8.12.0016, 26 mov. | ✅ 30 | ✅ Petição (Outras), 283 KB, 43.446 | ✅ 283 KB (apagado) |
| tjal-esaj | ✅ 24 OABs | ✅ 20 | ✅ 15, todas sem ciência | ✅ 1 | ✅ 2 / 43 | ✅ 0719268-17.2025.8.02.0058, 51 mov. | ✅ 96 | ✅ Petição, 60 KB, 5.254 | ✅ 60 KB (apagado) |
| tjam-esaj | ✅ `sajcas` (7 s) | ✅ 1 | ✅ 0 | ✅ 0 | ✅ 0 / 0 | ✅ 0547731-07.2024.8.04.0001, 7 mov. | ⚠️ 0 itens sem erro | — nada para ler | — | 

TJAM: ver o item 2 das regressões.

## Projudi TJGO (1 portal)

| Utilidade | Resultado |
|---|---|
| `projudi_list_intimations` | ✅ 4,5 s; Diário 290 (declarado 290), 1 aguardando publicação fora da home, `portalPending` 0, `complete=true` |
| `projudi_list_processes` | ✅ 4,0 s; 11 categorias, todas `complete=true` (totais 145/247/41/2/2/2/35/2/22/1/1), 1 audiência hoje |
| `projudi_get_process` | ✅ 5026335-73 (número curto), 84 movimentações, 71 arquivos (1,1 s). O processo de 17/11 (5649512-56) está na lista do Diário e foi evitado |
| `projudi_list_documents` | ✅ 31 movimentações com arquivo, 71 arquivos |
| `projudi_read_documents` | ✅ petição, `extracted_ocr`, 12.030 caracteres (11,8 s), `ciencia=nenhuma` |

## Números

- 49 portais oferecidos e 49 tentados. 48 com login OK; 1 recusado (tjmt-1g).
- 377 chamadas de ferramenta: 368 ✅, 1 ❌ (login do tjmt-1g), 4 ⚠️ e 4 "não exercido".
  - Os 4 ⚠️: TJAM com 0 documentos, mais os 3 downloads que o harness marcou como ⚠️ e na verdade são ✅.
  - Os 4 "não exercido": trf2-2g, tjpr-2g e tnu sem processo; ato do tjpi-1g sem expediente.
- PJe: 24 cadeias completas (contadores → caixa → Push → processo → documento → ato), menos o ato do tjpi-1g. Eproc: 16 cadeias completas até o documento, e os outros 3 não têm processo. e-SAJ: 3 cadeias completas, e o TJAM para na lista de documentos. Projudi: cadeia completa.
- Nenhuma ciência foi registrada. Nenhuma intimação pendente foi aberta. Nenhuma escrita no portal.

Scripts e logs: `/tmp/claude-1000/-home-gabriel-orca-jurisync/9c86fc6a-8f2f-4b15-beb8-88573e49d6f7/scratchpad/fullval/herica/` (`results.jsonl`, `pje.log`, `eproc.log`, `esaj_*.log`, `projudi.log`).
