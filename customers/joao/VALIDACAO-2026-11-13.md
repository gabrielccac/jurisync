# João — validação de login em 13/11/2026

Testes ao vivo de autenticação e leitura da página inicial, com as credenciais de `customers/joao/.env` (`JURISYNC_CUSTOMER=joao`). Nenhum segredo é reproduzido aqui.

| Portal | Método | Resultado | Evidência ou mensagem literal | Tentativas |
|---|---|---|---|---|
| `trf1-1g` | password_totp | ✅ Entrou | HTTP 200 no host do tribunal; link de logout e ViewState; menus: Processos, Expedientes, Acervo, Painel. | 1 |
| `trf5-1g` | password_totp | ✅ Entrou | HTTP 200 no host do tribunal; link de logout e ViewState; menus: Processos, Expedientes, Painel. | 1 |
| `trf3-1g` | password_totp | ✅ Entrou | HTTP 200 no host do tribunal; link de logout e ViewState; menus: Processos, Expedientes, Painel. | 1 |
| `trf6-1g` | browser_totp | ❌ Falhou | `eproc rejected the username/password (page: Nome de usuário ou senha inválida.)` Rejeitado antes do TOTP; sem CAPTCHA. Não repeti para não arriscar bloqueio da conta. | 1 |
| `trf6-2g` | browser_totp | ⛔ Não testado | `trf6-1g` e `trf6-2g` usam a mesma conta (`TRF6_*`), e essa credencial acabou de ser rejeitada. Outro envio arriscaria bloquear a conta. Precisa corrigir usuário (UF + OAB) ou senha antes. | 0 |

## Limites

- Máximo de 2 tentativas por portal; só login e leitura da página inicial. Nenhuma ciência, troca de senha ou alteração de cadastro. Nenhum `.env`, `portals.toml` ou código alterado.
- PJe: rotina `login_session` do conector, sem gravar cache de sessão. Eproc: `client.authenticate` (navegador headless); um CAPTCHA interromperia o teste, e nenhum apareceu.
- Escopo reduzido a João por orientação da coordenação; Jean e Elisandra não foram testados por mim.
