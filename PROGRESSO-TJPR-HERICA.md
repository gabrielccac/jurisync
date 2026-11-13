# Hérica — TJPR, Projudi e TJSE — 13/11/2026

Branch `feat/tjpr-herica`, worktree `/home/gabriel/orca/jurisync-wt-trt-b`.
Sem push. Somente login e leitura; nenhum `.env` ou `portals.toml` alterado.

| Portal | Login | Leituras reais | Cadastrado/conectado | Tentativas de autenticação |
|---|---|---|---|---|
| eproc `tjpr-1g` | ⚠️ A1 e TOTP aceitos; sessão parou na escolha de perfil | Nenhuma | Já existente; correção compartilhada implementada, smoke final 1G pendente | 2 nesta tarefa + 1 anterior = 3 |
| eproc `tjpr-2g` | ✅ A1 + TOTP direto + perfil ADVOGADO principal; conector nativo completo | Painel, pendências, prazos e lista responderam; zero itens | Sim, novo portal | 3 |
| Projudi `tjpr-projudi-1g` | ❌ A1 recusado: somente A3; senha ausente | Somente simulado; Turmas Recursais não validadas | Preparado, `wired=False`, fora do MCP | 3 A1; 0 senha |
| Projudi `tjrr-1g` | ⛔ Senha ausente; formulário CPF + senha + possível TOTP | Somente simulado | Preparado, `wired=False`, fora do MCP | 0 |
| Projudi `tjam-1g` | ⛔ Senha ausente; login local | Somente simulado | Preparado, `wired=False`, fora do MCP | 0 |
| TJSE Portal do Advogado | ⛔ CAPTCHA de imagem já na entrada: precisa de uma pessoa | Nenhuma | Não conectado | 0 |
| e-SAJ TJAM (opcional) | ⛔ CAS avançou, mas redirecionou para HTTP; bloqueado antes da requisição insegura | Nenhuma | Não cadastrado | 1 A1 |

O eproc e o Projudi do TJPR são sistemas distintos. A recusa A1 no Projudi não
se aplica ao eproc, que aceitou o mesmo certificado.

## Validação real e limites

- **eproc 2G**: a última das três tentativas usou o conector completo, sem atalhos
  de autenticação. A1 → formulário OTP direto → seleção transitória de ADVOGADO
  pela OAB principal → painel. Não existia amostra na lista para validar processo
  ou documento. Todos os contadores/listas lidos retornaram zero.
- **eproc 1G**: a recusa anterior do conector era uma suposição errada de que o
  formulário Continue sempre existe. Nesta tarefa A1 e OTP passaram, mas a
  sondagem inicial não tratava os botões de seleção de perfil. O ajuste foi
  validado no 2G; não se repetiu autenticação 1G após o limite conservador de três.
- **Projudi PR/RR/AM**: exploração pública, testes com respostas simuladas e
  tentativas A1 do PR foram executados por Claudio (Sonnet 5.5). Integrei os
  commits, revisei o código e repeti os testes; não repeti os logins deles.
  A mensagem informada por Claudio para o Projudi PR é:
  `Certificado utilizado não é A3. Somente certificados A3 são permitidos pra login.`
  O redirecionamento observado foi `ERROA3=601`. O limite de três A1 foi atingido.
- **RR e AM**: nenhum fluxo A1 utilizável foi encontrado nas páginas públicas
  observadas. Isso não equivale a uma recusa de certificado testada nesses dois.
- **TJSE**: entrada pública em <https://www.tjse.jus.br/portaldoadvogado/>;
  destino interno anunciado pelo JavaScript:
  <https://www.tjse.jus.br/tjnet/portaladv/index.wsp>. Formulário CPF + senha +
  CAPTCHA. Não se apresentou A1 nem se enviou senha/código. A janela 2FA já está
  no HTML inicial e não comprova autenticação nem envio de código.
- **e-SAJ TJAM**: uma assinatura A1 enviada, seguida de retorno ao fluxo de perfil.
  Erro exato do guard de transporte:
  `Unexpected e-SAJ login redirect URL: http://consultasaj.tjam.jus.br/esajperfil/abrirConfirmacaoDados.do`.
  Não foi seguido HTTP nem confirmada atualização de dados. Sem painel validado.

## Alterações e commits locais

| Commit | Alteração |
|---|---|
| `2041d5f` | eproc: aceitar OTP direto, selecionar perfil TJPR sem gravar padrão; cadastrar `tjpr-2g`; testes e relatório |
| `f7c8a04` | Projudi PR: Keycloak e leitura básica preparados, apenas simulados |
| `becc949` | Evidência de recusa A1 no Projudi PR e correção da lista de dados faltantes |
| `be4cebd` | Projudi RR: SSO próprio e preparo com respostas simuladas |
| `9afcb2f` | Projudi AM: login local com `tck` MD5 e preparo com respostas simuladas |
| `30da407` | OAB precisa coincidir inteira; campos ocultos de dispositivo/padrão excluídos |

Código: `src/jurisync/connectors/eproc/certificate.py`,
`src/jurisync/connectors/projudi/keycloak.py`, `legacy.py`, `tjam.py` e
`src/jurisync/portals.py`. Testes em `tests/connectors/eproc/`,
`tests/connectors/projudi/` e `tests/core/test_portals.py`.

Os adaptadores Projudi continuam desligados no registro e não estão em
`server.ADAPTERS`: login, estado autenticado, pesquisa e movimentos pós-login são
hipóteses cobertas por páginas sintéticas. Documentos permanecem bloqueados até
haver prova de leitura sem ciência. Nenhum teste simulado é apresentado como
validação ao vivo.

Relatórios técnicos em `docs/connectors/`:
`eproc-tjpr.md`, `projudi-tjpr-prep.md`, `projudi-tjrr-prep.md`,
`projudi-tjam-prep.md`, `tjse-portaladv.md`, `esaj-tjam-probe.md`.

## Dados que ainda precisam ser obtidos

1. Senha do **Projudi PR**.
2. Senha do **Projudi RR**.
3. Senha do **Projudi AM** e o identificador de login, caso seja um usuário `UF...`
   em vez do CPF.
4. Para o **TJSE**, senha própria do Portal do Advogado e acesso assistido por
   uma pessoa devido ao CAPTCHA. Nenhuma senha foi solicitada em chat.

O CPF do PR/RR já está no A1. Os perfis `PROJUDI PR` (QR1/índice6 ou QR3/índice2)
 e `PROJUDI TJRR` (QR2/índice0) já existem. O perfil Projudi genérico dos QRs não
foi atribuído ao TJAM sem evidência. Não é preciso pedir novamente CPF, OAB,
QR do PR/RR ou número de processo: a amostra de leitura pode vir do painel/DJEN.

O eproc usa `Eproc/TJPR` do QR2/índice5. Com várias OABs, o seletor
`TJPR_1G_PROFILE_MATCH`/`TJPR_2G_PROFILE_MATCH` precisa ser informado no processo;
na validação foi escolhida a OAB principal já fornecida, sem alterar arquivos.

## Verificação e higiene

- Testes dos conectores e registro: **176 passed, 140 subtests passed**.
- Suíte completa: **1014 passed, 16 skipped, 328 subtests passed, 1 failed**.
  Falha: `DjenServerTest.test_victor_profile_has_his_three_oabs`. Neste worktree
  falta `customers/victor/settings.toml`; código e teste DJEN não foram alterados.
  O mesmo teste passou (**1 passed**) apontando `JURISYNC_CUSTOMERS_DIR` somente
  no processo para a pasta de clientes original. Nenhum arquivo foi copiado para
  contornar o teste.
- `ruff check`, `ruff format --check` (11 arquivos tocados) e `git diff --check` limpos.
- SHA-256 do A1 antes e depois:
  `e04bce05c0edf6d856d22973a447ee8c130948851dc8228ea0e2efd13df69a6b`.
- Nenhum `.env`, `portals.toml`, cadastro, ciência, perfil padrão ou liberação de
  dispositivo alterado. Nenhum segredo ou cookie salvo nos relatórios.
- PEMs efêmeros são removidos pelo contexto do conector. Scripts e resultados
  temporários próprios foram removidos ao concluir. Permanecem código, testes,
  relatórios e commits locais; nenhum push.
