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


if __name__ == "__main__":
    unittest.main()
