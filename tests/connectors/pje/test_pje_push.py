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

    def test_push_rows_are_scoped_to_the_watched_process_grid(self):
        html = """<table><tr><th>Processo</th><th>Data de inclusão</th><th>Email alternativo</th><th>Observação</th></tr><tr><td></td><td>1016572-38.2026.4.01.0000</td><td>today</td><td></td><td></td></tr></table><table><tr><th>Processo</th></tr><tr><td>0006826-91.2011.4.01.0000</td></tr></table>"""
        self.assertEqual(
            [item["process_number"] for item in push.parse_push_items(html)], ["1016572-38.2026.4.01.0000"]
        )

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


if __name__ == "__main__":
    unittest.main()
