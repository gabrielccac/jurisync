"""Offline checks: no credentials or portal requests."""
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from integrations.pje import auth, client, trf1_2g, notifications


class PjeChecks(unittest.TestCase):
    def test_response_html_falls_back_when_pje_lies_about_utf8(self):
        response = SimpleNamespace(
            headers={"Content-Type": "text/html; charset=UTF-8"},
            content="Judiciário — definição".encode("cp1252"),
            text="Judici�rio � defini��o",
        )
        self.assertEqual(trf1_2g.response_html(response), "Judiciário — definição")

    def test_authenticator_migration_accounts(self):
        def varint(value):
            result = bytearray()
            while value > 127:
                result.append((value & 127) | 128)
                value >>= 7
            return bytes(result + bytes([value]))

        def field(number, value):
            return varint(number << 3 | 2) + varint(len(value)) + value

        account = field(1, b"test-secret") + field(2, b"PJe user") + field(3, b"PDPJ") + bytes([32, 1, 40, 1, 48, 2])
        encoded = base64.urlsafe_b64encode(field(1, account)).decode().rstrip("=")
        parsed = auth.migration_accounts(f"otpauth-migration://offline?data={encoded}")

        self.assertEqual(parsed[0]["issuer"], "PDPJ")
        self.assertEqual(parsed[0]["name"], "PJe user")
        self.assertEqual(auth.migration_totps(encoded).digits, 6)

    def test_portal_routing_and_session_reuse(self):
        for portal, module, name in (("1g", client.trf1_1g, "search_process_1g"), ("2g", client.trf1_2g, "search_process")):
            with self.subTest(portal=portal):
                session = Mock()
                result = {"success": True, "timeline": [{"date": "today", "movements": []}, {"date": "yesterday", "movements": []}]}
                with patch.object(module, name, return_value=result) as search, patch.object(client, "login_session") as login:
                    actual = client.fetch_process("1024853-70.2023.4.01.3400", portal=portal, scope="latest", session=session)
                search.assert_called_once_with(session, "1024853-70.2023.4.01.3400")
                login.assert_not_called()
                session.close.assert_not_called()
                self.assertEqual(actual["portal"], portal)
                self.assertEqual(len(actual["timeline"]), 1)

    def test_invalid_input_fails_before_login(self):
        with patch.object(client, "login_session") as login:
            for kwargs in ({"portal": "3g"}, {"scope": "bad"}, {"download_dir": "unused"}):
                with self.assertRaises(ValueError):
                    client.fetch_process("1024853-70.2023.4.01.3400", **kwargs)
            with self.assertRaises(ValueError):
                client.fetch_process("invalid")
            login.assert_not_called()

    def test_timeline_preserves_document_context(self):
        html = """
        <div class="media data"><div class="media-body"><span class="text-muted data-interna">04 set. 2026</span>
        <div class="media tipo-documento"><span class="text-upper texto-movimento">Juntada de certidão</span>
        <a><span>123 - Certidão</span></a>
        <div class="col-sm-12"><small class="text-muted pull-right">17:05</small>
        """
        timeline = trf1_2g.parse_timeline(html, "/documents/", "https://pje2g.trf1.jus.br")
        candidate = client.movement_candidates({"portal": "2g", "numero_processo": "test", "timeline": timeline})[0]
        self.assertEqual(candidate["recordType"], "movement")
        self.assertEqual(candidate["text"], "Juntada de certidão")
        self.assertEqual(candidate["documents"][0]["url"], "https://pje2g.trf1.jus.br/documents/123")
        self.assertTrue(candidate["hasDocuments"])
        self.assertEqual(candidate["contextAssessment"], "not_analyzed")
        self.assertEqual(candidate["entryType"], "documento")
        self.assertEqual(candidate["typeCode"], "documento")

    def test_detail_and_search_metadata_are_parsed(self):
        detail = """
        <div id="maisDetalhes">
          <dl>
            <dt>Classe judicial</dt><dd>JUIZADO (436)</dd>
            <dt>Assunto</dt><dd>DIREITO (12734) / BENEFÍCIO (6114)</dd>
            <dt>Autuação</dt><dd>15 dez. 2025</dd>
            <dt>Segredo de justiça?</dt><dd>NÃO</dd>
            <dt>Contato órgão julgador</dt><dd>(96) 0000-0000</dd><dd>vara@example.test</dd>
          </dl>
        </div>
        <div id="poloAtivo"><span>Polo ativo</span><span>AUTOR</span><li>ADVOGADO (ADVOGADO)</li></div>
        <div id="poloPassivo"><span>Polo passivo</span><span>RÉU</span></div>
        <div id="outrosInteressados"><span>Outros interessados</span><span>PERITO (PERITO)</span></div>
        <script>var listaIdsDocumentos = [1, 2];</script>
        """
        parsed = trf1_2g.parse_process_details(detail)
        self.assertEqual(parsed["judicial_class"], {"name": "JUIZADO", "code": "436"})
        self.assertEqual(parsed["subjects"][1]["code"], "6114")
        self.assertFalse(parsed["confidential"])
        self.assertEqual(parsed["judicial_body_contacts"]["email"], "vara@example.test")
        self.assertEqual(client.trf1_2g.parse_parties(detail)["active"], ["AUTOR", "ADVOGADO (ADVOGADO)"])

        search = """
        <table id="fPP:processosTable">
          <tr><th>Ações</th><th>Processo</th><th>Características</th><th>Órgão julgador</th><th>Autuado em</th><th>Classe judicial</th><th>Polo ativo</th><th>Polo passivo</th><th>Última moviment.</th></tr>
          <tr><td></td><td><a id="select" onclick="idProcessoSelecionado:123">1020340-18.2025.4.01.3100</a></td><td><i title="Prioritário"></i></td><td>Vara</td><td>15/12/2025</td><td>JUIZADO</td><td>AUTOR</td><td>RÉU</td><td>Publicado</td></tr>
        </table>
        """
        result = trf1_2g.parse_search_results(search)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["process_id"], "123")
        self.assertEqual(result["items"][0]["priority"], "Prioritário")

    def test_document_inventory_and_pagination_are_explicit(self):
        html = """
        <div class="media data"><div><span class="data-interna">07 set. 2026</span>
          <div class="media interno tipo-M"><span class="media-left"><span class="icon-auto sistema" title="Movimento processual lançado"></span></span><span class="texto-movimento">Movimento</span><div class="col-sm-12"><small>17:05</small></div></div>
        </div></div>
        <script>var listaIdsDocumentos = [123, 456];</script>
        <script>novaPagina=function(){};</script>
        """
        timeline = trf1_2g.parse_timeline(html, "/documents/")
        inventory = trf1_2g.parse_document_inventory(html, timeline, "/documents/")
        self.assertEqual([item["id"] for item in inventory], ["123", "456"])
        self.assertTrue(inventory[0]["visible_in_timeline"] is False)
        self.assertEqual(trf1_2g.parse_pagination(html, timeline)["mode"], "infinite_scroll")

    def test_portal_credentials_are_separate(self):
        with patch.dict("os.environ", {"PJE_1G_USERNAME": "one", "PJE_1G_PASSWORD": "p1", "PJE_1G_2FA_DATA": "s1",
                                     "PJE_2G_USERNAME": "two", "PJE_2G_PASSWORD": "p2", "PJE_2G_2FA_DATA": "s2"}, clear=True):
            self.assertEqual(client.credentials("1g"), ("one", "p1", "s1"))
            self.assertEqual(client.credentials("2g"), ("two", "p2", "s2"))

    def test_existing_notification_parsers(self):
        notifications.self_test()


if __name__ == "__main__":
    unittest.main()
