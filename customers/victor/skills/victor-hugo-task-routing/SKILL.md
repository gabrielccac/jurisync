---
name: victor-hugo-task-routing
description: Roteia intimações DJEN/Comunica (fonte primária) com enriquecimento PJe TRF5 secundário para criação de tarefas no ADVBOX conforme o POP de Movimentação de Intimações Judiciais. Use quando o usuário pedir para verificar atualizações de processos, sugerir tarefas a criar ou validar o roteamento DJEN→ADVBOX.
---

# Roteamento DJEN → ADVBOX (PJe `trf5-1g` como fonte secundária)

Transforme comunicações DJEN/Comunica normalizadas em tarefas ADVBOX seguindo
o POP. Fonte primária: poll `src/jurisync/connectors/djen/`
(`client` + `normalize` + `store`, API pública sem auth, OABs via perfil do
cliente — nunca hardcoded). PJe entra SOMENTE quando `detalhar_pje=true`.
O credenciamento deste cliente (certificado A1 + TOTP) vale SOMENTE
para o portal `trf5-1g` (`pje1g.trf5.jus.br`) — nunca use os portais TRF1
(`1g`/`2g`) nesta skill.
Nunca invente movimentos, prazos, tarefas ou responsáveis. Toda decisão de
roteamento deve citar a comunicação DJEN (`id`/data) que a originou.

Na execução agendada, o agente consulta o MCP DJEN, aplica o POP, confere
duplicatas no ADVBOX, cria as tarefas pelo MCP ADVBOX e verifica o resultado.
Registre cada `id` DJEN como tratado somente depois da criação verificada ou de
uma decisão explícita de não criar tarefa. O CLI DJEN `--poll` grava IDs como
*vistos* logo após a busca; seu estado não confirma roteamento nem escrita no
ADVBOX e não serve como ledger de tarefas concluídas.

Leia sempre antes de rotear:

- `references/djen-text-fingerprints.md` (texto DJEN → seção POP; quando pedir PJe);
- `references/tpu-fingerprints.md` (movimentos PJe do gate secundário; quais são ruído);
- `references/pop-chains.md` (seção POP → cadeia de tarefas com átomos e prazos);
- `references/daily-review.md` (prévia diária, decisão de detalhar no PJe,
  descrições propostas e prazos).

Quando o usuário pedir uma execução de teste/revisão das intimações diárias,
use a prévia de `references/daily-review.md`: leia e classifique todas as
comunicações da janela, detalhe no PJe as que atenderem aos gatilhos, calcule
os prazos suportados, verifique duplicatas e apresente a descrição proposta
por tarefa. Nesse modo, não escreva no ADVBOX nem marque IDs como tratados.
Só passe à criação quando o usuário pedir a execução com escrita.

## Fluxo (5 portões, semiautomático)

0. **Consulta DJEN**: `djen_search_communications` por OAB/UF do perfil
   (`14663/PB`, `42543/PE`, `48647/PE`; 7 variantes), tribunal e janela
   explícitos. Paginar até `count`, deduplicar por `id`. A ferramenta é só
   leitura; o poll incremental com estado roda à parte no CLI. Não criar
   tarefa para `data_cancelamento` / `ativo=false` sem re-cheque.
1. **Família**: classifique o `texto` DJEN (`djen-text-fingerprints.md`).
   Ruído (conclusos, decorrido-terceiros, juntada-email/cert-idão
   administrativa, mero expediente sem ordem, arquivado) → pare, sem tarefa.
2. **Direção**: confira `advogados[]` (`numero_digits`+`uf_oab` nossos) e
   `destinatarios[].polo` do cliente. Ordem para terceiros → monitorar.
   Para filtrar PE no TRF5, confira os quatro últimos dígitos CNJ (`830x`)
   ou a UF do órgão; nunca procure `830x` em qualquer posição do número.
3. **Seção POP**: (família + verbo de ordem do teor) → seção POP. Casos ambíguos (acórdão definitivo-vs-retorno, embargos
   perspectiva, MS, DCB explícita-vs-implícita) exigem leitura do teor —
   chame o LLM-juiz com o texto e responda em JSON estrito.
4. **Detalhe PJe (só se `detalhar_pje=true`)**: `fetch_process`
   (`numero_processo_mascara`, `trf5-1g`) + até 5 `documentIds` para o teor;
   reclassifica e completa. Texto DJEN suficiente (~2/3 dos JEF-INSS) →
   pula este portão.
5. **Expansão e prazo**: seção POP → cadeia ordenada de tarefas (átomos
   `tasks_id`, responsáveis). Para cada prazo judicial em dias aplicável à
   parte autora, extraia o número do ato e chame `djen_calculate_deadline`
   com `data_disponibilizacao`. Use `advboxDeadline` em `deadline` e registre
   ID DJEN, prazo extraído, publicação, início, vencimento e calendário nos comentários.
   Se houver mais de um prazo, escolha pelo destinatário e pela providência;
   se o prazo for apenas "legal", em dias corridos, condicional, remetido a
   Expedientes, ou o calendário não cobrir a data/unidade, não invente
   `deadline`: encaminhe para revisão.

## Invariantes de criação

- `guests[]` SEMPRE inclui Deivid (`216759`), senão a tarefa some das
  listagens da integração.
- Todo `POST /posts`: `lawsuits_id`, `tasks_id` (átomo, nunca workflow
  como tipo), `date` DD/MM/YYYY, `hour`, `comments` com nome POP + detalhe,
  `create=1`, `v2=1`. Template: some `workflow_id`/`workflow_sequence`
  (um POST por etapa, encadeado via `sequence`).
- `deadline` é o vencimento judicial calculado. `date` é o início da tarefa
  conforme a cadeia POP; não use a data de criação como vencimento.
  Antecipação interna exige regra do escritório confirmada separadamente;
  não subtraia três dias automaticamente. Datas de acompanhamento (`+1d`,
  `+90d`, próxima segunda-feira, DCB−15d) seguem a cadeia POP.
- Anexos (laudos, cálculos, planilhas): `POST /s3` (stage:
  `root=posts`, `folder=files/temp/{userId}`) imediatamente antes do
  `POST /posts`. Um stage = uma tarefa; nunca reutilize código entre
  tarefas (re-baixe + re-stage). Em aborto, delete staged via
  `POST s3 {id, type: file, _method: DELETE}`.
- Idempotência por ID de comunicação DJEN (nunca por processo). Duplicada
  (mesmo tipo+processo+data) → pular. Itens PJe-avulsos (compliance) usam o
  ID de evento PJe.
- Correções do usuário voltam para esta skill (versione a mudança).

## Ferramentas

- Leitura DJEN (primária): `djen_search_communications` para uma página
  normalizada e `djen_get_certidao_text` para texto extraído da certidão
  pública. Se `extractionStatus=ocr_needed`, peça OCR; não invente texto.
  Documentos do processo (laudos/cálculos) vêm do PJe autenticado.
- Cálculo DJEN: `djen_calculate_deadline` para intimações JFPE dentro da
  cobertura do calendário publicado pela ferramenta. A data do documento
  nos autos não substitui `data_disponibilizacao` do DJEN.
- Leitura PJe (secundária, só `detalhar_pje=true`): `pje_get_process` (portal `trf5-1g`), até 5 `documentIds`
  para o teor. Documentos anexos (laudos) podem vir só como shell do
  visualizador — nesses casos o conteúdo vem dos anexos ADVBOX.
- Leitura ADVBOX: `advbox_list_activities` (conferir duplicadas),
  `advbox_list_members`, `advbox_list_workflows`, `advbox_workflow_tasks`,
  `advbox_lawsuit_report` (histórico para o condicional da perícia social).
- Escrita ADVBOX (gate `ADVBOX_ALLOW_WRITES=true`): `advbox_create_task`
  (átomo avulso ou de workflow + anexos), `advbox_complete_task`,
  `advbox_delete_task`. Sem o gate, entregue rascunhos estruturados e pare.
