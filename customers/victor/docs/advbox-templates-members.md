# ADVBOX — Members & Task Templates

Source: `GET /posts/create` (embeds full roster + workflow list) + `POST workflow/tasks` (steps).
Mapped 2026-09-28. Member list is global (same for any lawsuit); partners are lawsuit-scoped via `POST lawsuits/network` (empty for 17519594).
Tools: `advbox_list_members`, `advbox_list_lawsuit_partners`, `advbox_list_workflows`, `advbox_list_workflow_tasks`.

## Members

The roster (IDs, names and usernames) is not kept in the repository: read it live with `advbox_list_members`.

## Sector groups (squad select, 10)

| Group | ID | Members (IDs) |
|---|---|---|
| ATENDIMENTO | 3329 | 45284,93707,61411,81628,24809,179650 |
| COMERCIAL | 754 | 45022,93707,50550,93736,132071,202747,180777,202745,206548 |
| CONTROLADORIA | 3328 | 137077,216759,132063,130507,93726,55515,198959 |
| COORDENAÇÃO | 760 | 27938,45289 |
| DPA | 755 | 113206,118433,118438,136347,30710,55515,29682 |
| DPJ | 758 | 72411,49187,51137,179652,55515,108476,145859,72707 |
| DRP | 759 | 198957,182349,961 |
| FINANCEIRO | 756 | 124333,45290,33264,32996,216761,131493 |
| RECEPÇÕES | 757 | 51848,99801,209956,175870,115756,88077,93717,30710,104888,45289,143043 |
| TRIAGEM | 3330 | 51848,108485,115756,179431,177328,108477,124206 |

## Workflows = task templates (41, 135 steps)

Resolve steps at create time: `POST workflow/tasks` `{id, sequence: 1..N}` → `data-task` (template task ID), `data-user` (default responsible), step title.

### 1410 — BENEFÍCIO POR INCAPACIDADE RURAL (1 steps)

- task `1213275` default `—` — Benefício Por Incapacidade Rural - Adm

### 1411 — BENEFÍCIO POR INCAPACIDADE URBANO (1 steps)

- task `1213274` default `—` — Benefício Por Incapacidade Urbana - Adm

### 1446 — DIST - DPPA (1 steps)

- task `1210707` default `—` — Reanalise - Adm

### 2178 — DIST - LEAD (1 steps)

- task `1210707` default `—` — Reanalise - Adm

### 6205 — 1 - INTIMAÇÃO - BENEFICIO CONCEDIDO (5 steps)

- task `572240` default `93726` — Abrir Intimação
- task `1150274` default `93726` — Benefício Concedido
- task `1535343` default `225770` — Avisar Resultado Processual
- task `1146721` default `33264` — Acompanhar Implantação -adm
- task `25864` default `93726` — Diligência

### 6206 — 2 - INTIMAÇÃO - BENEFÍCIO INDEFERIDO (4 steps)

- task `572240` default `93726` — Abrir Intimação
- task `1149787` default `93726` — Benefício Indeferido
- task `5842834` default `108477` — Inspeção de Negativa
- task `1535343` default `202747` — Avisar Resultado Processual

### 6220 — 4 - EFETUAR DISTRIBUIÇÃO - AIT (2 steps)

- task `1217002` default `113206` — Efetuar Distribuição - Adm
- task `4453618` default `118433` — Analise Documental - Ait

### 6221 — 5 - EFETUAR DISTRIBUIÇÃO - JUD PREV - S (2 steps)

- task `972786` default `72411` — Efetuar Distribuição - Jud Prev
- task `3472443` default `108477` — Triagem

### 6222 — 3 - INTIMAÇÃO - EXIGÊNCIA (4 steps)

- task `572240` default `93726` — Abrir Intimação
- task `25828` default `29682` — Cumprir Exigência - Adm
- task `6971134` default `118433` — Cumprimento de Exigência Simples -adm
- task `7733389` default `216759` — Acompanhar Prazo

### 6558 — 6 - INTIMAÇÃO - PRORROGAÇÃO CONCEDIDA (5 steps)

- task `572240` default `93726` — Abrir Intimação
- task `5075233` default `93726` — Prorrogação Concedida
- task `4161197` default `131493` — Cobrar Prorrogação
- task `740820` default `206548` — Fazer Pedido de Prorrogação
- task `1535343` default `225770` — Avisar Resultado Processual

### 6559 — 7 - INTIMAÇÃO - PRORROGAÇÃO INDEFERIDA (4 steps)

- task `572240` default `93726` — Abrir Intimação
- task `5075234` default `93726` — Prorrogação Indeferida
- task `5842834` default `108477` — Inspeção de Negativa
- task `1535343` default `202747` — Avisar Resultado Processual

### 7081 — TRIAGEM (3 steps)

- task `5347991` default `124206` — Efetuar Distribuição - Triagem
- task `3472443` default `—` — Triagem
- task `5795027` default `45022` — Enviar Vídeo de Feedback

### 7128 — 0 - SENTENÇA PROCEDENTE JUD (7 steps)

- task `572240` default `93726` — Abrir Intimação
- task `1273723` default `93726` — Sentença Previdenciária Procedente
- task `1535343` default `225770` — Avisar Resultado Processual
- task `481309` default `259464` — Acompanhar Rpv/pcr
- task `1146726` default `33264` — Acompanhar Implantação - Jud
- task `7185396` default `179652` — Análise Recursal
- task `7733389` default `72707` — Acompanhar Prazo

### 7131 — 00 - SENTENÇA IMPROCEDENTE JUD (4 steps)

- task `572240` default `93726` — Abrir Intimação
- task `1535343` default `202747` — Avisar Resultado Processual
- task `1273724` default `93726` — Sentença Previdenciária Improcedente
- task `7185396` default `179652` — Análise Recursal

### 7132 — 000- SENTENÇA EXTINTIVA JUD (4 steps)

- task `572240` default `93726` — Abrir Intimação
- task `3095733` default `93726` — Sentença Extintiva - Prev
- task `1535343` default `202747` — Avisar Resultado Processual
- task `7185396` default `179652` — Análise Recursal

### 9231 — 8 - PROTOCOLO ADM (3 steps)

- task `1127909` default `93726` — Processo Protocolado-adm
- task `726991` default `137077` — Acompanhar Andamento-adm
- task `1379985` default `206548` — Marcar Perícia Médica/social - Adm

### 10012 — 11 - VERIFICAR PRORROGAÇÃO (6 steps)

- task `25839` default `280388` — Avisar Perícia Médica-adm
- task `679506` default `30710` — Instrução Á Pericia Administrativa-adm
- task `1023897` default `206548` — Verificar Resultado da Prorrogação-adm
- task `726991` default `137077` — Acompanhar Andamento-adm
- task `7733391` default `93726` — Prorrogação Solicitada
- task `4161197` default `131493` — Cobrar Prorrogação

### 10013 — 22 - VERIFICAR PRORROGAÇÃO - CONCEDIDO (3 steps)

- task `1023897` default `206548` — Verificar Resultado da Prorrogação-adm
- task `4161197` default `131493` — Cobrar Prorrogação
- task `7733391` default `93726` — Prorrogação Solicitada

### 10915 — 9 - AIT (5 steps)

- task `3273150` default `93726` — Ait Protocolado - Adm
- task `25839` default `180777` — Avisar Perícia Médica-adm
- task `679506` default `30710` — Instrução Á Pericia Administrativa-adm
- task `7264942` default `216759` — Perícia/ Av.social Marcada
- task `726991` default `137077` — Acompanhar Andamento-adm

### 11708 — 09 - AVISAR PERICIA/PERICIA MARCADA (2 steps)

- task `25839` default `280388` — Avisar Perícia Médica-adm
- task `7264942` default `—` — Perícia/ Av.social Marcada

### 12049 — Z - ANALISE DE LAUDO (3 steps)

- task `572240` default `93726` — Abrir Intimação
- task `1461638` default `72411` — Analisar Laudo Médico Judicial
- task `7733389` default `198959` — Acompanhar Prazo

### 12050 — Q - AVISAR PERÍCIA JUDICIAL (3 steps)

- task `572240` default `198959` — Abrir Intimação
- task `1146724` default `280382` — Avisar Perícia Médica - Judicial
- task `7264942` default `132063` — Perícia/ Av.social Marcada

### 12051 — ' - CUMPRIR ATO (3 steps)

- task `572240` default `93726` — Abrir Intimação
- task `265427` default `93726` — Cumprir Ato Ordinatório
- task `7733389` default `198959` — Acompanhar Prazo

### 12052 — '' - PETIÇÃO DIVERSA (3 steps)

- task `572240` default `93726` — Abrir Intimação
- task `637612` default `93726` — Petição Diversa
- task `7733389` default `—` — Acompanhar Prazo

### 12383 — PROPOSTA DE ACORDO - ZZ (2 steps)

- task `572240` default `93726` — Abrir Intimação
- task `646681` default `49187` — Analisar Proposta de Acordo

### 12384 — CÁLCULOS - XX (3 steps)

- task `572240` default `93726` — Abrir Intimação
- task `430576` default `72707` — Analise/manifestação dos Calculos
- task `7733389` default `198959` — Acompanhar Prazo

### 14485 — ZZ - EXIGÊNCIA SIMPLES (3 steps)

- task `572240` default `—` — Abrir Intimação
- task `6971134` default `—` — Cumprimento de Exigência Simples -adm
- task `7733389` default `216759` — Acompanhar Prazo

### 14486 — NN - EXIGÊNCIA NORMAL (3 steps)

- task `572240` default `—` — Abrir Intimação
- task `25828` default `29682` — Cumprir Exigência - Adm
- task `7733389` default `216759` — Acompanhar Prazo

### 15743 — AAACORDÃO IMPROVIDO (5 steps)

- task `572240` default `93726` — Abrir Intimação
- task `1274083` default `93726` — Acórdao Improvido - Prev
- task `8358651` default `72411` — Análise de Sentenças - Jud
- task `1535343` default `202747` — Avisar Resultado Processual
- task `7185396` default `179652` — Análise Recursal

### 16453 — JJ - LI (3 steps)

- task `25863` default `225126` — Ligar Para Cliente
- task `1210583` default `225126` — Atendimento Cliente/telefone
- task `1535343` default `225126` — Avisar Resultado Processual

### 16590 — WILLI - PROPOSTA ACEITA (5 steps)

- task `1273723` default `93726` — Sentença Previdenciária Procedente
- task `1146726` default `33264` — Acompanhar Implantação - Jud
- task `481309` default `259464` — Acompanhar Rpv/pcr
- task `7733389` default `72707` — Acompanhar Prazo
- task `1535343` default `225770` — Avisar Resultado Processual

### 17040 — ARRAIS/CLEYTON (4 steps)

- task `568751` default `198957` — Cadastro Cliente/processo
- task `1119122` default `198957` — Documento Anexado
- task `5347986` default `198959` — Contrato Fechado - Drp
- task `5347991` default `124206` — Efetuar Distribuição - Triagem

### 17520 — BT - MANIFESTAÇÃO AO LAUDO MÉDICO (2 steps)

- task `1461638` default `72411` — Analisar Laudo Médico Judicial
- task `25858` default `72411` — Manifestação a Laudo Médico Judicial

### 17525 — EM - MANIFESTAÇÃO AO LAUDO SOCIAL (3 steps)

- task `5427574` default `49187` — Analisar Laudo Social Judicial
- task `5427572` default `49187` — Manifestação a Laudo Social Judicial
- task `25865` default `198959` — Diligência Cumprida

### 17608 — CADASTRO MANOELA (3 steps)

- task `568751` default `182349` — Cadastro Cliente/processo
- task `1119122` default `182349` — Documento Anexado
- task `1400289` default `179650` — Primeiro Atendimento - Online

### 17609 — CADASTRO CLEYTON (3 steps)

- task `568751` default `198957` — Cadastro Cliente/processo
- task `1119122` default `198957` — Documento Anexado
- task `1400289` default `179650` — Primeiro Atendimento - Online

### 17760 — SS - PROTOCOLO JUD (2 steps)

- task `9318097` default `132063` — Processo Protocolado-jud
- task `5795027` default `225126` — Enviar Vídeo de Feedback

### 17799 — WILLI - PROCESSO APTO CÍVEL (4 steps)

- task `658745` default `72707` — Analise Inicial - Triagem
- task `3770614` default `124206` — Processo Apto
- task `3309804` default `72411` — Efetuar Distribuição - Jud Cível
- task `7185386` default `72707` — Petição Inicial - Cível

### 18592 — EE - SIMPLES (2 steps)

- task `572240` default `137077` — Abrir Intimação
- task `6971134` default `—` — Cumprimento de Exigência Simples -adm

### 18641 — INSP NEGATIVA (5 steps)

- task `6002063` default `—` — Parecer - Triagem
- task `5347991` default `124206` — Efetuar Distribuição - Triagem
- task `853916` default `275960` — Cobrar Pendencia Externa
- task `3770614` default `124206` — Processo Apto
- task `972786` default `72411` — Efetuar Distribuição - Jud Prev

### 18694 — ARRAIS/MANU (4 steps)

- task `568751` default `182349` — Cadastro Cliente/processo
- task `1119122` default `182349` — Documento Anexado
- task `5347986` default `198959` — Contrato Fechado - Drp
- task `5347991` default `124206` — Efetuar Distribuição - Triagem

## Create-task payload (wired; live create/delete not yet validated)

Endpoint `POST /posts` (multipart, has `file[]`; CSRF `_token`). Template flow: pick `workflow` → `POST workflow/tasks` → set `tasks_id` + `workflow_id`/`workflow_sequence` → submit. Squad select auto-fills `user` from group `data-members`. Free task: skip workflow, fill `comments` + dates directly.

Fields (39): `_token, lawsuits_id, user, squad, has_partner, tasks_id, workflow, workflow_id, workflow_sequence, workflow_limit, steps_id, stages_id, send_sms, send_email, date, hour, date_deadline, display_schedule, toggle_end, all_day, date_end, hour_end, local, comments, file[], important, urgent, display_date, is_recurring, recurrence, repeat_on_week, private, retroactive, date_completed, create=1, ai_suggestion, create_token, editing, v2=1`.

The MCP now exposes `advbox_create_task` (with `ADVBOX_ALLOW_WRITES=true`). The form and client script confirm `guests[]` for responsibles and `date` as DD/MM/YYYY. A live create/delete check still needs a dedicated test lawsuit; the previously noted TI TESTE customer was not available in this account.
