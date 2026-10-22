# Eproc — cobertura atual e features planejadas

Levantamento feito ao vivo no **JFSC** (`trf4-1g-sc`, `eproc.jfsc.jus.br`) em 2026-10-14,
dumpando o painel autenticado e a tela de processo. Serve de backlog para ampliar o
connector `src/jurisync/connectors/eproc/` (read-only).

## Cobertura atual

- Tools: `eproc_list_notices` (filas listadas em `QUEUE_KINDS`),
  `eproc_list_processes` (Relação de Processos do advogado) e
  `eproc_get_process` (eventos + documentos, até 5 docs, `save` opcional).
- Ações no allowlist (`READ_ACTIONS`/`AJAX_ACTIONS`): as 2 filas + `painel_adv_listar`,
  `processo_pesquisa_rapida`, `processo_selecionar`, `processo_consulta_listar`,
  `relatorio_processo_procurador_listar`,
  `acessar_documento(_implementacao)`; ajax: `montar_tabela_eventos_filtrados`,
  `retornar_citacao_intimacao_estado`.
- Login: SSO Keycloak via browser headless (SeleniumBase) + TOTP.

## Relação de Processos (implementado em 2026-10-21)

Chamada MCP: `eproc_list_processes({"portal": "trf6-1g"})`.
CLI: `python -m jurisync.connectors.eproc.client processes --portal trf6-1g`.

Usa a sessão existente e o link assinado anunciado pelo painel, com um GET ao relatório.
Retorna número CNJ, juízo, classe, partes, localidade, assunto, último evento e sua data,
autuação e valor da causa. Os números podem ser usados em `eproc_get_process` para
buscar movimentos e textos dos documentos.

O escopo é o relatório do advogado autenticado, com os filtros padrão do portal,
não uma busca irrestrita de todos os processos do tribunal. No TRF6 observado:
situação Todos e baixados excluídos. `filters` informa esses valores quando disponíveis.
Lê somente a página retornada; consultar `pagination.complete` e `totalItems` antes de
considerar a lista completa. Não percorre páginas nem altera filtros automaticamente.

O parser dedicado foi conferido com a captura de sete processos do TRF6 1G; outros
portais dependem do mesmo layout e do link no painel. Ausência do link, layout
desconhecido, sessão expirada e acesso negado são erros, não listas vazias confirmadas.
Hashes e links assinados não são expostos no resultado. A suíte Eproc contém 45 testes.

`parse_panel()` descobre as ações anunciadas; `require_route` permite somente as rotas
mapeadas. Novas telas continuam exigindo parser e inclusão explícita no allowlist.

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

1. Generalizar `eproc_list_notices(queue)` → todos os kinds cross-processo (filas +
   audiências + perícias + preparadas + mensagens + conciliações + relação de processos).
2. `eproc_get_process` → acrescentar seções por-processo (custas, exames/perícia, audiências,
   quesitos, certidão, árvore de documentos, autos completos).
3. Novo `eproc_catalog` → tabelas auxiliares (tipos de petição/evento, assuntos, localidades).

## Pendências de credencial

- Login JFSC validado com a senha **armazenada** (12 chars) em `customers/elisandra/.env`;
  a senha `Ricas321#` foi testada e é **inválida**.
- QR da Elisandra salvo em `customers/elisandra/qr-elisandra.png` — 3 perfis TOTP:
  `Eproc/TJRS`, `Eproc/TRF4` (JFSC/JFRS), `Eproc/TJSC`.
