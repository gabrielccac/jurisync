"""Process detail on anonymised TJSP pages: movements and their documents.

The fixtures keep the shapes TJSP served on 2026-11-27; every code, id, number, name and text is invented.
"""

from pathlib import Path

from jurisync.connectors.esaj import parsers

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_a_movement_document_is_the_document_code_of_its_link_not_the_movement_sequence():
    # Live, the receipt check's 64 (the movement's sequence) was returned twice as documentId; the viewer knows the
    # document by the nine-digit cdDocumento of the link.
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
