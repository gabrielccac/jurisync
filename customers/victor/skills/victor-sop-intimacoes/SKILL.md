---
name: victor-sop-intimacoes
description: SOP de intimações do escritório do Victor. Lê comunicações DJEN/Comunica (fonte primária) do TRF5 1G/JEF, enriquece no PJe quando o texto não basta e propõe ou cria as tarefas ADVBOX do SOP (átomo, descrição, início, final, anexo). Use para verificar atualizações de processos, sugerir tarefas a criar ou validar o roteamento DJEN→ADVBOX.
---

# SOP de intimações: DJEN → PJe → tarefas ADVBOX (Victor)

Transforma comunicações DJEN em tarefas ADVBOX conforme o SOP (POP) de Movimentação de
Intimações Judiciais. O texto DJEN vem primeiro; o PJe do TRF5 entra só para o que o texto
não traz, no portal que emitiu a comunicação (`enriquecimento-pje.md` §0): `trf5-1g`
(`pje1g.trf5.jus.br`, varas e JEFs) ou `trf5-2g` (`pje2g.trf5.jus.br`, Turmas Recursais de
PE e PB e o Tribunal). O credenciamento do cliente (certificado A1 + TOTP) vale para esses
dois: nunca use os portais TRF1 nesta skill.

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
   IDs. Antes de criar, **liste as tarefas do processo** (pendentes e concluídas) e
   compare por ato, não só pelo ID DJEN: a equipe também cria tarefas à mão (`tarefas.md` §6).
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
5. **Dedupe antes de criar** → `tarefas.md` §6: liste as tarefas do processo e compare
   com a tarefa proposta; se o ato já está coberto, o resultado é **check** ou
   **incremento**, não criar.
6. **Tarefa** → `references/tarefas.md`: átomo e cadeia (§3), cabeçalho e campos por tipo
   (§4), início e final (§1), anexo (§5), invariantes de criação (§7). Em
   `CUMPRIR ATO ORDINATÓRIO`, o responsável é quem fez a `PETIÇÃO INICIAL` do processo no
   ADVBOX (`tarefas.md` §3, §1), não quem protocolou.
7. **Prévia ou criação** com verificação: depois de criar, releia o histórico do processo
   e confirme a tarefa.

## Ferramentas

Os parâmetros de cada ferramenta estão na descrição do próprio MCP; aqui ficam só o papel
de cada uma e as armadilhas que mudam a decisão.

- **DJEN** (primário, leitura): `djen_search_communications` (use `compact=true` e
  `textLimit`: uma página completa passa de 270 mil caracteres), `djen_get_certidao_text`
  (`extractionStatus=ocr_needed` → peça OCR), `djen_calculate_deadline` (a data do
  documento nos autos não substitui `data_disponibilizacao`).
- **PJe** (secundário, leitura, portal `trf5-1g` ou `trf5-2g` conforme o `portal` da
  comunicação, `enriquecimento-pje.md` §0): `pje_get_process` (linha do tempo,
  inventário e `expedientes`, com o prazo `limite` de cada comunicação),
  `pje_read_expediente_ato` (recusa expedientes com ciência pendente) e
  `pje_read_documents`. A certidão de intimação volta com `expedientes` (prazo por
  destinatário); `save=true` grava o original na pasta compartilhada para anexar.
- **ADVBOX** (leitura): `advbox_list_lawsuits` (busca por CNJ); `advbox_lawsuit_report`
  com `taskSearch` acha o ID DJEN no histórico, concluídas incluídas (limite os
  relatórios com `maxTasks`, `maxNotices`, `maxTextChars`); `advbox_find_tasks` vê só
  tarefas **pendentes**; `advbox_list_publications` mostra as intimações que o ADVBOX
  recebeu e se já foram tratadas (o texto vem de `advbox_activity_details`).
- **ADVBOX** (escrita, gate `ADVBOX_ALLOW_WRITES=true`): `advbox_create_task` (rode antes
  com `dryRun=true`), `advbox_update_task` (só tarefa **pendente**; o ADVBOX **substitui**
  a tarefa e devolve um `postId` NOVO), `advbox_complete_task`, `advbox_delete_task`
  (pede confirmação).

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
