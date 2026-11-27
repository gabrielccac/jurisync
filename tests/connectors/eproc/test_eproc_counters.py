"""eproc_get_counters: the panel's ajax counters and each instance's citações/intimações counters."""

import asyncio
import json
import unittest
from unittest import mock

from mcp.client.client import Client

from jurisync.connectors.eproc import client, server
from jurisync.connectors.eproc.parsers import parse_panel, parse_panel_counters
from jurisync.core.session_pool import SessionExpired

BASE = "https://eproc1g.tjmg.jus.br/eproc/"
PANEL_URL = BASE + "controlador.php?acao=painel_adv_listar&hash=p"
# Shape of the TJMG lawyer panel (27/11/2026): two instance tabs, the "expiring today" link and the ajax counters.
PANEL = """
<a class="nav-link" href="#MG" data-urlAssinadaCarregamento="controlador_ajax.php?acao_ajax=retornar_citacao_intimacao_estado&estado=MG&usuarioAtual=true&hash=s1">MG</a>
<a class="nav-link" href="#TJMG" data-urlAssinadaCarregamento="controlador_ajax.php?acao_ajax=retornar_citacao_intimacao_estado&estado=TJMG&hash=s2">TJMG</a>
<fieldset id="fldCitacaoIntimacaoMG" data-urlAssinadaCarregamento="controlador_ajax.php?acao_ajax=retornar_citacao_intimacao_estado&estado=MG&usuarioAtual=true&hash=s1">
<a href="controlador.php?acao=citacao_intimacao_prazo_aberto_listar&vence_hoje=S&acao_origem=painel_adv_listar&hash=t">1</a></fieldset>
<script>
    var ajaxBuscarDadosPainel = function () {
        this.ajaxTarget = 'controlador_ajax.php?acao_ajax=listar_painel_advogado&hash=c';
    };
    function inicializar() {
        buscarDadosPainel('fldCitacaoIntimacaoAdvogado');
        buscarDadosPainel('fldAudiencia');
        buscarDadosPainel('fldDepositosJudiciais');
        buscarDadosPainel('fldAudiencia');
    }
</script>"""
XML = '<?xml version="1.0" encoding="iso-8859-1"?>\n<{0}>{1}</{0}>'
FIELDSETS = {
    "fldCitacaoIntimacaoAdvogado": XML.format(
        "fldCitacaoIntimacaoAdvogado",
        "<citacaoIntimacao>16</citacaoIntimacao><processoPrazoAberto>43</processoPrazoAberto>",
    ),
    "fldAudiencia": XML.format("fldAudiencia", "<audienciaFutura>2</audienciaFutura><novoContador>5</novoContador>"),
    # TJMG answers this fieldset with a PHP error page.
    "fldDepositosJudiciais": "<br /><b>Fatal error</b>: Uncaught Error: Class not found",
}


def state(pending, opened):
    results = {"numCitacaoIntimacaoPendente": str(pending), "numCitacaoIntimacaoAberto": str(opened), "dias": "30"}
    return json.dumps({"usuarios": [{"str_ident": "X", "resultados": {**results, "bolCPC": "1"}, "links": {}}]})


class Response:
    def __init__(self, body):
        self.status_code = 200
        self.content = body.encode("iso-8859-1")
        self.url = BASE
        self.headers = {}


class Portal:
    def __init__(self, fieldsets=FIELDSETS, states=None):
        self.fieldsets = fieldsets
        self.states = states or {"s1": state(16, 43), "s2": "Perfil não cadastrado"}
        self.calls = []

    def request(self, method, url, data=None, **_kwargs):
        self.calls.append((method, url, data))
        if "listar_painel_advogado" in url:
            return Response(self.fieldsets[data["idFieldset"]])
        return Response(self.states[url.split("hash=")[1]])

    def close(self):
        pass


class CountersTest(unittest.TestCase):
    def test_panel_names_its_counter_call_fieldsets_and_instances(self):
        panel = parse_panel(PANEL, BASE)
        self.assertIn("listar_painel_advogado", panel["countersEndpoint"])
        self.assertEqual(
            panel["counterFieldsets"], ["fldCitacaoIntimacaoAdvogado", "fldAudiencia", "fldDepositosJudiciais"]
        )
        self.assertEqual(
            [(entry["instance"], entry["label"], entry["current"]) for entry in panel["instances"]],
            [("MG", "MG", True), ("TJMG", "TJMG", False)],
        )
        self.assertEqual(panel["dueTodayCount"], 1)

    def test_xml_reply_reads_every_counter_and_anything_else_is_none(self):
        self.assertEqual(parse_panel_counters(FIELDSETS["fldAudiencia"]), {"audienciaFutura": 2, "novoContador": 5})
        self.assertIsNone(parse_panel_counters(FIELDSETS["fldDepositosJudiciais"]))
        self.assertIsNone(parse_panel_counters(""))

    def test_counters_tolerate_a_failing_fieldset_and_instance(self):
        portal = Portal()
        result = client.read_counters(session=portal, panel_html=PANEL, panel_url=PANEL_URL, portal="tjmg-1g")
        self.assertTrue(result["success"])
        panel = {entry["fieldset"]: entry for entry in result["panel"]}
        self.assertEqual(
            [(counter["label"], counter["count"]) for counter in panel["fldCitacaoIntimacaoAdvogado"]["counters"]],
            [("Pendentes", 16), ("Prazo aberto", 43)],
        )
        self.assertEqual(
            panel["fldAudiencia"]["counters"][1], {"key": "novoContador", "label": "novoContador", "count": 5}
        )
        self.assertIn("error", panel["fldDepositosJudiciais"])
        self.assertEqual(
            result["instances"][0]["counters"], {"pending": 16, "open_deadlines": 43, "expiredDeadlineDays": 30}
        )
        self.assertTrue(result["instances"][0]["current"])
        self.assertIn("error", result["instances"][1])
        self.assertEqual(result["dueToday"], 1)
        posts = [data for method, _, data in portal.calls if method == "POST"]
        self.assertEqual([data["idFieldset"] for data in posts], list(FIELDSETS))
        self.assertTrue(all(data["exibePauta"] == "1" for data in posts))

    def test_a_login_page_expires_the_session(self):
        portal = Portal(fieldsets={**FIELDSETS, "fldAudiencia": '<form><input name="username"></form>'})
        with self.assertRaises(SessionExpired):
            client.read_counters(session=portal, panel_html=PANEL, panel_url=PANEL_URL, portal="tjmg-1g")

    def test_a_panel_without_the_counter_call_still_reads_the_instances(self):
        panel = PANEL.split("<script>")[0]
        result = client.read_counters(session=Portal(), panel_html=panel, panel_url=PANEL_URL, portal="tjmg-1g")
        self.assertEqual(result["panel"], [])
        self.assertEqual(result["diagnostics"], ["panel_counters_unavailable"])
        self.assertEqual(len(result["instances"]), 2)

    def test_tool_is_read_only_and_reaches_the_client(self):
        srv = server.create_server(["tjmg-1g"])

        async def run():
            async with Client(srv) as session:
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                result = await session.call_tool("eproc_get_counters", {"portal": "tjmg-1g"})
                return tools["eproc_get_counters"], result

        with (
            mock.patch.object(server.sessions, "_login", return_value=(Portal(), PANEL, PANEL_URL)),
            mock.patch.object(server, "read_counters", wraps=client.read_counters) as counters,
        ):
            tool, result = asyncio.run(run())
        server.sessions.close()
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertFalse(result.is_error)
        self.assertEqual(counters.call_args.kwargs["portal"], "tjmg-1g")


if __name__ == "__main__":
    unittest.main()
