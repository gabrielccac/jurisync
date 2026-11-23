# Servidor remoto rodando local: validação ponta a ponta (2026-11-23)

O servidor hospedado (`jurisync-hosted`) rodou nesta máquina como roda na VPS, com o cofre, o banco e os perfis numa
pasta temporária, e foi usado como um escritório usaria: login OAuth, cliente MCP por HTTP (streamable), logins reais
nos portais da Fernanda, só leitura. Código: worktree `chore/hardening` em `704cce4` (havia uma alteração não
commitada de outro agente em `src/jurisync/install/credentials_bundle.py`, só a constante `SCRYPT_N`, sem efeito no
comportamento).

Resultado: **as 5 verificações passaram**. Nenhum erro do servidor; três observações menores no fim.

## Preparação

Tudo numa pasta temporária (`$S`, 0700), apagada no fim:

```bash
export JURISYNC_HOSTED_DIR=$S/hosted          # store.sqlite + vault/
export JURISYNC_CUSTOMERS_DIR=$S/customers    # perfis instalados pelo apply, como /var/lib/jurisync/customers
export JURISYNC_STATE_DIR=$S/state            # sessões e downloads dos conectores
export PJE_SESSION_DIR=$S/pje-sessions        # cache de cookies do PJe
export PYTHONPATH=src
python -m jurisync.hosted.cli init            # chave do cofre gravada direto num arquivo 0600, não exibida
python -m jurisync.hosted.cli provision export --out $S/rollout --passphrase-file $S/rollout.passphrase \
  --customers-dir /home/gabriel/orca/jurisync/customers --customer fernanda barbara
# plan.json: users de fernanda = ["validacao@fernanda.test"]
python -m jurisync.hosted.cli provision apply $S/rollout --passphrase-file $S/rollout.passphrase \
  --user-passwords $S/user-passwords.txt
```

- `export`: `fernanda: 41 portal(is) + advbox | conectores pje,eproc,djen,advbox | cofre: 64 nome(s), 1 arquivo(s)`;
  `barbara: só local (A3 — instalação local): 17 portal(is) ficam na instalação do escritório`. Senha do pacote
  criada 0600 fora da pasta do pacote; `plan.json` e `fernanda.jurisync` 0600.
- `apply`: perfis instalados em `$S/customers/{fernanda,barbara}`; fernanda criada com 64 valores + 1 arquivo (A1),
  usuário criado, senha em arquivo 0600 (não impressa); barbara `registrado como só local`, nada no cofre.
  Nenhum login no apply.
- O `export` usou `--customers-dir` real (só leitura); `apply`/`serve` usaram `JURISYNC_CUSTOMERS_DIR` temporário,
  para o apply não sobrescrever `customers/` do repositório.

## 1. `portal list` e `portal check` — OK

`portal list fernanda` antes: `Certificado A1: <titular>, válido até 09/11/2027` e as 41 linhas (18 Eproc + 22 PJe +
tjms-eproc-1g), todas `certificado A1  2FA=sim  não testado`, mais `advbox  login com senha  não testado`.
`portal list barbara`: `barbara: só local (A3 — instalação local); nenhum portal no servidor`.

Um login por portal, um de cada vez:

| Portal | Sistema | Resultado | Tempo |
|---|---|---|---|
| `trf1-1g` | PJe (A1 + TOTP, Keycloak SSO) | `trf1-1g: login OK` | 5,0 s |
| `tjsc-1g` | Eproc A1 | `tjsc-1g: login OK` | 4,4 s |
| `trt1-1g` | PJe-JT (A1) | `trt1-1g: login OK` | 3,0 s |

`portal list` depois: as três com `OK`, 39 `não testado`. `history` registra `teste de login OK` para cada uma.

Mesma configuração do servidor: `portal check` (`cli.run_check`, cli.py:216) e o `serve` (`TenantGate`, app.py:293)
abrem o escopo pela mesma função `access.tenant_scope`, que aplica `access.tenant_values`: `JURISYNC_CUSTOMER` do
tenant e o `[env].set` do perfil instalado (62 valores fixados para fernanda: `*_CERT`, `*_OTP_PROFILE_INDEX`) por
cima do cofre. O `portal list` usa o mesmo `tenant_values` (cli.py:267).

## 2. `serve` com OAuth real e chamadas MCP — OK

```bash
python -m jurisync.hosted.cli serve --base-url http://127.0.0.1:18765 --host 127.0.0.1 --port 18765
python deploy/smoke_test.py http://127.0.0.1:18765 --plan $S/rollout/plan.json \
  --user validacao@fernanda.test=$S/fernanda.password --call-saved-searches
```

Sem `--open-tenant` nem `JURISYNC_HOSTED_DEV`: o caminho OAuth real. Smoke test: **0 falha(s)** — `/healthz`,
metadados OAuth, issuer, recurso protegido `/mcp`, 401 com `resource_metadata`, registro dinâmico + `/authorize` +
`/login` + PKCE + `/token`, `31 ferramentas (advbox, djen, eproc, pje)`, rascunhos do PJe fora da lista, sistemas =
conectores do plano, `esaj_get_session recusado (escritório sem esaj)`, `djen_list_saved_searches (0 busca(s))`.

Depois, cliente MCP do SDK (`mcp` 2.3.0, `streamable_http_client` + `ClientSession`) com o token OAuth obtido pelos
helpers do smoke test (`sign_in`), uma sessão MCP por chamada:

| Chamada | Resultado |
|---|---|
| `tools/list` | 31 ferramentas. `portal` das 6 ferramentas PJe: enum com os 22 portais PJe da fernanda; das 3 Eproc: os 19 Eproc. Nenhum `pje_draft_*` nem `pje_search_pending_drafts`. |
| `pje_list_inbox` trf1-1g `maxPages=1` | ok, 5,3 s (com o login). Caixa "Pendentes de ciência ou de resposta": 4 de 4, `complete=true`; todas com `cienciaPendente=false` (Diário Eletrônico). |
| `pje_get_process` 1027808-06.2026.4.01.4100 `scope=latest` | ok, 4,2 s. 16 documentos no inventário, 3 expedientes (2 citações, 1 intimação), todos com ciência já registrada. |
| `pje_read_documents` doc 2299541720 (Petição inicial, sem expediente) `textLimit=500` | ok, 1,6 s. PDF 205 249 bytes, `extractionStatus=extracted`. Bloco `ciencia: {"estado": "nenhuma"}` presente. `acknowledgeCiencia` não enviado. |
| `eproc_list_notices` tjsc-1g `open_deadlines` | ok, 8,7 s (com o login). 202 itens, `complete=true`. |
| `eproc_list_notices` tjsc-1g `pending` | ok, 2,6 s, sem login novo. 18 itens. |
| `djen_search_saved` 16–23/11 | erro esperado: `This office has no saved DJEN searches` (fernanda não tem `DJEN_SEARCHES`). |
| `djen_search_communications` processo 10278080620264014100, 01–23/11 | ok, 330 ms, 1 comunicação. (Primeira tentativa com o número formatado foi recusada pela validação, como a descrição do campo avisa.) |

Sessões:

- O `serve` **não** reaproveitou a sessão do `portal check`: o log do serve mostra
  `trf1-1g certificate login complete` às 12:07:54, dentro da primeira chamada (o check foi às 12:06:28, em outro
  processo). Não há cache em disco: `$S/pje-sessions` e `$S/state` ficaram vazios.
- O `serve` entrou uma vez e reaproveitou: um único `certificate login complete` no log inteiro; `pje_get_process` e
  `pje_read_documents` não tiveram nenhuma requisição de login. No Eproc, a primeira chamada levou 8,7 s e a segunda
  2,6 s, com 2 GETs e nenhum processo filho (Chrome) durante a segunda.
- trt1-1g não foi chamado pelo serve (só pelo check), então o serve não entrou lá.

## 3. Credencial trocada sem reiniciar — OK

Com o serve rodando:

```bash
python -m jurisync.hosted.cli portal remove fernanda trf5-tru    # portal não usado
```

Saída `trf5-tru: acesso removido do cofre de fernanda.`; cofre de 65 para 64 nomes. Em seguida, sem reiniciar:

- `tools/list`: o enum `portal` do PJe caiu de 22 para 21, sem `trf5-tru`; Eproc continua com 19.
- `pje_list_inbox` com `portal=trf5-tru`: recusado antes de chegar ao portal (0 requisições no log):
  `pje_list_inbox: no login is configured for trf5-tru on this office; configured: trf1-1g, trf5-1g, ...`.
- `history`: `acesso ao portal removido: trf5-tru`.

Não testado de propósito: que a próxima chamada a trf1-1g/tjsc-1g refaz o login com a nova assinatura das credenciais
(é o comportamento documentado), porque seria um segundo login no mesmo portal no mesmo processo.

## 4. Barbara (escritório só local) — OK

- `user add teste@barbara.test --tenant barbara`: recusado —
  `ValueError: barbara uses its local install: it has no users on the server` (ver observação 2). Sem usuário, não
  há como entrar pelo OAuth.
- `secrets list barbara`: `Valores: -` / `Arquivos: -`.
- Para ver o `/mcp` como barbara sem usuário, um segundo serve de teste na porta 18766 com
  `JURISYNC_HOSTED_DEV=1 ... serve --open-tenant barbara` (modo de teste da documentação; só chamadas sem portal):
  - `tools/list`: **0 ferramentas**.
  - `djen_list_saved_searches` e `pje_list_inbox`: `This office uses its local Jurisync install (A3 — instalação
    local); nothing is served for it here`. Nenhuma requisição a portal no log.
  - `POST /pje/mcp`: 403 `{"error":"insufficient_scope","error_description":"pje is not enabled for this office"}`.
- `calls barbara` registra as duas recusas (`erro NotEnabled`, 0 ms); `history barbara`: `escritório só local`.

## 5. `calls` e `history` — OK

`calls fernanda` (usuário, ferramenta, resultado, duração; sem argumentos):

```
12:10:06  validacao@fernanda.test  pje_list_inbox  erro NotEnabled  2 ms          (trf5-tru removido)
12:09:47  validacao@fernanda.test  djen_search_communications  ok  330 ms
12:09:41  validacao@fernanda.test  djen_search_communications  erro ValidationError  0 ms
12:09:40  validacao@fernanda.test  djen_search_saved  erro ToolError  0 ms
12:09:23  validacao@fernanda.test  eproc_list_notices  ok  2555 ms
12:08:53  validacao@fernanda.test  eproc_list_notices  ok  8657 ms
12:08:34  validacao@fernanda.test  pje_read_documents  ok  1640 ms
12:08:21  validacao@fernanda.test  pje_get_process  ok  4228 ms
12:07:56  validacao@fernanda.test  pje_list_inbox  ok  5313 ms
12:07:04  validacao@fernanda.test  djen_list_saved_searches  ok  0 ms
12:07:04  validacao@fernanda.test  esaj_get_session  erro NotEnabled  0 ms         (smoke test: isolamento)
```

`history fernanda`: `credenciais importadas: <só nomes>`, `usuário criado`, os três `teste de login OK` e
`acesso ao portal removido: trf5-tru`.

## Segurança

- Nenhum valor impresso: chave do cofre, senha do pacote, senha do usuário e token foram direto para arquivos 0600.
- Busca nos logs (`serve`, checks, smoke test): nenhuma ocorrência da senha do usuário, do token OAuth ou da chave do
  cofre; nenhum `Bearer`, `access_token`, `refresh_token` ou cookie de sessão. A linha de acesso do uvicorn mostra o
  `/authorize` com `state` e `code_challenge` (públicos por definição do PKCE), nunca o `code` nem o token.
- Leitura apenas: nenhuma escrita, nenhuma ciência; o único documento lido não está ligado a expediente.

## Observações (menores, nenhuma bloqueia)

1. **`portal list` continua mostrando um portal removido.** Depois de `portal remove fernanda trf5-tru`, a lista
   ainda traz `trf5-tru  PJe  certificado A1  2FA=não  não testado`, enquanto o servidor já não o oferece. A lista vem
   do perfil (`office_portals`, cli.py:277 → `customers.load_setup`), e o método aparece como A1 porque o
   `PJE_TRF5_TRU_CERT=true` fixado no perfil e o A1 do escritório continuam. Sugestão: marcar "fora do servidor"
   quando o portal não está em `configured_portals()` no escopo do escritório.
2. **`user add` num escritório só local termina em traceback.** cli.py:533 chama `Store.add_user`, que levanta
   `ValueError` (store.py:197); o CLI não converte em `SystemExit` com mensagem, como faz em `tenant connectors` e
   `import-bundle`. A recusa está certa; só a forma.
3. **`provision export` cria `rollout/profiles/` com 0775** (os arquivos `plan.json` e `.jurisync` ficam 0600).
   Os perfis não têm segredos, então é só consistência.

## Limpeza

Os dois `serve` foram parados (`/healthz` sem resposta, nenhum processo restante). A pasta temporária (store,
cofre, pacote, senha do pacote, senhas de usuário, token, perfis instalados, logs) foi apagada. Nenhum arquivo de
código ou teste foi alterado; nada foi commitado.
