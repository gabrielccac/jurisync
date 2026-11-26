# Hérica — revalidação completa em 17/11/2026

Cadeia por portal: login, contadores/filas, caixa, processo, documento. Só leitura, sem ciência.


Branch `main` (c63887e). Chamadas diretas às funções do `connectors/pje/server.py`, um portal por vez, uma cadeia completa por login. Tudo foi só leitura: `acknowledgeCiencia=False` passado de forma explícita em toda leitura, nenhum `pje_read_expediente_ato`, nenhuma escrita, nada de "liberar dispositivo". Nenhum `.env`, `portals.toml`, certificado ou código foi alterado. Nenhum segredo foi registrado.

Contadores = buckets 0 a 6 (ou 0 a 7) do painel, nesta ordem: Pendentes de ciência ou de resposta / Apenas pendentes de ciência / Ciência pelo destinatário / Ciência pelo Judiciário / Prazo findo em 10 dias / Sem prazo / Respondidos em 10 dias (/ 8º bucket onde existe). Caixa = bucket padrão (índice 0). Processo e documento sempre lidos de um processo do Push.

| Portal | Login | Contadores | Caixa | Processo | Documento | Observação |
|---|---|---|---|---|---|---|
| trf1-1g | ✅ | ✅ 18/3/0/14/5/1/7 | ✅ 18 linhas | ✅ 1000003-00.2018.4.01.3500 (Push 494) | ✅ Petição inicial (`extracted_unnumbered`), mais de 20.000 chars (truncado no textLimit=20000 desta execução) | 2 logins, os dois OK (o 2º porque o meu script lia mal o inventário) |
| trf1-2g | ✅ | ✅ 5/3/0/1/1/1/0 | ✅ 5 linhas (3 pendentes de ciência), 3 caixas | ✅ 1000004-00.2019.4.01.9999 (Push 208) | ⚠️ Petição inicial = `short_html`, 93 bytes / 8 chars (só a capa HTML, a peça está nos anexos) | 2 logins (o 2º releu o mesmo doc) |
| trf3-1g | ✅ | ✅ 13/5/0/6/4/2/5/0 | ✅ 13 linhas (5 pendentes), 9 caixas | ✅ 5000005-00.2025.4.03.6332 (Push 135) | ✅ Petição inicial, 13.875 chars | |
| trf3-2g | ✅ | ✅ todos 0 | ✅ 0 linhas | ✅ 5000006-00.2024.4.03.6301 (Push 9) | ✅ Petição Inicial (ID de origem 332459850), 17.450 chars | |
| trf5-1g | ✅ | ✅ 5/1/0/4/0/0/1 | ✅ 5 linhas, 5 caixas | ✅ 0000007-00.2024.4.05.8200 (Push 216) | ✅ Petição inicial, 29.257 chars | |
| trf5-2g | ✅ | ✅ 1/0/0/0/0/1/0 | ✅ 1 linha | ✅ 0000136-76.2024.4.05.9830 (Push 4) | ✅ Petição inicial, 15.974 chars | |
| tjba-1g | ✅ | ✅ 74/25/0/25/26/4/23/27 | ✅ 74 linhas (3 pendentes), 22 caixas | ✅ 8000008-00.2026.8.05.0020 (Push 377) | ✅ Petição Inicial, 35.860 chars | |
| tjce-1g | ✅ | ✅ 6/0/0/4/3/2/5 | ✅ 6 linhas (2 pendentes), 3 caixas | ✅ 3000009-00.2025.8.06.0071 (Push 155) | ✅ Petição Inicial, 20.647 chars | |
| tjce-2g | ✅ | ✅ 5/0/0/3/0/2/0 | ✅ 5 linhas | ✅ 3000010-00.2025.8.06.0000 (Push 3) | ⚠️ Petição Inicial = `short_html`, 38 chars. No 2º login: "Documento de Comprovação (…decisao)" em PDF, 2.458 chars ✅ | 2 logins |
| tjdft-1g | ✅ | ✅ 6/2/0/3/2/1/2 | ✅ 6 linhas (2 pendentes), 4 caixas | ✅ 0700011-00.2020.8.07.0009 (Push 56) | ⚠️ Petição Inicial = `short_html`, 97 bytes / 26 chars (capa HTML) | 2 logins (o 2º releu o mesmo doc) |
| tjes-1g | ✅ CAPTCHA do WAF resolvido nas 2 execuções (~18 s) | ✅ 9/5/0/4/0/0/1 | ✅ 9 linhas (5 pendentes), 5 caixas | ✅ 5000012-00.2025.8.08.0021 (Push 52) | ✅ Petição Inicial, PDF de 283.821 bytes, 28.432 chars | 1º login: 3 processos recusados com razão por ciência pendente (Intimação no Diário sem id de documento: 10885825, 15750761 e despacho 14517843). 2º login: o 4º processo foi lido |
| tjes-2g | ✅ | ✅ 3/0/0/3/0/0/0 | ✅ 3 linhas | ✅ 5000012-00.2025.8.08.0021 (Push 4) | ✅ Petição Inicial (ID de origem 73533924), 28.432 chars | |
| tjma-1g | ✅ | ✅ 9/0/0/3/0/4/4/0 | ✅ 9 linhas (2 pendentes), 5 caixas | ✅ 0800013-00.2024.8.10.0055 (Push 129) | ✅ Petição Inicial, 24.369 chars | |
| tjmt-1g | ✅ | ✅ 14/1/2/11/6/0/7/13 | ✅ 14 linhas (1 pendente), 8 caixas | ✅ 1000014-00.2019.8.11.0041 (Push 91) | ⚠️ Petição Inicial (21282592) = `http_error`, 0 chars. No 2º login, 1000015-00.2025.8.11.0001: Certidão de trânsito em julgado (HTML), 556 chars ✅ | Ver bug 2 |
| tjmt-2g (novo) | ✅ | ✅ 8/2/0/1/2/5/0/6 | ✅ 8 linhas (2 pendentes) | ✅ 1000016-00.2025.8.11.9005 e 1000017-00.2025.8.11.0000 (Push 31) | ❌ Petição Inicial `http_error` nos dois processos (335162368 e 337562382): HTTP 404, `text/html;charset=ISO-8859-1`, 0 bytes | Login, painel, caixa, Push e processo validados pela 1ª vez. Ver bug 2 |
| tjpa-1g | ✅ | ✅ 56/9/8/24/2/15/12 | ✅ 56 linhas (4 pendentes), 18 caixas | ⚠️ 0800018-00.2026.8.14.0070 abre, mas a aba Expedientes volta `total 0, complete false` | ❌ `ciência not checked: the process' Expedientes list could not be read in full…`, igual em 3 processos | Push ❌ `Could not uniquely identify the PJe Push process grid`. Processos tirados da caixa. Ver bug 1 |
| tjpb-1g | ✅ | ✅ 11/0/0/9/2/1/8 | ✅ 11 linhas, 6 caixas | ✅ 0800019-00.2024.8.15.0451 (Push 88) | ✅ Petição Inicial, 9.238 chars | |
| tjpe-1g | ✅ | ✅ 31/0/0/27/11/4/11 | ✅ 31 linhas, 9 caixas | ✅ 0000020-00.2025.8.17.3450 (Push 208) | ✅ Petição Inicial (Outras), 30.251 chars | |
| tjap-1g | ✅ | ✅ todos 0 | ✅ 0 linhas | ✅ 6000021-00.2026.8.03.0002 (Push 2) | ✅ Petição Inicial, 30.938 chars | |
| tjrn-1g | ✅ | ✅ 21/7/0/13/5/1/26 | ✅ 21 linhas (7 pendentes), 8 caixas | ✅ 0800022-00.2025.8.20.5124 (Push 178), aberto pelo **Acervo** (a busca passa por `listView`, cai no reCAPTCHA e o conector percorre o painel: 25 POSTs, depois o detalhe) | ✅ Petição Inicial, 33.769 chars | ~24 s por chamada por causa do Acervo |
| tjrn-2g | ✅ | ✅ 4/1/0/3/3/0/1 | ✅ 4 linhas (1 pendente) | ✅ 0800023-00.2025.8.20.5106 (Push 35) | ✅ Petição Inicial (ID de origem 171753524), 37.111 chars | |
| tjro-1g | ✅ | ✅ 2/0/0/2/0/0/0 | ✅ 2 linhas, 2 caixas | ✅ 7000024-00.2024.8.22.0001 (Push 35) | ✅ PETIÇÃO INICIAL, 9.171 chars | |
| tjro-2g | ✅ | ✅ todos 0 | ✅ 0 linhas | ✅ 7000025-00.2025.8.22.0001 (Push 11) | ✅ PETIÇÃO INICIAL (ID de origem 120512436), 29.251 chars | Na validação de 13/11 não havia processo para testar; agora testado |
| tjpi-1g | ✅ | ✅ 1/0/0/1/1/0/4 | ✅ 1 linha | ✅ 0800026-00.2026.8.18.0027 (Push 126) | ✅ Petição Inicial, 37.436 chars | |
| tjpi-2g | ✅ | ✅ 9/0/0/9/1/0/0 | ✅ 9 linhas | ✅ 0800027-00.2025.8.18.0051 (Push 34) | ✅ Petição Inicial (ID de origem 79621339), 20.595 chars | |

**Resumo:** 25/25 logins OK, todos na 1ª tentativa de cada execução (nenhum login falhou). Contadores e caixa ✅ nos 25 portais. Push ✅ em 24 (❌ no TJPA). Processo ✅ em 25, parcial no TJPA (sem expedientes). Leitura de documento ✅ em 21; ⚠️ em trf1-2g, tjdft-1g e tjmt-1g; ❌ em tjmt-2g (petição inicial) e tjpa-1g. Nenhuma ciência foi registrada.

## Bugs do conector (descritos, não corrigidos)

1. **TJPA 1G: aba Expedientes e Push**
   - `search.py` → `expedientes.fetch_expedientes` / `parse_expedientes` / `tab_complete`: na aba Expedientes do TJPA, `parse_expedientes` não acha nenhuma linha e `tab_complete` dá falso (diagnóstico `expedientes_incomplete`). Por isso `pje_read_documents` recusa todo documento com "ciência not checked". A recusa em si está certa (é a proteção), mas hoje nenhum documento do TJPA pode ser lido sem ciência explícita.
   - `push.py` → `parse_push_items`: `RuntimeError("Could not uniquely identify the PJe Push process grid")`. No TJPA há 0 ou mais de 1 tabela com a coluna "Processo".
   - Mesmo portal, `parsers.extract_documents` / `process._document_inventory`: o `documentInventory` inclui códigos de movimento como se fossem documentos (ids `12164`, `60`, `92`, `1061`, `85`, com títulos como "Proferidas outras decisões não especificadas" e "Publicado Citação em 10/11/2026.").
2. **TJMT 1G/2G: download da petição inicial dá 404.** A rota de download do conector (`documents.py`, ou `kz.fetch_document` se o TJMT for KZ) recebe HTTP 404 (`text/html;charset=ISO-8859-1`, 0 bytes), e o resultado vira `extractionStatus=http_error`. Isso aconteceu com as petições iniciais 21282592 (1G), 335162368 e 337562382 (2G). Um documento HTML recente do 1G (certidão 254485019) abriu normalmente. A suspeita é a URL/rota de documentos antigos ou em PDF no TJMT. Precisa de captura da URL que o próprio portal usa.
3. **Menores:**
   - `get_process.diagnostics` volta como string com repr de dict Python (`"{'code': 'expedientes_incomplete', …}"`), não como objeto.
   - `pagination.complete=false` mesmo quando `more_pages_possible=false` e `pagination_no_new_events`, em todos os portais.
   - Nos 2º graus que recebem autos de origem (trf3-2g, trf5-2g, tjes-2g, tjrn-2g, tjro-2g, tjpi-2g), o inventário traz itens sem título, que são os ids "de origem" duplicados.
   - A rota pelo Acervo (`via: "acervo"`, TJRN 1G) não aparece na saída de `pje_get_process`. Só dá para ver pelo tráfego.

Observação: nos casos `short_html` (trf1-2g, tjce-2g, tjdft-1g), a "Petição inicial" é uma capa HTML curta e o texto da peça está nos documentos anexos. Não é erro de extração.

## O que a cliente precisa fazer

- **Nada para o login:** o A1 e o TOTP funcionam nos 25 portais, incluindo o TJMT 2G novo e o TJES 1G com o CAPTCHA do WAF.
- **TJES 1G:** há expedientes no Diário com ciência pendente (pelo menos 10885825, 15750761 e 14517843). O escritório decide se registra a ciência; o conector não abre esses documentos sem autorização.
- **TJPA e TJMT:** a leitura de documentos depende de correção no conector (bugs 1 e 2). Não há ação da cliente.


Branch `main` (c63887e), sem alterar código, `.env`, `portals.toml` ou certificado. Ferramentas MCP chamadas dentro do processo (`create_server(...).call_tool`). Portais rodados um de cada vez, com 31 s de intervalo entre eles. Tudo foi só leitura: nenhuma ciência, nenhum protocolo, nenhum "liberar dispositivo" nem "definir como padrão".

**Proteção da ciência.** O eproc não recusa abrir documento: ele abre e só depois avisa no bloco `ciencia`. Por isso, antes de abrir qualquer documento, o harness fazia a mesma checagem do conector (`client.pending_notices`, as 4 filas pendentes lidas até o fim) e também procurava eventos "AGUARD. ABERTURA" (`ciencia.awaiting_events`). Só abria se as duas checagens viessem vazias. Nenhuma leitura voltou com bloco `ciencia`. No e-SAJ ficaram de fora os processos das intimações com `ciencia=False`, e a trava `validarAberturaIntimacaoNaoRecebida` do conector ficou ativa. No Projudi só foi lida a petição inicial de 2022 (peça da própria parte).

| Portal | Login | Filas/intimações | Lista de processos | Processo | Documento | Observação |
|---|---|---|---|---|---|---|
| trf2-1g-rj | ✅ 1 tentativa | ✅ pendentes 0, prazos abertos 3 | ✅ 21 | ✅ 5000028-00.2026.4.02.5119 (28 eventos, 23 docs) | ✅ Petição inicial, 8.896 caracteres | sem bloco ciência |
| trf2-1g-es | ✅ 1 | ✅ pend. 1, abertos 0 | ✅ 7 | ✅ 5000029-00.2026.4.02.5002 (17 ev., 21 docs) | ✅ Petição inicial, 10.557 | |
| trf2-2g | ✅ 1 | ✅ 0 / 0 | ✅ 0 ("Nenhum registro encontrado") | — | — | não há processo no 2G |
| trf4-1g-rs | ✅ 1 | ✅ pend. 2, abertos 2 | ✅ 6 | ✅ 5000030-00.2026.4.04.7112 (9 ev., 18 docs) | ✅ Petição inicial, 43.462 | |
| trf4-1g-sc | ✅ 1 | ✅ 0 / 6 | ✅ 13 | ✅ 5000031-00.2024.4.04.7205 (174 ev., 81 docs, 2 páginas) | ✅ Petição inicial, 28.075 | |
| trf4-1g-pr | ✅ 1 | ✅ 0 / 5 | ✅ 15 | ✅ 5000032-00.2024.4.04.7006 (78 ev., 52 docs) | ✅ Petição inicial, 8.012 | |
| trf4-2g | ✅ 1 | ✅ 0 / 0 | ✅ 6 | ✅ 5300033-00.2025.4.04.9666 (4 ev., 2 docs) | ✅ Petição inicial (segredo nível 1), 1.852 | |
| tjsp-1g | ⚠️ com a configuração atual: ❌ `Eproc request failed: Set TJSP_1G_USERNAME and TJSP_1G_PASSWORD in .env` (2×, nada chegou ao portal). Com `TJSP_1G_CERT=true` e `TJSP_1G_USER_PROFILE=SP527617` só no ambiente do processo: ✅ 1 | ✅ pend. 28, abertos 89 | ✅ 50 | ✅ 4000034-00.2026.8.26.0037 (16 ev., 20 docs) | ✅ Petição inicial, 11.979 | falta config no `[env].set` |
| tjsp-2g | ⚠️ igual ao 1G: `Set TJSP_2G_USERNAME and TJSP_2G_PASSWORD in .env`; com `TJSP_2G_CERT`/`USER_PROFILE=SP527617` no processo: ✅ 1 | ✅ 5 / 18 | ✅ 50 | ✅ 4000035-00.2025.8.26.0691 (24 ev., 4 docs) | ✅ Petição, 8.720 | idem |
| tjsc-1g | ✅ 1 | ✅ 4 / 43 | ✅ 196 | ✅ 5000036-00.2025.8.24.0075 (77 ev., 38 docs) | ✅ Petição inicial, 35.802 | |
| tjsc-2g | ✅ 1 | ✅ 2 / 18 | ✅ 52 | ✅ 5100037-00.2026.8.24.0000 (8 ev., 15 docs) | ✅ Petição inicial, 22.169 | |
| tjrj-eproc-1g | ❌ 1ª: `SSLError: curl: (35) Recv failure: Connection reset by peer`; 2ª: `Timeout: curl: (28) Operation timed out after 30000 milliseconds with 0 bytes received` | — | — | — | — | Falhou na 1ª requisição (rede/TLS). A1 e TOTP não chegaram a ser enviados. Às 15:5x, `curl` do shell recebeu 302 do host. Retestar |
| tjrs-1g | ✅ 1 | ✅ 7 / 18 | ✅ 50 | ✅ 5000038-00.2026.8.21.0002 (22 ev., 22 docs) | ✅ Petição inicial, 35.422 | |
| tjmg-1g | ✅ 1 | ✅ 12 / 59 | ✅ 50 | ✅ 1000039-00.2025.8.13.0005 (105 ev., 47 docs) | ✅ Emenda à inicial, 2.245 | |
| tjpr-1g | ✅ 1 (A1, perfil GO022729) | ✅ 0 / 0 | ✅ 1 | ✅ 6000040-00.2026.8.16.0137 (42 ev., 21 docs) | ✅ Petição inicial, 20.909 | A1 validado de ponta a ponta |
| tjpr-2g | ✅ 1 | ✅ 0 / 0 | ✅ 0 ("Nenhum registro") | — | — | |
| tjac-1g | ✅ 1 | ✅ 0 / 4 | ✅ 34 | ✅ 5000041-00.2026.8.01.0008 (47 ev., 43 docs) | ✅ Petição inicial, 30.822 | |
| tjac-2g | ✅ 2 execuções, 1 login cada | ✅ 0 / 0 | ✅ 10 | ✅ 5000042-00.2025.8.01.0001 (5 ev., 5 docs); 5000043-00.2025.8.01.0001 (4 ev., 1 doc) | ⚠️ 1ª: ❌ `UnexpectedToolError ... PydanticSerializationError: ... UnicodeEncodeError: 'utf-8' codec can't encode character '\ud83d' ... surrogates not allowed` (Petição, doc 821780512764725559220332452906); 2ª: ✅ Certidão (HTML), 959 | bug de serialização (ver abaixo) |
| tnu | ✅ 1 (primeira validação) | ✅ 0 / 0 | ✅ 0 ("Nenhum registro") | — | — | login A1 + TOTP OK; não há processo |
| tjgo-1g (Projudi) | ✅ 2 execuções (a 1ª entrou, mas o harness quebrou: erro meu, `structuredContent`) | — (o conector não tem ferramenta de intimações) | — (o conector não tem ferramenta de lista) | ✅ 5600044-00.2022.8.09.0085 (169 movimentações, 91 arquivos); número tirado do DJEN (OAB GO 22729) | ✅ `inicialdivinocarvalhodearaujo.pdf.p7s` (21/10/2022), 22.464 caracteres | |
| tjsp-esaj | ✅ 2 execuções, `usuarioLogado: true`, 24 OABs | ⚠️ intimações: ❌ `curl: (28) Operation timed out after 30001 ms` (2×); audiências 0; petições aguardando assinatura 4; concluídas 5; caixa postal 20 | — (sem ferramenta) | ✅ 10 processos lidos, mas sempre **5 movimentações**; ❌ `Unexpected process detail endpoint` em 1000045-00.2025.8.26.0233 | ❌ `flow_required: No authorized document viewer link in the account dashboard` (10 processos, de petições e da caixa postal) | |
| tjms-esaj | ✅ 1 | ✅ intimações 15, audiências 3, petições 6 aguardando / 63 concluídas, caixa postal 20 | — | ✅ 0800046-00.2025.8.12.0016 (49 movimentações, 2 partes) | ✅ Emenda à inicial (doc 248037477), 80 KB, 2.206 caracteres | trava de intimação não disparou |
| tjal-esaj | ✅ 2 execuções | ✅ intimações 15 (todas `ciencia=False`, por isso evitadas), audiências 1, petições 2 / 46, caixa postal 20 | — | ✅ 9 processos (17 a 51 movimentações); ❌ `Unexpected process detail endpoint` em 0700047-00.2026.8.02.0047 | ❌ `flow_required: No authorized document viewer link in the account dashboard` (9 processos de petições) | |
| tjam-esaj | ✅ 1 (`sessionSource: sajcas`) | ✅ intimações 0, audiências 0, petições 0/0, caixa postal 1 | — | ✅ 0500048-00.2024.8.04.0001 (7 mov.) | ❌ `flow_required: No authorized document viewer link...` | não há processo no painel |
| trf6-1g / trf6-2g | não tentado | — | — | — | — | Falta configuração: o `portals.toml` pede `browser_totp` com `TRF6_USERNAME`/`TRF6_PASSWORD`, que não existem. Em 13/11 a entrada foi só com o A1, por navegador + proxy local (mitmproxy), caminho que o conector não tem. `TRF6_2FA_QR` e `TRF6_OTP_PROFILE_MATCH` aparecem 2× no `customers/herica/.env` |
| tjto-1g / tjto-2g | não tentado | — | — | — | — | DNS às 16:09: `mtls.tjto.jus.br` continua **NXDOMAIN** (getent rc=2; DoH Google e Cloudflare Status=3; SOA `ns.tjto.jus.br` serial 2026111710). `eproc1/2.tjto.jus.br` resolvem (177.0.107.47/48) |

Números: 19 eproc + 1 Projudi + 4 e-SAJ = 24 portais com login, e 23 entraram (o tjrj-eproc-1g falhou por rede). A cadeia completa até o documento passou em 17: 15 eproc (tjsp com flags só no processo), tjgo e tjms. Os demais eproc (trf2-2g, tjpr-2g, tnu) pararam na lista porque não há processo, e o tjrj-eproc-1g falhou no login. Nenhum TOTP foi recusado.

## Bugs encontrados (não corrigidos)

1. **eproc_get_process quebra com texto que tem surrogate solto** (TJAC 2G). `core/document_text.py::extract_document_text` devolve o texto do `pypdf` (`page.extract_text()`) com o caractere `\ud83d`, metade de um emoji. Na hora de serializar a resposta em `connectors/eproc/server.py::eproc_get_process`, o Pydantic falha com `PydanticSerializationError ... surrogates not allowed`, e a chamada inteira se perde (metadados, linha do tempo e aviso de ciência). O documento já tinha sido baixado. Esse caminho é usado por todos os conectores que extraem texto de PDF. Correção sugerida: normalizar o texto (por exemplo, `encode('utf-8','replace')`).
2. **Configuração do TJSP eproc (customers/herica/portals.toml)**. A seção declara `certificate_totp`, mas o `[env].set` não tem `TJSP_1G_CERT`/`TJSP_2G_CERT` nem `TJSP_1G_USER_PROFILE`/`TJSP_2G_USER_PROFILE` (`SP527617`). Com isso, `client.authenticate` cai no login por senha e para em "Set TJSP_1G_USERNAME and TJSP_1G_PASSWORD in .env". Com as 4 variáveis definidas só no processo, tudo passou. O registro no `portals.py` continua como `browser_totp`.
3. **e-SAJ: documentos só abrem para processos que o painel do advogado resolve**. `esaj/base.py::dashboard_process` consulta `tarefas-adv/api/processos?numerosProcesso=` e recarrega as petições. No TJSP, no TJAL e no TJAM não resolveu nenhum dos 20 processos tentados, mesmo os das petições concluídas. No TJMS funcionou. Resultado: `esaj_list_documents`/`esaj_read_document` não funcionam no TJSP e no TJAL na prática.
4. **e-SAJ TJSP: `tarefas-adv/api/intimacoes` excede o timeout de 30 s** (`core/http.TIMEOUT`) nas 2 sessões, e a fila de intimações do TJSP fica ilegível.
5. **e-SAJ TJSP: `esaj_get_process` devolve sempre 5 movimentações** (10 processos). Parece que o parser lê só a lista recolhida das "últimas movimentações". No TJMS (49) e no TJAL (17 a 51) a lista veio completa.
6. **e-SAJ `Unexpected process detail endpoint`** (TJSP 1000045-00.2025.8.26.0233, TJAL 0700047-00.2026.8.02.0047): a consulta redireciona para uma rota que `load_process` não aceita. Provavelmente é uma lista de processos/incidentes ou outra instância.
7. **Projudi: não há ferramenta de intimações nem de lista de processos.** É preciso saber o número antes (aqui saiu do DJEN). Também não há trava de ciência no Projudi.
8. Observação: o eproc lê o documento antes de checar a ciência e só depois avisa (decisão de 09/11). Para validar isso com segurança, o harness teve de repetir antes a checagem `pending_notices`.

## O que a cliente/equipe precisa fazer

- **TJSP eproc**: no `[env].set` da Hérica, incluir `TJSP_1G_CERT`, `TJSP_2G_CERT` = "true" e `TJSP_1G_USER_PROFILE`, `TJSP_2G_USER_PROFILE` = "SP527617" (é ajuste de configuração, nada muda para a cliente).
- **TRF6**: fornecer usuário e senha do eproc TRF6, ou implementar o login A1 por navegador. Remover a duplicata de `TRF6_*` no `.env`.
- **TJTO**: nada a fazer; o tribunal continua sem DNS para `mtls.tjto.jus.br`. A alternativa é a cliente fornecer usuário e senha do eproc TJTO.
- **TJRJ eproc**: retestar mais tarde (falha de rede, as credenciais não foram testadas).
- **e-SAJ**: os documentos do TJSP, do TJAL e do TJAM dependem dos bugs 3 a 5. A cliente não precisa fazer nada.
