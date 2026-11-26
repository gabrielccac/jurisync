# Tucujuris (TJAP)

Conector `connectors/tucujuris`, servidor MCP `jurisync-tucujuris` (`python -m jurisync.connectors.tucujuris.server`).
Portal `tjap-tucujuris` em `portals.py` (sistema `tucujuris`, prefixo `TJAP_TUCUJURIS`, CNJ J=8, TR=03,
`guess_by_number=False`). Somente leitura em relação ao portal.

O Tucujuris é o sistema próprio do TJAP. Não é o PJe do TJAP (`tjap-1g`, `pje.tjap.jus.br/1g/`). Os números CNJ
dos dois sistemas têm o mesmo formato, e nada no número indica onde o processo tramita. Por isso o número sozinho
nunca leva ao Tucujuris: só um link `tucujuris.tjap.jus.br` leva (`portals.for_host`).

## Arquitetura do portal

- SPA em Knockout + RequireJS, servida em `https://tucujuris.tjap.jus.br/pages/<pagina>/<pagina>.html`. A API JSON
  fica em `https://tucujuris.tjap.jus.br/api/`. A configuração está em `js/config.js` (`api`,
  `repos_api = /repositorio`, `ws_url = wss://...:3333`).
- O site está atrás da Cloudflare. O `curl` comum recebe um desafio (`cf-mitigated: challenge`, HTTP 403). O
  curl_cffi com o perfil `chrome` passa. Se aparecer um desafio, o conector levanta `HumanVerificationRequired` e
  não tenta resolver nada.
- Formato das respostas (`js/ds.js`): `{"ambiente": "producao", "status": "OK", "dados": ...}` em caso de sucesso, ou
  `{"status": "ERRO", "mensagem": ...}` em caso de erro. HTTP 401 quer dizer que a sessão acabou (o SPA volta ao
  login). As chamadas autenticadas levam o header `Auth-Token: <token do login>`, mais `Tucujuris-Front-Url` com a
  URL da página. O SPA guarda o token no localStorage e não usa cookie de sessão. Parâmetros GET em lista vão no
  formato do jQuery (`tipos[]=1&tipos[]=4`).

## Login

1. `GET /pages/login/login.html`: carrega os cookies da Cloudflare e detecta um desafio antes de enviar a credencial.
2. `POST /api/publico/login` com `{"login", "senha", "permanente": false, "fonte": "tucujuris"}`, enviado **uma
   vez só**. Se a resposta for `ERRO`, o conector levanta `LoginRejected` e o `SessionPool` não tenta de novo até
   acabar o cool-down. Em caso de sucesso, `dados.token` é a sessão e `dados.situcao_segundo_fator` (com o erro de
   grafia do próprio portal) diz o estado do segundo fator.
3. Com `verificacao_pendente`, vem `POST /api/usuario/segundo-fator-verificar {"codigo": "<TOTP>"}` com o token.
   Se o código vencer em menos de 8 s, o conector espera a próxima janela. Se o código for recusado, levanta
   `LoginRejected`. Quando a resposta pede `configuracao_pendente` (cadastrar autenticador) ou `requer_trocar_senha`,
   o conector levanta `LoginRejected` e não faz nada no portal.
4. `GET /api/usuario/buscar-varas-disponiveis`: se a conta precisar escolher uma lotação (servidores), o conector
   recusa. Advogado não tem lotação.

**Formato do usuário**: a página de login diz que o **advogado** usa o número da OAB seguido da sigla da seccional
(`9999AP`). Matrícula vale para magistrados e servidores, e-mail para cidadãos, e CPF sem pontos para procuradores,
defensores e peritos. O campo não tem máscara. `client.login_name` normaliza `OAB/AP 1234`, `1234/AP` e `AP-01234`
para `1234AP`, e um CPF com pontuação para só os dígitos. O CPF de um advogado é recusado com "Essa conta de
usuário não existe" (validação de 16/11/2026).

Variáveis: `TJAP_TUCUJURIS_USERNAME`, `TJAP_TUCUJURIS_PASSWORD`, `TJAP_TUCUJURIS_2FA_DATA` ou `TJAP_TUCUJURIS_2FA_QR`,
e `TJAP_TUCUJURIS_OTP_PROFILE_MATCH` (ou `_OTP_PROFILE_INDEX`) quando o export tem várias contas.

Sessão: o pool revalida a sessão depois de 120 s ociosa, com `GET /api/usuario/segundo-fator-situacao`. Uma
resposta 401 dispara um novo login, uma única vez, e a chamada é repetida.

## Tools

| Tool | Endpoints (todos GET) |
|---|---|
| `tucujuris_list_processes(archived, query)` | `autos/buscar-autos-pesssoa-esta-envolvida?situacao_processo=0/1` ("Meus processos"; a rota tem três "s" no próprio portal) |
| `tucujuris_get_process(process_number, include_documents)` | o número é procurado em "Meus processos"; depois vêm `publico/buscar-detalhes-autos-consulta-publica` (cabeçalho, capa com partes e advogados, movimentos), `publico/buscar-anexos-processo-consulta-publica` (arquivos) e `comunicacoes-processuais/buscar-comunicacoes?situacao=porprocesso` (marca o que está pendente) |
| `tucujuris_list_documents(process_number, movement_id)` | idem; com movimento: `publico/buscar-anexos-movimento-consulta-publica` |
| `tucujuris_read_documents(process_number, document_ids)` | `publico/download-anexo-movimento-consulta-publica?nome_tabela&id_tabela&nome_campo&chave_consumo`, ou `/repositorio/<link_stream>` |
| `tucujuris_download_document(process_number, document_id)` | idem; salva em `JURISYNC_DOCUMENTS_DIR` |
| `tucujuris_list_intimacoes(situacao, periodo, process_number)` | `comunicacoes-processuais/buscar-periodos-comunicacoes`, `.../buscar-tipos-comunicacao-periodo` e `.../buscar-comunicacoes` |

Os ids de documento vêm sempre do índice atual do processo. Nome de tabela, coluna, chave de consumo e token nunca
aparecem no resultado. As mídias hospedadas fora do Tucujuris (`midia_fora_do_tucujuris`/`url_arquivo`) são listadas
com `outsideTucujuris`, mas o conector não as busca.

Um processo que não está em "Meus processos" só aparece na consulta pública (`publico/buscar-autos-consulta-publica`),
que exige um "passe" de CAPTCHA (Turnstile). Com o usuário logado, o SPA pede o passe em `publico/buscar-passe-captcha`.
Se esse passe vier vazio, o conector responde "asks a CAPTCHA" e não resolve CAPTCHA nenhum. O mesmo acontece quando
um detalhe responde `requer_chave_consumo`.

## Ciência (comunicações processuais)

- A página "Comunicações processuais" (Res. TJAP nº 1074, art. 18) lista as comunicações `naolidas` e `lidas`, ou
  todas as de um processo (`porprocesso`). Uma comunicação não lida tem `dt_intimacao` nulo.
- **A ciência é registrada pelo botão "Marcar como lido"**:
  `POST /api/comunicacoes-processuais/processar-comunicacao {"autos_pessoa_intimacao_id"}`. Depois disso a
  comunicação vai para "lidas" e passa a ter a "Data da comunicação" (`dt_intimacao`). Quando ninguém lê, a ciência
  passa a ser tácita no fim do prazo regulamentar.
- Bloqueio no conector:
  1. `client.guard` só deixa passar uma lista fixa de GETs e dois POSTs (o login e o código). A rota
     `processar-comunicacao` é recusada com qualquer método, antes de qualquer envio. As outras rotas que alteram
     algo (`segundo-fator-confirmar/resetar`, `definir-acesso-vara`, `seguir-processo`, `gerar-chave-consumo`,
     `salvar-senha`) também estão em `FORBIDDEN`.
  2. Os arquivos de um movimento que originou uma comunicação ainda não lida (`id_movimento_origem`) só são lidos ou
     baixados com `acknowledgeCiencia=true` (`CienciaPending` sem ele; decisão de 25/11/2026). Eles aparecem com `pendingCiencia: true`. Não confirmamos que abrir o arquivo
     registra a ciência no Tucujuris (o SPA só registra pelo botão), então o usuário decide.
  3. Testes: `GuardTest.test_marking_a_comunicacao_read_is_refused_for_every_method`,
     `test_a_refused_request_is_never_sent`, `ReadTest.test_documents_of_a_pending_movement_open_only_when_acknowledged`
     e, em cada leitura, `assert_read_only` (só os dois POSTs do login saem).

## Hosted

As credenciais vêm de `portal.env` (com escopo de tenant), e o QR vira `2FA_DATA` no cofre. Isso combina com o padrão
hosted. Mesmo assim, o conector **não** entrou em `TENANT_READY`, porque ainda não foi validado ao vivo. Ele entra lá
depois de um login real e de uma leitura de processo e documento. Está registrado em `install/serve.py`
(`jurisync-http tucujuris`), em `.mcp.json` e no script `jurisync-tucujuris`.

## Limites conhecidos (não verificados ao vivo)

- Os formatos das respostas autenticadas foram deduzidos dos templates do SPA. Ainda não foram observados.
- Download: o conector manda o token no header `Auth-Token`. O SPA manda o token como parâmetro de query (`Auth-Token`),
  porque o visualizador de PDF abre em iframe, e o `ds.download` também envia `Auth-Login`. Se o backend aceitar só
  a query, o download vai falhar com `access_denied`/`request_failed`.
- Datas saem do jeito que o portal manda.
