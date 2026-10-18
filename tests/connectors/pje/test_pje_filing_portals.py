import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bs4 import BeautifulSoup

from jurisync.connectors.pje.filing import WRITE_STAGES, DraftClient, run_stage
from jurisync.connectors.pje.filing.checkpoint import phase_fingerprints
from jurisync.connectors.pje.filing.constants import FILING_PORTALS, filing_portal
from jurisync.connectors.pje.filing.status import run_status
from jurisync.connectors.pje.filing.store import LocalRunStore
from jurisync.workflows.filing.profile import load_profile

FIXTURES = Path(__file__).parent / "fixtures" / "trf1"
PAYLOAD = {
    "subject": {"search": "s", "code": "1", "name": "S"},
    "authorCpf": "12345678909",
    "author": {"name": "EXAMPLE"},
}


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class PortalConfigurationTests(unittest.TestCase):
    def test_each_portal_has_its_own_urls_and_trf1_lists_what_is_unverified(self):
        trf1, trf5 = filing_portal("trf1-1g"), filing_portal("trf5-1g")
        self.assertEqual(trf1.register_url, "https://pje1g.trf1.jus.br/pje/Processo/cadastrar.seam")
        self.assertEqual(trf5.update_url, "https://pje1g.trf5.jus.br/pje/Processo/update.seam")
        self.assertEqual(
            {key: config.writes for key, config in FILING_PORTALS.items()}, {"trf5-1g": True, "trf1-1g": True}
        )
        self.assertEqual(trf5.unverified, ())
        self.assertTrue(any("Incluir" in step for step in trf1.unverified))
        client = DraftClient(None, portal="trf1-1g")
        self.assertEqual((client.portal.host, client.pending_url), ("pje1g.trf1.jus.br", trf1.pending_url))
        with self.assertRaisesRegex(ValueError, "does not support trf1-2g"):
            filing_portal("trf1-2g")

    def test_trf1_writes_still_need_an_approved_payload_and_a_matching_portal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalRunStore(tmp)
            Path(tmp, "payload.json").write_text(json.dumps({**PAYLOAD, "portal": "trf1-1g"}))
            with mock.patch("jurisync.connectors.pje.filing.run._login", side_effect=AssertionError):
                for stage in sorted(WRITE_STAGES - {"delete-draft"}):
                    with self.subTest(stage), self.assertRaisesRegex(ValueError, "not been approved"):
                        run_stage(stage, run=store, portal="trf1-1g")
                with self.assertRaisesRegex(ValueError, "payload is for trf1-1g, not trf5-1g"):
                    run_stage("classification", run=store, portal="trf5-1g")
                Path(tmp, "payload.json").write_text(json.dumps({**PAYLOAD, "portal": "trf1-2g"}))
                with self.assertRaisesRegex(ValueError, "does not support trf1-2g"):
                    run_stage("fill", run=store)

    def test_status_of_a_trf1_run_names_the_steps_to_watch(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalRunStore(tmp)
            payload = {**PAYLOAD, "portal": "trf1-1g"}
            store.write_json(
                "checkpoint.json", {"draftId": "1", "status": "complete", "completed": phase_fingerprints(payload)}
            )
            result = run_status(store, payload)
        self.assertEqual(result["portal"], "trf1-1g")
        self.assertEqual(result["unverifiedSteps"], list(FILING_PORTALS["trf1-1g"].unverified))


class Trf1CapturedPagesTests(unittest.TestCase):
    """Fragments of TRF1 pages captured read-only in Etapa A (no personal data)."""

    def test_the_register_form_matches_the_ids_the_flow_uses(self):
        client = DraftClient(None, portal="trf1-1g")
        client.get = lambda url: fixture("register_form.html")
        result = client.register_options()
        self.assertIn("DIREITO ASSISTENCIAL", result["areas"])
        self.assertIn("DIREITO PREVIDENCIÁRIO", result["areas"])
        soup = BeautifulSoup(fixture("register_form.html"), "html.parser")
        for suffix in ("areaDireitoCombo", "jurisdicaoCombo", "classeJudicialCombo"):
            self.assertEqual(len([s for s in soup.find_all("select") if s.get("name", "").endswith(suffix)]), 1, suffix)
        self.assertIsNotNone(soup.find("input", attrs={"name": "processoTrfForm:incluiProcessoButton"}))

    def test_subjects_are_read_from_the_assunto_tab_when_the_draft_page_lacks_them(self):
        client = DraftClient(None, portal="trf1-1g")
        calls = []
        client.get = lambda url: '<input type="hidden" name="javax.faces.ViewState" value="v1"/>'
        client.ajax = lambda url, state, fields, container, action: calls.append(action) or fixture("assunto_tab.html")
        result = client.add_subject("draft", {"code": "6177", "name": "Concessão", "search": "Concessão"})
        self.assertEqual(result, {"alreadyAdded": True, "subjectCode": "6177"})
        self.assertEqual(calls, [{"assunto": "assunto", "ajaxSingle": "assunto"}])

    def test_a_failed_incluir_reports_what_pje_said(self):
        client = DraftClient(None, portal="trf1-1g")
        client._classification_form = lambda classification: (
            '<input name="javax.faces.ViewState" value="v"/>',
            {},
            "inc",
            {},
        )
        client.ajax = lambda *args: '<span class="rich-messages-label">Órgão julgador: campo obrigatório</span>'
        with self.assertRaisesRegex(RuntimeError, "Órgão julgador: campo obrigatório. Check Não Protocolados"):
            client.create_draft({})


class Trf1ProfileTests(unittest.TestCase):
    def test_victors_trf1_profile_uses_labels_the_trf1_form_offers(self):
        profile = load_profile("victor-trf1")
        offered = {
            option.get_text(" ", strip=True)
            for option in BeautifulSoup(fixture("document_types.html"), "html.parser").find_all("option")
        }
        self.assertEqual(profile.portal, "trf1-1g")
        self.assertLessEqual(set(profile.supporting_types), offered)
        self.assertIn("Petição inicial", offered)
        self.assertEqual(profile.portal_type("Laudo Médico"), "Exame médico")
        self.assertEqual(profile.portal_type("Documento Comprobatório"), "Comprovante (Outros)")
        self.assertEqual(profile.defaults()["classification"], {"area": "DIREITO ASSISTENCIAL"})


if __name__ == "__main__":
    unittest.main()
