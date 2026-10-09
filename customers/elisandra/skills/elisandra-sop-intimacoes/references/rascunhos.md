# Rascunho de tarefa por tipo de ato

Cada rascunho traz: **título**, **descrição**, **início**, **final** (ou "sem data final")
e **anexos** (nomes devolvidos em `savedAs`). Mostre ao usuário para aprovação; nada é
criado automaticamente.

## Modelo de descrição

```
{TIPO DO ATO} | {CNJ} | {portal} | DJEN {id} de {data}
Ato: {o que foi decidido/designado, em uma ou duas frases}
Dados: {data/hora/local/perito, prazo e consequência, valores, conforme o tipo}
Prazo: {N dias; final DD/MM/AAAA | sem data final (ciência pendente) | sem prazo}
Fontes: eventos {ids}, documentos {nomes}
Pendências: {o que não foi encontrado e onde se procurou}
```

## Tipos de ato

| Ato (sinais) | Tarefa proposta | Prazo | Anexo |
|---|---|---|---|
| **Perícia designada** (evento "Perícia designada") | Avisar/acompanhar a perícia | Evento, não prazo: data e hora da perícia | Nenhum; coloque data, hora, local e perito na descrição. O ATOORD só se o usuário pedir |
| **Sentença** ("Julgado procedente/improcedente…", documento SENT) | Analisar sentença / recurso | Dias da fila (ex.: 10); final só quando a ciência constar | A sentença (SENT) |
| **Cálculos / remessa à contadoria** ("Remetidos os Autos", INF, CALC, HISTCRE) | Conferir cálculos e decidir impugnação | 15 dias para impugnar (do INF); ausência = concordância tácita | INF e a planilha CALC |
| **Despacho com ordem** (DESPADEC) | Cumprir a ordem do despacho | Dias do despacho ou da fila | O despacho, se houver ordem à parte autora |
| **Acórdão / Turma Recursal** | Analisar acórdão / recurso | Dias da fila (ex.: 15) | O acórdão (documento ligado à intimação, não o voto antigo) |
| **Petição ou quesitos juntados pela própria parte** | Nenhuma (conferir) | — | — |
| **Lista de distribuição, pauta de julgamento, ata de sessão** | Ruído/monitorar | — | — |
| **Sigiloso** ("Processo sigiloso…") | Abrir os autos autenticado e seguir o ato resultante | Do ato resultante | Do ato resultante |

Tipos não previstos: registre em `fora-do-mapa.md` desta pasta (crie quando houver o
primeiro caso) com a evidência e pare.
