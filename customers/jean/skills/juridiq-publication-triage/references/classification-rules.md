# Classificação: ato publicado → modelo de tarefa

Tabela de decisão da triagem. Os títulos entre aspas são **exatos** dos modelos cadastrados no Juridiq — reproduza inclusive espaços finais quando existirem (alguns modelos têm), substituindo `(Nome Cliente)` / `(Nome do Cliente)` pelo nome da parte autora.

Antes de criar qualquer tarefa desta tabela, confira com `list_task` (filtro `lawSuit`) se já não existe tarefa equivalente para o mesmo processo e providência.

## Índice

1. [Fase de conhecimento](#1-fase-de-conhecimento)
2. [Perícias](#2-perícias)
3. [Sentenças](#3-sentenças)
4. [Mandado de segurança](#4-mandado-de-segurança)
5. [Acórdãos](#5-acórdãos)
6. [RPV e execução](#6-rpv-e-execução)
7. [Intimações genéricas e sem teor](#7-intimações-genéricas-e-sem-teor)
8. [Só ciência](#8-só-ciência)

---

## 1. Fase de conhecimento

| Ato publicado | Tarefa |
|---|---|
| Contestação do INSS juntada / intimação para réplica | **"Réplica BPC - (Nome Cliente)"** |
| Proposta de acordo do INSS | **"Manifestação Aceitação Acordo - (Nome Cliente)"** — modelo já vem com prioridade Alta |
| Emenda à inicial, regularização de documentos, despacho com prazo para a parte autora | **"Manifestação - (Nome Cliente)"** |

## 2. Perícias

| Ato publicado | Tarefa |
|---|---|
| Perícia médica designada, **com data/hora/local** | **"Comunicação Perícia Médica Federal - (Nome Cliente)"** — incluir data, hora, local e nome do perito na descrição. **Criar também o evento na agenda** (Fase 5 do SKILL.md) |
| Perícia ou estudo social designado, **com data/hora/local** | **"Comunicação Perícia Social - (Nome do Cliente)"** + evento na agenda |
| Ausência à perícia / redesignação | **"Remarcação Perícia Judicial - (Nome Cliente) "** *(atenção ao espaço final no título do modelo)* |
| Laudo pericial juntado, **conclusão desfavorável** | **"Impugnação ao Laudo - (Nome Cliente)"** |
| Laudo pericial juntado, **conclusão favorável** | **"Manifestação Laudo Favorável - (Nome do Cliente) "** *(espaço final)* |
| Laudo juntado, **conclusão não informada** na publicação | **"Impugnação ao Laudo - (Nome Cliente)"** provisória, com observação "verificar teor no PJe" |

**Perícia anunciada sem data/hora** (teor não publicado — típico das intimações da Central de Perícias do TRF1): use **"Comunicação Perícia Médica Federal - (Nome Cliente)"** provisória, com a observação "verificar teor no PJe" na descrição, e **não** crie evento na agenda enquanto não houver data e hora confirmadas.

A tarefa nasce na coluna de perícias com os responsáveis definidos pelo modelo ao vivo, não no quadro jurídico: intimação da Central de Perícias sobre perícia é assunto de comunicação com o cliente. Como já nasce no modelo mais provável, a consulta ao PJe normalmente só precisa **completar a descrição** com data, hora, local e perito. Se o ato revelar ser outra coisa — redesignação, laudo juntado, despacho com prazo —, altere a mesma tarefa com `juridiq_update_task`, levando título, coluna, responsáveis e tags do modelo correto; não crie outra nem exclua a provisória.

## Prazos legais de referência

Use sempre o prazo informado no ato. Estes valores servem só de fallback quando o ato é silente — e são mais confiáveis que o `daysForDue` do modelo, que é 5 dias para quase tudo:

| Peça | Prazo legal |
|---|---|
| Recurso inominado (JEF) | 10 dias — art. 42 da Lei 9.099/95 |
| Apelação | 15 dias úteis — art. 1.003, §5º, do CPC |
| Embargos de declaração | 5 dias — art. 1.023 do CPC |
| Réplica / impugnação à contestação | 15 dias úteis — art. 350 do CPC |
| Manifestação sobre laudo pericial | 15 dias úteis — art. 477, §1º, do CPC |

O prazo da tarefa deve ficar **antes** do prazo legal, com margem para revisão e protocolo — não no último dia.

## 3. Sentenças

| Sentença | Tarefa |
|---|---|
| **Improcedente / extinção em Juizado Especial Federal** | **"Recurso Inominado BPC - (Nome Cliente)"** — urgente no resumo |
| **Improcedente ou extinção em vara comum** (não JEF) | **"Apelação BPC - (Nome do Cliente)"** — urgente no resumo |

O recurso segue o rito do juízo: JEF → recurso inominado; vara comum → apelação. Confira o órgão na publicação antes de escolher o modelo.

**Atenção:** as regras desta seção valem para as ações de BPC. **Mandado de segurança tem regra própria — veja a [seção 4](#4-mandado-de-segurança)**, onde a extinção sem mérito NÃO gera tarefa de recurso.
| **Procedente com condenação do INSS** (BPC concedido + atrasados) | **DUAS tarefas:** "Expedição RPV - (Nome Cliente)" e "Comunicação Sentença Favorável - (Nome do Cliente)" |
| **Homologatória de acordo** | **DUAS tarefas**, as mesmas do caso anterior |

A tarefa de RPV nasce já na sentença — não porque a requisição possa ser expedida antes do trânsito em julgado (não pode: art. 100, §5º, da CF e art. 535, §3º, do CPC), mas porque é o acompanhamento interno que garante o **pedido de destaque dos honorários contratuais** (art. 22, §4º, da Lei 8.906/94) antes da expedição. Registre na descrição que o INSS ainda pode recorrer.

### Extinção SEM resolução do mérito por não comparecimento à perícia ou abandono

Caso especial, e a diferença importa. O fundamento varia — abandono da causa por mais de 30 dias com intimação pessoal prévia (art. 485, III e §1º, do CPC), extinção do art. 51, I, da Lei 9.099/95 aplicável aos JEFs, ou falta superveniente de interesse (art. 485, IV/VI). **Confira na sentença qual foi**, porque isso muda o recurso e as condições do reajuizamento; não presuma o inciso III.

A extinção sem mérito **não obsta nova propositura** (art. 486 do CPC), e o escritório frequentemente prefere **REAJUIZAR** em vez de recorrer. Ressalva: nas extinções pelos incisos II e III do art. 485, o art. 486, §2º, condiciona o despacho da nova inicial à prova do pagamento das custas e honorários. Em JEF de 1º grau isso é inócuo (arts. 54 e 55 da Lei 9.099/95), mas **em vara comum o reajuizamento não é gratuito** — sinalize no resumo quando for esse o caso.

Crie a tarefa de recurso normalmente (é a salvaguarda de prazo), mas **destaque no resumo que cabe ao advogado optar entre recurso inominado e reajuizamento**. Se o advogado já tiver decidido pelo reajuizamento, a tarefa correta passa a ser:

- **"Protocolar Inicial BPC - (Nome Cliente)"** — quando a inicial já estiver pronta;
- **"Inicial BPC - (Nome Cliente)"** — quando precisar ser confeccionada.

Em ambas, registre na descrição a orientação de ajuizar no foro do **domicílio da parte autora** e com **pedido expresso de perícia na localidade de residência**.

## 4. Mandado de segurança

O escritório impetra MS contra a **mora do INSS** na análise de requerimento administrativo. Isso muda o que conta como derrota — e o recurso tem modelo próprio: **"Apelação MS - (Nome Cliente)"**, não "Apelação BPC". A apelação em BPC ataca sentença de improcedência do benefício; a apelação em MS ataca a denegação da ordem, com fundamentação e rito distintos (Lei 12.016/09).

| Desfecho | Tarefa |
|---|---|
| **Segurança DENEGADA com resolução de mérito** (o juízo examinou e rejeitou o direito líquido e certo) | **"Apelação MS - (Nome Cliente)"** — urgente no resumo |
| **Sentença procedente / concessiva** (mora reconhecida, sem atrasados) | Apenas **"Comunicação Sentença Favorável - (Nome do Cliente)"**. Sem RPV: em MS não há condenação pecuniária (Súmulas 269 e 271 do STF). Registre na descrição que a sentença concessiva está sujeita a **reexame necessário** (art. 14, §1º, da Lei 12.016/09) — o cliente não deve ser informado de que o processo acabou |
| **Extinção SEM resolução do mérito** — inclusive art. 485, VI, por perda superveniente do objeto quando o INSS analisa o requerimento no curso do processo | **NÃO criar tarefa de apelação.** O escritório não recorre nesses casos |

Sobre a extinção sem mérito no MS: quando o INSS analisa o requerimento durante o processo, o mandamus perde o objeto **porque cumpriu sua função** — a mora foi sanada. Apelar não traria utilidade. O que interessa é o **resultado da análise administrativa**, e é isso que deve ir para o resumo:

- benefício **concedido** → só ciência; se houver contato pendente com o cliente, avaliar tarefa de comunicação;
- benefício **indeferido** → a providência útil é **ajuizar a ação de BPC-LOAS** ("Inicial BPC" ou "Protocolar Inicial BPC"), no foro do domicílio da parte autora, com pedido de perícia na localidade de residência.

Se a publicação não revelar o resultado administrativo, sinalize no resumo para verificação no dossiê previdenciário/GERID — sem criar a tarefa de recurso.

## 5. Acórdãos

| Acórdão | Tarefa |
|---|---|
| **Favorável** — provimento do recurso do cliente, negado provimento ao recurso do INSS, ou manutenção da sentença procedente | **"Comunicação Acórdão Favorável - (Nome do Cliente)"** + verificar RPV (regra abaixo) |
| **Desfavorável** | **"Embargos de Declaração - (Nome Cliente)"**, destacando no resumo para o advogado avaliar o recurso cabível |

**RPV no acórdão favorável — não é regra única.** O raciocínio "a RPV já foi aberta na sentença" só vale quando havia sentença procedente a manter (negado provimento ao recurso do INSS, ou manutenção da procedência). Quando o acórdão **dá provimento ao recurso do cliente**, a sentença era improcedente e **nunca existiu tarefa de RPV** — aí ela nasce aqui. Confira com `list_task` (filtro `lawSuit`) se existe tarefa "Expedição RPV" para o processo: se existir, não duplique; se não existir e o acórdão condenou o INSS a atrasados, crie **"Expedição RPV - (Nome Cliente)"**.

## 6. RPV e execução

| Ato publicado | Tarefa |
|---|---|
| RPV já expedida / migrada / SIREA / valores disponíveis | **NENHUMA tarefa** — a tarefa de RPV nasce na sentença. Apenas marcar lida |

## 7. Intimações genéricas e sem teor

| Ato publicado | Tarefa |
|---|---|
| Qualquer outra intimação **com prazo** dirigida à parte autora | **"Manifestação - (Nome Cliente)"** |
| Lista de intimações do PJe TRF1 **sem teor**, dirigida à parte autora, com prazo | **"Manifestação - (Nome Cliente)"** provisória, com observação "verificar teor no PJe" — completada na Fase 4 do SKILL.md, quando o Chrome estiver disponível |
| Intimação sem teor dirigida ao **INSS ou ao MPF** | Só ciência |

**Exceção importante:** as intimações da **Central de Perícias do TRF1** ("acerca do ato ordinatório / despacho / decisão relacionado a perícia, prazo 5 dias") **não** caem nesta seção, mesmo sem teor — anunciam assunto de perícia, e vão para "Comunicação Perícia Médica Federal" provisória, conforme a [seção 2](#2-perícias). Sinalize no resumo como pendentes de verificação.

## 8. Só ciência

Nenhuma tarefa; marcar lida e registrar no resumo:

- intimação dirigida a outra parte;
- ata de distribuição;
- pauta de julgamento;
- republicação de ato já tratado;
- processo em que o escritório está no polo passivo;
- despacho de arquivamento definitivo e retorno dos autos à origem.

Esta lista é fechada de propósito. Um ato que não se encaixe em nenhuma das hipóteses acima **não é "só ciência" por não ter prazo aparente** — se você não conseguiu identificar a providência, o caminho é deixar a publicação **não lida** (Fase 6 do SKILL.md), não marcá-la como ciência. Marcar lida é irreversível; deixar não lida custa uma conferência manual.
