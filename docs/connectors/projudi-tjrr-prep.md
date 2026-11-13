# Projudi TJRR (Hérica Tavares & Borba) — preparo sem senha

Data: 2026-11-13. Branch `work/projudi-pr-herica`. **Nenhuma autenticação foi tentada**: a configuração da cliente não tem
login nem senha do Projudi RR. Só houve leituras públicas: GET de `tjpr/autenticacao.do` (o redirecionamento) e do
formulário do Keycloak, e 2 handshakes TLS sem certificado, apenas para ver se o servidor pede um.

## Veredito

| Portal | Login | Leituras | Cadastrada | Tentativas de login |
|---|---|---|---|---|
| `tjrr-1g` (`wired=False`, flavor `legacy-keycloak`) | CPF + senha + TOTP no Keycloak `keycloak.tjrr.jus.br`, realm `projudi`, client `projudi-client`. Implementado, **só com respostas simuladas** | processo por número e movimentações, **só simulado**. Documentos **bloqueados** | **Não verificável sem login.** Há um QR "PROJUDI TJRR" (indício, não prova da conta) | **0** |

**precisa de login e senha da cliente.**

## A1 e QR

- **Não há login por certificado no TJRR.** Evidência técnica, não só texto: `projudi.tjrr.jus.br` e `keycloak.tjrr.jus.br`
  não pedem certificado de cliente no handshake TLS ("No client certificate CA names sent", conferido ao vivo em
  2026-11-13), e o formulário do Keycloak só tem CPF + senha. A rota legada `token/autenticacaoCertificado.do` do TJRR
  redireciona para o serviço de certificado do **TJPR** (`projudicrt.tjpr.jus.br`); esse serviço, testado com o A1 da
  Hérica, recusa A1 ("Certificado utilizado não é A3", ver `projudi-tjpr-prep.md`). Não repeti o teste por esse caminho:
  não é um login do TJRR e o token seria do TJRR.
- QR: `QR_Herica_2.jpg` índice 0, emissor **`PROJUDI TJRR`**. O seletor explícito
  `TJRR_PROJUDI_OTP_PROFILE_MATCH=PROJUDI TJRR` (já no `.env` da cliente) escolhe um único perfil (TOTP, 6 dígitos, 30 s,
  SHA1) entre os 10 do arquivo. O perfil "Projudi" numérico (QR 1 índice 0 e QR 2 índice 8) **não** foi associado a
  nenhum portal: pode ser outro Projudi; não se adivinha. O teste `test_the_explicit_profile_is_chosen_and_an_ambiguous_export_is_never_guessed`
  garante que um export ambíguo sem `OTP_PROFILE_MATCH` dá erro em vez de escolher.
- As chaves do `.env` usam o prefixo `TJRR_PROJUDI_*`; o registro agora também lê esse prefixo (`legacy_env_prefixes`),
  além de `TJRR_1G_*`.

## O que foi implementado

- `src/jurisync/connectors/projudi/legacy.py` (novo): leituras compartilhadas das gerações legadas (menu por link com `_tj`
  tirado da página, formulário de busca, resultado, bloqueio de links de ciência/intimação/citação/petição/assinatura/
  alteração/cadastro/senha/perfil, CAPTCHA/WAF → "precisa de uma pessoa" ignorando comentários HTML, documentos bloqueados).
- `src/jurisync/connectors/projudi/keycloak.py`: generalizado para TJRR por configuração (`SSO`): host
  `keycloak.tjrr.jus.br`, realm `projudi`, **sem `/auth`** no caminho, entrada `tjpr/autenticacao.do`. As regras de
  segurança do login são as do TJPR (1 POST de credencial + no máximo 1 de TOTP, só campos de navegador, nada de
  "lembrar-me", CAPTCHA e ações de conta param).
- `src/jurisync/portals.py`: campo `login_path` (TJRR: `tjpr/autenticacao.do`) e `legacy_env_prefixes=("TJRR_PROJUDI",)`.
  Continua `wired=False`; adaptador **não** registrado em `server.ADAPTERS`; TJGO e `base.Adapter` intactos.
- Testes: `tests/connectors/projudi/test_keycloak.py` (6 novos para TJRR; 32 no total), todos com respostas sintéticas.

## Mock versus real

| Real (observado ao vivo, público, 2026-11-13) | Simulado (hipótese, não validado) |
|---|---|
| `tjpr/autenticacao.do` → `keycloak.tjrr.jus.br/realms/projudi/...` (sem `/auth`), client `projudi-client` | Etapa de TOTP (`kc-otp-login-form`, campo `otp`) |
| `form#kc-form-login` com `username`, `password`, `credentialId`, `rememberMe`; sem CAPTCHA, sem botão de certificado | Estado "logado" (não ser a home pública + links `_tj`) |
| Sem pedido de certificado de cliente no TLS | Menu "Busca Processual Simples", campo de número e layout do resultado |

Não afirmo leitura validada. Turmas Recursais: **não validadas** (nada específico foi observado).

## O que falta

1. **Senha do Projudi RR.** Único dado que falta de fato.
2. CPF do login: o e-CPF do A1 traz o CPF (não impresso); o `USERNAME` ainda vem de `TJRR_PROJUDI_USERNAME`.
3. A doc do tribunal avisa: 5 senhas ou 5 TOTP errados bloqueiam o perfil; por isso o conector não repete.
4. Credenciamento do TJRR é manual (OAB-RR/TJRR): se a Hérica não tem conta, o login não existe; só o login revela.
