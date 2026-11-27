"""eproc_list_audiencias: the panel's audiência lists (upcoming, held, not held), with their conciliation twins."""

import asyncio
import unittest
from unittest import mock

from mcp.client.client import Client

from jurisync.connectors.eproc import client, server
from jurisync.connectors.eproc.parsers import parse_hearing_list
from jurisync.core.session_pool import SessionExpired

BASE = "https://eproc1g.tjmg.jus.br/eproc/"
PANEL_URL = BASE + "controlador.php?acao=painel_adv_listar&hash=p"
PANEL = (
    '<a href="controlador.php?acao=audiencia_futura_listar&amp;proxima=S&amp;hash=n">próxima</a>'
    '<a href="controlador.php?acao=audiencia_futura_listar&amp;hash=a">2</a>'
    '<a href="controlador.php?acao=audiencia_futura_conciliacao_listar&amp;hash=c">8</a>'
)


def hearing(number, court, event, room, start, end):
    # Shape of TJMG "Lista de Audiências" (27/11/2026), names replaced; the portal leaves the place <td> unclosed.
    return (
        f'<tr class="infraTrClara"><td style="text-align:center;"><a href="controlador.php?acao=processo_selecionar'
        f'&num_processo=1&hash=h">{number}</a><br/>({court})<p><b>Autor</b><br />FULANO DE TAL - <b>AUTOR</b><br />'
        f"X<BR /><b>Réu</b><br />BANCO EXEMPLO S.A. - <b>RÉU</b><br /><br /></td><td>{event}</td>"
        f'<td style="text-align:center;"><b>Sala</b>: <br/>{room}<p><br/><b>Início</b><br/>{start}<br/>'
        f"<b>Previsão Término</b><br/>{end}<td></td></tr>"
    )


def page(*rows, title="Lista de Audiências"):
    if not rows:
        return '<div id="divInfraAreaTabela" class="infraAreaTabela">\n</div>'
    header = "<tr><th>Número Processo</th><th>Evento</th><th>Local Data/Hora</th><th>Observação</th></tr>"
    return (
        f'<table class="infraTable"><caption>{title} ({len(rows)} registros):</caption>{header}{"".join(rows)}</table>'
    )


HEARINGS = page(
    hearing(
        "0000001-23.2026.8.13.0024",
        "5ª Vara Cível da Comarca de Exemplo",
        "Audiência de Instrução e Julgamento designada conduzida por Juiz(a)",
        "sala de audiências 5ª Vara Cível",
        "12/05/2027 15:30:00",
        "12/05/2027 16:30:00",
    )
)
CONCILIATIONS = page(
    hearing(
        "0000002-08.2026.8.13.0024",
        "Vara Única",
        "Audiência de Conciliação designada",
        "CEJUSC",
        "30/11/2026 15:00:00",
        "30/11/2026 15:30:00",
    )
)


class Response:
    def __init__(self, body):
        self.status_code = 200
        self.content = body.encode("iso-8859-1")
        self.url = BASE
        self.headers = {}


class Portal:
    def __init__(self, **pages):
        self.pages = pages
        self.urls = []

    def request(self, method, url, **kwargs):
        self.urls.append(url)
        self.posts = [*getattr(self, "posts", []), kwargs["data"]] if method == "POST" else getattr(self, "posts", [])
        return Response(self.pages[url.split("hash=")[1]])

    def close(self):
        pass


class HearingListTest(unittest.TestCase):
    def test_rows_read_number_court_parties_place_and_times(self):
        parsed = parse_hearing_list(HEARINGS, BASE)
        self.assertTrue(parsed["pagination"]["complete"])
        item = parsed["items"][0]
        self.assertEqual(
            {key: item[key] for key in ("number", "court", "event", "location", "start", "end", "observation")},
            {
                "number": "0000001-23.2026.8.13.0024",
                "court": "5ª Vara Cível da Comarca de Exemplo",
                "event": "Audiência de Instrução e Julgamento designada conduzida por Juiz(a)",
                "location": "sala de audiências 5ª Vara Cível",
                "start": "12/05/2027 15:30:00",
                "end": "12/05/2027 16:30:00",
                "observation": "",
            },
        )
        self.assertEqual(
            item["parties"],
            [{"role": "AUTOR", "name": "FULANO DE TAL"}, {"role": "RÉU", "name": "BANCO EXEMPLO S.A."}],
        )
        self.assertEqual(item["processNumber"], item["number"])

    def test_an_empty_list_is_complete_and_another_page_is_not(self):
        self.assertEqual(parse_hearing_list(page(), BASE)["pagination"]["totalItems"], 0)
        self.assertTrue(parse_hearing_list(page(), BASE)["pagination"]["complete"])
        self.assertFalse(parse_hearing_list("<html>erro</html>", BASE)["pagination"]["complete"])

    def test_both_lists_come_together_soonest_first(self):
        portal = Portal(a=HEARINGS, c=CONCILIATIONS)
        result = client.list_hearings(session=portal, panel_html=PANEL, panel_url=PANEL_URL, portal="tjmg-1g")
        self.assertTrue(result["success"])
        self.assertEqual(
            [(item["kind"], item["date"]) for item in result["audiencias"]],
            [("conciliacao", "2026-11-30"), ("audiencia", "2027-05-12")],
        )
        self.assertEqual(result["returned"], 2)
        self.assertEqual(
            [(entry["kind"], entry["count"]) for entry in result["lists"]], [("audiencia", 1), ("conciliacao", 1)]
        )
        self.assertFalse(any("proxima" in url for url in portal.urls))
        self.assertNotIn("url", result["audiencias"][0])

    def test_a_panel_without_the_conciliation_list_says_so(self):
        panel = PANEL.split('<a href="controlador.php?acao=audiencia_futura_conciliacao')[0]
        result = client.list_hearings(session=Portal(a=page()), panel_html=panel, panel_url=PANEL_URL, portal="tjmg-1g")
        self.assertTrue(result["success"])
        self.assertEqual(result["audiencias"], [])
        self.assertEqual(result["lists"][1]["problem"], "not in the panel")

    def test_a_login_page_expires_the_session(self):
        portal = Portal(a='<input name="username">', c=CONCILIATIONS)
        with self.assertRaises(SessionExpired):
            client.list_hearings(session=portal, panel_html=PANEL, panel_url=PANEL_URL, portal="tjmg-1g")


HELD_PANEL = (
    '<a href="controlador.php?acao=audiencia_realizada_listar&amp;hash=r">Realizadas</a>'
    '<a href="controlador.php?acao=audiencia_realizada_conciliacao_listar&amp;hash=rc">Realizadas conciliação</a>'
    '<a href="controlador.php?acao=audiencia_nao_realizada_listar&amp;hash=n">Não realizadas</a>'
)
FIRST = "5000001-00.2026.8.13.0024"
SECOND = "5000002-00.2026.8.13.0024"


def row(number, start):
    return hearing(number, "Vara Única", "Audiência realizada", "Sala 1", start, start[:11] + "17:00:00")


def paged(rows, *, page_index, pages, action="audiencia_realizada_listar", total=None):
    options = "".join(f'<option value="{index}">{index + 1}</option>' for index in range(pages))
    table = page(*rows).replace(f"({len(rows)} registros)", f"({total or len(rows)} registros)")
    return (
        f'<form id="frmRelatorioFinal" method="post" action="controlador.php?acao={action}&amp;hash=p{page_index + 1}">'
        f'<input type="hidden" name="hdnInfraPaginaAtual" value="{page_index}">'
        f'<input type="hidden" name="hdnExcel" value="1"><select id="selInfraPaginacaoSuperior">{options}</select>'
        f"{table}</form>"
    )


def held(session, **kwargs):
    return client.list_hearings(
        "held", session=session, panel_html=HELD_PANEL, panel_url=PANEL_URL, portal="tjmg-1g", **kwargs
    )


class HearingSituationsTest(unittest.TestCase):
    def test_held_lists_latest_first(self):
        portal = Portal(r=page(row(FIRST, "10/03/2026 14:00:00")), rc=page(row(SECOND, "20/05/2026 09:30:00")))
        result = held(portal)
        self.assertEqual(
            [url.split("acao=")[1].split("&")[0] for url in portal.urls],
            [
                "audiencia_realizada_listar",
                "audiencia_realizada_conciliacao_listar",
            ],
        )
        self.assertEqual(
            (result["situation"], result["returned"], result["total"], result["next"]), ("held", 2, 2, None)
        )
        first = result["audiencias"][0]
        self.assertEqual((first["kind"], first["processNumber"], first["number"]), ("conciliacao", SECOND, SECOND))
        self.assertEqual(
            (first["start"], first["startText"], first["end"], first["date"]),
            ("2026-05-20T09:30:00", "20/05/2026 09:30:00", "2026-05-20T17:00:00", "2026-05-20"),
        )

    def test_kind_and_process_number_narrow_the_read(self):
        portal = Portal(r=page(row(FIRST, "10/03/2026 14:00:00"), row(SECOND, "11/03/2026 14:00:00")))
        result = held(portal, kind="audiencia", process_number=SECOND)
        self.assertEqual(len(portal.urls), 1)
        self.assertEqual([item["processNumber"] for item in result["audiencias"]], [SECOND])
        self.assertEqual(result["total"], 1)
        not_held = client.list_hearings(
            "not_held", "conciliacao", session=Portal(), panel_html=HELD_PANEL, panel_url=PANEL_URL, portal="tjmg-1g"
        )
        self.assertEqual(
            not_held["lists"], [{"kind": "conciliacao", "count": 0, "complete": False, "problem": "not in the panel"}]
        )
        self.assertFalse(not_held["success"])

    def test_upcoming_stays_the_default(self):
        result = client.list_hearings(
            session=Portal(a=HEARINGS, c=CONCILIATIONS), panel_html=PANEL, panel_url=PANEL_URL, portal="tjmg-1g"
        )
        self.assertEqual(result["situation"], "upcoming")
        self.assertEqual([item["kind"] for item in result["audiencias"]], ["conciliacao", "audiencia"])

    def test_pages_follow_the_list_pager_up_to_max_pages(self):
        first = paged([row(FIRST, "10/03/2026 14:00:00")], page_index=0, pages=2, total=2)
        second = paged([row(SECOND, "09/03/2026 14:00:00")], page_index=1, pages=2, total=2)
        result = held(Portal(r=first), kind="audiencia")
        self.assertEqual((result["returned"], result["total"], result["next"]), (1, 2, {"maxPages": 2}))
        self.assertFalse(result["lists"][0]["complete"])
        portal = Portal(r=first, p1=second)
        result = held(portal, kind="audiencia", max_pages=2)
        self.assertIn("acao=audiencia_realizada_listar", portal.urls[1])
        self.assertEqual(dict(portal.posts[0])["hdnInfraPaginaAtual"], "1")
        self.assertEqual(dict(portal.posts[0])["hdnExcel"], "0")
        self.assertEqual([item["processNumber"] for item in result["audiencias"]], [FIRST, SECOND])
        self.assertTrue(result["lists"][0]["complete"])
        self.assertIsNone(result["next"])

    def test_an_answer_over_the_budget_is_cut_with_an_offset_to_go_on(self):
        rows = [row(FIRST, "10/03/2026 14:00:00").replace("Sala 1", "Sala " + "x" * 300)] * 300
        portal_page = page(*rows)
        result = held(Portal(r=portal_page), kind="audiencia")
        seen = result["returned"]
        self.assertLess(seen, 300)
        while result["next"]:
            self.assertLess(len(client.compact(result)), client.BUDGET)
            result = held(Portal(r=portal_page), kind="audiencia", offset=result["next"]["offset"])
            seen += result["returned"]
        self.assertEqual(seen, 300)

    def test_a_caption_counting_more_rows_than_read_is_not_complete(self):
        one_of_many = page(row(FIRST, "10/03/2026 14:00:00")).replace("(1 registros)", "(843 registros - 1 a 500)")
        result = held(Portal(r=one_of_many), kind="audiencia")
        self.assertFalse(result["lists"][0]["complete"])
        self.assertEqual(result["total"], 843)
        filtered = held(Portal(r=one_of_many), kind="audiencia", process_number=FIRST)
        self.assertEqual((filtered["returned"], filtered["total"]), (1, None))

    def test_one_item_over_the_budget_comes_alone_and_next_moves_on(self):
        huge = row(FIRST, "10/03/2026 14:00:00").replace("Sala 1", "Sala " + "x" * client.BUDGET)
        rows = page(huge, row(SECOND, "11/03/2026 14:00:00"))
        result = held(Portal(r=rows), kind="audiencia")
        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["next"], {"maxPages": 1, "offset": 1})
        rest = held(Portal(r=rows), kind="audiencia", offset=1)
        self.assertEqual((rest["returned"], rest["next"]), (1, None))

    def test_items_without_a_date_come_last_in_every_order(self):
        undated = (
            row(FIRST, "10/03/2026 14:00:00").replace("10/03/2026 14:00:00", "").replace("10/03/2026 17:00:00", "")
        )
        rows = page(undated, row(SECOND, "11/03/2026 14:00:00"), row(FIRST, "12/03/2026 14:00:00"))
        result = held(Portal(r=rows), kind="audiencia")
        self.assertEqual([item["date"] for item in result["audiencias"]], ["2026-03-12", "2026-03-11", None])
        upcoming = client.list_hearings(
            "upcoming", "audiencia", session=Portal(a=rows), panel_html=PANEL, panel_url=PANEL_URL, portal="tjmg-1g"
        )
        self.assertEqual([item["date"] for item in upcoming["audiencias"]], ["2026-03-11", "2026-03-12", None])

    def test_another_layout_is_a_problem_not_an_empty_list(self):
        result = held(Portal(r="<html><h1>Outra tela</h1></html>"), kind="audiencia")
        self.assertFalse(result["success"])
        self.assertEqual(result["lists"][0]["problem"], "layout not recognized")

    def test_bad_arguments_are_refused_before_any_request(self):
        for arguments in ({"situation": "all"}, {"kind": "pericia"}, {"process_number": "123"}, {"max_pages": 0}):
            with self.assertRaises(ValueError):
                client.list_hearings(
                    session=Portal(), panel_html=PANEL, panel_url=PANEL_URL, portal="tjmg-1g", **arguments
                )


class HearingWriteGuardTest(unittest.TestCase):
    WRITES = ("intimacao_pauta_confirmar", "audiencia_cadastrar", "audiencia_alterar", "processo_movimentar")

    def test_hearing_lists_are_read_actions_and_writes_are_not(self):
        self.assertEqual(len(client.HEARING_ACTIONS), 6)
        self.assertLessEqual(client.HEARING_ACTIONS, client.READ_ACTIONS)
        for action in self.WRITES:
            self.assertNotIn(action, client.READ_ACTIONS)
            with self.assertRaises(RuntimeError):
                client.require_route(
                    f"{BASE}controlador.php?acao={action}&hash=w", client.HEARING_ACTIONS, portal="tjmg-1g"
                )

    def test_a_pager_form_pointing_elsewhere_is_never_posted(self):
        first = paged([row(FIRST, "10/03/2026 14:00:00")], page_index=0, pages=2, action="intimacao_pauta_confirmar")
        portal = Portal(r=first)
        with self.assertRaises(RuntimeError):
            held(portal, kind="audiencia", max_pages=2)
        self.assertEqual(len(portal.urls), 1)


class HearingToolTest(unittest.TestCase):
    def test_tool_passes_its_arguments_and_keeps_upcoming_as_default(self):
        seen = []

        def fake(situation, kind, process_number, **kwargs):
            seen.append((situation, kind, process_number, kwargs["max_pages"], kwargs["offset"]))
            return {"success": True, "audiencias": [], "returned": 0, "total": 0, "next": None}

        async def run(srv):
            async with Client(srv) as mcp:
                tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
                await mcp.call_tool("eproc_list_audiencias", {"portal": "trf4-1g-sc"})
                arguments = {"portal": "trf4-1g-sc", "situation": "not_held", "kind": "conciliacao", "maxPages": 3}
                await mcp.call_tool("eproc_list_audiencias", {**arguments, "processNumber": FIRST, "offset": 5})
                return tools["eproc_list_audiencias"]

        with (
            mock.patch.object(server.sessions, "_login", return_value=(mock.Mock(), "<html></html>", "https://x")),
            mock.patch.object(server, "list_hearings", side_effect=fake),
        ):
            tool = asyncio.run(run(server.create_server(["trf4-1g-sc"])))
        server.sessions.close()
        self.assertTrue(tool.annotations.read_only_hint)
        self.assertEqual(seen, [("upcoming", None, None, 1, 0), ("not_held", "conciliacao", FIRST, 3, 5)])


if __name__ == "__main__":
    unittest.main()
