# Prévia diária DJEN → ADVBOX

Use esta prévia para revisar uma execução diária e comparar a classificação, a
necessidade de detalhamento no PJe, as descrições propostas e os prazos antes
de criar tarefas. **A prévia não escreve no ADVBOX nem marca comunicações como
tratadas.**

## Revisão por comunicação

Para cada ID DJEN:

1. Confira cancelamento/atividade, destinatário, polo e OAB. Ruído, ordem dirigida
   a terceiro e duplicata não geram tarefa.
2. Classifique pelo texto integral, usando `djen-text-fingerprints.md`, e resolva
   a cadeia no `pop-chains.md`. Não roteie pelo título isolado da comunicação.
3. Decida e registre `PJe: sim/não` e o motivo. Quando necessário, consulte o
   PJe TRF5 1G, associe movimentos e anexos por processo, data, origem e conteúdo,
   e leia os documentos relevantes (até cinco IDs por consulta). Não conclua pelo
   movimento mais próximo sem conferir o teor.
4. Confira no histórico do ADVBOX se a providência já foi feita e, para perícia
   social, se já há instrução/responsável. Identifique duplicata pelo ID DJEN.
5. Extraia o prazo do ato dirigido ao cliente. Calcule pela data de
   disponibilização com `djen_calculate_deadline` quando suportado. Mostre o
   prazo, publicação, início, vencimento e calendário usado.
6. Gere uma descrição por tarefa/etapa da cadeia, preservando o título e o estilo
   do escritório. Copie o trecho operativo do ato quando ele trouxer requisitos,
   prazo ou consequência; acrescente apenas contexto confirmado no DJEN/PJe.

Se o PJe falhar, o prazo estiver ambíguo ou houver conflito entre fontes, a
prévia deve mostrar a pendência e não apresentar uma data como confirmada. Não
crie tarefa nem registre a comunicação como tratada durante esta revisão.

## Quando e como detalhar no PJe

| Padrão da intimação | Busca no PJe |
|---|---|
| DJEN traz ordem completa, prazo numérico e documentos/dados pedidos | Em geral não precisa; use o texto DJEN e calcule o prazo. |
| Texto curto/genérico, “último ato”, ID de origem ou remissão a Expedientes | Obrigatória: localize o ato referido e leia a decisão/certidão que fundamenta a intimação. |
| Prazo ausente, “prazo legal”, condicional ou divergente | Obrigatória: confira o ato originário, certidão e anexos. Se continuar ambíguo ou incompatível com o calculador, encaminhe para revisão. |
| Laudo, cálculo, proposta ou contestação que precisa ser analisado | Obrigatória quando o conteúdo integral/anexo não estiver no DJEN; abra o documento e identifique seu ID. |
| Perícia com data, hora, local, perito ou instruções ausentes, ou que remete “aos autos” | Obrigatória: procure designação, mandado e certidão relacionados. |
| Sentença | Leia o dispositivo integral no DJEN; consulte o PJe se o dispositivo estiver incompleto, remeter a outro documento ou faltar anexo necessário. |
| Acórdão, embargos ou decisão sobre DCB | Leia o teor/dispositivo no PJe para distinguir resultado definitivo de retorno, identificar quem opôs os embargos e verificar se há DCB expressa. |
| Sigilo/segredo de justiça | Consulte o PJe autenticado; não conclua a partir do texto público. |
| Audiência/sessão com data, hora, local/link e instruções completos no DJEN | Em geral não precisa; leve os dados publicados à tarefa. Consulte o PJe se houver remissão ou informação faltante. |
| Obrigação de fazer | Confira quem deve cumprir, qual obrigação foi imposta e o prazo dado ao INSS; determine se a parte autora também recebeu uma providência. |

### Busca em cluster

1. Use como âncora o CNJ e a intimação DJEN. Anote data de disponibilização,
   ID de origem e expressões que identifiquem o ato (“último ato”, “documento
   ID”, “perícia designada”, “manifestar-se sobre”).
2. Na linha do tempo do PJe, encontre o evento pelo ID referido ou pela
   combinação de processo, tipo/conteúdo e data. Não escolha um movimento apenas
   por estar mais próximo no tempo.
3. Abra o cluster relacionado: comunicação/intimação, certidão, decisão ou
   despacho de origem, mandado e documentos juntados na mesma sequência. Se o
   dado ainda não aparecer, amplie a busca para movimentos imediatamente
   anteriores e posteriores que compartilhem assunto, parte, perito ou ID de
   origem; pare quando encontrar fonte suficiente ou concluir que não consta.
4. Abra os anexos do cluster e leia o conteúdo, não apenas o nome ou o resumo do
   movimento. Para laudo, planilha, contestação e proposta, registre o ID e os
   elementos necessários à tarefa. Se o visualizador trouxer só uma estrutura
   vazia, procure o arquivo correspondente nos anexos do ADVBOX conforme a
   integração; OCR/leitura indisponível deve aparecer como pendência.
5. Confirme a correspondência pelo CNJ e por pelo menos um vínculo de conteúdo,
   ID, destinatário ou evento. Registre os IDs de movimento/documento e o dado
   extraído. Se não localizar, escreva “não localizado no cluster consultado”;
   não infira data, prazo ou instrução.

PJe suficiente encontrado → `PJe: sim`, com IDs consultados. DJEN suficiente →
`PJe: não — DJEN suficiente`. Falha do conector ou documento inacessível →
`PJe: pendente`, sem tratar ausência de resposta como ausência de informação.

## Prazos

- O prazo fatal é o prazo processual aplicável à providência da parte autora,
  extraído do ato e calculado a partir da disponibilização DJEN conforme o
  calendário disponível.
- Data de audiência/perícia é data de evento, não prazo fatal. Mostre-a na
  descrição e não a use como vencimento processual.
- Prazo dado ao INSS para cumprir obrigação não é automaticamente prazo fatal
  da parte autora; use acompanhamento da cadeia POP.
- Se não houver duração numérica confirmável, mostre `prazo não identificado`
  e deixe `deadline` vazio. Se a fonte trouxer “prazo legal”, prazo condicional,
  contagem não suportada ou remissão a Expedientes, encaminhe para revisão.
- Se DJEN e certidão/documento PJe divergirem, registre os dois valores e não
  escolha silenciosamente um deles.
- Há histórico de tarefas separadas `ACOMPANHAR PRAZO INTERNO` e
  `ACOMPANHAR PRAZO FATAL` (processo 0005115-69.2025.4.05.8303). Isso confirma
  que o escritório usa ambos em alguns casos, mas não define antecedência
  interna universal. Só proponha prazo interno quando uma regra POP o indicar;
  nunca subtraia três dias por padrão.

## Padrões de descrição por tipo

Preserve o título/átomo do ADVBOX. Escreva uma descrição curta que leve para a
tarefa o comando e os dados necessários. Identifique a fonte com `DJEN [id]`,
data de disponibilização e CNJ; se o PJe foi consultado, cite o ID do movimento
ou documento. Copie o trecho operativo quando requisitos, prazo ou consequência
forem importantes; não transcreva fundamentação irrelevante.

| Tarefa/família | Conteúdo a registrar |
|---|---|
| `ABRIR INTIMAÇÃO` | Síntese do ato e da próxima providência; em audiência/sessão, data, hora, local/link. |
| `CUMPRIR ATO ORDINATÓRIO` | O que apresentar/corrigir, documentos exigidos, trecho relevante e prazo. |
| `ANALISAR LAUDO ...` / manifestação | Tipo de laudo, ID PJe, conclusão relevante para a providência e prazo de manifestação. |
| `ANALISE/MANIFESTAÇÃO DOS CALCULOS` | Documento/planilha, parâmetros da decisão, providência pedida e prazo. |
| `APRESENTAR CONTRARRAZOES` / `IMPUGNAR CONTESTAÇÃO` | Parte que recorreu/contestou, pontos a responder, documento de origem e prazo; crie só se houver ordem dirigida à parte autora. |
| `SENTENÇA ...` / `ACÓRDÃO ...` / `EMBARGOS ...` | Resultado do dispositivo, efeito concreto e próximo passo; classifique pelo teor, não pelo rótulo automático. |
| `ANALISAR PROPOSTA DE ACORDO` | ID da proposta, termos essenciais a revisar e prazo para manifestação. |
| `AVISAR PERÍCIA ...` / instrução social | Data, hora, perito, local, itens exigidos e orientação ao cliente; para perícia social, anote a diligência e o estado da instrução já atribuída. |
| `FAZER PEDIDO DE PRORROGAÇÃO` | DCB e fonte; aplique datas da cadeia POP só quando a decisão permitir confirmar o marco. |
| `ACOMPANHAR IMPLANTAÇÃO - JUD` | Obrigação imposta ao INSS, benefício e prazo de cumprimento; não transforme prazo do INSS em prazo fatal da parte autora. |
| Audiência/sessão sem átomo próprio confirmado | Use o átomo indicado pelo POP; enquanto não houver, registre os dados do evento em `ABRIR INTIMAÇÃO`. Após o evento, anote o resultado em `REALIZAÇÃO DE AUDIÊNCIA` se essa etapa fizer parte do fluxo. |

Formato-base: `[TÍTULO ADVBOX] — DJEN [id], disponibilizado em [data],
processo [CNJ]. [providência e contexto confirmado]. [PJe: documento/movimento
[id], se consultado]. Prazo: [termo] dias; vencimento [data calculada].`

Se não houver prazo, use: `Prazo não identificado no DJEN/PJe consultado em
[data].` Se houver divergência, cite os valores e a origem de cada um.

## Saída da execução de teste

Apresente uma linha por comunicação e, se houver cadeia, uma descrição por
tarefa:

| DJEN ID / data | Processo | Classificação e direção | ADVBOX: cadeia/duplicata | PJe detalhado? motivo e IDs lidos | Descrição proposta | Prazo: termo, vencimento e fonte | Decisão |
|---|---|---|---|---|---|---|---|

`Decisão` deve ser `criar após revisão`, `monitorar`, `duplicada`, `cancelada`,
`ruído` ou `revisão manual`. Neste modo, não execute criação, conclusão,
exclusão ou marcação de ID tratado.
