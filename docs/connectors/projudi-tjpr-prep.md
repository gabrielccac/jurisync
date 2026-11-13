# Projudi TJPR (Hérica Tavares & Borba) — preparo sem senha

Data: 2026-11-13. Branch `work/projudi-pr-herica` (de `20de698`). **Nenhuma autenticação foi tentada**: a configuração
da cliente não tem login nem senha do Projudi PR. Só houve 3 GET públicos (home, `autenticacao.do` e o formulário do
Keycloak), sem enviar credenciais.

## Veredito

| Portal | Login | Leituras | Cadastrada | Tentativas de login |
|---|---|---|---|---|
| `tjpr-projudi-1g` (`wired=False`, flavor `legacy-keycloak`) | CPF + senha + TOTP no Keycloak `sso.projudi.tjpr.jus.br`, realm `projudi`. Implementado, **só com respostas simuladas** | processo por número e movimentações, **só simulado**. Documentos **bloqueados** | **Não verificável sem login.** Existe perfil TOTP "PROJUDI PR" nos QRs, o que indica cadastro do autenticador, não prova a conta | **0** (autenticação). 3 GET públicos |

**precisa de login e senha da cliente.** Sem isso, nada foi validado ao vivo.

## Opção A1 e QRs

- A página pública (`home.do`) oferece "Certificado Digital" **somente A3 ICP-Brasil, com PIN do cartão**. O formulário do
  Keycloak tem só CPF/CNPJ + senha, sem certificado. **Não há opção A1.** Zero tentativas com o A1; nenhum TLS contornado.
- Perfis Projudi nos 4 QRs (só arquivo, índice base 0 e emissor; nenhum segredo):

| Arquivo | Índice | Emissor | Perfil |
|---|---|---|---|
| `QR_Herica_1.jpg` | 6 | (vazio) | **PROJUDI PR** |
| `QR_Herica_3.jpg` | 2 | (vazio) | **PROJUDI PR** (mesmo segredo do QR 1) |
| `QR_Herica_1.jpg` | 0 | Projudi | outro Projudi (nome numérico); `QR_Herica_2.jpg` índice 8 é o mesmo |
| `QR_Herica_2.jpg` | 0 | PROJUDI TJRR | TJRR |
| `QR_Herica_4.jpg` | 0, 1 | PDPJ, GERID | sem Projudi PR |

  O `OTP_PROFILE_MATCH` `PROJUDI PR` seleciona um único perfil (TOTP, 6 dígitos, 30 s, SHA1) em `QR_Herica_1` e `QR_Herica_3`.
  `Eproc/TJPR` (QR 2, índice 5) é do eproc, não do Projudi. As chaves `TJPR_1G_*` do `.env` da cliente são do eproc.
- PDPJ: o portal não tem botão PDPJ; o client `projudi-tjpr` do SSO nacional recusa o `redirect_uri` (doc de 2026-11-05).
  Não foi tentado.

## O que foi implementado

- `src/jurisync/connectors/projudi/keycloak.py` (novo): `KeycloakAdapter` configurado por portal (`SSO`), só `tjpr-projudi-1g`.
  - Login: `autenticacao.do` → Keycloak → `POST` com só os campos que o navegador envia (campos ocultos, usuário, senha;
    sem "lembrar-me"/"confiar no dispositivo", sem botão submit) → TOTP quando pedido → volta ao Projudi → confere página logada.
  - Máximo de 1 POST de credencial e 1 de TOTP por `login`, sem repetição. `LoginRejected` aciona o cooldown do `SessionPool`.
  - Sem credencial configurada: `MissingCredentials` **antes de qualquer requisição**. Usuário fora do formato CPF/CNPJ: `ValueError`.
  - CAPTCHA/verificação humana: `HumanVerificationRequired` ("precisa de uma pessoa"); nunca resolvido.
  - Ação de conta do Keycloak (cadastrar autenticador, trocar senha, termos): `AccountActionRequired`; nunca executada.
  - Redirecionamentos só no host do portal e no do SSO; `;jsessionid` é removido dos caminhos.
  - Navegação por link com `_tj` extraído da página (nunca montado à mão), ignorando links de ciência/intimação/citação/
    petição/assinatura/alteração/cadastro/senha/perfil.
  - Leitura: menu "Busca Processual Simples" → formulário com campo de número → resultado (ou abre a única entrada com o número).
  - Documentos: `DocumentsBlocked` em `fetch_index`, `movement_files`, `download_url` e `download`. Abrir um documento pode
    registrar ciência e nada garante o contrário.
- `src/jurisync/portals.py`: `login_url` do flavor `legacy-keycloak` passou a `autenticacao.do` (era uma rota que
  redireciona ao SSO institucional errado). O portal continua `wired=False`.
- O adaptador **não** foi registrado em `server.ADAPTERS`: nenhuma tool MCP o alcança. O TJGO não foi alterado.
- Testes: `tests/connectors/projudi/test_keycloak.py` (26), todos com respostas **sintéticas**.

## Mock versus real

| Real (observado em página pública) | Simulado (hipótese, não validado) |
|---|---|
| Redirecionamento `autenticacao.do` → Keycloak; `form#kc-form-login` com `username`, `password`, `credentialId`, `rememberMe` | Formulário e campos da etapa de TOTP (`kc-otp-login-form`, `otp`), vindos do Keycloak padrão e do PJe |
| Só A3 no portal; sem A1 | Estado logado = não ser a home pública + links `_tj` |
| Links internos usam `_tj` por sessão (doc de 2026-11-05) | Menu "Busca Processual Simples", campo de número, layout do resultado e das movimentações |

Não afirmo leitura validada. Para ligar: registrar o adaptador em `server.ADAPTERS`, `wired=True` e só depois de um
smoke ao vivo com login + processo (e, para documentos, prova de que não registra ciência).

## Dados faltantes para pedir à cliente

1. CPF ou CNPJ do login do Projudi PR (o `username` do Keycloak).
2. Senha do Projudi PR (a do Projudi, não a da intranet).
3. Confirmar que a conta de advogada está ativa no Projudi PR e que o autenticador "PROJUDI PR" é o exigido no login
   (o cadastro de advogado exige A3 ou a OAB-PR).
4. Um número CNJ do TJPR (`8.16`) em que ela seja advogada, para validar a leitura de processo.
5. Se o login atual dela cai em seleção de perfil (advogado/parte) após entrar: qual perfil usar.
6. Turmas Recursais: um processo recursal para validar; a modelagem simulada cobre só o 1º grau.

Variáveis para o processo (não gravadas em arquivo): `TJPR_PROJUDI_1G_USERNAME`, `TJPR_PROJUDI_1G_PASSWORD`,
`TJPR_PROJUDI_1G_2FA_QR` (caminho de `QR_Herica_1.jpg`) e `TJPR_PROJUDI_1G_OTP_PROFILE_MATCH=PROJUDI PR`.

## Verificações

- `pytest` do repositório: 992 passed, 16 skipped, 1 failed. A falha (`test_victor_profile_has_his_three_oabs`) é anterior:
  falha igual sem as minhas mudanças, porque depende de `customers/`, que é ignorado pelo Git e não existe neste worktree.
- `ruff check` e `ruff format --check` limpos nos arquivos novos.
- SHA256 do `Cert_Herica.pfx` igual antes e depois (`e04bce05c0edf6d8…`). Nenhum PEM, `.env` ou `portals.toml` gravado.
