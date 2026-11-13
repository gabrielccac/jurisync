# Eproc TJMG — segundo grau

Portal `tjmg-2g`: `https://eproc2g.tjmg.jus.br/eproc/`, região CNJ 13, justiça estadual, método `certificate_totp`, prefixo `TJMG_2G`.

## Login e configuração

O portal usa o formulário local de certificado, sem Keycloak/PDPJ: `destino_v2_cert` → `externo_controlador.php?acao=login_cert` → retorno com código TOTP local. Reutiliza o fluxo local do TJMS 1G; a assinatura TLS é apresentada apenas aos destinos aceitos pelo guard, e `chkLiberarDispositivo` nunca é enviado.

Variáveis, sem gravar valores em arquivo nesta validação: `PJE_A1_P12`, `PJE_A1_PIN`, `TJMG_2G_CERT=true`, `TJMG_2G_2FA_QR` ou `TJMG_2G_2FA_DATA`, e seletor `TJMG_2G_OTP_PROFILE_INDEX`/`TJMG_2G_OTP_PROFILE_MATCH`. Para o Allan foi usado o perfil Eproc/TJMG no índice 9 do QR_Allan_2.jpeg, somente no processo.

## Validação ao vivo — 13/11/2026

Allan: duas autenticações com A1 + TOTP, sendo a segunda pelo `certificate.cert_login` já incorporado ao código. Painel `frmPainelAdvogado`, filas `pending` e `open_deadlines` e relação de processos responderam pelos leitores existentes. Todas as listagens consultadas estavam sem itens; processo específico e documentos permanecem sem validação por falta de amostra. Nenhuma ciência ou liberação de dispositivo.

O teste bloqueava qualquer destino PDPJ antes de enviar o request, por causa da conta desativada no SSO nacional do cliente. O fluxo TJMG 2G não precisou dele. A1, .env e portals.toml preservados; nenhum segredo adicionado ao Git.

## Testes

Cobertura de registro/roteamento, despacho de TJMG 2G para o formulário local e regressão do fluxo compartilhado que omite a liberação de dispositivo. Os leitores de painel e processo são os mesmos do eproc existente.

Verificação final: **462 passed, 1 skipped, 164 subtests passed** em `tests/core/`, `tests/connectors/pje/` e `tests/connectors/eproc/`; Ruff lint/format aprovados.
