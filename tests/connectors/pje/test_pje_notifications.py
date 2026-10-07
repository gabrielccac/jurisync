import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jurisync.connectors.pje import notifications


class PjeNotificationChecks(unittest.TestCase):
    def test_push_pagination_ignores_impossible_page_text_and_incomplete_counts(self):
        result = notifications.parse_push_page(
            "<div>160 resultados encontrados</div><p>referência 7 de 2</p>",
            rows_returned=162,
        )
        self.assertIsNone(result["current_page"])
        self.assertIsNone(result["total_pages"])
        self.assertFalse(result["complete"])

    def test_push_rows_are_scoped_to_the_watched_process_grid(self):
        html = """<table><tr><th>Processo</th><th>Data de inclusão</th><th>Email alternativo</th><th>Observação</th></tr><tr><td></td><td>1016572-38.2026.4.01.0000</td><td>today</td><td></td><td></td></tr></table><table><tr><th>Processo</th></tr><tr><td>0006826-91.2011.4.01.0000</td></tr></table>"""
        self.assertEqual(
            [item["process_number"] for item in notifications.parse_push_items(html)], ["1016572-38.2026.4.01.0000"]
        )

    def test_push_reader_reuses_supplied_session_without_closing_it(self):
        push_url = "https://pje1g.trf1.jus.br/pje/Push/listView.seam?iframe=true"
        session = Mock()
        session.get.return_value = SimpleNamespace(
            status_code=200,
            url=push_url,
            content=b"<html></html>",
            headers={"Content-Type": "text/html"},
        )
        with patch.object(notifications, "login_portal") as login:
            result = notifications.read_push("trf1-1g", session=session)

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
        with patch.object(notifications, "login_portal", return_value=(session, None)) as login:
            result = notifications.read_push("trf5-1g")
        login.assert_called_once_with("trf5-1g")
        self.assertTrue(result["success"])
        session.close.assert_called_once()

    def test_ambiguous_child_control_fails_closed(self):
        html = """
        <form id='formAbaExpediente'>
          <button id='formAbaExpediente:listaAgrSitExp:0:first:jNp'
                  onclick='A4J.AJAX.Submit()'>Abrir</button>
          <button id='formAbaExpediente:listaAgrSitExp:0:second:jNp'
                  onclick='A4J.AJAX.Submit()'>Abrir</button>
        </form>
        """
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            notifications.discover_child_control(html, 0)

    def test_submit_requires_refreshed_view_state(self):
        session = Mock()
        session.post.return_value = SimpleNamespace(
            status_code=200,
            url="https://pje1g.trf1.jus.br/pje/Painel/painel_usuario/advogado.seam",
            content=b"<partial-response><changes><update id='tree'><![CDATA[x]]></update></changes></partial-response>",
        )
        form = "<form id='formAbaExpediente'><input name='javax.faces.ViewState' value='old'></form>"
        with self.assertRaisesRegex(RuntimeError, "refreshed JSF ViewState"):
            notifications.submit_control(
                session,
                "https://pje1g.trf1.jus.br/pje/Painel/painel_usuario/advogado.seam",
                form,
                "formAbaExpediente:listaAgrSitExp:0:child",
            )
        request = session.post.call_args.kwargs
        self.assertFalse(request["allow_redirects"])
        self.assertEqual(request["timeout"], notifications.REQUEST_TIMEOUT)

    def test_submit_accepts_richfaces_full_html_fragment_with_new_state(self):
        panel_url = "https://pje1g.trf1.jus.br/pje/Painel/painel_usuario/advogado.seam"
        session = Mock()
        session.post.return_value = SimpleNamespace(
            status_code=200,
            url=panel_url,
            content=b"<html><form id='other'><input name='javax.faces.ViewState' value='new'></form><span id='formAbaExpediente:listaAgrSitExp:0:tree:jNp'></span></html>",
        )
        original = "<form id='formAbaExpediente'><input name='javax.faces.ViewState' value='old'></form>"
        result = notifications.submit_control(session, panel_url, original, "formAbaExpediente:listaAgrSitExp:0:expand")
        self.assertIn('value="new"', result)
        self.assertIn("tree:jNp", result)

    def test_expansion_carries_latest_form_between_buckets(self):
        panel_url = "https://pje1g.trf1.jus.br/pje/Painel/painel_usuario/advogado.seam"
        panel_html = """
        <form id='formAbaExpediente'>
          <input name='javax.faces.ViewState' value='initial'>
          <div id='formAbaExpediente:listaAgrSitExp:0:linhaN1'><span class='nomeTarefa'>One</span><span class='pull-right'>1</span></div>
          <div id='formAbaExpediente:listaAgrSitExp:1:linhaN1'><span class='nomeTarefa'>Two</span><span class='pull-right'>1</span></div>
        </form>
        """
        session = Mock()
        session.get.return_value = SimpleNamespace(
            status_code=200,
            url=panel_url,
            content=panel_html.encode(),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
        login_response = SimpleNamespace(url=panel_url)
        with (
            patch.object(notifications, "login_portal", return_value=(session, login_response)),
            patch.object(notifications, "expand_bucket", side_effect=["state-two", "state-three"]) as expand,
            patch.object(notifications, "parse_expanded_items", return_value=[]),
        ):
            result = notifications.read_portal("trf1-1g", expand=True)

        self.assertTrue(result["success"])
        self.assertEqual(expand.call_args_list[0].args[2], panel_html)
        self.assertEqual(expand.call_args_list[1].args[2], "state-two")
        session.close.assert_called_once_with()

    def test_bucket_partial_and_push_parsing(self):
        sample = """
        <form id='formAbaExpediente'>
          <div id='formAbaExpediente:listaAgrSitExp:0:linhaN1'>
            <span class='nomeTarefa'>Apenas pendentes de ciência</span><span class='pull-right'>3</span>
          </div>
        </form>
        """
        assert notifications.parse_buckets(sample) == [{"index": 0, "label": "Apenas pendentes de ciência", "count": 3}]
        assert notifications.PROCESS_RE.findall("1016572-38.2026.4.01.0000") == ["1016572-38.2026.4.01.0000"]
        assert (
            len(
                notifications.parse_expanded_items(
                    "<table id='formExpedientes:tbExpedientes'><tr><td>Intimação 1016572-38.2026.4.01.0000</td></tr></table>",
                    0,
                    "https://pje1g.trf1.jus.br",
                )
            )
            == 1
        )
        partial = """
        <partial-response><changes>
          <update id='javax.faces.ViewState'><![CDATA[state-2]]></update>
          <update id='formAbaExpediente:listaAgrSitExp:0:tree'><![CDATA[
            <button id='formAbaExpediente:listaAgrSitExp:0:next' onclick='A4J.AJAX.Submit()'>Abrir</button>
          ]]></update>
        </changes></partial-response>
        """
        state_html = """<form id='formAbaExpediente'><input name='javax.faces.ViewState' value='state-1'><div id='formAbaExpediente:listaAgrSitExp:0:tree'></div></form>"""
        assert notifications.parse_partial_response(partial)["view_state"] == "state-2"
        advanced = notifications.apply_partial_updates(state_html, partial)
        assert 'value="state-2"' in advanced and "formAbaExpediente:listaAgrSitExp:0:next" in advanced
        with self.assertRaises(RuntimeError):
            notifications.parse_expanded_items(
                "<table id='formExpedientes:tbExpedientes'><tr><td>1024853-70.2023.4.01.3400 <a href='https://evil.example/x'>x</a></td></tr></table>",
                0,
                "https://pje1g.trf1.jus.br",
            )
        push_sample = """
        <table><tr><th></th><th>Processo</th><th>Data de inclusão</th><th>Email alternativo</th><th>Observação</th></tr><tr class='rich-table-row'>
          <td></td><td id='j_id217:j_id221:0:j_id226'>1016572-38.2026.4.01.0000</td>
          <td>03/09/2026 10:00</td><td></td><td>teste</td>
        </tr></table>
        """
        assert notifications.parse_push_items(push_sample)[0]["process_number"] == "1016572-38.2026.4.01.0000"
        assert notifications.parse_push_page("<div>2 resultados encontrados</div><button disabled>Próximo</button>", 2)[
            "complete"
        ]


if __name__ == "__main__":
    unittest.main()
