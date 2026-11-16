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
