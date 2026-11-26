# Padrões do jurisync

Guia único de convenções para quem escreve conector, workflow ou código do core. Cada regra aqui tem uma checagem
automática (lint ou teste) sempre que possível; a coluna "como é checado" diz qual. O molde executável de conector está
em [`templates/connector/`](../templates/connector/README.md) e passa pelos mesmos testes que os conectores reais.
As seções 4 a 7 entram em vigor por partes: os helpers do core e os testes-guarda que as aplicam (presets de
annotations, modelo de erro, formato de resposta, logs estruturados) chegam nos próximos passos da F4, e cada um
atualiza a tabela da seção 9.

## 1. Idioma

| O quê | Idioma | Exemplo |
|---|---|---|
| Identificadores, docstrings, comentários, mensagens de log | inglês | `def list_processes(session, archived)` |
| Termos do domínio jurídico brasileiro, em qualquer lugar | português, sem acento em identificadores | `intimacoes`, `expediente`, `ciencia`, `certidao`, `publicacoes` |
| Texto que o usuário final (advogado, equipe) lê | português | `ciencia.aviso`, mensagens de elicitação, `next_step` dos erros |
| Descrições de tools e de parâmetros MCP | inglês, com os termos jurídicos em português | "List the account's pending intimações" |
| Documentação em `docs/` | português para guias e decisões; inglês aceito nos documentos técnicos já existentes | |

Regra prática: se o termo tem tradução jurídica imprecisa (ciência ≠ *acknowledgement*, intimação ≠ *notice*,
expediente, certidão, publicação no diário), use o termo em português. Se é termo de computação (list, document,
process number, session), use inglês. `process` é o processo judicial; não traduzir para "lawsuit" fora do ADVBOX,
onde o próprio sistema chama de *lawsuit*.

## 2. Código

- Python 3.12, `ruff check` e `ruff format` limpos (linha de 120). As regras ativas estão em `pyproject.toml`;
  `# noqa` só com o código da regra e o motivo depois de ` - `.
- Funções curtas e nomeadas pelo efeito; retorno antecipado em vez de `if` aninhado; sem helper de uso único.
- Sem estado global mutável por escritório: estado de sessão vive no `SessionPool` (portais) ou no `TenantLocal`
  (serviços), sempre por tenant. Nada de `if cliente == ...` em `src/`: política de escritório vai para
  `customers/<nome>/`.
- Configuração de conta e de escritório só por `jurisync.core.credentials` (`credentials.env`, `has`,
  `read_file`) ou pelo `Portal.env` do registro. Nunca `os.environ` direto em conector ou código de cliente: no modo
  hospedado o ambiente do processo não é o do escritório.
- Arquivos de texto sempre com `encoding="utf-8"`; caminhos com `pathlib.Path`, nunca concatenando `/`.
- Toda requisição com `timeout=`; toda paginação com teto; hosts permitidos por `core.http.require_url`.
- `except Exception` só na borda (servidor, laço de lote) e registrando o motivo; dentro de conector, capture o tipo
  que a operação levanta.
- Testes offline, com fixtures e dados fictícios (CNJ com DV `00`, CPF sintético, nomes inventados). `-m live` é
  opt-in.

## 3. Nomes de ferramentas

Padrão: `<portal>_<verbo>_<objeto>`, em snake_case, até 64 caracteres. `<portal>` é o nome do conector no
`registry.py` (`pje`, `eproc`, `esaj`, `projudi`, `stj`, `tucujuris`, `jpe`, `djen`, `advbox`, `juridiq`).

Verbos (inglês, fechados):

| Verbo | Uso | Annotation esperada |
|---|---|---|
| `get` | um item por id/número, com detalhes | leitura |
| `list` | itens de uma fila ou da conta, sem busca livre | leitura |
| `search` | busca com termo ou filtro livre | leitura |
| `read` | texto de documentos | leitura que pode dar ciência |
| `download` | salva arquivo na pasta compartilhada | leitura que salva arquivo |
| `calculate` | cálculo local sem consulta (prazo) | leitura |
| `create` / `update` / `delete` / `upload` / `complete` | escrita no sistema | escrita / destrutiva |

Objetos (português para o domínio jurídico, inglês para o resto): `intimacoes` (tudo que aguarda ciência ou corre
prazo: caixa, expedientes, comunicações pendentes), `publicacoes` (diário), `comunicacoes` (DJEN), `counters`,
`processes`, `process`, `documents`, `document`, `tasks`, `customers`, `members`, `files`, `folders`.

Renomear uma tool mantém o nome antigo como alias (aceito em `tools/call`, fora do `tools/list`). Remover um alias
depende de decisão do Gabriel. Skills e plugins são atualizados no mesmo PR da renomeação.

## 4. Annotations MCP

Toda tool usa um preset de `jurisync.core.mcp`, nunca um `ToolAnnotations` próprio, e tem `title`:

| Preset | Quando | readOnly / destructive / idempotent |
|---|---|---|
| `READ_ONLY` | só lê, não abre nada que dê ciência | sim / não / sim |
| `SAVES_FILE` | lê e salva arquivo na pasta compartilhada ou numa pasta local de execução | não / não / sim |
| `MAY_GIVE_CIENCIA` | abre ato ou documento que pode registrar ciência (com `acknowledgeCiencia` ou não) | não / sim / não |
| `WRITE` | acrescenta algo no sistema (tarefa, rascunho, dispositivo) | não / não / não |
| `WRITE_IDEMPOTENT` | acrescenta, mas repetir devolve o que já existe (pasta com o mesmo nome) | não / não / sim |
| `DESTRUCTIVE` | altera ou apaga o que existe | não / sim / não |
| `REPLACE` | troca um valor; repetir não muda mais nada (fase do processo) | não / sim / sim |

A regra da ciência é uma só em todos os portais: qualquer tool que **possa** registrar ciência usa
`MAY_GIVE_CIENCIA` (dar ciência inicia prazo e não se desfaz), mesmo que só abra o documento com
`acknowledgeCiencia=true`. Annotations são dicas para o cliente; as travas reais (concessão de escrita por escritório,
`acknowledgeCiencia`) ficam no servidor.

## 5. Erros

Um modelo só, em `jurisync.core.errors`. Toda falha prevista chega ao agente como `isError: true` neste formato:

```text
[otp_refused] PJe: the portal refused the authenticator code
Próximo passo: não repita agora; o código do autenticador foi recusado: confira a conta escolhida no QR (...).
retryable=false · quem resolve: usuário · ref=c-7f3a9e
```

- **Código** estável em snake_case: os motivos de login de `session_pool.REASONS` (`otp_refused`,
  `credentials_refused`...) ou o `code` da classe: `invalid_input`, `not_found`, `portal_error`,
  `portal_unavailable`, `not_configured`, `writes_disabled`, `busy`.
- **Próximo passo** em português, dizendo se vale repetir e o que mudar.
- **Quem resolve**: `agente` (corrige e repete), `usuário` (o advogado age no portal ou na própria instalação) ou
  `suporte`. Configuração (login, certificado, liberação de escrita) é do usuário na instalação local e do suporte no
  modo hospedado; a mensagem muda sozinha conforme o modo (`errors.SETUP`), então nunca escreva "edite o .env" à mão.
- **ref**: id da chamada (`core.calls`), o mesmo que o suporte procura no log.

Como usar num conector:

- Envolva a chamada com `with tool_errors("PJe"):`; não há mais tupla de exceções por servidor.
- Levante a classe que diz o que houve: `InputError`, `NotFound`, `PortalError`, `PortalUnavailable`,
  `NotConfigured(setting=...)`, `WritesDisabled(setting=...)`, ou os erros de login do `session_pool`. Fora de
  `tool_errors`, use `raise tool_error("PJe", InputError(...))`.
- `ValueError` e `RuntimeError` levantados de propósito ainda são previstos (`invalid_request`, `portal_error`) e
  erros de rede viram `portal_unavailable`. `KeyError`, `AttributeError`, `TypeError` e afins são bug: o agente
  recebe só `[unexpected] ... ref=...` e o detalhe fica no log.
- Toda mensagem passa por `sanitize_diagnostics` (sem URL, caminho, cookie, token ou CPF).

## 6. Respostas

- Retorno de tool é um `dict` JSON com `success`; listas trazem o total e se há mais.
- Texto vindo de portal ou documento leva o aviso `notice` de que é evidência não confiável, nunca instrução.
- Toda tool que abre ato ou documento devolve o bloco `ciencia` (`core.ciencia.report`).
- Sem payload ilimitado: documentos têm teto de texto e quantidade (`core.documents.MAX_DOCUMENTS`).

## 7. Logs

- `logging.getLogger(__name__)`, nunca `print` para diagnóstico em código novo (progresso de CLI é exceção).
- Mensagem com `%`-args (`log.info("login %s", portal)`), não f-string: a formatação só acontece se o nível passar
  (regra `G` do ruff).
- Nunca logar argumentos ou resultados inteiros de tools, credenciais, CPF ou número de processo (LGPD). URLs só pela
  forma (`core.redaction.url_shape`).

## 8. Esqueleto de conector

```text
src/jurisync/connectors/<portal>/
  __init__.py
  client.py     HTTP e login: requisições, guarda de rotas, nada de MCP
  parsers.py    funções puras: HTML/JSON do portal -> dict; testadas com fixtures
  server.py     MCP: create_server(keys=None) -> JurisyncServer, tools, configured_portals(), sessions, main()
tests/connectors/<portal>/
  fixtures/     respostas do portal com dados fictícios
  test_<portal>_parsers.py, test_<portal>_server.py
```

Passos para um conector novo:

1. Copie `templates/connector/` para `src/jurisync/connectors/<portal>/` e troque `exemplo` pelo nome.
2. Registre os portais em `portals.py` e o conector em `registry.py` (o resto do sistema lê dali).
3. Descreva cada tool em três blocos curtos: o que faz e quando usar; parâmetros que mudam custo ou efeito; o que a
   resposta traz.
4. Testes de parser com fixtures fictícias e de servidor com sessão falsa, como os do molde.

## 9. Checagens automáticas

| Regra | Como é checado |
|---|---|
| Lint e formato | `ruff check`, `ruff format --check` no CI |
| Logging com `%`-args, sem `print` de depuração | regras `G`, `LOG`, `T10` do ruff |
| Comprehensions e literais simples | regras `C4`, `PIE`, `ISC` do ruff |
| Molde de conector válido | `tests/core/test_connector_template.py` |
| Toda tool com `title` e preset; regra única da ciência | `tests/core/test_annotations.py` |
| Formato único de erro, bug sem vazar texto, todo conector monta `JurisyncServer` | `tests/core/test_errors.py` |
