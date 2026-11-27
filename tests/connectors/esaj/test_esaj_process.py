"""Process detail on anonymised TJSP pages: the page's blocks, the second instance, the movements and their documents.

The fixtures keep the shapes TJSP served on 2026-11-27; every code, id, number, name and text is invented.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jurisync.connectors.esaj import base, common, parsers, process, search

FIXTURES = Path(__file__).parent / "fixtures"
NUMBER = "0000001-00.2026.8.26.0001"
HOST = "https://esaj.tjsp.jus.br"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def reply(text, url=f"{HOST}/x", status=200):
    return SimpleNamespace(
        status_code=status, url=url, text=text, content=text.encode(), json=lambda: json.loads(text), headers={}
    )


def test_a_movement_document_is_the_document_code_of_its_link_not_the_movement_sequence():
    # Live, the receipt check's 64 (the movement's sequence) was returned twice as documentId; the link names the
    # document by its nine-digit cdDocumento.
    first, second = parsers.movements(fixture("cpopg_movimentacoes.html"))
    assert first["documents"] == [
        {
            "documentId": "900000001",
            "sequence": "64",
            "title": "Proferido Despacho de Mero Expediente",
            "accessStatus": "flow_required",
        }
    ]
    assert second["documents"] == []
    assert second["text"] == "Juntada de Petição Intermediária"


def test_a_link_without_the_receipt_check_still_names_its_document():
    row = (
        '<tr class="containerMovimentacao"><td>01/11/2026</td><td>'
        '<a href="/cpopg/abrirDocumentoVinculadoMovimentacao.do?processo.codigo=AB1&amp;cdDocumento=123">Decisão</a>'
        '<a href="/cpopg/show.do?processo.codigo=AB1">outro processo</a></td></tr>'
    )
    (movement,) = parsers.movements(row)
    assert movement["documents"] == [
        {"documentId": "123", "sequence": None, "title": "Decisão", "accessStatus": "flow_required"}
    ]


def test_the_first_instance_page_gives_its_extra_fields_and_blocks():
    result = parsers.process(fixture("cpopg_show.html"), NUMBER)
    fields = result["fields"]
    assert fields["valorAcaoProcesso"] == "R$ 10.000,00"
    assert fields["dataHoraDistribuicaoProcesso"] == "10/01/2026 às 10:00 - Livre"
    assert (fields["areaProcesso"], fields["numeroControleProcesso"]) == ("Cível", "2026/000001")
    assert fields["outrosAssuntos"] == "Práticas Abusivas"
    assert result["tags"] == ["Tramitação prioritária"]
    assert result["peticoesDiversas"] == [
        {"data": "12/02/2026", "tipo": "Pedido de Habilitação"},
        {"data": "20/02/2026", "tipo": "Petições Diversas"},
    ]
    assert result["incidentes"] == [
        {
            "recebidoEm": "01/06/2026",
            "classe": "Cumprimento de sentença (0000009-00.2026.8.26.0001)",
            "processNumber": "0000009-00.2026.8.26.0001",
            "codigo": "1A0009C",
        }
    ]
    (apenso,) = result["apensos"]
    assert (apenso["processNumber"], apenso["apensamento"], apenso["codigo"]) == (
        "0000008-00.2026.8.26.0001",
        "05/05/2026",
        "1A0008A",
    )
    # "Não há audiências" and the note printed under the block are no hearings; the CDA table is not read as one.
    assert result["audiencias"] == []


def test_future_hearings_are_read_by_their_header():
    page = fixture("cpopg_show.html").replace(
        '<tr><td id="processoSemAudiencias">Não há Audiências futuras vinculadas a este processo.</td></tr>',
        "<tr><th>Data</th><th>Audiência</th><th>Situação</th><th>Qt. Pessoas</th></tr>"
        "<tr><td>10/12/2026</td><td>Conciliação</td><td>Designada</td><td>2</td></tr>",
    )
    assert parsers.process(page, NUMBER)["audiencias"] == [
        {"data": "10/12/2026", "audiencia": "Conciliação", "situacao": "Designada", "qtPessoas": "2"}
    ]


def test_the_second_instance_page_gives_rapporteur_body_judgments_and_composition():
    result = parsers.process(fixture("cposg_show.html"), NUMBER)
    fields = result["fields"]
    assert fields["relatorProcesso"] == "RELATOR EXEMPLO"
    assert fields["orgaoJulgadorProcesso"] == "99ª Câmara de Direito Privado"
    assert (fields["situacaoProcesso"], fields["secaoProcesso"]) == ("Julgado", "Direito Privado 2")
    assert fields["origemProcesso"].startswith("Comarca de Exemplo")
    assert result["tags"] == ["Julgado"]
    assert result["julgamentos"] == [
        {
            "data": "01/09/2026",
            "situacaoDoJulgamento": "Julgado",
            "decisao": "Deram provimento em parte ao recurso. V. U.",
        }
    ]
    assert [member["participacao"] for member in result["composicaoJulgamento"]] == ["Relator", "2º"]
    assert result["numerosPrimeiraInstancia"][0]["processNumber"] == NUMBER
    assert result["incidentes"] == [] and result["apensos"] == []


SECOND_FIRST_PAGE = (
    '<tr class="fundoClaro movimentacaoProcesso"><td class="dataMovimentacaoProcesso">01/09/2026</td><td></td>'
    '<td class="descricaoMovimentacaoProcesso"><a class="linkMovVincProc" href="#?cdDocumento=12" cddocumento="12" '
    "onclick=\"javascript:jQuery.saj.validarAberturaIntimacaoNaoRecebida(event, this, 'RS0000000001', 12)\">"
    "Acórdão registrado</a></td></tr>"
    '<tr><td><a id="linkMaisRecolherMovimentacoes" href="javascript:" data-cursor="k2">Mais</a></td></tr>'
)
SECOND_LAST_PAGE = (
    '<tr class="fundoEscuro movimentacaoProcesso"><td class="dataMovimentacaoProcesso">01/03/2026</td><td></td>'
    '<td class="descricaoMovimentacaoProcesso">Distribuído por sorteio</td></tr>'
    '<tr id="metaPaginaMovimentacoes" data-tem-mais-paginas="false" data-cursor="k3"><td></td></tr>'
)


def test_second_instance_movements_page_by_the_link_then_the_meta_row():
    rows, cursor = parsers.movement_page(SECOND_FIRST_PAGE)
    assert cursor == "k2" and [row["text"] for row in rows] == ["Acórdão registrado"]
    # The second instance's "cdDocumento" repeats the movement sequence: it is no viewer document.
    assert rows[0]["documents"] == [
        {"documentId": None, "sequence": "12", "title": "Acórdão registrado", "accessStatus": "flow_required"}
    ]
    rows, cursor = parsers.movement_page(SECOND_LAST_PAGE)
    assert cursor is None and [row["text"] for row in rows] == ["Distribuído por sorteio"]
    more = SECOND_LAST_PAGE.replace('data-tem-mais-paginas="false"', 'data-tem-mais-paginas="true"')
    assert parsers.movement_page(more)[1] == "k3"


def test_get_process_in_the_second_instance_searches_by_number_and_pages_by_cdprocesso(monkeypatch):
    consulted, requested = [], []

    def consult(session, portal, path, **kwargs):
        consulted.append((path, kwargs.get("params")))
        # A single match: the second instance answers the search with the detail page itself, at search.do.
        return reply(fixture("cposg_show.html"), url=f"{HOST}/cposg/search.do")

    def request(session, portal, method, path, **kwargs):
        requested.append((method, path, kwargs["params"]))
        return reply(SECOND_FIRST_PAGE if "cursor" not in kwargs["params"] else SECOND_LAST_PAGE)

    monkeypatch.setattr(search, "consult", consult)
    monkeypatch.setattr(common, "request", request)
    result = base.load_process(None, "tjsp-esaj", NUMBER, "2g")
    assert result["instancia"] == "2g" and result["processCode"] == "RS0000000001"
    assert result["fields"]["relatorProcesso"] == "RELATOR EXEMPLO"
    assert [row["text"] for row in result["movements"]] == ["Acórdão registrado", "Distribuído por sorteio"]
    assert consulted[0][0] == "cposg/search.do" and consulted[0][1]["cbPesquisa"] == "NUMPROC"
    assert [params for _m, _p, params in requested] == [
        {"cdProcesso": "RS0000000001"},
        {"cdProcesso": "RS0000000001", "cursor": "k2"},
    ]
    assert {path for _m, path, _p in requested} == {"cposg/carregarMovimentacoesAjax.do"}


def test_several_second_instance_entries_read_the_first_and_list_the_rest(monkeypatch):
    listing = fixture("cposg_lista.html").replace("0000003-00.2026.8.26.0001", NUMBER)
    pages = iter([reply(listing, url=f"{HOST}/cposg/search.do"), reply(fixture("cposg_show.html"))])
    consulted = []

    def consult(session, portal, path, **kwargs):
        consulted.append((path, kwargs.get("params")))
        return next(pages)

    monkeypatch.setattr(search, "consult", consult)
    monkeypatch.setattr(process, "paged_movements", lambda *a, **k: ([], True))
    result = base.load_process(None, "tjsp-esaj", NUMBER, "2g")
    assert consulted[1] == ("cposg/show.do", {"processo.codigo": "RS0000000001"})
    assert result["alternatives"] == [{"codigo": "RS0000000002", "classe": "Agravo de Instrumento"}]


def test_an_instance_or_code_the_portal_does_not_take_is_refused_before_any_request(monkeypatch):
    monkeypatch.setattr(common, "request", lambda *a, **k: pytest.fail("no request expected"))
    monkeypatch.setattr(search, "consult", lambda *a, **k: pytest.fail("no request expected"))
    with pytest.raises(ValueError):
        base.load_process(None, "tjal-esaj", "0000001-00.2026.8.02.0001", "2g")
    with pytest.raises(ValueError):
        base.load_process(None, "tjsp-esaj", NUMBER, "1g", "../x")
