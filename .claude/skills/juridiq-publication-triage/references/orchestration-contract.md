---
name: juridiq-publication-triage-contract
description: Orquestracao deterministica e auditavel da triagem de publicacoes Juridiq, preservando todas as regras da skill legada e adicionando contratos explicitos para deduplicacao, enriquecimento PJe, idempotencia, eventos e estado de leitura.
---

# Triagem Juridiq - contrato de orquestracao v2

Leia integralmente o `../SKILL.md` antes de executar.
Esta versao nao remove nem substitui nenhuma regra da skill legada. Ela torna
explicitos os contratos que o agente deve produzir e os limites que o runner
deve impor. As regras de `classification-rules.md`, `task-models.md` e
`pje-enrichment.md` continuam normativas.

## Ordem obrigatoria

1. Busque publicacoes com `list_publication(isHandled=false, page, limit)` ate o fim.
2. Considere somente registros com `readAt=null` e `isHandled` diferente de true.
3. Normalize cada registro sem perder o objeto bruto.
4. Resolva `lawSuitId` ausente com `list_lawSuit(processNumber)` e aceite somente correspondencia exata.
5. Deduplicate em lote por ato, processo e data; escolha a versao com teor.
6. Avalie cada ato: `sufficient`, `no_action`, `needs_context` ou `review`.
7. Para `needs_context`, consulte o PJe somente para a informacao faltante.
8. Reavalie usando apenas evidencias citaveis.
9. Antes de cada criacao, consulte `list_task` filtrado pelo processo/lawsuit.
10. Crie, pule ou atualize uma tarefa conforme a providencia, nunca pelo titulo isolado.
11. Crie evento somente com data e hora confirmadas.
12. Marque como lidas somente as publicacoes concluidas, duplicadas ou de ciencia.
13. Deixe falhas, ambiguidade e falta de contexto nao resolvida como nao lidas.
14. Persista o resultado por publicacao e produza o placar final exigido pela skill legada.

## Contrato de publicacao normalizada

O agente deve receber este envelope, mantendo `raw` para auditoria:

```json
{
  "publicationId": "uuid",
  "lawSuitId": "uuid-or-null",
  "processNumber": "CNJ-or-null",
  "source": "DJEN-or-PJe-or-other",
  "court": "string-or-null",
  "availabilityDate": "YYYY-MM-DD-or-null",
  "text": "string",
  "author": "string-or-null",
  "representatives": [],
  "readAt": null,
  "isHandled": false,
  "raw": {}
}
```

Nao fabrique `publicationId`, processo, teor, data ou correspondencia com PJe.

## Contrato de deduplicacao

Chame `agent.deduplicate({ publications })` uma vez por lote. A resposta deve
cobrir todos os IDs exatamente uma vez:

```json
{
  "groups": [
    {
      "canonicalActKey": "process|date|act-or-providence|recipient",
      "publicationIds": ["pub-1", "pub-2"],
      "primaryPublicationId": "pub-1",
      "duplicatePublicationIds": ["pub-2"],
      "status": "process",
      "reason": "Mesmo processo, ato, destinatario e data; pub-1 tem teor.",
      "evidence": [
        {"publicationId": "pub-1", "quote": "trecho exato"},
        {"publicationId": "pub-2", "quote": "trecho exato"}
      ]
    }
  ]
}
```

Use `status=review` quando a equivalencia nao for segura. Nao agrupe apenas
por processo, titulo ou data. A duplicata nao e classificada nem gera tarefa;
ela pode ser marcada lida somente quando o grupo primario for concluido.

## Contrato de avaliacao

```json
{
  "publicationId": "pub-1",
  "status": "sufficient|no_action|needs_context|review",
  "reason": "explicacao curta",
  "evidence": [{"sourceId": "publication:pub-1", "quote": "trecho exato"}],
  "missingInformation": ["data da pericia"]
}
```

`sufficient` e `no_action` exigem teor/evidencia e lista de faltas vazia.
`needs_context` deve nomear a informacao concreta a buscar no PJe. `review`
fica nao lida e nao cria tarefa.

## Contrato de enriquecimento PJe

O runner chama `pje_get_process(processNumber, portal, scope, documentIds)` com
o processo, a data e o ato como âncoras. A ferramenta retorna o snapshot
completo de processo, partes, movimentos e inventario de documentos numa
primeira chamada sem `documentIds`; o texto extraido de documentos especificos
so vem numa chamada seguinte informando `documentIds` (ate 5 por chamada). Nao
existe um endpoint confirmado de "movimentos vizinhos" — a selecao do
documento certo dentro do mesmo cluster de movimento (ver `pje-enrichment.md`)
e responsabilidade do agente. O agente deve selecionar somente evidencias
relacionadas:

```json
{
  "status": "sufficient|insufficient|ambiguous|not_found",
  "selectedEvidence": [
    {
      "source": "movement|document|process",
      "sourceId": "movement-or-document-id",
      "date": "YYYY-MM-DD-or-null",
      "title": "string-or-null",
      "text": "texto retornado",
      "relation": "same_act|preceding|following|supports",
      "confidence": 0.91,
      "quote": "trecho exato"
    }
  ],
  "missingInformation": [],
  "reason": "como a evidencia resolve a lacuna"
}
```

Priorize o mesmo dia, depois movimentos imediatamente anteriores/posteriores,
e confirme processo, ato, destinatario, partes, datas e documentos referidos.
Se houver mais de uma correspondencia plausivel, retorne `ambiguous` e nao
invente uma escolha. Se o documento for PDF e nao houver texto extraido, a
publicacao permanece pendente; nao trate metadado de anexo como teor.

## Contrato de roteamento e tarefa

O agente deve aplicar `classification-rules.md` e retornar:

```json
{
  "publicationId": "pub-1",
  "status": "sufficient|no_action",
  "classificationRule": "regra exata usada da classificacao",
  "evidence": [{"sourceId": "movement:m-1", "quote": "trecho exato"}],
  "tasks": [
    {
      "action": "create|update",
      "type": "manifestacao",
      "obligationKey": "processo|providencia|destinatario",
      "existingTaskId": "uuid-or-omitted",
      "clientName": "nome para o titulo",
      "description": "descricao executavel e fundamentada",
      "initialDate": "YYYY-MM-DD",
      "finalDate": "YYYY-MM-DD-or-omitted",
      "personIds": ["uuid"]
    }
  ],
  "events": []
}
```

`obligationKey` identifica a providencia e nao pode ser derivado apenas do
titulo. O runner consulta tarefas da mesma lawsuit antes da criacao. Tarefa
manual ou criada por execucao anterior tambem conta.

Para uma tarefa provisoria, a rota final deve usar `action=update` e o mesmo
`existingTaskId`. Nunca crie uma segunda tarefa e nunca delete a provisoria.

## Contrato de eventos

Use `juridiq_list_events` filtrando por `lawSuitId`, periodo e tipo para fazer a
checagem direta de duplicidade. Use `juridiq_get_event` para confirmar um
candidato especifico. Estado local e tarefas relacionadas continuam sendo
evidencia complementar quando os filtros nao forem suficientes:

```json
{
  "eventKey": "process|tipo|date|initialHour",
  "action": "create|skip_existing",
  "reason": "data e hora confirmadas; ou justificativa da conferencia indireta",
  "evidence": [{"sourceId": "movement:m-1", "quote": "trecho exato"}],
  "payload": {
    "title": "Pericia Medica (Representante) - Justica Federal (UF)",
    "date": "YYYY-MM-DD",
    "initialHour": "HH:MM",
    "finalHour": "HH:MM",
    "responsibleIds": ["uuid"],
    "lawSuitId": "uuid",
    "personId": "uuid"
  }
}
```

Sem data e hora confirmadas, nao crie evento. A resposta deve informar se a
checagem foi direta (`juridiq_list_events`/`juridiq_get_event`) ou apenas
complementar.

## Estado e leitura

O estado local e mecanismo de retomada, nao substituto do `readAt` do Juridiq:

```json
{
  "publications": {
    "pub-1": {
      "status": "pending_read|completed|review|error",
      "outcome": "processed|no_action|duplicate|existing_task",
      "taskIds": [],
      "markedRead": false,
      "reason": "string",
      "updatedAt": "ISO-8601"
    }
  },
  "events": {}
}
```

O fluxo de mutacao e:

```text
create/update task
→ create event if required
→ save pending_read
→ PATCH /publication/read-movements-many {"ids":[...]}
→ save completed + markedRead=true
```

Use lotes de no maximo 50 IDs. Se o PATCH falhar, nao marque como concluido;
deixe `pending_read`/`readPending` para nova tentativa. Falha, sigilo,
processo nao encontrado sem teor suficiente e qualquer duvida real permanecem
nao lidos.

## Limites de ferramentas

- `juridiq_list_events`/`juridiq_get_event` fazem a verificacao direta de eventos;
  nao declare verificacao direta se a consulta falhar.
- `pje_get_process` fornece movimentos/documentos, mas nao promete um
  movimento vizinho automaticamente; a selecao e uma decisao do agente
  baseada no snapshot retornado (ver a regra de cluster de documentos em
  `pje-enrichment.md`).
- Extracao de PDF nao e garantida pelo adapter atual. Se for necessaria, deve
  ser uma etapa explicita que retorna texto, paginas e status de OCR.
- O texto extraido por `pje_get_process` pode vir com acentuacao corrompida
  (encoding). Ate confirmacao de correcao, nao usar esse texto verbatim em
  descricao de tarefa ou resumo sem normalizar ou sinalizar a limitacao (ver
  aviso conhecido em `pje-enrichment.md`).
- O account de leitura e escrita deve ser o mesmo nesta integracao, pois IDs
  Juridiq sao escopados por conta. Escritas de teste usam somente a conta de
  teste autorizada.

## Resumo obrigatorio

Retorne em portugues: publicacoes buscadas/processadas, tarefas criadas,
atualizadas, existentes e duplicadas, eventos criados/pulados, urgencias,
pendencias PJe, erros e o placar de leitura com cada ID/processo/motivo que
permaneceu nao lido.
