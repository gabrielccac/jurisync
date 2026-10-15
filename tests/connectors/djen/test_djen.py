"""Offline checks DJEN: sem credenciais, sem rede (urllib mockado)."""

import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from unittest.mock import patch
from urllib.error import URLError

from jurisync.connectors.djen import client, documents, normalize, poll, store

# Fixture real: id 740120612 (TRF5 38ª PE, laudo pericial). Texto íntegro
# preservado parcial; campos estruturais exatos da API.
FIXTURE = {
    "id": 740120612,
    "data_disponibilizacao": "2026-09-29",
    "siglaTribunal": "TRF5",
    "tipoComunicacao": "Intimação",
    "nomeOrgao": "38ª Vara Federal PE",
    "idOrgao": 94328,
    "texto": "PODER JUDICIÁRIO JUSTIÇA FEDERAL DA 5ª REGIÃO PE / SERRA TALHADA PROCESSO: 0003527-90.2026.4.05.8303 CLASSE: PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436) POLO ATIVO: MARIA DE LOURDES DA SILVA REPRESENTANTES POLO ATIVO: VICTOR HUGO VALERIANO PINTO - PB14663 e GRAZIELE ALVES DA SILVA - PE48647 POLO PASSIVO:INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS INTIMAÇÃO De ordem do(a) MM Juiz(a), ficam as partes intimadas para se manifestarem sobre o(s) laudo(s) pericial(is). SERRA TALHADA, 28 de setembro de 2026.",
    "numero_processo": "00035279020264058303",
    "meio": "D",
    "link": "https://pje1g.trf5.jus.br/pje/Processo/ConsultaDocumento/listView.seam?x=26092800234813500000209931495",
    "tipoDocumento": "Intimação",
    "nomeClasse": "PROCEDIMENTO DO JUIZADO ESPECIAL CíVEL",
    "codigoClasse": "436",
    "numeroComunicacao": 48,
    "ativo": True,
    "hash": "lx8NKdwzYKdioGfPTKe7QWRbD1mrEX",
    "status": "P",
    "motivo_cancelamento": None,
    "data_cancelamento": None,
    "datadisponibilizacao": "29/09/2026",
    "meiocompleto": "Diário de Justiça Eletrônico Nacional",
    "numeroprocessocommascara": "0003527-90.2026.4.05.8303",
    "destinatarios": [{"nome": "MARIA DE LOURDES DA SILVA", "comunicacao_id": 740120612, "polo": "A"}],
    "destinatarioadvogados": [
        {
            "id": 1388173015,
            "comunicacao_id": 740120612,
            "advogado_id": 1458369,
            "advogado": {"id": 1458369, "nome": "VICTOR HUGO VALERIANO PINTO", "numero_oab": "14663", "uf_oab": "PB"},
        },
        {
            "id": 1388173016,
            "comunicacao_id": 740120612,
            "advogado_id": 1579254,
            "advogado": {"id": 1579254, "nome": "GRAZIELE ALVES DA SILVA", "numero_oab": "48647", "uf_oab": "PE"},
        },
    ],
}


class DjenChecks(unittest.TestCase):
    def test_normalize_fixture(self):
        c = normalize.normalize_item(FIXTURE)
        self.assertEqual(c["id"], 740120612)
        self.assertEqual(c["numero_processo_mascara"], "0003527-90.2026.4.05.8303")
        self.assertEqual(c["numero_processo"], "00035279020264058303")
        self.assertNotIn("is_pe", c)
        self.assertNotIn("secao_pop", c)
        self.assertEqual(c["advogados"][0]["numero_digits"], "14663")
        self.assertEqual(c["advogados"][1]["uf_oab"], "PE")
        self.assertEqual(c["hash"], "lx8NKdwzYKdioGfPTKe7QWRbD1mrEX")

    def test_oab_digits_ignora_variante(self):
        self.assertEqual(normalize.normalize_oab_digits("14663-A"), "14663")
        self.assertEqual(normalize.normalize_oab_digits("PB14663"), "14663")

    def test_normalizer_preserves_cancellation_for_skill(self):
        c = normalize.normalize_item(dict(FIXTURE, data_cancelamento="2026-09-29"))
        self.assertEqual(c["data_cancelamento"], "2026-09-29")
        self.assertIn("laudo(s) pericial(is)", c["texto"])

    def test_build_url(self):
        url = client.build_url("14663", "PB", "2026-09-28", "2026-09-29", "TRF5", 1, 50)
        self.assertIn("numeroOab=14663", url)
        self.assertIn("ufOab=PB", url)
        self.assertIn("siglaTribunal=TRF5", url)

    def test_itens_limite(self):
        with self.assertRaises(ValueError):
            client.fetch_page("14663", "PB", "2026-09-28", "2026-09-29", None, 1, 1000)
        with self.assertRaises(ValueError):
            client.fetch_page("14663", "PB", "2026-09-28", "2026-09-29", None, 1, 2)
        with self.assertRaises(ValueError):
            client.fetch_page("", "PB", "2026-09-28", "2026-09-29")

    def test_fetch_page_dead_letter_apos_retries(self):
        with patch.object(client, "_read_json", return_value=({"status": "error"}, 500)):
            with patch.object(client.time, "sleep", return_value=None):
                with self.assertRaises(client.DjenError):
                    client.fetch_page("14663", "PB", "2026-09-28", "2026-09-29")

    def test_retry_only_transient_failures(self):
        with patch.object(client, "_read_json", return_value=(None, 400)) as read:
            with patch.object(client.time, "sleep") as sleep:
                with self.assertRaises(client.DjenError):
                    client.fetch_page("14663", "PB", "2026-09-28", "2026-09-29")
                self.assertEqual(read.call_count, 1)
                sleep.assert_not_called()
        with patch.object(client, "_read_json", side_effect=URLError("offline")) as read:
            with patch.object(client.time, "sleep", return_value=None):
                with self.assertRaises(client.DjenError):
                    client.fetch_page("14663", "PB", "2026-09-28", "2026-09-29")
                self.assertEqual(read.call_count, 3)

    def test_fetch_all_pagina(self):
        p1 = {"status": "success", "count": 3, "items": [{"id": 1}, {"id": 2}]}
        p2 = {"status": "success", "count": 3, "items": [{"id": 3}]}
        with patch.object(client, "fetch_page", side_effect=[p1, p2]) as fp:
            count, items = client.fetch_all("14663", "PB", "2026-09-28", "2026-09-29", "TRF5", 2)
        self.assertEqual(count, 3)
        self.assertEqual([i["id"] for i in items], [1, 2, 3])
        self.assertEqual(fp.call_count, 2)

    def test_fetch_all_rejects_partial_page(self):
        p1 = {"count": 3, "items": [{"id": 1}, {"id": 2}]}
        p2 = {"count": 3, "items": []}
        with patch.object(client, "fetch_page", side_effect=[p1, p2]):
            with self.assertRaisesRegex(client.DjenError, "pagination incomplete"):
                client.fetch_all("14663", "PB", "2026-09-28", "2026-09-29")

    def test_cli_all_and_variants_write_json_to_stdout(self):
        for extra, method, payload in (
            (["--all"], "fetch_all", (1, [{"id": 1}])),
            (["--all-variants"], "fetch_all_variants", {"count": 1, "unique": 1, "items": [{"id": 1}]}),
        ):
            stdout, stderr = StringIO(), StringIO()
            argv = [
                "djen",
                "--numero-oab",
                "14663",
                "--uf-oab",
                "PB",
                "--inicio",
                "2026-09-29",
                "--fim",
                "2026-09-29",
                *extra,
            ]
            with patch("sys.argv", argv), patch.object(client, method, return_value=payload):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    client.main()
            self.assertEqual(json.loads(stdout.getvalue())["items"], [{"id": 1}])
            self.assertEqual(stderr.getvalue(), "")

    def test_cli_certidao_text_returns_json(self):
        stdout = StringIO()
        with patch("sys.argv", ["djen", "--certidao", "abc123", "--certidao-text"]):
            with patch.object(client, "fetch_certidao", return_value=b"%PDF-1.7"):
                with patch("jurisync.core.document_text.extract_document_text", return_value=("extracted", "certidão")):
                    with redirect_stdout(stdout):
                        client.main()
        self.assertEqual(json.loads(stdout.getvalue())["text"], "certidão")

    def test_cli_certidao_file_returns_json_on_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = os.path.join(tmp, "cert.pdf")
            stdout = StringIO()
            with patch("sys.argv", ["djen", "--certidao", "abc123", "--certidao-out", destination]):
                with patch.object(client, "fetch_certidao", return_value=b"%PDF-1.7"):
                    with redirect_stdout(stdout):
                        client.main()
            self.assertEqual(json.loads(stdout.getvalue())["file"], destination)
            with open(destination, "rb") as fh:
                self.assertEqual(fh.read(), b"%PDF-1.7")

    def test_store_idempotencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.json")
            st = store.load_state(path)
            items = [{"id": 1}, {"id": 2}]
            novos, dups = store.filter_new(items, st)
            self.assertEqual(len(novos), 2)
            store.mark_seen(st, novos)
            store.save_state(path, st)
            st2 = store.load_state(path)
            novos2, dups2 = store.filter_new(items + [{"id": 3}], st2)
            self.assertEqual([i["id"] for i in novos2], [3])
            self.assertEqual(dups2, 2)

    def test_store_emits_cancellation_once(self):
        state = {"ids_vistos": [1], "ids_cancelados": []}
        cancelled = {"id": 1, "data_cancelamento": "2026-09-29"}
        new, _ = store.filter_new([cancelled], state)
        self.assertEqual(new, [cancelled])
        store.mark_seen(state, new)
        self.assertEqual(store.filter_new([cancelled], state)[0], [])

    def test_poll_advances_only_after_complete_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(poll, "fetch_all_variants", return_value={"count": 1, "unique": 1, "items": [FIXTURE]}):
                result = poll.poll_window("14663", "PB", "TRF5", state_dir=tmp, today=date(2026, 9, 29))
            self.assertEqual(result["new"], 1)
            self.assertEqual(result["inicio"], "2026-08-31")
            path = store.state_path(tmp, "14663", "PB", "TRF5")
            self.assertEqual(store.load_state(path)["ultima_data"], "2026-09-29")
            with patch.object(poll, "fetch_all_variants", side_effect=client.DjenError("partial")):
                with self.assertRaises(client.DjenError):
                    poll.poll_window("14663", "PB", "TRF5", state_dir=tmp, today=date(2026, 9, 30))
            self.assertEqual(store.load_state(path)["ultima_data"], "2026-09-29")

    def test_stats_por_orgao(self):
        items = [normalize.normalize_item(FIXTURE), normalize.normalize_item(FIXTURE)]
        s = store.stats(items)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["por_orgao"]["38ª Vara Federal PE"], 2)

    def test_build_url_person_case_params(self):
        url = client.build_url(
            "61216", "RS", "2026-09-03", "2026-10-03", "STJ", 1, 5, nome_advogado="ELISANDRA BECKER", meio="D"
        )
        self.assertIn("nomeAdvogado=ELISANDRA+BECKER", url)
        self.assertIn("meio=D", url)
        self.assertNotIn("nomeParte", url)

    def test_search_page_exige_criterio(self):
        with self.assertRaises(ValueError):
            client.search_page({"dataDisponibilizacaoInicio": "2026-09-03", "dataDisponibilizacaoFim": "2026-10-03"})

    def test_search_page_passa_params(self):
        payload = {"status": "success", "count": 1, "items": [{"id": 7}]}
        with patch.object(client, "_read_json", return_value=(payload, 200)):
            result = client.search_page({"nomeAdvogado": "ELISANDRA BECKER", "ufOab": "RS"})
        self.assertEqual(result["count"], 1)

    def test_search_all_pagina(self):
        p1 = {"status": "success", "count": 3, "items": [{"id": 1}, {"id": 2}]}
        p2 = {"status": "success", "count": 3, "items": [{"id": 3}]}
        with patch.object(client, "search_page", side_effect=[p1, p2]):
            count, items = client.search_all({"nomeParte": "ZANGRANDE"})
        self.assertEqual(count, 3)
        self.assertEqual([i["id"] for i in items], [1, 2, 3])

    def test_list_tribunals(self):
        rows = [{"uf": "", "nomeEstado": "Nacional", "instituicoes": [{"sigla": "STJ"}]}]
        with patch.object(client, "_read_json", return_value=(rows, 200)):
            self.assertEqual(client.list_tribunals(), rows)
        with patch.object(client, "_read_json", return_value=({"status": "error"}, 500)):
            with self.assertRaises(client.DjenError):
                client.list_tribunals()

    def test_fetch_caderno_validacao(self):
        meta = {"sigla_tribunal": "STJ", "url": "https://x/y.zip"}
        with patch.object(client, "_read_json", return_value=(meta, 200)):
            self.assertEqual(client.fetch_caderno("STJ", "2026-09-29", "D"), meta)
        with self.assertRaises(ValueError):
            client.fetch_caderno("STJ", "2026-09-29", "X")
        with patch.object(client, "_read_json", return_value=({"erro": "nada"}, 200)):
            with self.assertRaises(client.DjenError):
                client.fetch_caderno("STJ", "2026-10-03", "D")

    def test_strip_html(self):
        raw = "<table><tr><td>REsp 1</td></tr></table>"
        self.assertEqual(normalize.strip_html(raw), "REsp 1")
        self.assertEqual(normalize.strip_html("texto puro"), "texto puro")
        c = normalize.normalize_item(dict(FIXTURE, texto="<b>Intima&ccedil;&atilde;o</b> para laudo"))
        self.assertEqual(c["texto_limpo"], "Intimação para laudo")
        self.assertTrue(c["texto"].startswith("<b>"))

    def test_resolve_portal_link_hosts(self):
        jfrs = documents.resolve_portal(
            {
                "link": "https://eproc.jfrs.jus.br/eprocV2/externo_controlador.php?acao=x",
                "numero_processo": "50123668820254047104",
            }
        )
        self.assertEqual((jfrs["system"], jfrs["portal"], jfrs["wired"]), ("eproc", "trf4-1g-rs", True))
        tjrs = documents.resolve_portal(
            {
                "link": "https://eproc1g.tjrs.jus.br/eproc/externo_controlador.php?acao=x",
                "numero_processo": "52375895520248210001",
            }
        )
        self.assertEqual((tjrs["system"], tjrs["portal"], tjrs["wired"]), ("eproc", "tjrs-1g", True))
        stj = documents.resolve_portal(
            {
                "link": "https://processo.stj.jus.br/processo/dj/documento/?seq_documento=1",
                "numero_processo": "52313092320248217000",
            }
        )
        self.assertEqual((stj["portal"], stj["wired"]), ("stj", False))

    def test_resolve_portal_cnj_fallback(self):
        trf5 = documents.resolve_portal({"link": "", "numero_processo": "00035279020264058303"})
        self.assertEqual((trf5["system"], trf5["portal"], trf5["wired"]), ("pje", "trf5-1g", True))
        jfrs = documents.resolve_portal({"link": None, "numeroprocessocommascara": "5012366-88.2025.4.04.7104"})
        self.assertEqual(jfrs["portal"], "trf4-1g-rs")
        unknown = documents.resolve_portal({"link": "", "numero_processo": "curto"})
        self.assertFalse(unknown["wired"])

    def test_format_mascara(self):
        self.assertEqual(documents.format_mascara("50123668820254047104"), "5012366-88.2025.4.04.7104")
        with self.assertRaises(ValueError):
            documents.format_mascara("123")

    def test_fetch_link_document_guards(self):
        with self.assertRaises(ValueError):
            documents.fetch_link_document("http://evil.example/doc.pdf")
        with self.assertRaises(ValueError):
            documents.fetch_link_document("https://eproc.jfrs.jus.br/x")

    def test_fetch_link_document_ok(self):
        class FakeResp:
            url = "https://eproc.jfrs.jus.br/eprocV2/doc"
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, _n):
                data, self._done = getattr(self, "_d", b"<html><body>doc</body></html>"), True
                if getattr(self, "_done", False) and not hasattr(self, "_x"):
                    self._x = 1
                    return data
                return b""

        with patch("urllib.request.urlopen", return_value=FakeResp()):
            ctype, blob = documents.fetch_link_document("https://eproc.jfrs.jus.br/eprocV2/doc")
        self.assertEqual(ctype, "text/html")
        self.assertIn(b"doc", blob)

    def test_fetch_attachments_guards(self):
        stj = {"link": "https://processo.stj.jus.br/processo/dj/documento/?seq_documento=1", "numero_processo": "52313092320248217000"}
        with self.assertRaisesRegex(ValueError, "sem conector"):
            documents.fetch_attachments(stj, session=object(), document_ids=["1"])
        jfrs = {"link": "https://eproc.jfrs.jus.br/eprocV2/x", "numeroprocessocommascara": "5012366-88.2025.4.04.7104"}
        with self.assertRaisesRegex(ValueError, "session"):
            documents.fetch_attachments(jfrs, document_ids=["1"])
        with self.assertRaisesRegex(ValueError, "document_ids"):
            documents.fetch_attachments(jfrs, session=object())

    def test_fetch_attachments_eproc_mocked(self):
        from jurisync.connectors.eproc import client as eproc_client
        from jurisync.connectors.eproc import documents as eproc_documents

        item = {"link": "https://eproc.jfrs.jus.br/eprocV2/x", "numeroprocessocommascara": "5012366-88.2025.4.04.7104"}
        fake_process = {"success": True, "numero": "5012366-88.2025.4.04.7104"}
        fake_records = [{"id": "100", "content": b"<html><body>laudo texto</body></html>", "content_type": "text/html"}]
        with (
            patch.object(eproc_client, "fetch_process", return_value=fake_process) as fp,
            patch.object(eproc_documents, "read_selected_documents", return_value=fake_records),
        ):
            result = documents.fetch_attachments(item, session=object(), document_ids=["100"])
        fp.assert_called_once()
        self.assertEqual(result["portal"]["portal"], "trf4-1g-rs")
        self.assertIn("laudo texto", result["attachments"][0]["text"])


if __name__ == "__main__":
    unittest.main()
