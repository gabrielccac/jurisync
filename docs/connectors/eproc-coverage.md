# Eproc — cobertura atual e features planejadas

Levantamento feito ao vivo no **JFSC** (`trf4-1g-sc`, `eproc.jfsc.jus.br`) em 2026-10-14,
dumpando o painel autenticado e a tela de processo. Serve de backlog para ampliar o
connector `src/jurisync/connectors/eproc/` (read-only).

## Cobertura atual

- Tools: `eproc_list_intimacoes` (filas listadas em `QUEUE_KINDS`; sem fila, pendentes e prazos abertos juntos,
  cada item com `situacao`/`prazoFinal` de `core.intimacao`), `eproc_get_counters` (contadores do painel e de cada
  instância), `eproc_list_audiencias` (audiências futuras e de conciliação), `eproc_list_processes` (Relação de
  Processos do advogado; baixados ficam de fora em todo portal salvo `includeArchived`, filtros `prazo` e
  `intimationDateFrom`/`intimationDateTo`), `eproc_search_processes` (Consulta Processual do tribunal) e
  `eproc_get_process` (eventos, `prazos` de cada intimação, informações adicionais, audiências e perícias,
  documentos até 5, `save` opcional). Formatos conferidos no TJMG e na JFRS em 27/11/2026.
- As duas tools de lista aceitam `processNumber` (filtro aplicado aqui sobre as linhas lidas: nenhuma das duas listas
  do portal tem campo de número; com o filtro, `total` do envelope é o achado quando a lista foi lida inteira, senão
  `null`); todo item traz `processNumber` ao lado de `number`, que fica por um ciclo.
- Ações no allowlist (`READ_ACTIONS`/`AJAX_ACTIONS`): as 2 filas + `painel_adv_listar`,
  `processo_pesquisa_rapida`, `processo_selecionar`, `processo_consulta_listar`,
  `relatorio_processo_procurador_listar`,
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
`next` traz o `maxPages` que lê o resto, estimado pelo tamanho da página quando o pager não diz.
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

**Precisa de validação ao vivo.** Não há captura de resposta da busca: o formato (HTML ou JSON com HTML), a
paginação (`hdnInfraPaginaAtual` com os campos `hdnInfra*` da resposta) e os cabeçalhos são inferidos da estrutura
do formulário. A página tem CAPTCHA (`divInfraCaptcha`); se o portal pedir, a tool responde `captcha_required`. Pela
ajuda dos tribunais, a busca por nome pode devolver primeiro uma lista de pessoas para escolher
(`numIdPessoa`): esse caso volta como `search_answer_without_processes`, ainda não atendido. "Pesquisa Fonética"
(`chkNomeParte`) não é exposta.

## Features planejadas

### A. Filas/contadores extras (mesmo formato do `list_notices`)
- `citacao_intimacao_pendente_listar_urgente` (pendentes urgentes)
- `citacao_intimacao_pendente_listar_acordo` (pendentes de acordo)
- `citacao_art334_pendente_listar` (audiência conciliatória art. 334)
- `citacao_intimacao_decurso_prazo_listar` (decurso de prazo)
- `intimacao_de_pauta_listar` (intimação de pauta)

### B. Audiências (agenda cross-processo)
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
- `arvore_documento_listar` — aba **"Árvore"** (árvore de documentos dos autos)
- `download_documentos_evento` / `abrir_em_abas_documentos_evento` — baixar/abrir todos os
  documentos de um evento (lote)
- `selecionar_processos_agendar_arquivo_completo` — exportar/agendar o **arquivo completo
  (autos completos)**

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
