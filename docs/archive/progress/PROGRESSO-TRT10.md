# TRT10 — Allan — validação de 13/11/2026

Branch `feat/trt-4-9-2-tjms`; nenhuma alteração em configuração de cliente.

| Portal | Login | Leituras | Tentativas |
|---|---|---|---|
| trt10-1g | ❌ Conta desativada. Contate o suporte | Não executadas | 1 |
| trt10-2g | ❌ Conta desativada. Contate o suporte | Não executadas | 1 |

SHA-256 do PFX antes: `1a829c55033751898210b74fb7a340f487dc774372bd1269de48d226d581685b`.

A1, `.env` e `portals.toml`: hashes antes/depois idênticos. Nenhuma configuração gravada; cookies e documentos apenas em memória. Sem ciência, dispositivo liberado ou alteração de conta.

Conferência offline adicional: arquivo carregado corresponde a Cert_Allan.pfx e titular esperado. A mensagem ocorreu antes do TOTP: um callback de certificado por grau e zero códigos enviados. Não houve recusa repetida. Nenhum TRT10 cadastrado, pois não foi possível validar acesso.

Por orientação urgente do Gabriel, todos os testes PJe/PDPJ do Allan foram encerrados; nenhuma nova tentativa será feita. Ambientes PJe adicionais cancelados, sem teste nesta frente.
