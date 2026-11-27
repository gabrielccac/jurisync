"""Árvore de documentos: the whole-case inventory of eproc_get_process, over fictitious pages faked by URL."""

import unittest

from jurisync.connectors.eproc import arvore, ciencia, client
from jurisync.core.session_pool import SessionExpired
from tests.connectors.eproc.test_eproc_ciencia import (
    BASE,
    NUMBER,
    OTHER,
    PANEL,
    PANEL_URL,
    READ,
    Portal,
    Reply,
    detail,
    queue,
)

DIGITS = NUMBER.replace("-", "").replace(".", "")
TREE_BUTTON = (
    f'<a class="infraButton" href="controlador.php?acao=arvore_documento_listar&amp;txtNumProcesso={DIGITS}'
    '&amp;hash=t" target="_blank" title="Tecla de atalho: Alt+Y">Árvore</a>'
)


def tree_link(doc, event, label, tooltip=""):
    href = f"controlador.php?acao=acessar_documento&amp;doc={doc}&amp;evento={event}&amp;key=k&amp;hash=h{doc}"
    return f'<a class="infraLinkDocumento" href="{href}" title="{tooltip}">{label}</a>'


TREE = (
    "<ul>"
    "<li>Evento 1 - 01/10/2026 09:00:00 - DISTRIBUIÇÃO<ul>"
    f"<li>{tree_link(70, 1, 'INIC1', 'PETIÇÃO INICIAL')}</li>"
    f"<li>{tree_link(71, 1, 'PROC1', 'PROCURAÇÃO')}</li>"
    "</ul></li>"
    "<li>Evento 2 - 02/11/2026 10:00:00 - DESPACHO<ul>"
    f"<li>{tree_link(20, 2, 'DESPADEC1')}</li>"
    "</ul></li>"
    "</ul>"
)


def portal(*, timeline_complete=False, tree=TREE, **pages):
    page = detail(
        events_url=None
        if not timeline_complete
        else "controlador_ajax.php?acao_ajax=montar_tabela_eventos_filtrados&hash=e"
    )
    pages = {"citacao_intimacao_pendente_listar": queue(OTHER), **pages}
    return Portal(detail=page + TREE_BUTTON, arvore_documento_listar=tree, **pages)


def read(fake, document_ids=None, **kwargs):
    return client.fetch_process(
        NUMBER, "page", document_ids, session=fake, panel_html=PANEL, panel_url=PANEL_URL, portal="jfsc", **kwargs
    )


class ParseTreeTest(unittest.TestCase):
    def test_anchors_give_id_event_title_and_date(self):
        extra = (
            f"<li>{tree_link(70, 1, 'REPETIDO')}</li>"
            '<li><a href="https://outro.example/eprocV2/controlador.php?acao=acessar_documento&amp;doc=99">X</a></li>'
            '<li><a href="controlador.php?acao=download_documentos_evento&amp;doc=98&amp;evento=1">Baixar</a></li>'
            f"<li>{tree_link(72, 9, 'CERT1', 'CERTIDÃO de 05/11/2026')}</li>"
        )
        documents = arvore.parse_tree(TREE.replace("</ul></li><li>Evento 2", extra + "</ul></li><li>Evento 2"), BASE)
        self.assertEqual([document["id"] for document in documents], ["70", "71", "72", "20"])
        first = documents[0]
        self.assertEqual(
            {key: first[key] for key in ("id", "eventId", "title", "description", "date")},
            {
                "id": "70",
                "eventId": "1",
                "title": "INIC1",
                "description": "PETIÇÃO INICIAL",
                "date": "2026-10-01T09:00:00",
            },
        )
        self.assertEqual(first["url"], BASE + "controlador.php?acao=acessar_documento&doc=70&evento=1&key=k&hash=h70")
        self.assertEqual(documents[2]["date"], "2026-11-05")  # its own date wins over the event's
        self.assertIsNone(documents[3]["description"])
        self.assertEqual(documents[3]["date"], "2026-11-02T10:00:00")

    def test_infra_arvore_script_nodes_are_read(self):
        page = (
            "<script>\n"
            'Nos[0] = new infraArvoreNo("EVENTO","E4",null,"","","Evento 4 - 01/10/2026 09:00:00","Evento 4");\n'
            'Nos[1] = new infraArvoreNo("DOCUMENTO","40","E4","controlador.php?acao=acessar_documento&doc=40'
            '&evento=4&hash=h","ifrVisualizacao","PETIÇÃO INICIAL","INIC1","imagens/pdf.gif");\n'
            "Nos[2] = new infraArvoreNo('DOCUMENTO','41','E4','controlador.php?acao=processo_agravar&doc=41','_blank',"
            "'Agravo','AGRAVO');\n"
            "</script>"
        )
        self.assertEqual(
            [
                {key: value for key, value in document.items() if key != "url"}
                for document in arvore.parse_tree(page, BASE)
            ],
            [
                {
                    "id": "40",
                    "eventId": "4",
                    "title": "INIC1",
                    "description": "PETIÇÃO INICIAL",
                    "date": "2026-10-01T09:00:00",
                }
            ],
        )

    def test_unknown_page_is_an_empty_list(self):
        self.assertEqual(arvore.parse_tree("<html><p>Sem documentos</p></html>", BASE), [])
        self.assertEqual(arvore.parse_tree("", BASE), [])

    def test_tree_link_must_name_this_process(self):
        self.assertEqual(
            arvore.tree_url(TREE_BUTTON, BASE, NUMBER),
            f"{BASE}controlador.php?acao=arvore_documento_listar&txtNumProcesso={DIGITS}&hash=t",
        )
        self.assertIsNone(arvore.tree_url(TREE_BUTTON, BASE, OTHER))
        self.assertIsNone(arvore.tree_url("<p>sem botão</p>", BASE, NUMBER))


class TreeRouteTest(unittest.TestCase):
    def test_tree_is_a_read_action_and_writes_stay_blocked(self):
        url = f"{BASE}controlador.php?acao=arvore_documento_listar&txtNumProcesso={DIGITS}&hash=t"
        self.assertEqual(client.require_route(url, portal="jfsc"), url)
        self.assertEqual(client.require_route(url, {arvore.TREE_ACTION}, portal="jfsc"), url)
        for action in (
            "selecionar_processos_agendar_arquivo_completo",
            "download_documentos_evento",
            "abrir_em_abas_documentos_evento",
            "intimacao_pauta_confirmar",
            "processo_agravar",
            "processo_movimentar",
            "peticionar",
        ):
            with self.subTest(action=action), self.assertRaises(RuntimeError):
                client.require_route(url.replace(arvore.TREE_ACTION, action), portal="jfsc")
        with self.assertRaises(RuntimeError):  # the tree read can never reach a document
            client.require_route(
                url.replace(arvore.TREE_ACTION, "acessar_documento"), {arvore.TREE_ACTION}, portal="jfsc"
            )


class TreeInventoryTest(unittest.TestCase):
    def test_incomplete_timeline_lists_the_tree_without_opening_anything(self):
        fake = portal()
        result = read(fake)
        self.assertEqual(result["inventorySource"], "tree")
        self.assertIn("arvore_documento_listar", fake.actions)
        self.assertEqual(fake.opened, [])
        self.assertEqual([document["id"] for document in result["documentInventory"]], ["20", "10", "70", "71"])
        self.assertEqual(
            result["documentInventory"][2],
            {
                "id": "70",
                "eventId": "1",
                "title": "INIC1",
                "description": "PETIÇÃO INICIAL",
                "date": "2026-10-01T09:00:00",
            },
        )
        self.assertEqual(result["inventory"], {"returned": 4, "total": 4, "next": None})
        self.assertEqual(result["metadata"]["documentCount"], 4)
        self.assertEqual(result["diagnostics"], ["event_refresh_unavailable"])
        self.assertNotIn("hash", str(result["documentInventory"]))

    def test_timeline_mode_and_complete_timeline_do_not_request_the_tree(self):
        for fake, kwargs in ((portal(), {"inventory": "timeline"}), (portal(timeline_complete=True), {})):
            with self.subTest(kwargs=kwargs):
                result = read(fake, **kwargs)
                self.assertNotIn("arvore_documento_listar", fake.actions)
                self.assertEqual(result["inventorySource"], "timeline")
                self.assertEqual([document["id"] for document in result["documentInventory"]], ["20", "10"])

    def test_tree_mode_lists_it_even_with_a_complete_timeline(self):
        fake = portal(timeline_complete=True)
        result = read(fake, inventory="tree")
        self.assertEqual(result["inventorySource"], "tree")
        self.assertEqual(result["inventory"]["total"], 4)

    def test_a_document_only_in_the_tree_is_read_through_the_existing_flow(self):
        fake = portal(timeline_complete=True)
        result = read(fake, ["70"])
        self.assertEqual(result["inventorySource"], "tree")
        self.assertEqual(fake.opened, READ)
        self.assertEqual(result["attachments"][0]["id"], "70")
        self.assertEqual(result["attachments"][0]["extractionStatus"], "extracted")
        self.assertEqual(result["ciencia"]["estado"], "nenhuma")
        # The tree was listed before anything was opened.
        self.assertLess(fake.actions.index("arvore_documento_listar"), fake.actions.index("acessar_documento"))

    def test_tree_documents_go_through_the_ciencia_lock(self):
        pending = portal(citacao_intimacao_pendente_listar=queue(OTHER, NUMBER))
        with self.assertRaises(ciencia.CienciaPending):
            read(pending, ["70"])
        self.assertIn("arvore_documento_listar", pending.actions)
        self.assertEqual(pending.opened, [])
        unchecked = portal(citacao_intimacao_pendente_listar=Reply("erro", status=500))
        with self.assertRaises(ciencia.CienciaUnchecked):
            read(unchecked, ["71"])
        self.assertEqual(unchecked.opened, [])
        acknowledged = portal(citacao_intimacao_pendente_listar=queue(OTHER, NUMBER))
        result = read(acknowledged, ["70"], acknowledge_ciencia=True)
        self.assertEqual(acknowledged.opened, READ)
        self.assertEqual(result["ciencia"]["estado"], "dada")

    def test_a_tree_that_cannot_be_listed_leaves_the_timeline_inventory(self):
        for fake, problem in (
            (portal(tree=Reply("erro", status=500)), "document_tree_failed"),
            (portal(tree="<p>vazio</p>"), "document_tree_empty"),
            (
                Portal(detail=detail(events_url=None), citacao_intimacao_pendente_listar=queue(OTHER)),
                "document_tree_unavailable",
            ),
        ):
            with self.subTest(problem=problem):
                result = read(fake)
                self.assertEqual(result["inventorySource"], "timeline")
                self.assertEqual(result["diagnostics"], ["event_refresh_unavailable", problem])
                self.assertEqual(fake.opened, [])

    def test_tree_answering_the_login_page_expires_the_session(self):
        with self.assertRaises(SessionExpired):
            read(portal(tree='<input name="username">'))

    def test_a_long_inventory_is_shown_in_pages(self):
        tree = "".join(f"<li>{tree_link(1000 + index, 1, f'DOC{index}')}</li>" for index in range(160))
        result = read(portal(tree=tree))
        self.assertEqual(
            result["inventory"], {"returned": 150, "total": 162, "next": {"inventoryOffset": 150, "inventory": "tree"}}
        )
        following = result["inventory"]["next"]
        rest = read(portal(tree=tree), inventory=following["inventory"], inventory_offset=following["inventoryOffset"])
        self.assertEqual(rest["inventory"], {"returned": 12, "total": 162, "next": None})
        self.assertEqual(rest["documentInventory"][-1]["id"], "1159")

    def test_bad_inventory_arguments_are_refused_before_any_request(self):
        for kwargs in ({"inventory": "all"}, {"inventory_offset": -1}, {"inventory_offset": True}):
            fake = portal()
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                read(fake, **kwargs)
            self.assertEqual(fake.actions, [])


if __name__ == "__main__":
    unittest.main()
