---
name: elisandra-sop-intimacoes
description: Intimações da Elisandra Becker (OAB 61216/RS): busca no DJEN, enriquecimento no eproc (JFRS/JFSC) e rascunho de tarefa (título, descrição, datas, anexos) para aprovação no chat. Use para revisar as publicações da semana ou preparar tarefas a partir de uma intimação.
---

# SOP de intimações: DJEN → eproc → rascunho de tarefa (Elisandra)

A Elisandra decide e cria as tarefas pelo chat, sem mapeamento fixo de tarefas. Esta skill
traz o fluxo de dados e o formato do rascunho; **não cria nada** em sistema externo. O
destino das tarefas (Tramitação Inteligente, MCP próprio) ainda não está ligado: até lá o
resultado é o rascunho abaixo, para a Elisandra aprovar.

## Regras que não mudam

1. **Nunca invente** data, prazo, perito, local ou resultado. Sem dado, escreva "não
   identificado" e onde procurou.
2. **Classifique pelo teor** do ato (documento/descrição do evento), não pelo título da
   comunicação.
3. **Prazo**: o `deadlineEnd` da fila do eproc (`eproc_list_notices`); sem ele, os dias do
   evento "Expedida/certificada a intimação eletrônica" ou do documento, e diga que a data
   final depende da ciência. Prazo pendente sem ciência não tem data final.
4. **Dados pessoais mínimos** na descrição: número do processo e fatos do ato; o nome do
   periciando só quando o ato exige.
5. Uma sessão eproc por vez: o eproc recusa um segundo login da mesma conta
   ("Usuário logado como…"). Não rode buscas em paralelo.

## Fluxo

1. **DJEN**: `djen_search_communications` com a janela de datas (`inicio`/`fim`, ISO) e
   `numeroOab: "61216"`, `ufOab: "RS"`, sem tribunal. Essa busca traz TRF4 (eproc JFRS/JFSC),
   TJRS, TRT4, TRF3 e STJ. Se a busca por OAB falhar, use `nomeAdvogado: "ELISANDRA BECKER"`
   (testado em 19/10/2026: mesmas comunicações). Só STJ: acrescente `tribunal: "STJ"` e
   `meio: "D"`. Pagine até `count`, deduplique por `id`, use `compact=true`.
2. **Portal**: pelo número CNJ (`portals.for_process`): origem 71xx = `trf4-1g-rs`,
   72xx = `trf4-1g-sc`; justiça estadual 8.21 = `tjrs-1g`. TRT4 e TRF3 não têm enriquecimento
   ligado: informe só o texto DJEN.
3. **Eproc**: `eproc_get_process` (`scope=page`) e `eproc_list_notices` (`open_deadlines`
   e `pending`). Veja `references/enriquecimento-eproc.md`.
4. **Rascunho**: `references/rascunhos.md` (tipos de ato, descrição, prazo, anexos).
5. **Anexos**: `eproc_get_process` com `documentIds` e `save=true` guarda o documento na
   pasta compartilhada (HTML do eproc vira PDF) e devolve `savedAs`.

## Referências

| Arquivo | Para quê |
|---|---|
| `references/enriquecimento-eproc.md` | Como casar a intimação com o evento e o documento; onde está cada dado |
| `references/rascunhos.md` | Tipos de ato, modelo de descrição, prazo e anexos por tipo |
