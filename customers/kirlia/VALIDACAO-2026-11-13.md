# Kirlia Mara — validação de login em 13/11/2026

Testes ao vivo somente de autenticação e leitura da página de entrada/painel, usando as credenciais de `customers/kirlia/.env`. Nenhuma senha, código, QR, CPF ou endereço de e-mail é reproduzido neste relatório.

| Portal | Resultado | Evidência | Tentativas de login |
|---|---|---|---|
| PJe TRF1 1º grau (`trf1-1g`) | ❌ Senha expirada | O conector detectou a mensagem de senha expirada após o envio da credencial, antes do formulário de TOTP. | 1 |
| PJe TRF3 1º grau (`trf3-1g`) | ✅ Entrou | Resposta HTTP 200 no host do tribunal, link `logout.seam`, campo `javax.faces.ViewState` e navegação interna com “Processos”. Senha e TOTP aceitos. | 1 |
| Eproc JFRJ/TRF2 (`trf2-1g-rj`) | ✅ Entrou | Senha e TOTP aceitos; HTTP 200 no painel do advogado, com formulário `frmPainelAdvogado`. | 1 |
| Eproc TJSP 1º grau (`tjsp-1g`) | ✅ Entrou | Senha e TOTP aceitos no SSO; retorno ao eproc e HTTP 200 no painel do advogado, com formulário `frmPainelAdvogado`. | 1 |
| eSAJ TJSP (`tjsp-esaj`) | ✅ Chegou à etapa de código por e-mail | Após a credencial: `$.saj.cas.DuploFatorHabilitado = true || false;` e e-mail de destino preenchido no modal. Antes da credencial: flag `false` e destino vazio. Campo `tokenInformado` com tamanho máximo 6. | 2 |

## TRF1

Diagnóstico literal retornado pelo conector (URL removida):

> The portal reports an expired password; reset it through the portal and update the configured password

A página integral não foi armazenada; portanto, não há transcrição literal da mensagem do portal além da detecção de “senha expirada”. Não repeti a tentativa e não solicitei troca de senha. Para validar o acesso completo, a titular precisa regularizar a senha e atualizar a credencial local.

## eSAJ

Texto do modal, com destino omitido:

> Insira o código de validação enviado para seu endereço de e-mail principal [omitido] e seus e-mails alternativos cadastrados no Portal.

A primeira tentativa chegou à etapa de e-mail. A segunda confirmou que o modal estava ativado pelo resultado da autenticação, pois o HTML público já contém o modal estático. Nenhum código foi solicitado à cliente, informado ao portal ou reenviado pelo botão de reenvio. O recebimento efetivo do e-mail e a conclusão do login não foram verificados.

## Limites e alterações

- Nenhum portal ultrapassou duas tentativas. Nenhum CAPTCHA foi apresentado nos fluxos testados.
- Testes por HTTP; nenhum navegador visível foi aberto.
- Nenhuma ciência de intimação, alteração de cadastro ou troca de senha.
- GERID fora do teste.
- Nenhum código do conector, `.env`, QR ou configuração foi alterado. Os eprocs foram validados por HTTP reproduzindo o formulário; o caminho `browser_totp` do conector não foi executado.
- Relatório salvo neste arquivo. Scripts temporários e resultados sanitizados de `/tmp` foram removidos após a consolidação. Nenhum cookie autenticado foi salvo em disco pelos testes.


## Reteste 25/11

Somente leitura. Nenhuma ciência, troca de senha ou de cadastro; nenhum CAPTCHA apresentado. `.env`, `portals.toml` e código não foram alterados. Nenhuma senha, código, CPF, e-mail completo ou número de processo é reproduzido aqui.

| Portal | Resultado | Leituras | Tentativas de login |
|---|---|---|---|
| PJe TRF1 1º grau (`trf1-1g`) | Entrou: senha nova e TOTP do perfil PDPJ aceitos; painel do advogado carregado (`.../pje/Painel/painel_usuario/advogado.seam`, HTTP 200). A "senha expirada" de 13/11 não se repetiu. | Contadores validados; caixa validada; Push validado; processo validado; documento validado (detalhes abaixo) | 1 login real (a segunda e a terceira execução reaproveitaram o cache de sessão em disco, sem novo login) |
| e-SAJ TJSP (`tjsp-esaj`) | Sem entrar: login e senha aceitos, parou na etapa do código de e-mail (`$.saj.cas.DuploFatorHabilitado = true`, cookie `CASTGC` ausente). | Nenhuma (sem sessão) | 1 |

### PJe TRF1

- Contadores: 7 grupos lidos. "Pendentes de ciência ou de resposta" 5; "Apenas pendentes de ciência" 0; "Ciência dada, pendente de resposta" 4 e 0; "Prazo findou nos últimos 10 dias" 1; "Sem prazo" 1; "Respondidos nos últimos 10 dias" 2.
- Caixa de expedientes: grupos 0, 3 e 6 lidos por completo (`complete: true`). Todas as linhas com `cienciaPendente: false` (ciência dada pelo sistema); como "Apenas pendentes de ciência" é 0, não há ciência pendente na conta.
- Push: 55 processos cadastrados, lista completa; 5 linhas retornadas na checagem (número, data de cadastro, id).
- Processo: processo escolhido da caixa, sem ciência pendente (PendingNotice não disparou). Leu metadados, partes, 22 itens da linha do tempo, 51 documentos no inventário e 6 expedientes (completo; 1 aberto para resposta, nenhum com ciência pendente).
- Documento: 1 contestação (HTML, 269 KB, extração `extracted`, 16.781 caracteres de texto, hash conferido). Bloco `ciencia: estado "nenhuma"`.
- Prova de página autenticada: painel do advogado e consulta de processo do advogado (`listProcessoCompletoAdvogado.seam`) carregados. A leitura de ato de expediente (`pje_read_expediente_ato`) não foi exercitada: exigiria abrir expediente, e a conta não tem nenhum sem ciência dada seguro para esse teste.
- Última entrada bem-sucedida: 25/11/2026.

### e-SAJ TJSP: o que há e o que falta

- O conector atual (`main`, `src/jurisync/connectors/esaj/base.py`) já implementa login por usuário e senha (`uses_password`, `password_form`, CPF formatado com máscara), usado quando a conta não tem A1. Ele detecta o 2º fator (`TWO_FACTOR_ON`) e levanta `EsajTwoFactorRequired`: "e-SAJ accepted the password and sent a validation code to the account's e-mail: the connector cannot read it. Use the A1 certificate (no code) or have a person log in". Não há campo de código, leitura de caixa de e-mail nem outra forma de receber o código; `TJSP_ESAJ_2FA_DATA` no `.env` não é usado pelo e-SAJ (o código não é TOTP). Nota: o comentário em `portals.toml` ainda diz que o conector só faz certificado, o que está desatualizado.
- Confirmado hoje (1 tentativa): senha aceita, o portal manda o código para o e-mail e abre o modal. Texto do portal: "Insira o código de validação enviado para seu endereço de e-mail principal [mascarado] e seus e-mails alternativos cadastrados no Portal." Campo `tokenInformado` com tamanho máximo 6; botão de reenvio `btnReenvio` (não acionado).
- E-mail que recebe o código (segundo o modal, mascarado pelo próprio portal): domínio `hotmail.com`, começa com `ki`. Pode haver e-mails alternativos cadastrados, não identificados.
- Nenhum código foi pedido a ninguém, informado ao portal ou reenviado.
- Para completar o login sem certificado o conector precisaria de: (1) um canal para obter o código de 6 dígitos da caixa `hotmail.com` dela (leitura automática da caixa via IMAP/Graph com autorização da titular, ou encaminhamento do e-mail do TJSP para uma caixa que o Jurisync leia), e (2) um passo no login que, ao detectar o 2º fator, aguarde o e-mail, envie `tokenInformado` e confirme o `CASTGC`. Alternativas: a titular obter um A1 (sem código), ou desativar o duplo fator no portal (ação dela). Cada login por senha dispara um e-mail novo; manter a regra de no máximo 2 tentativas.

### Limites

- Scripts temporários e a pasta `/tmp/kt` removidos. O cache de sessão do PJe permanece no cache privado de usuário do Jurisync, como em qualquer uso do conector.

## Validação completa 25/11 (conector e servidor remoto)

Um login por portal pelo conector na máquina e um pelo servidor remoto (`teste@kirlia.jurisync.local`), só leitura, `acknowledgeCiencia=False`; nenhuma ciência registrada, nenhum CAPTCHA, nenhuma recusa.

| Portal | Conector | Servidor |
|---|---|---|
| PJe TRF1 1º grau | ✅ ver "Reteste 25/11" | ✅ contadores, Push, processo, documento `extracted` (542 caracteres) |
| PJe TRF3 1º grau | ✅ contadores, caixa 37 (6 sem ciência), acervo 157 (1 página), Push 122, processo, documento `extracted` | ✅ contadores, Push, processo, documento `extracted` (689 caracteres) |
| Eproc JFRJ | ✅ 5 filas (todas 0), 1 processo, 16 documentos | ✅ documento `extracted_ocr`, 22.372 caracteres |
| Eproc TJSP 1º grau | ✅ 5 filas (prazos abertos 4), 9 processos, 509 documentos no processo | ✅ documento `extracted_ocr`, 1.162 caracteres |

Os dois Eproc entraram pelo login do conector sem abrir o Chrome. Fora: e-SAJ TJSP e ADVBOX (aparelho não verificado).
