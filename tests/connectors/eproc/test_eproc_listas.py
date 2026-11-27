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


def messages_page(rows, *, read=False, count=None):
    table = ""
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
<input type="hidden" id="hdnInfraPaginaAtual" name="hdnInfraPaginaAtual" value="0"/>
{table}
</form></div></body></html>"""


UNREAD = [
    ("13/11/2026 08:33:12", "Indisponibilidade programada", "O sistema ficará indisponível no sábado das 20h às 23h."),
    ("07/12/2025 16:15:41", "Nova versão", "Correção no envio de petições intercorrentes."),
]
READ = [("02/02/2026 10:00:00", "Aviso antigo", "Mensagem já lida pelo advogado fictício.")]


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
                "title": "Indisponibilidade programada",
                "text": "O sistema ficará indisponível no sábado das 20h às 23h.",
            },
        )

    def test_no_table_is_an_empty_list_and_another_page_is_not_a_list(self):
        self.assertEqual(listas.parse_messages(messages_page([])), {"filter": "naoLidas", "items": [], "total": 0})
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
        self.assertEqual(first["next"], {"status": "unread", "offset": first["returned"]})
        titles, page = [item["title"] for item in first["mensagens"]], first
        while page["next"]:
            page = read(listas.list_messages, Session(messages_page(rows)), "caixa_mensagem_listar", **page["next"])
            self.assertLess(len(compact(page)), 60_000)
            titles += [item["title"] for item in page["mensagens"]]
        self.assertEqual(titles, [f"Aviso {index}" for index in range(30)])

    def test_a_portal_without_the_menu_link_says_not_available(self):
        with self.assertRaises(NotSupported) as raised:
            read(listas.list_messages, Session(), "painel_adv_listar")
        self.assertEqual(raised.exception.code, "not_available")


class AllowlistTest(unittest.TestCase):
    def test_actions_that_open_or_write_stay_blocked(self):
        for action in ("caixa_mensagem_vizualizar",):
            with self.subTest(action=action), self.assertRaises(RuntimeError):
                client.require_route(f"{BASE}controlador.php?acao={action}&hash=x", portal=PORTAL)
        for action in ("caixa_mensagem_listar",):
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

    def test_not_available_is_a_clear_tool_error(self):
        result = self.call("eproc_list_mensagens", {"portal": PORTAL}, Session(), panel("painel_adv_listar"))
        self.assertTrue(result.is_error)
        self.assertIn("[not_available]", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
