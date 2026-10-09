# Achados do teste de roteamento DJEN → PJe → ADVBOX — 06/10/2026

Este registro preserva os resultados observados no dry run de 06/10/2026. Ele complementa a metodologia permanente de [`previa-diaria.md`](../skills/victor-sop-intimacoes/references/previa-diaria.md): documenta exemplos reais usados para validar quando detalhar no PJe, como descrever tarefas e como tratar prazos que não foram localizados.

## Escopo e limites do teste

- A busca diária retornou 55 comunicações únicas por ID DJEN. Dez textos tinham duplicatas exatas; a análise agrupou esses textos, restando 45 corpos distintos.
- O resultado foi igual nas consultas `14663/PB` e `48647/PE`; `42543/PE` não retornou comunicações nessa janela.
- Foram lidos detalhes de 21 processos no PJe em uma sessão reutilizada. O ADVBOX foi consultado para conferir atividades e histórico, sem criar, editar, concluir ou excluir tarefas.
- As ferramentas MCP DJEN, cálculo de prazo e PJe falharam nesta execução. A leitura foi concluída pelos conectores locais; o PJe foi consultado em lote usando uma sessão autenticada.
- A checagem de duplicidade não foi conclusiva: a listagem de atividades não trouxe todo o histórico que apareceu no relatório do processo. Não tratar ausência na lista como prova de que a tarefa não existe.

## O que os testes confirmaram

1. **DJEN completo pode bastar.** Quando a comunicação traz o comando, o destinatário, o prazo numérico e o objeto da providência, não é necessário abrir o PJe só para repetir os mesmos dados. Use o calculador com a data de disponibilização.
2. **PJe é necessário para resolver referência ou conteúdo.** Abrir o processo quando o texto remete a “último ato”, a ID de documento/expediente, a anexo, ou não deixa claro quem deve agir, qual providência foi imposta ou se existe prazo para o cliente.
3. **Prazo de terceiro não é prazo fatal do cliente.** Em obrigação dirigida ao INSS, registrar o prazo do INSS como acompanhamento conforme POP; não atribuí-lo à parte autora.
4. **“Não localizado” é um resultado válido.** Se a intimação, certidão e anexos pertinentes do cluster foram consultados e não contêm prazo ou data de evento, registrar a ausência com o escopo da busca. Não preencher uma data por inferência.
5. **A descrição deve levar a informação útil para a tarefa.** Manter o átomo/título usado pelo escritório, identificar DJEN e CNJ, resumir a ação e citar o documento PJe quando consultado. Informar prazo calculado ou declarar que não foi identificado.

## Quando o detalhamento no PJe mudou ou completou a tarefa

| Caso observado | O que foi conferido | Resultado para o roteamento |
|---|---|---|
| `0013812-45.2026.4.05.8303` — decisão PJe `191697892` | A decisão concede 10 dias à parte autora para quesitos/assistente e dá prazo próprio ao perito para o laudo. | Roteie a providência da parte autora em 10 dias, vencimento calculado em **22/10/2026**. Não confunda com os 10 dias do perito. Descrição deve dizer que se trata de quesitos/assistente. |
| `0009302-86.2026.4.05.8303` — laudo `191981341`, certidão `192153620` | A comunicação era sobre manifestação relativa ao exame/laudo; a certidão confirmava 5 dias. | `ANALISAR LAUDO` / manifestação em **15/10/2026**. O PJe confirmou a base e o prazo; não usar uma contestação posterior como se fosse o documento intimado. |
| `0003597-10.2026.4.05.8303` — documentos `192035095` e `192035094`, certidão `192231156` | O primeiro documento legível era avaliação social/econômica; o segundo era apenas um shell HTML sem conteúdo útil. A certidão indicava 15 dias. | Prazo calculado em **29/10/2026**. A tarefa existente `ANALISAR LAUDO MÉDICO JUDICIAL` merece revisão de tipo: o relatório legível era social, não médico. Shell sem conteúdo não prova ausência do documento; buscar o anexo correspondente ou registrar a limitação. |
| `0008552-84.2026.4.05.8303` — documentos `192035103` e `192035102`, certidão `192337553` | Intimação `192337550` pedia manifestação sobre exame e relatório de inspeção; um arquivo era relatório social legível e o outro shell. Certidão indicava 5 dias. | Vencimento calculado em **15/10/2026**. A descrição deve identificar os documentos efetivamente acessíveis e marcar a análise do conteúdo médico como pendente se não houver arquivo legível. |
| `0001332-14.2026.4.05.8310` — laudo `192071427` | O documento era avaliação social relacionada a aposentadoria rural. | Classificar pelo teor do laudo, não pelo rótulo genérico do evento. Nenhum prazo numérico foi confirmado na documentação consultada. |
| `0003503-62.2026.4.05.8303` — laudo `191957739`, certidão `192186302` | Certidão do cluster indicava 5 dias para manifestação. | Tarefa de análise do laudo com vencimento em **15/10/2026**. |
| `0009634-53.2026.4.05.8303` — proposta `191813378`, certidão `192205333` | Proposta de benefício assistencial à pessoa com deficiência e certidão de 5 dias. | `ANALISAR PROPOSTA DE ACORDO`, vencimento em **15/10/2026**. A descrição deve identificar a espécie/termos propostos. |
| `0007535-13.2026.4.05.8303` — proposta `191949319` | Proposta de benefício por incapacidade temporária; certidão do cluster indicava 5 dias. IDs DJEN `747498033` e `747958590` tinham texto idêntico. | Uma análise da proposta, não duas tarefas por causa das duas comunicações idênticas; vencimento em **15/10/2026**. |
| `0001229-25.2026.4.05.8304` — proposta `191734481`, certidão `192222622` | Proposta de benefício assistencial à pessoa com deficiência e prazo de 5 dias. | `ANALISAR PROPOSTA DE ACORDO`, vencimento em **15/10/2026**. |
| `0009728-83.2026.4.05.8308` — decisão `191835167` | Decisão concedia prorrogação de 15 dias em relação à intimação de origem `186389287`. | Vincular a prorrogação à intimação original; vencimento calculado em **29/10/2026**. Não contar 15 dias da data em que a análise foi feita. |
| `0008262-40.2024.4.05.8303` | Ordem dirigida ao INSS para reativar o benefício em 15 dias; autora recebeu ciência. Histórico e documentos consultados indicavam que o benefício já havia sido reativado em 16/04/2026. | Não atribuir à autora o prazo de cumprimento do INSS. Como o fato gerador aparentemente já ocorreu, reconciliar o estado do benefício e as tarefas existentes antes de propor outra tarefa de implantação. |

### Perícias: ausência de data/hora no material consultado

Foram examinados clusters de perícia nos processos `0001253`, `0002505`, `0002988`, `0004089`, `0003853`, `0003830`, `0004397`, `0004292` e `0004988` (todos da 20ª Vara). As comunicações/mandados/certidões de nomeação consultados remetiam a data e hora aos autos, mas a data e hora do exame não apareceram nos documentos legíveis recuperados. A certidão de nomeação em 05/10 era a data da nomeação, não a data da perícia.

Consequências observadas:

- Não anunciar data/hora da perícia nem criar evento de agenda sem confirmação documental.
- Se a cadeia POP exigir aviso ao cliente, criar a tarefa de aviso como **pendente de localização da designação**, sem inventar data/hora; descrever que os documentos consultados não a informavam e continuar a busca no cluster/anexos acessíveis.
- Para perícia social, a decisão entre `INSTRUÇÃO DE PERÍCIA SOCIAL` e `DILIGÊNCIA` dependeu do histórico do ADVBOX: sem instrução anterior, sugerir instrução; quando já havia instrução/responsável ou atividade futura, usar diligência/acompanhamento conforme a cadeia POP. Não decidir só pelo evento PJe.
- O mandado HTML de um dos casos veio como shell no leitor padrão. A inspeção manual indicou avaliação domiciliar e dados socioeconômicos, mas não recuperou a data/hora. Conteúdo parcialmente legível não confirma os dados ausentes.

No ADVBOX, o processo `0004089` já tinha `AVISAR PERÍCIA MÉDICA` (DJEN `748130715`) sem data/hora confirmada no cluster. O processo `0002988` já tinha `ABRIR INTIMAÇÃO` e `DILIGÊNCIA` associados à comunicação `748139177`. Esses casos mostram por que é preciso verificar o histórico antes de propor tarefas novas.

## Quando o prazo não foi identificado

Registrar **“prazo não identificado nos documentos consultados”** quando, após conferir a comunicação e o cluster pertinente, não houver termo numérico atribuível à providência do cliente. Isso não equivale a afirmar que juridicamente não existe prazo.

| Situação | Como registrar e encaminhar |
|---|---|
| Perícia nomeada, mas designação com dia/hora ausente nos documentos lidos | Prazo fatal: não identificado. Data/hora do exame: não localizada no cluster consultado. Manter a busca/aviso pendente; sem vencimento inventado. |
| Sentença/acórdão com comando de ciência, resultado ou monitoramento, sem prazo numérico dirigido ao cliente no texto consultado | Descrever o resultado e a próxima etapa POP. Prazo fatal: não identificado na comunicação/documentos consultados. Fazer revisão manual se a medida depender de prazo recursal não explicitado. |
| Obrigação dirigida ao INSS, enquanto a autora apenas toma ciência | Registrar o prazo do INSS como acompanhamento quando a cadeia POP exigir; prazo fatal da autora: não identificado nesse ato. |
| PJe indisponível, documento em shell, extração vazia ou anexo inacessível | **PJe pendente / teor não acessível**, não “prazo inexistente”. A descrição deve dizer qual documento/ID falhou e deixar `deadline` vazio até revisão. |
| Texto “prazo legal”, contagem condicional, referência a Expedientes, ou divergência entre DJEN e certidão | Não calcular por aproximação. Registrar as fontes e valores encontrados e encaminhar para revisão manual. |

Exemplo relevante: no `0008262-40.2024.4.05.8303`, o PJe alocava 15 dias ao INSS para cumprir e 1 dia à autora para ciência. Os 15 dias não eram prazo fatal da autora. Como a reativação já constava em 16/04/2026, a ação correta era reconciliar estado e tarefas, não criar prazo da autora.

## Padrão de descrição validado para o dry run

Use uma descrição por etapa/tarefa, conservando o átomo do POP:

> `[TÍTULO ADVBOX] — DJEN [ID], disponibilizado em [data], processo [CNJ]. [Providência específica e objeto]. PJe: [tipo e ID do documento/movimento, se consultado]. Prazo: [n] dias; vencimento [data calculada].`

Exemplos do formato aplicado:

- **Analisar proposta:** `ANALISAR PROPOSTA DE ACORDO — DJEN 747498033, processo 0007535-13.2026.4.05.8303. Revisar proposta de benefício por incapacidade temporária (PJe, proposta 191949319) e orientar manifestação. Prazo: 5 dias; vencimento 15/10/2026.`
- **Manifestar sobre laudo:** `ANALISAR LAUDO — DJEN 747908099, processo 0009302-86.2026.4.05.8303. Conferir laudo do exame de 02/10/2026 (PJe, doc. 191981341) e preparar manifestação. Prazo: 5 dias; vencimento 15/10/2026.`
- **Perícia sem designação localizada:** `AVISAR PERÍCIA MÉDICA — DJEN 748130715, processo [CNJ]. Nomeação localizada; data/hora do exame não constam nos documentos acessíveis do cluster consultado. Localizar designação no PJe e orientar o cliente após confirmação. Prazo fatal/data do exame: não identificados; sem vencimento confirmado.`
- **Sem prazo da autora:** `ACOMPANHAR IMPLANTAÇÃO - JUD — DJEN [ID], processo 0008262-40.2024.4.05.8303. Ordem de reativação dirigida ao INSS; histórico indica reativação em 16/04/2026. Prazo da autora: não identificado neste ato. Conferir implantação e reconciliar tarefa existente.`

As datas de exemplo usam o calendário JFPE consultado no teste: 5 dias úteis → **15/10/2026**, 10 dias úteis → **22/10/2026**, 15 dias úteis → **29/10/2026**. A contagem parte da disponibilização/publicação aplicável conforme o calculador; não é regra fixa para qualquer data, comarca ou calendário.

## Achados sobre tarefas já existentes

- A consulta diária de atividades encontrou 14 registros ADVBOX associados a comunicações DJEN. Não houve escrita.
- No processo `0008885` (comunicação `748075389`), havia tarefa `APRESENTAR CONTRARRAZOES`, mas o acórdão de 2º grau negava o recurso da parte autora e apenas mencionava contrarrazões anteriores do INSS. O rótulo parece incompatível com o ato atual; revisar para análise recursal/aviso de resultado, sem criar contrarrazões automaticamente.
- No processo `0003597`, tarefa existente dizia “laudo médico”, mas o documento legível era avaliação social/econômica. Ajustar tipo com base no conteúdo, não apenas no nome da tarefa ou evento.
- No processo `0001253`, o relatório do processo mostrava `ABRIR INTIMAÇÃO` para o evento atual, embora a listagem de atividades usada na deduplicação não o retornasse. A consulta de atividades por janela, sozinha, não garante deduplicação completa.
- Decisões de 2º grau sem prazo numérico explícito na comunicação geraram encaminhamento para análise/aviso/monitoramento conforme resultado, não um vencimento inventado. Confirmar prazo processual em fonte apropriada antes de registrar deadline.

## Regra prática resultante

1. Faça primeiro a classificação e o roteamento com o texto integral DJEN.
2. Abra o PJe apenas quando ele puder resolver uma referência, um conteúdo/anexo, o destinatário da ordem, um prazo ambíguo ou dado de perícia ausente.
3. Leia o cluster e os anexos; registre IDs, conteúdo realmente acessível e limitações de extração.
4. Calcule somente o prazo confirmado e dirigido à parte autora. Se não localizado, deixe o vencimento vazio e explique o que foi consultado.
5. Antes de propor nova tarefa, compare ID DJEN e histórico completo do processo no ADVBOX; uma lista parcial não prova inexistência.
6. Mantenha o resultado como prévia até revisão. Este teste não criou nem alterou tarefas.

## Artefato bruto

A resposta DJEN bruta ficou em `/tmp/victor-djen-daily-2026-10-06.json` durante a execução. Ela não é a fonte deste registro durável e pode não permanecer no ambiente; para reprodução, buscar novamente a janela e guardar a exportação junto ao relatório.
