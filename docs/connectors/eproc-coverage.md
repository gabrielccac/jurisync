# Eproc — cobertura atual e features planejadas

Levantamento feito ao vivo no **JFSC** (`trf4-1g-sc`, `eproc.jfsc.jus.br`) em 2026-10-14,
dumpando o painel autenticado e a tela de processo. Serve de backlog para ampliar o
connector `src/jurisync/connectors/eproc/` (read-only).

## Cobertura atual

- Tools: `eproc_list_notices` (filas: `open_deadlines`, `pending`) e
  `eproc_get_process` (eventos + documentos, até 5 docs, `save` opcional).
- Ações no allowlist (`READ_ACTIONS`/`AJAX_ACTIONS`): as 2 filas + `painel_adv_listar`,
  `processo_pesquisa_rapida`, `processo_selecionar`, `processo_consulta_listar`,
  `acessar_documento(_implementacao)`; ajax: `montar_tabela_eventos_filtrados`,
  `retornar_citacao_intimacao_estado`.
- Login: SSO Keycloak via browser headless (SeleniumBase) + TOTP.

## O gap (causa raiz)

`parse_panel()` (parsers.py) só extrai **2 links de fila** + pesquisa rápida + endpoint de
estado. Todo o resto que o painel anuncia (~65 ações) é descartado, e `require_route` é
allowlist rígida (qualquer outra `acao` → erro). É recorte deliberado — ampliar exige
descobrir as ações + parsers + allowlist.

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
  Réus, Localidade, Assunto, Último Evento, Datas, Valor da Causa)
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
