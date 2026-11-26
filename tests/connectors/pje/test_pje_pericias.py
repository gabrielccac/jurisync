from unittest.mock import Mock, patch

from jurisync.connectors.pje import pericias

# The trf5-1g tab of 26/11/2026, with invented names and CPF.
TAB = """<span id="processoPericiaNovaPericia"><table class="rich-table" id="processoPericiaNovaPericiaList">
<thead><tr><th>Data</th><th>Periciado</th><th>Valor da per&iacute;cia</th><th>Perito</th><th>Situa&ccedil;&atilde;o</th></tr></thead>
<tbody><tr><td colspan="5"></td></tr>{rows}</tbody><tfoot><tr><td colspan="5"></td></tr></tfoot></table>
<span class="pull-right text-muted">{total} resultados encontrados</span></span>"""
ROW = """<tr><td><div>25/11/2026 14:54</div></td><td><div>AUTOR TESTE</div></td><td><div>R$ 312,00</div></td>
<td><div>PERITA TESTE - 123.456.789-00</div></td><td><div>Designada</div></td></tr>"""


def test_parse_row_drops_the_expert_cpf():
    items, complete = pericias.parse_pericias(TAB.format(rows=ROW, total=1))
    assert complete is True
    assert items == [
        {
            "data": "2026-11-25",
            "hora": "14:54",
            "periciado": "AUTOR TESTE",
            "valor": "R$ 312,00",
            "perito": "PERITA TESTE",
            "situacao": "Designada",
        }
    ]


def test_a_total_that_does_not_match_or_a_missing_table_is_incomplete():
    assert pericias.parse_pericias(TAB.format(rows=ROW, total=2))[1] is False
    assert pericias.parse_pericias("<html>Erro</html>") == ([], False)
    assert pericias.parse_pericias("<span>0 resultados encontrados</span>") == ([], True)


def test_fetch_posts_the_tab_control_over_the_navbar_form():
    detail = '<form id="navbar"><input type="hidden" name="javax.faces.ViewState" value="j_id4"/></form>'
    assert not pericias.has_tab(detail) and pericias.has_tab(detail + pericias.TAB_CONTROL)
    answer = Mock(status_code=200, content=TAB.format(rows=ROW, total=1).encode(), headers={})
    with patch.object(pericias.jsf, "post", return_value=answer) as post:
        items, complete = pericias.fetch_pericias(Mock(), detail, "https://pje1g.trf5.jus.br/pje/x")
    body = post.call_args.args[2]
    assert body[pericias.TAB_CONTROL] == pericias.TAB_CONTROL and body["javax.faces.ViewState"] == "j_id4"
    assert len(items) == 1 and complete
