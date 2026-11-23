# Camada de serviço do servidor remoto: cache, sincronização, medição e relatórios (desenho, 16/11/2026)

> Desenho para depois; ver roadmap.md.

Base: o mapa do código de 16/11 (`hosted/`, conectores, `docs/performance.md`,
`docs/archive/architecture-review-2026-11-10.md`) e a pesquisa de 16/11 sobre servidores MCP para agentes (fontes no
fim). Substituiu as fases C–E de `docs/archive/rollout-plan.md` e detalha `docs/archive/sync-plan.md`.

## 1. Problema medido

| Situação em 16/11 | Custo |
|---|---|
| `pje_get_process` → `pje_read_documents` → `pje_read_expediente_ato` no mesmo processo | 6–8 requisições cada (23/11: a busca fica 180 s na sessão, 1–2 requisições; a aba Expedientes é sempre relida) |
| `pje_list_inbox` em todas as regiões | ~43 requisições; contadores com `regions=true`: 62 |
| `djen_search_saved` | N buscas × 7 variantes de OAB (buscas já em paralelo) |
| `eproc_get_process` com documentos | relia as 4 filas pendentes a cada chamada (23/11: guardadas 120 s por sessão) |
| e-SAJ / Projudi | refazem inventário ou reabrem o processo por documento |
| Dado de processo, expediente, referência ADVBOX | nenhum cache; toda pergunta vai ao portal |
| Registro por chamada (`store.calls`) | só ferramenta, usuário, ok/erro, duração; não conta requisições, logins, processos, bytes, cache |
| Ciência no Eproc | documentos abrem sem trava; o bloco `ciencia` informa (decidido: informar, não travar) |

## 2. Arquitetura

```
Claude / ChatGPT / rotinas ──► /mcp (OAuth, TenantGate)            API HTTP (relatórios, painel)
                                   │                                     │
                         ferramentas finas (adaptadores MCP)             │
                                   │                                     │
                     ┌──────── serviços por sistema ─────────────────────┘
                     │   Listing (sem ciência) · Opening (pode dar ciência) · Writes
                     │   cache de leitura (TTL + stale-while-revalidate) · single-flight
                     │   orçamento por escritório (token bucket) · circuit breaker por portal
                     │   medição (UsageMeter) em toda requisição ao portal
                     │
          read model por escritório (SQLite)  ◄── sincronizador (jobs por escritório×portal, só Listing)
                     │
                 conectores (login, HTML/JSON, parsers) ── pool de sessões (já existe)
```

Um processo só (como hoje), sem Redis/Celery. O SQLite por escritório fica atrás de um repositório para
trocar por Postgres quando houver mais de um servidor.

## 3. Read model e sincronização ("o cliente não precisa chamar o portal")

O que o sincronizador mantém, por escritório (só serviço Listing, nunca abre ato nem documento):

| Dado | Fonte | Frequência | Regra |
|---|---|---|---|
| Comunicações | DJEN (OABs do escritório) | de hora em hora, janela 7 dias | dedupe por id |
| Contadores da caixa | PJe, PJe-JT, Eproc, e-SAJ, Projudi | 3×/dia (7h, 12h, 17h) + antes de cada rotina | 1 requisição por portal |
| Itens da caixa | mesmos | só quando o contador mudou | lista do bucket alterado |
| Processos ativos (capa, partes, movimentações, expedientes) | portal do processo | quando aparece comunicação/expediente novo; diário enquanto houver prazo aberto | `scope=latest` |
| Push / acompanhados | PJe | diário | diff da lista |
| Referência ADVBOX/Juridiq (membros, workflows, colunas) | API | cache 1 h | sem sync de tarefas |

"Processos que o escritório checa diariamente" = processos ativos: com comunicação, expediente ou prazo nos
últimos 90 dias, mais os que o escritório consultou nos últimos 30 dias (vem da medição, §6).

Cada leitura das ferramentas responde do read model com `asOf` e `stale` e aceita `maxAgeMinutes` (padrão por
ferramenta; `0` = ao vivo). Se o dado está velho, devolve o que tem e agenda a atualização
(stale-while-revalidate, RFC 5861); se o portal está fora, devolve o último dado com o aviso (stale-if-error).

Mudanças viram eventos (`change_events`: nova intimação, novo expediente, prazo em N dias) que alimentam as
rotinas e uma ferramenta `jurisync_whats_new(since)` — o agente pergunta "o que mudou" em vez de varrer portais.

## 4. Cache das leituras ao vivo

| Chave (sempre com o escritório) | TTL | Invalida |
|---|---|---|
| processo PJe: id + chave de acesso + detalhe | 15 min | abertura de ato/documento do processo |
| aba Expedientes do processo (gate de ciência) | 2 min | qualquer abertura |
| filas pendentes do Eproc (gate) | 2 min | qualquer abertura |
| inventário de documentos e-SAJ / índice Projudi | 15 min | — |
| contadores e caixa | servidos do read model | sync ou `maxAgeMinutes=0` |
| DJEN busca | 15 min | sync horário |
| texto de documento | só depois da ciência ou de documento sem expediente | nunca antes |

Single-flight por (escritório, portal, chave): dois advogados pedindo o mesmo processo ao mesmo tempo geram
uma leitura. Meta medida: `get_process` seguido de `read_documents` no mesmo processo ≤ 2 requisições
(em 16/11: 13–14).

## 5. Ciência por construção

- Serviço **Listing** (sincronizador, contadores, listas, capa, movimentações) não tem caminho para
  `fetch_ato`, `read_documents` do PJe/PJe-JT, documentos do Eproc, abertura no e-SAJ/Projudi. Teste que
  importa o módulo do sincronizador e falha se esses símbolos forem alcançáveis.
- Serviço **Opening** devolve sempre o bloco `ciencia` (`core/ciencia.py`). PJe, Projudi e JPe pedem
  `acknowledgeCiencia=true` para um ato pendente; o Eproc abre e informa (decisão: o usuário é informado, não
  travado).
- Anotações: ferramentas de abertura com `readOnlyHint=false`, `destructiveHint=true` (hoje
  `esaj_read_document` e `projudi_read_documents` estão como só-leitura). A trava é no servidor; a anotação
  só informa o cliente.
- Cada ciência registrada vira evento de medição (`ciencia=1`), visível no relatório.

## 6. Medição (o que o escritório consome)

Tabela `usage_events` (store central, append-only, 13 meses):

| Campo | Exemplo |
|---|---|
| at, tenant, user, client (claude/chatgpt/rotina) | |
| tool, connector, portal | `pje_get_process`, `pje`, `trf5-1g` |
| outcome, error_type, duration_ms | |
| source: `cache` / `read_model` / `live` | |
| portal_requests, logins, bytes_down | 6, 0, 84 000 |
| process (CNJ, hash para relatórios externos), documents_opened, ciencia | |
| tokens_out (tamanho da resposta) | |

Como medir sem tocar cada conector: um `UsageMeter` em `contextvars` aberto pelo `Office.call_tool`; o
`log_request` de `core/http.py` (já chamado em toda requisição) e o pool de sessões (`login`) incrementam o
medidor; o adaptador grava uma linha ao fim. Os jobs do sincronizador gravam com `client=sync`.

Observabilidade: spans OpenTelemetry com as convenções MCP (`mcp.method.name`, `gen_ai.tool.name`,
`error.type`, histograma `mcp.server.operation.duration`) + atributos próprios (`jurisync.tenant`,
`jurisync.portal`, `jurisync.cache_hit`, `jurisync.portal_requests`). Nunca argumentos nem resultados.

Cobrança (quando houver): agregado diário por escritório enviado ao Stripe Meters ou OpenMeter com
`identifier` idempotente; sem número de processo como dimensão.

## 7. Relatórios para o escritório

API HTTP (mesmo OAuth, papel `admin` do escritório) + página simples, e uma ferramenta `jurisync_usage_report`:

- **Uso**: chamadas por dia, por advogado, por ferramenta; quanto saiu do cache vs portal.
- **Carteira**: processos ativos monitorados, consultados no período, novos expedientes/intimações, prazos
  abertos.
- **Ciência**: quais atos foram abertos, por quem, quando (controle interno).
- **Saúde dos acessos**: último login bem-sucedido por portal, senha/A1 a vencer, portais indisponíveis.
- **Sincronização**: última atualização por portal, falhas.
Exporta CSV/PDF; envio semanal por e-mail opcional.

## 8. Proteções

- Orçamento por escritório: token bucket de chamadas ao vivo por minuto e de requisições ao portal por hora;
  limite de concorrência (já há semáforo de logins). Excedeu → resposta do read model com aviso, não erro.
- Circuit breaker por portal (fechado/aberto/meio-aberto): portal fora responde rápido com o último dado e
  "indisponível desde HH:MM".
- Login: continua o pool (um login por escritório×portal, cool-down de 15 min após recusa).

## 9. Superfície das ferramentas (menos tokens, menos chamadas)

- Formato de lista único `{items, total, page, hasMore, asOf, stale}`; `responseFormat: concise|detailed`
  (concisa por padrão, ~1/3 dos tokens); resposta padrão < 10 mil tokens; textos com `textLimit`.
- `outputSchema` tipado por ferramenta (hoje é objeto genérico) e erros acionáveis (`isError` com o que fazer).
- Ferramentas de fluxo, sobre o read model: `jurisync_whats_new`, `jurisync_agenda` (prazos e audiências de
  todos os sistemas), `jurisync_process_overview(cnj)` (junta DJEN + portal + ADVBOX). As por-sistema ficam
  para quando o agente precisa ir fundo.
- Lista de ferramentas estável e em ordem determinística (preserva o cache de prompt do cliente); `title` e
  anotações em todas (exigência dos diretórios da Anthropic e da OpenAI).
- Transporte: `stateless_http` + `json_response` no SDK atual; estado no store, não na sessão MCP (prepara a
  especificação 2026-07-28, que é stateless).
- Downloads grandes: `resource_link` em vez do conteúdo; Tasks MCP depois que Claude/ChatGPT suportarem.

## 10. Ordem de implementação

| Etapa | Entrega | Prova |
|---|---|---|
| 1. Medição | `UsageMeter` + `usage_events` + relatório de uso básico | uma chamada de cada sistema gera linha com requisições e logins corretos |
| 2. Ciência | Listing/Opening separados; anotações corrigidas | teste de alcance do sincronizador |
| 3. Cache ao vivo + single-flight | TTLs do §4 em PJe, Eproc, e-SAJ, Projudi | `get_process`+`read_documents` ≤ 2 requisições; medido no `usage_events` |
| 4. Read model + sync | store por escritório; jobs DJEN, contadores, caixa, processos ativos; `asOf/stale/maxAgeMinutes` | `pje_list_inbox` responde < 1 s do store; requisições/dia por escritório caem (comparar etapa 1) |
| 5. Ferramentas de fluxo e relatórios | `whats_new`, `agenda`, `process_overview`, `usage_report`, página do escritório | rotina diária do Victor usa só ferramentas de fluxo |
| 6. Proteções e OTel | token bucket, circuit breaker, spans | portal derrubado responde do store em < 1 s |

A etapa 1 vem primeiro porque é a régua: mede o antes e prova o ganho de cada etapa seguinte.

## Fontes

- Anthropic, Writing effective tools for agents — https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, Code execution with MCP — https://www.anthropic.com/engineering/code-execution-with-mcp
- Anthropic, Advanced tool use — https://www.anthropic.com/engineering/advanced-tool-use
- MCP 2025-11-25 (tools, resources, tasks) — https://modelcontextprotocol.io/specification/2025-11-25/changelog
- MCP 2026-07-28 (stateless, ttlMs/cacheScope, Mcp-Name) — https://blog.modelcontextprotocol.io/posts/2026-07-28/
- Claude prompt caching — https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Claude Code MCP (limites de saída) — https://code.claude.com/docs/en/mcp
- OpenAI Apps SDK reference — https://developers.openai.com/apps-sdk/reference
- RFC 5861 (stale-while-revalidate / stale-if-error) — https://www.rfc-editor.org/rfc/rfc5861
- Stripe, rate limiters — https://stripe.com/blog/rate-limiters ; usage recording — https://docs.stripe.com/billing/subscriptions/usage-based/recording-usage-api
- Fowler, CircuitBreaker — https://martinfowler.com/bliki/CircuitBreaker.html
- OpenTelemetry MCP semantic conventions — https://github.com/open-telemetry/semantic-conventions-genai (docs/gen-ai/mcp.md)
- FastMCP ResponseCachingMiddleware — https://gofastmcp.com/python-sdk/fastmcp-server-middleware-caching
- GitHub MCP server (toolsets, read-only) — https://github.com/github/github-mcp-server
