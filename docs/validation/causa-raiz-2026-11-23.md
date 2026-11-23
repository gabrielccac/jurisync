# Causa raiz das falhas da regressão de 23/11/2026

Falhas vistas em `regressao-2026-11-23.md`, reproduzidas ao vivo (só leitura) e corrigidas.

## Projudi e Eproc

### Projudi TJGO (anaflavia · `tjgo-1g`): "Process not found or not accessible to this account"

**Reprodução (um login, uma sessão, só leitura; nenhuma intimação aberta):**

| Passo | Resultado |
|---|---|
| `BuscaProcesso` de 5847109-96.2026.8.09.0051 **antes** das listas | ✅ "Dados Processo" (processo em segredo de justiça, acessível à conta) |
| `projudi_list_intimations` + `projudi_list_processes` | ✅ Diário 81, completa; 12 categorias, completa. O processo está na categoria 83 "UPJ dos Juizados Especiais Cíveis" |
| A mesma busca **depois** das listas (ativo e arquivado) | ❌ "Nenhum Processo foi localizado" |
| Busca de outro processo tirado da própria lista (5185787-46) | ❌ "Nenhum Processo foi localizado" |
| `POST BuscaProcesso PaginaAtual=4` (botão "Limpar" do formulário) e a mesma busca | ✅ os dois processos abrem |

**Causa:** o servlet `BuscaProcesso` guarda os filtros da busca na sessão e cada pedido só troca os campos que envia. As listas de processos próprios (`Proprio=1&Id_ServentiaSubTipo=<n>&ProcessoStatusCodigo=0`, uma por categoria) deixam na sessão o tipo de serventia da última categoria lida (81, Fazenda Pública Estadual). A busca por número que vem depois não manda `Id_ServentiaSubTipo`, por isso procura só nesse tipo. A busca de arquivados também deixa o status "1" para a busca seguinte. Não houve mudança de acesso nem de serventia: em 17/11 a busca veio antes de qualquer lista. Mandar `Id_ServentiaSubTipo=""` não basta, porque o status continua preso.

**Correção** (`connectors/projudi/tjgo.py`): toda busca por número começa pelo "Limpar" do próprio formulário (`_clear_search`, `POST BuscaProcesso PaginaAtual=4`). É só navegação e custa um pedido a mais.

**Confirmação ao vivo, na mesma sessão:** listas → `load_process` de 5847109-96.2026.8.09.0051 deu ✅ 24 movimentações e 30 arquivos, igual a 17/11. Listas de novo → 5185787-46.2026.8.09.0174 ✅ (89 mov., 50 arquivos), e pelo número curto também ✅.

### Eproc TRF6 (fernanda · `trf6-1g`): "login page did not open in the browser"

**Código:** `certificate._browser_login_page` levantava `RuntimeError` sem motivo. Agora levanta `LoginFailed` com motivo e diz o que o Chrome mostrava (forma da URL sem query, status HTTP da navegação e título):
- `captcha` quando o desafio anti-robô do F5 (`bobcmn`/TSPD, ou marcadores de Cloudflare/Imperva/"verify you are human") não libera a página em 60 s. Também quando o F5 recusa ("The requested URL was rejected"). Nesse caso para na hora, sem nova tentativa. O "support ID" do `<noscript>` da página de desafio não conta como recusa.
- `portal_unavailable` quando a entrada não carrega (timeout ou erro de rede do Selenium) ou quando nenhuma página de login aparece.
- Se a página assenta sem desafio e sem o botão do certificado (a home do portal, ou o formulário clássico), o navegador abre a entrada SSO (`externo_controlador.php?acao=SSO/login`) uma vez, como o login HTTP (`_login_page`) já fazia. Nada é digitado nem clicado.

**Diagnóstico ao vivo (só navegação, sem credencial):** o Chrome headless (`uc=True`, as mesmas opções) recebe a página de desafio do F5 com HTTP 200 e, em cerca de 3 s, chega a `sso-eproc.trf6.jus.br/realms/eproc/protocol/openid-connect/auth` com o botão `certificado-sso-eproc.trf6.jus.br`. Isso vale pela raiz `/eproc/` e pela entrada SSO. Redirecionamento e marcação não mudaram, e o desafio é transposto como em 19/11.

**Tentativa controlada (um login, pelo mesmo caminho da ferramenta):** ✅ login com certificado A1 + TOTP; `eproc_list_notices` pendentes 0 linhas (11 s); `eproc_list_processes` 6 processos, como em 19/11.

**Causa da falha de 23/11:** não foi reproduzível. O portal e o desafio funcionam hoje, e o código de navegação é o mesmo. A rodada da regressão não guardou o que o Chrome viu, então não dá para saber se foi uma recusa momentânea do F5, uma lentidão do portal ou um problema local do Chrome (havia outras rodadas com navegador em paralelo). Com a mudança, uma nova ocorrência sai com `[captcha]` ou `[portal_unavailable]` e com a página vista. Não há nada a corrigir na detecção da página de login.

**Testes:** suíte completa 1445 ✅ (1 pulado), 5 novos (1 Projudi, 4 Eproc) e 1 reescrito (Eproc: desafio que não libera agora exige `LoginFailed`/`captcha`). `ruff check` e `ruff format --check` limpos.

## e-SAJ

Reprodução ao vivo, só leitura, com o A1 da herica: **1 login por portal** (tjsp-esaj, tjal-esaj, tjam-esaj). No TJSP a sessão foi reaproveitada entre os scripts (cookies num arquivo 0600 do scratch, conferidos por `esaj/api/auth/session` antes de cada uso). Nenhuma intimação aberta, nenhuma ciência, nenhuma petição. Os processos com intimação sem ciência (3 no TJSP, 15 no TJAL) foram barrados pela trava do conector, sem nada abrir. Os documentos lidos passaram antes pela checagem `validarAberturaIntimacaoNaoRecebida`, que respondeu "sem pendência". Base: `chore/hardening`, que já tinha o 0af7e25 (consulta do painel por código), ainda não validado ao vivo.

### 1. `flow_required: No authorized document viewer link in the account dashboard`

**Reprodução (TJSP):** o painel (`tarefas-adv/api/processos?cdsProcesso=`) devolve `urlPasta` para os processos das próprias listas de tarefas (petições e intimações; 6 de 7 códigos). Com isso, 3 processos de petições abriram (70, 66 e 13 documentos). Já para os 21 processos citados na caixa postal, o painel não devolveu **nada**, nem pelo número nem pelo código: 16 deram `flow_required` e 2 deram `intimacao_pending` (correto).

**Causa:** o painel só resolve os processos das listas de tarefas. O navegador abre a pasta por outro caminho, o botão "Visualizar autos" do `cpopg/show.do`: `a.linkPasta` → `GET /cpopg/abrirPastaDigital.do?processo.codigo=<código>`. A resposta é **texto puro com a URL** do visualizador (`/pastadigital/abrirPastaProcessoDigital.do?nuProcesso=…&cdProcesso=…&instanciaProcesso=pg&cdProcessoMaster=…&cdServico=190101&…&ticket=<uso único>`), que o script da página então abre. O conector não conhecia esse caminho.

Caso particular, 1006316-80.2025.8.26.0278, "Em grau de recurso": o `show.do` do 1º grau não tem "Visualizar autos", só "Consultar em 2ª instância" (`/cpopg/abrirConsultaProcessoSG.do`). Esse link leva ao `/cposg/show.do`, cujo botão (`#pbVisualizarAutos`, `href="#"`) chama `GET /cposg/verificarAcessoPastaDigital.do?cdProcesso=<input oculto cdProcesso>`, conforme `acessoRecurso-2.10.2-0.js`. Pelo script, 200 é a URL do visualizador, 406 é o limite diário de pastas em processos não vinculados, e qualquer outra resposta é o diálogo de senha. Ao vivo, o 2º grau respondeu 403 com o diálogo de senha ("Se for advogado(a) nesse processo…", campo `senhaProcesso`), porque a conta não está vinculada ao recurso. Já o `abrirPastaDigital.do` do 1º grau, chamado com o código do processo, **serve** a pasta de 1º grau para a conta vinculada.

**Correção** (`esaj/base.py::detail_viewer`, `second_instance_records`; `parsers.records_link`, `second_instance_link`, `hidden_process_code`):
- O painel continua sendo o primeiro caminho. Quando ele não traz `urlPasta`, o conector usa o "Visualizar autos" do `show.do`. A URL devolvida só é seguida se estiver no host do portal, no caminho `/pastadigital/abrirPastaProcessoDigital.do` e com o `nuProcesso` do próprio processo. O link de acessibilidade é ignorado.
- Para processo em grau de recurso, o conector tenta primeiro a pasta do 2º grau, pelo mesmo caminho do botão. Se o 2º grau pedir senha, cai na pasta do 1º grau. O 406 vira `access_limit` e nada mais é tentado. Se os dois graus recusarem, a resposta é `password_required`. A recusa de senha (`#liberarAutoPorSenha`) continua `password_required`.
- Com o código em mãos, as 4 listas de petições não são mais recarregadas (só sem código, quando a consulta pede CAPTCHA).
- `request(..., accept=(403,))` deixa a checagem do 2º grau ler o próprio 403. Em qualquer outro lugar, 403 continua sendo `EsajBlocked`.

**Confirmação ao vivo:**
- TJSP: 15 dos 16 processos da caixa postal que davam `flow_required` passaram a listar de 27 a 102 documentos, todos `permission_check_required`. O 16º (o de grau de recurso) lista 56 documentos pela pasta de 1º grau.
- `esaj_read_document` no TJSP: 0001082-60.2026.8.26.0356 deu PDF de 63 KB, `extracted`, 7.934 caracteres; 1006316-80.2025.8.26.0278 deu 263 KB, `extracted`, 34.119 caracteres.
- TJAL: os 17 processos de petições e da caixa postal sem intimação pendente listaram de 15 a 96 documentos. Leitura de 0700043-25.2026.8.02.0042: 286 KB, `extracted`, 44.485 caracteres.
- TJAM: o único processo da conta (0547731-07.2024.8.04.0001) dá `password_required`, porque o "Visualizar autos" só abre o pedido de senha (`#liberarAutoPorSenha`) e a conta não está vinculada. **Não tem remédio sem a senha do cartório.**

### 2. `Unexpected process detail endpoint` (TJSP 1001092-05.2025.8.26.0233, TJAL 0700505-64.2026.8.02.0047)

**Reprodução:** os dois processos hoje abrem pelo painel (`consultationSource: dashboard`), com 41 e 26 movimentações. A causa foi a já descrita no 0af7e25: o painel lista uma entrada por instância (`PG`/`SG`) sem ordem fixa, e o `urlCpo` de 2º grau (`/cposg/show.do`) caía no teste de rota. Esse commit escolhe a entrada `PG` e manda a entrada só-`SG` para a consulta de 1º grau. Confirmado ao vivo.

**Falha nova achada na reprodução (TJSP):** consultas seguidas deram `flow_required: No unique process detail available to this account` em processos que existem. O `search.do` respondia 200 com o formulário e a mensagem **"Foram identificadas multiplas consultas simultâneas."** Medição: 4 buscas com 0,1 s de intervalo levaram essa resposta; com 0,5 s também; com 1 s ou mais a busca foi servida. **Correção:** `consult()` reconhece a mensagem (`parsers.simultaneous_queries`) e repete a página após 2 s e depois 4 s. Se o portal continuar recusando, a resposta é `rate_limited`, com o motivo, e nunca vira "processo não encontrado" nem tentativa pelo navegador. Vale para `search.do` (HTTP e com token) e para o `show.do`. **Ao vivo:** 16 `list_documents` e 4 `get_process` em sequência, sem pausa no script, passaram todos; vários levaram 3,3 s, ou seja, uma repetição.

### 3. Timeout de `tarefas-adv/api/intimacoes` (TJSP)

**Medição ao vivo:** a 1ª chamada **de qualquer lista do painel** na sessão é lenta, e as seguintes são imediatas. `peticoes` levou 57,5 s como 1ª chamada da sessão. `intimacoes` levou 27,9 s como 1ª chamada de intimações, e a repetição 0,0 s (3 itens, 2,3 KB). Em 17/11 foram mais de 30 s duas vezes; o 0af7e25 registra 65 s e mais de 180 s. **A API não aceita paginação nem período:** o bundle do painel (`main.*.chunk.js`) faz `GET /api/intimacoes` sem parâmetros. Só `peticoes-filtro` (POST com datas) tem período. **Causa:** o tribunal monta as listas da conta no 1º pedido da sessão. O 0af7e25 deu 150 s só às intimações, enquanto `peticoes`/`audiencias` (`esaj_list_tasks`) seguiam com 30 s e `processos` com 60 s, e a 1ª chamada medida (57,5 s) passaria disso. **Correção:** `panel()` aplica `PANEL_TIMEOUT = 150` s a **todas** as chamadas `tarefas-adv/api/*`, e o timeout vira `portal_timeout` com orientação. **Ao vivo:** todas as listas responderam (intimações 3, audiências 0, petições 4/0/0/4).

### 4. `paged_movements` no TJSP

**Confirmado ao vivo, sem mudança:** `carregarMovimentacoesAjax.do` paginado por cursor traz o histórico inteiro. Em 1006125-49.2025.8.26.0047 vieram 66 movimentações e em 1000713-48.2025.8.26.0691 vieram 81, `complete=True`, sem duplicatas, em ordem decrescente, terminando em "Distribuído Livremente (por Sorteio)". Os outros processos trouxeram de 4 a 81 movimentações. As "5 movimentações" de 17/11 eram anteriores ao 0af7e25.

**Testes:** `tests/connectors/esaj/test_esaj_panel.py` ganhou 18 casos: espera do painel para cada lista, retentativa e `rate_limited` no bloqueio de consultas, "Visualizar autos" com a URL devolvida checada (host, caminho e número), 2º grau / recurso ao 1º grau / `access_limit` / `password_required`, e o 403 lido só quando pedido.

## PJe

Leituras ao vivo só de leitura, com a trava de ciência ligada (`pending_notice`) e sessões em cache (nenhum login novo foi preciso). Nenhum `acknowledgeCiencia`, nenhum ato de intimação aberto.

### 1. TJBA 1G (juliana): download da petição 507664773 com `request failed: Timeout`

**Medição (8020841-42.2025.8.05.0080):**

| Pedido | Primeiro byte | Corpo | Tamanho |
|---|---|---|---|
| 507664773 (Petição Inicial), 1ª vez | 67,4 s | 1,5 s | 878.936 B |
| mesma, pela ferramenta (limite antigo de 165 s) | nada em 165 s, duas vezes | — | — |
| mesma, logo depois de cada falha | 11,7 s e 2,3 s | ~1 s | 878.936 B |
| 507664774 (Procuração), fria | 30,3 s | 0,8 s | 403.955 B |
| 520030341 (Certidão, HTML) | 0,3 s | — | 4.369 B |
| rota curta `.../documento/download/<id>` | HTTP 404 (o TJBA só serve a rota longa) | | |

**Causa:** o TJBA prepara o PDF antes de mandar o primeiro byte (de 30 s a mais de 2 min quando "frio") e depois o mantém pronto por um tempo. O corpo chega em cerca de 1 s. O `_download` usava `timeout=30` com `stream=True`, e no curl isso vira "menos de 1 byte/s por 30 s". Por isso todo PDF frio caía em `Timeout`, a procuração inclusive (30,3 s).

**Correção** (`pje/documents.py`): tempo de conexão de 15 s e espera de 120 s pelo primeiro byte (`timeout=(15, 120)`). Um pedido que não recebeu nada é repetido uma vez (`_download_patiently`): o portal termina de preparar o PDF entre os dois. O corpo tem prazo de 300 s por documento. Depois de 360 s na chamada, os documentos que faltam voltam `not_attempted` com o motivo. O teto de 10 MiB continua. Se nada chegar nas duas tentativas, o `failure` diz "the portal sent nothing for 135 s, twice; it may still be preparing the document". No PJe-JT, `kz.fetch_document` usa a mesma espera.

**Confirmação ao vivo:** `read_documents` de 507664773 ✅ `extracted`, 47.866 caracteres, 57,9 s.

### 2. TJPA 1G (juliana): item 151361043 "sem local de download"

- `pje_list_watched_processes` (código novo do Push): ✅ 6 linhas, `hasMore=false`, sem erro.
- `search_process` de 0870742-49.2025.8.14.0301: `download_pattern_found=False`. Nenhum dos 29 itens do inventário tinha URL, então a falha era `NO_ROUTE`, não `NO_LINK`.

**Estrutura capturada (sem dados pessoais):** o TJPA não tem `window.open('.../documento/download/...')` nem `var url = ... + idDocumento`. A rota aparece só no visualizador: `<iframe id="frameBinario" src="/pje/seam/resource/rest/pje-legacy/documento/download/<id>#toolbar=0" ...>`. O `DOWNLOAD_PATTERN` exigia `"` logo depois do id, e o `#toolbar=0` fazia a regra falhar.

**Correção** (`pje/search.py`): a terceira alternativa do `DOWNLOAD_PATTERN` aceita um fragmento opcional (`\d+(?:#[^"]*)?"`). A URL montada continua sem query nem fragmento, dentro da rota que `documents.py` aceita.

**Confirmação ao vivo:** `read_documents` de 151361043 ✅ HTTP 200, PDF de 762.477 B, `extracted`, 28.976 caracteres (9 s, sem clique no visualizador).

### 3. Keycloak: falso `credentials_refused`

**Estado encontrado:** o commit 2416cc1 já tinha trocado os marcadores amplos por textos exatos de recusa e já citava o alerta do SSO. O que faltava: erros de sessão ("Sessão inválida", "Invalid Request", "Você já está autenticado", "Ação expirada") caíam em `unknown`, e todo erro de fluxo terminava num `RuntimeError` sem nova tentativa.

**Correção** (`pje/auth/sso.py`):
- `FLOW_ERRORS` passou a cobrir as mensagens de fluxo do Keycloak em pt-BR e en: cookie, expirou/expirada, timed out, sessão/session, requisição inválida/invalid request, já autenticado/already logged in, erro inesperado.
- `credential_refusal` devolve `LoginFailed` (`reason=unexpected`, `flow_error=True`) para esses casos, com o alerta sanitizado na mensagem (CPF e e-mail mascarados).
- `login_session` faz uma tentativa nova, com sessão nova, só para `flow_error`. Se falhar de novo, a mensagem diz "twice". Recusa (`credentials_refused`, `password_expired`, `blocked`) continua `LoginRejected`, nunca é repetida e mantém o cool-down. `Parâmetro inválido: redirect_uri/client_id` fica `unknown`, sem nova tentativa.

**Testes:** 5 recusas × 2 temas e 13 erros de fluxo × 2 temas; uma recusa = 1 POST; erro de fluxo = 2 POSTs e "twice"; erro de fluxo seguido de sessão nova que chega ao OTP. Não houve login ao vivo, porque não dava para reproduzir sem arriscar a conta.

### 4. PJe-JT (KZ): reaproveitar a busca entre `get_process` e `read_documents`

**Causa:** a memória de 180 s (`search._recall`) só valia para o PJe clássico. `search_process` desvia para `kz.search_process` antes de consultá-la.

**Correção** (`pje/kz.py`): memória própria por sessão (número → id, registro, partes, timeline), por 180 s. Os expedientes são lidos de novo a cada chamada, para a trava de ciência. Uma leitura em que as partes falharam não é guardada.

**Confirmação ao vivo** (fernanda · `trt1-1g`, 0101627-48.2026.5.01.0013): `get_process` 6 pedidos; `read_documents` da Petição Inicial **3 pedidos** (antes 7): token, expedientes, documento. ✅ `extracted`, 68.352 caracteres, ciência `nenhuma`.

### 5. eliane: "[login_missing] Set PJE_A1_PIN…" sem certificado nenhum

**Causa:** `auth_method` só escolhia senha num portal de certificado (TRTs, TRF5) quando usuário, senha **e** autenticador estavam presentes. Sem o autenticador, caía em `certificate_totp`, e `load_configured` pedia o PIN de um A1 que a conta não tem.

**Correção:** com usuário e senha e sem A1, o método é `password_totp` (`auth/sessions.py`). `credentials()` (`process.py`) diz "Password login to trt7-1g also needs the PDPJ authenticator: … set TRT7_1G_2FA_DATA or TRT7_1G_2FA_QR". Sem login nenhum, `cert_login` cita os dois caminhos: usuário, senha e 2FA, ou `PJE_A1_P12`/`PJE_A1_PIN`. O motivo continua `login_missing`.

**Confirmação (local, sem rede, com o `.env` da eliane):** trt5-2g, trt7-1g e trt24-1g → `password_totp`, `[login_missing] Password login to … also needs the PDPJ authenticator: … TRT*_2FA_DATA or TRT*_2FA_QR`.

### 6. PDFs digitalizados (`ocr_needed`)

**Motor:** o ADVBOX usa o `pdf-inspector` (`process_pdf_with_ocr_bytes`, PP-OCRv6 via ONNX). Ele exige uma biblioteca PDFium e o ONNX Runtime. No `.venv` nada disso era encontrado ("failed to load PDFium"), então o OCR do ADVBOX também não rodava aqui. Os modelos já estavam no cache (`~/.cache/pdf-inspector`).

**Correção:**
- Novo `core/ocr.py`. Usa o mesmo motor, só quando o PDF não tem camada de texto e tem imagens. Lê no máximo 30 páginas, 4 por chamada, e não começa chamada nova depois de 90 s (`JURISYNC_OCR_MAX_PAGES`/`_SECONDS`). Usa `offline=True`, então nunca baixa modelo durante uma leitura. `JURISYNC_OCR=off` desliga.
- Sem `PDFIUM_LIB_PATH`/`ORT_DYLIB_PATH`, procura as bibliotecas nos wheels `pypdfium2` e `onnxruntime`.
- Novo extra opcional `pdf-ocr` (`pypdfium2==4.30.0`, `onnxruntime==1.30.0`), com `uv.lock` atualizado. Instalado no `.venv` do worktree.
- Em `core/document_text.py`, o status fica `extracted_ocr` (ou `extracted_ocr_partial` quando o limite cortou páginas). Sem motor, continua `ocr_needed`.

**Confirmação ao vivo** (natalia · `tjpa-1g`, 0802512-14.2023.8.14.0013, Documento de Comprovação 98734222, `ocr_needed` em 19/11): ✅ `extracted_ocr`, 716 caracteres, 16 s.

**Pendência fora do PJe:** em `connectors/eproc/documents.py:202-203` o `save` só grava `extracted` ou `ocr_needed`/`empty`. Um PDF lido por OCR (`extracted_ocr*`) deixaria de ser salvo. O arquivo é de outro agente e não foi mexido.

### Testes

Suíte completa: 1514 ✅, 1 pulado. `ruff check` e `ruff format --check` limpos. Novos ou alterados: `test_pje_documents.py` (espera e repetição do download, prazo do corpo, orçamento da chamada), `test_pje.py` (iframe do TJPA), `test_pje_password_sso.py` (recusa × fluxo × repetição), `test_pje_kz.py` (contagem de pedidos com a memória, expiração, leitura incompleta não guardada, espera do PDF), `test_pje_auth.py` (senha sem autenticador, sem login nenhum), `tests/core/test_ocr.py` (PDF digitalizado gerado e lido pelo motor real; sem motor; desligado; limites de páginas e tempo; status parcial).
