"""PJe 1.x (trf5-legacy): panel counters, inbox and search, from saved real pages (names and CPF anonymised)."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from jurisync.connectors.pje import acervo, inbox, jsf, legacy, process, push, search

FIXTURES = Path(__file__).parent / "fixtures" / "trf5_legacy"
BASE = "https://pje.trf5.jus.br/pje/"


def page(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeSession:
    """Serves the panel and search pages: GETs by path, POSTs by what the payload asks for."""

    def __init__(self, post_pages, get_pages):
        self.post_pages, self.get_pages, self.posts, self.cookies = post_pages, get_pages, [], None

    def _response(self, url, text):
        # Encoded as the page declares, as the court sends it: the panel pages are ISO-8859-1.
        encoding = "iso-8859-1" if "charset=ISO-8859-1" in text[:4096] else "utf-8"
        return SimpleNamespace(
            status_code=200, url=url, headers={}, text=text, content=text.encode(encoding), close=lambda: None
        )

    def get(self, url, **kwargs):
        return self._response(url, self.get_pages[url.removeprefix(BASE)])

    def post(self, url, data="", **kwargs):
        self.posts.append(data)
        for needle, text in self.post_pages:
            if needle in data:
                return self._response(url, text)
        raise AssertionError(f"unexpected POST {data[:200]}")

    def close(self):
        pass


def panel_session():
    return FakeSession(
        [
            ("idCaixa_server_submit", page("painel_acervo_geral.html")),
            ("tabProcAdvPainelIntimacaoRpv", page("aba_rpv.html")),
            ("tabProcAdvPainelIntimacaoPendente", page("aba_pauta.html")),
            ("tabProcAdvPainelIntimacao=", page("aba_intimacoes.html")),
            ("expedientesPendentesAdvogadoToogle=", page("toggle_pendentes.html")),
        ],
        {legacy.PANEL_PATH: page("painel_acervo.html")},
    )


class TimedSession(FakeSession):
    def __init__(self, *args):
        super().__init__(*args)
        self.timeouts = []

    def get(self, url, **kwargs):
        self.timeouts.append(kwargs["timeout"])
        return super().get(url, **kwargs)

    def post(self, url, data="", **kwargs):
        self.timeouts.append(kwargs["timeout"])
        return super().post(url, data, **kwargs)


class Pje1xTimeout(unittest.TestCase):
    def test_the_legacy_timeout_reaches_only_legacy_requests(self):
        """It used to be set on the shared jsf.TIMEOUT, which every office's requests read while a walk was paused."""
        legacy_session = TimedSession(panel_session().post_pages, panel_session().get_pages)
        walk = legacy._walk_panel(legacy_session, "trf5-legacy")
        next(walk)
        other = TimedSession([], {legacy.PANEL_PATH: page("painel_acervo.html")})
        jsf.get(other, BASE + legacy.PANEL_PATH, label="other office")
        list(walk)
        self.assertEqual(other.timeouts, [jsf.TIMEOUT])
        self.assertEqual(set(legacy_session.timeouts), {legacy.LEGACY_TIMEOUT})
        self.assertGreater(len(legacy_session.timeouts), 1)


class Pje1xPanel(unittest.TestCase):
    def test_fixtures_carry_no_personal_data(self):
        for path in FIXTURES.glob("*.html"):
            text = path.read_text(encoding="utf-8").lower()
            for term in ("maximil", "ataide", "012.387", "32060"):
                self.assertNotIn(term, text, path.name)

    def test_tabs_and_buckets_are_read_from_the_pages(self):
        tabs = legacy.parse_tabs(page("painel_acervo.html"))
        self.assertEqual(
            [t[1] for t in tabs],
            ["Acervo", "Intimações", "Intimações de RPV/PRC", "Intimações de Pauta de Julgamento"],
        )
        buckets = legacy.parse_buckets(page("aba_intimacoes.html"))
        self.assertEqual(
            [(label, count) for _, label, count in buckets],
            [
                ("Pendentes", 0),
                ("Confirmadas pelo intimado/citado e dentro do prazo", 0),
                ("Confirmadas pelo PJe e dentro do prazo", 0),
                ("Que acabaram o prazo nos últimos 10 dias", 0),
                ("Sem prazo", 0),
                ("Respondidas nos últimos 10 dias", 0),
            ],
        )
        self.assertEqual(buckets[0][0], "expedientesPendentesAdvogadoToogle")
        self.assertEqual(len(legacy.parse_buckets(page("aba_pauta.html"))), 5)

    def test_counters_walk_acervo_and_the_three_intimacao_tabs(self):
        session = panel_session()
        result = inbox.read_counters("trf5-legacy", session=session)
        labels = [b["label"] for b in result["buckets"]]
        self.assertTrue(result["success"])
        self.assertEqual(labels[:2], ["Acervo / Pendentes de Manifestação", "Acervo / Acervo Geral"])
        self.assertIn("Intimações / Pendentes", labels)
        self.assertIn("Intimações de RPV/PRC / Sem prazo", labels)
        self.assertIn("Intimações de Pauta de Julgamento / Pendentes", labels)
        self.assertEqual(len(labels), 2 + 6 + 6 + 5)
        self.assertEqual({b["count"] for b in result["buckets"]}, {0})
        self.assertEqual([b["index"] for b in result["buckets"]], [str(i) for i in range(len(labels))])

    def test_empty_inbox_is_complete_and_opens_nothing(self):
        session = panel_session()
        result = inbox.read_inbox("trf5-legacy", session=session)
        self.assertEqual(result["bucket"]["label"], "Intimações / Pendentes")
        self.assertEqual((result["inboxes"], result["complete"]), ([], True))
        self.assertFalse([p for p in session.posts if "expedientesPendentesAdvogadoToogle=" in p])

    def test_inbox_bucket_is_chosen_by_index_or_label_and_filters_are_refused(self):
        buckets = [{"index": "0", "label": "A / X"}, {"index": "1", "label": "B / X"}]
        self.assertEqual(legacy.select_bucket(buckets, "1")["label"], "B / X")
        self.assertEqual(legacy.select_bucket(buckets, "A /")["index"], "0")
        with self.assertRaisesRegex(ValueError, "is not"):
            legacy.select_bucket(buckets, "X")
        with self.assertRaisesRegex(ValueError, "filters"):
            inbox.read_inbox("trf5-legacy", filters={"processNumber": "x"}, session=panel_session())

    def test_expanded_bucket_grid_is_empty_with_its_total(self):
        grid = legacy.parse_grid(page("toggle_pendentes.html"))
        self.assertEqual((grid["total"], grid["rows"]), (0, []))

    def test_grid_rows_are_keyed_by_header(self):
        html = """<table class="rich-table"><thead><tr><th></th><th>Processo</th><th>Classe Judicial</th><th>Expediente</th></tr></thead>
        <tbody><tr><td><input/></td><td><a>0000001-02.2026.4.05.8300</a></td><td>PROCEDIMENTO</td><td>Intimação</td></tr></tbody></table>
        <span>Foram encontrados: 1 resultados</span>"""
        grid = legacy.parse_grid(html)
        self.assertEqual(grid["total"], 1)
        self.assertEqual(
            grid["rows"],
            [
                {
                    "processNumber": "0000001-02.2026.4.05.8300",
                    "columns": {
                        "Processo": "0000001-02.2026.4.05.8300",
                        "Classe Judicial": "PROCEDIMENTO",
                        "Expediente": "Intimação",
                    },
                }
            ],
        )

    def test_non_empty_acervo_bucket_listing_is_refused_clearly(self):
        session = panel_session()
        buckets = [{"index": "0", "label": "Acervo / Acervo Geral", "count": 3, "kind": "acervo"}]
        from unittest.mock import patch

        with patch.object(legacy, "_find_bucket", return_value=(buckets[0], "<html></html>")):
            with self.assertRaisesRegex(ValueError, "not supported"):
                legacy.read_inbox(session, "trf5-legacy", bucket="0")

    def test_the_default_inbox_does_not_open_the_later_tabs(self):
        # max, 23/11: "Intimações de Pauta de Julgamento" took 27-37 s, and every inbox call opened it.
        session = panel_session()
        inbox.read_inbox("trf5-legacy", session=session)
        self.assertFalse([p for p in session.posts if "IntimacaoRpv" in p or "IntimacaoPendente" in p])
        self.assertEqual(len(session.posts), 2)  # Acervo Geral, then the Intimações tab

    def test_a_bucket_with_rows_opens_its_toggle_from_the_tab_page_already_read(self):
        session = panel_session()
        session.post_pages[3] = (
            "tabProcAdvPainelIntimacao=",
            page("aba_intimacoes.html").replace("Pendentes - 0", "Pendentes - 2", 1),
        )
        gets = []
        original = session.get
        session.get = lambda url, **kwargs: gets.append(url) or original(url, **kwargs)
        result = inbox.read_inbox("trf5-legacy", session=session)
        self.assertEqual(result["bucket"]["count"], 2)
        self.assertEqual(len(gets), 1)  # the panel, once
        self.assertIn("expedientesPendentesAdvogadoToogle=", session.posts[-1])
        self.assertEqual(len(session.posts), 3)

    def test_a_partial_label_still_reads_every_tab_to_be_sure_it_names_one_bucket(self):
        session = panel_session()
        result = inbox.read_inbox("trf5-legacy", bucket="Pauta de Julgamento / Pendentes", session=session)
        self.assertEqual(result["bucket"]["label"], "Intimações de Pauta de Julgamento / Pendentes")
        self.assertTrue([p for p in session.posts if "IntimacaoPendente" in p])


GRID_BODY = '<tbody id="formProcessoTrfInicialAdvogado:processoTrfInicialAdvogadoList:tb"></tbody>'
DETAIL = "openPopUp('p','/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id={id}&ca={ca}')"


def acervo_row(number, process_id=None, autuacao="05/03/2024", expediente=False):
    """A grid row in the column order of the real header (no live page had rows: names and numbers are fake)."""
    link = f'<a onclick="{DETAIL.format(id=process_id, ca="cd" * 16)}">' if process_id else "<a>"
    cells = [
        "<td></td>",
        f"<td>{link}APELAÇÃO CÍVEL {number}</a></td>",
        "<td>PARTE AUTORA DE TESTE</td>",
        "<td>INSTITUTO DE TESTE</td>",
        f"<td>{autuacao}</td>",
        *(["<td>Intimação</td>"] if expediente else []),
        "<td></td>",
    ]
    return f"<tr>{''.join(cells)}</tr>"


def with_rows(html, rows, total):
    html = html.replace(GRID_BODY, GRID_BODY.replace("></tbody>", f">{''.join(rows)}</tbody>"), 1)
    return html.replace("Foram encontrados:  0 resultados", f"Foram encontrados:  {total} resultados")


def acervo_session(panel=None, general=None):
    return FakeSession(
        [("idCaixa_server_submit", general or page("painel_acervo_geral.html"))],
        {legacy.PANEL_PATH: panel or page("painel_acervo.html")},
    )


class Pje1xAcervo(unittest.TestCase):
    def test_empty_acervo_is_complete_and_opens_only_the_two_sub_tabs(self):
        session = acervo_session()
        result = acervo.read_acervo("trf5-legacy", session=session)
        self.assertTrue(result["success"])
        self.assertEqual((result["total"], result["returned"], result["items"], result["complete"]), (0, 0, [], True))
        self.assertEqual(
            [box["label"] for box in result["jurisdictions"]],
            ["Acervo / Pendentes de Manifestação", "Acervo / Acervo Geral"],
        )
        self.assertEqual(len(session.posts), 1)  # Acervo Geral; no Intimações tab, row or Ciente
        self.assertNotIn("Ciente", session.posts[0])

    def test_grid_rows_become_items_in_the_shared_shape(self):
        first, second = "0000001-02.2024.4.05.0000", "0000003-04.2023.4.05.8300"
        general = with_rows(
            page("painel_acervo_geral.html"), [acervo_row(first, "77"), acervo_row(second, autuacao="")], 2
        )
        pending = with_rows(page("painel_acervo.html"), [acervo_row(first, "77", expediente=True)], 1)
        result = acervo.read_acervo("trf5-legacy", session=acervo_session(pending, general))
        self.assertEqual((result["total"], result["returned"], result["complete"]), (2, 2, True))
        item = result["items"][0]
        self.assertEqual(
            item,
            {
                "processNumber": first,
                "processId": "77",
                "classe": "APELAÇÃO CÍVEL",
                "assunto": None,
                "partes": "PARTE AUTORA DE TESTE x INSTITUTO DE TESTE",
                "orgaoJulgador": None,
                "distribuidoEm": "2024-03-05",
                "ultimoMovimento": None,
                "jurisdicao": None,
                "caixa": "Acervo / Pendentes de Manifestação",
            },
        )
        self.assertEqual((result["items"][1]["processId"], result["items"][1]["distribuidoEm"]), (None, None))
        self.assertEqual([box["count"] for box in result["jurisdictions"]], [1, 2])

    def test_fewer_rows_than_the_total_or_a_jurisdiction_tree_is_not_complete(self):
        general = with_rows(page("painel_acervo_geral.html"), [acervo_row("0000001-02.2024.4.05.0000")], 30)
        result = acervo.read_acervo("trf5-legacy", session=acervo_session(general=general))
        self.assertFalse(result["complete"])
        self.assertIn("1 of 30", result["notes"][0])
        tree = page("painel_acervo_geral.html").replace(
            '<div id="jurisdicaoForm:j_id317:childs"></div>',
            '<div id="jurisdicaoForm:j_id317:childs"><table class="rich-tree-node"><tr><td>RECIFE</td></tr></table></div>',
        )
        result = acervo.read_acervo("trf5-legacy", session=acervo_session(general=tree))
        self.assertEqual((result["total"], result["complete"]), (0, False))
        self.assertIn("jurisdiction tree", result["notes"][0])

    def test_a_page_without_the_grid_is_an_error_not_an_empty_acervo(self):
        with self.assertRaisesRegex(RuntimeError, "grid was not rendered"):
            legacy.list_processes(acervo_session(panel="<html><body>sessão expirada</body></html>"), "trf5-legacy")


class Pje1xPush(unittest.TestCase):
    def test_empty_push_list_reports_zero_results_and_complete(self):
        html = page("push_vazio.html")
        self.assertEqual(push.parse_push_items(html), [])
        pagination = push.parse_push_page(html, 0)
        self.assertEqual(pagination["results_count"], 0)
        self.assertTrue(pagination["complete"])


class Pje1xSearch(unittest.TestCase):
    def search_session(self, result_page):
        return FakeSession(
            [("consultarProcessoForm", result_page)],
            {"Processo/ConsultaProcesso/listView.seam": page("consulta_form.html")},
        )

    def test_process_not_listed_is_a_clean_failure(self):
        session = self.search_session(page("consulta_vazia.html"))
        result = search.search_process(session, "0000001-02.2026.4.05.0000", portal="trf5-legacy")
        self.assertFalse(result["success"])
        self.assertIn("not found", result["error"])
        self.assertTrue(result["error"].startswith("[process_not_found] "))
        self.assertEqual(result["reason"], "process_not_found")
        self.assertEqual(result["search_results"]["count"], 0)
        self.assertIn("0000001-02.2026.4.05.0000", session.posts[0].replace("%2D", "-").replace("%2E", "."))

    def test_the_search_sends_the_parameters_its_button_declares(self):
        form = page("consulta_form.html")
        self.assertEqual(
            legacy.search_parameters(form),
            {
                "consultarProcessoForm:j_id330": "1",
                "consultarProcessoForm:searchButton": "consultarProcessoForm:searchButton",
            },
        )
        session = self.search_session(page("consulta_vazia.html"))
        search.search_process(session, "0000001-02.2026.4.05.0000", portal="trf5-legacy")
        self.assertIn("j_id330", session.posts[0])
        rebuilt = form.replace("'consultarProcessoForm:j_id330':'1',", "")
        self.assertEqual(
            legacy.search_parameters(rebuilt),
            {"consultarProcessoForm:searchButton": "consultarProcessoForm:searchButton"},
        )
        session = FakeSession(
            [("consultarProcessoForm", page("consulta_vazia.html"))],
            {"Processo/ConsultaProcesso/listView.seam": rebuilt},
        )
        result = search.search_process(session, "0000001-02.2026.4.05.0000", portal="trf5-legacy")
        self.assertNotIn("j_id330", session.posts[0])
        self.assertIn("searchButton", session.posts[0])
        self.assertEqual(result["search_results"]["count"], 0)

    def test_fetch_process_reports_the_search_summary_for_a_missing_process(self):
        session = self.search_session(page("consulta_vazia.html"))
        result = process.fetch_process("0000001-02.2026.4.05.0000", portal="trf5-legacy", session=session)
        self.assertFalse(result["success"])
        self.assertEqual(result["searchSummary"]["count"], 0)

    def test_listed_row_with_a_detail_link_is_found(self):
        html = """<table class="rich-table" id="consultaProcessoList"><thead><tr><th>Processo</th><th>Distribuição</th></tr></thead>
        <tbody><tr><td><a onclick="openPopUp('x','/pje/Processo/ConsultaProcesso/Detalhe/listProcessoCompletoAdvogado.seam?id=77&ca=%s')">0000001-02.2026.4.05.8300</a></td><td>01/01/2026</td></tr></tbody></table>
        <span>Foram encontrados: 1 resultados</span>""" % ("ab" * 16)
        rows = legacy.parse_search_rows(html)
        self.assertEqual((rows[0]["processId"], rows[0]["ca"]), ("77", "ab" * 16))
