# Cadeias POP → tarefas ADVBOX

Fonte: POP Movimentação de Intimações Judiciais + conformidade validada em
produção (casos 0005115, 0010551, 0012250). IDs entre crases são `tasks_id`
(atom); IDs de responsável são `users_id` (`advbox_list_members`).

O prazo fatal em dias vem do ato dirigido ao cliente e é calculado com
`djen_calculate_deadline` a partir da disponibilização DJEN. Não deduza
automaticamente três dias: a política de antecipação interna ainda precisa
ser confirmada. Quando o ato não traz duração numérica aplicável, revise
Expedientes antes de preencher `deadline` no ADVBOX.

## Etapa 0 — sempre primeira

- ABRIR INTIMAÇÃO `572240` (Kaique `132063`), mesmo dia. Checklist depois.

## §1 Ato ordinatório / prazo inicial / emenda

- CUMPRIR ATO ORDINATÓRIO `265427` — responsável: quem protocolou o caso
  (Samara `108476`, …); se ninguém protocolou, alternar Fabiana `51137` /
  Samara. Prazo conforme PJe.

## §2 Laudos (baixar + anexar o laudo na tarefa)

- Social → ANALISAR LAUDO SOCIAL JUDICIAL `5427574` (Emilly `49187`)
- Médico/pericial → ANALISAR LAUDO MÉDICO JUDICIAL `1461638` (Brenda `72411`)

## §3 Sentença de mérito (sempre após ABRIR INTIMAÇÃO)

**3.1 Procedente** (`Julgado procedente o pedido`):
1. SENTENÇA PREVIDENCIÁRIA `1273723` / CÍVEL `1409555` (Maurício `93726`, mesmo dia)
2. AVISAR RESULTADO PROCESSUAL `1535343` (Yslaine `225770`, dia seguinte)
3. Tarefa ao Financeiro (grupo `756`, tarefa livre)
4. ACOMPANHAR IMPLANTAÇÃO-JUD `1146726` (Daniela `33264`, segundas recorrentes)
5. ACOMPANHAR RPV/PCR `481309` (Matheus `259464`, +90d)
6. ANÁLISE RECURSAL `7185396` (M. Fernanda `179652`, prazo do ato; antecipação interna sob revisão)

**3.2 Improcedente**: SENTENÇA IMPROCEDENTE `1273724` (Maurício, mesmo dia) →
AVISAR (Maria Vitória `202747`, dia seguinte) → ANÁLISE RECURSAL (prazo do ato).

**3.3 Extintiva**: igual à 3.2 com SENTENÇA EXTINTIVA `3095733`.

**MS**: SENTENÇA MS (gap: sem átomo — tratar como livre + `comments`
detalhado) → ANÁLISE RECURSAL (prazo do ato) → AVISAR (Yslaine, dia seguinte).

Desvio de produção observado: AVISAR saiu para Maria Vitória (não Yslaine)
em 10.1 real; ANÁLISE DE SENTENÇAS-JUD (Brenda) inserida pós-decisão;
produção acompanha PRAZO INTERNO + FATAL (dois `ACOMPANHAR PRAZO`).

## §4 Cálculos

- ANALISE/MANIFESTAÇÃO DOS CALCULOS `430576` (Williadja `72707`) — anexar a
  planilha da intimação; prazo do ato dirigido ao autor.

## §5 Recurso do INSS → APRESENTAR CONTRARRAZOES `6283998` (M. Fernanda)

## §6 Contestação → IMPUGNAR CONTESTAÇÃO `298696` (Emilly)

## §7 Perícia médica

- ABRIR INTIMAÇÃO (mesmo dia) → AVISAR PERÍCIA MÉDICA-JUDICIAL `1146724`
  (Arthur `280388`, +1d; mesmo dia + aviso direto se a perícia for próxima).

## §8 Perícia social (condicional ao histórico ADVBOX)

- Sem tarefa de instrução/sem responsável → INSTRUÇÃO DE PERÍCIA SOCIAL
  JUDICIAL `1338370`, alternar Fabiana/Samara + notificar.
- Já atribuída → manter dono, lançar DILIGÊNCIA `25864` informando a
  designação.

## §9 Acórdão (ler o teor: definitivo ou retorno — ver `tpu-fingerprints.md`)

**10.1 Provido definitivo**: ABRIR → ACÓRDÃO PROVIDO `1274082`/`7185391`
(Maurício, mesmo dia) → AVISAR (Yslaine, mesmo dia) → Financeiro →
IMPLANTAÇÃO (segundas) → RPV/PCR (+90d).
**10.2 Improvido definitivo**: ABRIR → ACÓRDÃO IMPROVIDO `1274083`/`7185390`
→ AVISAR (Maria Vitória, dia seguinte) → ANÁLISE RECURSAL (prazo do ato).
**10.3 Retorno à 1ª instância**: só ABRIR + tarefa do ato ordenado. Sem
financeiro/RPV. Validado em produção (anulação → DILIGÊNCIA + instrução).

## §10 Embargos (perspectiva do cliente; ler o teor)

- ABRIR (Kaique, mesmo dia) → EMBARGOS ACOLHIDOS `7187555`/`7186606` ou
  REJEITADOS `7187557`/`7187558` (Maurício) → ANÁLISE RECURSAL (prazo do ato) →
  AVISAR (Yslaine se ganho / Maria Vitória se perda, dia seguinte).

## §11 DCB/prorrogação (benefício por incapacidade)

- DCB expressa → novo processo de prorrogação + FAZER PEDIDO DE
  PRORROGAÇÃO `740820` (Roberta `206548`); fatal = DCB, início = −15d.
- DCB não fixada → mesma tarefa; fatal+início = próxima sexta após sentença.

## Situações fora do mapa → escalar, nunca aplicar por analogia.
