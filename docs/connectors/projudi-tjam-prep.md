# Projudi TJAM (Hérica Tavares & Borba) — preparo sem senha

Data: 2026-11-13. Branch `work/projudi-pr-herica`. **Nenhuma autenticação foi tentada**: a configuração da cliente não tem
login nem senha do Projudi AM. Só houve leituras públicas: 1 GET da tela de login, 1 GET do `login.js` e 1 handshake TLS
sem certificado, só para ver se o servidor pede um.

## Veredito

| Portal | Login | Leituras | Cadastrada | Tentativas de login |
|---|---|---|---|---|
| `tjam-1g` (`wired=False`, flavor `legacy-local`) | Formulário local `login` + `senha` + `tck`. Implementado, **só com respostas simuladas**. Sem TOTP observado | processo por número e movimentações, **só simulado**. Documentos **bloqueados** | **Não verificável sem login.** Nenhum QR é do TJAM (o perfil "Projudi" numérico não foi atribuído a ele) | **0** |

**precisa de login e senha da cliente** (login `UF000…` ou CPF/CNPJ, mais a senha do sistema).

## A1 e QR

- **Não há login por certificado no TJAM.** Evidência técnica: `projudi.tjam.jus.br` **não pede certificado de cliente** no
  handshake TLS ("No client certificate CA names sent", ao vivo em 2026-11-13), então não existe ponto onde apresentar o A1. A
  tela de login só tem `login` e `senha`. (O texto do portal, que diz que o sistema usa só o certificado gerado por ele
  mesmo, confirma, mas a conclusão não depende dele.) Zero tentativas com o A1.
- O certificado interno do Projudi serve para assinar petições, não para entrar. O conector não o pede nem usa a senha dele.
- QR: nenhum dos 4 QRs tem perfil do TJAM. O perfil "Projudi" numérico (QR 1 índice 0 e QR 2 índice 8, mesmo segredo) é de
  origem ambígua e **não foi associado ao TJAM**. A tela do TJAM não mostrou segundo fator.

## O que foi implementado

- `src/jurisync/connectors/projudi/tjam.py` (substitui o placeholder que só recusava):
  - `GET usuario/logon.do?actionType=inicio` → `form#formLogin`; lê o sal da tela e calcula `tck = md5(sal + login)` como o
    `login.js` do portal (verificado: o `hex_md5` do portal é o MD5 padrão, igual ao `hashlib` para texto ASCII/latin-1).
  - 1 POST de credencial por `login`, sem repetição, com o `action` da página (com `;jsessionid` e `_tj`) como veio; só o
    destino é validado. O navegador desabilita o botão antes de enviar, então nenhum botão vai no corpo.
  - WAF/CAPTCHA (F5 "Request Rejected" etc.): `HumanVerificationRequired` ("precisa de uma pessoa"), nunca contornado.
    Comentários HTML são ignorados: a tela real mantém um bloco reCAPTCHA **comentado** que não é desafio.
  - Sem credencial: `MissingCredentials` antes de qualquer requisição. Senha errada: `LoginRejected` (o `SessionPool` aplica
    o cooldown).
  - Leituras e documentos bloqueados herdados de `legacy.py`; os rótulos de menu são hipóteses.
- `src/jurisync/portals.py`: `legacy_env_prefixes=("TJAM_PROJUDI",)` além de `TJAM_1G_*`. Continua `wired=False`; adaptador
  **não** registrado em `server.ADAPTERS`; TJGO e `base.Adapter` intactos.
- Testes: `tests/connectors/projudi/test_legacy_local.py` (14), com a estrutura real da tela de login (observada ao vivo) e
  sal e respostas posteriores sintéticos.

## Mock versus real

| Real (observado ao vivo, público, 2026-11-13) | Simulado (hipótese, não validado) |
|---|---|
| `form#formLogin` com `login`, `senha`, `tck`, `latitude`, `longitude`, `locationGranted`; `action` com `;jsessionid` e `_tj` | Resposta ao login: erro "Usuário ou senha inválidos!" (da doc de 2026-11-05) e a página logada |
| `submitLogin()` com `hex_md5("<sal de 64 hex>" + login)` e botão desabilitado | Estado "logado" = sem `formLogin` e com link `actionType=logout` |
| Sem pedido de certificado de cliente no TLS; reCAPTCHA só comentado | Menu de busca, campo de número e layout do resultado; F5 pode reagir ao POST |

Não afirmo leitura validada. Turmas Recursais: **não aplicável/validada**: nada foi observado.

## O que falta

1. **Senha do Projudi AM** e o **login** (`UF000…` ou CPF/CNPJ). O CPF está no e-CPF do A1 (não impresso); o campo aceita CPF/CNPJ,
   mas se o cadastro dela usar `UF000…` o CPF não serve: só o login mostra.
2. Se ela tem conta de advogada no Projudi AM: o cadastro exige CPF + OAB (formulário público) e termo de adesão.
