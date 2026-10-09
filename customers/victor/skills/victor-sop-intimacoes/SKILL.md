---
name: victor-sop-intimacoes
description: SOP de intimações do escritório do Victor. Lê comunicações DJEN/Comunica (fonte primária) do TRF5 1G/JEF, enriquece no PJe quando o texto não basta e propõe ou cria as tarefas ADVBOX do SOP (átomo, descrição, início, final, anexo). Use para verificar atualizações de processos, sugerir tarefas a criar ou validar o roteamento DJEN→ADVBOX.
---

# SOP de intimações: DJEN → PJe → tarefas ADVBOX (Victor)

Transforma comunicações DJEN em tarefas ADVBOX conforme o SOP (POP) de Movimentação de
Intimações Judiciais. O texto DJEN vem primeiro; o PJe `trf5-1g` (`pje1g.trf5.jus.br`)
entra só para o que o texto não traz. O credenciamento do cliente (certificado A1 +
TOTP) vale **somente** para `trf5-1g`: nunca use os portais TRF1 nesta skill.

Esta skill é **por cliente**: os padrões, átomos, responsáveis e registros fora do mapa
são do escritório do Victor. Os arquivos de procedimento (`classificacao.md`,
`enriquecimento-pje.md`) são a parte que um futuro cliente poderia reaproveitar; os
dados do cliente estão em `tarefas.md` e `fora-do-mapa.md`.

## Modos

- **Prévia** (padrão para testes e revisões): classifica, enriquece, calcula prazos e
  mostra a tarefa proposta. Não escreve no ADVBOX nem marca nada como tratado.
  Siga `references/previa-diaria.md`.
- **Execução com escrita**: só quando o usuário pedir, e com `ADVBOX_ALLOW_WRITES=true`.
  Sem o gate, entregue rascunhos e pare.

## Regras que não mudam

1. **Nunca invente** movimentos, prazos, datas, tarefas ou responsáveis. Toda decisão
   cita o ID DJEN e a data da comunicação que a originou. Sem dado, diga "não
   identificado" e onde procurou.
2. **Classifique pelo teor do ato**, não pela etiqueta de movimento do PJe nem pelo
   título da comunicação.
3. **Prazo da parte autora = `limite` da aba Expedientes do PJe** (`pje_get_process`,
   campo `expedientes`); sem linha, a certidão de intimação (EXPEDIENTES GERADOS) com
   `djen_calculate_deadline`. "Sem prazo" no Diário Eletrônico = nenhum vencimento;
   prazo do INSS (meio Sistema) = acompanhamento, nunca vencimento da autora.
4. **Data de perícia ou audiência é evento**, não prazo. Sem data nos documentos,
   escreva "não designada" e a fonte.
5. **Um ato, uma cadeia.** IDs DJEN com o mesmo texto geram uma tarefa; liste todos os
   IDs. Antes de criar, procure o ID DJEN no histórico do ADVBOX (`tarefas.md` §6).
6. **Fora do mapa → escalar.** Nunca aplique um padrão por analogia: registre em
   `references/fora-do-mapa.md` com a evidência e pare.
7. **Anexo** só quando o tipo de tarefa pede (`tarefas.md` §5).
8. **Não existe ledger.** O CLI `--poll` marca IDs como vistos e não prova que houve
   roteamento. O estado do processo é o ADVBOX (ID DJEN no comentário).

## Fluxo por comunicação

0. **Consulta DJEN**: `djen_search_communications` por OAB/UF do perfil
   (`14663/PB`, `42543/PE`, `48647/PE`), tribunal e janela explícitos. Paginar até
   `count`, deduplicar por `id`. A ferramenta é só leitura.
1. **Escopo e direção** → `references/classificacao.md` §1–§2.
2. **Padrão** (P1…P19) → `classificacao.md` §4. Casos ambíguos exigem ler o teor
   (§6): monocrático × colegiado, definitivo × retorno, embargos, DCB.
3. **Enriquecimento** → `references/enriquecimento-pje.md`: nível N0–N3 por padrão; a
   certidão do movimento é a fonte do prazo.
4. **Resultado** → um de: **criar**, **check**, **incremento** (`advbox_update_task`),
   **escalar**,
   **monitorar**, **ruído**, **duplicada** (`fora-do-mapa.md` §1).
5. **Tarefa** → `references/tarefas.md`: átomo e cadeia (§3), cabeçalho e campos por tipo
   (§4), início e final (§1), anexo (§5), invariantes de criação (§7).
6. **Prévia ou criação** com verificação: depois de criar, releia o histórico do processo
   e confirme a tarefa.

## Ferramentas

- **DJEN** (primário, leitura): `djen_search_communications` (use `compact=true` e
  `textLimit`: uma página de 50 itens completa passa de 270 mil caracteres),
  `djen_get_certidao_text` (teor completo; `extractionStatus=ocr_needed` → peça OCR),
  `djen_calculate_deadline` (JFPE, calendário publicado pela ferramenta; a data do
  documento nos autos não substitui `data_disponibilizacao`).
- **PJe** (secundário, leitura): `pje_get_process` (portal `trf5-1g`: linha do tempo,
  inventário e `expedientes`, a aba Expedientes com o prazo `limite` de cada comunicação),
  `pje_read_expediente_ato` (o ato de um expediente e seus anexos; recusa os com ciência
  pendente) e `pje_read_documents` (só documentos, até 5 por chamada, sem linha do
  tempo). HTML sem o número CNJ volta como `extracted_unnumbered`; a certidão de
  intimação volta com `expedientes` (prazo por destinatário); `save=true` grava o original
  na pasta compartilhada (`savedAs`) para anexar. Áudios: extra `transcription`.
- **ADVBOX** (leitura): `advbox_list_lawsuits` (busca por CNJ); `advbox_lawsuit_report`
  com `taskSearch` (acha o ID DJEN no histórico, concluídas incluídas) e `maxTasks`,
  `maxNotices`, `maxTextChars` (relatórios chegam a 70 mil caracteres);
  `advbox_find_tasks` (tarefas **pendentes**, com `postId`); `advbox_list_activities`
  (inclui `deadline`; o filtro pendentes/concluídas não tem efeito hoje);
  `advbox_list_publications` (intimações que o ADVBOX recebeu: `period` today/3d/7d/30d ou
  `dateFrom`+`dateTo`, `tribunal` por sigla, `processNumber` CNJ, `responsible`; até 200 por
  página, `hasMore` indica a próxima; `status` pendente/concluida mostra se já foi tratada;
  o texto de uma intimação vem de `advbox_activity_details` com o `postId`);
  `advbox_list_members`, `advbox_list_workflows`, `advbox_workflow_tasks`.
- **ADVBOX** (escrita, gate `ADVBOX_ALLOW_WRITES=true`): `advbox_create_task` (anexos por
  `attachmentFiles`; `dryRun=true` valida corpo, workflow e arquivos sem enviar nada),
  `advbox_update_task` (edita uma tarefa **pendente**: comentário, data, prazo,
  responsáveis, anexos; o ADVBOX **substitui** a tarefa e o resultado tem um `postId`
  NOVO), `advbox_complete_task`, `advbox_delete_task` (pede confirmação).

## Referências (leia nesta ordem)

| Arquivo | Para quê |
|---|---|
| `references/classificacao.md` | Escopo, direção, ruído, padrões P1…P19, casos que pedem leitura do teor |
| `references/enriquecimento-pje.md` | Quão fundo ler no PJe por padrão, como ler os documentos, prazos |
| `references/tarefas.md` | Átomos e cadeias, responsáveis, descrição, datas, anexos, dedupe, invariantes |
| `references/fora-do-mapa.md` | Registro vivo de casos sem regra e divergências observadas; como promover |
| `references/previa-diaria.md` | Modo prévia e o formato de saída |

## Manutenção

- Correções do usuário voltam para esta skill; versione a mudança.
- Quando o escritório definir a regra de um caso em `fora-do-mapa.md`, **promova-a**:
  atualize `classificacao.md` e `tarefas.md` e feche a entrada.
- Registros de testes e dry-runs ficam em `customers/victor/docs/`; o conhecimento
  permanente fica aqui.
