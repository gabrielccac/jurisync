# Tarefas ADVBOX: átomos, cadeias, descrição, datas e anexos

Fonte: SOP (POP) Movimentação de Intimações Judiciais + conformidade validada em
produção (casos 0005115, 0010551, 0012250) + padrões observados em tarefas já criadas
no ADVBOX (08/10/2026). IDs entre crases são `tasks_id` (átomo); IDs de responsável são
`users_id` (`advbox_list_members`).

## 1. Datas

- **Início (`date`)**: data da tarefa conforme a cadeia. `ABRIR INTIMAÇÃO`: o dia da
  leitura/disponibilização. As demais: o dia seguinte ao ABRIR ou o dia da publicação
  (`AVISAR`: dia seguinte, conforme a seção). Não use a data de criação como vencimento.
- **Final (`deadline`)**: o vencimento do prazo da parte autora (certidão; ver
  `enriquecimento-pje.md` §5) calculado com `djen_calculate_deadline`. Sem prazo
  confirmável, deixe vazio e diga por quê na descrição.
- Data de perícia/audiência é **evento**, não vencimento: vai na descrição.
- Antecipação interna exige regra confirmada pelo escritório; não subtraia três dias.
- Datas de acompanhamento (`+1d`, `+90d`, segunda-feira, DCB−15d) seguem a cadeia.

## 2. Responsáveis (conferidos na lista do ADVBOX em 08/10/2026)

Kaíky `132063` · Maurício `93726` · Yslaine `225770` · Maria Vitória `202747` ·
Maria Daniela `33264` · João Matheus `259464` · Maria Fernanda `179652` ·
Emilly `49187` · Brenda `72411` · Arthur `280388` · Sâmara `108476` · Fabiana `51137` ·
Roberta `206548` · Williadja `72707` · CONTROLADORIA `216759`.

Os responsáveis abaixo são o padrão do POP. Exceção definida pelo escritório: em
`CUMPRIR ATO ORDINATÓRIO` o responsável é quem fez a petição inicial do processo, lido
no histórico do ADVBOX (§3, §1), não quem protocolou.

## 3. Átomos e cadeias

### Etapa 0 — sempre primeira
- `ABRIR INTIMAÇÃO` `572240` (Kaíky `132063`), mesmo dia. Checklist depois.

### §1 Ato ordinatório / prazo inicial / emenda / pedido de endereço
- `CUMPRIR ATO ORDINATÓRIO` `265427` — prazo conforme a certidão. Responsável: **quem fez
  a petição inicial do processo**, porque o ato ordinatório normalmente chega depois do
  peticionamento e quem peticionou conhece o caso. Para achar:
  1. `advbox_lawsuit_report` do processo com `taskSearch="PETIÇÃO INICIAL"` (inclui as
     concluídas): o responsável da tarefa `PETIÇÃO INICIAL …` (ex.: `PETIÇÃO INICIAL - PREV
     SM-URBANO`) é o responsável do ato ordinatório. Converta o nome no ID pela lista do §2
     (ou `advbox_list_members`).
  2. Não confunda com `PROTOCOLAR PREV - JUD` / `PROCESSO PROTOCOLADO-JUD`: quem protocola
     nem sempre é quem escreveu a petição.
  3. Mais de uma `PETIÇÃO INICIAL` com responsáveis diferentes, ou tarefa com mais de um
     responsável: mostre os nomes na prévia e pergunte. Nenhuma `PETIÇÃO INICIAL` no
     histórico: alternar Fabiana `51137` / Sâmara `108476` e registrar na prévia que o
     responsável não foi achado no histórico.
  Na prévia e no comentário, cite de onde veio o responsável (tarefa, data e nome).

### §2 Laudos (anexar o laudo na tarefa)
- Social → `ANALISAR LAUDO SOCIAL JUDICIAL` `5427574` (Emilly `49187`)
- Médico/pericial → `ANALISAR LAUDO MÉDICO JUDICIAL` `1461638` (Brenda `72411`)

### §3 Sentença de mérito (sempre após ABRIR INTIMAÇÃO)
**3.1 Procedente** (`Julgado procedente o pedido`):
1. `SENTENÇA PREVIDENCIÁRIA` `1273723` / `CÍVEL` `1409555` (Maurício `93726`, mesmo dia)
2. `AVISAR RESULTADO PROCESSUAL` `1535343` (Yslaine `225770`, dia seguinte)
3. Tarefa ao Financeiro (grupo `756`, tarefa livre)
4. `ACOMPANHAR IMPLANTAÇÃO-JUD` `1146726` (Maria Daniela `33264`, segundas recorrentes;
   a descrição leva o **prazo fatal** do INSS)
5. `ACOMPANHAR RPV/PCR` `481309` (João Matheus `259464`, +90d)
6. `ANÁLISE RECURSAL` `7185396` (Maria Fernanda `179652`, prazo do ato)

**3.2 Improcedente**: `SENTENÇA IMPROCEDENTE` `1273724` (Maurício, mesmo dia) → AVISAR
(Maria Vitória `202747`, dia seguinte) → `ANÁLISE RECURSAL` (prazo do ato).
**3.3 Extintiva**: igual à 3.2 com `SENTENÇA EXTINTIVA` `3095733`.
**Extinção parcial + improcedência**: sem entrada no POP → `fora-do-mapa.md` FM-01.
**MS**: `SENTENÇA MS` (sem átomo: tarefa livre + `comments` detalhado) → `ANÁLISE
RECURSAL` → AVISAR (Yslaine, dia seguinte).

Desvio de produção: AVISAR saiu para Maria Vitória (não Yslaine) em 10.1 real;
`ANÁLISE DE SENTENÇAS-JUD` (Brenda) inserida pós-decisão; produção acompanha PRAZO
INTERNO + FATAL (dois `ACOMPANHAR PRAZO`) em alguns casos, sem regra universal.

### §4 Cálculos
- `ANALISE/MANIFESTAÇÃO DOS CALCULOS` `430576` (Williadja `72707`) — prazo do ato dirigido
  ao autor.
- Quando a autora **produz** o cálculo (intimação "apresentar os cálculos"), o ADVBOX
  registra `ANALISE/MANIFESTAÇÃO DOS CALCULOS` com o texto da intimação e conclui com
  `DILIGÊNCIA CUMPRIDA` ("prazo cumprido com a apresentação da planilha"). Anexo: a
  planilha produzida.

### §5 Recurso do INSS → `APRESENTAR CONTRARRAZOES` `6283998` (Maria Fernanda)

### §6 Contestação → `IMPUGNAR CONTESTAÇÃO` `298696` (Emilly)

### §7 Perícia médica
- `ABRIR INTIMAÇÃO` (mesmo dia) → `AVISAR PERÍCIA MÉDICA-JUDICIAL` `1146724` (Arthur
  `280388`, +1d; mesmo dia + aviso direto se a perícia for próxima).

### §8 Perícia social (condicional ao histórico ADVBOX)
- Sem tarefa de instrução/sem responsável → `INSTRUÇÃO DE PERÍCIA SOCIAL JUDICIAL`
  `1338370`, alternar Fabiana/Sâmara + notificar.
- Já atribuída → manter dono, lançar `DILIGÊNCIA` `25864` informando a designação (ex.:
  mandado expedido, nomeação, endereço).

### §9 Acórdão (colegiado e definitivo; ver `classificacao.md` §6)
**9.1 Provido definitivo**: ABRIR → `ACÓRDÃO PROVIDO` `1274082`/`7185391` (Maurício, mesmo
dia) → AVISAR (Yslaine, mesmo dia) → Financeiro → IMPLANTAÇÃO (segundas) → RPV/PCR (+90d).
**9.2 Improvido definitivo**: ABRIR → `ACÓRDÃO IMPROVIDO` `1274083`/`7185390` → AVISAR
(Maria Vitória, dia seguinte) → `ANÁLISE RECURSAL` (prazo do ato).
**9.3 Retorno à 1ª instância**: só ABRIR + tarefa do ato ordenado. Sem financeiro/RPV.
Validado em produção (anulação → `DILIGÊNCIA` + instrução).
Decisão **monocrática** da Turma (art. 932 CPC) → `fora-do-mapa.md` FM-04/05.

### §10 Embargos (perspectiva do cliente; ler o teor)
- ABRIR (Kaíky, mesmo dia) → `EMBARGOS ACOLHIDOS` `7187555`/`7186606` ou `REJEITADOS`
  `7187557`/`7187558` (Maurício) → `ANÁLISE RECURSAL` (prazo do ato) → AVISAR (Yslaine se
  ganho / Maria Vitória se perda, dia seguinte).

### §11 DCB/prorrogação (benefício por incapacidade)
- DCB expressa → novo processo de prorrogação + `FAZER PEDIDO DE PRORROGAÇÃO` `740820`
  (Roberta `206548`); fatal = DCB, início = −15d.
- DCB não fixada → mesma tarefa; fatal+início = próxima sexta após sentença.

### Audiência / sessão
- Intimação com data, hora, local/link: `ABRIR INTIMAÇÃO` com os dados do evento na
  descrição; outro átomo só se o POP indicar. Após o evento, anote o resultado em
  `REALIZAÇÃO DE AUDIÊNCIA` se essa etapa fizer parte do fluxo. Consulte o PJe se faltar
  dado (remissão, link, local).

### Acordo (workflow `12383` "Workflow Proposta de Acordo")
- Passos: `ABRIR INTIMAÇÃO` `572240` (padrão Maurício `93726`) → `ANALISAR PROPOSTA DE
  ACORDO` **`646681`** (Emilly `49187`). O átomo 646681 não constava do POP antigo.
- Um POST por etapa, encadeado via `sequence` (ver §7).

**Situações fora do mapa → escalar em `fora-do-mapa.md`, nunca aplicar por analogia.**

## 4. Descrição: cabeçalho comum + campos por tipo

Cabeçalho (primeira linha, sempre):

```
POP §{n} | {ÁTOMO} | DJEN {id(s)} | disponibilização {DD/MM/AAAA} | processo {CNJ}
```

Corpo (linhas curtas; omita o que não se aplica, **nunca invente**):

```
Ato: {uma linha; para sentença/acórdão/ato, o trecho operativo entre aspas}
Pessoas: {perito/assistente (nomeação {id}) · contato do cliente, se houver}
Evento: {data, hora, local} ou "não designada (certidão {id})"
PJe: {tipo e ID dos documentos lidos}
Endereço/contato: {fonte e data} ou "não localizado: despacho de {data} pede {n} dias"
Prazo: {n} dias úteis (certidão {id}), vencimento {DD/MM/AAAA}
       ou "prazo não identificado em {documentos lidos, data}"
```

Campos obrigatórios por tipo:

| Tipo | Obrigatórios além do cabeçalho |
|---|---|
| `ABRIR INTIMAÇÃO` | Síntese do ato e próxima providência; perito/assistente se houver; audiência: data, hora, local/link |
| Perícia (`AVISAR`, `INSTRUÇÃO`, `DILIGÊNCIA`) | Perito/assistente, data/hora/local **ou** "não designada", contato/endereço (fonte), itens exigidos |
| `ANALISAR LAUDO …` | Tipo, ID PJe e data do laudo, 3–5 achados-chave (composição familiar/renda ou conclusão/DII), prazo |
| `ANALISAR PROPOSTA DE ACORDO` | Objeto/benefício, perícia (data, perito, DII), termos do INSS (DIB, DIP, DCB, atrasados), decisão do cliente (aceita/recusa) e próximo passo, prazo |
| `CUMPRIR ATO ORDINATÓRIO` | Exigências citadas literalmente, documentos que o cliente precisa trazer, prazo |
| `SENTENÇA …` / `ACÓRDÃO …` | Dispositivo entre aspas, efeito concreto, quem cumpre e em quantos dias, prazo de recurso |
| `AVISAR RESULTADO PROCESSUAL` | Canal e contato, resultado, DIB, DIP, retroativos, RPV |
| `ANALISE/MANIFESTAÇÃO DOS CALCULOS` | Parâmetros da sentença (DIB, início dos retroativos, índices), planilha, prazo |
| `ACOMPANHAR IMPLANTAÇÃO - JUD` | Obrigação do INSS, benefício, **prazo fatal** e sua fonte, estado ("ainda não implantado") |
| `ANÁLISE RECURSAL` | Resultado, prazo e base (certidão ou legal), recomendação (recorrer ou não) |
| `FAZER PEDIDO DE PRORROGAÇÃO` | DCB e fonte; datas da cadeia só se a decisão confirmar o marco |

Se faltar prazo: `Prazo não identificado em {documentos lidos}, {data}.` Se houver
divergência (texto × certidão), cite os valores e a origem de cada um.

## 5. Anexos por tipo

| Tipo | Anexar | Regra |
|---|---|---|
| `ANALISAR LAUDO …` | O PDF do laudo ("Laudo Pericial (NOME)"), nunca o registro do editor | §2: anexo obrigatório |
| `ANALISAR PROPOSTA DE ACORDO` | PDF da proposta do INSS | Obrigatório |
| `ANALISE/MANIFESTAÇÃO DOS CALCULOS` | A planilha (a produzida, quando a autora a apresenta) | Obrigatório |
| `DILIGÊNCIA` (mandado) | O mandado | Opcional |
| Demais tipos | Nenhum | Os fatos vão na descrição; o PDF fica no PJe |

Como anexar: `pje_read_documents` com `save=true` grava o original como
`{CNJ só dígitos}_{docId}.pdf` e devolve `savedAs`; passe esse nome em
`advbox_create_task.attachmentFiles` (até 5 arquivos; pdf, png, jpg, mp3, txt; 25 MB cada).
Valide antes com `dryRun=true`. A pasta de envio da conta (`files/temp/<id>`) é lida da
própria sessão; `ADVBOX_UPLOAD_FOLDER` só é necessário para sobrescrever.

Regras: um stage por tarefa, nunca reutilize o código entre tarefas (re-baixe e
re-stage); documentos sigilosos só depois de conferir o acesso e com o mínimo de dados
pessoais.

## 6. Dedupe (antes de criar)

A equipe já cria muitas dessas tarefas, e nem todas trazem o ID DJEN no comentário. Por
isso a checagem **não** se limita a buscar o ID: **liste as tarefas do processo onde a
nova seria criada e compare**. O passo é obrigatório, também na prévia (a prévia mostra o
resultado da checagem).

1. **Liste as tarefas do processo** (pendentes e concluídas):
   - `advbox_lawsuit_report` do processo, com `maxTasks` suficiente para cobrir o
     histórico desde a data da comunicação (inclui concluídas e as de outros usuários);
   - `advbox_find_tasks` do processo, para as **pendentes** e seu `postId`.
2. **Busque o ID DJEN**: `taskSearch="{id DJEN}"` no relatório. Achou: é a mesma
   comunicação, já tratada.
3. **Compare mesmo sem o ID**: para cada tarefa listada, confira átomo/tipo + ato
   (mesmo documento, decisão ou evento) + janela de datas a partir da disponibilização.
   O mesmo ato pode ter vários IDs DJEN, e a equipe pode ter criado a tarefa à mão, com
   título ou data um pouco diferentes. Em caso de dúvida se é o mesmo ato, leia o
   comentário da tarefa existente; se ainda houver dúvida, **escale** (não crie).
   **O critério é a obrigação, não o texto nem o ID.** Pergunte: o que a equipe precisa
   fazer, sobre qual documento/decisão/evento, e a tarefa existente já cobre isso? Uma
   intimação posterior de texto diferente pode levar à **mesma** tarefa: reiteração
   ou republicação ("intime-se novamente", "cumpra-se a decisão anterior"), despacho que
   só manda cumprir o que a decisão anterior já mandou, nova intimação sobre o mesmo
   laudo, proposta de acordo ou emenda. Nesses casos é `check`; se a nova intimação traz
   prazo ou dado diferente, é `incremento` (atualize a pendente). Já a **nova obrigação**
   do mesmo tipo (nova perícia depois de concluída a anterior, novo prazo aberto por nova
   decisão, tarefa anterior já concluída e novo ato exigindo nova providência) é tarefa
   nova. Se o ato anterior já foi concluído, confirme que a nova intimação não é só eco
   dele antes de criar.
4. **Decida**: se já existe, resultado `check` (nada a fazer) ou `incremento` (acrescentar
   informação à pendente, ver `fora-do-mapa.md` §1), nunca nova tarefa. Registre na
   prévia e no comentário qual tarefa existente (`postId`, data, responsável) cobriu o ato.
   Casos reais a evitar: duas tarefas para a mesma proposta de acordo; duas
   `ANALISAR LAUDO SOCIAL` para o mesmo laudo.
5. **Dentro do mesmo lote**, comunicações com o mesmo ato geram uma só tarefa (a primeira
   criada passa a constar na listagem das seguintes: releia a listagem a cada criação).

## 7. Invariantes de criação (`POST /posts`)

- `guests[]` SEMPRE inclui **CONTROLADORIA (`216759`)**, senão a tarefa some das
  listagens da integração.
- Todo POST: `lawsuits_id`, `tasks_id` (átomo, nunca workflow como tipo), `date`
  DD/MM/YYYY, `hour`, `comments`, `create=1`, `v2=1`. Template: some
  `workflow_id`/`workflow_sequence` (um POST por etapa, encadeado via `sequence`).
- Anexos: `POST /s3` (stage: `root=posts`, `folder=files/temp/{userId}`) imediatamente
  antes do `POST /posts`. Em aborto, delete o staged via `POST s3 {id, type: file,
  _method: DELETE}`.
- Idempotência por ID de comunicação DJEN (nunca por processo). Itens PJe-avulsos
  (compliance) usam o ID de evento PJe.
- Escrita só com `ADVBOX_ALLOW_WRITES=true`; sem o gate, entregue rascunhos e pare.
- `guests[]` inclui `216759` porque é a conta com que a integração entra no ADVBOX (a
  pasta de envio dela é `files/temp/216759`); no ADVBOX o nome é CONTROLADORIA.
- **Incremento / edição** de tarefa existente: `advbox_update_task`, só em tarefa
  **pendente** (a ferramenta recusa posts já substituídos, concluídos ou excluídos). O
  ADVBOX implementa a edição como substituição: o `postId` muda, o antigo deixa de valer
  e **qualquer referência guardada deve usar o ID DJEN do comentário, não o postId**. Os
  anexos existentes são mantidos. Passe `expectComments` (trecho do comentário atual) para
  que a edição seja recusada se o postId apontar para outra tarefa, e valide antes com
  `dryRun=true`. Para acrescentar informação use `appendComments`, não `comments`.
