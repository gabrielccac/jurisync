# Enriquecimento no PJe: quão fundo ler

Princípio: o texto DJEN primeiro; o PJe só para o que falta (comando, prazo, anexo,
referência). Registre sempre o que foi lido (IDs de movimento e documento) e o que
**não** foi encontrado e onde se procurou.

Ferramentas: `pje_get_process` (portal `trf5-1g`, `scope: page` ou `all`: linha do tempo,
inventário e a aba **Expedientes**), `pje_read_expediente_ato` (o ato de uma comunicação
da aba, com seus anexos) e `pje_read_documents` (só os documentos pedidos, até 5 por chamada, ids do
inventário; devolve texto, estado de extração e, nas certidões, os `expedientes`). Verificado em 08/10/2026: `scope: all`
devolveu a mesma linha do tempo de duas páginas dos casos testados; não há movimentos
mais antigos escondidos.

## 1. Como um ato se liga aos documentos

Cada ato aparece como um cluster de movimentos com a mesma data:

1. **Ato** — despacho, sentença, intimação, mandado.
2. **Intimação** — o documento cujo texto o DJEN publica.
3. **Certidão de Intimação** — mesmo movimento. A seção **EXPEDIENTES GERADOS** lista,
   por documento: parte, meio (Diário Eletrônico ou Sistema), **prazo em dias** e se
   é pessoal. **É a fonte do prazo da parte autora.**
4. **Certidão de nomeação** (AJG) — perito/assistente nomeado, nº da nomeação,
   honorários. Não traz data de perícia.
5. **Anexos do ato** — laudo, proposta, petição, mandado: o conteúdo que a tarefa usa.

Caminho: ID DJEN → documento do mesmo movimento → certidão do movimento → ler
EXPEDIENTES GERADOS → abrir anexos e o ato referido, se houver.

### Aba Expedientes e leitura do ato

A aba Expedientes do processo (`expedientes` em `pje_get_process`) lista cada comunicação
por destinatário e é a ponte entre a comunicação DJEN e o PJe:

1. **Achar o expediente da comunicação.** Case pelo `documentId` (o ID do documento da
   intimação, o mesmo do movimento) e pela data (`expedidoEm` = disponibilização no
   DJEN, ou o dia útil anterior). Escolha a linha da **parte autora** (meio Diário
   Eletrônico, nome dela); a do INSS (Expedição eletrônica) é só acompanhamento.
   Não achou: diga "expediente não localizado" e use o texto DJEN.
2. **Ler o que ela diz sem abrir nada:** `prazoDias`, `limite`, `semPrazo`, `respondido`
   (já houve resposta: provável check, não tarefa nova), `fechado`.
3. **Abrir o ato** quando o campo obrigatório da tarefa depende do conteúdo:
   `pje_read_expediente_ato(processNumber, portal, expedienteId)` devolve a caixa do
   expediente, o texto completo do ato e os `anexos` (documentId e título; leia-os com
   `pje_read_documents`). Use para: nomeação de perícia (anexo do ato), mandados,
   intimações com anexo (proposta, laudo, cálculo).
4. **Ciência pendente exige permissão.** Abrir o ato (ou ler o documento da intimação com
   `pje_read_documents`) de uma comunicação com ciência pendente pode registrar a ciência da
   parte e iniciar o prazo. As duas ferramentas leem tudo o mais por padrão, mas recusam esses
   casos e a mensagem lista os expedientes. **Pergunte ao usuário e só repita a chamada com
   `acknowledgeCiencia=true` depois de um sim explícito.** Sem o sim, use apenas o texto DJEN.
   O cliente pode liberar a leitura sem perguntar em `customers/<cliente>/settings.toml`
   (`[pje] ciencia_on_read = "allow"`). Nesse caso, e também quando o usuário disse sim, o
   resultado traz `ciencia.pendenteAoLer` (expedientes que estavam pendentes quando foram lidos):
   **avise no chat e registre na descrição da tarefa** a linha `Ciência possivelmente registrada
   na leitura em {data}: expediente {id}, prazo {limite}`.
   Se o resultado trouxer `cienciaChecked: false`, a aba Expedientes não pôde ser lida e a
   verificação foi pulada: diga isso na nota.

Limites (verificados em 09/10/2026): a aba mostra só comunicações de 1º grau (Turma
Recursal não aparece); processo sigiloso ou sem expediente gerado volta vazio
(0012920-39); um prazo de "1 dia" em decisão de tutela é padrão do sistema e não um
prazo real, então leia o ato antes de tratá-lo como vencimento.

## 2. Níveis de profundidade

| Nível | Ler | Quando |
|---|---|---|
| **N0** | Só o texto DJEN | Ato completo, sem prazo a confirmar ou prazo no texto |
| **N1** | + certidão de intimação do movimento | Confirmar prazo (parte e INSS) |
| **N2** | + ato referido, nomeação, despacho anterior, petição de cumprimento | Texto remete a ID, a perito, a endereço ou a ato anterior |
| **N3** | + anexo (laudo PDF, proposta PDF, planilha) | A tarefa analisa ou anexa o documento |

Suba de nível só quando o anterior deixar um campo obrigatório de `tarefas.md` vazio.

## 3. O que ler por padrão

| Padrão | Nível | Abrir | Extrair | Prazo vem de |
|---|---|---|---|---|
| P1 / P2 Laudo | N3 | Anexo "Laudo Pericial (NOME)" em PDF; certidão da intimação | Tipo (social/médico), data, conclusão, achados-chave | Certidão |
| P3 Perícia (nomeação) | N2 | Intimação, certidão de nomeação, certidões "ainda a ser designada" | Perito/assistente, nº da nomeação; data **só se constar** | Nenhum (evento) |
| P4 Mandado social | N2 | Mandado, petição de cumprimento mais recente, nomeação | Executante, endereço e acesso (fonte e data) | Nenhum (certidão: 0 dias) |
| P5 / P6 Acordo | N1 + proposta | Despacho/intimação, certidão, PDF da proposta | Termos (benefício, DIB, DII, atrasados), decisão do cliente | DJEN ou certidão |
| P7 Emenda | N1 | Intimação, certidão | Lista literal das exigências | Certidão (ou texto) |
| P8 Dilação | N2 | Despacho, **ato referido por ID**, certidão | O que deve ser cumprido; vínculo com o ato original | Certidão do despacho |
| P9 / P10 Sentença | N1 | Dispositivo (DJEN basta), certidão | Resultado, obrigações (quem cumpre, em quantos dias) | Certidão (recurso) |
| P11 Homologação | N1 | Sentença, intimação de obrigação de fazer, certidão de trânsito | Obrigação do INSS, prazo, trânsito | Autora: nenhum. INSS: texto |
| P12 Cálculo | N2 | Intimação, certidão, **sentença** | Parâmetros (DIB, retroativos, índices) | Certidão |
| P13 Turma Recursal | N0 | Texto DJEN integral | Resultado; monocrático ou colegiado | Legal: revisar |
| P15 Pedido de endereço | N1 | Despacho, certidão, **petição de cumprimento** (se já houver) | Prazo; se já foi cumprido | Certidão |
| P16 Cumprimento | N1 | Despacho, certidão | Obrigação do INSS e "prazo fatal" dele | Texto / certidão |
| P18 Sigilo | N2 | Autos pelo PJe autenticado | Identificar o ato e segui-lo | Do padrão resultante |

## 4. Como ler os documentos (armadilhas verificadas)

- **Escolha o anexo certo.** Um movimento de laudo traz vários documentos: o de título
  só "Laudo Pericial" costuma ser um registro do editor do PJe (14 caracteres de
  texto). O laudo é o PDF "Laudo Pericial (NOME)". Áudios (MP3) acompanham.
- **HTML do PJe sem o número CNJ** (intimações de laudo, notificações curtas) volta como
  `extracted_unnumbered`: o documento foi buscado por ID do processo, então é aceito se
  tiver conteúdo suficiente e nenhuma marca de erro, login ou sigilo. Páginas curtas ou
  de erro continuam `unrecognized_html`.
- **Áudio:** com o extra `transcription` (faster-whisper) o leitor transcreve em
  português. A transcrição é aproximada; use só para confirmar fatos que também
  estejam no PDF. Leva minutos por gravação.
- **Link público da notícia** (`link` do DJEN) exige login e não traz o ato. A
  **certidão pública** (`djen_get_certidao_text`) traz o teor completo e a regra de
  publicação, mas **não** a tabela de prazos.
- **Sigilo:** o PJe autenticado abre os autos. Use o mínimo de dados pessoais (menores,
  saúde) na descrição.
- **Atraso do PJe:** uma decisão publicada no DJEN pode ainda não estar na linha do tempo
  (visto em 0010472-15). Nesse caso o texto DJEN é a única fonte; diga isso na nota.
- **Etiqueta de movimento não é prova** do resultado (ver `classificacao.md`).

## 5. Prazos

- **Fonte principal: aba Expedientes do processo** (`pje_get_process` devolve
  `expedientes`: `items`, `abertos`, `total`). Cada linha é um ato de comunicação por
  destinatário: tipo, `documentId`, parte, meio, `expedidoEm`, `ciencia`, `prazoDias`,
  `semPrazo`, **`limite`** (data final calculada pelo próprio PJe, "Data limite prevista")
  e `respondido`. Cobre todo o histórico, inclusive prazos que não aparecem no texto
  DJEN nem na certidão. Verificado ao vivo em 09/10/2026 (0011585-19, 0004089-96,
  0013812-45, 0010472-15). A linha da **parte autora** é a de meio Diário Eletrônico
  com o nome dela; a do INSS (Expedição eletrônica) é acompanhamento. Processo sem
  linhas (`total: 0`, visto em 0012920-39): o PJe ainda não gerou o expediente; use o
  texto DJEN e diga isso.
- Se `limite` divergir do cálculo por `djen_calculate_deadline`, **prevalece o `limite`
  do PJe** e a divergência vai na nota. A regra de contagem do PJe não é reproduzida
  pela calculadora em todos os casos (sentença de 0010472-15: 10 dias úteis da ciência
  de 05/08 dão 19/08; o PJe mostra 20/08).
- A **certidão de intimação** (EXPEDIENTES GERADOS) continua útil para o texto e como
  confirmação; o texto DJEN nem sempre traz o prazo ("prazo constante no menu Expedientes").
- Meio **Diário Eletrônico** com "0 / sem prazo" = nenhum vencimento para a autora.
- Meio **Sistema** (INSS) = acompanhamento da cadeia, nunca vencimento da autora.
- Quando o texto e a certidão divergirem, registre os dois valores (ex.: sentença
  fixa 30 dias para o INSS e a certidão mostra 0). Não escolha em silêncio.
- Cálculo: `djen_calculate_deadline` com `data_disponibilizacao` (calendário JFPE,
  cobertura até 2026-12-19). A certidão diz que a publicação é o primeiro dia útil
  seguinte à disponibilização e que a contagem começa no dia útil seguinte à
  publicação; **confirme** que a ferramenta aplica essa regra. Dias úteis consideram
  feriados (12/10/2026 é feriado nacional). Conferência manual de 06/10:
  5 dias úteis → 15/10, 10 → 22/10, 15 → 29/10.
- "Prazo legal", condicional, contagem não suportada ou remissão não resolvida →
  sem `deadline`; encaminhe para revisão.

## 6. Endereço e contato (perícia social)

- A fonte é a **petição mais recente** que respondeu ao despacho de endereço, não a
  petição inicial (que pode estar desatualizada). Cite o ID e a data.
- Se a petição trouxer só indicações de acesso, diga isso ("logradouro não
  informado"). Se não houver petição, diga: "não localizado: despacho de {data} pede
  {n} dias".
- Perícia social designada sem data: escreva "não designada (certidão {id})". Não
  invente data nem hora.
