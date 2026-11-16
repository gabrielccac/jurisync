"""Eproc state-court quirks seen in live checks: TNU landing page, TJSP 2G empty report, TJTO image CAPTCHA."""

import unittest
from unittest.mock import Mock, patch

from selenium.common.exceptions import UnexpectedAlertPresentException

from jurisync.connectors.eproc import auth
from jurisync.connectors.eproc.parsers import parse_lawyer_process_report

BASE = "https://eproc.example.jus.br/eproc/"

# TNU lands on a contact-data notice; the lawyer panel is only reachable from the menu.
TNU_LANDING = """<html><body><a href="controlador.php?acao=painel_advogado_listar&amp;hash=opaque">Painel do Advogado</a>
<a href="controlador.php?acao=sair&amp;hash=opaque">Sair</a></body></html>"""

# TJSP 2G: the report is only the filter form plus this message when the lawyer has no process there.
TJSP_EMPTY_REPORT = """<html><body><form id="frmProcessoLista" method="post">
<select name="selStatusProcesso"><option selected>Todos</option></select>
<input type="checkbox" name="chkMostrarBaixados" value="S">
<div>Nenhum registro encontrado.</div><button type="button">Buscar</button></form></body></html>"""


class PanelLinkTests(unittest.TestCase):
    def test_panel_link_finds_either_panel_route(self):
        link = auth.panel_link(TNU_LANDING, BASE + "controlador.php?acao=pessoa_alterar")
        self.assertEqual(link, BASE + "controlador.php?acao=painel_advogado_listar&hash=opaque")
        legacy = '<a href="controlador.php?acao=painel_adv_listar&hash=x">p</a>'
        self.assertIn("painel_adv_listar", auth.panel_link(legacy, BASE))
        self.assertIsNone(auth.panel_link("<a href='controlador.php?acao=sair'>x</a>", BASE))

    def test_is_panel_accepts_both_names(self):
        self.assertTrue(auth.is_panel(BASE + "controlador.php?acao=painel_advogado_listar"))
        self.assertTrue(auth.is_panel(BASE + "controlador.php?acao=painel_adv_listar"))
        self.assertFalse(auth.is_panel(BASE + "controlador.php?acao=pessoa_alterar"))

    def test_open_panel_navigates_to_the_panel_link_without_submitting(self):
        driver = Mock(page_source=TNU_LANDING)
        driver.get_current_url.return_value = BASE + "controlador.php?acao=pessoa_alterar"
        with patch.object(auth, "wait_for_step", return_value="panel") as wait:
            self.assertEqual(auth.open_panel(driver), "panel")
        driver.get.assert_called_once_with(BASE + "controlador.php?acao=painel_advogado_listar&hash=opaque")
        wait.assert_called_once_with(driver)
        driver.click.assert_not_called()

    def test_open_panel_fails_clearly_without_a_link(self):
        driver = Mock(page_source="<html></html>")
        driver.get_current_url.return_value = BASE + "controlador.php"
        with self.assertRaisesRegex(RuntimeError, "no panel link"):
            auth.open_panel(driver)

    def test_wait_for_step_reports_home_for_a_logged_in_landing_page(self):
        driver = Mock(page_source=TNU_LANDING)
        driver.get_current_url.return_value = BASE + "controlador.php?acao=pessoa_alterar"
        with patch.object(auth, "has", return_value=False):
            self.assertEqual(auth.wait_for_step(driver, timeout=2), "home")


class ImageCaptchaTests(unittest.TestCase):
    def test_image_captcha_is_reported_instead_of_clicking_enviar(self):
        driver = Mock()
        driver.get_current_url.return_value = BASE + "externo_controlador.php"
        with patch.object(auth, "has", side_effect=lambda _driver, selector, *a: selector == auth.CAPTCHA_INPUT):
            with self.assertRaisesRegex(RuntimeError, "image CAPTCHA"):
                auth.wait_for_step(driver, timeout=2)
        driver.click.assert_not_called()

    def test_confirmation_alert_still_raises_a_clear_error(self):
        driver = Mock()
        driver.get_current_url.side_effect = UnexpectedAlertPresentException("alert")
        with patch.object(auth, "dismiss_alert", return_value="Informe o código de confirmação."):
            with self.assertRaisesRegex(RuntimeError, "confirmation code"):
                auth.wait_for_step(driver, timeout=2)


class EmptyReportTests(unittest.TestCase):
    def test_tjsp_empty_report_is_an_empty_success(self):
        parsed = parse_lawyer_process_report(TJSP_EMPTY_REPORT, BASE)
        self.assertEqual(parsed["items"], [])
        self.assertEqual(parsed["diagnostics"], [])
        self.assertTrue(parsed["pagination"]["complete"])

    def test_unknown_layout_is_still_rejected(self):
        parsed = parse_lawyer_process_report("<html><form id='frmProcessoLista'></form></html>", BASE)
        self.assertEqual(parsed["diagnostics"], ["unsupported_process_report_layout"])


if __name__ == "__main__":
    unittest.main()


# TJSP draws the queue counters through the state ajax call; the page only keeps the "expiring today" link.
TJSP_PANEL = """<html><body>
<a class="nav-link" data-urlAssinadaCarregamento="controlador_ajax.php?acao_ajax=retornar_citacao_intimacao_estado&estado=SP&usuarioAtual=true&hash=s1">SP</a>
<a class="nav-link" data-urlAssinadaCarregamento="controlador_ajax.php?acao_ajax=retornar_citacao_intimacao_estado&estado=TJSP&hash=s2">TJSP</a>
<a href="controlador.php?acao=citacao_intimacao_prazo_aberto_listar&vence_hoje=S&acao_origem=painel_adv_listar&hash=t">0</a>
</body></html>"""

TJSP_STATE = {
    "usuarios": [
        {
            "links": {
                "intimacaoPendenteUrgente": "controlador.php?acao=citacao_intimacao_pendente_listar_urgente&hash=a",
                "intimacaoPendente": "controlador.php?acao=citacao_intimacao_pendente_listar&hash=b",
                "intimacaoPendenteAcordo": "controlador.php?acao=citacao_intimacao_pendente_listar_acordo&hash=c",
                "citacaoArt334": "controlador.php?acao=citacao_art334_pendente_listar&hash=d",
                "intimacaoPauta": "controlador.php?acao=intimacao_de_pauta_listar&hash=e",
                "intimacaoAberto": "controlador.php?acao=citacao_intimacao_prazo_aberto_listar&hash=f",
                "decursoPrazo": "controlador.php?acao=citacao_intimacao_decurso_prazo_listar&hash=g",
            }
        }
    ]
}


class TjspQueueTests(unittest.TestCase):
    def run_queue(self, queue):
        import json

        from jurisync.connectors.eproc import client

        base = "https://eproc1g.tjsp.jus.br/eproc/"

        class Response:
            status_code = 200

            def __init__(self, body):
                self.content = body.encode("iso-8859-1")
                self.url = base
                self.headers = {}

        class Session:
            def __init__(self):
                self.urls = []

            def request(self, method, url, **kwargs):
                self.urls.append(url)
                if "retornar_citacao_intimacao_estado" in url:
                    return Response(json.dumps(TJSP_STATE))
                return Response("<html><body>Nenhum registro encontrado.</body></html>")

        session = Session()
        result = client.list_notices(
            queue, session=session, panel_html=TJSP_PANEL, panel_url=base + "controlador.php", portal="tjsp-1g"
        )
        return result, session.urls

    def test_every_queue_is_read_from_the_state_links(self):
        from jurisync.connectors.eproc import client

        for queue, action in client.QUEUE_KINDS.items():
            result, urls = self.run_queue(queue)
            self.assertTrue(result["success"], queue)
            self.assertIn(f"acao={action}&", urls[-1], queue)
            self.assertNotIn("vence_hoje", urls[-1], queue)
            self.assertIn("estado=SP", urls[0])  # the user's own context, not another tab

    def test_state_link_with_another_action_is_ignored(self):
        from jurisync.connectors.eproc import client

        panel = {"stateEndpoint": "controlador_ajax.php?acao_ajax=retornar_citacao_intimacao_estado&hash=s"}
        state = {"usuarios": [{"links": {"intimacaoPendente": "controlador.php?acao=sair&hash=x"}}]}
        base = "https://eproc1g.tjsp.jus.br/eproc/"

        class Session:
            def request(self, *args, **kwargs):
                import json

                return Mock(status_code=200, content=json.dumps(state).encode(), url=base, headers={})

        endpoint = client._state_endpoint(Session(), panel, "pending", panel_url=base, portal="tjsp-1g")
        self.assertIsNone(endpoint)
