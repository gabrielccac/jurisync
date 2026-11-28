# Eproc — cobertura atual e features planejadas

Levantamento feito ao vivo no **JFSC** (`trf4-1g-sc`, `eproc.jfsc.jus.br`) em 2026-10-14,
dumpando o painel autenticado e a tela de processo. Serve de backlog para ampliar o
connector `src/jurisync/connectors/eproc/` (read-only).

## Cobertura atual

- Tools: `eproc_list_intimacoes` (filas listadas em `QUEUE_KINDS`; sem fila, pendentes e prazos abertos juntos,
  cada item com `situacao`/`prazoFinal` de `core.intimacao`), `eproc_get_counters` (contadores do painel e de cada
  instância), `eproc_list_audiencias` (audiências futuras, realizadas e não
  realizadas, cada uma com a lista de conciliação), `eproc_list_processes` (Relação de
  Processos do advogado; baixados ficam de fora em todo portal salvo `includeArchived`, filtros `prazo` e
  `intimationDateFrom`/`intimationDateTo`), `eproc_search_processes` (Consulta Processual do tribunal) e
  `eproc_get_process` (eventos, `prazos` de cada intimação, informações adicionais, audiências e perícias,
  documentos até 5, `save` opcional). Formatos conferidos no TJMG e na JFRS em 27/11/2026. Mais as listas
  read-only da seção "Listas do menu" abaixo.
- As duas tools de lista aceitam `processNumber` (filtro aplicado aqui sobre as linhas lidas: nenhuma das duas listas
  do portal tem campo de número; com o filtro, `total` do envelope é o achado quando a lista foi lida inteira, senão
  `null`); todo item traz `processNumber` ao lado de `number`, que fica por um ciclo.
- Ações no allowlist (`READ_ACTIONS`/`AJAX_ACTIONS`): as 2 filas + `painel_adv_listar`,
  `processo_pesquisa_rapida`, `processo_selecionar`, `processo_consulta_listar`,
  `relatorio_processo_procurador_listar`, `arvore_documento_listar`,
  `acessar_documento(_implementacao)`; ajax: `montar_tabela_eventos_filtrados`,
  `retornar_citacao_intimacao_estado`.
- Login: SSO Keycloak via browser headless (SeleniumBase) + TOTP.

## Ciência ao ler documentos (trava desde 2026-11-27)

`eproc_get_process` lê capa, partes, eventos e inventário sem abrir documento. Com `documentIds`, antes de abrir
qualquer coisa, o conector lê as filas de pendentes do advogado (`pending`, `pending_urgent`, `pending_agreement`,
`art334`) e procura o processo nelas:

- processo em fila pendente: recusa com `ciência pending`, nada aberto;
- filas que não puderam ser lidas até o fim: recusa com `ciência not checked`, nada aberto;
- em ambos os casos a leitura só acontece com `acknowledgeCiencia=true` (autorização do usuário, agora ou antecipada)
  ou com `[eproc] ciencia_on_read = "allow"` no `settings.toml` do escritório, e o bloco `ciencia` informa `dada` ou
  `desconhecida`.

É a mesma regra do PJe e usa o mesmo texto de autorização (`core.ciencia.AUTHORIZATION`).

**Mudança de decisão.** Em 09/11/2026 ficou decidido que os documentos do Eproc eram sempre abertos, com o aviso
`dada` depois da leitura. A exploração ao vivo do JURI-21 mostrou que o aviso chega depois de um fato irreversível
(a ciência inicia o prazo), e em 27/11/2026 a regra passou a ser a do PJe, a pedido do Gabriel.

## Inventário pela Árvore (2026-11-27, JURI-21 item 5)

A linha do tempo de `eproc_get_process` é lida até 5 páginas (`MAX_EVENT_PAGES`); um processo maior fica com
`pagination.complete=false` e `event_pagination_limit`, e os documentos dos eventos não lidos faltavam no
`documentInventory`. O botão **Árvore** da tela do processo (`arvore_documento_listar&txtNumProcesso=...&hash=...`)
lista todos os documentos dos autos sem abrir nenhum, e agora completa o inventário (`eproc/arvore.py`).

- `inventory` (`auto` por padrão): `auto` lista a Árvore quando a linha do tempo não foi lida até o fim ou quando um
  `documentIds` pedido não está nela; `tree` lista sempre; `timeline` nunca (uma requisição a menos).
- `inventorySource`: `tree` quando a Árvore entrou no inventário (linha do tempo + Árvore), senão `timeline`. Se a
  Árvore não puder ser lida, o inventário fica o da linha do tempo e `diagnostics` diz por quê:
  `document_tree_unavailable` (a página não tem o botão para este processo), `document_tree_failed` (erro HTTP ou
  acesso negado), `document_tree_empty` (página sem nenhum link de documento reconhecido).
- Cada documento achado só pela Árvore traz `id`, `eventId`, `title` (rótulo, ex. INIC1), `description` (dica,
  ex. PETIÇÃO INICIAL) e `date` (ISO; a do próprio nó ou a do evento). Documentos que já estão na linha do tempo
  ficam com a entrada dela.
- Orçamento: quando a Árvore entrou (`inventory: "tree"` na resposta como `inventorySource: "tree"`), `documentInventory`
  mostra 150 entradas por resposta a partir de `inventoryOffset`; `inventory` traz `returned`, `total` e `next`, os
  argumentos para ler o resto (`inventory: "tree"`, `inventoryOffset`, `documentIds: []` e `save: false`, para que seguir
  o `next` nunca reabra nem regrave documentos). Cada chamada relê o processo inteiro. `documentIds` são procurados no
  inventário completo, não só na fatia mostrada.
- Compatibilidade por um ciclo: com `inventorySource: "timeline"` o `documentInventory` sai inteiro, como antes da
  Árvore (`inventoryOffset` não corta nada e `inventory.next` é `null`).
- Ciência: abrir a Árvore só faz GET em `arvore_documento_listar` (rota própria em `require_route`, nunca
  `acessar_documento`). Um documento achado só na Árvore é lido pelo mesmo `read_selected_documents` e passa pela
  mesma trava (`ciencia.check` antes de abrir; recusa com `ciência pending`/`ciência not checked` sem
  `acknowledgeCiencia`). `save=true` vale só para os `documentIds` pedidos, como antes.

**Falta validar ao vivo.** A página da Árvore não foi aberta na exploração do JURI-21 (só o botão aparece nas capturas
de `processo_selecionar` do TJSC 1G, TJRJ 1G, TRF4 2G e TRF6 2G, junto com `InfraArvore.js`). O parser lê as duas
formas mais prováveis do padrão infra: âncoras para `acessar_documento` com `doc=` e `evento=` (com a data no nó ou
no cabeçalho "Evento N - DD/MM/AAAA ...") e nós `new infraArvoreNo(...)` do script com o mesmo link. Se a página real
montar a árvore por outra chamada (ajax ou frame), o resultado é `document_tree_empty` e o inventário continua o da
linha do tempo; aí é preciso capturar a página e ajustar o parser.

**Download Completo não implementado.** O botão chama `selecionar_processos_agendar_arquivo_completo`: ele agenda a
geração do arquivo completo dos autos no servidor do tribunal (um job), ou seja, é escrita. Fica fora do allowlist; o
teste `TreeRouteTest` prova que ela, `download_documentos_evento`, `abrir_em_abas_documentos_evento` e as ações de
escrita seguem bloqueadas.

## Audiências do advogado (`eproc_list_audiencias`)

As seis listas da seção "Audiências" do painel, lidas pelo link que o próprio painel publica e conferidas por
`require_route` com a ação de cada lista (`HEARING_SITUATIONS`/`HEARING_ACTIONS` em `client.py`, somadas a
`READ_ACTIONS`). Nada é aberto e não se dá ciência.

| `situation` | `kind=audiencia` | `kind=conciliacao` | Ordem |
|---|---|---|---|
| `upcoming` (padrão, comportamento anterior) | `audiencia_futura_listar` | `audiencia_futura_conciliacao_listar` | mais próxima primeiro |
| `held` | `audiencia_realizada_listar` | `audiencia_realizada_conciliacao_listar` | mais recente primeiro |
| `not_held` | `audiencia_nao_realizada_listar` | `audiencia_nao_realizada_conciliacao_listar` | mais recente primeiro |

- Sem `kind`, as duas listas da situação são lidas e juntadas (como antes nas futuras).
- `processNumber` (opcional) mantém só as linhas desse processo; `total` passa a ser o número achado quando as
  listas foram lidas até o fim, senão `null`. Sem filtro, `total` é a soma dos contadores do portal.
- `maxPages` (1 a 10) segue o paginador Infra de cada lista (reposta o formulário com `hdnInfraPaginaAtual`, como a
  Relação de Processos); resposta acima de `core.responses.BUDGET` é cortada e `next` traz `{"maxPages", "offset"}`.
- Itens: `processNumber` (com `number`, o nome antigo), `start`/`end` em ISO (`startText`/`endText` com
  o texto do portal por um ciclo) e `date` (o dia de `start`); sem data, o item vai para o fim em qualquer ordem. `lists[]` diz por
  lista `count`, `complete` (a legenda "(N registros)" manda: menos linhas lidas que N é incompleto),
  `totalItems`, `totalPages` e `problem` (fora do painel, erro do portal, layout não reconhecido, página que não
  bateu).
- Tela que não traz nem a tabela conhecida nem a área de tabela vazia vira `problem: "layout not recognized"`, para
  não confundir mudança de layout com agenda vazia.

**Falta validar com linhas reais as listas realizadas e não realizadas.** O layout das futuras foi conferido no TJMG e
na JFRS (27/11/2026); as outras quatro estavam vazias em todas as capturas do JURI-21 (TJSC 1G/2G, TJRJ, TRF4 2G,
TRF6 2G), então o parser assume as mesmas colunas (Número Processo, Evento, Local Data/Hora, Observação). A
paginação também não foi vista ao vivo em nenhuma lista de audiências.

A tabela "Situação | Quantidade de Audiências" do `painel_advogado_listar` (TJSC 1G e TJRJ) fica com a frente de
contadores.

## Relação de Processos (implementado em 2026-10-21)

Chamada MCP: `eproc_list_processes({"portal": "trf6-1g"})`.
CLI: `python -m jurisync.connectors.eproc.client processes --portal trf6-1g`.

Usa a sessão existente e o link assinado anunciado pelo painel, com um GET ao relatório.
Retorna número CNJ, juízo, classe, partes, localidade, assunto, último evento e sua data,
autuação e valor da causa. Os números podem ser usados em `eproc_get_process` para
buscar movimentos e textos dos documentos.

O escopo é o relatório do advogado autenticado, com os filtros padrão do portal,
não uma busca irrestrita de todos os processos do tribunal. Em todos os portais observados
(TRF4, TRF6, TJSC e TJRJ, 27/11/2026): situação Todos e baixados excluídos. `filters` informa
esses valores quando disponíveis. As partes vêm com `pole` (`active` para "Autores Principais",
`passive` para "Réu(s)"): a relação não mostra o polo processual (Requerente no TRF4, Apelante
no TRF6), que só aparece em `eproc_get_process` (`parties[].role`).
Com `maxPages` maior que 1 as páginas seguintes são pedidas como o pager faz (o formulário de novo, com
`hdnInfraPaginaAtual`). Nenhum portal mostrou a contagem de páginas do pager (`totalPages` nulo), então a leitura
segue o total da legenda ("(843 registros - 1 a 500)") e para quando ele é atingido, quando uma página volta vazia,
repetida ou de outra lista, em `maxPages` ou no orçamento de 60k caracteres.
`next` traz o `maxPages` que lê o resto, estimado pelo tamanho da página quando o pager não diz; quando a leitura
parou no orçamento não há `next` (um `maxPages` maior pararia no mesmo lugar), só a nota pedindo filtros mais estreitos.
Consultar `pagination.complete` e `totalItems` antes de considerar a lista completa.

**Data da intimação.** `intimationDateFrom`/`intimationDateTo` (`AAAA-MM-DD`) vão para "Data Início"/"Data Fim"
(`dataInicioField`/`dataFimField`, `DD/MM/AAAA`), no mesmo reenvio do formulário que `prazo` e
`includeArchived`; num formulário que só tenha "Data da Intimação" (`dataIntimacaoField`) cabe um dia só. Os quatro
tribunais capturados têm os três campos ao lado de "Prazo"; que Início/Fim filtram a data da intimação (e não a
do prazo) é leitura da tela, ainda não conferida com linhas reais.

O parser dedicado foi conferido com a captura de sete processos do TRF6 1G; outros
portais dependem do mesmo layout e do link no painel. Ausência do link, layout
desconhecido, sessão expirada e acesso negado são erros, não listas vazias confirmadas.
Hashes e links assinados não são expostos no resultado. A suíte Eproc contém 45 testes.

`parse_panel()` descobre as ações anunciadas; `require_route` permite somente as rotas
mapeadas. Novas telas continuam exigindo parser e inclusão explícita no allowlist.

## Consulta Processual (`eproc_search_processes`, 2026-11-27)

Busca no tribunal, não na carteira do advogado: devolve processos de terceiros (consulta pública), diferente de
`eproc_list_processes`. Um critério por chamada: `partyName` (pelo menos nome e sobrenome, como o portal exige),
`taxId` (CPF ou CNPJ, só dígitos e `. - /`, enviado formatado), `oab` (UF e até 6 dígitos, enviado como
`SC012345`, o formato da ajuda do portal) ou `originNumber` (originário/relacionado); `includeClosed` marca
"Exibir Baixados"; `maxPages` até 5.

O GET de `processo_consultar` traz o formulário `frmProcessoListaAjax`; cada opção de `tipoPesquisa` diz o
próprio valor e a rota AJAX que o Consultar chama (`data-acaoConsultaAssinada`): `processos_consulta_por_nome_parte`,
`..._documento_identificacao`, `..._oab`, `..._originario`. O valor e a rota são lidos do formulário; tipo ausente
no portal é `invalid_input`. Essas rotas ficam fora de `READ_ACTIONS`/`AJAX_ACTIONS`: só a busca, que as nomeia,
chega nelas, e um teste prova que rotas de escrita (movimentar, peticionar, distribuir, confirmar pauta) continuam
bloqueadas. O resultado é lido pelos `<th>` da tabela (número, classe, partes com polo, assunto, órgão, situação,
autuação em ISO; o resto em `extra`), com CPF mascarado; nada do que foi buscado vai para log ou erro.

A paginação da busca segue a da Relação sem depender de `totalPages`: com `maxPages` maior que 1 as páginas seguintes
são pedidas até o total da legenda, uma página vazia, uma página repetida ou de outra lista (nota
`search_page_N_mismatch`, nada misturado), o `maxPages` (nota e `next` estimado por `page_count`) ou o orçamento de 60k
caracteres (nota, sem `next`). `send` só aceita as rotas da Consulta quando quem chama as nomeia (`actions`); as
outras leituras continuam presas a `READ_ACTIONS`/`AJAX_ACTIONS`, e um teste prova que não chegam à busca.

**Precisa de validação ao vivo.** Não há captura de resposta da busca: o formato (HTML ou JSON com HTML), a
paginação (`hdnInfraPaginaAtual` com os campos `hdnInfra*` da resposta) e os cabeçalhos são inferidos da estrutura
do formulário. A página tem CAPTCHA (`divInfraCaptcha`); se o portal pedir, a tool responde `captcha_required`. Pela
ajuda dos tribunais, a busca por nome pode devolver primeiro uma lista de pessoas para escolher
(`numIdPessoa`): esse caso volta como `search_answer_without_processes`, ainda não atendido. "Pesquisa Fonética"
(`chkNomeParte`) não é exposta.

## Listas do menu (JURI-21, item 7; 27/11/2026)

Código em `src/jurisync/connectors/eproc/listas.py`. Cada tool pega o link que o menu do painel autenticado anuncia,
faz o mesmo GET (ou o POST do filtro da própria lista, como a página faz) e lê a tabela. Nenhuma abre linha, botão ou
formulário que age; cada ação lida entra explicitamente em `READ_ACTIONS`, e os testes provam que as ações de
abrir/escrever das mesmas telas continuam bloqueadas. Portal cujo menu não tem a ação responde o erro
`[not_available]` (o portal não oferece a lista; não é falha da leitura). Todas usam o envelope de lista
(`returned`, `total`, `next`) e datas ISO por `responses.dated` (o texto do portal em `<campo>Text` por um
ciclo, como no #34; nas colunas por cabeçalho, `<cabeçalho>Text`).

### Mensagens: `eproc_list_mensagens`

- Ação: `caixa_mensagem_listar` ("Caixa de Mensagens"). `status='unread'` (padrão) é o GET da página, que já abre no
  filtro "NÃO lidas"; `status='read'` posta o formulário `frmMensagemLista` com `rdoFiltro=lidas`, como o rádio da
  página faz. O resultado confere que o portal aplicou o filtro pedido.
- Itens `mensagens`: `sentAt` (ISO), `title`, `text` (o texto como a lista mostra, CPF mascarado); `total` vem da
  legenda "(N registros)". Uma página que não cabe no orçamento de 60k sai cortada, com
  `next = {status, page, offset}`.
- Ciência/estado: a mensagem só é aberta por `caixa_mensagem_vizualizar` (link do título, com `id_mensagem` e
  `status=naoLidas`), que nunca é requisitado e fica fora do allowlist. A listagem em si não marca como lida: no TRF4
  2G a visão "NÃO lidas" mostrava 24 mensagens, a mais antiga de 2012, ainda não lidas. É inferência das capturas;
  confirmar ao vivo que o contador não cai depois de uma leitura da lista.
- Capturas: TRF4 2G (24 linhas) e TJRJ (1) com tabela; TJSC 1G/2G e TRF6 sem tabela (nenhuma não lida), lido como
  lista vazia. Não há paginação nas capturas. Com paginador (`selInfraPaginacaoSuperior`), `page` lê a página pedida
  reenviando o formulário com `hdnInfraPaginaAtual`, como o paginador faz, e `next` aponta a próxima página, com uma
  `note`; o conector confere que o portal mostrou a página pedida. Sem paginador e com a legenda contando mais
  mensagens do que a página mostrou, a resposta traz uma `note` dizendo que o resto não foi lido.

### Paradas do sistema e suspensões de prazo: `eproc_list_paradas`

- Ação: `eprocsysadm_listar` ("Paradas do Sistema" no menu, "Relatório de Paradas" na página), um GET.
- `paradas`: `description` (Descrição Motivo Evento), `prorrogacao` (Houve Prorrogação de Prazo?: `true`/`false`,
  `null` se vier outro texto), `start`/`end` (ISO); `total` é o `hdnInfraNroItens` da página. `suspensoes`
  (tabela "Lista de suspensões cadastradas"): `date` (ISO) e `type` (Fechamento, Abertura).
- Os links de cada linha (`eprocsysadm_consultar`, `eprocsysadm_certidao`) não são seguidos e ficam fora do
  allowlist. Serve para conferir se o termo final de um prazo mudou; o conector não recalcula prazo.
- Capturas: TRF4 2G, TRF6 2G, TJSC 1G/2G com 19 a 78 paradas e 30 a 44 suspensões, tudo numa página; TJRJ sem
  tabela (lista vazia). Layout de colunas diferente é erro, não lista vazia.

### Petições preparadas: `eproc_list_peticoes_preparadas`

- Ações: `peticao_inicial_preparada_listar` (`kind='iniciais'`, "Petições Iniciais pendentes de distribuição",
  formulário `frmPeticaoInicial`) e `movimentacao_preparada_listar` (`kind='movimentacoes'`, "Movimentações
  pendentes", `frmMovimentacaoFutura`), um GET cada.
- Os botões Distribuir (`peticao_inicial_preparada_distribuir`) e Peticionar (`movimentacao_preparada_peticionar`)
  postam o mesmo formulário com outra ação; nenhum deles entra no allowlist, e os testes provam que ficam bloqueados.
- Itens `peticoes`: `processNumber` (o primeiro CNJ da linha, ou `null`) e `columns` (cada célula sob o texto do
  cabeçalho, datas ISO, CPF mascarado), sem as colunas de seleção e de ações. Uma linha com número de células
  diferente do cabeçalho vai com `columns: null` e as células sem rótulo em `cells`, e `notes` avisa.
- Lista vazia só quando a página diz: "Nenhum registro encontrado" (ou o mesmo para guia, petição, movimentação),
  legenda com 0 registros, ou a área da lista (`#divInfraAreaTabela`) desenhada vazia num formulário sem campo de
  filtro, que é como as cinco capturas vieram. Formulário sem tabela fora disso é `[portal_error]`
  `unsupported_peticoes_preparadas_layout` (ou `unsupported_custas_layout`). Toda resposta das duas listas traz em
  `notes` que a lista não foi validada ao vivo com linhas reais.
- **Falta validar com linhas.** As cinco capturas vieram vazias (formulário sem tabela), então o parser lê por
  `<th>` em vez de colunas fixas; confirmar os cabeçalhos num portal com rascunho e então fixar os campos.

### Guias de custas e depósitos: `eproc_list_custas`

- Ações: `guia_pagamento_custas_gerados_listar` (`kind='custas'`, menu Custas > "Guias geradas pelo usuário") e
  `guia_pagamento_depositos_judiciais_gerados_listar` (`kind='depositos'`, menu Depósitos Judiciais > "Guias geradas
  pelo usuário"), um GET cada. Só TRF4 e TRF6 têm esses itens no menu; nos demais a tool responde `[not_available]`
  sem requisitar nada. O TJRJ usa sistemas próprios (`tjrj_custas`, `tjrj_bb`, GRERJ), fora desta tool.
- Não lê "Guias geradas para um processo" (`processo_custas_consultar`) nem "Consultar/gerar guias"
  (`processo_depositos_judiciais_consultar`): são as telas que geram guia. Ficam fora do allowlist.
- Itens `guias`: `processNumber` e `columns` por cabeçalho, como nas preparadas.
- **Inferido, sem captura da página.** O explorador não abriu essas listas; o parser supõe o padrão das listas infra
  (formulário que posta para a própria ação, `table.infraTable`). Página sem esse formulário, ou que pede filtro e
  não mostra tabela, é erro, não lista vazia (regra de vazio das preparadas).
  Validar ao vivo no TRF4/TRF6, inclusive se a lista exige filtro (datas) antes de mostrar linhas.

## Features planejadas

### A. Filas/contadores extras (mesmo formato do `list_notices`)
- `citacao_intimacao_pendente_listar_urgente` (pendentes urgentes)
- `citacao_intimacao_pendente_listar_acordo` (pendentes de acordo)
- `citacao_art334_pendente_listar` (audiência conciliatória art. 334)
- `citacao_intimacao_decurso_prazo_listar` (decurso de prazo)
- `intimacao_de_pauta_listar` (intimação de pauta)

### B. Audiências (agenda cross-processo)
As seis listas abaixo estão no `eproc_list_audiencias` (seção "Audiências do advogado"); falta `conciliacao_listar`.
- `audiencia_futura_listar` — "Audiências Futuras (Designada, Redesignada, Prorrogada, Adiada, Antecipada)"
- `audiencia_futura_conciliacao_listar`
- `audiencia_realizada_listar`, `audiencia_nao_realizada_listar`
- `audiencia_realizada_conciliacao_listar`, `audiencia_nao_realizada_conciliacao_listar`
- `conciliacao_listar` (Processo, Data de Criação, Nome das Partes)

### C. Perícias (cross-processo)
- `pericia_futura_listar` → colunas: Processo, Periciado, Horário, Local, Perito, Especialidade

### D. "Preparadas" (rascunhos montados, ainda não protocolados)
- `peticao_inicial_preparada_listar` — "Petições Iniciais pendentes de distribuição"
- `movimentacao_preparada_listar` — "Movimentações pendentes"

### E. Mensagens/avisos
- `caixa_mensagem_listar` → Data/Hora, Título, Mensagem
- `novidades_sistema`

### F. Relatórios / busca de processos
- `relatorio_processo_procurador_listar` — "Relação de Processos" (Número, Classe, Autores,
  Réus, Localidade, Assunto, Último Evento, Datas, Valor da Causa): implementado acima.
- `processo_consultar` — "Consulta Processual" (filtros: `numNrProcesso`, `strNomeParte`,
  datas, apenso, mostrar baixados) + export Excel (`hdnExcel`)

### G. Autos do processo
- `arvore_documento_listar` — aba **"Árvore"** (árvore de documentos dos autos): implementado acima
- `download_documentos_evento` / `abrir_em_abas_documentos_evento` — baixar/abrir todos os
  documentos de um evento (lote)
- `selecionar_processos_agendar_arquivo_completo` — exportar/agendar o **arquivo completo
  (autos completos)**: escrita (agenda job no tribunal), não implementado

### H. Abas por-processo (novas seções no `eproc_get_process`)
- `audiencia_listar` (audiências do processo)
- `exame_listar` (Perícia)
- `processo_custas_listar` (Custas)
- `processo_parte_quesito_listar` (Quesitos da Parte Autora / Complementares)
- `certidao_narratoria_gerar` (Certidão Narratória)
- `processo_movimento_consultar` / `processo_movimento_filtrar`
- `historico_assunto_processo_listar`, `historico_classe_competencia_processo`,
  `historico_retificacao_partes_listar`
- `processo/listar_suspensoes_e_feriados`, `mandado_seguranca_tr`,
  `processo_medida_cautelar`, `processo_informa_lei14289`, `pesquisa_processo_doc_parte`

### I. Custas/depósitos
- `processo_custas_consultar`, `processo_depositos_judiciais_consultar`,
  `guia_pagamento_custas_gerados_listar`, `guia_pagamento_depositos_judiciais_gerados_listar`,
  `depositos_judiciais_advogado_listar`

### J. Catálogo/metadados (apoio, não são casos)
- `tipo_peticao_judicial_listar` (~142 tipos; Petição, Aplicabilidade, Preparo)
- `evento_peticao_tipo_documento_listar`, `competencia_assunto_judicial_listar`,
  `localidade_judicial_listar`

## Nota técnica

Telas usam a infra **DataTables** do eproc (estado em `hdnInfraItens`, `hdnInfraItensHash`,
`hdnInfraNroItens`, `hdnInfraSelecoes`, `selEventosJudiciaisSelecionados`, `sbmPlanilha`).
É o mesmo padrão do `montar_tabela_eventos_filtrados` que já tratamos (hash dinâmico + POST).
Não é stack novo.

## Encaixe proposto na estrutura

1. Generalizar `eproc_list_intimacoes(queue)` → todos os kinds cross-processo (filas +
   audiências + perícias + preparadas + mensagens + conciliações + relação de processos).
2. `eproc_get_process` → acrescentar seções por-processo (custas, exames/perícia, audiências,
   quesitos, certidão, árvore de documentos, autos completos).
3. Novo `eproc_catalog` → tabelas auxiliares (tipos de petição/evento, assuntos, localidades).

## Pendências de credencial

- Login JFSC validado com a senha **armazenada** (12 chars) em `customers/elisandra/.env`;
  a senha `Ricas321#` foi testada e é **inválida**.
- QR da Elisandra salvo em `customers/elisandra/qr-elisandra.png` — 3 perfis TOTP:
  `Eproc/TJRS`, `Eproc/TRF4` (JFSC/JFRS), `Eproc/TJSC`.
