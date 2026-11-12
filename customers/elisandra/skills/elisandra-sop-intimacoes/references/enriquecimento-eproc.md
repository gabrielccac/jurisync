# Enriquecimento no eproc (JFRS, JFSC e TJRS)

Verificado ao vivo em 09/10/2026 (JFRS e JFSC) e 14/10/2026 (TJRS) com as credenciais da Elisandra.

## Casar a intimação com o evento

- A linha do tempo (`timeline`) tem `dateTime`, `label`, `description` e `documents`
  (`id`, `name`). A comunicação DJEN aparece como os eventos **"Disponibilizado no DJEN"** e
  **"Publicado no DJEN"**; a data de `data_disponibilizacao` casa com o primeiro.
- O ato que gerou a intimação é o evento **imediatamente anterior** (mesmo dia ou até 3
  dias antes), normalmente junto de "Expedida/certificada a intimação eletrônica". O
  documento do ato está em `documents` desse evento. **Não use "o documento mais recente"**:
  numa intimação de acórdão o mais recente era um voto de agosto.

## Onde está cada dado

| Dado | Onde |
|---|---|
| Data, hora, local e perito da perícia | **Descrição do evento** "Perícia designada" (o ATOORD só manda consultar o evento) |
| Prazo em dias | Fila (`deadlineEvent`: "… 15 dias") |
| Data final do prazo | Fila `open_deadlines` (`deadlineEnd`); `pending` não tem data (ciência aberta) |
| Resultado da sentença | Rótulo do evento ("Julgado improcedente o pedido") e dispositivo no documento SENT |
| Conclusão do laudo | Texto da sentença/laudo (ex.: "sem incapacidade atual") |
| Cálculos e prazo de impugnação | Documentos INF (prazo, consequência), CALC e HISTCRE (planilhas) |
| Ordem do despacho | Documento DESPADEC |

## Limites

- Intimação ausente das filas: já respondida ou sem prazo aberto; diga isso.
- Processos de TRT4 e TRF3: sem enriquecimento ligado. O TJRS (`tjrs-1g`) lê fila, linha do tempo
  (paginada em 100 eventos) e documentos; a paginação usa `processo_selecionar_pagina`.
- A escala `scope=all` não existe no eproc; `page` devolveu o histórico completo nos casos
  testados (`complete: true`).
