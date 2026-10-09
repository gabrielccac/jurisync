# Classificação das intimações

Use esta referência para decidir, para cada comunicação DJEN, se ela gera tarefa e
de qual **padrão** (P1…P19) ela é. Cada padrão aponta para `enriquecimento-pje.md`
(quão fundo ler no PJe) e para `tarefas.md` (a tarefa que ele gera).

Base empírica: 50 comunicações únicas de 06/10/2026 (OAB 14663/PB), cada família
mapeada no PJe com as movimentações vizinhas e os documentos do cluster
(verificado em 08/10/2026). Distribuição viva de 28–29/09/2026 (203 itens):
sentença 46, perícia designada 26, cálculos 21, contrarrazões 13, laudo 12,
acordo 11, emenda 6, indefinido 68.

**Regra de precedência:** classifique pelo **teor do ato**, nunca pela etiqueta de
movimento do PJe nem pelo título da comunicação. Caso real: o PJe etiquetou como
"Julgado improcedente o pedido" uma sentença cujo dispositivo diz "julgo
PROCEDENTES" (0004674-54).

## 1. Gate de escopo

| `sigla_tribunal` | Ação |
|---|---|
| `TRF5` (1º grau/JEF, `pje1g.trf5.jus.br`) | Seguir esta referência |
| `TRF1`, `TJPE`, `TRT6` | Fora do escopo: tratar manualmente. Portais TRF1 são proibidos nesta skill; TJPE não tem calendário JFPE |

Amostra de 06/10: TRF5 42, TJPE 5, TRF1 2, TRT6 1. O credenciamento do cliente
(certificado A1 + TOTP) vale só para `trf5-1g`.

## 2. Gate de direção

Só vira tarefa se `advogados[]` contém os nossos (`numero_digits` + `uf_oab`:
`14663/PB`, `42543/PE`, `48647/PE`) e o polo intimado é o do cliente
(`destinatarios[].polo`).

- Ordem para terceiros (ex.: INSS) → monitorar, não criar.
- Petição/manifestação **nossa** → compliance (concluir a tarefa correspondente), não
  nova tarefa.
- Petição/manifestação **deles** → ler: impõe prazo a nós? (ex.: contestação → §6);
  senão monitorar.
- Intimação por referência ("último ato", "ID de origem") → buscar o ato antes de
  classificar.
- Comunicação cancelada (`data_cancelamento` / `ativo=false`) → rechecar antes de criar.

## 3. Ruído (nunca cria tarefa)

`Decorrido prazo` (de terceiros ou sem ordem nossa), juntada administrativa de
certidão/e-mail/ofício, `Conclusos`, `Recebidos`/`Devolvidos os autos`, `Remetidos`
(interno; exceto "em grau de recurso" para Turma Recursal = acompanhar), despacho de
mero expediente sem ordem, `Arquivado Definitivamente`, `Baixa Definitiva`,
`Distribuído`, `Classe retificada`, `Pedido de Sustentação Oral` (informativo).

## 4. Padrões

"Cover" = texto DJEN curto (≤ ~700 caracteres) só com cabeçalho e frase padrão: o
comando pode estar na certidão (`djen_get_certidao_text`) ou no PJe.

| # | Padrão | Sinal no texto DJEN / `tipo_documento` | Tarefa (`tarefas.md`) | Nível PJe |
|---|---|---|---|---|
| P1 | Laudo social a manifestar | "manifestem-se sobre o(s) laudo(s) pericial(is)"; laudo é estudo social | §2 `ANALISAR LAUDO SOCIAL JUDICIAL` | N3 |
| P2 | Laudo médico a manifestar | mesmo sinal; laudo é pericial médico | §2 `ANALISAR LAUDO MÉDICO JUDICIAL` | N3 |
| P3 | Perícia: nomeação / aviso | "perícia designada, conforme data e hora registradas nos autos" | §7 (médica) / §8 (social) | N2 |
| P4 | Mandado de perícia social | `MANDADO`: "determinada a realização de perícia social na residência… constantes na petição" | §8 `DILIGÊNCIA` | N2 |
| P5 | Despacho: aceite de acordo | "informar se aceita o acordo proposto… Prazo: N dias" (texto curto, completo) | workflow 12383 `ANALISAR PROPOSTA DE ACORDO` | N1 + proposta |
| P6 | Proposta de acordo | "manifestar-se sobre a proposta de acordo… prazo constante no menu Expedientes" | workflow 12383 `ANALISAR PROPOSTA DE ACORDO` | N1 + proposta |
| P7 | Emenda / requisitos | `Intimação para Emendar` / ato ordinatório: "fica a parte autora intimada para, no prazo de N dias, anexar…" | §1 `CUMPRIR ATO ORDINATÓRIO` | N1 |
| P8 | Dilação sobre ato anterior | "defiro… dilação de prazo… acerca da decisão/despacho de ID.N" | §1, para o ato referido | N2 |
| P9 | Sentença procedente | "julgo procedente" | §3.1 (cadeia completa) | N1 |
| P10 | Sentença improcedente / extintiva | "julgo improcedente"; "extingo o processo sem resolução do mérito" | §3.2 / §3.3. **Parcial → fora do mapa FM-01** | N1 |
| P11 | Homologação de acordo com obrigação ao INSS | "homologo… a transação… INSS cumpra a obrigação de fazer no prazo de N dias" | **Fora do mapa FM-02** | N1 |
| P12 | Cálculo a apresentar | "apresentar os cálculos dos valores retroativos" | §4 `DILIGÊNCIA` + `ANALISE/MANIFESTAÇÃO DOS CALCULOS` | N2 |
| P13 | Decisão da Turma Recursal | `RECURSO INOMINADO`, "Turma Recursal" | §9 só se **colegiado e definitivo**; monocrática ou diligência → **FM-04/05/06** | N0 |
| P15 | Despacho que pede endereço p/ perícia social | "determino a realização da perícia social… trazer aos autos endereço atualizado" | §1 `CUMPRIR ATO ORDINATÓRIO` (ou check se já cumprido) | N1 |
| P16 | Cumprimento de sentença / implantação | classe "Cumprimento de Sentença contra a Fazenda"; despacho sobre INSS | §3.1 `ACOMPANHAR IMPLANTAÇÃO - JUD` | N1 |
| P17 | Perícia nomeada com quesitos facultativos | "nomeio o perito… facultada a indicação de assistente e quesitos no prazo de N dias" | **Fora do mapa FM-03** | N1 |
| P18 | Sigilo | "Documento protegido por sigilo" | Segue o padrão do ato, identificado pelo PJe autenticado | N2 |
| P19 | Famílias do POP sem caso na amostra | contrarrazões (§5), contestação do INSS (§6), embargos (§10), acórdão colegiado (§9), DCB/prorrogação (§11), audiência/sessão | Ver `tarefas.md` | N2–N3 |

Notas:
- P5 e P6 geram a mesma tarefa; só mudam a fonte do prazo (texto DJEN ou certidão).
- P1/P2: o tipo vem do conteúdo do laudo (social = assistente social; médico = perito
  médico), não do título do movimento.
- Mesmo texto com IDs DJEN diferentes = **um ato, uma cadeia**. Registre todos os IDs
  no comentário.

## 5. Gatilhos por movimento do PJe (quando se parte do PJe)

| Movimento/documento PJe | Padrão / seção |
|---|---|
| `Expedida/certificada a comunicação eletrônica :: Intimação`, `Publicado Intimação` | Etapa 0 + ler o teor |
| `Ato ordinatório praticado` com ordem/prazo | P7 / §1 |
| `Juntada de Petição de laudo pericial` | P1/P2 / §2 |
| `Julgado procedente…` + Sentença | P9 (confirmar pelo teor) |
| `Julgado improcedente` / `Extinto o processo…` + Sentença | P10 (confirmar pelo teor) |
| `Intimação para Contrarrazões` | §5 |
| `Juntada de Petição de contestação` (do INSS) | §6, só se abrir prazo para nós |
| `Acórdão` + Relatório + Voto | §9 (ler: definitivo × retorno × monocrático) |
| Decisão sobre embargos | §10 (perspectiva do cliente) |
| `Expedição de Intimação para Obrigação de Fazer` | Etapa 0 + acompanhar o cumprimento (prazo do teor) |
| `Certidão de Trânsito em Julgado` | Fechamento: conferir RPV/implantação em curso |
| Nossa própria petição (recurso, manifestação, cumprimento) | Compliance: concluir a tarefa aberta |

## 6. Casos que exigem leitura do teor

Regras não bastam; leia o ato (ou use o LLM-juiz com o texto e responda em JSON):

1. **Acórdão:** definitivo (cadeia cheia) × retorno (só o ato ordenado) ×
   **monocrático** (art. 932 CPC; ainda impugnável: não é definitivo).
2. **Embargos:** quem opôs × acolhido/rejeitado → perspectiva do cliente.
3. **MS:** concedido/denegado no dispositivo.
4. **DCB:** data expressa × mera permissão futura (muda fatal e início).
5. **Sentença:** resultado pelo dispositivo; extinção parcial → fora do mapa.
6. **Etiqueta × texto:** quando divergirem, vale o texto e registre a divergência em
   `fora-do-mapa.md`.
