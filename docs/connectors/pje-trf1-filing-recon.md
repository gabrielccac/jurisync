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
