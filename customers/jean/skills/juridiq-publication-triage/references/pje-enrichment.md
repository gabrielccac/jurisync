# Consulta de teor no PJe (via API)

Como completar as tarefas provisórias ("verificar teor no PJe") consultando o processo pela ferramenta `pje_get_process`, sem causar dano processual.

## A regra que não pode ser violada

**A leitura precisa ser sempre passiva.** Nunca chamar qualquer ferramenta ou fluxo que tenha como efeito dar ciência de uma intimação pendente ou disparar prazo — isso inclui, se algum dia existir, qualquer endpoint equivalente a "abrir expediente" ou "confirmar ciência".

`pje_get_process` é seguro para esse fim: seu retorno é descrito como "movements, not confirmed publications" — ou seja, lê a timeline pública de movimentos e documentos já juntados, sem interagir com nada pendente de ciência. Isso é confirmado pelo campo `diagnostics` retornado em cada chamada, que mostra a navegação real feita internamente: exclusivamente `/pje/Processo/ConsultaProcesso/listView.seam` e `/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam`, nunca uma rota de Expedientes.

**Sempre confira o `diagnostics` da resposta.** Se ele mostrar qualquer rota fora de `ConsultaProcesso`, ou se a resposta da ferramenta trouxer algo que pareça exigir confirmação/ciência, **pare e reporte** — mantenha a tarefa provisória e sinalize no resumo, sem prosseguir.

## Pré-requisitos

1. Ferramenta `pje_get_process` disponível no conector `pje`. A autenticação é feita internamente pelo adaptador; não há sessão de navegador nem login manual do agente.
2. **Nunca** digitar CPF, senha, certificado ou qualquer credencial em nenhuma interface. Se a ferramenta retornar erro de autenticação, é um problema de configuração do adaptador — reporte, não tente contornar manualmente.

A consulta funciona igualmente em execuções interativas e em execuções agendadas na nuvem, sem depender de navegador do usuário.

## Aviso conhecido — encoding

Em validação (12/09/2026), o texto retornado por `pje_get_process` (tanto em campos estruturados como `judicial_body`/`subjects` quanto no texto extraído de documentos) veio com **acentuação corrompida** (ex.: "Judiciário" → "Judici�rio"). Até que isso seja corrigido no adaptador:

- **Não copiar o texto bruto extraído diretamente** para descrição de tarefa ou resumo sem antes verificar/normalizar a acentuação.
- Se não for possível confirmar que o encoding foi corrigido, sinalizar no resumo que a descrição da tarefa pode conter caracteres corrompidos, para revisão humana.
- Re-testar com o processo de exemplo `1053017-92.2025.4.01.3200` (documentos `2226603925`, `2226604785`, `2251061353`) antes de remover este aviso.

## Fluxo

1. Chamar `pje_get_process(processNumber, portal="both", scope="page")` para obter partes, movimentos e inventário de documentos (sem texto ainda).
2. Localizar, na timeline retornada, o movimento correspondente ao ato que gerou a intimação (mesma data da publicação, mesmo tipo de ato).
3. **Reunir todos os documentos do mesmo cluster de movimento** — não parar no primeiro documento anexado. Um mesmo evento processual (ex.: perícia agendada) costuma gerar mais de um documento na mesma data/horário próximo (ex.: uma Certidão automática + um Ato ordinatório), e cada um traz uma parte da informação. Priorize, nessa ordem:
   - documentos no mesmo dia e mesmo horário (ou horário muito próximo) do movimento identificado;
   - documentos imediatamente anteriores/posteriores na timeline, quando referenciarem o mesmo ato (confira pelo texto do movimento, ex. "ID do último ato proferido nos autos" dentro de uma Intimação aponta para o documento que ela está comunicando);
   - nunca presuma relação entre documentos de datas distantes só porque o tipo é parecido.
4. Chamar novamente `pje_get_process` incluindo os `documentIds` relevantes (até 5 por chamada) para obter o texto extraído desses documentos.
5. Se a informação buscada ainda não aparecer em nenhum dos documentos do cluster, e não houver mais candidatos plausíveis no mesmo dia, trate como `insufficient` — não invente o dado. Mantenha a tarefa provisória e registre no resumo o que faltou.

## O que extrair, por tipo de ato

**Designação de perícia médica ou social** — o caso mais comum nas intimações da Central de Perícias. A informação normalmente está dividida entre dois documentos do mesmo cluster:

- **Certidão automática de marcação de perícia**: geralmente traz data, hora, nome do perito e especialidade.
- **Ato ordinatório / intimação de perícia**, publicado no mesmo movimento: geralmente traz o **endereço completo do local** (clínica/consultório/endereço da Central de Perícias), instruções ao autor (documentos, laudos e exames a levar) e, por vezes, o próprio nome do perito e a data/hora repetidos.

Busque os dois antes de decidir que a informação está incompleta. O nome do representante legal da parte autora não precisa ser buscado no texto do documento — já vem estruturado em `parties.active` na resposta principal do `pje_get_process`.

Com esses dados, complete a descrição da tarefa provisória (`Comunicação Perícia Médica Federal` ou `Comunicação Perícia Social`) com data, hora, local e perito, remova a marca de provisória e crie o evento na agenda (Fase 5 do SKILL.md). Sem data e hora confirmadas em nenhum dos documentos do cluster, não crie evento.

**Laudo pericial juntado** — leia a conclusão para decidir entre impugnação e manifestação favorável. Os critérios de favorabilidade estão nas skills `manifestacao-laudo-bpc` e `manifestacao-laudo-medico-bpc`; em caso de dúvida real sobre a conclusão, mantenha a tarefa como impugnação provisória e sinalize no resumo, porque impugnar por engano é reversível e deixar de impugnar não é.

**Despacho ou decisão com prazo** — identifique exatamente o que foi determinado à parte autora e o prazo, e reescreva a descrição da tarefa com esses dados.

## Ao terminar

- **Altere a tarefa existente com `juridiq_update_task` — nunca crie outra nem exclua a provisória.** A mesma chamada aceita `description`, `title`, `columnId`, `responsiblesId`, `tags`, `priority` e datas, então mesmo uma troca completa de modelo se resolve no lugar, preservando histórico e vínculo com o processo.
- Remova a marca "TAREFA PROVISÓRIA — VERIFICAR TEOR NO PJe" do topo da descrição e ajuste `finalDate` se o ato indicar outro prazo.
- Se o ato revelou ser outro tipo (redesignação, laudo juntado, despacho com prazo), consulte o modelo ao vivo e leve na mesma chamada o `title`, `columnId`, `responsiblesId` e `tags` corretos.
- Registre no resumo quais tarefas provisórias foram resolvidas e quais permaneceram, incluindo os casos em que a informação buscada não apareceu em nenhum documento do cluster.
