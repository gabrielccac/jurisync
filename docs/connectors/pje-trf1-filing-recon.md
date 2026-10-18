# TRF1 PJe 1º grau: reconhecimento para o protocolo (Etapa A, só leitura)

Data: 17/10/2026. Portal `trf1-1g` (`pje1g.trf1.jus.br`), login por senha + TOTP do próprio conector. Objetivo: saber
o que do fluxo de rascunho já implementado no TRF5 (`docs/connectors/pje-trf5-filing.md`) vale no TRF1, para o caso
real do escritório na SJAP.

**Nada foi criado nem salvo.** As requisições foram: GET das telas; as atualizações Ajax do próprio formulário de
cadastro ao escolher área, jurisdição e classe (as mesmas que o fluxo faz antes do **Incluir**, que não foi enviado);
e, num rascunho que o escritório já tinha em **Não Protocolados**, só a carga das abas (Partes, Informativo, Anexar,
Assunto) e duas pesquisas de assunto, que listam resultados sem associar nenhum. Nenhum formulário de parte foi
aberto, nenhum botão de incluir, vincular, gravar ou remover foi acionado, e o modo do editor de documentos não foi
trocado. Nenhum dado pessoal do rascunho consultado entra neste relatório.

Como repetir a parte do cadastro (só leitura; o estágio nunca envia Incluir):

```bash
.venv/bin/python -m jurisync.connectors.pje.filing --stage register-options --portal trf1-1g \
  --area "DIREITO ASSISTENCIAL" --jurisdiction "Seção Judiciária do Amapá"
```

## Resumo

| Ponto | TRF5 (implementado) | TRF1 (observado) | Impacto |
| --- | --- | --- | --- |
| Versão do PJe | 2.11.2.0 (captura de 05/10/2026) | **2.13.3.0_TRF1** (atualizado em 15/10/2026) | versão mais nova; mesmos componentes nas telas vistas |
| Login | certificado A1 + TOTP | senha + TOTP | já suportado por `login_portal`; o filing agora usa o login do portal |
| URLs | `/pje/Processo/cadastrar.seam`, `update.seam`, `ConsultaProcessoNaoProtocolado/listView.seam` | as mesmas, no host do TRF1 | configuradas por portal |
| Cadastro: formulário e ids | `processoTrfForm`, `areaDireitoCombo`, `jurisdicaoCombo`, `classeJudicialCombo`, `incluiProcessoButton` | os mesmos sufixos (o código casa por sufixo) | igual |
| Áreas | o filing usa `DIREITO PREVIDENCIÁRIO` por padrão (`facts.classification.area` pode trocar) | 27 áreas; há **`DIREITO ASSISTENCIAL`** separada de `DIREITO PREVIDENCIÁRIO` | o padrão deve vir do perfil do portal, não do código |
| Jurisdição | `PE / Petrolina` (rótulo por cidade) | rótulo por seção: **`Seção Judiciária do Amapá`**, `Subseção Judiciária de Laranjal do Jari-AP`, `Subseção Judiciária de Oiapoque-AP` (67 opções, iguais nas duas áreas) | sem rótulo "JEF Macapá": juizado × vara sai da classe; o órgão julgador não aparece antes do Incluir |
| Classe | `PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)` | o mesmo rótulo existe na SJAP | igual |
| Campo extra no cadastro | — | `processoReferenciaDiv` (processo de referência) | provavelmente opcional; verificar no rascunho de teste |
| Não Protocolados | `consultaProcessoNaoProtocoladoForm`, `nomeParte`, `numeroCPFCNPJCPF`, `consulta`, grade e "Remover" | os mesmos ids | igual |
| Abas do rascunho | form, assunto, partes, características, anexar, informativo | `form`, `assunto`, `tabPartes`, `processoPrioridadeProcesso`, `novoAnexo`, `informativo` | igual |
| Assunto | o formulário de busca vem na página do rascunho | **só aparece depois de carregar a aba `assunto`** | ajuste pequeno em `subjects.py` |
| Códigos de assunto | `11946` Pessoa com Deficiência | `11946` (DIREITO ASSISTENCIAL › Benefício Assistencial › Pessoa com Deficiência) e `11947` Idoso | tabela unificada: os mesmos códigos |
| Partes | `poloAtivoDatalistDiv`, `formVincularPoloAtivo`, `addParteA`, `supResetarVincRepPA`, `gridPartesPoloAtivoEdit` | todos presentes, mais `poloPassivoDatalistDiv`, as barras de polo passivo e outros participantes e um recurso novo "aproveitar advogados de outros processos" | estrutura igual; os formulários internos não foram abertos |
| Documento principal | modo PDF, `cbTD`, upload RichFaces | `raTipoDocPrincipal` PDF/HTML presentes; a aba abre em HTML e o controle de upload PDF só aparece ao trocar o modo (não trocado) | upload não verificado |
| Tipos de documento | Petição inicial, Planilha, Documento de Identificação, Processo Administrativo, Documento Comprobatório, Carta de Indeferimento, Carta de Concessão, Laudo Médico, Outros Documentos | lista do documento principal com 83 tipos. Existem `Petição inicial`, `Inicial`, `Processo administrativo` (minúsculo), `Carta de Concessão`, `Procuração`, `Documentos Diversos`, `Exame médico`, `Declaração de hipossuficiência/pobreza`. **Não existem** `Laudo Médico`, `Documento Comprobatório`, `Documento de Identificação`, `Planilha`, `Carta de Indeferimento`, `Outros Documentos` | **a classificação de tipos precisa ser por portal**; hoje `SUPPORTING_TYPES` está fixo em `prepare.py` |
| Limite de tamanho | 10 MB por PDF (painel do TRF5) | não visto (o painel PDF não foi aberto) | descobrir no rascunho de teste |

## O que já ficou pronto nesta etapa

- O conector tem uma configuração por portal (`connectors/pje/filing/constants.py`, `FILING_PORTALS`): URLs por portal
  e se o portal aceita escrita. `trf5-1g` escreve; `trf1-1g` está **só leitura** e todos os estágios que escrevem
  recusam antes do login.
- O portal sai do `portal` do payload (ou de `--portal` nos estágios sem payload); o login é o do portal (certificado
  no TRF5, senha + TOTP no TRF1).
- Novo estágio só leitura `register-options` lista as áreas, as jurisdições de uma área e as classes de uma
  jurisdição, sem enviar Incluir.

## O que só dá para descobrir criando um rascunho de teste

1. O que o **Incluir** pede no TRF1 depois da classe: órgão julgador, competência, o campo "processo de referência".
2. **Autor:** o formulário de pré-cadastro por CPF (`preCadastroPessoaFisicaForm`), os campos obrigatórios e o
   endereço.
3. **Advogado extra e representante legal:** o modal de `supResetarVincRepPA` (`comboRepresentacao`, os tipos
   `ADVOGADO`/`REPRESENTANTE`, a busca por CPF/OAB) e como o polo ativo lista o resultado. O representante também
   continua sem captura no TRF5.
4. **Polo passivo e outros participantes:** se o INSS é encontrado pelo mesmo CNPJ e se a SJAP pede órgão de
   cumprimento ou MPF (no TRF5 o perfil do Victor inclui CEAB-DJ e, às vezes, o MPF).
5. **Características:** campos e botões de gravar (justiça gratuita, tutela, valor da causa, prioridade "Juízo 100%
   Digital" ou equivalente).
6. **Documentos:** o modo PDF do principal, o controle de upload, os **tipos aceitos nos anexos** (a lista acima é a
   do principal; a dos anexos só aparece ao registrar um arquivo) e o limite de tamanho.
7. A releitura no Informativo e a remoção do rascunho de teste pelo "Remover" de Não Protocolados.

## Proposta para a Etapa B (só com aprovação)

- **Perfil por portal:** um perfil de filing próprio para o TRF1 (o perfil já tem `portal`), com área, polo passivo,
  participantes, prioridade e o mapa de tipos de documento do TRF1. Tirar `SUPPORTING_TYPES` e o padrão de área do código
  e levá-los ao perfil.
- **Assunto:** carregar a aba `assunto` antes da busca quando o formulário não vier na página.
- **Rascunho de teste supervisionado:** criar um rascunho com dados de teste no TRF1, mapear os itens 1–7 acima com
  o próprio conector (ou com uma captura do navegador), ajustar o que divergir e apagar o rascunho. Só então mudar
  `trf1-1g` para `writes=True`.
- **Estimativa:** cerca de 1 a 2 dias de trabalho, sendo meio dia de sessão acompanhada no portal (rascunho de teste
  e captura) e o restante em código e testes. O maior risco é o upload de documentos e o modal de representação, se
  o TRF1 2.13 tiver mudado esses formulários em relação ao TRF5 2.11.

## Etapa B: o que foi implementado (sem criar rascunho)

Nenhum rascunho foi criado para implementar esta etapa. O `trf1-1g` passou a escrever com o mesmo fluxo do TRF5
(fases, checkpoint, aprovação, hashes e registro de uploads da Fase 1) e com estas diferenças:

- **Configuração do portal:** `trf1-1g` tem `writes=True` e uma lista `unverified` dos passos que ainda não rodaram no
  TRF1. Essa lista sai em `unverifiedSteps` no resultado de cada estágio que escreve e no `pje_draft_status`.
- **Assunto (verificado na Etapa A):** quando a página do rascunho não traz o formulário de assunto, o fluxo carrega a
  aba `assunto`, cuja resposta tem a lista de assuntos associados e o formulário de busca.
- **Incluir:** se o PJe não devolver o endereço do novo rascunho, o erro traz as mensagens do próprio PJe (campo
  obrigatório etc.) e manda conferir Não Protocolados antes de tentar de novo.
- **Perfil do escritório `jean`** (`customers/jean/filing-profile.json`), conforme o roteiro "[IT] Protocolo de
  Iniciais BPC - PJE": matéria `DIREITO PREVIDENCIÁRIO`, jurisdição `Seção Judiciária do Amapá` (os facts trocam conforme
  o estado do cliente), classe `PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)`, assunto `11946`, INSS no polo passivo
  (busca "INSS" + CNPJ 29.979.036/0001-40), nenhum advogado extra fixo, justiça gratuita sim, tutela sim, segredo não,
  Juízo 100% Digital solicitado e prioridade `Pessoa com deficiência` quando o caso não indicar outra. Tipos:

  | Documento | Tipo no TRF1 | Fonte do rótulo |
  | --- | --- | --- |
  | Petição | Petição inicial | lista vista na Etapa A |
  | Procuração | Procuração | lista vista na Etapa A |
  | Comprovante de residência | Comprovante de residência | roteiro do escritório |
  | Folha resumo do CadÚnico, CNIS | Cadastro Nacional de Informações Sociais - CNIS | roteiro do escritório |
  | Laudos, atestados, exames, receituários | Atestado médico | roteiro do escritório |
  | Processo administrativo | Processo administrativo | lista vista na Etapa A |
  | Quesitos | Apresentação de quesitos | lista vista na Etapa A |
  | Declaração de hipossuficiência | Declaração de hipossuficiência/pobreza | lista vista na Etapa A |
  | Carta de concessão | Carta de Concessão | lista vista na Etapa A |
  | Comprovantes em geral (classificação "Documento Comprobatório") | Comprovante (Outros) | lista vista na Etapa A |
  | Identidade, indeferimento, planilha, outros | Documentos Diversos | lista vista na Etapa A |

  A "lista vista na Etapa A" é a do documento principal; a dos **anexos** ainda não foi vista. Os três rótulos do
  roteiro não estão na lista do principal (são de anexos, segundo as capturas do roteiro). Se algum faltar nos anexos,
  o passo falha dizendo qual opção não existe.
- **Características (aba lida só leitura num rascunho existente):** no TRF1 o **Juízo 100% Digital é um campo próprio**
  ("Deseja solicitar juízo 100% digital?", `solicitadoJuizo100PorCentoDigital`), gravado junto com justiça gratuita,
  tutela e valor da causa; no TRF5 ele é uma prioridade. **Segredo de justiça** tem formulário e botão próprios
  (`frmSegredoSig`, `grvSegredo`). As prioridades vêm de uma lista separada (`formAddPrioridadeProcesso`) que esconde
  as já associadas; a de PcD aparece como **`Pessoa com deficiência`**. No payload: `characteristics.priority` aceita
  uma lista (um texto só continua valendo), `digitalCourt` marca o campo do TRF1 (e falha onde o PJe não o tem) e
  `secrecy: false` marca e confere "Não" (o "Sim" fica manual). Cada valor é relido depois de gravar.
- **O que continua igual e se confere sozinho:** cada passo relê o rascunho depois de agir (assunto associado, autor no
  polo ativo, advogado com CPF/nome/OAB, órgão público pelo CNPJ, características gravadas, documentos no
  Informativo) e falha com mensagem clara se o dado não estiver lá. Nada segue às cegas.
- **Representante legal:** continua como passo manual registrado (igual ao TRF5). O registro no checkpoint e o
  `pje_draft_status` trazem nome, CPF, tipo e endereço (`author.representative.address {cep, numero}`) e o nome do
  autor, para quem fizer o passo à mão.

### Verificado de verdade no TRF1 × não verificado

| Passo | Situação |
| --- | --- |
| Login senha + TOTP, URLs, telas de cadastro e Não Protocolados, áreas, jurisdições, classe, códigos de assunto, aba de assunto, ids da barra de partes, tipos do documento principal | **verificado** (Etapa A, só leitura) |
| Incluir e o que vier depois da classe (órgão julgador, processo de referência) | não verificado |
| Selecionar o assunto na busca e vê-lo associado | não verificado |
| Autor por CPF (pré-cadastro, endereço) | não verificado |
| Advogado extra (modal de representação) | não verificado |
| INSS no polo passivo (busca "inss" + CNPJ 29.979.036/0001-40) | não verificado |
| Campos e opções da aba de características (Juízo 100% Digital, segredo, prioridades) | **verificado** (leitura) |
| Gravar características, prioridade e segredo | não verificado |
| Petição em PDF, anexos, tipos dos anexos, limite de tamanho (mantido em 10 MB) | não verificado |

### Roteiro do primeiro teste real (acompanhado)

Use um caso real com o usuário olhando o PJe ao mesmo tempo e o perfil `jean` (classificação, assunto e
características já vêm dele; os facts trazem autor, CPF, valor da causa e, se for o caso, jurisdição de outro estado,
prioridade diferente ou representante).
Rode por estágios, não `create-fill` de uma vez, e confira no portal depois de cada um:

1. `create`: o rascunho aparece em Não Protocolados? Se falhar, leia a mensagem do PJe (campo pedido depois da
   classe) e anote; confira se algum rascunho ficou criado.
2. `subject`: o assunto 11946 (ou o do caso) aparece na aba Assunto?
3. `author`: autor certo no polo ativo, com o endereço esperado?
4. `attorney` (se houver advogado extra): advogado com OAB certa?
5. `public-bodies`: o INSS entrou? A SJAP pede órgão de cumprimento ou MPF (se sim, acrescentar ao perfil)?
6. `characteristics`: justiça gratuita sim, tutela sim, valor da causa, Juízo 100% Digital sim, segredo não e a
   prioridade "Pessoa com deficiência" gravados?
7. `documents`: a petição sobe em PDF? Os anexos aceitam os tipos do perfil? Qual o limite de tamanho mostrado?
8. `pje_draft_status` com `checkPje: true`: conclusão `verified`, ou `verified_manual_steps_pending` se houver
   representante.

Se houver representante legal, inclua-o à mão na aba Partes com os dados que o `pje_draft_status` lista.
O que divergir vira ajuste no perfil `jean` ou no código. Quando tudo tiver passado, os itens podem sair da
lista `unverified` de `trf1-1g`.

## Primeiro rascunho real (18/10/2026): o modal de partes

O primeiro teste acompanhado criou o rascunho e associou o assunto; o estágio `author` falhou porque, no TRF1, o modal
"Associar parte ao processo" começa por um passo que o TRF5 não tem. Lido no próprio rascunho, só com as atualizações
de exibição (abrir o modal e trocar os selects; nada pesquisado, confirmado, vinculado ou salvo):

1. **"1 Passo → Tipo de Vinculação"**: um select "Tipo da Parte" com ids gerados (`j_id2888:j_id2892`). No polo ativo as
   opções são ASSISTENTE, TESTEMUNHA, TERCEIRO INTERESSADO, **AUTOR**, LITISCONSORTE; no passivo, TESTEMUNHA, TERCEIRO
   INTERESSADO, **REU** (sem acento), LITISCONSORTE.
2. Só depois de escolher o tipo aparece o **`preCadastroPessoaFisicaForm`**, o mesmo do TRF5, já com "Jurídica" e
   "Órgão Público = Sim" marcados por padrão.
3. Com "Física": "Brasileiro?" (Sim marcado), CPF e **Pesquisar** (`pesquisarDocumentoPrincipal`), com os mesmos
   sufixos de id que o código do TRF5 usa. Com "Jurídica" + órgão público: Nome e **Pesquisar**
   (`btnPesquisarOrgaoPublico`), também iguais.

O conector agora responde esse passo quando o portal tem `party_roles` (TRF1: AUTOR para o autor, REU para o INSS),
achando o select pelas opções, e confere que o pré-cadastro abriu; no TRF5 nada muda. As respostas capturadas estão em
`tests/connectors/pje/fixtures/trf1/party_*.html` (sem dados pessoais).

**Ainda não visto:** tudo depois de Pesquisar (o nome devolvido pela Receita, Confirmar, os dados da parte, a aba
Endereços com CEP/número/Incluir e o botão final, que o roteiro do escritório chama de "Vincular parte ao processo"; o
código procura `btnInserirParteProcesso`, como no TRF5). Cada um desses passos falha dizendo qual campo não encontrou.

### Depois da pesquisa do CPF (lido no mesmo rascunho, 18/10/2026)

Para ver a tela de detalhes foi preciso repetir **Pesquisar** e **Confirmar** do pré-cadastro, como o estágio real já
tinha feito; nada foi incluído, selecionado como endereço, vinculado ou inserido.

- **Estado do rascunho:** o polo ativo tem só uma linha, de ADVOGADO (o advogado que o PJe lista sozinho); não há autor
  nem parte pela metade. O polo passivo está vazio.
- **Pesquisar:** devolve o nome civil (`dsNomeCivil`) e o botão **Confirmar** (`btnConfirmarCadastro`), como no TRF5.
- **Detalhes da parte (`formInserirParteProcesso`):** nome preenchido, nome social, `nomeGenitor` (pai, vazio), óbito,
  etnia, estado civil, escolaridade, profissão, nacionalidade, UF/município, sigilo, morador de rua e representante
  (`comboRepresentante` = núcleos de prática jurídica). **Nome da genitora e data de nascimento aparecem só como
  rótulos**, sem campo, e a tabela de validação da Receita veio quase vazia (só "Sexo"). Por isso o TRF5 exigia esses
  dois campos e o TRF1 não exige (`author_required_details`): a identidade é conferida pelo nome devolvido e, depois de
  vincular, por nome **e CPF** na mesma linha do polo ativo.
- **Abas:** Informações pessoais, **Endereços**, Meios de contato, Outras filiações.
- **Endereços:** mesmos ids do TRF5 (CEP com sugestão, número, complemento, **Incluir**, grade "Usar no processo").
  A pessoa já tinha um endereço cadastrado com o CEP e o número do caso, ainda não marcado: o fluxo o marca e não
  cadastra outro.
- **Botão final:** `btnInserirParteProcesso`, com o texto **"Vincular parte ao processo"**.
- **INSS:** REU → Jurídica + Órgão Público → "INSS" → Pesquisar devolve uma linha (Autarquia Federal, CNPJ
  29.979.036/0001-40), que se seleciona por um link; aparece **Inserir** (`btnInserirOrgPub`) e, só como texto, a
  procuradoria. Igual ao TRF5.

Fixtures: `tests/connectors/pje/fixtures/trf1/author_*.html` e `inss_*.html` (dados pessoais trocados por valores de
exemplo).
