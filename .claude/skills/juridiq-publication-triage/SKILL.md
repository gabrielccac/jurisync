---
name: juridiq-publication-triage
description: Processar publicacoes Juridiq com classificacao, deduplicacao, enriquecimento PJe e tarefas/eventos. Use quando o usuario pedir para verificar, mapear ou transformar novas publicacoes em tarefas.
---

# Triagem de publicacoes Juridiq

Use as ferramentas do MCP `juridiq-pipeline` para obter dados. Nao invente
publicacoes, movimentos, prazos, tarefas ou eventos.

Antes da primeira execucao, leia integralmente:

- `references/core-rules.md` (regras legadas);
- `references/orchestration-contract.md` (contrato de orquestracao);
- `references/classification-rules.md`;
- `references/task-models.md`;
- `references/pje-enrichment.md`.

Fluxo:

1. Use `juridiq_list_pending_publications` for the production triage. Use
   `juridiq_list_publications` with `isHandled=true` or an explicit date/order
   filter only when the user asks for historical records; they include already
   handled publications and must not be reprocessed as new work. Interpret
   returned dates semantically when the user asks for the most recent records;
   there is no local `recentOnly` flag.
   Use `juridiq_list_lawsuits` when the user asks for a general process list;
   use `juridiq_find_lawsuit` only for a known process number.
2. Normalize e deduplicate por ato/processo/data; prefira a versao com teor.
3. Resolva processo com `juridiq_find_lawsuit` quando faltar `lawSuitId`.
4. Avalie teor e necessidade de contexto.
5. Quando necessario, use `pje_get_process`, priorizando o processo, a data e
   os movimentos imediatamente relacionados. Movimento retornado pelo PJe nao
   e automaticamente uma publicacao.
6. Use `juridiq_list_tasks` antes de qualquer criacao. Compare providencia
   semantica para o mesmo lawsuit; titulo isolado nao prova duplicidade.
7. Use `juridiq_list_events` by lawsuit/date before creating a calendar event;
   use `juridiq_get_event` when a candidate needs confirmation. Use os modelos
   vivos de `juridiq_list_task_templates` e siga a classificacao.
8. Em producao, nao use as ferramentas de escrita `_test`. Nesta configuracao,
   as ferramentas de escrita sao deliberadamente limitadas a conta de teste.
9. So use `juridiq_mark_publications_read_test` depois de concluir a tarefa;
   falha ou duvida permanece nao lida.
10. Informe no resumo o que foi criado, atualizado, existente, duplicado,
    pendente no PJe, nao lido e por qual motivo.

As credenciais sao carregadas pelo processo MCP a partir do `.env`; nunca
solicite que o usuario cole senha ou token no prompt e nunca exponha token nos
resultados.
