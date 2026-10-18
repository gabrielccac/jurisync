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


# Labels the office's own filing guide ("[IT] Protocolo de Iniciais BPC - PJE") shows on TRF1 supporting rows; the
# read-only capture only had the main-document list, which does not carry them.
OFFICE_GUIDE_LABELS = {
    "Comprovante de residência",
    "Cadastro Nacional de Informações Sociais - CNIS",
    "Atestado médico",
}
# Tipos de ANEXO que o próprio TRF1 aceitou num protocolo real do escritório (comprovante de 19/06/2025); a lista
# capturada em document_types.html é a do documento principal, que é menor que a dos anexos.
OFFICE_FILED_ATTACHMENT_LABELS = {
    "Carteira de identidade",
    "Carta de indeferimento de benefício",
    "Comprovante de situação cadastral no CPF",
    "Certidão de nascimento",
}


class Trf1ProfileTests(unittest.TestCase):
    def test_the_office_trf1_profile_uses_labels_trf1_offers(self):
        profile = load_profile("jean")
        offered = {
            option.get_text(" ", strip=True)
            for option in BeautifulSoup(fixture("document_types.html"), "html.parser").find_all("option")
        }
        self.assertEqual(profile.portal, "trf1-1g")
        self.assertLessEqual(
            set(profile.supporting_types), offered | OFFICE_GUIDE_LABELS | OFFICE_FILED_ATTACHMENT_LABELS
        )
        self.assertIn("Petição inicial", offered)
        self.assertEqual(profile.portal_type("Laudo Médico"), "Atestado médico")
        self.assertEqual(profile.portal_type("Documento Comprobatório"), "Comprovante (Outros)")
        self.assertEqual(profile.defaults()["classification"]["area"], "DIREITO PREVIDENCIÁRIO")


class Trf1CharacteristicsTests(unittest.TestCase):
    """The TRF1 characteristics tab (captured read-only): Juízo 100% Digital and segredo are their own fields."""

    def client(self, panels):
        client = DraftClient(None, portal="trf1-1g")
        calls = []
        client.characteristics_panel = lambda url: panels.pop(0) if len(panels) > 1 else panels[0]
        client.ajax = lambda url, state, fields, container, action: (
            calls.append((fields, action)) or fixture("characteristics_tab.html")
        )
        return client, calls

    def test_a_draft_already_matching_costs_no_writes(self):
        client, calls = self.client([fixture("characteristics_tab.html")])
        wanted = {
            "claimValue": "1.000,00",
            "legalAid": True,
            "injunction": False,
            "digitalCourt": True,
            "secrecy": False,
            "priority": ["Pessoa com Deficiência"],
        }
        result = client.save_characteristics("draft", wanted)
        self.assertEqual(calls, [])
        self.assertTrue(result["alreadySaved"])
        self.assertEqual((result["priority"], result["priorityAdded"]), (["Pessoa com Deficiência"], []))
        self.assertEqual(result["secrecy"], {"value": False, "changed": False})

    def test_digital_court_and_injunction_are_saved_in_the_main_form_and_read_back(self):
        before = fixture("characteristics_tab.html")
        soup = BeautifulSoup(before, "html.parser")
        for radio in soup.find_all("input", attrs={"type": "radio"}):
            if "tutelaLiminar" in radio["name"]:
                if radio["value"] == "true":
                    radio["checked"] = "checked"
                else:
                    del radio["checked"]
        after = str(soup)
        client, calls = self.client([before, after])
        wanted = {"claimValue": "1.000,00", "legalAid": True, "injunction": True, "digitalCourt": True, "priority": []}
        result = client.save_characteristics("draft", wanted)
        self.assertFalse(result["alreadySaved"])
        saved_fields = calls[0][0]
        digital = next(
            name for name in saved_fields if name.endswith("solicitadoJuizo100PorCentoDigitalSelectOneRadio")
        )
        injunction = next(name for name in saved_fields if name.endswith("tutelaLiminarSelectOneRadio"))
        self.assertEqual((saved_fields[digital], saved_fields[injunction]), ("true", "true"))
        client, _ = self.client([before, before])
        with self.assertRaisesRegex(RuntimeError, "did not retain the characteristic tutelaLiminarSelectOneRadio"):
            client.save_characteristics("draft", wanted)

    def test_a_priority_pje_does_not_offer_is_named_with_the_options(self):
        client, _ = self.client([fixture("characteristics_tab.html")])
        wanted = {"claimValue": "1.000,00", "legalAid": True, "injunction": False, "priority": ["Doença rara"]}
        with self.assertRaisesRegex(RuntimeError, "no priority 'Doença rara'; options: .*Idoso\\(a\\)"):
            client.save_characteristics("draft", wanted)

    def test_digital_court_on_a_pje_without_the_field_says_to_use_the_priority(self):
        page = fixture("characteristics_tab.html")
        soup = BeautifulSoup(page, "html.parser")
        for radio in soup.find_all(
            "input", attrs={"name": lambda n: (n or "").endswith("Juizo100PorCentoDigitalSelectOneRadio")}
        ):
            radio.decompose()
        client, _ = self.client([str(soup)])
        wanted = {"claimValue": "1.000,00", "legalAid": True, "injunction": False, "digitalCourt": True}
        with self.assertRaisesRegex(RuntimeError, "no Juízo 100% Digital field"):
            client.save_characteristics("draft", wanted)


if __name__ == "__main__":
    unittest.main()


class PartyModalBase(unittest.TestCase):
    """Replays captured PJe responses in order; the call after the last one stops the flow."""

    class Stop(Exception):
        pass

    def client(self, responses, portal="trf1-1g"):
        client = DraftClient(None, portal=portal)
        calls = []

        def ajax(url, state, fields, container, action):
            calls.append((dict(fields), dict(action)))
            if not responses:
                raise self.Stop
            return fixture(responses.pop(0))

        client.ajax = ajax
        return client, calls


class Trf1PartyModalTests(PartyModalBase):
    """The TRF1 'Associar parte ao processo' modal, captured read-only on a real draft (display steps only)."""

    def test_the_author_answers_tipo_da_parte_autor_before_the_cpf_lookup(self):
        client, calls = self.client(["party_add_active.html", "party_role_autor.html", "party_person_fisica.html"])
        with self.assertRaises(self.Stop):
            client.add_author("draft", "12345678909", "EXAMPLE", panel=fixture("party_toolbars.html"))
        opened, role, person, lookup = calls
        self.assertIn("formVincularPoloAtivo:supResertarVincPartePA", opened[1])
        self.assertEqual(role[0]["j_id2888:j_id2892"], "3", "AUTOR")
        self.assertEqual(person[0]["preCadastroPessoaFisicaForm:tipoPessoaDecoration:tipoPessoa"], "F")
        cpf = "preCadastroPessoaFisicaForm:preCadastroPessoaFisica_nrCPFDecoration:preCadastroPessoaFisica_nrCPF"
        self.assertEqual(lookup[0][cpf], "123.456.789-09")
        self.assertIn("preCadastroPessoaFisicaForm:pesquisarDocumentoPrincipal", lookup[1])

    def test_inss_answers_tipo_da_parte_reu_then_searches_the_public_body(self):
        client, calls = self.client(["party_add_passive.html", "party_role_reu.html"])
        body = {"search": "INSS", "cnpj": "29979036000140", "name": "INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS"}
        with self.assertRaises(self.Stop):
            client.add_passive_body("draft", body, panel=fixture("party_toolbars.html"))
        opened, role, search = calls
        self.assertIn("formVincularPoloPassivo:supResetarVincPartePP", opened[1])
        self.assertEqual(
            role[0]["j_id2888:j_id2892"],
            next(
                option["value"]
                for option in BeautifulSoup(fixture("party_add_passive.html"), "html.parser").find_all("option")
                if option.get_text(strip=True) == "REU"
            ),
        )
        fields = search[0]
        self.assertEqual(fields["preCadastroPessoaFisicaForm:tipoPessoaDecoration:tipoPessoa"], "J")
        self.assertEqual(
            fields["preCadastroPessoaFisicaForm:isOrgaoPublico1Decoration:isOrgaoPublico1SelectOneRadio"], "true"
        )
        self.assertEqual(
            fields[
                "preCadastroPessoaFisicaForm:preCadastroPessoaFisica_nomePJDecoration:preCadastroPessoaFisica_nomePJ"
            ],
            "INSS",
        )
        self.assertIn("preCadastroPessoaFisicaForm:btnPesquisarOrgaoPublico", search[1])

    def test_a_missing_role_option_fails_clearly_and_trf5_has_no_role_step(self):
        client, _ = self.client(["party_add_passive.html"])
        with self.assertRaisesRegex(RuntimeError, "'Tipo da Parte' step with the option AUTOR"):
            client.add_author("draft", "12345678909", "EXAMPLE", panel=fixture("party_toolbars.html"))
        trf5, calls = self.client([], portal="trf5-1g")
        self.assertEqual(trf5._choose_party_role("<html></html>", "active"), "<html></html>")
        self.assertEqual(calls, [])


class Trf1AuthorFlowTests(PartyModalBase):
    """The rest of the author and INSS steps, replayed from responses captured on a real TRF1 draft (anonymized)."""

    ADDRESS = {"cep": "68900-000", "numero": "10"}

    @staticmethod
    def selected_addresses():
        return fixture("author_addresses.html").replace("radio-button-uncheck_16x16.png", "radio-button_16x16.png")

    def test_the_author_reaches_vincular_with_the_registered_address_selected(self):
        responses = [
            "party_add_active.html",
            "party_role_autor.html",
            "party_person_fisica.html",
            "author_lookup.html",
            "author_details.html",
            "author_addresses.html",
        ]
        client, calls = self.client(responses)
        original = client.ajax

        def ajax(url, state, fields, container, action):
            if len(calls) == 6:  # "Usar no processo" on the registered address
                calls.append((dict(fields), dict(action)))
                return self.selected_addresses()
            return original(url, state, fields, container, action)

        client.ajax = ajax
        with self.assertRaises(self.Stop):
            client.add_author(
                "draft", "12345678909", "PARTE EXEMPLO", address=self.ADDRESS, panel=fixture("party_toolbars.html")
            )
        insert_fields, insert_action = calls[-1]
        self.assertIn("formInserirParteProcesso:btnInserirParteProcesso", insert_action)
        for suffix in ("tpParteAlteracao", "comboParteSigilosa", "comboMoradorRua", "comboRepresentante"):
            self.assertTrue(any(name.endswith(suffix) for name in insert_fields), suffix)
        cep = next(name for name in insert_fields if name.endswith("cadastroPartePessoaEnderecoCEP"))
        self.assertIn(cep, insert_fields)

    def test_trf5_still_requires_mother_and_birth_date_from_the_registry(self):
        self.assertEqual(
            filing_portal("trf5-1g").author_required_details,
            ("nomeGenitoraDecoration:nomeGenitora", "dataNascimentoDecoration:dataNascimentoInputDate"),
        )
        self.assertEqual(filing_portal("trf1-1g").author_required_details, ())

    def test_inss_is_found_by_cnpj_and_reaches_inserir(self):
        client, calls = self.client(
            ["party_add_passive.html", "party_role_reu.html", "inss_search.html", "inss_selected.html"]
        )
        body = {"search": "INSS", "cnpj": "29979036000140", "name": "INSTITUTO NACIONAL DO SEGURO SOCIAL - INSS"}
        with self.assertRaises(self.Stop):
            client.add_passive_body("draft", body, panel=fixture("party_toolbars.html"))
        self.assertIn("preCadastroPessoaFisicaForm:btnInserirOrgPub", calls[-1][1])
