# Prévia diária DJEN → ADVBOX

Use a prévia para revisar uma execução diária (ou um teste) antes de criar tarefas.
**A prévia não escreve no ADVBOX nem marca comunicações como tratadas.** Só passe à
criação quando o usuário pedir a execução com escrita.

## Passos por comunicação

Para cada ID DJEN da janela (paginar `djen_search_communications` até `count`,
deduplicar por `id`; páginas grandes: use `itens` pequeno ou grave em arquivo):

1. **Escopo e direção** (`classificacao.md` §1–§2). Fora do escopo, ruído, ordem a
   terceiros e comunicação cancelada não geram tarefa.
2. **Padrão** (`classificacao.md` §4), pelo texto integral. Se o padrão for da lista
   "fora do mapa" (`fora-do-mapa.md`), o resultado é **escalar**.
3. **Nível de PJe** (`enriquecimento-pje.md` §3). Registre `PJe: não — DJEN suficiente`
   ou `PJe: sim` com os IDs lidos. Falha do conector ou documento ilegível =
   `PJe: pendente`, nunca "sem informação".
4. **Duplicata e estado atual** (`tarefas.md` §6): procure o ID DJEN no histórico do
   processo no ADVBOX (`advbox_lawsuit_report`). Para perícia social, veja se já há
   instrução/responsável (§8 do POP).
5. **Prazo** (`enriquecimento-pje.md` §5): termo da certidão, publicação, início,
   vencimento e calendário usado. Divergência ou prazo não suportado → revisão.
6. **Tarefa(s)** (`tarefas.md` §3–§5): átomo, responsável, início, final, descrição pelo
   cabeçalho e campos do tipo, anexo.

Se o PJe falhar, o prazo estiver ambíguo ou houver conflito entre fontes, a prévia
mostra a pendência e **não** apresenta uma data como confirmada.

## Saída

Uma linha por comunicação (ou por ato, quando há IDs duplicados) e, havendo cadeia,
uma linha por tarefa:

| DJEN ID / data | Processo | Padrão e direção | Nível PJe e IDs lidos | ADVBOX: duplicata / estado | Tarefa (átomo, responsável) | Início | Final e fonte | Anexo | Descrição proposta | Decisão |
|---|---|---|---|---|---|---|---|---|---|---|

`Decisão`: `criar após revisão`, `check`, `incremento`, `escalar`, `monitorar`,
`duplicada`, `cancelada`, `ruído` ou `revisão manual`. Neste modo, não execute criação,
conclusão, exclusão nem marcação de ID tratado.

## Regras práticas de revisão

- Data de audiência/perícia é evento, não prazo fatal.
- Prazo dado ao INSS não é prazo fatal da autora.
- O resultado de um ato vem do teor, não da etiqueta de movimento.
- Prazo interno só quando uma regra do SOP indicar; nunca subtraia três dias por padrão.
- Casos novos que não cabem em nenhum padrão: registre em `fora-do-mapa.md` (§2) e
  mostre na prévia como `escalar`.
