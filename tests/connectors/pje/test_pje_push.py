import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jurisync.connectors.pje import push


class PjeNotificationChecks(unittest.TestCase):
    def test_push_pagination_ignores_impossible_page_text_and_incomplete_counts(self):
        result = push.parse_push_page(
            "<div>160 resultados encontrados</div><p>referência 7 de 2</p>",
            rows_returned=162,
        )
        self.assertIsNone(result["current_page"])
        self.assertIsNone(result["total_pages"])
        self.assertFalse(result["complete"])

    def test_an_empty_push_registry_is_a_complete_list_of_zero(self):
        # TRF5 2G / TJRJ 1G with nothing registered: no grid, no count, only this sentence.
        html = """<ul><li>Processos cadastrados</li><li>Processos relacionados</li></ul>
        <div class="alert">Não há nenhum processo cadastrado na sua lista de processos do Push.</div>"""
        self.assertEqual(push.parse_push_items(html), [])
        result = push.parse_push_page(html, rows_returned=0)
        self.assertEqual(result["results_count"], 0)
        self.assertTrue(result["complete"])

    def test_push_rows_are_scoped_to_the_watched_process_grid(self):
        html = """<table><tr><th>Processo</th><th>Data de inclusão</th><th>Email alternativo</th><th>Observação</th></tr><tr><td></td><td>1016572-38.2026.4.01.0000</td><td>today</td><td></td><td></td></tr></table><table><tr><th>Processo</th></tr><tr><td>0006826-91.2011.4.01.0000</td></tr></table>"""
        self.assertEqual(
            [item["process_number"] for item in push.parse_push_items(html)], ["1016572-38.2026.4.01.0000"]
        )

    def test_an_empty_unrelated_table_with_a_process_column_is_not_the_grid(self):
        # The page renders the "Processos relacionados" tab too, with its own (empty) "Processo" grid.
        html = """<table id="f:processosRelacionadosTable"><thead><tr><th>Processo</th><th>Data de inclusão</th>
        <th>Email alternativo</th><th>Observação</th></tr></thead><tbody></tbody></table>
        <table id="f:grid"><tr><th></th><th>Processo</th></tr>
        <tr><td></td><td>0000001-11.2026.8.13.0024</td></tr><tr><td></td><td>0000002-22.2026.8.13.0024</td></tr></table>"""
        items = push.parse_push_items(html)
        self.assertEqual(
            [i["process_number"] for i in items], ["0000001-11.2026.8.13.0024", "0000002-22.2026.8.13.0024"]
        )

    def test_an_empty_process_table_with_numbers_elsewhere_is_not_an_empty_registry(self):
        html = """<table id="f:processosCadastradosTable"><tr><th>Processo</th><th>Data de inclusão</th></tr></table>
        <table id="f:other"><tr><th>Número</th></tr><tr><td>0000001-11.2026.8.13.0024</td></tr></table>
        <div><span>0000002-22.2026.8.13.0024</span></div>"""
        with self.assertRaises(RuntimeError) as raised:
            push.parse_push_items(html)
        message = str(raised.exception)
        self.assertIn(
            "tables seen: id=f:processosCadastradosTable heads=['Processo', 'Data de inclusão'] rows=0", message
        )
        self.assertIn("id=f:other heads=['Número']", message)
        self.assertNotIn("0000001", message)
        # Truly empty: an empty grid and no number anywhere, or the portal's own message.
        self.assertEqual(push.parse_push_items(html.split('<table id="f:other">')[0]), [])
        empty = "Não há nenhum processo cadastrado na sua lista de processos do Push. 0000001-11.2026.8.13.0024"
        self.assertEqual(push.parse_push_items(f"<table><tr><th>Processo</th></tr></table><p>{empty}</p>"), [])

    def test_the_push_list_table_wins_over_another_grid_listing_processes(self):
        html = """<div id="f:relacionados"><table id="f:other"><tr><th>Processo</th><th>Data de inclusão</th>
        <th>Email alternativo</th><th>Observação</th></tr><tr><td>0000009-99.2026.8.08.0024</td><td></td><td></td><td></td></tr>
        </table></div><table id="f:processosCadastradosTable"><tr><th>Processo</th></tr>
        <tr><td>0000001-11.2026.8.08.0024</td></tr></table>"""
        self.assertEqual([i["process_number"] for i in push.parse_push_items(html)], ["0000001-11.2026.8.08.0024"])
        # Without the Push list's id, the full grid wins as before.
        unnamed = html.replace("f:processosCadastradosTable", "f:x")
        self.assertEqual([i["process_number"] for i in push.parse_push_items(unnamed)], ["0000009-99.2026.8.08.0024"])

    def test_an_ambiguous_grid_names_the_tables_seen_without_their_data(self):
        html = """<table id="a"><tr><th>Processo</th></tr><tr><td>0000001-11.2026.8.14.0301</td></tr></table>
        <table><tr><th>Processo</th><th>Classe</th></tr><tr><td>0000002-22.2026.8.14.0301</td></tr></table>"""
        with self.assertRaises(RuntimeError) as raised:
            push.parse_push_items(html)
        message = str(raised.exception)
        self.assertIn("id=a heads=['Processo'] rows=1", message)
        self.assertIn("id=- heads=['Processo', 'Classe'] rows=1", message)
        self.assertNotIn("0000001", message)
        with self.assertRaisesRegex(RuntimeError, r"tables seen: id=- heads=\['Número'\]"):
            push.parse_push_items("<table><tr><th>Número</th></tr><tr><td>0000001-11.2026.8.14.0301</td></tr></table>")

    def test_trf1_push_grid_has_only_the_process_column_and_keeps_repeated_registrations(self):
        html = """<table id='x'><tr><th></th><th>Processo</th></tr>
        <tr><td><a onclick="construirLogicaAcesso(426331);">go</a></td><td>1003236-66.2019.4.01.3312</td></tr>
        <tr><td></td><td>1005375-88.2019.4.01.3312</td></tr>
        <tr><td></td><td>1005375-88.2019.4.01.3312</td></tr></table>
        <p>3 resultados encontrados</p>"""
        items = push.parse_push_items(html)
        self.assertEqual([item["process_number"] for item in items][-2:], ["1005375-88.2019.4.01.3312"] * 2)
        self.assertEqual((items[0]["process_id"], items[0]["registered_at"]), (426331, ""))
        self.assertTrue(push.parse_push_page(html, len(items))["complete"])

    def test_push_reader_reuses_supplied_session_without_closing_it(self):
        push_url = "https://pje1g.trf1.jus.br/pje/Push/listView.seam?iframe=true"
        session = Mock()
        session.get.return_value = SimpleNamespace(
            status_code=200,
            url=push_url,
            content=b"<html></html>",
            headers={"Content-Type": "text/html"},
        )
        with patch.object(push, "login_portal") as login:
            result = push.read_push("trf1-1g", session=session)

        self.assertTrue(result["success"])
        login.assert_not_called()
        session.close.assert_not_called()

    def test_trf5_push_uses_certificate_login_route(self):
        session = Mock()
        session.get.return_value = SimpleNamespace(
            status_code=200,
            url="https://pje1g.trf5.jus.br/pje/Push/listView.seam?iframe=true",
            content=b"<html></html>",
            headers={"Content-Type": "text/html"},
        )
        with patch.object(push, "login_portal", return_value=(session, None)) as login:
            result = push.read_push("trf5-1g")
        login.assert_called_once_with("trf5-1g")
        self.assertTrue(result["success"])
        session.close.assert_called_once()

    def test_a_portal_that_refuses_the_push_page_says_so(self):
        # TRF6 PJe, 25/11/2026: Push/listView.seam answers 302 to error.seam, "Sem permissão para acessar a página."
        # On 26/11 the lawyer's menu had no Push entry at all.
        refused = "<html><div>Sem permissão para acessar a página.</div></html>"
        for location, html, message in (
            ("/pje/error.seam?cid=1", refused, "does not let this account open the Push page"),
            ("/pje/error.seam?cid=1", "<html>Erro inesperado</html>", "Push list returned HTTP 302"),
            ("/pje/login.seam", refused, "Push list returned HTTP 302"),
            ("https://other.example/pje/error.seam", refused, "Push list returned HTTP 302"),
        ):
            with self.subTest(location=location, html=html):
                session = Mock()
                session.get.side_effect = [
                    SimpleNamespace(status_code=302, url="x", content=b"", headers={"Location": location}),
                    SimpleNamespace(
                        status_code=200, url="x", content=html.encode(), headers={"Content-Type": "text/html"}
                    ),
                ]
                with self.assertRaisesRegex(RuntimeError, message):
                    push.read_push("trf6-pje-1g", session=session)
                self.assertEqual(session.get.call_count, 2 if "/pje/error.seam" in location[:15] else 1)

    def test_push_process_pattern_and_row_parsing(self):
        assert push.PROCESS_RE.findall("1016572-38.2026.4.01.0000") == ["1016572-38.2026.4.01.0000"]
        push_sample = """
        <table><tr><th></th><th>Processo</th><th>Data de inclusão</th><th>Email alternativo</th><th>Observação</th></tr><tr class='rich-table-row'>
          <td></td><td id='j_id217:j_id221:0:j_id226'>1016572-38.2026.4.01.0000</td>
          <td>03/09/2026 10:00</td><td></td><td>teste</td>
        </tr></table>
        """
        assert push.parse_push_items(push_sample)[0]["process_number"] == "1016572-38.2026.4.01.0000"
        assert push.parse_push_page("<div>2 resultados encontrados</div><button disabled>Próximo</button>", 2)[
            "complete"
        ]

    def test_tjba_2g_incident_numbers_are_listed_under_their_process_number(self):
        html = """<table><tr><th></th><th>Processo</th></tr>
        <tr><td></td><td>8003528-10.2021.8.05.0080R2.2.EDCiv</td></tr>
        <tr><td></td><td>8005133-08.2019.8.05.0000R1.1.AgIntCiv</td></tr>
        <tr><td></td><td>8019223-67.2022.8.05.0080</td></tr></table>"""
        items = push.parse_push_items(html)
        self.assertEqual(
            [(i["process_number"], i.get("incident")) for i in items],
            [
                ("8003528-10.2021.8.05.0080", "R2.2.EDCiv"),
                ("8005133-08.2019.8.05.0000", "R1.1.AgIntCiv"),
                ("8019223-67.2022.8.05.0080", None),
            ],
        )

    def test_tjrj_grid_with_the_paginator_nested_inside_is_read(self):
        from pathlib import Path

        html = (Path(__file__).parent / "fixtures" / "push_tjrj.html").read_text()
        items = push.parse_push_items(html)
        self.assertEqual(
            [(i["process_number"], i["process_id"], i["registered_at"]) for i in items],
            [
                ("0000001-11.2026.8.19.0001", 1000001, "10/12/2025 08:10"),
                ("0000002-22.2026.8.19.0001", 1000002, "21/10/2026 13:56"),
                ("0000003-33.2026.8.19.0001", 1000003, "01/10/2026 09:00"),
            ],
        )
        self.assertEqual(items[1]["observation"], "obs teste")
        page = push.parse_push_page(html, len(items))
        # "10/12/2025" is an inclusion date, not "page 10 of 12".
        self.assertEqual((page["current_page"], page["total_pages"], page["has_next_page"]), (None, None, None))
        self.assertEqual((page["results_count"], page["complete"]), (3, True))

    def test_datascroller_pages_say_there_is_a_next_page(self):
        html = """<table id="grid"><thead><tr><th>Processo</th></tr></thead>
        <tfoot><tr><td><table class="rich-dtascroller-table"><tr><td class="rich-datascr-act">1</td>
        <td class="rich-datascr-inact">2</td><td class="rich-datascr-inact">3</td></tr></table>
        <span>45 resultados encontrados</span></td></tr></tfoot>
        <tbody><tr><td>0000001-11.2026.8.14.0001</td></tr><tr><td>0000002-22.2026.8.14.0001</td></tr></tbody></table>"""
        items = push.parse_push_items(html)
        self.assertEqual(len(items), 2)
        page = push.parse_push_page(html, len(items))
        self.assertEqual((page["current_page"], page["total_pages"], page["has_next_page"]), (1, 3, True))
        self.assertFalse(page["complete"])
        explicit = push.parse_push_page("<p>Página 2 de 4</p><p>1 resultados encontrados</p>", 1)
        self.assertEqual((explicit["current_page"], explicit["total_pages"]), (2, 4))


def _scroller_page(numbers, page, pages, *, related=""):
    """A TJMG-like Push page: the registry grid with its own RichFaces datascroller, page ``page`` of ``pages``."""
    buttons = "".join(
        f'<td class="rich-datascr-{"act" if n == page else "inact"}">{n}</td>' for n in range(1, pages + 1)
    )
    rows = "".join(f"<tr><td>{number}</td><td>01/10/2026 09:00</td></tr>" for number in numbers)
    return f"""<html><body><form id="j_id192" action="/pje/Push/listView.seam">
    <input type="hidden" name="javax.faces.ViewState" value="state{page}"/>
    <table id="j_id192:processosCadastradosTable"><thead><tr><th>Processo</th><th>Data de inclusão</th></tr></thead>
    <tfoot><tr><td><table class="rich-dtascroller-table"><tr>{buttons}</tr></table></td></tr></tfoot>
    <tbody>{rows}</tbody></table>
    <script>new Richfaces.Datascroller('j_id192:processosCadastradosTable:scroller', function(event){{
    A4J.AJAX.Submit('j_id192',event,{{'similarityGroupingId':'j_id192:processosCadastradosTable:scroller',
    'parameters':{{'j_id192:processosCadastradosTable:scroller':event.memo.page,
    'ajaxSingle':'j_id192:processosCadastradosTable:scroller'}} }}); return false;}});</script>
    {related}</form></body></html>"""


def _response(html, url="https://pje.tjmg.jus.br/pje/Push/listView.seam"):
    return SimpleNamespace(status_code=200, url=url, content=html.encode(), headers={"Content-Type": "text/html"})


RELATED = """<td id="j_id192:processosRelacionadosTab"><div id="j_id192:divProcessosRelacionados">
<table id="j_id192:dataTableProcessosRelacionados"><thead><tr><th></th><th>Processo</th></tr></thead><tbody>
<tr><td></td><td>5000009-99.2025.8.13.0261</td></tr></tbody></table>
<div class="rich-datascr"><table><tr><td class="rich-datascr-act">1</td><td class="rich-datascr-inact">2</td>
</tr></table></div></div>
<script>new Richfaces.Datascroller('j_id192:dataTableProcessosRelacionados:scrollerListaProcessosRelacionados',
function(event){A4J.AJAX.Submit('j_id192',event,{}); return false;});</script></td>"""


class PushRegistryPages(unittest.TestCase):
    def _numbers(self, start, count):
        return [f"{n:07d}-11.2026.8.13.0024" for n in range(start, start + count)]

    def _read(self, pages, **options):
        session = Mock()
        session.get.return_value = _response(pages[0])
        session.post.side_effect = [_response(page) for page in pages[1:]]
        return push.read_push("tjmg-pje-1g", session=session, **options), session

    def test_the_related_processes_tab_is_not_the_registry(self):
        # TJMG TR: an empty registry beside 4 pages of "Processos relacionados" was read as 20 registrations.
        html = (
            '<form id="j_id192"><td id="j_id192:processosCadastradosTab">Não há nenhum processo cadastrado na sua '
            f"lista de processos do Push.</td>{RELATED}</form>"
        )
        self.assertEqual(push.parse_push_items(html), [])
        page = push.parse_push_page(html, 0)
        self.assertEqual((page["results_count"], page["total_pages"], page["complete"]), (0, None, True))
        result, session = self._read([html])
        self.assertEqual((result["total"], result["hasMore"], result["complete"]), (0, False, True))
        self.assertIn("Processos relacionados", result["related_note"])
        session.post.assert_not_called()

    def test_every_page_of_the_registry_is_read_up_to_the_limit(self):
        pages = [
            _scroller_page(self._numbers(1, 20), 1, 3, related=RELATED),
            _scroller_page(self._numbers(21, 20), 2, 3),
            _scroller_page(self._numbers(41, 5), 3, 3),
        ]
        result, session = self._read(pages, limit=500)
        self.assertEqual((result["total"], result["hasMore"], result["complete"]), (45, False, True))
        self.assertEqual((result["current_page"], result["total_pages"]), (3, 3))
        self.assertEqual(len({item["process_number"] for item in result["items"]}), 45)
        # The page turn is the datascroller's own A4J submit, carrying the latest ViewState.
        bodies = [call.kwargs["data"] for call in session.post.call_args_list]
        self.assertIn("j_id192%3AprocessosCadastradosTable%3Ascroller=2", bodies[0])
        self.assertIn("javax.faces.ViewState=state2", bodies[1])
        self.assertNotIn("Relacionados", bodies[0])

    def test_a_window_reads_only_the_pages_it_needs_and_says_more_remain(self):
        pages = [_scroller_page(self._numbers(1, 20), 1, 3), _scroller_page(self._numbers(21, 20), 2, 3)]
        result, session = self._read(pages, limit=10, offset=15)
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(result["rows_returned"], 10)
        # No count on the page and pages left: the total is unknown, not the rows read so far.
        self.assertEqual((result["total"], result["hasMore"], result["complete"]), (None, True, False))
        self.assertIsNone(result["total_pages"])

    def test_a_page_that_does_not_turn_is_an_error_not_a_short_list(self):
        pages = [_scroller_page(self._numbers(1, 20), 1, 3), _scroller_page(self._numbers(1, 20), 1, 3)]
        with self.assertRaisesRegex(RuntimeError, "did not turn to page 2"):
            self._read(pages, limit=500)

    def test_a_walk_by_offset_continues_where_the_last_call_stopped(self):
        pages = [_scroller_page(self._numbers(1 + 20 * n, 20), n + 1, 3) for n in range(3)]
        session = Mock()
        session.get.return_value = _response(pages[0])
        session.post.side_effect = [_response(page) for page in pages[1:]]
        windows = [push.read_push("tjmg-pje-1g", session=session, limit=20, offset=offset) for offset in (0, 20, 40)]
        # Page 1 once, then one page turn per call, each from the ViewState the previous turn left.
        self.assertEqual((session.get.call_count, session.post.call_count), (1, 2))
        bodies = [call.kwargs["data"] for call in session.post.call_args_list]
        self.assertIn("scroller=2", bodies[0])
        self.assertIn("scroller=3", bodies[1])
        self.assertIn("javax.faces.ViewState=state2", bodies[1])
        self.assertEqual([w["items"][0]["process_number"] for w in windows], self._numbers(1, 41)[::20])
        self.assertEqual([w["hasMore"] for w in windows], [True, True, False])
        self.assertEqual((windows[2]["complete"], windows[2]["total_pages"]), (True, 3))
        # The whole registry is read: a process filter and a window already read need no request.
        wanted = self._numbers(33, 1)[0]
        found = push.read_push("tjmg-pje-1g", session=session, process_number=wanted)
        again = push.read_push("tjmg-pje-1g", session=session, limit=5, offset=10)
        self.assertEqual((session.get.call_count, session.post.call_count), (1, 2))
        self.assertEqual(([item["process_number"] for item in found["items"]], found["total"]), ([wanted], 1))
        self.assertEqual(again["items"][0]["process_number"], self._numbers(11, 1)[0])
        # Callers get their own copy: changing a returned row leaves the remembered one alone.
        again["items"][0]["process_number"] = "changed"
        self.assertNotEqual(push.read_push("tjmg-pje-1g", session=session, limit=1, offset=10)["items"], again["items"])

    def test_a_continuation_the_portal_refuses_starts_over_and_old_rows_expire(self):
        pages = [_scroller_page(self._numbers(1 + 20 * n, 20), n + 1, 3) for n in range(3)]
        session = Mock()
        session.get.return_value = _response(pages[0])
        # The second call's turn answers page 1 again (the ViewState expired); the restart turns normally.
        session.post.side_effect = [_response(pages[0]), _response(pages[1])]
        push.read_push("tjmg-pje-1g", session=session, limit=20)
        result = push.read_push("tjmg-pje-1g", session=session, limit=20, offset=20)
        self.assertEqual((session.get.call_count, session.post.call_count), (2, 2))
        self.assertEqual(result["items"][0]["process_number"], self._numbers(21, 1)[0])
        session.post.side_effect = [_response(pages[1])]
        with patch.object(push.time, "monotonic", return_value=push.time.monotonic() + push.WALK_SECONDS + 1):
            push.read_push("tjmg-pje-1g", session=session, limit=20, offset=20)
        self.assertEqual((session.get.call_count, session.post.call_count), (3, 3))

    def test_reusing_the_rows_never_extends_their_lifetime(self):
        session = Mock()
        session.get.return_value = _response(_scroller_page(self._numbers(1, 20), 1, 1))
        clock = [1000.0]
        with patch.object(push.time, "monotonic", lambda: clock[0]):
            push.read_push("tjmg-pje-1g", session=session, limit=20)
            for _ in range(3):
                clock[0] += push.WALK_SECONDS / 2
                push.read_push("tjmg-pje-1g", session=session, limit=20)
        # Read at 1000, reused at 1150 and 1300, read again at 1450 and reused at 1600 (as a fresh snapshot).
        self.assertEqual(session.get.call_count, 2)

    def test_a_failed_read_forgets_the_walk(self):
        pages = [_scroller_page(self._numbers(1 + 20 * n, 20), n + 1, 3) for n in range(3)]
        session = Mock()
        session.get.return_value = _response(pages[0])
        push.read_push("tjmg-pje-1g", session=session, limit=20)
        # The continuation answers page 1 again, and so does the restart: an error, and nothing is kept.
        session.post.side_effect = [_response(pages[0]), _response(pages[0])]
        with self.assertRaisesRegex(RuntimeError, "did not turn to page 2"):
            push.read_push("tjmg-pje-1g", session=session, limit=20, offset=20)
        self.assertIsNone(push._recall(session, "tjmg-pje-1g"))


if __name__ == "__main__":
    unittest.main()
