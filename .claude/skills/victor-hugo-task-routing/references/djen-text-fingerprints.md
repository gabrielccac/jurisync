# Fingerprints texto-DJEN → seção POP (fonte primária, sem login)

Distribuição live 28–29/09/2026 (`14663/PB TRF5`, 203 itens):
`sentenca 46, pericia_designada 26, calculos 21, contrarrazoes 13,
laudo 12, acordo 11, emenda 6, indefinido 68` (→ `detalhar_pje`).

## Gatilhos (texto da comunicação basta)

| Texto DJEN contém | Família | Seção POP |
|---|---|---|
| `laudo(s) pericial(is)`, `manifestem-se sobre o(s) laudo(s)` | laudo | §2 (baixar laudo; viewer-shell → anexos ADVBOX) |
| `contrarrazões`, `contrarrazoar` | contrarrazoes | §5 (`6283998`; JEF: recurso inominado) |
| `cálculos`, `manifestem-se sobre os cálculos` | calculos | §4 (`430576`, anexar planilha) |
| `sentença`, `julgo procedente`, `julgo improcedente`, `extinto o processo` | sentenca | §3 (ler dispositivo: 3.1/3.2/3.3/MS) |
| `designo perícia`, `perícia médica/social` | pericia_designada | §7/§8 (§8 condicional ao histórico ADVBOX) |
| `acordo`, `proposta de acordo` | acordo | §? (conferir cadeia; nunca analogia) |
| `emendar`, `emenda à inicial`, `prazo inicial`, `ato ordinatório` | emenda | §1 (`265427`) |

## Direção (gate 2, sem PJe)

Só vira tarefa se `advogados[]` contém os nossos
(`numero_digits` + `uf_oab`: `14663/PB`, `42543/PE`, `48647/PE`)
e o polo intimado é o do cliente (`destinatarios[].polo`).
Ordem para terceiros → monitorar, não criar.

## `detalhar_pje=true` (PJe secundário obrigatório)

- `último ato`, `ID de origem`, texto < 200 chars sem verbo mapeado
- `segredo de justiça` / `sigilo` (conferir via PJe autenticado)
- `acórdão`, `embargos de declaração`, `DCB` (casos LLM-juiz: definitivo-vs-retorno, perspectiva, expressa-vs-implícita)
- `data_cancelamento` presente ou `ativo=false` (re-checar antes de criar)
- prazo fatal a calcular com documento, laudo/cálculo anexo necessário

Fluxo: `fetch_process(numero_processo_mascara, portal="trf5-1g")`
+ até 5 `documentIds` do inventário → teor → reclassifica → completa cadeia.
Idempotência segue por `id` DJEN, nunca por processo.
