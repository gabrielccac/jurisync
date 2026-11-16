# Thaile Dantas — validação de login em 13/11/2026

Testes ao vivo apenas de autenticação e leitura da página inicial, com `JURISYNC_CUSTOMER=thaile`. As credenciais vêm de OCR da imagem de 02/11 e não foram conferidas. Nenhuma senha, usuário, QR ou código de autenticador é reproduzido aqui. `tjsp-esaj` ficou fora do teste.

| Portal | Método | Resultado | Tentativas |
|---|---|---|---|
| `tjsp-1g` | browser_totp | ✅ Painel do Advogado (eproc1g.tjsp.jus.br), formulário `frmPainelAdvogado`, título ":: eproc - Painel do Advogado ::" | 1 |
| `tjsc-1g` | browser_totp | ⛔ Senha aceita; o portal mostra aviso sobre ativar autenticação em 2 fatores (inscrição do autenticador). Seguir alteraria o cadastro. Mensagem do conector na tentativa: "Timed out waiting for the next eproc login step" | 2 |
| `tjsc-2g` | browser_totp | ⛔ Mesmo caso: senha aceita; página de aviso de ativação de 2 fatores (texto lido na tentativa 2). | 2 |
| `tjrs-1g` | browser_totp | ❌ "eproc rejected the username/password (page: Nome de usuário ou senha inválida.)" | 1 |
| `tjba-1g` | password_totp | ❌ "OTP submission did not redirect; the code may be invalid or expired" (usuário e senha passaram; falhou o TOTP) | 2 |
| `tjpb-1g` | password_totp | ❌ "The portal rejected the username/password; use the CPF/username from the browser request" | 1 |
| `tjrj-1g` | password_totp | ❌ "The portal reports an expired password; reset it through the portal and update the configured password" | 1 |
| `tjmt-1g` | password_totp | ❌ "The portal reports an expired password; reset it through the portal and update the configured password" | 1 |
| `tjpe-1g` | password_totp | ❌ "The portal rejected the username/password; use the CPF/username from the browser request" | 1 |
| `trf3-1g` | password_totp | ❌ "OTP submission did not redirect; the code may be invalid or expired" (usuário e senha passaram; falhou o TOTP) | 2 |

Todas as mensagens ❌ acima têm o sufixo "(Form 'kc-otp-login-form' was not returned by ...)" nos portais PJe de usuário/senha, com a URL do SSO omitida.

## Credenciais compartilhadas (comparação por hash SHA-256, sem valores)

- Grupo A, mesmo usuário e mesma senha: `tjsp-1g`, `tjsc-1g`, `tjsc-2g`, `tjrs-1g`. O `tjsp-1g` entrou e o TJSC aceitou a senha, então o par provavelmente não tem erro de OCR. O `tjrs-1g` rejeita o mesmo par: o TJRS pode ter cadastro ou senha diferente, não é provável erro de leitura.
- Grupo B, mesmo usuário e mesma senha: `tjpb-1g`, `tjrj-1g`, `tjpe-1g`. Três respostas distintas do SSO para o mesmo par (duas "rejeitada", uma "expirada").
- Mesmo usuário do grupo B, senha diferente: `tjmt-1g` (senha expirada) e `trf3-1g` (senha aceita, TOTP recusado).
- `tjba-1g`: usuário diferente de todos, senha igual à do grupo B (aceita; TOTP recusado).

## Observações

- TOTP recusado em `tjba-1g` e `trf3-1g` nas duas tentativas, com intervalo de mais de 30 s entre elas. O relógio local está correto (data do servidor conferida). Pode ser perfil errado do autenticador (`*_OTP_PROFILE_INDEX`) ou export de 09/11 trocado; não verificado.
- O README diz que faltava o QR, mas `.env` já tem `*_2FA_DATA` para os 10 portais; nenhum falhou com "Set ..._2FA_DATA". O README está desatualizado.
- `portals.toml` declara `password_totp` para tjba, tjpb, tjrj e tjmt, mas o registro do Jurisync os marca como `certificate_totp`. Sem `PJE_A1_P12` configurado o conector usa usuário/senha, que foi o fluxo testado.
- Para TJSC, não foi possível distinguir mais; o conector não trata a página de inscrição de 2 fatores. Resolver exige ação da titular no portal.
- Nenhum CAPTCHA ou código por e-mail apareceu. Navegador sempre headless. Nenhum portal passou de 2 tentativas.
- TJPB, TJPE, TJRS: não repeti, pois um erro de usuário/senha não muda na segunda tentativa. TJRJ e TJMT: senha expirada, sem tentativa de troca.
- Nenhuma ciência, troca de senha ou cadastro. Nenhum `.env`, `portals.toml` ou código alterado. Os PJe foram logados direto, sem gravar cache de sessão.
