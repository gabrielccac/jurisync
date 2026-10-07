import unittest
from unittest.mock import patch

from jurisync.connectors.eproc import client

NUMBER = "0000001-23.2026.4.04.7207"
BASE = "https://eproc.jfsc.jus.br/eprocV2/"


class Response:
    def __init__(self, status=200, body="", url=BASE, headers=None):
        self.status_code = status
        self.content = body.encode("iso-8859-1")
        self.url = url
        self.headers = headers or {}


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)

    def close(self):
        self.closed = True


class EprocClientTests(unittest.TestCase):
    def test_require_route_rejects_untrusted_routes(self):
        for url in (
            "http://eproc.jfsc.jus.br/eprocV2/controlador.php?acao=painel_adv_listar",
            "https://other.example/eprocV2/controlador.php?acao=painel_adv_listar",
            "https://user:pass@eproc.jfsc.jus.br/eprocV2/controlador.php?acao=painel_adv_listar",
            "https://eproc.jfsc.jus.br/eprocV2/controlador.php?acao=exportar",
        ):
            with self.assertRaises(RuntimeError):
                client.require_route(url, portal="jfsc")

    def test_fetch_process_follows_only_same_host_search_redirect(self):
        session = Session(
            [
                Response(
                    302,
                    url=BASE + "controlador.php?acao=processo_pesquisa_rapida",
                    headers={"Location": "controlador.php?acao=processo_selecionar&hash=opaque"},
                ),
                Response(200, "<html>detail</html>"),
                Response(200, "<html>events</html>"),
            ]
        )
        with (
            patch.object(
                client,
                "parse_panel",
                return_value={
                    "quickSearch": {
                        "action": "controlador.php?acao=processo_pesquisa_rapida&hash=opaque",
                        "method": "post",
                        "numberField": "txtNumProcessoPesquisaRapida",
                        "submitField": "btnPesquisaRapidaSubmit",
                        "submitValue": None,
                        "returnField": "acao_retorno_pesquisa_rapida",
                        "returnValue": "painel_adv_listar",
                    }
                },
            ),
            patch.object(
                client,
                "parse_process_detail",
                return_value={
                    "numero": NUMBER,
                    "partes": [{"nome": "Parte"}],
                    "eventos": [],
                    "documentos": [],
                    "hash": "secret",
                    "eventRequest": {
                        "url": "controlador_ajax.php?acao_ajax=montar_tabela_eventos_filtrados&hash=opaque",
                        "data": {"pagina": "0"},
                    },
                },
            ),
            patch.object(
                client, "parse_event_response", return_value={"eventos": [], "pagination": {"complete": True}}
            ),
        ):
            result = client.fetch_process(NUMBER, session=session, panel_html="", panel_url=BASE, portal="jfsc")
        self.assertTrue(result["success"])
        self.assertEqual(result["processNumber"], NUMBER)
        self.assertNotIn("hash", str(result))
        self.assertNotIn("url", str(result))
        self.assertEqual(session.calls[0][2]["data"]["btnPesquisaRapidaSubmit"], "")
        self.assertEqual([call[0] for call in session.calls], ["POST", "GET", "POST"])

    def test_search_form_is_replayed_and_process_redirect_action_is_required(self):
        session = Session(
            [Response(302, url=BASE, headers={"Location": "controlador.php?acao=painel_adv_listar&hash=opaque"})]
        )
        panel = {
            "quickSearch": {
                "action": "controlador.php?acao=processo_pesquisa_rapida&hash=opaque",
                "method": "post",
                "numberField": "live_number",
                "submitField": "live_submit",
                "submitValue": "Pesquisar",
                "returnField": "live_return",
                "returnValue": "live_target",
            }
        }
        with patch.object(client, "parse_panel", return_value=panel), self.assertRaises(RuntimeError):
            client.fetch_process(NUMBER, session=session, panel_html="", panel_url=BASE, portal="jfsc")
        self.assertEqual(
            session.calls[0][2]["data"],
            {"live_number": NUMBER, "live_submit": "Pesquisar", "live_return": "live_target"},
        )

    def test_http_200_login_body_is_session_expiry(self):
        session = Session([Response(200, '<form id="login"><input name="username"></form>')])
        with patch.object(
            client,
            "parse_panel",
            return_value={"queues": {"pending": "controlador.php?acao=citacao_intimacao_pendente_listar&hash=x"}},
        ):
            result = client.list_notices("pending", session=session, panel_html="", panel_url=BASE, portal="jfsc")
        self.assertFalse(result["success"])
        self.assertEqual(result["diagnostics"], ["session_expired"])

    def test_process_number_mismatch_is_not_success(self):
        session = Session(
            [
                Response(302, url=BASE, headers={"Location": "controlador.php?acao=processo_selecionar&hash=x"}),
                Response(200, "detail"),
            ]
        )
        panel = {
            "quickSearch": {
                "action": "controlador.php?acao=processo_pesquisa_rapida&hash=x",
                "method": "post",
                "numberField": "n",
                "submitField": "s",
                "submitValue": "",
                "returnField": "r",
                "returnValue": "p",
            }
        }
        with (
            patch.object(client, "parse_panel", return_value=panel),
            patch.object(
                client,
                "parse_process_detail",
                return_value={"numero": "0000002-23.2026.4.04.7207", "eventos": [], "documentos": []},
            ),
        ):
            result = client.fetch_process(NUMBER, session=session, panel_html="", panel_url=BASE, portal="jfsc")
        self.assertFalse(result["success"])
        self.assertEqual(result["diagnostics"], ["process_not_found"])

    def test_queue_uses_parser_pagination_and_does_not_claim_complete(self):
        session = Session([Response(200, "queue")])
        with (
            patch.object(
                client,
                "parse_panel",
                return_value={"queues": {"pending": "controlador.php?acao=citacao_intimacao_pendente_listar&hash=x"}},
            ),
            patch.object(
                client,
                "parse_process_list_page",
                return_value={
                    "items": [{"numero": NUMBER, "url": "https://eproc.jfsc.jus.br/private?hash=x"}],
                    "pagination": {"page": 0, "totalItems": 8, "itemCount": 1, "complete": False},
                },
            ),
        ):
            result = client.list_notices("pending", session=session, panel_html="", panel_url=BASE, portal="jfsc")
        self.assertFalse(result["pagination"]["complete"])
        self.assertNotIn("url", str(result))

    def test_event_ajax_pages_are_bounded_combined_and_reported(self):
        event_url = "controlador_ajax.php?acao_ajax=montar_tabela_eventos_filtrados&hash=e"
        session = Session(
            [
                Response(302, url=BASE, headers={"Location": "controlador.php?acao=processo_selecionar&hash=p"}),
                Response(200, "detail"),
                Response(200, "events0"),
                Response(200, "events1"),
            ]
        )
        panel = {
            "quickSearch": {
                "action": "controlador.php?acao=processo_pesquisa_rapida&hash=q",
                "method": "post",
                "numberField": "n",
                "submitField": "s",
                "submitValue": "",
                "returnField": "r",
                "returnValue": "p",
            }
        }
        detail = {
            "numero": NUMBER,
            "partes": [],
            "documentos": [],
            "eventos": [{"id": "1", "descricao": "detail"}],
            "eventRequest": {"url": event_url, "data": {"filtro": "on"}},
        }
        parsed = [
            {
                "eventos": [{"id": "1", "descricao": "ajax"}],
                "pagination": {"url": event_url, "totalPages": 2, "complete": False},
            },
            {
                "eventos": [{"id": "2", "descricao": "page 1", "documentos": [{"id": "12"}]}],
                "pagination": {"url": event_url, "totalPages": 2, "complete": True},
            },
        ]
        with (
            patch.object(client, "parse_panel", return_value=panel),
            patch.object(client, "parse_process_detail", return_value=detail),
            patch.object(client, "parse_event_response", side_effect=parsed),
        ):
            result = client.fetch_process(NUMBER, session=session, panel_html="", panel_url=BASE, portal="jfsc")
        self.assertEqual([item["id"] for item in result["timeline"]], ["1", "2"])
        self.assertTrue(result["pagination"]["complete"])
        self.assertEqual(result["pagination"]["loadedPages"], 2)
        self.assertEqual(result["documentInventory"], [{"id": "12"}])
        self.assertNotIn("pagina", session.calls[2][2]["data"])
        self.assertEqual(session.calls[3][2]["data"]["pagina"], "1")
        self.assertTrue(all("controlador_ajax.php" in call[1] for call in session.calls[2:]))

    def test_document_tokens_are_not_exposed(self):
        session = Session(
            [
                Response(302, url=BASE, headers={"Location": "controlador.php?acao=processo_selecionar&hash=p"}),
                Response(200, "detail"),
                Response(200, "events"),
            ]
        )
        panel = {
            "quickSearch": {
                "action": "controlador.php?acao=processo_pesquisa_rapida&hash=q",
                "method": "post",
                "numberField": "n",
                "submitField": "s",
                "submitValue": "",
                "returnField": "r",
                "returnValue": "p",
            }
        }
        detail = {
            "numero": NUMBER,
            "partes": [],
            "eventos": [],
            "documentos": [
                {
                    "id": "9",
                    "key": "secret",
                    "evento": "7",
                    "mesmoGrau": "S",
                    "hash": "h",
                    "url": "https://eproc.jfsc.jus.br/eprocV2/controlador.php?acao=acessar_documento&hash=h",
                }
            ],
            "eventRequest": None,
        }
        with (
            patch.object(client, "parse_panel", return_value=panel),
            patch.object(client, "parse_process_detail", return_value=detail),
            patch.object(client, "read_selected_documents", return_value=[]),
        ):
            result = client.fetch_process(NUMBER, session=session, panel_html="", panel_url=BASE, portal="jfsc")
        self.assertEqual(result["documentInventory"], [{"id": "9"}])

    def test_http_200_single_quote_login_form_is_session_expiry(self):
        session = Session([Response(200, "<form><input name='username'></form>")])
        with patch.object(
            client,
            "parse_panel",
            return_value={"queues": {"pending": "controlador.php?acao=citacao_intimacao_pendente_listar&hash=x"}},
        ):
            result = client.list_notices("pending", session=session, panel_html="", panel_url=BASE, portal="jfsc")
        self.assertEqual(result["diagnostics"], ["session_expired"])

    def test_require_route_accepts_ajax_allowlist_only(self):
        url = BASE + "controlador_ajax.php?acao_ajax=montar_tabela_eventos_filtrados&hash=x"
        self.assertEqual(client.require_route(url, client.AJAX_ACTIONS, ajax=True, portal="jfsc"), url)
        with self.assertRaises(RuntimeError):
            client.require_route(
                url.replace("montar_tabela_eventos_filtrados", "delete"), client.AJAX_ACTIONS, ajax=True, portal="jfsc"
            )


if __name__ == "__main__":
    unittest.main()
