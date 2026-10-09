# Fora do mapa: registro vivo

Tudo que o SOP não prevê, mais as divergências vistas em tarefas que já existem no
ADVBOX. Este arquivo cresce: cada caso novo entra aqui como `novo`; quando o escritório
decide a regra, ela é **promovida** para `classificacao.md` e `tarefas.md` e a entrada
é fechada. O agente propõe; uma pessoa aprova. Nada daqui é aplicado por analogia.

## 1. Resultados possíveis para uma comunicação

| Resultado | Quando | O que o agente faz |
|---|---|---|
| **Criar** | Tem padrão e dados completos | Propõe a tarefa (ou cria, com escrita habilitada) |
| **Check** | Nada a criar; falta só conferir algo | Lê PJe/ADVBOX, anota o resultado na nota de roteamento. Ex.: a petição que cumpre o despacho já está no PJe |
| **Incremento** | Já existe tarefa **pendente** para o ato | Acrescenta informação com `advbox_update_task` (`appendComments`, anexos). O ADVBOX substitui a tarefa e devolve um `postId` novo |
| **Escalar** | Sem padrão ou padrão ambíguo | Cria entrada `novo` aqui, com a evidência, e para |
| **Monitorar / ruído / duplicada** | Ordem para terceiros, ruído ou mesmo ato já tratado | Não cria |

## 2. Como registrar uma entrada

| Campo | Conteúdo |
|---|---|
| ID | `FM-nn` |
| Padrão | Nome curto |
| Sinal | Texto ou documento que o identifica |
| Evidência | IDs DJEN, CNJ, IDs PJe lidos |
| Estado | `novo` → `em análise` → `decidido` → `promovido` |
| Regra do escritório | Preenchida só quando decidida (átomo, responsável, prazo, anexo) |
| Criação interina | O que o agente faz até haver regra (normalmente: nada) |

Dados pessoais mínimos: use CNJ e IDs, não nomes de partes nem de menores.

## 3. Entradas

| ID | Padrão | Sinal / evidência | Estado | Criação interina |
|---|---|---|---|---|
| FM-01 | Sentença com extinção parcial + improcedência | 0013197-55 (DJEN 747530824, 747936611): parcelas até 31/01/2024 extintas por coisa julgada; restante improcedente; certidão `192164678`: 10 dias (autora e INSS). O SOP tem só procedente, improcedente e extintiva | novo | Escalar. Candidata: §3.2 com `ANÁLISE RECURSAL` (10 dias) |
| FM-02 | Homologação de acordo com obrigação ao INSS | 0011165-80 (DJEN 747819674): sentença `192132639` homologa transação; INSS cumpre em 30 dias; certidão `192288129`: autora sem prazo, INSS 0; intimação de obrigação de fazer `192288122` ("prazo legal"); trânsito em 05/10 | novo | Escalar. Candidata: `AVISAR RESULTADO` + `ACOMPANHAR IMPLANTAÇÃO-JUD` + `ACOMPANHAR RPV/PCR` (+90d do trânsito) |
| FM-03 | Perícia nomeada com quesitos/assistente facultativos | 0013812-45 (DJEN 747559681, 747878463): decisão nomeia perito; assistente e quesitos "facultada… no prazo de 10 dias"; data da perícia ainda não designada | novo | `ABRIR INTIMAÇÃO` (§7); a tarefa de quesitos aguarda a regra |
| FM-04 | Decisão monocrática da Turma: "nego seguimento" | 0010472-15 (DJEN 747638715, 747640629, 747648232): a decisão ainda não aparece na linha do tempo do PJe; recurso cabível, prazo legal | novo | Escalar com o texto DJEN; sem tarefa |
| FM-05 | Decisão monocrática da Turma: "nego provimento" | 0008885-36 (DJEN 748075389): julgamento monocrático (art. 932 CPC) que mantém a extinção por coisa julgada. O ADVBOX já tem `ACÓRDÃO IMPROVIDO` e `ANÁLISE RECURSAL` | novo | Escalar. Confirmar se monocrático equivale a acórdão definitivo para o §9.2 |
| FM-06 | Conversão em diligência pela Turma (esclarecimentos do perito) | 0000933-06 (DJEN 748136912): Turma pede esclarecimentos ao perito e manda intimar as partes depois; sem prazo ao cliente agora | novo | `ABRIR INTIMAÇÃO` + `DILIGÊNCIA` `25864` informando o estado |
| FM-07 | Átomo `ANALISAR PROPOSTA DE ACORDO` | Workflow `12383` resolve para `646681`; o SOP antigo só listava o workflow | decidido (documentado em `tarefas.md`) | — |
| FM-08 | Cálculo que a autora produz | 0012824-58: `ANALISE/MANIFESTAÇÃO DOS CALCULOS` + `DILIGÊNCIA CUMPRIDA` já usados no ADVBOX (0008262-40) | decidido por precedente; confirmar com o escritório | Seguir `tarefas.md` §4 |

## 4. Observações de casos existentes que fogem do previsto

Tarefas já criadas no ADVBOX que divergem do que a leitura do PJe sustenta. Servem para
revisar o classificador e as tarefas, não como acusação:

| ID | Caso | Observação |
|---|---|---|
| OBS-01 | 0002988-24 | `DILIGÊNCIA` registra "data designada: 05/10/2026 às 17:30". Nenhum documento do PJe lido traz essa data: as certidões dizem que a perícia social está "ainda a ser designada". A data parece ser a da publicação. Verificar e corrigir |
| OBS-02 | 0009634-53 | Duas tarefas para a mesma proposta (`ANALISAR PROPOSTA DE ACORDO` e `MANIFESTAR SOBRE A PROPOSTA DE ACORDO`, ambas 07/10) e duas `ANALISAR LAUDO SOCIAL JUDICIAL` (07/10 e 27/10). Possível duplicata |
| OBS-03 | 0004674-54 | O PJe etiqueta a sentença como "Julgado improcedente o pedido"; o dispositivo diz "julgo PROCEDENTES". A cadeia do ADVBOX segue o texto (procedente) |
| OBS-04 | 0008885-36 | `ACÓRDÃO IMPROVIDO` lançado para uma decisão monocrática (ver FM-05) |
| OBS-06 | 0013812-45 | A aba Expedientes mostra a decisão de 05/10 (indeferimento de tutela) com "1 dia" para a autora e o INSS: padrão do sistema, não prazo real. A nomeação de perícia (10 dias para quesitos) é outro ato |
| OBS-05 | 0013812-45 | `ABRIR INTIMAÇÃO` com descrição estruturada "POP §7 \| … \| Teste controlado: somente a etapa inicial foi criada". É o formato de cabeçalho adotado em `tarefas.md` §4 |

## 5. Pendências de validação

1. `djen_calculate_deadline` aplica a regra de publicação da certidão? (Conferência
   manual de 06/10: 5 → 15/10, 10 → 22/10, 15 → 29/10.) **Atenuada em 09/10/2026:** o
   prazo da parte autora é o `limite` da aba Expedientes; a calculadora fica como
   conferência. Ainda falta comparar as duas em vários casos (visto: 0010472-15, 10 dias
   úteis dão 19/08 e o PJe mostra 20/08).
2. Emenda de 0012920-39: sem certidão na linha do tempo e **sem linhas na aba
   Expedientes**; o prazo de 10 dias vem só do texto DJEN.
3. Fonte da data das perícias sociais depois da designação (perito/executante).
4. ADVBOX: edição **resolvida** (08/10/2026, capturada no navegador e validada ao vivo):
   `GET /posts/create?id=<post>&edit=true` traz o formulário preenchido e `POST /posts`
   com o corpo de criação mais `editing=<post>` e o `create_token` do formulário; a
   resposta traz `kanban.add` (post novo) e `kanban.remove` (o antigo). Anexo novo: `POST
   /s3` e `file[]`; remover anexo: `POST /s3` com `_method=DELETE` e o código do arquivo.
   Falta capturar: (c) listar tarefas **concluídas** (o filtro `completed` da listagem não
   muda o resultado). A tarefa de teste (lawsuit `18175173`, responsável CONTROLADORIA,
   comentário "TESTE DE INTEGRAÇÃO") está hoje no post `285556799` depois de duas edições
   de teste; pode ser excluída.
4a. Validar `djen_search_communications(compact=true)` ao vivo quando o DJEN voltar.
5. Perícia do tipo médica × social em 0004089-96 (o ADVBOX tem `AVISAR PERÍCIA MÉDICA`;
   o mandado do PJe é de perícia social).
