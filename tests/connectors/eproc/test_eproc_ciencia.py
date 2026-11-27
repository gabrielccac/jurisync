"""The ciência block of eproc process reads, over the real parsers and a session faked by URL."""

import json
import unittest
from unittest import mock

from jurisync.connectors.eproc import ciencia, client
from jurisync.core.session_pool import SessionExpired

NUMBER = "5000001-23.2026.4.04.7207"
OTHER = "5000002-08.2026.4.04.7207"
BASE = "https://eproc.jfsc.jus.br/eprocV2/"
PANEL_URL = BASE + "controlador.php?acao=painel_adv_listar&hash=p"
SEARCH = '<form id="formPesquisaRapida" action="controlador.php?acao=processo_pesquisa_rapida&amp;hash=s" method="post"><input name="txtNumProcessoPesquisaRapida"><input name="btnPesquisaRapidaSubmit"><input name="acao_retorno_pesquisa_rapida" value="painel_adv_listar"></form>'
PANEL = SEARCH + '<a href="controlador.php?acao=citacao_intimacao_pendente_listar&amp;hash=q">Pendentes</a>'
EVENTS_URL = "controlador_ajax.php?acao_ajax=montar_tabela_eventos_filtrados&hash=e"
INTIMACAO = "Refer. ao Evento: 2 (AUTOR - Parte de teste) Prazo: 15 dias Status:AGUARD. ABERTURA"


def document(doc, event):
    href = f"controlador.php?acao=acessar_documento&amp;doc={doc}&amp;evento={event}&amp;key=k&amp;hash=h{doc}"
    return f'<a class="infraLinkDocumento" data-doc="{doc}" href="{href}" title="DOC{doc}">DOC{doc}</a>'


def event(number, description, extra="", documents=""):
    return (
        f'<tr id="trEvento{number}"><td>{number}</td><td>0{number}/11/2026 10:00:00</td>'
        f'<td><label class="infraEventoDescricao">{description}</label> {extra}</td>'
        f'<td><label class="infraEventoUsuario">u</label></td><td>{documents}</td></tr>'
    )


def timeline(intimacao=INTIMACAO):
    return (
        '<table id="tblEventos">'
        + event(3, "Expedida/certificada a intimação eletrônica", intimacao)
        + event(2, "Despacho/Decisão", documents=document(20, 2))
        + event(1, "Distribuído por sorteio", documents=document(10, 1))
        + "</table>"
    )


def detail(events_url=EVENTS_URL, intimacao=INTIMACAO):
    script = f"<script>var u = '{events_url}';</script>" if events_url else ""
    return f'<span id="txtNumProcesso">{NUMBER}</span>{script}{timeline(intimacao)}'


def queue(*numbers, total=None):
    rows = "".join(
        '<tr class="infraTrClara"><td><input class="infraCheckbox" value="7"></td>'
        f'<td><a href="controlador.php?acao=processo_selecionar&amp;hash=x">{number}</a></td>'
        "<td>Classe</td><td>Assunto</td><td>Intimação - 15 dias</td><td>03/11/2026 10:00:00</td><td></td><td></td></tr>"
        for number in numbers
    )
    header = "<tr>" + "<th>c</th>" * 8 + "</tr>"
    count = len(numbers) if total is None else total
    return f'<input name="hdnInfraNroItens" value="{count}"><table class="infraTable">{header}{rows}</table>'


class Reply:
    def __init__(self, body="", status=200, headers=None, url=BASE):
        self.status_code = status
        self.content = body.encode("iso-8859-1") if isinstance(body, str) else body
        self.headers = headers or {"Content-Type": "text/html"}
        self.url = url

    def iter_content(self, *_args, **_kwargs):
        return iter([self.content])

    def close(self):
        pass


class Portal:
    """A session that answers by eproc action, so a test can say what was (not) requested."""

    def __init__(self, **pages):
        self.pages = {"detail": detail(), "events": timeline(), **pages}
        self.actions = []

    def request(self, method, url, **_kwargs):
        action = url.split("acao_ajax=" if "acao_ajax=" in url else "acao=")[1].split("&")[0]
        self.actions.append(action)
        if action == "processo_pesquisa_rapida":
            return Reply(status=302, headers={"Location": "controlador.php?acao=processo_selecionar&hash=d"})
        if action in ("processo_selecionar", "montar_tabela_eventos_filtrados"):
            action = {"processo_selecionar": "detail"}.get(action, "events")
        if action == "acessar_documento":
            doc = url.split("doc=")[1].split("&")[0]
            target = f"{BASE}controlador.php?acao=acessar_documento_implementacao&doc={doc}&hash=i"
            return Reply(f'<iframe id="conteudoIframe" src="{target}"></iframe>')
        if action == "acessar_documento_implementacao":
            return Reply(f"<html><p>{NUMBER}</p><p>teor do documento</p></html>")
        page = self.pages[action]
        return page if isinstance(page, Reply) else Reply(page)

    @property
    def opened(self):
        return [action for action in self.actions if action.startswith("acessar_documento")]

    def close(self):
        pass


def read(portal, document_ids, *, panel=PANEL, **kwargs):
    return client.fetch_process(
        NUMBER, "page", document_ids, session=portal, panel_html=panel, panel_url=PANEL_URL, portal="jfsc", **kwargs
    )


READ = ["acessar_documento", "acessar_documento_implementacao"]


class CienciaNoticeTest(unittest.TestCase):
    def test_no_pending_intimacao_reads_and_says_none(self):
        portal = Portal(citacao_intimacao_pendente_listar=queue(OTHER))
        result = read(portal, ["20"])
        self.assertEqual(result["attachments"][0]["extractionStatus"], "extracted")
        self.assertIn("teor do documento", result["attachments"][0]["text"])
        self.assertEqual(portal.opened, READ)
        self.assertEqual(result["ciencia"], {"estado": "nenhuma"})
        # An empty queue that says it is empty is "nothing pending" too.
        result = read(Portal(citacao_intimacao_pendente_listar=queue()), ["10"])
        self.assertEqual(len(result["attachments"]), 1)
        self.assertEqual(result["ciencia"], {"estado": "nenhuma"})

    def test_without_document_ids_nothing_is_opened_and_a_pending_intimacao_is_announced(self):
        portal = Portal(citacao_intimacao_pendente_listar=queue(NUMBER))
        result = read(portal, None)
        self.assertTrue(result["success"])
        self.assertEqual(portal.opened, [])
        notice = result["ciencia"]
        self.assertEqual(notice["estado"], "pendente")
        self.assertEqual(notice["intimacoes"][0]["queue"], "pending")
        self.assertIn("nenhum documento foi aberto", notice["aviso"])
        self.assertNotIn("documentosDaIntimacao", notice)
        result = read(Portal(citacao_intimacao_pendente_listar=queue(OTHER)), None)
        self.assertEqual(result["ciencia"], {"estado": "nenhuma"})
        result = read(Portal(citacao_intimacao_pendente_listar=Reply("erro", status=500)), None)
        self.assertEqual(result["ciencia"]["estado"], "nenhuma")
        self.assertIn("Não foi possível verificar", result["ciencia"]["aviso"])
        self.assertTrue(result["ciencia"]["motivo"])

    def test_an_intimacao_awaiting_the_djen_says_so_and_the_read_lists_its_deadlines(self):
        waiting = queue(NUMBER).replace("<td></td><td></td></tr>", "<td>Aguarda publicação no DJEN</td><td></td></tr>")
        result = read(Portal(citacao_intimacao_pendente_listar=waiting), None)
        self.assertEqual(result["ciencia"]["estado"], "pendente")
        self.assertIn("aguarda a publicação no DJEN", result["ciencia"]["aviso"])
        self.assertEqual([item["eventId"] for item in result["prazos"]], ["3"])
        self.assertEqual(result["prazos"][0]["status"], "aguardando_abertura")
        result = read(Portal(citacao_intimacao_pendente_listar=queue(NUMBER)), None)
        self.assertNotIn("DJEN", result["ciencia"]["aviso"])

    def test_pending_queues_are_read_once_per_session_until_a_document_is_opened(self):
        portal = Portal(citacao_intimacao_pendente_listar=queue(NUMBER))

        def reads():
            return portal.actions.count("citacao_intimacao_pendente_listar")

        with mock.patch.object(client, "clock", return_value=1000.0):
            for _ in range(3):
                self.assertEqual(read(portal, None)["ciencia"]["estado"], "pendente")
            self.assertEqual(reads(), 1)
            self.assertEqual(read(portal, ["20"])["ciencia"]["estado"], "dada")  # the memo answers, then is dropped
            self.assertEqual(reads(), 1)
            read(portal, None)
            self.assertEqual(reads(), 2)
            other = Portal(citacao_intimacao_pendente_listar=queue())
            read(other, None)  # another session (portal or office) reads its own
            self.assertEqual(other.actions.count("citacao_intimacao_pendente_listar"), 1)
        with mock.patch.object(client, "clock", return_value=1000.0 + client.PENDING_TTL):
            read(portal, None)
        self.assertEqual(reads(), 3)
        # A failed reading is not kept: the next call tries again.
        failing = Portal(citacao_intimacao_pendente_listar=Reply("erro", status=500))
        read(failing, None)
        read(failing, None)
        self.assertEqual(failing.actions.count("citacao_intimacao_pendente_listar"), 2)

    def test_a_queue_answering_the_login_page_expires_the_session_before_anything_opens(self):
        portal = Portal(citacao_intimacao_pendente_listar='<form><input name="username"></form>')
        with self.assertRaises(SessionExpired):
            read(portal, ["10"])
        self.assertEqual(portal.opened, [])

    def test_pending_intimacao_reads_and_tells_the_user(self):
        portal = Portal(citacao_intimacao_pendente_listar=queue(OTHER, NUMBER))
        result = read(portal, ["20"])
        self.assertEqual(portal.opened, READ)
        self.assertEqual(result["attachments"][0]["extractionStatus"], "extracted")
        notice = result["ciencia"]
        self.assertEqual(notice["estado"], "dada")
        self.assertEqual(
            notice["intimacoes"],
            [
                {
                    "queue": "pending",
                    "deadlineEvent": "Intimação - 15 dias",
                    "requestedAt": "03/11/2026 10:00:00",
                    "deadlineStart": "",
                    "deadlineEnd": "",
                }
            ],
        )
        self.assertEqual(notice["eventos"][0]["eventId"], "3")
        self.assertNotIn("documentosDaIntimacao", notice)  # the event link is not confirmed
        self.assertEqual(
            notice["aviso"],
            f"Este processo ({NUMBER}) tinha intimação aguardando ciência (Intimação - 15 dias, expedida em "
            "03/11/2026 10:00:00; na linha do tempo: evento 3 de 03/11/2026 10:00:00, referente ao evento 2); "
            "abrir os documentos pode ter registrado a ciência e iniciado o prazo de 15 dias. Informe o advogado.",
        )

    def test_notice_names_the_requested_documents_of_the_event_when_the_link_is_provable(self):
        pending = {"citacao_intimacao_pendente_listar": queue(NUMBER)}
        with mock.patch.object(ciencia, "EVENT_LINK_CONFIRMED", True):
            for ids, tied in ((["10"], []), (["20"], ["20"]), (["10", "20"], ["20"])):
                with self.subTest(ids=ids):
                    result = read(Portal(**pending), ids)
                    self.assertEqual(result["ciencia"]["documentosDaIntimacao"], tied)
                    self.assertEqual(len(result["attachments"]), len(ids))
            result = read(Portal(**pending, detail=detail(events_url=None)), ["20"])
            self.assertNotIn("documentosDaIntimacao", result["ciencia"])  # timeline not fully loaded

    def test_unreadable_pending_state_reads_and_says_it_was_not_verified(self):
        cases = {
            "queue answers an error": ({"citacao_intimacao_pendente_listar": Reply("erro", status=500)}, PANEL),
            "queue has more rows than were read": (
                {"citacao_intimacao_pendente_listar": queue(OTHER, total=40)},
                PANEL,
            ),
            "queue page without a total": (
                {"citacao_intimacao_pendente_listar": queue(OTHER).replace("hdnInfraNroItens", "x")},
                PANEL,
            ),
            "panel does not show the pending queue": ({}, SEARCH),
            "pending queue link leaves the allowlist": (
                {},
                SEARCH + '<a href="https://evil.invalid/controlador.php?acao=citacao_intimacao_pendente_listar">x</a>',
            ),
        }
        for reason, (pages, panel) in cases.items():
            with self.subTest(reason=reason):
                portal = Portal(**pages)
                result = read(portal, ["10"], panel=panel)
                self.assertEqual(portal.opened, READ)
                notice = result["ciencia"]
                self.assertEqual(notice["estado"], "desconhecida")
                self.assertTrue(notice["motivo"])
                self.assertIn("Não foi possível verificar", notice["aviso"])
                self.assertIn("Informe o advogado", notice["aviso"])

    def test_every_pending_queue_the_panel_shows_is_checked(self):
        panel = PANEL + '<a href="controlador.php?acao=citacao_intimacao_pendente_listar_urgente&amp;hash=u">U</a>'
        portal = Portal(
            citacao_intimacao_pendente_listar=queue(), citacao_intimacao_pendente_listar_urgente=queue(NUMBER)
        )
        self.assertEqual(read(portal, ["10"], panel=panel)["ciencia"]["intimacoes"][0]["queue"], "pending_urgent")
        # A kind the panel shows but that cannot be read leaves the state unknown.
        portal = Portal(
            citacao_intimacao_pendente_listar=queue(),
            citacao_intimacao_pendente_listar_urgente=Reply("erro", status=500),
        )
        self.assertEqual(read(portal, ["10"], panel=panel)["ciencia"]["estado"], "desconhecida")

    def test_queue_links_of_a_state_panel_are_read_once(self):
        """TJSP-style panels publish the queue links through one ajax state payload instead of static links."""
        panel = SEARCH + (
            '<a data-urlassinadacarregamento="controlador_ajax.php?acao_ajax=retornar_citacao_intimacao_estado'
            '&amp;hash=st"></a>'
        )
        links = {
            "intimacaoPendente": "controlador.php?acao=citacao_intimacao_pendente_listar&hash=b",
            "citacaoArt334": "controlador.php?acao=citacao_art334_pendente_listar&hash=c",
            "intimacaoPendenteUrgente": "controlador.php?acao=sair&hash=x",  # not the queue it claims: ignored
        }
        state = json.dumps({"usuarios": [{"links": links}]})
        portal = Portal(
            retornar_citacao_intimacao_estado=state,
            citacao_intimacao_pendente_listar=queue(),
            citacao_art334_pendente_listar=queue(NUMBER),
        )
        self.assertEqual(read(portal, ["10"], panel=panel)["ciencia"]["intimacoes"][0]["queue"], "art334")
        self.assertEqual(portal.actions.count("retornar_citacao_intimacao_estado"), 1)
        self.assertNotIn("sair", portal.actions)
        portal = Portal(retornar_citacao_intimacao_estado=Reply("erro", status=500))
        self.assertEqual(
            read(portal, ["10"], panel=panel)["ciencia"]["motivo"], "the panel did not expose the pending queue"
        )


# The process page's "Informações Adicionais" legend and action buttons (TJMG and JFRS, 27/11/2026).
EXTRAS = (
    '<legend id="legInfAdicional" onclick="carregarInformacoesAdicionais(\'controlador.php?acao=processo_montar_'
    "informacoes_adicionais&num_processo=1&do_log=0&hash=i', {\n    'id_pessoa_entidade': '1,2',\n    'pagina': '',\n"
    '    \'ia_aberta\': false\n});">Informações Adicionais</legend><div id="fldInformacoesAdicionais_content"></div>'
    '<a class="infraButton" href="controlador.php?acao=audiencia_listar&amp;num_processo=1&amp;hash=a">Audiência</a>'
    '<a class="infraButton" href="controlador.php?acao=exame_listar&amp;num_processo=1&amp;hash=x">Perícia</a>'
)
INFORMATION = (
    '<div class="row"><div class="col"><div class="row"><span class="col">Justiça Gratuita:</span>'
    '<span class="col font-weight-bold"> Deferida</span></div></div><div class="col"><div class="row">'
    '<span class="col">Valor da Causa:</span><span class="col"> R$ 1.000,00 </span></div></div>'
    '<div class="col"><div class="row"><span class="col">Chave Processo:</span><span class="col"></span></div></div></div>'
)
HEARINGS = (
    '<table class="infraTable"><caption>Lista de Audiências (1 registro):</caption><tr><th>Sala</th>'
    "<th>Data/Hora Início</th><th>Data/Hora Fim</th><th>Descrição da Audiência</th><th>Magistrado</th>"
    '<th>Realizador</th><th>Situação</th></tr><tr class="infraTrClara"><td>Sala 1</td><td>12/05/2027 15:30</td>'
    '<td>12/05/2027 16:30</td><td><td>JUIZ DE TESTE<td><input type="hidden" value="" /></td><td>DESIGNADA</td></tr>'
    "</table>"
)
EXAMS = (
    '<table class="infraTable" id="tblExamesProcesso"><tr><th>Periciado</th><th>Data-Hora designada</th><th>Sala</th>'
    '<th>Perito</th><th>Especialidade</th></tr><tr class="infraTrClara"><td>FULANO</td>'
    "<td>08/01/2027  08:40 -  09:00</td><td>Sala de Perícias 01</td><td>PERITO TESTE</td><td>Médico</td></tr></table>"
)


class ProcessExtrasTest(unittest.TestCase):
    def test_additional_information_hearings_and_exams_come_with_the_process(self):
        portal = Portal(
            detail=detail() + EXTRAS,
            citacao_intimacao_pendente_listar=queue(OTHER),
            processo_montar_informacoes_adicionais=INFORMATION,
            audiencia_listar=HEARINGS,
            exame_listar=EXAMS,
        )
        result = read(portal, None)
        self.assertTrue(result["success"])
        self.assertEqual(
            result["metadata"]["additionalInformation"],
            {"Justiça Gratuita": "Deferida", "Valor da Causa": "R$ 1.000,00"},
        )
        self.assertEqual(
            result["audiencias"],
            [
                {
                    "location": "Sala 1",
                    "start": "12/05/2027 15:30",
                    "end": "12/05/2027 16:30",
                    "description": "",
                    "judge": "JUIZ DE TESTE",
                    "conductedBy": "",
                    "status": "DESIGNADA",
                }
            ],
        )
        self.assertEqual(result["pericias"][0]["scheduledAt"], "08/01/2027 08:40 - 09:00")
        self.assertEqual(result["pericias"][0]["specialty"], "Médico")
        self.assertEqual(portal.opened, [])
        self.assertEqual(result["diagnostics"], [])

    def test_a_process_without_the_buttons_asks_nothing_more(self):
        portal = Portal(citacao_intimacao_pendente_listar=queue(OTHER))
        result = read(portal, None)
        self.assertEqual((result["audiencias"], result["pericias"]), (None, None))
        self.assertEqual(result["metadata"]["additionalInformation"], {})
        self.assertNotIn("audiencia_listar", portal.actions)

    def test_a_failing_list_is_a_diagnostic_and_a_login_page_expires_the_session(self):
        portal = Portal(
            detail=detail() + EXTRAS,
            citacao_intimacao_pendente_listar=queue(OTHER),
            processo_montar_informacoes_adicionais=Reply("erro", status=500),
            audiencia_listar=HEARINGS,
            exame_listar=EXAMS,
        )
        result = read(portal, None)
        self.assertEqual(result["diagnostics"], ["additional_information_failed"])
        self.assertEqual(len(result["audiencias"]), 1)
        expired = Portal(
            detail=detail() + EXTRAS,
            processo_montar_informacoes_adicionais='<input name="username">',
        )
        with self.assertRaises(SessionExpired):
            read(expired, ["20"])
        self.assertEqual(expired.opened, [])


class TimelineMarkerTest(unittest.TestCase):
    def test_awaiting_events_read_status_reference_and_days(self):
        events = [
            {
                "id": "5",
                "dateTime": "05/11",
                "description": "Intimação Refer. aos Eventos: 3 e 4 Prazo: 5 dias Status: AGUARDANDO ABERTURA",
            },
            {"id": "4", "description": "Intimação Refer. ao Evento: 2 Prazo: 15 dias Status:ABERTO"},
            {"id": "3", "description": "Despacho"},
        ]
        self.assertEqual(
            ciencia.awaiting_events(events),
            [{"eventId": "5", "dateTime": "05/11", "label": "", "refersTo": ["3", "4"], "deadlineDays": 5}],
        )
        self.assertEqual(ciencia.awaiting_events(None), [])

    def test_reference_reads_the_event_with_or_without_colon(self):
        for reference in ("Refer. ao Evento 6 (AUTOR - Parte de teste)", "Refer. ao Evento: 6", "Refer. ao Evento:6"):
            with self.subTest(reference=reference):
                events = [
                    {
                        "id": "7",
                        "description": f"Expedida/certificada a intimação eletrônica {reference} Status:AGUARD. ABERTURA",
                    }
                ]
                self.assertEqual(ciencia.awaiting_events(events)[0]["refersTo"], ["6"])

    def test_deadlines_read_the_live_event_format_with_its_djen_dates(self):
        # TJMG and JFRS timelines, 27/11/2026 (names replaced): an open deadline, one closed by its decurso, a perito's
        # deadline without days ("Status do Prazo"), and the DJEN events that refer to them.
        events = [
            {"id": "43", "description": "Publicado no DJEN - no dia 12/11/2026 - Refer. ao Evento: 41"},
            {"id": "42", "description": "Disponibilizado no DJEN - no dia 11/11/2026 - Refer. ao Evento: 41"},
            {
                "id": "41",
                "classes": ["infraTrClara", "infraEventoPrazoAberto"],
                "description": "Expedida/certificada a intimação eletrônica Refer. ao Evento 40 ( AUTOR - FULANO DE TAL) "
                "Prazo: 10 dias Status:ABERTO Data inicial da contagem do prazo: 13/11/2026 00:00:00 "
                "Data final: 27/11/2026 23:59:59",
            },
            {"id": "37", "description": "Decorrido prazo - Refer. aos Eventos: 33 e 34"},
            {"id": "35", "description": "Disponibilizado no DJEN - no dia 03/09/2026 - Refer. aos Eventos: 33, 34"},
            {
                "id": "34",
                "description": "Expedida/certificada a intimação eletrônica - URGENTE Refer. ao Evento 14 e ao Evento 15 "
                "( RÉU - EMPRESA X LTDA) Prazo: 15 dias Status:FECHADO ( 37 - Decorrido prazo ) Data inicial da "
                "contagem do prazo: 08/09/2026 00:00:00 Data final: 28/09/2026 23:59:59",
            },
            {
                "id": "18",
                "description": "Expedida/certificada a intimação eletrônica - Perito Refer. ( PERITO - BELTRANO) Status "
                "do Prazo: ABERTO Data inicial da contagem do prazo: 06/11/2026 00:00:00 Data final: 22/01/2027 23:59:59",
            },
            {"id": "2", "description": "Despacho"},
        ]
        found = ciencia.deadlines(events)
        self.assertEqual([item["eventId"] for item in found], ["18", "34", "41"])
        perito, closed, running = found
        self.assertEqual(
            {key: running[key] for key in ("refersTo", "party", "days", "status", "closedBy", "start", "end")},
            {
                "refersTo": ["40"],
                "party": "AUTOR",
                "days": 10,
                "status": "aberto",
                "closedBy": None,
                "start": "2026-11-13",
                "end": "2026-11-27",
            },
        )
        self.assertEqual((running["djenAvailable"], running["djenPublished"]), ("2026-11-11", "2026-11-12"))
        self.assertEqual(
            (
                closed["refersTo"],
                closed["status"],
                closed["closedBy"],
                closed["djenAvailable"],
                closed["djenPublished"],
            ),
            (["14", "15"], "fechado", "37 - Decorrido prazo", "2026-09-03", None),
        )
        self.assertEqual(
            (perito["party"], perito["days"], perito["status"], perito["end"]), ("PERITO", None, "aberto", "2027-01-22")
        )
        self.assertEqual(ciencia.awaiting_events(events), [])  # every deadline was opened already

    def test_a_deadline_neither_open_nor_closed_still_awaits_opening(self):
        events = [
            {
                "id": "9",
                "description": "Expedida/certificada a intimação eletrônica Refer. ao Evento 8 ( AUTOR - FULANO) Prazo: "
                "15 dias Status:AGUARDANDO",
            }
        ]
        self.assertEqual(ciencia.deadlines(events)[0]["status"], "aguardando")
        self.assertEqual([marker["eventId"] for marker in ciencia.awaiting_events(events)], ["9"])
        self.assertEqual(ciencia.awaiting_events(events)[0]["refersTo"], ["8"])

    def test_tied_documents_need_the_confirmed_link(self):
        events = [
            {"id": "5", "description": "Refer. ao Evento: 3 Status:AGUARD. ABERTURA", "documents": [{"id": "50"}]},
            {"id": "3", "description": "Despacho", "documents": [{"id": "30"}, {"id": "31"}]},
        ]
        markers = ciencia.awaiting_events(events)
        self.assertIsNone(ciencia.tied_documents(events, markers))
        with mock.patch.object(ciencia, "EVENT_LINK_CONFIRMED", True):
            self.assertEqual(ciencia.tied_documents(events, markers), {"50", "30", "31"})
            self.assertIsNone(ciencia.tied_documents(events, []))


class CliTest(unittest.TestCase):
    def test_cli_reads_documents_and_offers_every_queue(self):
        seen = {}

        def fetch(*args, **kwargs):
            seen.update(kwargs, args=args)
            return {"success": True}

        argv = ["eproc", "process", "--portal", "trf4-1g-sc", "--process-number", NUMBER, "--document-id", "9"]
        with (
            mock.patch.object(client, "fetch_process", side_effect=fetch),
            mock.patch.object(client, "load_env_file"),
            mock.patch.object(client, "enable_request_logging"),
            mock.patch("builtins.print"),
        ):
            with mock.patch("sys.argv", argv):
                client.main()
            self.assertEqual(seen["args"], (NUMBER, "page", ["9"]))
            self.assertNotIn("acknowledge_ciencia", seen)
            with (
                mock.patch.object(client, "list_notices", return_value={"success": True}) as notices,
                mock.patch("sys.argv", ["eproc", "queue", "--portal", "trf4-1g-sc", "--queue", "hearing_notice"]),
            ):
                client.main()
            notices.assert_called_once_with("hearing_notice", portal="trf4-1g-sc")


if __name__ == "__main__":
    unittest.main()
