"""eproc_list_intimacoes: the shared situação of every row, and the default list of what needs the lawyer."""

import unittest

from jurisync.connectors.eproc import client
from jurisync.core import intimacao

BASE = "https://eproc1g.tjmg.jus.br/eproc/"
PANEL_URL = BASE + "controlador.php?acao=painel_adv_listar&hash=p"
PANEL = (
    '<a href="controlador.php?acao=citacao_intimacao_pendente_listar&amp;hash=q">16</a>'
    '<a href="controlador.php?acao=citacao_intimacao_prazo_aberto_listar&amp;vence_hoje=S&amp;hash=t">1</a>'
    '<a href="controlador.php?acao=citacao_intimacao_prazo_aberto_listar&amp;hash=o">43</a>'
    '<a href="controlador.php?acao=citacao_intimacao_prazo_aberto_listar&amp;urgente=true&amp;hash=u">0</a>'
)


def row(number, identifier, start, end, *, expired=False):
    first = "" if expired else f'<td><input class="infraCheckbox" value="{identifier}"></td>'
    middle = "" if expired else f"<td>{start}</td>"
    return (
        f'<tr class="infraTrClara">{first}<td><a href="controlador.php?acao=processo_selecionar&amp;hash=x">{number}</a>'
        "<br/><b>Juízo: </b>BHE 6 V.C. J</td><td>Classe</td><td>Assunto</td>"
        f"<td>Expedida/certificada a intimação eletrônica<br/>15 dias</td><td>25/11/2026 17:05:41</td>{middle}"
        f"<td>{end}</td>{'<td></td>' if expired else ''}</tr>"
    )


def queue(*rows, expired=False):
    headers = ["Processo", "Classe", "Assunto", "Evento e Prazo", "Data envio requisição"]
    headers = [*headers, "Final Prazo", "Conferido ?"] if expired else ["", *headers, "Início Prazo", "Final Prazo"]
    header = "<tr>" + "".join(f"<th>{name}</th>" for name in headers) + "</tr>"
    caption = f"<caption>Lista ({len(rows)} registros):</caption>"
    return f'<table class="infraTable">{caption}{header}{"".join(rows)}</table>'


PENDING = queue(row("0000001-23.2026.8.13.0024", "11|1", "Aguarda publicação no DJEN", ""))
OPEN = queue(
    row("0000002-08.2026.8.13.0024", "22|1", "13/11/2026 00:00:00", "27/11/2026 23:59:59"),
    row("0000003-90.2026.8.13.0024", "33|1", "14/11/2026 00:00:00", "04/12/2026 23:59:59"),
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


def listing(queue_name=None, **pages):
    portal = Portal(**pages)
    result = client.list_notices(queue_name, session=portal, panel_html=PANEL, panel_url=PANEL_URL, portal="tjmg-1g")
    return result, portal.urls


class SituationTest(unittest.TestCase):
    def test_each_queue_gives_its_situation_and_portal_deadline(self):
        cases = [
            ({"deadlineStart": "Aguarda publicação no DJEN"}, "pending", (intimacao.AWAITING_PUBLICATION, None, None)),
            ({"deadlineStart": "", "deadlineEnd": ""}, "pending_urgent", (intimacao.AWAITING_CIENCIA, None, None)),
            ({"deadlineEnd": "27/11/2026 23:59:59"}, "open_deadlines", (intimacao.RUNNING, "2026-11-27", "portal")),
            ({"deadlineEnd": "27/11/2026 23:59:59"}, "due_today", (intimacao.RUNNING, "2026-11-27", "portal")),
            ({"deadlineEnd": "07/10/2026 23:59:59"}, "expired_deadline", (intimacao.EXPIRED, "2026-10-07", "portal")),
            ({"deadlineEnd": ""}, "hearing_notice", (intimacao.UNKNOWN, None, None)),
        ]
        for item, queue_name, expected in cases:
            with self.subTest(queue=queue_name):
                found = client.situation(item, queue_name)
                self.assertEqual((found["situacao"], found["prazoFinal"], found["prazoFinalTipo"]), expected)


class AttentionListTest(unittest.TestCase):
    def test_without_queue_pending_and_open_deadlines_come_together(self):
        result, urls = listing(q=PENDING, o=OPEN)
        self.assertTrue(result["success"])
        self.assertIsNone(result["queue"])
        self.assertEqual(
            [(item["queue"], item["situacao"], item["prazoFinal"]) for item in result["items"]],
            [
                ("pending", "aguardando_publicacao", None),
                ("open_deadlines", "prazo_correndo", "2026-11-27"),
                ("open_deadlines", "prazo_correndo", "2026-12-04"),
            ],
        )
        self.assertEqual(result["items"][0]["court"], "BHE 6 V.C. J")
        self.assertEqual(
            [(entry["queue"], entry["count"]) for entry in result["queues"]], [("pending", 1), ("open_deadlines", 2)]
        )
        self.assertTrue(result["pagination"]["complete"])
        self.assertFalse(any("vence_hoje" in url or "urgente" in url for url in urls))
        self.assertNotIn("url", str(result["items"]))

    def test_an_intimacao_in_two_queues_is_listed_once(self):
        both = queue(row("0000002-08.2026.8.13.0024", "22|1", "Aguarda publicação no DJEN", ""))
        result, _ = listing(q=both, o=OPEN)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["queue"], "pending")

    def test_an_unreadable_open_deadline_page_is_not_complete(self):
        result, _ = listing(q=PENDING, o="<html>erro</html>")
        self.assertTrue(result["success"])  # the open-deadline page had no table: zero rows, not complete
        self.assertFalse(result["pagination"]["complete"])

    def test_explicit_queues_keep_their_own_links(self):
        for name, marker in (("urgent", "urgente=true"), ("due_today", "vence_hoje=S"), ("open_deadlines", "hash=o")):
            with self.subTest(queue=name):
                result, urls = listing(name, u=queue(), t=OPEN, o=OPEN)
                self.assertIn(marker, urls[-1])
                self.assertEqual(result["queue"], name)
                self.assertTrue(all(item["queue"] == name for item in result["items"]))

    def test_expired_rows_carry_the_portal_final_date(self):
        expired = queue(row("0000004-75.2026.8.13.0024", "", "", "07/10/2026 23:59:59", expired=True), expired=True)
        panel = PANEL + '<a href="controlador.php?acao=citacao_intimacao_decurso_prazo_listar&amp;hash=d">67</a>'
        portal = Portal(d=expired)
        result = client.list_notices(
            "expired_deadline", session=portal, panel_html=panel, panel_url=PANEL_URL, portal="tjmg-1g"
        )
        self.assertEqual(
            [(item["situacao"], item["prazoFinal"]) for item in result["items"]], [("prazo_vencido", "2026-10-07")]
        )


if __name__ == "__main__":
    unittest.main()
