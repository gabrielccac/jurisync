# JFSC eproc exploration

Read-only capture completed with the configured account on 2026-09-18. The raw
responses (panel, queues, process detail, event filters, document wrappers, and
malformed-token probes) were used to build the parsers and were deleted on
2026-10-06 because they contained real case data. Re-capture with
`cli/eproc_portal_smoke.py` when a parser needs new evidence.

## Observed surface

- Authentication reached the JFSC panel using its portal-specific account and
  OTP profile configuration.
- The panel exposed two useful read paths:
  `citacao_intimacao_prazo_aberto_listar` and
  `citacao_intimacao_pendente_listar`.
- `retornar_citacao_intimacao_estado` is a GET endpoint taking `estado` and
  optional `usuarioAtual`; it returns JSON with login/request status and
  per-user result/link groups.
- Quick process search posts `txtNumProcessoPesquisaRapida`,
  `btnPesquisaRapidaSubmit`, and `acao_retorno_pesquisa_rapida` to
  `processo_pesquisa_rapida`. A successful lookup returned 302 and the
  same-session redirect opened the process detail page.
- The list pages are POST forms with session-bound `hash` values and hidden
  state (`hdnInfraNroItens`, `hdnInfraItens`, `hdnInfraItensHash`,
  `hdnInfraPaginaAtual`, `hdnInfraHashCriterios`). The sample returned 33 and
  6 rows; the page uses DataTables/client rendering rather than a visible
  server-page selector in this capture.
- A process link is a dynamic `processo_selecionar` URL. The detail page
  returned 77 events and 36 document references in the sample.
- The captured detail sections include cover, reminders, alerts, subjects,
  parties/representatives, additional information, actions, and events.
- Event rows carry stable event IDs, timestamp, description, user metadata,
  party scope, CSS status classes, and document links. Document links include
  `doc`, `evento`, `key`, `mesmoGrau`, and `hash`.
- Event filtering is AJAX through
  `controlador_ajax.php?acao_ajax=montar_tabela_eventos_filtrados`. The page
  JavaScript also supports a dynamic pagination URL and POST `{pagina: N}`;
  the URL and total-page count are returned by the filter response when that
  response is split into the portal's `±` fragments. The unfiltered sample
  returned the complete event table, so a second filtered fixture is still
  needed to exercise page N > 0.
- `acao=acessar_documento` returns a wrapper, not the document body. Depending
  on document/version, the wrapper either embeds an `#conteudoIframe` or loads
  `acessar_documento_implementacao` via JavaScript. Following that URL in the
  same authenticated session produced both real HTML and a real PDF.
- Document hashes are session-bound: reusing a wrapper URL in a new session
  returns `Sem acesso ao documento solicitado. Código de segurança inválido.`
- Missing or malformed document hashes and no-cookie requests returned HTTP
  200 HTML denial bodies rather than an authentication redirect. Adapters
  must classify the body, not rely on the status code alone.
- The document sample contained 11 HTML and 25 PDF references. No restricted
  document was opened and no rate-limit behavior was induced.

## Adapter implications

1. Keep the browser session alive while following process, event, and document
   links; preserve the complete cookie jar, not only `PHPSESSID`.
2. Add bounded list/event reads with dynamic-hash and ISO-8859-1 handling.
3. Implement event filtering plus pagination as a separate read operation.
4. Implement document reads as wrapper-following: accept iframe or JS
   implementation routes, restrict to the JFSC host, preserve query tokens,
   enforce a byte limit, and return `format`, content type, and extraction
   diagnostics.
5. Add fixtures for the two wrapper variants, HTML implementation, PDF
   implementation, event rows, and a multi-page filtered response before
   exposing an eproc MCP connector.

## Remaining capture gaps

- No current queue or event sample exposed a page greater than zero, so
  multi-page handling must remain bounded and fixture-tested until observed.
- Search no-result/error behavior, expired-session behavior, response
  `Content-Disposition`, and cookie metadata beyond the current helper's
  `PHPSESSID` view remain unobserved.
- Related-process, other-grade/appeal, deadlines, and suspension data were not
  present as stable standalone sections in the sampled process. Treat them as
  later, evidence-driven additions rather than part of the first adapter.
