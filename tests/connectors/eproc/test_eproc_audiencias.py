"""eproc_list_audiencias: the panel's upcoming hearing and conciliation lists, soonest first."""

import unittest

from jurisync.connectors.eproc import client
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

    def request(self, method, url, **_kwargs):
        self.urls.append(url)
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


if __name__ == "__main__":
    unittest.main()
