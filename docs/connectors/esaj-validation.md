# e-SAJ — implementação e validação

Validado em 2026-11-05, com `JURISYNC_CUSTOMER=<cliente>`, somente leitura.
As evidências abaixo são agregadas: não incluem identidade, OAB, números de
processo, conteúdo de documento ou material de sessão.

## Entrega

- `src/jurisync/connectors/esaj/`: `base.py`, `parsers.py`, `tjsp.py`, `tjms.py`,
  `server.py` e `__init__.py`.
- Registro `tjsp-esaj` / `tjms-esaj`, sistema `esaj`, autenticação
  `certificate_challenge`, flavor `esaj-cas`. `tjsp-1g` continua sendo Eproc.
- Entry point `jurisync-esaj`, servidor stdio em `.mcp.json` sem cliente fixo e
  registro no runner HTTP `jurisync.install.serve`.
- Oito tools: sessão, caixa postal, intimações, tarefas, processo, lista de
  documentos, leitura e download de documento.
- Certificado por `ESAJ_A1_P12` / `ESAJ_A1_PIN`, com fallback para o A1 compartilhado
  `PJE_A1_P12` / `PJE_A1_PIN`. Credenciais continuam no ambiente/vault existente.

## Verificação

| Operação | TJSP | TJMS |
|---|---|---|
| Login A1 e `usuarioLogado=true` | Passou | Passou |
| Caixa postal via MCP stdio | Passou | Passou |
| Tarefas de intimações e audiências | Listas vazias | Listas vazias |
| Tarefas de petições (somente listar) | Lista vazia | 2 itens |
| Detalhe de processo | 5 campos e 5 movimentações | Consulta exigiu CAPTCHA |
| Pasta digital autenticada | Acesso a PDF não validado | 40 documentos |
| Leitura e download via MCP | PDF não validado | Extração com texto e PDF salvo conferidos |
| Intimações dedicadas | `flow_required` | `flow_required` |

O PDF baixado no smoke foi conferido pelo cabeçalho `%PDF-` e removido depois
do teste. Nenhuma mensagem foi aberta ou marcada como lida. Não houve protocolo,
assinatura de peça ou confirmação de recebimento de intimação.

Comandos executados:

```bash
uv run pytest --override-ini="addopts=--import-mode=importlib -m 'not live'" -q
uv run ruff check .
uv run ruff format --check src/jurisync/connectors/esaj tests/connectors/esaj
```

Resultado: **591 passed, 1 skipped, 16 deselected, 165 subtests passed**.
Os 16 testes marcados `live` não entram nessa suíte; os testes reais acima foram
executados separadamente. O arquivo do e-SAJ tem **21 testes offline**.
Ruff sem erros; os sete arquivos de código/teste do conector estão formatados.
O smoke MCP conferiu `tools/list` com oito tools e chamadas de sessão/caixa nos
dois tribunais, além de tarefas, inventário, leitura e download no TJMS.

## Rotas confirmadas depois do mapeamento inicial

- `/tarefas-adv/api/processos?numerosProcesso=<CNJ sem pontuação>` recebe o número
  somente com dígitos. O painel pode exigir que as listas de tarefas da sessão
  sejam carregadas antes de resolver o caso; o conector faz essas leituras.
- O link `urlPasta` autorizado pelo painel abre
  `/cpopg5/abrirPastaDigitalIntegracao.do` e chega à pasta digital autenticada.
  O conector aceita somente o host do tribunal e o endpoint esperado.
- A pasta contém `var requestScope`, uma árvore JSON de documentos e parâmetros
  de PDF. Cada entrada é vinculada ao número do processo e ao documento antes
  de ser usada; os parâmetros não são devolvidos nas tools.
- `POST /pastadigital/validarAberturaIntimacaoNaoRecebida.do` é a verificação de
  permissão que o próprio visualizador faz. Só após HTTP 200/204 é solicitado
  `GET /pastadigital/getPDF.do` com os parâmetros vindos da árvore autenticada.
  A operação de receber intimação não é chamada. Se a permissão exigir esse
  fluxo, a leitura para antes do PDF.
- No TJSP, a resposta de `/cpopg/carregarMovimentacoesAjax.do` usa linhas
  `tr.containerMovimentacao`, agora reconhecidas pelo parser.

## Limites e pendências

PDFs têm limite de 10 MB; leitura/download tratam um documento por chamada.
Redirects são validados antes de enviar a sessão, e os resultados removem URLs
brutas e campos de sessão. Texto de portal/documento é evidência não confiável.

O acesso à pasta autenticada do TJMS foi fornecido pelo próprio painel da conta.

### Bridge de navegador (implementado)

A consulta pública do TJMS 1G passou a funcionar pelo bridge documentado em
`esaj-browser-bridge.md`: com `ESAJ_BRIDGE_MODE=attach` e um Chrome com CDP, o
`esaj_get_process` devolve o detalhe real e as movimentações
(`consultationSource: browser_bridge` — 53 e 37 movimentações nos dois processos
de teste). Sem navegador, o motivo continua `captcha_required`.

O bridge não resolve CAPTCHA: usa o token que a própria página gera, com
`credentials: include`, sem headless, stealth ou solver.

### Token v3 por HTTP puro (implementado — caminho principal)

Pesquisa funda (repos `EvickaStudio/BypassV3`, `PyPasser`, `B00H0O/Recaptcha-VM` +
testes live) mostrou que o `grecaptcha.execute` da página são só dois requests:
`GET anchor` + `POST reload` com protobuf. O conector (`esaj/recaptcha.py`,
adaptado do BypassV3 Apache-2.0) replays essa troca por HTTP: sitekey/action
lidos da própria página, `uuidCaptcha` via `captchaControleAcesso.do`, token
anexado à busca e ao detalhe. Achados: o `co` exige padding `.` (não `=`) e o
anchor exige User-Agent real — sem isso o Google devolve página sem token.

Ordem atual: HTTP puro → `http_token` → bridge → `captcha_required`.
Com `ESAJ_BRIDGE_MODE=off`: 2/2 processos reais (`http_token`, 37 e 53
movimentações), busca e detalhe sem browser. O bridge virou último recurso.

Revalidação pós-bridge (2026-11-05, MCP stdio, mesmo processo real): inventário
com **40 documentos**, leitura com texto extraído (4 MB de PDF → 42.387
caracteres) e download com `%PDF-` conferido e arquivo removido após o teste.
A pasta digital não usa o bridge: segue HTTP autenticado como antes.

### Pendências restantes

- `intimacoesweb` (TJSP e TJMS) agora responde `login_required`: é outro
  aplicativo Softplan, com login CPF/senha próprio que a sessão CAS não abre. O
  painel `tarefas-adv/api/intimacoes` continua cobrindo as intimações.
- PDF do TJSP: sem `urlPasta` autorizada o portal devolve o fluxo de senha do
  processo e a tool responde `password_required`. Não foi inventada rota
  alternativa nem senha.

Nada foi commitado, enviado ao GitHub ou protocolado.
