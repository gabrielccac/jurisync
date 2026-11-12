# e-SAJ — bridge de navegador para a consulta pública (reCAPTCHA v3)

Status: **implementado, mas rebaixado a último recurso em 2026-11-05** — a consulta
passou a funcionar via token v3 mintado por HTTP puro (`http_token`, sem browser).
O bridge continua como fallback quando o caminho HTTP falha.

## Problema

Só a consulta pública do TJMS 1G (`/cpopg5/search.do`) exige reCAPTCHA v3. O token nasce
em JavaScript dentro da página; `requests`/`curl_cffi`/`httpx` não executam JS e recebem
**403**. Testes de bancada:

| Abordagem | Resultado |
|---|---|
| `curl_cffi` puro | 403 |
| Chrome novo headless (`--headless=new`, perfil frio) | token = 0 em 60 s |
| Chrome headed sob Xvfb, perfil frio | token = 0 em 77 s |
| **Chrome já aberto (perfil quente) + `fetch` na página** | **200** com resultado processado |

Conclusão: a única variante que funciona é reutilizar um navegador real, com perfil e IP do
usuário, e executar a requisição no contexto da própria página.

## Princípio

O bridge **não autentica**, **não resolve captcha** e **não esconde automação**. Ele apenas
devolve ao conector o HTML que o portal entregaria ao navegador do próprio usuário. O token
v3 é mintado pelo site; o bridge o lê do DOM e o envia no `fetch` com `credentials: "include"`.

Sem stealth, sem solver externo. Se não houver navegador, o resultado continua sendo
`captcha_required`.

## Modos

| Modo | Comportamento |
|---|---|
| `off` (padrão) | Nada muda: `captcha_required` |
| `attach` | Conecta por CDP a um Chrome já aberto com `--remote-debugging-port` |
| `launch` | Abre Chrome **headless** (`--headless=new`) com `--user-data-dir` persistente e CDP; `ESAJ_BRIDGE_HEADLESS=false` abre com janela |
| `auto` | `attach`; se não houver CDP, `launch` |

Configuração por ambiente:

```
ESAJ_BRIDGE_MODE    = off | attach | launch | auto      (padrão: off)
ESAJ_BRIDGE_CDP     = http://127.0.0.1:9222            (padrão)
ESAJ_BRIDGE_PROFILE = ~/.cache/jurisync/esaj-chrome    (só no launch)
ESAJ_BRIDGE_HEADLESS= true | false                     (padrão: true, só no launch)
ESAJ_BRIDGE_TIMEOUT = 60                               (segundos, total)
ESAJ_BRIDGE_MAX_BYTES = 2097152
```

## Fluxo

1. `base.load_process` faz a consulta HTTP normal.
2. Se a resposta contém captcha (`parsers.captcha`) ou o request devolve 403 → tenta o bridge.
3. `bridge.search(portal, number)`:
   1. resolve um target CDP cujo host seja o do portal;
   2. se não houver, cria aba (`PUT /json/new`) e navega para `/{prefix}/open.do`;
   3. espera `#id_recaptcha_response_token` não vazio (polling de 500 ms até o timeout);
   4. executa `Runtime.evaluate` (`awaitPromise`, `returnByValue`) com:

      ```js
      (async () => {
        const token = document.querySelector('#id_recaptcha_response_token')?.value || '';
        const uuid  = document.querySelector('#uuidCaptcha')?.value || '';
        const p = new URLSearchParams({ /* mesmos params do caminho HTTP */,
          recaptcha_response_token: token, uuidCaptcha: uuid });
        const r = await fetch('/cpopg5/search.do?' + p, { credentials: 'include' });
        const t = await r.text();
        return { status: r.status, url: r.url, text: t.slice(0, MAX) };
      })()
      ```

   5. valida host e path da URL final; devolve `{status, url, text}`.
4. O HTML volta para `parsers.process` / `parsers.movements` sem nenhuma mudança de parser.
5. O resultado do tool ganha `consultationSource: "http" | "browser_bridge"`.

Falha em qualquer passo → `EsajBlocked("Consultation requires CAPTCHA", "captcha_required")`
com `captchaBridge: {mode, attempted, reason}`, nunca uma exceção nova.

## Segurança

- Host: só `portals.esaj(portal).host`. Qualquer outro alvo é recusado antes do CDP.
- Path: allowlist `/{prefix}/open.do`, `/{prefix}/search.do`, `/{prefix}/show.do`.
- Somente GET no `fetch`; o bridge não faz POST, não protocola, não assina.
- Nenhum cookie, header ou token é exportado, logado ou devolvido nas tools.
- HTML devolvido é dado não confiável; passa por `redact_text`/`sanitize_diagnostics`.
- Limite de bytes e de tempo; aba criada pelo bridge é fechada ao final.
- No `launch`: perfil persistente (nunca descartável) e zero flags de stealth
  (`--disable-blink-features=AutomationControlled` e afins são proibidos).
- Sem serviço de resolução de CAPTCHA. Sem proxy residencial.

## Onde entra

| Arquivo | Mudança |
|---|---|
| `src/jurisync/connectors/esaj/bridge.py` (novo) | `BrowserBridge`: `available()`, `status()`, `search()`; CDP via `websocket-client` (síncrono, combina com o resto do conector) |
| `base.py` | `load_process` usa o bridge no ramo de captcha; `document_inventory` não muda |
| `server.py` | `esaj_get_session` passa a devolver `captchaBridge: {mode, available, host}` |
| `pyproject.toml` | declarar `websocket-client` como dependência direta (já existe no ambiente via seleniumbase) |

Nenhuma tool nova. O contrato das oito tools atuais não muda além dos campos informativos.

## Testes

- **Unitários, sem Chrome:** allowlist de host/path, montagem dos params, corte de bytes,
  fallback para `captcha_required`, `available()` sem CDP.
- **CDP falso:** stub do transporte devolvendo respostas gravadas; cobre o laço de espera do
  token e a validação da URL final.
- **Live (marcado `live`):** `ESAJ_BRIDGE_MODE=attach` com um Chrome do operador e um número
  real; conferir `consultationSource == "browser_bridge"` e ausência de dado pessoal no log.
- **Regressão:** com `ESAJ_BRIDGE_MODE=off`, o teste existente de `captcha_required` no TJMS
  continua passando.

## Não-objetivos

- Não faz o conector funcionar em VPS sem navegador: lá o resultado continua `captcha_required`.
- Não automatiza o login CAS do e-SAJ pelo navegador (login segue HTTP + A1).
- Não baixa documentos pelo navegador (pasta digital já funciona por HTTP no TJMS).
- Não se aplica ao TJSP: a busca GET do `cpopg` não tem captcha.
- Não substitui as rotas autenticadas (`tarefas-adv`, caixa postal, pasta digital), que são
  a via principal e não dependem de navegador.

## Rollout

`off` por padrão. No PC do cliente, `ESAJ_BRIDGE_MODE=attach` com o Chrome aberto com
`--remote-debugging-port=9222`. O MCP HTTP local (`127.0.0.1`) já roda na mesma máquina.

## Resultado da validação live (2026-11-05)

Chrome real com `--remote-debugging-port`, sessão A1 do conector injetada via CDP:

| Chamada | Resultado |
|---|---|
| Busca por número inexistente | 200, `search.do`, página “Não existem informações” |
| `esaj_get_process` em processo real do TJMS | `consultationSource: browser_bridge`, 5 campos, 2 partes, **53 movimentações** |
| Segundo processo real | idem, 37 movimentações |
| Chamada MCP `esaj_get_process` (stdio) | sucesso, `browser_bridge`, 37 movimentações |
| `esaj_get_session` | `captchaBridge: {mode: attach, available: true}` |
| Modo `launch` (Chrome novo, perfil próprio) | 200 com token válido |
| **`launch` headless, perfil novo** | **200 em 12 s** — validado depois da cota do reCAPTCHA normalizar; por isso o `launch` abre headless por padrão |

O bridge abre **aba própria** e a fecha ao final; nunca navega a aba do usuário. O código do
`launch` encerra o Chrome que subiu.

Duas correções que a validação exigiu:

1. `parsers.captcha` marcava qualquer página do `cpopg5` como captcha, porque o input
   oculto `recaptcha_response_token` existe até nas páginas de resultado. Agora só conta
   widget visível (`.g-recaptcha`, `[data-sitekey]`, iframe) ou a palavra no texto visível.
2. O link `urlCpo` do painel abre o detalhe por HTTP e o TJMS responde com página de
   CAPTCHA. O `load_process` refaz esse detalhe pelo bridge (`BrowserBridge.fetch`) antes
   de desistir.

O HTML devolvido pelo bridge passa pelos mesmos parsers; nenhum parser mudou por causa dele.
