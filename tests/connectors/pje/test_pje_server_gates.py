"""The PJe MCP server's own gates: draft writes need PJE_ALLOW_DRAFT_WRITES, and a pending ciência is only given
when the user agreed (acknowledgeCiencia, or the customer's ciencia_on_read = 'allow')."""

import asyncio
import contextlib
import copy
import os
import tempfile
import typing
import unittest
from unittest import mock

from mcp.client.client import Client

from jurisync.connectors.pje import expedientes, search, server
from jurisync.core import credentials
from jurisync.core.session_pool import SessionExpired, SessionPool

NUMBER = "0003597-10.2026.4.05.8303"
URL = "https://pje1g.trf5.jus.br/pje/seam/resource/rest/pje-legacy/documento/download/TRF5/token/123/456"
CERTIDAO = (
    f"<html><p>PROCESSO : {NUMBER} EXPEDIENTES GERADOS Parte Documento Meio Prazo Pessoal "
    "LAERCIO JOSE (456 - Intimação) Diário Eletrônico 15 dias Não "
    "O(s) documento(s) ficará(ão) disponível(is)</p></html>"
).encode()
PENDING = {
    "expedienteId": "9",
    "documentId": "456",
    "tipo": "Intimação",
    "parte": "AUTORA",
    "prazoDias": 15,
    "limite": "2026-10-30",
    "ciencia": None,
    "atoPath": "/pje/Painel/painel_usuario/popup/visualizarExpediente.seam?idProcessoParteExpediente=9&x=2",
}
TIMELINE = [
    {"date": "05 out. 2026", "movements": [{"documents": [{"id": "456", "title": "Intimação", "download_url": URL}]}]}
]


def call(name, arguments):
    async def run():
        async with Client(server.server) as client:
            return await client.call_tool(name, arguments)

    result = asyncio.run(run())
    return result.content[0].text if result.is_error else result.structured_content, result.is_error


class PjeServerCase(unittest.TestCase):
    def setUp(self):
        self.session = mock.MagicMock()
        self.logins = []
        pool = SessionPool(
            login=lambda key: self.logins.append(key) or self.session,
            refresh=lambda _key, session: session,
            close=lambda _session: None,
        )
        patcher = mock.patch.object(server, "sessions", pool)
        patcher.start()
        self.addCleanup(patcher.stop)
        setting = mock.patch.object(server, "ciencia_on_read", return_value="ask")
        self.ciencia_on_read = setting.start()
        self.addCleanup(setting.stop)


class DraftWriteGateTest(PjeServerCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = mock.patch.dict(os.environ, {"JURISYNC_FILING_DIR": self.directory.name, "PJE_ALLOW_DRAFT_WRITES": ""})
        env.start()
        self.addCleanup(env.stop)
        stage = mock.patch.object(server.filing, "run_stage", return_value={"ok": True})
        self.run_stage = stage.start()
        self.addCleanup(stage.stop)

    def test_every_write_stage_and_the_delete_are_refused_without_the_flag(self):
        calls = [
            ("pje_draft_write", {"runId": "run1", "stage": stage}) for stage in typing.get_args(server.DraftWriteStage)
        ]
        calls.append(("pje_draft_delete", {"draftId": "123", "authorName": "EXAMPLE"}))
        jobs = dict(server.fill_jobs)
        # Locally the flag is unset; hosted, an office without it does not inherit the server's own "true".
        for server_flag, office in (
            ("", None),
            ("false", None),
            ("true", {}),
            ("true", {"PJE_ALLOW_DRAFT_WRITES": "no"}),
        ):
            for name, arguments in calls:
                with self.subTest(server_flag=server_flag, office=office, tool=name, stage=arguments.get("stage")):
                    scope = credentials.scope("office-a", office) if office is not None else contextlib.nullcontext()
                    with mock.patch.dict(os.environ, {"PJE_ALLOW_DRAFT_WRITES": server_flag}), scope:
                        text, error = call(name, arguments)
                    self.assertTrue(error)
                    self.assertIn("PJE_ALLOW_DRAFT_WRITES=true", text)
        self.run_stage.assert_not_called()
        self.assertEqual(self.logins, [])
        self.assertEqual(server.fill_jobs, jobs)  # no background fill was started

    def test_reads_still_work_and_a_granted_write_is_never_repeated(self):
        _, error = call("pje_search_pending_drafts", {"name": "EXAMPLE"})
        self.assertFalse(error)
        self.assertEqual(self.run_stage.call_args.args[0], "pending-search")

        # A write runs once on the pooled session (sessions.session), never through sessions.run's retry.
        self.run_stage.reset_mock()
        self.run_stage.side_effect = SessionExpired("expired mid-write")
        with mock.patch.dict(os.environ, {"PJE_ALLOW_DRAFT_WRITES": "true"}):
            text, error = call("pje_draft_delete", {"draftId": "123", "authorName": "EXAMPLE"})
        self.assertTrue(error, text)
        self.assertEqual(self.run_stage.call_count, 1)
        self.assertEqual(self.run_stage.call_args.args[0], "delete-draft")
        self.assertEqual(self.run_stage.call_args.kwargs["draft_id"], "123")


class ReadDocumentsCienciaTest(PjeServerCase):
    def read(self, **arguments):
        response = mock.Mock(status_code=200, headers={"Content-Type": "text/html;charset=UTF-8"})
        response.iter_content.return_value = iter([CERTIDAO])
        self.session.get.return_value = response
        found = {
            "success": True,
            "portal": "trf5-1g",
            "numero_processo": NUMBER,
            "processo_id": "123",
            "timeline": copy.deepcopy(TIMELINE),
            "expedientes": [dict(PENDING)],
        }
        with mock.patch.object(search, "search_process", return_value=found):
            return call(
                "pje_read_documents",
                {"processNumber": NUMBER, "portal": "trf5-1g", "documentIds": ["456"], **arguments},
            )

    def test_a_pending_communication_is_refused_until_the_user_agrees(self):
        text, error = self.read()
        self.assertTrue(error)
        self.assertIn("acknowledgeCiencia=true", text)
        self.session.get.assert_not_called()

        result, error = self.read(acknowledgeCiencia=True)
        self.assertFalse(error, result)
        self.assertEqual(result["ciencia"]["estado"], "dada")
        self.assertEqual(result["ciencia"]["intimacoes"][0]["expedienteId"], "9")
        self.session.get.assert_called()

    def test_the_customer_setting_decides_only_when_the_call_does_not(self):
        self.ciencia_on_read.return_value = "allow"
        result, error = self.read()
        self.assertFalse(error, result)
        self.assertEqual(result["ciencia"]["estado"], "dada")
        self.session.get.reset_mock()
        text, error = self.read(acknowledgeCiencia=False)
        self.assertTrue(error)
        self.session.get.assert_not_called()


class ReadExpedienteAtoCienciaTest(PjeServerCase):
    def setUp(self):
        super().setUp()
        lookup = {"process_id": "123", "ca": "abc", "search_results": [], "referer": "https://pje1g.trf5.jus.br/pje/"}
        page = {
            "html": "<html></html>",
            "status": 200,
            "download_pattern": None,
            "first_timeline": [],
            "timeline": copy.deepcopy(TIMELINE),
            "pagination": None,
        }
        act = mock.Mock(status_code=200, content=b"<html><body>Intime-se a autora.</body></html>", headers={})
        for target in (
            mock.patch.object(search, "_recall", return_value=(None, None)),
            mock.patch.object(search, "_find_process", return_value=lookup),
            mock.patch.object(search, "_detail_page", return_value=page),
            mock.patch.object(search, "fetch_expedientes", side_effect=lambda *_: ([dict(PENDING)], True)),
        ):
            target.start()
            self.addCleanup(target.stop)
        opener = mock.patch.object(expedientes.jsf, "get", return_value=act)
        self.open_act = opener.start()
        self.addCleanup(opener.stop)

    def read(self, **arguments):
        result, error = call(
            "pje_read_expediente_ato", {"processNumber": NUMBER, "portal": "trf5-1g", "expedienteId": "9", **arguments}
        )
        self.assertFalse(error, result)
        return result

    def test_a_pending_act_stays_closed_and_reports_pendente(self):
        result = self.read()
        self.assertIn("ciência not yet registered", result["ato"]["error"])
        self.assertEqual(result["ciencia"]["estado"], "pendente")
        self.assertEqual(result["ciencia"]["intimacoes"][0]["expedienteId"], "9")
        self.open_act.assert_not_called()

    def test_with_acknowledge_ciencia_the_act_is_opened_and_reported_dada(self):
        result = self.read(acknowledgeCiencia=True)
        self.assertNotIn("error", result["ato"])
        self.assertEqual(result["ciencia"]["estado"], "dada")
        self.open_act.assert_called_once()
        self.assertIn("idProcessoParteExpediente=9", self.open_act.call_args.args[1])

    def test_the_customer_setting_opens_it_unless_the_call_says_no(self):
        self.ciencia_on_read.return_value = "allow"
        self.assertEqual(self.read(acknowledgeCiencia=False)["ciencia"]["estado"], "pendente")
        self.open_act.assert_not_called()
        self.assertEqual(self.read()["ciencia"]["estado"], "dada")
        self.open_act.assert_called_once()


if __name__ == "__main__":
    unittest.main()
