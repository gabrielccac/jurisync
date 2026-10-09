import pytest

from jurisync.connectors.pje import inbox

BUCKET_ROW = """<div id="formAbaExpediente:listaAgrSitExp:{n}:linhaN1"><div><a href="#" id="x"
onclick="A4J.AJAX.Submit('formAbaExpediente',event,{{'similarityGroupingId':'formAbaExpediente:listaAgrSitExp:{n}:j_id178','containerId':'conteudoAbaExpedientes','parameters':{{'formAbaExpediente:listaAgrSitExp:{n}:j_id178':'formAbaExpediente:listaAgrSitExp:{n}:j_id178','ajaxSingle':'formAbaExpediente:listaAgrSitExp:{n}:j_id178'}} }} );return false;"
title="Clique para abrir este agrupador"><span class="nomeTarefa">{label}</span> <span class="pull-right mr-10">{count}</span></a></div></div>"""

TREE = "formAbaExpediente:listaAgrSitExp:0:trPend"


def branch(key, label, count, expanded="false", children=""):
    node = f"{TREE}:{key}::j_id182"
    return f"""<table class="rich-tree-node" id="{node}"><tbody><tr id="{node}:mainRow">
<td class="rich-tree-node-handleicon"><input class="rich-tree-h" id="{node}NodeExpanded" name="{node}NodeExpanded" type="hidden" value="{expanded}"/></td>
<td class="rich-tree-node-icon" rich:onexpand="A4J.AJAX.Submit('formAbaExpediente',event,{{'similarityGroupingId':'{TREE}:{key}::j_id183','containerId':'conteudoAbaExpedientes','parameters':{{'ajaxSingle':'{node}','{TREE}:{key}::j_id183':'{TREE}:{key}::j_id183'}} }} )"><i title="Jurisdição"></i></td>
<td><a id="{TREE}:{key}::jNp" href="#"><span class="nomeTarefa">{label}</span> <span class="pull-right">{count}</span></a></td></tr></tbody></table>
<div id="{node}:childs" class="rich-tree-node-children">{children}</div>"""


def leaf(key, count):
    node = f"{TREE}:{key}:-1::j_id185"
    form = f"{TREE}:{key}:\\x2D1::j_id186"
    control = f"{form}:cxExItem"
    return f"""<table class="rich-tree-node" id="{node}"><tbody><tr id="{node}:mainRow"><td><i title="Caixa"></i></td><td>
<a id="{TREE}:{key}:-1::j_id186:cxExItem" href="#" onclick="A4J.AJAX.Submit('{form}',event,{{'similarityGroupingId':'{control}','containerId':'conteudoAbaExpedientes','parameters':{{'{control}':'{control}','ajaxSingle':'{control}'}} }} );return false;"><span class="nomeTarefa">Caixa de entrada</span> <span class="pull-right">{count}</span></a></td></tr></tbody></table>"""


def row(
    expediente,
    *,
    notice="O sistema registrou ciência em 21/09/2026 23:59",
    prazo="Prazo:15 dias",
    button="",
    limit=True,
):
    limite = "Data limite prevista para manifestação: 13/10/2026 23:59" if limit else ""
    return f"""<tr><td id="formExpedientes:tbExpedientes:{expediente}:j_id547"><span id="formExpedientes:tbExpedientes:{expediente}:btnTomarCiencia">{button}</span>
<span id="formExpedientes:tbExpedientes:{expediente}:btnResponder"><a>Responder</a></span></td>
<td><div class="col-md-4"><span title="Destinatário">PARTE TESTE</span><span title="Tipo de documento">Ato Ordinatório ({expediente})</span>
<span title="Meio de comunicação">Expedição eletrônica (<span title="Data de criação do expediente">09/09/2026 12:01</span>)</span>
<div title="Prazo para manifestação">{prazo}</div><div>{notice}</div><div>{limite}</div></div>
<div class="col-md-8"><a class="numero-processo-expediente" href="#" onclick="window.open('/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id=3413962&amp;ca=SECRET','b')">CumSenFaz 0003328-68.2026.4.05.8303</a>
Pessoa com Deficiência A X INSS /18ª Vara Federal PE Último movimento: 29/09/2026 18:43 - Juntada de Petição</div></td></tr>"""


def inbox_page(rows, total, current=1, scroller="scPendentes"):
    return f"""<div><table id="formExpedientes:tbExpedientes"><tbody id="formExpedientes:tbExpedientes:tb">{"".join(rows)}</tbody></table>
<div id="formExpedientes:tbExpedientes:{scroller}"><table id="formExpedientes:tbExpedientes:{scroller}_table"><tr><td class="rich-datascr-act">{current}</td></tr></table></div>
<span>{total} resultados encontrados</span></div>"""


def test_parse_buckets_reads_fixed_buckets_and_their_controls():
    html = BUCKET_ROW.format(n=0, label="Pendentes", count=871) + BUCKET_ROW.format(n=1, label="Sem prazo", count=0)
    assert inbox.parse_buckets(html) == [
        {"index": 0, "label": "Pendentes", "count": 871, "control": "formAbaExpediente:listaAgrSitExp:0:j_id178"},
        {
            "index": 1,
            "label": "Sem prazo",
            "count": 0,
            "control": "formAbaExpediente:listaAgrSitExp:0:j_id178".replace(":0:", ":1:"),
        },
    ]


def test_parse_tree_finds_branches_and_inboxes_with_their_parent():
    html = branch("18", "AL / Santana do Ipanema", 1, "true", leaf("18", 1)) + branch("6", "CE / Juazeiro", 4)
    nodes = {n["label"]: n for n in inbox.parse_tree(html, 0)}
    region, box, other = nodes["AL / Santana do Ipanema"], nodes["Caixa de entrada"], nodes["CE / Juazeiro"]
    assert (
        region["expanded"] and region["expand"]["params"]["ajaxSingle"] == f"{TREE}:18::j_id182" and not region["inbox"]
    )
    assert (
        box["parent"] == region["id"]
        and box["count"] == 1
        and box["inbox"]["params"]["ajaxSingle"].endswith(":cxExItem")
    )
    assert box["inbox"]["form"] == f"{TREE}:18:-1::j_id186"
    assert not other["expanded"] and other["count"] == 4 and other["kind"] == "Jurisdição"


def test_tree_state_carries_node_flags_for_the_next_request():
    state = inbox.tree_state(branch("18", "A", 1, "true") + branch("6", "B", 2), 0)
    assert state == {f"{TREE}:18::j_id182NodeExpanded": "true", f"{TREE}:6::j_id182NodeExpanded": "false"}


def test_parse_inbox_rows_total_and_page():
    page = inbox.parse_inbox(inbox_page([row("33487371"), row("33487372")], 626, current=2))
    assert (page["total"], page["page"], page["scroller"]) == (626, 2, "formExpedientes:tbExpedientes:scPendentes")
    first = page["rows"][0]
    assert first["expedienteId"] == "33487371" and first["tipo"] == "Ato Ordinatório"
    assert first["meio"] == "Expedição eletrônica" and first["expedidoEm"] == "2026-09-09"
    assert first["ciencia"] == {"por": "O sistema", "em": "2026-09-21"} and first["cienciaPendente"] is False
    assert (first["prazoDias"], first["limite"], first["limiteTipo"]) == (15, "2026-10-13", "manifestação")
    assert first["processNumber"] == "0003328-68.2026.4.05.8303" and first["classe"] == "CumSenFaz"
    assert first["processId"] == "3413962" and "SECRET" not in str(first)
    assert first["ultimoMovimento"] == {"em": "2026-09-29", "descricao": "Juntada de Petição"}


def test_parse_inbox_row_variants_person_notice_no_deadline_and_pending_notice():
    person = row("1", notice="BENEDITA DIAS tomou ciência em 05/10/2026 15:01")
    nodeadline = row("2", prazo="Prazo: sem prazo", limit=False)
    pending = row("3", notice="", button='<a class="btn">Tomar ciência</a>')
    rows = inbox.parse_inbox(inbox_page([person, nodeadline, pending], 3))["rows"]
    assert rows[0]["ciencia"] == {"por": "BENEDITA DIAS", "em": "2026-10-05"}
    assert rows[1]["semPrazo"] is True and rows[1]["prazoDias"] is None and rows[1]["limite"] is None
    assert rows[2]["ciencia"] is None and rows[2]["cienciaPendente"] is True


@pytest.mark.parametrize(
    "kind,control",
    [
        ("page", "formExpedientes:tbExpedientes:33547536:btnTomarCiencia"),
        ("page", "formExpedientes:tbExpedientes:scPendentes:btnResponder"),
        ("search", "formExpedientes:distribuirExpedientes"),
        ("branch", "formExpedientes:tbExpedientes:34184751:j_id629:j_id630"),
        ("inbox", "formAbaExpediente:listaAgrSitExp:0:trPend:18::j_id182"),
    ],
)
def test_check_control_refuses_everything_but_read_only_panel_controls(kind, control):
    with pytest.raises(RuntimeError, match="refused"):
        inbox.check_control(kind, control)


def test_check_control_accepts_the_read_only_shapes():
    for kind, control in [
        ("bucket", "formAbaExpediente:listaAgrSitExp:0:j_id178"),
        ("branch", f"{TREE}:18::j_id182"),
        ("inbox", f"{TREE}:18:-1::j_id186:cxExItem"),
        ("page", "formExpedientes:tbExpedientes:scPendentes"),
        ("search", "formExpedientes:btPesq"),
    ]:
        assert inbox.check_control(kind, control) == control


FIELDS = [
    ("formExpedientes:numeroProcesso:numeroSequencial", ""),
    ("formExpedientes:numeroProcesso:numeroDigitoVerificador", ""),
    ("formExpedientes:numeroProcesso:Ano", ""),
    ("formExpedientes:numeroProcesso:ramoJustica", ""),
    ("formExpedientes:numeroProcesso:respectivoTribunal", ""),
    ("formExpedientes:numeroProcesso:NumeroOrgaoJustica", ""),
    ("formExpedientes:j_id517InputDate", ""),
    ("formExpedientes:j_id517InputCurrentDate", "10/2026"),
    ("formExpedientes:j_id519InputDate", ""),
    ("formExpedientes:itNE", ""),
]


def test_apply_filters_fills_cnj_parts_and_the_expedition_interval():
    filled = dict(
        inbox.apply_filters(
            FIELDS, {"processNumber": "0003231-05.2025.4.05.8303", "from": "2026-10-01", "to": "2026-10-09"}
        )
    )
    assert [filled[f"formExpedientes:numeroProcesso:{n}"] for n in inbox.CNJ_FIELDS] == [
        "0003231",
        "05",
        "2025",
        "4",
        "05",
        "8303",
    ]
    assert (filled["formExpedientes:j_id517InputDate"], filled["formExpedientes:j_id519InputDate"]) == (
        "01/10/2026",
        "09/10/2026",
    )
    assert filled["formExpedientes:j_id517InputCurrentDate"] == "10/2026"


@pytest.mark.parametrize(
    "filters",
    [{"processNumber": "123"}, {"from": "01/10/2026"}, {"bogus": "x"}],
)
def test_apply_filters_rejects_bad_input(filters):
    with pytest.raises(ValueError):
        inbox.apply_filters(FIELDS, filters)


class FakePanel:
    def __init__(self, pages, search_pages=None):
        self.pages, self.search_pages, self.calls = pages, search_pages, []

    def open_inbox(self, node):
        self.calls.append("open")
        return self.pages[0]

    def search(self, filters):
        self.calls.append(("search", filters))
        return self.search_pages[0]

    def turn_page(self, number, scroller):
        self.calls.append(number)
        return self.pages[number - 1]


def page(number, ids, total=5):
    return {"rows": [{"expedienteId": i} for i in ids], "total": total, "page": number, "scroller": "sc"}


def test_read_pages_walks_every_page_and_checks_the_total():
    panel = FakePanel([page(1, "ab"), page(2, "cd"), page(3, "e")])
    box = inbox._read_pages(panel, {}, ["PE", "Caixa de entrada"], 25)
    assert panel.calls == ["open", 2, 3]
    assert (box["pages"], box["returned"], box["complete"]) == (3, 5, True)


def test_read_pages_reports_incomplete_when_capped_or_short():
    panel = FakePanel([page(1, "ab"), page(2, "cd"), page(3, "e")])
    assert inbox._read_pages(panel, {}, [], 2)["complete"] is False
    repeated = FakePanel([page(1, "ab"), page(2, "ab"), page(3, "ab")])
    box = inbox._read_pages(repeated, {}, [], 25)
    assert (box["returned"], box["complete"]) == (2, False)


def test_read_pages_keeps_rows_that_arrive_while_reading():
    panel = FakePanel([page(1, "ab", 3), page(2, "bc", 3)])
    box = inbox._read_pages(panel, {}, [], 25)
    assert (box["returned"], box["complete"], box["changedWhileReading"]) == (3, True, False)
    grown = FakePanel([page(1, "ab", 3), page(2, "bcd", 3)])
    box = inbox._read_pages(grown, {}, [], 25)
    assert (box["returned"], box["complete"], box["changedWhileReading"]) == (4, True, True)


def test_read_pages_rejects_a_page_that_is_not_the_requested_one():
    panel = FakePanel([page(1, "ab"), page(1, "ab")])
    with pytest.raises(RuntimeError, match="page 1 instead of 2"):
        inbox._read_pages(panel, {}, [], 25)


def test_read_pages_searches_inside_the_inbox_when_filtered():
    panel = FakePanel([page(1, "ab")], search_pages=[page(1, "a", total=1)])
    box = inbox._read_pages(panel, {}, [], 25, {"processNumber": "x"})
    assert panel.calls == ["open", ("search", {"processNumber": "x"})] and box["total"] == 1 and box["complete"]


class FakeTreePanel:
    def __init__(self):
        self.expanded = []

    def open_bucket(self, bucket):
        return inbox.parse_tree(branch("18", "AL / Santana", 1) + branch("6", "CE / Juazeiro", 4), 0)

    def expand(self, bucket, node):
        self.expanded.append(node["label"])
        key = node["id"].split(":")[-3] if False else node["id"].split(":trPend:")[1].split("::")[0]
        return inbox.parse_tree(branch(key, node["label"], node["count"], "true", leaf(key, node["count"])), 0)


def test_walk_expands_only_regions_that_match_and_finds_their_inboxes():
    panel = FakeTreePanel()
    nodes = inbox.walk(panel, {"index": 0}, regions=["juazeiro"])
    assert panel.expanded == ["CE / Juazeiro"]
    inboxes = [n for n in nodes.values() if n["inbox"]]
    assert [inbox._paths(nodes)[n["id"]] for n in inboxes] == [["CE / Juazeiro", "Caixa de entrada"]]


def test_walk_without_regions_expands_every_branch():
    panel = FakeTreePanel()
    nodes = inbox.walk(panel, {"index": 0})
    assert sorted(panel.expanded) == ["AL / Santana", "CE / Juazeiro"]
    assert sum(1 for n in nodes.values() if n["inbox"]) == 2


def test_server_default_follows_the_customer_setting_and_an_explicit_value_wins(monkeypatch):
    from jurisync.connectors.pje import server

    monkeypatch.setattr(server, "ciencia_on_read", lambda: "allow")
    assert server._allow_ciencia(None) is True and server._allow_ciencia(False) is False
    monkeypatch.setattr(server, "ciencia_on_read", lambda: "ask")
    assert server._allow_ciencia(None) is False and server._allow_ciencia(True) is True
