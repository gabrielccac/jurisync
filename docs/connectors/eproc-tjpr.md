# eproc TJPR — validação de 13/11/2026

Validação com A1 da Hérica, sem alterar configuração da cliente. Somente login e leitura.

| Portal | Login real | Leituras reais | Tentativas |
|---|---|---|---|
| `tjpr-1g` | A1 e TOTP aceitos, sessão parada na escolha de perfil | Nenhuma | 2 nesta tarefa + 1 registrada anteriormente |
| `tjpr-2g` | A1 + TOTP + seleção do perfil ADVOGADO principal | Painel, contadores de pendências e prazos, lista de processos; todos retornaram zero itens | 3 |

Nenhum processo ou documento foi validado: não havia amostra na lista do segundo grau.
Não houve novas tentativas no primeiro grau após atingir o limite. A correção da
seleção de perfil foi validada de ponta a ponta somente no segundo grau.

O SSO pode responder diretamente com `kc-otp-login-form`, sem o formulário
`kc-x509-login-info`. Sua ausência não é recusa do certificado quando existe o
formulário OTP. O conector agora aceita os dois caminhos.

Após OTP, o TJPR oferece `frmEscolherUsuario` com botões `acaoLogar(id)`.
O conector segue o POST observado para `pessoa_usuario_logar`, seleciona apenas
ADVOGADO e envia somente `lista_processos`, sem preferência de perfil padrão ou
liberação de dispositivo. Se houver mais de uma OAB, exige um seletor explícito
`TJPR_1G_PROFILE_MATCH` ou `TJPR_2G_PROFILE_MATCH`, informado só no ambiente do
processo. O seletor ignora pontuação e zeros iniciais do número da OAB.

O perfil TOTP usado foi `Eproc/TJPR` do QR 2 (índice 5, base zero). Não foram
gravados segredo, cookies, PIN, `.env` ou `portals.toml`. O A1 permaneceu idêntico.
O cadastro de `tjpr-2g` usa o mesmo conector e as mesmas restrições do primeiro grau.

Testes simulados cobrem OTP direto, recusa sem formulário válido, ambiguidade de
perfis, seletor de OAB, exclusão de campos de dispositivo e isolamento por tribunal.
