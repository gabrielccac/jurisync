"""Offline checks: no credentials or portal requests."""

import base64
import os
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock, patch

import pytest

from jurisync import portals
from jurisync.connectors.pje import parsers, search
from jurisync.connectors.pje import process as client
from jurisync.connectors.pje.auth import sso as auth
from jurisync.core import keycloak, totp

TRF1_SEARCH = portals.pje("trf1-1g").search_url


class PjeChecks(unittest.TestCase):
    def test_expired_password_has_actionable_login_error(self):
        html = (
            '<form id="loginForm"></form><span class="kc-feedback-text">Senha expirada. Solicite uma nova senha</span>'
        )
        kind, reason = keycloak.credential_failure(html)
        self.assertEqual(kind, "expired")
        self.assertIn("expired password", reason)

    def test_response_html_falls_back_when_pje_lies_about_utf8(self):
        response = SimpleNamespace(
            headers={"Content-Type": "text/html; charset=UTF-8"},
            content="Judiciário — definição".encode("cp1252"),
            text="Judici�rio � defini��o",
        )
        self.assertEqual(parsers.response_html(response), "Judiciário — definição")

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
        parsed = totp.migration_accounts(f"otpauth-migration://offline?data={encoded}")

        self.assertEqual(parsed[0]["issuer"], "PDPJ")
        self.assertEqual(parsed[0]["name"], "PJe user")
        self.assertEqual(totp.totp(totp.select_account(totp.parse_accounts(encoded))).digits, 6)

    def test_login_form_actions_are_limited_to_pje_origins(self):
        allowed = "<form id='loginForm' action='/real-login'></form>"
        self.assertEqual(
            auth.form_action(allowed, "https://sso.cloud.pje.jus.br/login", "loginForm"),
            "https://sso.cloud.pje.jus.br/real-login",
        )
        for action in (
            "https://evil.example/login",
            "http://sso.cloud.pje.jus.br/login",
            "https://user:s3cret@sso.cloud.pje.jus.br/login",
            "https://sso.cloud.pje.jus.br:8443/login",
        ):
            with self.subTest(action=action), self.assertRaises(RuntimeError):
                auth.form_action(f"<form id='loginForm' action='{action}'></form>", action, "loginForm")

    def test_portal_routing_and_session_reuse(self):
        for portal, number in (
            ("trf1-1g", "1024853-70.2023.4.01.3400"),
            ("trf1-2g", "1024853-70.2023.4.01.3400"),
            ("trf5-1g", "1024853-70.2023.4.05.3400"),
        ):
            with self.subTest(portal=portal):
                session = Mock()
                result = {
                    "success": True,
                    "timeline": [{"date": "today", "movements": []}, {"date": "yesterday", "movements": []}],
                }
                with (
                    patch.object(client.search, "search_process", return_value=result) as search,
                    patch.object(client, "login_portal") as login,
                ):
                    actual = client.fetch_process(number, portal=portal, scope="latest", session=session)
                search.assert_called_once_with(session, number, None, portal=portal)
                login.assert_not_called()
                session.close.assert_not_called()
                self.assertEqual(actual["portal"], portal)
                self.assertEqual(len(actual["timeline"]), 1)

    def test_parties_carry_masked_cpfs(self):
        result = {
            "success": True,
            "timeline": [],
            "parties": {
                "active": ["PARTE FICTICIA - CPF: 123.456.789-09 (EXEQUENTE)", "ADVOGADO FICTICIO (ADVOGADO)"],
                "passive": ["EMPRESA FICTICIA - CNPJ: 12.345.678/0001-90 (EXECUTADO)"],
                "other_interested": ["PERITA FICTICIA - CPF: 98765432100 (PERITO)"],
            },
        }
        with patch.object(client.search, "search_process", return_value=result):
            actual = client.fetch_process("1024853-70.2023.4.01.3400", portal="trf1-1g", session=Mock())
        self.assertEqual(
            actual["parties"],
            {
                "active": ["PARTE FICTICIA - CPF: 123.***.***-09 (EXEQUENTE)", "ADVOGADO FICTICIO (ADVOGADO)"],
                "passive": ["EMPRESA FICTICIA - CNPJ: 12.345.678/0001-90 (EXECUTADO)"],
                "other_interested": ["PERITA FICTICIA - CPF: 987******00 (PERITO)"],
            },
        )

    def test_download_pattern_finds_every_viewer_shape(self):
        self.assertTrue(search.DOWNLOAD_PATTERN.search("window.open('/pje/documento/download/TRF5/x/1/'"))
        legacy = "var url = '/1g/seam/resource/rest/pje-legacy/documento/download/-1/1g/7878703/' + idDocumento"
        match = search.DOWNLOAD_PATTERN.search(legacy)
        self.assertEqual(
            next(g for g in match.groups() if g),
            "/1g/seam/resource/rest/pje-legacy/documento/download/-1/1g/7878703/",
        )
        # TJPA's viewer frame (anonymized shape of its detail page): the PDF URL ends in "#toolbar=0".
        frame = (
            '<iframe id="frameBinario" src="/pje/seam/resource/rest/pje-legacy/documento/download/151000001'
            '#toolbar=0" width="100%" height="100%" style="border:5px #000000 solid"></iframe>'
        )
        match = search.DOWNLOAD_PATTERN.search(frame)
        self.assertEqual(next(g for g in match.groups() if g), "/pje/seam/resource/rest/pje-legacy/documento/download/")

    def test_cross_tribunal_number_fails_before_login(self):
        with patch.object(client, "login_portal") as login, self.assertRaisesRegex(ValueError, "TRF5"):
            client.fetch_process("1024853-70.2023.4.01.3400", portal="trf5-1g")
        login.assert_not_called()

    def test_invalid_input_fails_before_login(self):
        with self.assertRaises(TypeError):
            client.fetch_process("1024853-70.2023.4.01.3400")
        with patch.object(client, "login_portal") as login:
            for kwargs in (
                {"portal": "3g"},
                {"portal": "trf1-1g", "scope": "bad"},
                {"portal": "trf1-1g", "download_dir": "unused"},
            ):
                with self.assertRaises(ValueError):
                    client.fetch_process("1024853-70.2023.4.01.3400", **kwargs)
            with self.assertRaises(ValueError):
                client.fetch_process("invalid", portal="trf1-1g")
            login.assert_not_called()

    def test_tjpa_movement_glossary_link_is_not_a_document(self):
        html = """
        <div class="media data"><div class="media-body"><span class="text-muted data-interna">10 nov. 2026</span>
        <div class="media interno tipo-D"><div><i class="fa fa-bullhorn"></i>
        <a href="#" onclick="A4J.AJAX.Submit('divTimeLine',event,{'oncomplete':function(){Richfaces.showModalPanel('modalGlossario');},'parameters':{'codigo':'12164'}});return false;" title="Clique aqui para mais informações"><span class="text-upper texto-movimento">12164 - Proferidas outras decisões não especificadas</span></a></div>
        <a href="#" onclick="A4J.AJAX.Submit('divTimeLine',event,{});return false;"><i class="fa fa-file-pdf-o"></i><span class="" title="">190000001 - Decisão</span></a>
        <div class="col-sm-12"><small class="text-muted pull-right">10:00</small></div></div>
        """
        [day] = parsers.parse_timeline(html, "/documents/", "https://pje.tjpa.jus.br")
        [movement] = day["movements"]
        self.assertEqual(movement["movement"], "12164 - Proferidas outras decisões não especificadas")
        self.assertEqual([d["id"] for d in movement["documents"]], ["190000001"])

    def test_timeline_preserves_document_context(self):
        html = """
        <div class="media data"><div class="media-body"><span class="text-muted data-interna">04 set. 2026</span>
        <div class="media tipo-documento"><span class="text-upper texto-movimento">Juntada de certidão</span>
        <a><span>123 - Certidão</span></a>
        <div class="col-sm-12"><small class="text-muted pull-right">17:05</small>
        """
        timeline = parsers.parse_timeline(html, "/documents/", "https://pje2g.trf1.jus.br")
        candidate = client.movement_candidates({"portal": "trf1-2g", "numero_processo": "test", "timeline": timeline})[
            0
        ]
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
        parsed = parsers.parse_process_details(detail)
        self.assertEqual(parsed["judicial_class"], {"name": "JUIZADO", "code": "436"})
        self.assertEqual(parsed["subjects"][1]["code"], "6114")
        listed = detail.replace(
            "<dt>Assunto</dt><dd>DIREITO (12734) / BENEFÍCIO (6114)</dd>",
            "<dt>Assunto</dt><dd><ul><li>Auxílio por Incapacidade Temporária (6101)</li>"
            "<li>Restabelecimento (6178)</li></ul></dd>",
        )
        self.assertEqual(
            parsers.parse_process_details(listed)["subjects"],
            [
                {"name": "Auxílio por Incapacidade Temporária", "code": "6101"},
                {"name": "Restabelecimento", "code": "6178"},
            ],
        )
        self.assertFalse(parsed["confidential"])
        self.assertEqual(parsed["judicial_body_contacts"]["email"], "vara@example.test")
        self.assertEqual(parsers.parse_parties(detail)["active"], ["AUTOR", "ADVOGADO (ADVOGADO)"])

        search = """
        <table id="fPP:processosTable">
          <tr><th>Ações</th><th>Processo</th><th>Características</th><th>Órgão julgador</th><th>Autuado em</th><th>Classe judicial</th><th>Polo ativo</th><th>Polo passivo</th><th>Última moviment.</th></tr>
          <tr><td></td><td><a id="select" onclick="idProcessoSelecionado:123">1020340-18.2025.4.01.3100</a></td><td><i title="Prioritário"></i></td><td>Vara</td><td>15/12/2025</td><td>JUIZADO</td><td>AUTOR</td><td>RÉU</td><td>Publicado</td></tr>
        </table>
        """
        result = parsers.parse_search_results(search)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["process_id"], "123")
        self.assertEqual(result["items"][0]["priority"], "Prioritário")

    def test_search_table_without_leading_column_is_parsed(self):
        """TJPI's result table has 8 columns: the process number is the first cell, not the second."""
        html = (
            '<table id="fPP:processosTable"><tr><td><a onclick="idProcessoSelecionado:2748118">'
            '1020340-18.2025.8.18.0051</a></td><td><i title="Liminar"></i></td><td>Vara Unica</td>'
            "<td>15/09/2026</td><td>PROCEDIMENTO COMUM</td><td>AUTOR</td><td>REU</td><td>Concluso</td></tr></table>"
        )
        item = parsers.parse_search_results(html)["items"][0]
        self.assertEqual(item["process_id"], "2748118")
        self.assertEqual(item["priority"], "Liminar")
        self.assertEqual(item["judicial_body"], "Vara Unica")
        self.assertEqual(item["passive_party"], "REU")
        self.assertEqual(item["last_movement"], "Concluso")
        self.assertEqual(parsers.matching_process_id(html, "1020340-18.2025.8.18.0051")[0], "2748118")

    def test_search_never_selects_a_different_process(self):
        html = """<table id="fPP:processosTable"><tr><td></td><td><a onclick="idProcessoSelecionado:123">1020340-18.2025.4.01.3100</a></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr></table>"""
        self.assertEqual(parsers.matching_process_id(html, "1020340-18.2025.4.01.3100")[0], "123")
        self.assertIsNone(parsers.matching_process_id(html, "0000000-00.0000.4.01.0000")[0])
        self.assertIsNone(parsers.parse_search_results("<html></html>")["count"])

    def test_wrong_search_row_never_opens_detail(self):
        page = """<form id="fPP"><input name="javax.faces.ViewState" value="state"><input id="fPP:search" value="Pesquisar" onclick="A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'fPP:search'})"></form>"""
        result = """<input name="javax.faces.ViewState" value="new"><table id="fPP:processosTable"><tr><td></td><td><a onclick="idProcessoSelecionado:123">1020340-18.2025.4.01.3100</a></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr></table>"""
        response = lambda body: SimpleNamespace(
            status_code=200,
            url=portals.pje("trf1-2g").search_url,
            headers={"Content-Type": "text/html"},
            content=body.encode(),
            text=body,
        )
        session = Mock()
        session.get.return_value = response(page)
        session.post.return_value = response(result)
        actual = search.search_process(session, "0000000-00.0000.4.01.0000", portal="trf1-2g")
        self.assertFalse(actual["success"])
        session.get.assert_called_once()
        session.post.assert_called_once()

    def test_empty_search_table_looks_the_process_up_in_the_acervo(self):
        page = """<form id="fPP"><input name="javax.faces.ViewState" value="state"><input id="fPP:search" value="Pesquisar" onclick="A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'fPP:search'})"></form>"""
        empty = """<input name="javax.faces.ViewState" value="new"><table id="fPP:processosTable"></table>"""
        response = lambda body: SimpleNamespace(
            status_code=200,
            url=portals.pje("tjmt-2g").search_url,
            headers={"Content-Type": "text/html"},
            content=body.encode(),
            text=body,
        )
        session = Mock()
        session.get.return_value = response(page)
        session.post.return_value = response(empty)
        with mock.patch.object(search.acervo, "find_process", return_value=None) as find:
            actual = search.search_process(session, "1003829-23.2023.8.11.0021", portal="tjmt-2g")
        find.assert_called_once()
        self.assertEqual(actual["error"], "Process not found in the search nor in the lawyer's Acervo")

    def test_recaptcha_hidden_search_action_is_shared(self):
        html = """<form id="fPP"><script id="fPP:j_id77">executarPesquisaReCaptcha=function(){A4J.AJAX.Submit('fPP',null,{'similarityGroupingId':'fPP:j_id77','parameters':{'fPP:j_id77':'fPP:j_id77'}})};</script><input id="fPP:searchProcessos" value="Pesquisar" onclick="return executarReCaptcha();"></form>"""
        self.assertEqual(search.discover_controls(html)["search"], "fPP:j_id77")

    def test_tjpe_search_action_is_the_submeter_pesquisa_function_not_the_button(self):
        html = """<form id="fPP"><script>var captchaAtivo = false; function pesquisar(){ submeterPesquisa(); }</script>
        <input id="fPP:searchProcessos" value="Pesquisar" onclick="return pesquisar();;A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'fPP:searchProcessos'});return false;">
        <script id="fPP:j_id479">submeterPesquisa=function(){A4J.AJAX.Submit('fPP',null,{'similarityGroupingId':'fPP:j_id479','parameters':{'fPP:j_id479':'fPP:j_id479'} } )};</script>
        <script id="fPP:j_id480">refreshCaptchaAtivo=function(){A4J.AJAX.Submit('fPP',null,{'similarityGroupingId':'fPP:j_id480'})};</script></form>"""
        self.assertEqual(search.discover_controls(html), {"init": None, "search": "fPP:j_id479"})
        active = html.replace("captchaAtivo = false", "captchaAtivo = true")
        with self.assertRaisesRegex(RuntimeError, "reCAPTCHA"):
            search.discover_controls(active)

    def test_timeline_class_order_and_distinct_documents(self):
        html = """<div class="data media"><div class="media-body"><span class="data-interna text-muted">07 set. 2026</span></div><div class="tipo-M interno media"><span class="texto-movimento text-upper">Juntada</span><a><span>1 - A</span></a><div class="col-sm-12"><small>17:05</small></div></div><div class="tipo-M interno media"><span class="texto-movimento text-upper">Juntada</span><a><span>2 - B</span></a><div class="col-sm-12"><small>17:05</small></div></div></div>"""
        timeline = parsers.parse_timeline(html, "/documents/", "https://pje1g.trf1.jus.br")
        self.assertEqual(len(timeline[0]["movements"]), 2)
        self.assertEqual(len(search._merge_timelines(timeline, timeline)[0]["movements"]), 2)
        self.assertEqual([m["documents"][0]["id"] for m in timeline[0]["movements"]], ["1", "2"])

    def test_document_inventory_and_pagination_are_explicit(self):
        html = """
        <div class="media data"><div><span class="data-interna">07 set. 2026</span>
          <div class="media interno tipo-M"><span class="media-left"><span class="icon-auto sistema" title="Movimento processual lançado"></span></span><span class="texto-movimento">Movimento</span><div class="col-sm-12"><small>17:05</small></div></div>
        </div></div>
        <script>var listaIdsDocumentos = [123, 456];</script>
        <script>novaPagina=function(){};</script>
        """
        base_url = "https://pje2g.trf1.jus.br"
        timeline = parsers.parse_timeline(html, "/documents/", base_url)
        inventory = parsers.parse_document_inventory(html, timeline, "/documents/", base_url)
        self.assertEqual([item["id"] for item in inventory], ["123", "456"])
        self.assertTrue(inventory[0]["visible_in_timeline"] is False)
        self.assertEqual(parsers.parse_pagination(html, timeline)["mode"], "infinite_scroll")

    def test_inventory_flags_the_first_instance_copy_named_in_a_title(self):
        timeline = [
            {
                "date": "07 set. 2026",
                "movements": [
                    {"documents": [{"id": "10", "title": "Petição Inicial - ID de origem 99", "download_url": "u"}]}
                ],
            }
        ]
        html = "<script>var listaIdsDocumentos = [10, 99, 55];</script>"
        inventory = {i["id"]: i for i in parsers.parse_document_inventory(html, timeline, "/documents/", "https://x")}
        self.assertTrue(inventory["99"]["first_instance_copy"])
        self.assertNotIn("first_instance_copy", inventory["10"])
        self.assertNotIn("first_instance_copy", inventory["55"])  # older page, still readable

    def test_1g_controls_are_discovered_from_current_ajax_metadata(self):
        html = """
        <form id="fPP">
          <select id="fPP:numeroProcesso:ramoJustica"
            onchange="A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'fPP:init_77'})"></select>
          <input id="fPP:search_visible" value="Pesquisar"
            onclick="A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'fPP:search_88'})">
        </form>
        """
        self.assertEqual(search.discover_controls(html), {"init": "fPP:init_77", "search": "fPP:search_88"})

    def test_1g_search_control_works_when_portal_has_no_init_control(self):
        html = """
        <form id="fPP">
          <input id="fPP:numeroProcesso:ramoJustica" value="4">
          <input id="fPP:search_visible" value="Pesquisar"
            onclick="A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'fPP:search_88'})">
        </form>
        """
        self.assertEqual(search.discover_controls(html), {"init": None, "search": "fPP:search_88"})

    def test_1g_control_discovery_fails_closed_on_ambiguity_or_missing_row(self):
        ambiguous = """
        <form id="fPP">
          <select id="fPP:numeroProcesso:ramoJustica" onchange="A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'fPP:init'})"></select>
          <input id="fPP:a" value="Pesquisar" onclick="A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'fPP:a1'})">
          <input id="fPP:b" value="Pesquisar" onclick="A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'fPP:b1'})">
        </form>
        """
        with self.assertRaises(RuntimeError):
            search.discover_controls(ambiguous)
        with self.assertRaises(RuntimeError):
            search.select_control("<table></table>", "123")

    def test_1g_pagination_contract_is_host_and_path_bound(self):
        detail = (
            "https://pje1g.trf1.jus.br/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id=1"
        )
        safe = '<a href="/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?novaPagina=1">next novaPagina</a>'
        self.assertTrue(search.parse_timeline_contract(safe, detail).startswith(detail.split("?")[0]))
        with self.assertRaises(RuntimeError):
            search.parse_timeline_contract('<a href="https://evil.example/novaPagina">next</a>', detail)
        with self.assertRaises(RuntimeError):
            search.parse_timeline_contract(
                '<a href="/pje/Processo/ConsultaProcesso/Detalhe/other.seam?novaPagina=1">next</a>', detail
            )

    def test_1g_timeline_pagination_reports_cap(self):
        detail = (
            "https://pje1g.trf1.jus.br/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id=1"
        )
        html = '<a href="/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?novaPagina=1">novaPagina</a>'
        response = SimpleNamespace(
            status_code=200, headers={"Content-Type": "text/html"}, content=html.encode(), text=html
        )
        session = Mock()
        session.get.return_value = response
        _, pagination = search.fetch_timeline_pages(session, html, detail, None, TRF1_SEARCH, max_pages=2)
        self.assertFalse(pagination["complete"])
        self.assertEqual(pagination["loadedPages"], 2)
        self.assertEqual(pagination["diagnostics"][0]["code"], "pagination_limit")

    def test_1g_timeline_follows_each_returned_url_and_preserves_page_one(self):
        detail = (
            "https://pje1g.trf1.jus.br/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id=1"
        )
        first = '<div class="media data"><div class="media-body"><span class="text-muted data-interna">07 set. 2026</span></div></div><a href="/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?novaPagina=1">novaPagina</a>'
        page_a = '<a href="/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?novaPagina=2">novaPagina</a>'
        page_b = '<div class="media data"><div class="media-body"><span class="text-muted data-interna">06 set. 2026</span></div></div>'
        response = lambda body: SimpleNamespace(
            status_code=200, headers={"Content-Type": "text/html"}, content=body.encode(), text=body
        )
        session = Mock()
        session.get.side_effect = [response(page_a), response(page_b)]
        timeline, pagination = search.fetch_timeline_pages(session, first, detail, None, TRF1_SEARCH, max_pages=5)
        urls = [call.args[0] for call in session.get.call_args_list]
        self.assertEqual(urls, [detail.split("?")[0] + "?novaPagina=1", detail.split("?")[0] + "?novaPagina=2"])
        self.assertEqual([day["date"] for day in timeline], ["07 set. 2026", "06 set. 2026"])
        self.assertEqual(pagination["loadedPages"], pagination["loaded_pages"])
        self.assertTrue(pagination["complete"])
        self.assertFalse(pagination["more_pages_possible"])

    def test_1g_timeline_repeated_contract_stops_without_refetching(self):
        detail = (
            "https://pje1g.trf1.jus.br/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id=1"
        )
        html = '<a href="/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?novaPagina=1">novaPagina</a>'
        response = SimpleNamespace(
            status_code=200, headers={"Content-Type": "text/html"}, content=html.encode(), text=html
        )
        session = Mock()
        session.get.return_value = response
        _, pagination = search.fetch_timeline_pages(session, html, detail, None, TRF1_SEARCH, max_pages=5)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(pagination["diagnostics"][0]["code"], "pagination_repeated_contract")
        self.assertEqual(pagination["loadedPages"], 2)
        self.assertFalse(pagination["complete"])

    def test_ajax_timeline_action_uses_same_authenticated_session(self):
        detail = (
            "https://pje1g.trf1.jus.br/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id=1"
        )
        action = "<script id='divTimeLine:j_id77'>novaPagina=function(){A4J.AJAX.Submit('divTimeLine',null,{'similarityGroupingId':'divTimeLine:j_id77'})};</script>"
        first = (
            "<form id='divTimeLine'><input name='javax.faces.ViewState' value='first'></form><div class='media data'><span class='data-interna'>today</span><div class='media tipo-M'><span class='texto-movimento text-upper'>One</span></div></div>"
            + action
        )
        second = (
            "<input name='javax.faces.ViewState' value='second'><div class='media data'><span class='data-interna'>yesterday</span><div class='media tipo-M'><span class='texto-movimento text-upper'>Two</span></div></div>"
            + action
        )
        response = SimpleNamespace(
            status_code=200, headers={"Content-Type": "text/html"}, content=second.encode(), text=second
        )
        session = Mock()
        session.post.return_value = response
        timeline, pagination = search.fetch_timeline_pages(session, first, detail, None, TRF1_SEARCH, max_pages=2)
        self.assertEqual([day["date"] for day in timeline], ["today", "yesterday"])
        self.assertEqual(pagination["loadedPages"], 2)
        session.post.assert_called_once()
        self.assertIn("divTimeLine%3Aj_id77", session.post.call_args.kwargs["data"])

    def test_portal_credentials_are_separate(self):
        first, second = "JBSWY3DPEHPK3PXP", "GEZDGNBVGY3TQOJQ"
        with patch.dict(
            "os.environ",
            {
                "PJE_1G_USERNAME": "one",
                "PJE_1G_PASSWORD": "p1",
                "PJE_1G_2FA_DATA": first,
                "PJE_2G_USERNAME": "two",
                "PJE_2G_PASSWORD": "p2",
                "PJE_2G_2FA_DATA": second,
            },
            clear=True,
        ):
            username, password, account = client.credentials("trf1-1g")
            self.assertEqual((username, password, account["secret"]), ("one", "p1", first))
            username, password, account = client.credentials("trf1-2g")
            self.assertEqual((username, password, account["secret"]), ("two", "p2", second))

    def test_invalid_authenticator_data_fails_at_configuration(self):
        with patch.dict(
            "os.environ",
            {"PJE_1G_USERNAME": "one", "PJE_1G_PASSWORD": "p1", "PJE_1G_2FA_DATA": "not-a-secret!"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                client.credentials("trf1-1g")

    def test_shared_credentials_do_not_fallback_to_another_portal(self):
        with (
            patch.dict(
                "os.environ",
                {
                    "PJE_1G_USERNAME": "one",
                    "PJE_1G_PASSWORD": "p1",
                    "PJE_1G_2FA_DATA": "s1",
                    "PJE_USERNAME": "shared",
                    "PJE_PASSWORD": "shared",
                    "PJE_2FA_DATA": "shared",
                },
                clear=True,
            ),
            self.assertRaisesRegex(RuntimeError, "TRF1_2G_USERNAME"),
        ):
            client.credentials("trf1-2g")

    def test_otp_profile_selection_is_per_portal(self):
        def field(number, value):
            result = bytearray()
            tag = number << 3 | 2
            while tag > 127:
                result.append((tag & 127) | 128)
                tag >>= 7
            result.append(tag)
            length = len(value)
            while length > 127:
                result.append((length & 127) | 128)
                length >>= 7
            result.append(length)
            return bytes(result) + value

        def profile(name, secret):
            body = field(1, secret) + field(2, name.encode()) + field(3, b"PDPJ") + bytes([32, 2, 40, 2, 48, 2])
            return field(1, body)

        exported = (
            base64.urlsafe_b64encode(profile("first", b"first-secret") + profile("second", b"second-secret"))
            .decode()
            .rstrip("=")
        )
        environment = {
            "PJE_1G_USERNAME": "one",
            "PJE_1G_PASSWORD": "p1",
            "PJE_1G_2FA_DATA": exported,
            "PJE_1G_OTP_PROFILE_MATCH": "first",
            "PJE_2G_USERNAME": "two",
            "PJE_2G_PASSWORD": "p2",
            "PJE_2G_2FA_DATA": exported,
            "PJE_2G_OTP_PROFILE_INDEX": "1",
        }
        with patch.dict("os.environ", environment, clear=True):
            first = totp.totp(client.credentials("trf1-1g")[2])
            second = totp.totp(client.credentials("trf1-2g")[2])
            self.assertEqual((first.digits, first.digest().name), (8, "sha256"))
            self.assertEqual((second.digits, second.digest().name), (8, "sha256"))
            self.assertEqual(first.secret, base64.b32encode(b"first-secret").decode())
            self.assertEqual(second.secret, base64.b32encode(b"second-secret").decode())


if __name__ == "__main__":
    unittest.main()


def test_a_local_server_offers_and_logs_in_only_where_the_account_has_a_login(monkeypatch):
    from jurisync.connectors.pje import server
    from jurisync.portals import PORTAL_HELP, offer_portals

    for name in [n for n in os.environ if n.startswith(("PJE_", "TJ", "TRF", "TRT", "TST"))]:
        monkeypatch.delenv(name)
    assert server.configured_portals() == ()  # no login anywhere: nothing is claimed as configured
    monkeypatch.setenv("PJE_A1_P12", "/tmp/a1.p12")
    monkeypatch.setenv("PJE_1G_CERT", "true")  # TRF1 1G opts in to the A1
    monkeypatch.setenv("PJE_TRF5_1G_2FA_DATA", "JBSWY3DPEHPK3PXP")
    assert server.configured_portals() == ()  # the A1 file without its PIN cannot log in anywhere
    monkeypatch.setenv("PJE_A1_PIN", "1234")
    monkeypatch.setenv("TJAP_1G_USERNAME", "user")
    monkeypatch.setenv("TJAP_1G_PASSWORD", "secret")
    for prefix in ("PJE_1G", "PJE_TRF5_1G", "TJAP_1G", "TJPE_1G"):
        monkeypatch.setenv(f"{prefix}_2FA_DATA", "JBSWY3DPEHPK3PXP")
    keys = server.configured_portals()
    assert {"trf1-1g", "trf5-1g", "tjap-1g"} <= set(keys)  # opted in, certificate portal, password
    assert "trf1-2g" not in keys and "tjpe-1g" not in keys  # no authenticator / password portal without a login
    assert "trt7-1g" not in keys  # the A1 alone is not enough: the SSO asks the code
    assert "trf5-legacy" in keys  # PJe 1.x: no SSO, the A1 alone logs in

    srv = server.create_server()
    try:
        offer_portals(srv, keys)
        server.sessions.only(keys)
        schema = {
            t.name: t.parameters["properties"]["portal"]
            for t in srv._tool_manager.list_tools()
            if "portal" in t.parameters.get("properties", {})
        }
        assert all(field["enum"] == list(keys) for field in schema.values())
        assert all(field["description"] == PORTAL_HELP for field in schema.values())
        noted = [t.description for t in srv._tool_manager.list_tools() if "Portals: " in t.description]
        assert len(noted) == 1 and "trf5-1g (pje1g.trf5.jus.br)" in noted[0]
        with pytest.raises(ValueError, match="No login is configured for trf1-2g"):
            with server.sessions.session("trf1-2g"):
                pass
    finally:
        server.sessions._only = None


def test_pje1x_needs_only_the_a1_and_never_the_password(monkeypatch):
    """customers/max: TRF5's PJe 1.x (trf5-legacy) with the A1 and no authenticator."""
    from jurisync.connectors.pje import server

    for name in [n for n in os.environ if n.startswith(("PJE_", "TJ", "TRF", "TRT", "TST"))]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("PJE_TRF5_LEGACY_USERNAME", "user")
    monkeypatch.setenv("PJE_TRF5_LEGACY_PASSWORD", "secret")
    monkeypatch.setenv("PJE_TRF5_LEGACY_2FA_DATA", "JBSWY3DPEHPK3PXP")
    assert "trf5-legacy" not in server.configured_portals()  # PJe 1.x has no password login
    for name in ("USERNAME", "PASSWORD", "2FA_DATA"):
        monkeypatch.delenv(f"PJE_TRF5_LEGACY_{name}")
    monkeypatch.setenv("PJE_A1_P12", "/tmp/a1.p12")
    monkeypatch.setenv("PJE_A1_PIN", "1234")
    assert server.configured_portals() == ("trf5-legacy",)
