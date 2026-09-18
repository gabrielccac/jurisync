---
name: juridiq-publication-triage-core
description: Executa a triagem das publicações judiciais do escritório Jean Ferreira - Advogados (OAB AP-5849) no Juridiq — lê as publicações NÃO LIDAS (DJEN/PJe, TRF1/TRF4/TRF5 e justiça estadual), deduplica atos repetidos entre diários, classifica cada intimação (contestação, laudo pericial, acordo, designação de perícia, sentença, acórdão, RPV, despacho com prazo), cria as tarefas correspondentes pelos modelos já cadastrados no Juridiq sem duplicar, lança eventos de perícia na agenda e marca como lidas apenas as publicações efetivamente tratadas. Use esta skill sempre que o usuário pedir para "rodar a triagem das publicações", "processar as publicações do Juridiq", "verificar as intimações de hoje", "transformar as publicações em tarefas", "rodar a rotina das publicações", "ver o que caiu no diário", ou qualquer variação de processar publicações/intimações judiciais e lançar tarefas no Juridiq — mesmo sem citar as palavras exatas, e mesmo quando o pedido vier de uma tarefa agendada.
---

# Publicações Juridiq → Tarefas judiciais

Rotina de triagem das publicações judiciais do escritório Jean Ferreira - Advogados (Jean Ferreira, OAB AP-5849), que atua principalmente em **BPC-LOAS contra o INSS na Justiça Federal** (TRF1/TRF5/TRF4) e, em menor volume, em plano de saúde e cíveis estaduais.

Execute o fluxo inteiro e responda **em português**. Obtenha a data de hoje com `bash` — não presuma.

O objetivo final é simples de enunciar e difícil de acertar: **nenhuma intimação com prazo sem tarefa, nenhuma tarefa duplicada, e o estado de leitura refletindo exatamente o que foi tratado.** Tudo nesta skill existe para servir a esses três pontos.

## Princípios invioláveis

Estes são os pontos onde um erro custa caro — prazo perdido, cliente sem resposta, ou uma publicação que some sem ninguém ter lido.

- **Marcar como lida é irreversível por aqui.** O conector não tem operação para desfazer. Na dúvida real sobre a providência, **deixe não lida** e explique o motivo no resumo. Uma publicação a mais para conferência manual custa cinco minutos; uma perdida pode custar um prazo.
- **Nunca duplicar tarefa.** Antes de criar qualquer tarefa, verifique com `list_task` (filtro `lawSuit` = lawSuitId) se já existe tarefa equivalente para aquele processo e aquela providência — inclusive criada por uma execução anterior ou manualmente pela equipe. Se existir, não crie; registre no resumo como já existente.
- **Prazo é do ato, não do modelo.** Quando a intimação informa Data Limite ou prazo em dias, é esse prazo que manda (em dias úteis quando o ato assim indicar). O `daysForDue` do modelo é só o padrão para quando o ato não diz nada.
- **Só ciência também é trabalho concluído.** Intimação dirigida a outra parte, ata de distribuição, pauta de julgamento, republicação, processo em que o escritório está no polo passivo: nada a fazer, marca lida, registra no resumo.
- Chamadas de listagem do Juridiq devem sempre incluir `page` e `limit` para evitar erro da API. Retornos grandes vão para arquivo — processe com python em vez de tentar ler tudo no contexto.

## Ferramentas

Conector Juridiq (`mcp__Juridiq__*`; carregue via ToolSearch se necessário):
`list_publication`, `list_task_task_templates`, `list_task`, `list_lawSuit`, `get_lawSuit_movements_by_lawSuitId`, `create_task`, `create_event`, `update_publication_read_movements_many` e — para reclassificar tarefas provisórias depois da consulta ao PJe — `update_task_by_id` e `delete_task`.

**`juridiq_list_events` permite checar diretamente** se já existe evento de perícia para o processo e a data, antes de criar (filtre por `lawSuitId`, período e `itemType=events`; `juridiq_get_event` confirma um candidato específico). Use essa checagem direta como primária. A duplicidade pelo lado da tarefa (já existe tarefa de comunicação de perícia para aquele processo e data) continua útil como sinal complementar, para os casos em que a busca de evento não for conclusiva.

**Escopo:** a tabela de classificação cobre BPC-LOAS contra o INSS, que é o grosso do volume. Atos de processos de plano de saúde e cíveis estaduais não têm modelo próprio — use `Manifestação - (Nome Cliente)` com descrição detalhada e sinalize no resumo, para o escritório decidir se vale criar modelo específico.

## Fase 1 — Coleta e deduplicação

1. `list_publication` com `isHandled=false`, `limit=50`, paginando até o fim. Considere apenas as realmente não lidas (`readAt=null`).
2. Se nada voltar, diga só isso e encerre. Não invente trabalho.
3. **Deduplique por ato.** Quando o mesmo ato do mesmo processo aparece em mais de um diário na mesma data (típico: lista de intimações do PJe TRF1 + DJEN), trate **apenas a versão com teor**; a outra é duplicata e será marcada lida sem gerar tarefa.

Ao ler cada publicação, extraia: número do processo, órgão/vara, diário, data de disponibilização, `lawSuitId`, parte autora (e representante legal, quando houver), prazo informado e o teor do ato.

**Publicação sem `lawSuitId`:** não conclua de imediato que o processo não existe. Processos são cadastrados no Juridiq com frequência **depois** que a publicação chega, e o campo da publicação é um retrato do momento em que ela entrou. Consulte `list_lawSuit` com `processNumber` antes de decidir. Se o processo aparecer, siga normalmente com o `id` retornado. Se realmente não existir cadastro **e** o teor não permitir identificar a providência, a publicação fica não lida (ver Fase 6).

## Fase 2 — Classificação

Leia **`classification-rules.md`** — é a tabela que liga cada tipo de ato ao modelo de tarefa correto, com as regras finas (sentenças, acórdãos, mandado de segurança, perícias, RPV). Consulte-a para cada publicação; ela é o coração da skill e muda com mais frequência do que este arquivo.

Antes de criar, se houver dúvida sobre a intimação já ter sido cumprida, use `get_lawSuit_movements_by_lawSuitId` para ver o andamento.

Ao final desta fase você terá dois grupos: as publicações **com teor**, que já viram a tarefa definitiva, e as **sem teor**, que viram tarefa **provisória** — criada normalmente na Fase 3 e depois alterada na Fase 4, quando o PJe revelar o que o ato realmente é.

A provisória não é um rascunho descartável: é o registro visível de que aquele processo tem algo pendente de verificação, com prazo já lançado. Ela sempre nasce, mesmo quando a consulta ao PJe vai acontecer em seguida.

## Fase 3 — Criação das tarefas

1. Busque os modelos com `list_task_task_templates` (`limit=100`). O modelo é a fonte de **título, coluna (`columnId`), responsáveis, prioridade e tags**. Um snapshot dos modelos atuais está em `task-models.md` como referência rápida — mas **a chamada ao vivo é que vale**, porque o escritório cadastra modelos novos.
2. Título: use o título exato do modelo substituindo `(Nome Cliente)` / `(Nome do Cliente)` pelo nome da parte autora. Quando o diário só traz iniciais (comum em processos de menores), use as iniciais e acrescente o representante: `Manifestação - M. D. C. D. S. (repr. Wanessa Silva da Costa)`.
3. Vincule `lawSuitId` e `personIds` (a parte autora).
4. Descrição: escreva para quem vai executar a tarefa sem abrir os autos. Inclua resumo objetivo do ato, número do processo, órgão, data de disponibilização, prazo, e o que precisa ser decidido ou verificado. Quando a tarefa nasce de uma decisão judicial relevante, vale resumir os fundamentos — economiza uma leitura inteira depois.
5. Detalhe de API observado na prática: o `create_task` corta o primeiro caractere da descrição e às vezes o último. **Comece a descrição com dois espaços** e termine com um espaço — assim o corte cai no espaço e não no texto.

**Toda tarefa nasce de um modelo real, inclusive a provisória.** Não existe tarefa "genérica de espera": a publicação sem teor da Central de Perícias já nasce como `Comunicação Perícia Médica Federal`, porque é o desfecho mais provável e porque assim ela cai desde o início na coluna de perícias, com Hannah e Bruna — que são exatamente quem vai ligar para o cliente quando a data aparecer. A marca `TAREFA PROVISÓRIA — VERIFICAR TEOR NO PJe` vai no topo da descrição e é o que a distingue de uma tarefa fechada.

## Fase 4 — Consulta ao PJe e atualização das provisórias

Só para as publicações que ficaram provisórias. Sem essa fase, a designação de perícia fica sem data, sem local e sem evento na agenda — que é justamente o que o escritório precisa dessas intimações.

Verifique se as ferramentas do Chrome (`mcp__claude-in-chrome__*`) estão disponíveis e se o PJe TRF1 1º grau está logado.

**Estando disponível** — este é o caminho normal, não um extra. Para cada provisória: entre nos autos, leia o ato e **altere a tarefa existente** com `update_task_by_id`. Não crie outra nem exclua a provisória: a mesma chamada muda `description`, `title`, `columnId`, `responsiblesId`, `tags` e datas, então a tarefa se transforma no lugar, preservando o histórico e o vínculo com o processo.

- Era **designação de perícia** (caso mais comum): o modelo já está certo. Complete a descrição com data, hora, local completo e perito, ajuste `finalDate` se o ato indicar outro prazo, remova a marca de provisória — e crie o evento (Fase 5).
- Era **outra coisa** (redesignação, laudo juntado, despacho com prazo): troque na mesma chamada o `title`, `columnId`, `responsiblesId` e `tags` para os do modelo correto, conforme `classification-rules.md` e `task-models.md`.

**Não estando disponível** — caso das execuções agendadas puramente na nuvem, que não alcançam o navegador do computador. Não é erro: mantenha as provisórias como estão, não crie evento, liste-as no resumo em "verificar teor no PJe" e siga adiante.

Leia **`pje-enrichment.md`** antes de tocar no PJe — há ali uma regra que, se violada, registra ciência e dispara prazos contra o escritório.

## Fase 5 — Evento de perícia na agenda

O evento nasce **no momento em que data e hora existem de verdade** — direto da Fase 3, quando o diário já as trazia, ou da Fase 4, quando vieram do PJe. Nunca antes disso. Crie com `create_event`:

- **Título:** `Perícia Médica (Nome do Representante) - Justiça Federal (UF)` — nome do **representante legal** da parte autora (ou do próprio autor, quando não houver representante) e a sigla da UF da Seção Judiciária. Para perícia ou estudo social: `Perícia Social (Nome do Representante) - Justiça Federal (UF)`.
- **Responsáveis:** Hannah Oliveira - Pós Venda (`e631b150-fc66-46c0-99c3-51d229d5fe3e`) e Bruna Vitória - Comercial (`c5b58e8e-c6da-4b31-9706-1c34a27ef41d`).
- **`date`** = data da perícia; **`initialHour`** = horário designado; **`finalHour`** = uma hora depois.
- **Vínculos:** `lawSuitId` do processo e `personId` da parte autora.
- **Descrição:** nome do autor e do representante, perito, local completo, número do processo, vara/órgão e a orientação de levar documento com foto, laudos e exames atualizados.

Antes de criar, confira diretamente que não existe evento igual para o mesmo processo e data com `juridiq_list_events` (filtro `lawSuitId`, período e `itemType=events`); use a duplicidade pelo lado da tarefa apenas como sinal complementar (ver a nota na seção Ferramentas).

Se a Fase 4 não pôde rodar (sem Chrome) e o diário não trouxe data/hora, **não crie o evento**: a tarefa continua provisória e a pendência vai para o resumo. Um evento com data inventada é pior do que evento nenhum.

## Fase 6 — Marcar como lidas

Depois de concluída toda a criação, marque com `update_publication_read_movements_many` (em lotes) **todas as publicações tratadas**: as que geraram tarefa, as duplicatas e as de só ciência.

**Nunca marque como lida uma publicação que não conseguiu acessar, ler ou classificar.** Os casos típicos:

- processo sigiloso ou em segredo de justiça cujo teor não é publicado;
- publicação sem processo vinculado **e** sem teor suficiente para identificar a providência (depois de já ter checado `list_lawSuit` por `processNumber`, conforme a Fase 1);
- erro ou falha ao consultar a publicação;
- qualquer dúvida real sobre a providência cabível.

Nesses casos a publicação fica **não lida**, para conferência manual.

## Fase 7 — Resumo final

Termine com um resumo curto em português contendo:

**(a)** quantas publicações foram processadas no total;
**(b)** tarefas criadas — cliente, processo, modelo e prazo — e as que foram puladas por já existirem;
**(c)** eventos de perícia lançados na agenda, com data e hora;
**(d)** urgências: sentenças e acórdãos desfavoráveis, prazos ≤ 5 dias, perícias próximas;
**(e)** o que ficou como "verificar teor no PJe";
**(f)** **obrigatório — o placar de leitura**, em tabela e destacado: quantas foram marcadas como lidas e quantas ficaram NÃO LIDAS, listando cada não lida individualmente com número do processo, órgão e o motivo exato (sigiloso/sem teor, sem processo vinculado, falha de acesso, não classificada). Apresente o placar mesmo quando o número de não lidas for zero — nesse caso diga expressamente "0 não lidas".

Se a execução for agendada e não houver ninguém acompanhando, envie também uma notificação (`PushNotification`) com as urgências e o placar — o resumo no transcript não chega a ninguém.

## Arquivos de referência

- **`classification-rules.md`** — tabela ato → modelo de tarefa, com as regras finas de sentença, acórdão, mandado de segurança, perícia e RPV. Consulte para cada publicação.
- **`task-models.md`** — snapshot dos modelos cadastrados (títulos, colunas, responsáveis, tags, prazos padrão) e IDs úteis. Referência rápida; a chamada `list_task_task_templates` é que governa.
- **`pje-enrichment.md`** — como consultar o teor no PJe TRF1 sem registrar ciência, e como alterar a tarefa provisória depois. Leia na Fase 4, antes de abrir o navegador.
