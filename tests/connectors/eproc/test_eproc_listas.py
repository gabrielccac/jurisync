"""Eproc lists of eproc/listas.py: hand-written pages with the portals' structure and fictitious data."""

import asyncio
import unittest
from unittest import mock

from mcp.client.client import Client

from jurisync.connectors.eproc import client, listas, server
from jurisync.core.errors import NotSupported, PortalError
from jurisync.core.responses import compact

PORTAL = "trf4-1g-sc"
BASE = "https://eproc.jfsc.jus.br/eprocV2/"


class Response:
    def __init__(self, body="", status=200):
        self.status_code = status
        self.content = body.encode("iso-8859-1")  # the portal's charset, as client._decode reads it
        self.url = BASE
        self.headers = {}


class Session:
    def __init__(self, *bodies):
        self.responses = iter(Response(body) for body in bodies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)

    def close(self):
        pass


def panel(*actions):
    links = "".join(
        f'<a href="controlador.php?acao={action}&amp;hash=h{index}">x</a>' for index, action in enumerate(actions)
    )
    return f"<html><body><div id='menu'>{links}</div></body></html>"


def sort_header(label):
    """A list header as the infra draws it: the label inside a small ordering table."""
    arrow = (
        "<a href=\"javascript:void(0);\" onclick=\"infraAcaoOrdenar('X','ASC','Infra',null);\"><img src=\"s.gif\"/></a>"
    )
    return (
        f'<th class="infraTh"><table class="infraTableOrdenacao"><tr class="infraTrOrdenacao">'
        f'<td class="infraTdSetaOrdenacao">{arrow}</td><td class="infraTdRotuloOrdenacao" rowspan="2">{label}</td></tr>'
        f'<tr class="infraTrOrdenacao"><td class="infraTdSetaOrdenacao">{arrow}</td></tr></table></th>'
    )


def messages_page(rows, *, read=False, count=None, page=0, pages=None):
    table = ""
    pager = ""
    if pages:
        options = "".join(f'<option value="{index}">{index + 1}</option>' for index in range(pages))
        pager = (
            '<select id="selInfraPaginacaoSuperior" name="selInfraPaginacaoSuperior" '
            f"onchange=\"infraAcaoPaginar('=',this.value,'Infra');\">{options}</select>"
        )
    if rows:
        lines = "".join(
            f'<tr class="{"infraTrClara" if index % 2 == 0 else "infraTrEscura"}"><td>{when}</td>'
            f'<td><a href="controlador.php?acao=caixa_mensagem_vizualizar&amp;acao_origem=caixa_mensagem_listar'
            f'&amp;id_mensagem={index}&amp;status=naoLidas&amp;hash=v{index}">{title}</a></td><td>{body}</td></tr>'
            for index, (when, title, body) in enumerate(rows)
        )
        total = len(rows) if count is None else count
        table = (
            f'<table class="infraTable" summary="Mensagens."><caption class="infraCaption">Lista de Mensagens '
            f"({total} registros):</caption><tr>{sort_header('Data/Hora')}{sort_header('Título')}"
            f"{sort_header('Mensagem')}</tr>{lines}</table>"
        )
    return f"""<html><body><div id="divInfraAreaTelaD">
<form id="frmMensagemLista" method="post"
 action="controlador.php?acao=caixa_mensagem_listar&amp;acao_origem=caixa_mensagem_listar&amp;hash=f1">
<input type="hidden" id="hdnInfraTipoPagina" name="hdnInfraTipoPagina" value="1"/>
<button type="button" id="btnImprimir" onclick="infraImprimirTabela();">Imprimir</button>
<fieldset><legend>Filtro</legend>
<input type="radio" name="rdoFiltro" id="rdoNaoLidas" value="naoLidas" onclick="this.form.submit()" {"" if read else 'checked="checked"'}/>
<label for="rdoNaoLidas">Mensagem(ns) NÃO lida(s)</label>
<input type="radio" name="rdoFiltro" id="rdoLidas" value="lidas" onclick="this.form.submit()" {'checked="checked"' if read else ""}/>
<label for="rdoLidas">Mensagem(ns) lida(s)</label></fieldset>
<input type="hidden" id="hdnInfraNroItens" name="hdnInfraNroItens" value="0"/>
<input type="hidden" id="hdnInfraCampoOrd" name="hdnInfraCampoOrd" value="DesAssunto"/>
<input type="hidden" id="hdnInfraPaginaAtual" name="hdnInfraPaginaAtual" value="{page}"/>
{pager}{table}
</form></div></body></html>"""


UNREAD = [
    ("13/11/2026 08:33:12", "Indisponibilidade programada", "O sistema ficará indisponível no sábado das 20h às 23h."),
    ("07/12/2025 16:15:41", "Nova versão", "Correção no envio de petições intercorrentes."),
]
READ = [("02/02/2026 10:00:00", "Aviso antigo", "Mensagem já lida pelo advogado fictício.")]


def paradas_page(outages, suspensions):
    """Relatório de Paradas: outages with a checkbox and an action column, then the suspensions under an h3."""
    first = ""
    if outages:
        lines = "".join(
            f'<tr class="infraTrClara"><td><input type="checkbox" id="chkInfraItem{index}" name="chkInfraItem{index}" '
            f'value="9{index}" onclick="infraSelecionarItens(this,\'Infra\');"/></td><td>{reason}</td><td>{extended}</td>'
            f"<td>{start}</td><td>{end}</td><td>"
            f'<a href="controlador.php?acao=eprocsysadm_consultar&amp;id=9{index}&amp;hash=c{index}"><img src="consultar.gif"/></a>'
            f'<a href="controlador.php?acao=eprocsysadm_certidao&amp;id=9{index}&amp;hash=d{index}">certidão</a></td></tr>'
            for index, (reason, extended, start, end) in enumerate(outages)
        )
        first = (
            '<div id="divInfraAreaTabela" class="infraAreaTabela"><table class="infraTable"><tr>'
            '<th class="infraTh"><a href="javascript:void(0);"><img src="check.gif"/></a></th>'
            f"{sort_header('Descrição Motivo Evento')}{sort_header('Houve Prorrogação de Prazo?')}"
            f'{sort_header("Data Inicio")}{sort_header("Data Fim")}<th class="infraTh">Ações</th></tr>{lines}</table></div>'
        )
    second = ""
    if suspensions:
        lines = "".join(f'<tr class="infraTrClara"><td>{date}</td><td>{kind}</td></tr>' for date, kind in suspensions)
        second = (
            '<h3>Lista de suspensões cadastradas</h3><div class="infraAreaTabela"><table class="infraTable">'
            f'<tr><th class="infraTh">Data Suspensão.</th><th class="infraTh">Tipo Suspensão.</th></tr>{lines}</table></div>'
        )
    return f"""<html><body><div id="divInfraAreaTelaD">
<div id="divInfraBarraLocalizacao">Relatório de Paradas</div>
<form id="frmEprocsysadmLista" method="post"
 action="controlador.php?acao=eprocsysadm_listar&amp;acao_origem=eprocsysadm_listar&amp;hash=p1">
<button type="button" id="btnImprimir" onclick="infraImprimirTabela();">Imprimir</button>
<input type="hidden" id="hdnInfraNroItens" name="hdnInfraNroItens" value="{len(outages)}"/>
{first}{second}
</form></div></body></html>"""


OUTAGES = [
    ("DIFICULDADE DE ACESSO", "Sim", "21/10/2026 11:00:00", "21/10/2026 16:00:00"),
    ("PARADA PROGRAMADA", "Não", "12/03/2026 19:10:00", "12/03/2026 19:50:00"),
]
SUSPENSIONS = [("24/01/2026", "Fechamento"), ("10/03/2026", "Abertura")]


def prepared_page(form_id, title, button, rows=()):
    """A prepared list with its action button; a table only when rows are given (no capture had rows)."""
    table = ""
    if rows:
        lines = "".join(
            f'<tr class="infraTrClara"><td><input type="checkbox" name="chkInfraItem{index}" value="{index}"/></td>'
            f'<td><a href="controlador.php?acao=processo_selecionar&amp;num_processo=1&amp;hash=s">{number}</a></td>'
            f"<td>{created}</td><td>{kind}</td><td>{party}</td>"
            f'<td><a href="controlador.php?acao=peticao_inicial_preparada_alterar&amp;hash=e{index}">Alterar</a></td></tr>'
            for index, (number, created, kind, party) in enumerate(rows)
        )
        table = (
            f'<table class="infraTable"><caption>Lista ({len(rows)} registros):</caption><tr><th class="infraTh"></th>'
            f"{sort_header('Processo')}{sort_header('Data de Criação')}{sort_header('Tipo')}{sort_header('Parte')}"
            f'<th class="infraTh">Ações</th></tr>{lines}</table>'
        )
    return f"""<html><body><div id="divInfraAreaTelaD"><div id="divInfraBarraLocalizacao">{title}</div>
<form id="{form_id}" method="post" action="controlador.php?acao=x&amp;hash=f">
<input type="hidden" id="hdnInfraItensSelecionados" name="hdnInfraItensSelecionados" value=""/>
<button type="button" id="{button[0]}" onclick="{button[1]}();">{button[2]}</button>
<div id="divInfraAreaTabela" class="infraAreaTabela">{table}</div></form></div></body></html>"""


INITIAL_EMPTY = prepared_page(
    "frmPeticaoInicial",
    "Petições Iniciais pendentes de distribuição",
    ("btnDistribuir", "distribuirPeticaoInicialPreparada", "Distribuir"),
)
MOVEMENT_EMPTY = prepared_page(
    "frmMovimentacaoFutura",
    "Movimentações pendentes",
    ("btnPeticionar", "peticionarMovimentacaoPreparada", "Peticionar"),
)
PREPARED_ROWS = [
    ("5000001-00.2026.4.04.7200", "20/11/2026 14:05", "Petição intercorrente", "MARIA FICTÍCIA (123.456.789-09)"),
    ("", "21/11/2026", "Petição inicial", "JOÃO INVENTADO"),
]


def guias_page(action, rows=(), extra=""):
    """A guias list as the infra draws its lists (no capture shows it): its form posts to its own action."""
    table = ""
    if rows:
        lines = "".join(
            f'<tr class="infraTrClara"><td>{number}</td><td>{issued}</td><td>{due}</td><td>{value}</td>'
            f'<td><a href="controlador.php?acao=guia_pagamento_imprimir&amp;hash=g{index}">Imprimir</a></td></tr>'
            for index, (number, issued, due, value) in enumerate(rows)
        )
        table = (
            f'<table class="infraTable"><caption>Lista de Guias ({len(rows)} registros):</caption><tr>'
            f"{sort_header('Processo')}{sort_header('Data Emissão')}{sort_header('Vencimento')}{sort_header('Valor')}"
            f'<th class="infraTh">Ações</th></tr>{lines}</table>'
        )
    return f"""<html><body><div id="divInfraAreaTelaD">
<form id="frmGuias" method="post" action="controlador.php?acao={action}&amp;acao_origem={action}&amp;hash=g">
<button type="button" id="btnGerar" onclick="gerarGuia();">Gerar Guia</button>
{extra}{table}</form></div></body></html>"""


GUIAS = [("5000001-00.2026.4.04.7200", "10/11/2026", "20/11/2026", "R$ 150,00")]


def read(function, session, *actions, **arguments):
    return function(session=session, panel_html=panel(*actions), panel_url=BASE, portal=PORTAL, **arguments)


class MessagesTest(unittest.TestCase):
    def test_parse_reads_the_three_columns_with_iso_dates(self):
        parsed = listas.parse_messages(messages_page(UNREAD))
        self.assertEqual(parsed["filter"], "naoLidas")
        self.assertEqual(parsed["total"], 2)
        self.assertEqual(
            parsed["items"][0],
            {
                "sentAt": "2026-11-13T08:33:12",
                "sentAtText": "13/11/2026 08:33:12",
                "title": "Indisponibilidade programada",
                "text": "O sistema ficará indisponível no sábado das 20h às 23h.",
            },
        )

    def test_no_table_is_an_empty_list_and_another_page_is_not_a_list(self):
        self.assertEqual(
            listas.parse_messages(messages_page([])),
            {"filter": "naoLidas", "page": 0, "pages": None, "items": [], "total": 0},
        )
        self.assertIsNone(listas.parse_messages("<html><form id='outra'></form></html>"))

    def test_unread_is_one_get_of_the_list_and_never_opens_a_message(self):
        session = Session(messages_page(UNREAD))
        result = read(listas.list_messages, session, "caixa_mensagem_listar")
        self.assertEqual([method for method, *_ in session.calls], ["GET"])
        self.assertIn("acao=caixa_mensagem_listar", session.calls[0][1])
        self.assertEqual(
            {key: result[key] for key in ("status", "returned", "total", "next")},
            {"status": "unread", "returned": 2, "total": 2, "next": None},
        )
        self.assertEqual(
            [item["title"] for item in result["mensagens"]], ["Indisponibilidade programada", "Nova versão"]
        )
        self.assertNotIn("vizualizar", compact(result))
        self.assertNotIn("hash", compact(result))

    def test_read_posts_the_list_form_with_the_read_filter(self):
        session = Session(messages_page(UNREAD), messages_page(READ, read=True))
        result = read(listas.list_messages, session, "caixa_mensagem_listar", status="read")
        (method, url, kwargs) = session.calls[1]
        self.assertEqual(method, "POST")
        self.assertIn("acao=caixa_mensagem_listar", url)
        self.assertIn(("rdoFiltro", "lidas"), kwargs["data"])
        self.assertNotIn(("rdoFiltro", "naoLidas"), kwargs["data"])
        self.assertEqual(result["mensagens"][0]["sentAt"], "2026-02-02T10:00:00")

    def test_a_filter_the_portal_did_not_apply_is_an_error(self):
        session = Session(messages_page(UNREAD), messages_page(UNREAD))
        with self.assertRaisesRegex(PortalError, "did not show the read messages"):
            read(listas.list_messages, session, "caixa_mensagem_listar", status="read")

    def test_a_long_list_is_cut_to_the_budget_and_next_reads_the_rest(self):
        rows = [(f"0{1 + index % 9}/01/2026 10:00:00", f"Aviso {index}", "x" * 5_000) for index in range(30)]
        first = read(listas.list_messages, Session(messages_page(rows)), "caixa_mensagem_listar")
        self.assertLess(len(compact(first)), 60_000)
        self.assertEqual(first["total"], 30)
        self.assertEqual(first["next"], {"status": "unread", "page": 0, "offset": first["returned"]})
        titles, page = [item["title"] for item in first["mensagens"]], first
        while page["next"]:
            page = read(listas.list_messages, Session(messages_page(rows)), "caixa_mensagem_listar", **page["next"])
            self.assertLess(len(compact(page)), 60_000)
            titles += [item["title"] for item in page["mensagens"]]
        self.assertEqual(titles, [f"Aviso {index}" for index in range(30)])

    def test_cpfs_in_titles_and_texts_are_masked(self):
        rows = [("01/11/2026 09:00:00", "Parte 111.444.777-35", "Procurador CPF 11144477735 cadastrado.")]
        result = read(listas.list_messages, Session(messages_page(rows)), "caixa_mensagem_listar")
        self.assertEqual(result["mensagens"][0]["title"], "Parte 111.***.***-35")
        self.assertEqual(result["mensagens"][0]["text"], "Procurador CPF 111******35 cadastrado.")
        self.assertNotIn("444", compact(result))

    def test_more_portal_pages_give_a_note_and_next_that_posts_the_pager(self):
        first = read(listas.list_messages, Session(messages_page(UNREAD, count=4, pages=2)), "caixa_mensagem_listar")
        self.assertEqual(first["next"], {"status": "unread", "page": 1, "offset": 0})
        self.assertIn("2 páginas", first["notes"][0])
        session = Session(messages_page(UNREAD, count=4, pages=2), messages_page(READ, count=4, pages=2, page=1))
        second = read(listas.list_messages, session, "caixa_mensagem_listar", **first["next"])
        method, url, kwargs = session.calls[1]
        self.assertEqual(method, "POST")
        self.assertIn("acao=caixa_mensagem_listar", url)
        self.assertIn(("hdnInfraPaginaAtual", "1"), kwargs["data"])
        self.assertIn(("rdoFiltro", "naoLidas"), kwargs["data"])
        self.assertEqual((second["page"], second["returned"], second["next"]), (1, 1, None))
        self.assertNotIn("notes", second)

    def test_a_page_the_portal_did_not_show_is_an_error(self):
        session = Session(messages_page(UNREAD, count=4, pages=2), messages_page(UNREAD, count=4, pages=2))
        with self.assertRaisesRegex(PortalError, "did not show page 1"):
            read(listas.list_messages, session, "caixa_mensagem_listar", page=1)

    def test_a_count_beyond_the_page_without_a_pager_is_noted(self):
        result = read(listas.list_messages, Session(messages_page(UNREAD, count=60)), "caixa_mensagem_listar")
        self.assertEqual((result["total"], result["returned"], result["next"]), (60, 2, None))
        self.assertIn("sem paginador", result["notes"][0])

    def test_a_portal_without_the_menu_link_says_not_available(self):
        with self.assertRaises(NotSupported) as raised:
            read(listas.list_messages, Session(), "painel_adv_listar")
        self.assertEqual(raised.exception.code, "not_available")


class ParadasTest(unittest.TestCase):
    def test_parse_reads_both_tables_with_iso_dates_and_a_boolean_prorrogacao(self):
        parsed = listas.parse_paradas(paradas_page(OUTAGES, SUSPENSIONS))
        self.assertEqual(
            parsed["paradas"][0],
            {
                "description": "DIFICULDADE DE ACESSO",
                "prorrogacao": True,
                "start": "2026-10-21T11:00:00",
                "startText": "21/10/2026 11:00:00",
                "end": "2026-10-21T16:00:00",
                "endText": "21/10/2026 16:00:00",
            },
        )
        self.assertFalse(parsed["paradas"][1]["prorrogacao"])
        self.assertEqual(
            parsed["suspensoes"],
            [
                {"date": "2026-01-24", "type": "Fechamento", "dateText": "24/01/2026"},
                {"date": "2026-03-10", "type": "Abertura", "dateText": "10/03/2026"},
            ],
        )
        self.assertEqual(parsed["total"], 2)

    def test_a_portal_without_records_shows_the_form_and_no_table(self):
        self.assertEqual(listas.parse_paradas(paradas_page([], [])), {"paradas": [], "suspensoes": [], "total": 0})

    def test_unknown_columns_are_not_read_as_paradas(self):
        page = paradas_page(OUTAGES, []).replace("Houve Prorrogação de Prazo?", "Outra coluna")
        self.assertIsNone(listas.parse_paradas(page))

    def test_list_is_one_get_and_never_follows_the_row_links(self):
        session = Session(paradas_page(OUTAGES, SUSPENSIONS))
        result = read(listas.list_paradas, session, "eprocsysadm_listar")
        self.assertEqual(len(session.calls), 1)
        self.assertIn("acao=eprocsysadm_listar", session.calls[0][1])
        self.assertEqual((result["returned"], result["total"], result["next"]), (2, 2, None))
        self.assertEqual(len(result["suspensoes"]), 2)
        self.assertNotIn("certidao", compact(result))


class PreparedTest(unittest.TestCase):
    def test_empty_lists_as_captured_are_empty(self):
        for page, form in ((INITIAL_EMPTY, "frmPeticaoInicial"), (MOVEMENT_EMPTY, "frmMovimentacaoFutura")):
            self.assertEqual(
                listas.parse_header_table(page, f"form#{form}"), {"items": [], "total": 0, "mismatched": 0}
            )
        self.assertIsNone(listas.parse_header_table(INITIAL_EMPTY, "form#frmMovimentacaoFutura"))

    def test_a_form_without_a_table_that_does_not_say_empty_is_not_an_empty_list(self):
        for page in (
            INITIAL_EMPTY.replace('<div id="divInfraAreaTabela" class="infraAreaTabela"></div>', ""),
            INITIAL_EMPTY.replace('class="infraAreaTabela"></div>', 'class="infraAreaTabela">Carregando...</div>'),
            INITIAL_EMPTY.replace("<button", '<input type="text" name="dtaInicio" value=""/><button'),
        ):
            with self.subTest(page=page[-260:]):
                self.assertIsNone(listas.parse_header_table(page, "form#frmPeticaoInicial"))
                with self.assertRaisesRegex(PortalError, "unsupported_peticoes_preparadas_layout"):
                    read(listas.list_prepared, Session(page), "peticao_inicial_preparada_listar")
        said = INITIAL_EMPTY.replace("<button", "<p>Nenhum registro encontrado.</p><button").replace(
            '<div id="divInfraAreaTabela" class="infraAreaTabela"></div>', ""
        )
        self.assertEqual(listas.parse_header_table(said, "form#frmPeticaoInicial")["items"], [])

    def test_a_row_whose_cells_do_not_match_the_headers_is_kept_unlabelled_with_a_note(self):
        page = prepared_page("frmPeticaoInicial", "Petições", ("btnDistribuir", "d", "Distribuir"), PREPARED_ROWS)
        page = page.replace("<td>JOÃO INVENTADO</td>", '<td colspan="2">JOÃO INVENTADO</td><td>extra</td>')
        result = read(listas.list_prepared, Session(page), "peticao_inicial_preparada_listar")
        self.assertEqual(result["peticoes"][1]["columns"], None)
        self.assertIn("JOÃO INVENTADO", result["peticoes"][1]["cells"])
        self.assertIsNotNone(result["peticoes"][0]["columns"])
        self.assertEqual(len(result["notes"]), 2)
        self.assertIn("1 linha(s)", result["notes"][1])

    def test_rows_are_read_by_their_headers_without_the_checkbox_and_actions(self):
        page = prepared_page("frmPeticaoInicial", "Petições", ("btnDistribuir", "d", "Distribuir"), PREPARED_ROWS)
        parsed = listas.parse_header_table(page, "form#frmPeticaoInicial")
        self.assertEqual(parsed["total"], 2)
        self.assertEqual(
            parsed["items"][0],
            {
                "processNumber": "5000001-00.2026.4.04.7200",
                "columns": {
                    "Processo": "5000001-00.2026.4.04.7200",
                    "Data de Criação": "2026-11-20T14:05:00",
                    "Data de CriaçãoText": "20/11/2026 14:05",
                    "Tipo": "Petição intercorrente",
                    "Parte": "MARIA FICTÍCIA (123.***.***-09)",
                },
            },
        )
        self.assertEqual(parsed["items"][1]["processNumber"], None)
        self.assertEqual(parsed["items"][1]["columns"]["Processo"], None)

    def test_each_kind_reads_its_own_list_with_one_get(self):
        for kind, action, page in (
            ("iniciais", "peticao_inicial_preparada_listar", INITIAL_EMPTY),
            ("movimentacoes", "movimentacao_preparada_listar", MOVEMENT_EMPTY),
        ):
            with self.subTest(kind=kind):
                session = Session(page)
                result = read(listas.list_prepared, session, action, kind=kind)
                self.assertEqual(
                    [(method, f"acao={action}" in url) for method, url, _ in session.calls], [("GET", True)]
                )
                self.assertEqual(
                    (result["kind"], result["peticoes"], result["total"], result["next"]), (kind, [], 0, None)
                )
                self.assertEqual(result["notes"], [listas.UNVALIDATED])

    def test_another_page_is_an_error_not_an_empty_list(self):
        with self.assertRaises(PortalError):
            read(listas.list_prepared, Session(MOVEMENT_EMPTY), "peticao_inicial_preparada_listar")


class CustasTest(unittest.TestCase):
    def test_each_kind_reads_its_list_without_generating_a_guia(self):
        for kind, action in (
            ("custas", "guia_pagamento_custas_gerados_listar"),
            ("depositos", "guia_pagamento_depositos_judiciais_gerados_listar"),
        ):
            with self.subTest(kind=kind):
                session = Session(guias_page(action, GUIAS))
                result = read(listas.list_custas, session, action, kind=kind)
                self.assertEqual(
                    [(method, f"acao={action}" in url) for method, url, _ in session.calls], [("GET", True)]
                )
                self.assertEqual((result["kind"], result["returned"], result["total"]), (kind, 1, 1))
                self.assertEqual(
                    result["guias"][0],
                    {
                        "processNumber": "5000001-00.2026.4.04.7200",
                        "columns": {
                            "Processo": "5000001-00.2026.4.04.7200",
                            "Data Emissão": "2026-11-10",
                            "Data EmissãoText": "10/11/2026",
                            "Vencimento": "2026-11-20",
                            "VencimentoText": "20/11/2026",
                            "Valor": "R$ 150,00",
                        },
                    },
                )
                self.assertNotIn("imprimir", compact(result))

    def test_empty_only_when_the_page_says_so(self):
        action = "guia_pagamento_custas_gerados_listar"
        said = guias_page(action, extra="<p>Nenhum registro encontrado.</p>")
        result = read(listas.list_custas, Session(said), action)
        self.assertEqual((result["guias"], result["total"], result["notes"]), ([], 0, [listas.UNVALIDATED]))
        zero = guias_page(action, extra='<table class="x"><caption>Lista de Guias (0 registros):</caption></table>')
        self.assertEqual(read(listas.list_custas, Session(zero), action)["guias"], [])
        for page in (
            guias_page(action),
            guias_page(action, extra='<label>Data inicial</label><input type="text" name="dtaInicio" value=""/>'),
            "<html><body>Erro</body></html>",
        ):
            with self.subTest(page=page[-200:]), self.assertRaisesRegex(PortalError, "unsupported_custas_layout"):
                read(listas.list_custas, Session(page), action)

    def test_a_portal_without_the_menu_says_not_available_and_requests_nothing(self):
        session = Session()
        with self.assertRaises(NotSupported) as raised:
            read(listas.list_custas, session, "painel_adv_listar", kind="depositos")
        self.assertEqual(raised.exception.code, "not_available")
        self.assertEqual(session.calls, [])


class AllowlistTest(unittest.TestCase):
    def test_actions_that_open_or_write_stay_blocked(self):
        for action in (
            "caixa_mensagem_vizualizar",
            "eprocsysadm_consultar",
            "eprocsysadm_certidao",
            "peticao_inicial_preparada_distribuir",
            "peticao_inicial_preparada_alterar",
            "movimentacao_preparada_peticionar",
            "processo_custas_consultar",
            "processo_depositos_judiciais_consultar",
            "guia_pagamento_imprimir",
        ):
            with self.subTest(action=action), self.assertRaises(RuntimeError):
                client.require_route(f"{BASE}controlador.php?acao={action}&hash=x", portal=PORTAL)
        for action in (
            "caixa_mensagem_listar",
            "eprocsysadm_listar",
            "peticao_inicial_preparada_listar",
            "movimentacao_preparada_listar",
            "guia_pagamento_custas_gerados_listar",
            "guia_pagamento_depositos_judiciais_gerados_listar",
        ):
            client.require_route(f"{BASE}controlador.php?acao={action}&hash=x", portal=PORTAL)


class ServerTest(unittest.TestCase):
    def call(self, name, arguments, session, html):
        async def run():
            async with Client(server.create_server([PORTAL])) as mcp:
                return await mcp.call_tool(name, arguments)

        with mock.patch.object(server.sessions, "_login", return_value=(session, html, BASE)):
            try:
                return asyncio.run(run())
            finally:
                server.sessions.close()

    def test_eproc_list_mensagens(self):
        result = self.call(
            "eproc_list_mensagens", {"portal": PORTAL}, Session(messages_page(UNREAD)), panel("caixa_mensagem_listar")
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["returned"], 2)

    def test_eproc_list_paradas(self):
        result = self.call(
            "eproc_list_paradas",
            {"portal": PORTAL},
            Session(paradas_page(OUTAGES, SUSPENSIONS)),
            panel("eprocsysadm_listar"),
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["paradas"][1]["prorrogacao"], False)

    def test_eproc_list_peticoes_preparadas(self):
        result = self.call(
            "eproc_list_peticoes_preparadas",
            {"portal": PORTAL, "kind": "movimentacoes"},
            Session(MOVEMENT_EMPTY),
            panel("movimentacao_preparada_listar"),
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["kind"], "movimentacoes")

    def test_eproc_list_custas_with_rows(self):
        action = "guia_pagamento_depositos_judiciais_gerados_listar"
        result = self.call(
            "eproc_list_custas",
            {"portal": PORTAL, "kind": "depositos"},
            Session(guias_page(action, GUIAS)),
            panel(action),
        )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["guias"][0]["processNumber"], "5000001-00.2026.4.04.7200")
        self.assertEqual(result.structured_content["notes"], [listas.UNVALIDATED])

    def test_a_prepared_page_that_does_not_say_empty_is_a_portal_error(self):
        page = MOVEMENT_EMPTY.replace('<div id="divInfraAreaTabela" class="infraAreaTabela"></div>', "")
        result = self.call(
            "eproc_list_peticoes_preparadas",
            {"portal": PORTAL, "kind": "movimentacoes"},
            Session(page),
            panel("movimentacao_preparada_listar"),
        )
        self.assertTrue(result.is_error)
        self.assertIn("[portal_error]", result.content[0].text)
        self.assertIn("unsupported_peticoes_preparadas_layout", result.content[0].text)

    def test_not_available_is_a_clear_tool_error(self):
        for name in ("eproc_list_mensagens", "eproc_list_custas"):
            with self.subTest(tool=name):
                result = self.call(name, {"portal": PORTAL}, Session(), panel("painel_adv_listar"))
                self.assertTrue(result.is_error)
                self.assertIn("[not_available]", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
