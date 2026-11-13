# Allan — ambientes sem PDPJ — 13/11/2026

Branch `feat/trt-4-9-2-tjms`. PJe/PDPJ encerrado por orientação urgente; nenhum request ao SSO nacional foi feito na exploração abaixo. A1, .env e portals.toml preservados.

| Portal | Login | Leituras | Cadastrado | Tentativas de autenticação |
|---|---|---|---|---|
| tjba-eproc-1g | ❌ Não foi localizado usuário com este CPF [omitido]; portal oferece cadastro de advogado | Não executadas | Não | 2 |
| tjmt-projudi-1g | ⛔ Site em manutenção — TJMT (HTTP 503) | Não executadas | Não | 0 |
| tjam-esaj | ❌ Usuário e/ou senha inválidos (resposta ao envio por A1) | Não executadas | Não | 2 |
| tjmg-2g | ✅ A1 + TOTP; conector nativo e painel confirmados | painel: OK; pending: OK (0); open_deadlines: OK (0); listagem: OK (0); processo/documento: Não validados: listagem sem itens | Sim | 2 |
| tjrs-2g | ⛔ Redireciona ao PDPJ; interrompido antes de acessar o SSO | Não executadas | Não | 0 |

## Evidências e limites

- eproc TJBA 1G: mensagem literal, com CPF omitido: “Não foi localizado usuário com este CPF ([omitido]).” A página oferece cadastro de advogado. O aviso estático de navegador inseguro não foi tratado como recusa; a segunda sondagem identificou a mensagem específica do cadastro.
- Projudi TJMT: página pública “Site em manutenção — TJMT”, HTTP 503, sem formulário. Nenhum certificado ou código enviado.
- e-SAJ TJAM: duas respostas ao envio de assinatura do A1, a segunda confirmando a mensagem literal nova “Usuário e/ou senha inválidos”. Não houve CASTGC ou segundo fator por e-mail. O JavaScript oficial utiliza assinatura de hash SHA-256 e certificado Base64, como o conector. A resposta não permite afirmar que o PIN ou o certificado estejam inválidos: não houve autenticação e o portal não foi cadastrado.
- eproc TJMG 2G: primeira sondagem HTTP e segunda pelo cert_login nativo depois da implementação, ambas com A1 + TOTP aceitos. Painel do advogado, filas e relação de processos OK; listagem e filas sem itens. Processo e documento não puderam ser validados sem amostra. Checkbox de liberar dispositivo omitido. Perfil Eproc/TJMG, índice 9 do QR_Allan_2.jpeg, selecionado somente no ambiente do processo.
- eproc TJRS 2G: uma sondagem pública (zero autenticações) encontrou redirecionamento para sso.cloud.pje.jus.br. O destino foi recusado antes do request; sem certificado, senha ou código enviados ao PDPJ.
- Suspensos sem teste nesta frente: PJe TJBA 2G, PJe TJMT 2G, PJe TJMG, PJe TRF6 1G/2G e PJe TJDFT 2G. Os demais portais reservados a outros executores não foram acessados.
- Nenhuma ciência, petição, mudança de cadastro/senha, perfil padrão ou liberação persistente de dispositivo. Nenhum .env ou portals.toml de cliente foi gravado. Nenhuma resolução de CAPTCHA/verificação humana.
- Hashes de Cert_Allan.pfx, .env e portals.toml conferidos antes/depois de cada execução, sem mudanças. PEMs privados removidos automaticamente ao sair dos contextos mTLS. Cookies e conteúdos só em memória; scripts e resultados temporários removidos ao final.

Conferência offline: o certificado selecionado pelo conector e-SAJ é o mesmo A1 do Allan. Hash SHA-256 do PFX antes/depois: `1a829c55033751898210b74fb7a340f487dc774372bd1269de48d226d581685b`.

## Verificação final

- `pytest tests/core/ tests/connectors/pje/ tests/connectors/eproc/`: 462 passed, 1 skipped, 164 subtests passed.
- Ruff lint/format e git diff --check aprovados.
- Único novo cadastro nesta etapa: `tjmg-2g`; código validado ao vivo na segunda autenticação. Sem alteração em configurações do Allan.
