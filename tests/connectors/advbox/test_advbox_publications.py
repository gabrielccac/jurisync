import datetime
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from jurisync.connectors.advbox.client import (
    AdvboxClient,
    build_publications_query,
    publication_dates,
    resolve_court,
)
from jurisync.connectors.advbox.parsers import normalize_publications, parse_publication_courts, parse_publication_row
from jurisync.connectors.advbox.session import AdvboxSession

ROW = [
    '<a href="posts/9001/edit" class="link show-event click-row">MARIA DA SILVA</a>',
    "13/10/2026",
    "TRF5",
    "0013886-02.2026.4.05.8303",
    '<img src="https://example.test/files/profiles/321.jpg" class="profile" title="Ana Responsavel">',
    '<a href="posts/9001/edit" class="bt_publication show-event done"><i>done</i> Concluído</a>',
    "&nbsp;<div><a href='posts/9001/edit' title='Ver intimação'></a>"
    '<a href="customers/555/edit" class="icon popup"></a><a href="lawsuits/777/edit" class="icon popup"></a></div>',
]
PENDING_ROW = [*ROW[:2], "&nbsp;", *ROW[3:5], '<a href="posts/9002/edit"><i>description</i> Pendente</a>', ROW[6]]
COURTS_HTML = (
    '<select name="courts_id"><option value="" selected="selected">Selecione</option>'
    '<option value="80">TJPE - TJ - PERNAMBUCO</option>'
    '<option value="152">TJPE PROJUDI - TJ/PROJUDI - PERNAMBUCO</option>'
    '<option value="38">TRF5 - TRF - 5ª REGIAO</option></select>'
)


class ParserTest(unittest.TestCase):
    def test_row_keeps_ids_court_responsible_and_status(self):
        self.assertEqual(
            parse_publication_row(ROW),
            {
                "postId": "9001",
                "customer": {"id": "555", "name": "MARIA DA SILVA"},
                "lawsuitId": "777",
                "date": "13/10/2026",
                "court": "TRF5",
                "processNumber": "0013886-02.2026.4.05.8303",
                "responsible": {"id": "321", "name": "Ana Responsavel"},
                "status": "concluida",
            },
        )

    def test_blank_court_is_none_and_pending_is_the_default_status(self):
        record = parse_publication_row(PENDING_ROW)
        self.assertIsNone(record["court"])
        self.assertEqual(record["status"], "pendente")

    def test_page_reports_totals_and_whether_more_remain(self):
        page = normalize_publications({"data": [ROW, PENDING_ROW], "iTotalDisplayRecords": 5}, 2, page=2)
        self.assertEqual((page["total"], page["totalPages"], page["hasMore"]), (5, 3, True))
        last = normalize_publications({"data": [ROW], "iTotalDisplayRecords": 5}, 2, page=3)
        self.assertFalse(last["hasMore"])

    def test_courts_are_read_from_the_filter_select(self):
        courts = parse_publication_courts(COURTS_HTML)
        self.assertEqual([court["id"] for court in courts], [80, 152, 38])
        self.assertEqual(courts[2]["code"], "TRF5")


class RequestTest(unittest.TestCase):
    def test_presets_become_dates_ending_today(self):
        today = datetime.date(2026, 10, 9)
        self.assertEqual(publication_dates("today", today=today), ("09/10/2026", "09/10/2026"))
        self.assertEqual(publication_dates("3d", today=today), ("06/10/2026", "09/10/2026"))
        self.assertEqual(publication_dates("7d", today=today), ("02/10/2026", "09/10/2026"))
        self.assertEqual(publication_dates("30d", today=today), ("09/09/2026", "09/10/2026"))

    def test_bad_period_input_is_refused(self):
        for kwargs in (
            {},
            {"dateFrom": "01/10/2026"},
            {"period": "7d", "dateFrom": "01/10/2026", "dateTo": "02/10/2026"},
            {"dateFrom": "05/10/2026", "dateTo": "01/10/2026"},
            {"period": "1y"},
        ):
            with self.subTest(kwargs), self.assertRaises(ValueError):
                publication_dates(**kwargs)

    def test_query_carries_what_the_server_requires(self):
        query = parse_qs(
            build_publications_query(
                "tok",
                page=3,
                pageSize=100,
                dateFrom="01/10/2026",
                dateTo="09/10/2026",
                courtId=38,
                processNumber="0013886-02.2026.4.05.8303",
                responsibleId=321,
            )
        )
        flat = {key: value[0] for key, value in query.items()}
        self.assertEqual(flat["iDisplayStart"], "200")
        self.assertEqual(flat["iDisplayLength"], "100")
        self.assertEqual((flat["date_from"], flat["date_until"]), ("01/10/2026", "09/10/2026"))
        self.assertEqual((flat["courts_id"], flat["user"]), ("38", "321"))
        self.assertEqual(flat["process_number"], "0013886-02.2026.4.05.8303")
        self.assertEqual((flat["iColumns"], flat["iSortCol_0"], flat["_token"]), ("7", "5", "tok"))

    def test_page_size_is_capped(self):
        with self.assertRaises(ValueError):
            build_publications_query("tok", pageSize=201, dateFrom="01/10/2026", dateTo="09/10/2026")

    def test_sigla_resolves_exactly_and_ambiguity_lists_options(self):
        courts = parse_publication_courts(COURTS_HTML)
        self.assertEqual(resolve_court(courts, "trf5")["id"], 38)
        self.assertEqual(resolve_court(courts, "TJPE")["id"], 80)
        with self.assertRaisesRegex(ValueError, "TRF5 - TRF"):
            resolve_court(courts, "TRF9")


class ListPublicationsTest(unittest.TestCase):
    def client(self, payload=None, error=None):
        session = mock.Mock(spec=AdvboxSession)
        session.csrf_token.return_value = "tok"
        session.get_html.return_value = COURTS_HTML
        if error:
            session.get_json.side_effect = error
        else:
            session.get_json.return_value = payload
        session.using.side_effect = lambda handler: handler(session)
        return AdvboxClient(session=session), session

    def test_filters_reach_the_request_and_records_are_compact(self):
        client, session = self.client({"data": [ROW], "iTotalDisplayRecords": 1})
        result = client.list_publications(
            dateFrom="01/10/2026", dateTo="09/10/2026", tribunal="TRF5", processNumber="0013886-02.2026.4.05.8303"
        )
        path = session.get_json.call_args[0][0]
        query = parse_qs(urlsplit(path).query)
        self.assertEqual(urlsplit(path).path, "/content/publications")
        self.assertEqual((query["courts_id"], query["date_from"]), (["38"], ["01/10/2026"]))
        self.assertEqual((result["total"], result["records"][0]["postId"]), (1, "9001"))
        self.assertEqual(result["tribunal"]["code"], "TRF5")

    def test_courts_are_fetched_once_and_only_when_a_tribunal_is_asked(self):
        client, session = self.client({"data": [], "iTotalDisplayRecords": 0})
        client.list_publications(period="today")
        session.get_html.assert_not_called()
        client.list_publications(period="today", tribunal="TRF5")
        client.list_publications(period="today", tribunal="TJPE")
        self.assertEqual(session.get_html.call_count, 1)

    def test_account_without_the_module_gets_a_clear_error(self):
        client, _ = self.client(error=RuntimeError("ADVBOX GET /content/publications failed (404)"))
        with self.assertRaisesRegex(RuntimeError, "not enabled for this account"):
            client.list_publications(period="today")

    def test_other_failures_pass_through(self):
        client, _ = self.client(error=RuntimeError("ADVBOX GET /content/publications failed (500)"))
        with self.assertRaisesRegex(RuntimeError, "500"):
            client.list_publications(period="today")


if __name__ == "__main__":
    unittest.main()
