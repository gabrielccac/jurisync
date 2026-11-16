"""The deadline calculator: generated base calendar, local court calendars and the CPC art. 220 suspension."""

import unittest
from datetime import date

from jurisync.connectors.djen import client, deadlines
from jurisync.connectors.djen.deadlines import base_closure, calculate_deadline, easter, in_cpc_220


class BaseCalendar(unittest.TestCase):
    def test_easter_is_computed_for_any_year(self):
        known = {2024: (3, 31), 2025: (4, 20), 2026: (4, 5), 2027: (3, 28), 2028: (4, 16), 2038: (4, 25)}
        for year, (month, day) in known.items():
            self.assertEqual(easter(year), date(year, month, day))

    def test_national_holidays_close_every_court(self):
        for month, day in ((1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)):
            self.assertIsNotNone(base_closure(date(2027, month, day), federal=False), (month, day))
        self.assertEqual(base_closure(date(2027, 3, 26), federal=False), "Sexta-feira Santa")
        self.assertIsNone(base_closure(date(2027, 3, 23), federal=False))

    def test_federal_justice_days_of_lei_5010(self):
        closed = [
            date(2027, 2, 8),  # Carnival Monday
            date(2027, 2, 9),  # Carnival Tuesday
            date(2027, 3, 24),  # Holy Wednesday
            date(2027, 3, 25),
            date(2027, 3, 26),
            date(2027, 5, 27),  # Corpus Christi
            date(2027, 8, 11),
            date(2027, 11, 1),
            date(2027, 12, 8),
            date(2026, 12, 21),  # recess
            date(2027, 1, 6),
        ]
        for day in closed:
            self.assertIsNotNone(base_closure(day), day)
        for day in (date(2027, 2, 10), date(2027, 3, 23), date(2027, 1, 7), date(2026, 12, 18)):
            self.assertIsNone(base_closure(day), day)
        # A state court only gets the national layer; its own closures need a local calendar.
        for day in (date(2027, 2, 8), date(2027, 3, 24), date(2027, 5, 27), date(2027, 8, 11), date(2026, 12, 21)):
            self.assertIsNone(base_closure(day, federal=False), day)

    def test_cpc_220_window_is_inclusive(self):
        self.assertFalse(in_cpc_220(date(2026, 12, 19)))
        self.assertTrue(in_cpc_220(date(2026, 12, 20)))
        self.assertTrue(in_cpc_220(date(2027, 1, 20)))
        self.assertFalse(in_cpc_220(date(2027, 1, 21)))


class Deadlines(unittest.TestCase):
    def test_inside_the_local_calendar_the_result_is_unchanged_and_complete(self):
        result = calculate_deadline("2026-10-06", 10, "jfpe-2026-q4")
        self.assertEqual((result["publicationOn"], result["startsOn"]), ("2026-10-07", "2026-10-08"))
        self.assertEqual(result["dueOn"], "2026-10-22")  # 12/10 is not counted
        self.assertEqual(result["calendar"]["id"], "jfpe-2026-q4")
        self.assertTrue(result["calendarComplete"])
        self.assertEqual(result["warnings"], [])
        # The local file adds 30/10 (Dia do Servidor moved by Portaria JFPE 173/2026) to the base layer.
        local = calculate_deadline("2026-10-27", 1, court="JFPE")
        other = calculate_deadline("2026-10-27", 1, court="JFPB")
        self.assertEqual((local["dueOn"], other["dueOn"]), ("2026-10-29", "2026-10-29"))
        self.assertEqual(calculate_deadline("2026-10-27", 2, court="JFPE")["dueOn"], "2026-11-03")
        self.assertEqual(calculate_deadline("2026-10-27", 2, court="JFPB")["dueOn"], "2026-10-30")

    def test_term_crossing_the_year_skips_the_suspension(self):
        result = calculate_deadline("2026-12-07", 15, court="JFPE")
        self.assertEqual((result["publicationOn"], result["startsOn"]), ("2026-12-09", "2026-12-10"))  # 08/12 closed
        counted = result["countedDates"]
        self.assertEqual(counted[:7][-1], "2026-12-18")
        self.assertEqual(counted[7], "2027-01-21")
        self.assertFalse(any("2026-12-20" <= day <= "2027-01-20" for day in counted))
        self.assertEqual(result["dueOn"], "2027-02-01")
        self.assertTrue(result["cpc220Suspension"])
        self.assertFalse(result["calendarComplete"])
        self.assertTrue(any("JFPE após 19/12/2026" in warning for warning in result["warnings"]))

    def test_thirty_days_from_today_no_longer_fails(self):
        result = calculate_deadline("2026-11-09", 30, court="JFPE")
        self.assertEqual(result["startsOn"], "2026-11-11")
        self.assertNotIn("2026-11-20", result["countedDates"])
        self.assertNotIn("2026-12-08", result["countedDates"])
        self.assertEqual(result["dueOn"], "2027-01-26")
        self.assertFalse(result["calendarComplete"])

    def test_availability_inside_the_suspension_starts_on_21_january(self):
        recess = calculate_deadline("2026-12-22", 5, court="JFPE")
        self.assertEqual((recess["publicationOn"], recess["startsOn"]), ("2027-01-07", "2027-01-21"))
        self.assertEqual(recess["dueOn"], "2027-01-27")
        late = calculate_deadline("2027-01-19", 5, court="JFPE")
        self.assertEqual((late["publicationOn"], late["startsOn"]), ("2027-01-20", "2027-01-21"))

    def test_carnival_and_holy_week_2027(self):
        carnival = calculate_deadline("2027-02-03", 5, court="JFPE")  # publication 04/02, starts 05/02
        self.assertEqual(
            carnival["countedDates"], ["2027-02-05", "2027-02-10", "2027-02-11", "2027-02-12", "2027-02-15"]
        )
        holy = calculate_deadline("2027-03-19", 5, court="TRF5")  # publication 22/03, starts 23/03
        self.assertEqual(holy["countedDates"], ["2027-03-23", "2027-03-29", "2027-03-30", "2027-03-31", "2027-04-01"])
        state = calculate_deadline("2027-03-19", 5, court="TJPE")
        self.assertEqual(state["dueOn"], "2027-03-30")  # only Good Friday is in the national layer

    def test_court_without_local_calendar_is_computed_and_flagged(self):
        result = calculate_deadline("2026-10-06", 10, court="tjsp")
        self.assertEqual(result["court"], "TJSP")
        self.assertEqual(result["dueOn"], "2026-10-22")
        self.assertEqual(result["calendar"]["id"], "base")
        self.assertEqual(result["localCalendars"], [])
        self.assertFalse(result["calendarComplete"])
        self.assertTrue(any("Feriados locais e suspensões de expediente de TJSP" in w for w in result["warnings"]))
        self.assertTrue(any("não é da Justiça Federal" in w for w in result["warnings"]))

    def test_no_court_falls_back_to_jfpe_and_says_so(self):
        result = calculate_deadline("2026-10-06", 10)
        self.assertEqual(result["court"], "JFPE")
        self.assertTrue(result["calendarComplete"])
        self.assertTrue(any("Nenhum tribunal informado" in w for w in result["warnings"]))

    def test_refusals(self):
        for arguments in (("2025-01-01", 5), ("2026-10-06", 0), ("2026-10-06", 5, "nope"), ("06/10/2026", 5)):
            with self.assertRaises(ValueError):
                calculate_deadline(*arguments)
        with self.assertRaises(ValueError):
            calculate_deadline("2026-10-06", 5, court="../x")

    def test_local_calendar_agrees_with_the_base_layer(self):
        (calendar,) = deadlines.local_calendars("JFPE")
        only_local = [day for day in calendar["_closed"] if base_closure(day) is None]
        self.assertEqual(only_local, [date(2026, 10, 30)])


class SearchPageErrors(unittest.TestCase):
    def test_api_error_with_http_200_passes_the_message_through(self):
        from unittest.mock import patch

        payload = {"status": "error", "message": "Parâmetros inválidos: janela máxima excedida"}
        with patch.object(client, "_read_json", return_value=(payload, 200)) as read:
            with self.assertRaisesRegex(client.DjenError, "API error: Parâmetros inválidos"):
                client.search_page({"nomeParte": "FULANO"})
        self.assertEqual(read.call_count, 1)  # a refusal is not retried

    def test_window_span(self):
        client._check_window("2026-10-01", "2026-10-31", 31)
        with self.assertRaisesRegex(ValueError, "máximo é 31 dias"):
            client._check_window("2026-10-01", "2026-11-01", 31)


if __name__ == "__main__":
    unittest.main()
