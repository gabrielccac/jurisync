# DJEN / Comunica PJe — guia de tracking TRF5 (sessão 29/09/2026)

Fonte primária de intimações para o escritório: `https://comunica.pje.jus.br/`
(Diário de Justiça Eletrônico Nacional + editais). PJe `listView.seam` e DataJud
são fontes secundárias (detalhe dos autos / metadados por número).

## 1. Advogados monitorados (descoberta da sessão)

- VICTOR HUGO VALERIANO PINTO — **OAB/PE 42543** (principal) e **OAB/PB 14663**
  (atuação TRF5). Textos DJEN citam `VICTOR HUGO VALERIANO PINTO - PB14663`.
- GRAZIELE ALVES DA SILVA — **OAB/PE 48647** (aparece junto em quase tudo).
- Sociedade: VICTOR HUGO VALERIANO PINTO ADVOGADOS ASSOCIADOS —
  OAB/PE sociedade 3.208, CNPJ 35.478.241/0001-25.
- Perfil típico: JEF previdenciário x INSS. Varas PE: 18ª e 38ª (Serra Talhada),
  20ª (Salgueiro), 8ª.

OABs pertencem ao **perfil do cliente** (`customers/victor/`), nunca
hardcoded na integração.

## 2. O que cada fonte responde

| Fonte | Serve para | Não serve para |
|---|---|---|
| Comunica API (`comunicaapi.pje.jus.br/api/v1/comunicacao`, sem auth) | intimações por OAB/UF/tribunal/data; texto + destinatários + certidão PDF | capa por OAB sem janela; autos completos |
| PJe `listView.seam` por tribunal (ex `pje.trf5.jus.br/pjeconsulta/...`) | achar processo por OAB/nome (manual, captcha + motivação Res 121) | bulk; automação total |
| DataJud API pública (por `numeroProcesso`, alias tribunal) | capa + movimentos em lote | busca por OAB/nome (glossário não tem esses campos; Portaria 374/2026) |
| Escavador/Jusbrasil | volume agregado (ex 21.001 processos, 98% como advogado) | prova oficial; números mascarados no público |

## 3. Queries testadas (todas com `Accept: application/json`, IP brasileiro)

Central CNJ `cnj.jus.br/pjecnj/ConsultaPublica/listView.seam`: **GET 405 e POST 405**
+ `Human Verification` — endpoint desativado. Usar o PJe de cada tribunal.

```bash
# Base por OAB/UF/janela (janelas curtas, 1–7 dias ideal)
curl 'https://comunicaapi.pje.jus.br/api/v1/comunicacao?numeroOab=14663&ufOab=PB&dataDisponibilizacaoInicio=2026-09-28&dataDisponibilizacaoFim=2026-09-29&pagina=1&itensPorPagina=50'

# TRF5, mês
curl 'https://comunicaapi.pje.jus.br/api/v1/comunicacao?numeroOab=14663&ufOab=PB&siglaTribunal=TRF5&dataDisponibilizacaoInicio=2026-09-01&dataDisponibilizacaoFim=2026-09-29&pagina=1&itensPorPagina=50'

# Certidão oficial em PDF
curl 'https://comunicaapi.pje.jus.br/api/v1/comunicacao/{hash}/certidao' -H 'Accept: application/pdf'
# ex hash lx8NKdwzYKdioGfPTKe7QWRbD1mrEX → 200, PDF 58 KB
```

Resultados obtidos:
- `14663/PB + TRF5`, 01–29 set: **count 1438** (topo: `0003527-90.2026.4.05.8303`
  38ª PE, `0004456-23.2026.4.05.8304` 20ª PE, `0004687-53.2026.4.05.8303` 18ª PE).
- `14663/PB + TRF5`, 29 ago–29 set: **count 1554**, sendo **1365 PE** (18ª: 607,
  38ª: 430, 8ª: 184, 20ª: 108).
- `14663/PE`: **count 0** (UF errada — o número 14663 existe em várias seccionais;
  sem UF, `count 2119` misturando AL/PB/CE/etc).
- `42543/PE` set: **count 99** (inclui atuação TRF3: `5051046-22.2026.4.03.6301`
  JEF Catanduva); variantes `42543-A/B/E/N/O/S`: **0**.

## 4. Regras da API (armadilhas confirmadas)

- `numeroOab` é **match exato de string**: consultar as 7 variantes
  (`""`, `-A`, `-B`, `-E`, `-N`, `-O`, `-S`) e normalizar dígitos ao comparar.
  Sem UF, o mesmo número colide entre seccionais.
- Paginação via `pagina` + `itensPorPagina`; `count` = total. Usar
  `itensPorPagina=50` (API usa no mínimo 5; 100 provoca `500` esporádico)
  + retry limitado com backoff. Falha final gera `DjenError`; o scheduler
  externo decide a fila morta, sem retry infinito.
- PE dentro do TRF5 filtra na skill/perfil: os quatro últimos dígitos do
  número CNJ devem ser `830x` (8303 Serra Talhada, 8304 Salgueiro), ou o
  órgão deve identificar PE. A integração não fixa esse filtro.
- Citação pessoal vai ao Domicílio Eletrônico (login), não à API pública.
- Idempotência por `id` da comunicação; observar `data_cancelamento`.

## 5. Detalhe de um item (ex `id 740120612`)

Campos: `numero_processo` + `numeroprocessocommascara`, `siglaTribunal`,
`nomeOrgao`/`idOrgao`, `codigoClasse`/`nomeClasse` (436 JEF cível),
`tipoComunicacao`/`tipoDocumento` (Intimação), `data_disponibilizacao`,
`status`, `ativo`, `texto` (íntegra), `link` (doc PJe),
`destinatarios[{nome, polo}]`, `destinatarioadvogados[{advogado{nome,
numero_oab, uf_oab}}]`, `hash` (certidão).

Texto suficiente para triar ~90% dos JEF-INSS sem abrir o PJe. PJe fica como
fonte secundária quando o texto é genérico, há prazo fatal a calcular ou
sigilo a conferir (via `link` + `numero_processo`).

Exemplo real: `0003527-90.2026.4.05.8303`, 38ª PE, MARIA DE LOURDES DA SILVA
(polo A) x INSS — "intimadas para se manifestarem sobre o(s) laudo(s)
pericial(is)" → workflow `12049 Z-ANALISE DE LAUDO` (ver skill
`customers/victor/skills/victor-hugo-task-routing/` para cadeia POP completa e invariantes
AdvBox, ex `guests[]` com Deivid 216759).

## 6. Arquitetura (decisão da sessão)

- DJEN como integração **standalone** em `src/jurisync/connectors/djen/`
  (`client`, `normalize`, `store`, `poll`, `mcp`) — zero dependência de AdvBox/skill.
  Segue o padrão do repo: conectores sob `src/jurisync/connectors/`, orquestração na
  skill do cliente (cf. `README.md`, `docs/pipeline.md`).
- OABs/tribunais no **perfil do cliente**, não no código
  (`customers/victor/`).
- Skill de roteamento separada consome `Comunicacao` normalizada + perfil;
  POP e decisão de enriquecer pelo PJe ficam na skill, não no cliente DJEN.
- CLI `--poll --all-variants` faz uma janela incremental por
  `data_disponibilizacao` (catch-up de 30 dias na primeira carga), salva estado
  por ID/cancelamento só após busca completa e devolve digest por órgão.
  Não há scheduler automático. MCP é somente leitura e não avança estado.

## 7. Artefatos desta sessão (fora do repo; mover o que for útil para `data/`)

- `/tmp/opencode/trf5-pb14663-30d.json` — 1554 itens TRF5 29 ago–29 set.
- `/tmp/opencode/cert-740120612.pdf` — certidão exemplo (58 KB).
- `/tmp/opencode/trf5-pje.png` — screenshot PJe TRF5 via Playwright.

## 8. Próximos passos

1. Cadastrar OABs no perfil do cliente (14663/PB, 42543/PE, 48647/PE;
   tribunais TRF5+; filtro PE `830x`).
2. Configurar um scheduler para chamar o poll CLI se a consulta diária automática
   for necessária; importar CSV das 1365 PE apenas se precisar de carga manual.
