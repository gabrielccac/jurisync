---
name: juridiq-publication-triage
description: Processar publicacoes Juridiq com classificacao, deduplicacao, enriquecimento PJe e tarefas/eventos. Use quando o usuario pedir para verificar, mapear ou transformar novas publicacoes em tarefas.
---

# Triagem de publicacoes Juridiq

Orquestre os conectores MCP `juridiq` e, quando o teor estiver incompleto,
`pje`. Nao invente publicacoes, movimentos, prazos, tarefas ou eventos.

Leia quando aplicavel:

- `references/classification-rules.md`;
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
7. Use `juridiq_list_events` por lawsuit/data antes de criar um evento;
   use `juridiq_get_event` para confirmar um candidato. Resolva modelos,
   usuarios, colunas e tags pelas ferramentas de leitura ao vivo, nunca por IDs
   copiados de outro cliente.
8. As ferramentas de escrita Juridiq existem somente quando o administrador
   iniciou o conector com `JURIDIQ_ALLOW_WRITES=true`. A ausencia delas significa
   modo somente leitura; informe os rascunhos sem tentar contornar esse limite.
9. So use `juridiq_mark_publications_read` depois de concluir a tarefa; falha,
   falta de ferramenta de escrita ou duvida permanece nao lida.
10. Toda evidencia deve citar um `sourceId` conhecido e um trecho exato dessa
    mesma fonte. Conflito ou ambiguidade exige revisao humana.
11. Informe no resumo o que foi criado, atualizado, existente, duplicado,
    pendente no PJe, nao lido e por qual motivo.

Cada conector recebe sua propria conta no processo MCP. Nunca solicite que o
usuario cole senha ou token no prompt e nunca exponha credenciais nos resultados.
