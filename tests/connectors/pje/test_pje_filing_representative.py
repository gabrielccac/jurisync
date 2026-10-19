"""The author's legal representative on TRF1, replayed from responses read on draft 15508678 (anonymized) up to the
registry lookup; nothing after Confirmar was opened, so those steps run on hand-made pages here."""

import unittest
from pathlib import Path

from jurisync.connectors.pje.filing import DraftClient

FIXTURES = Path(__file__).parent / "fixtures" / "trf1"
REPRESENTATIVE = {"cpf": "11122233396", "name": "MARIA SOUZA LIMA", "kind": "mae"}
AUTHOR = {"name": "PEDRO SOUZA LIMA", "representative": REPRESENTATIVE}
LOOKUP = ["representative_modal.html", "representative_type_chosen.html", "representative_person.html"]
STATE = '<input type="hidden" name="javax.faces.ViewState" value="j_id9"/>'


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def panel(*rows):
    """The Partes tab with the active pole's toolbar and the given rows."""
    rows = "".join(rows)
    page = fixture("party_toolbars.html")
    opening = page.index(">", page.index('id="poloAtivoDatalistDiv"')) + 1
    return page[:opening] + f"<table>{rows}</table>" + page[opening:]


def linking_page(author_label="PEDRO SOUZA LIMA - CPF: 529.982.247-25 (AUTOR)"):
    """A guess at the page after Confirmar: the represented party and a Vincular button (not seen live yet)."""
    submit = (
        "A4J.AJAX.Submit('formRep',event,{'similarityGroupingId':'formRep:btnVincular','containerId':'regionPartes'})"
    )
    return (
        f'<html><body>{STATE}<form id="formRep"><select name="formRep:parteRepresentada">'
        f'<option value="">Selecione</option><option value="77">{author_label}</option></select>'
        f'<input type="button" name="formRep:btnVincular" value="Vincular" onclick="{submit}"/></form></body></html>'
    )


class RepresentativeTests(unittest.TestCase):
    def client(self, answers, listed_after=False):
        client = DraftClient(None, portal="trf1-1g")
        sent = []

        def ajax(url, state, fields, container, action):
            sent.append((dict(fields), dict(action)))
            answer = answers.pop(0)
            return fixture(answer) if answer.endswith(".html") else answer

        client.ajax = ajax
        row = '<tr class="rich-table-row"><td>M. S. L. - CPF: 111.222.333-96 (REPRESENTANTE)</td></tr>'
        client.party_panel = lambda url: panel(row) if listed_after else panel()
        return client, sent

    def test_the_lookup_follows_the_captured_pages_and_links_to_the_author(self):
        client, sent = self.client([*LOOKUP, "representative_found.html", linking_page(), "<html></html>"], True)
        result = client.add_representative("draft", REPRESENTATIVE, AUTHOR["name"], "52998224725", panel())
        self.assertEqual(result, {"alreadyAdded": False, "name": "MARIA SOUZA LIMA", "type": "REPRESENTANTE"})
        opened, kind, person, search, confirm, link = sent
        self.assertIn("formVincularPoloAtivo:supResetarVincRepPA", opened[1])
        chosen = kind[0]["formTipoRepresentacao:comboRepresentacao"]
        self.assertIn(f'value="{chosen}">REPRESENTANTE<', fixture("representative_modal.html"))
        self.assertEqual(person[0]["preCadastroPessoaFisicaRepresentanteForm:tipoPessoaDecoration:tipoPessoa"], "F")
        cpf = next(value for name, value in search[0].items() if name.endswith("Representante_nrCPF"))
        self.assertEqual(cpf, "111.222.333-96")
        self.assertIn("preCadastroPessoaFisicaRepresentanteForm:btnConfirmarCadastro", confirm[1])
        self.assertEqual((link[0]["formRep:parteRepresentada"], next(iter(link[1]))), ("77", "formRep:btnVincular"))

    def test_an_invalid_cpf_or_another_registry_name_stops_before_confirmar(self):
        client, sent = self.client([*LOOKUP, "representative_invalid_cpf.html"])
        with self.assertRaisesRegex(
            RuntimeError, "did not find the representative's CPF: Documento em formato inválido"
        ):
            client.add_representative("draft", REPRESENTATIVE, AUTHOR["name"], "52998224725", panel())
        client, sent = self.client([*LOOKUP, "representative_found.html"])
        with self.assertRaisesRegex(RuntimeError, "belongs to another name"):
            client.add_representative("draft", {**REPRESENTATIVE, "name": "JOANA PEREIRA"}, "X", "1", panel())
        self.assertFalse(any("btnConfirmarCadastro" in str(action) for _, action in sent))

    def test_a_listed_representative_costs_nothing_and_a_stop_leaves_a_manual_step_with_the_reason(self):
        client, sent = self.client([])
        listed = panel('<tr class="rich-table-row"><td>M. S. L. - CPF: 111.222.333-96 (REPRESENTANTE)</td></tr>')
        self.assertTrue(client.add_representative("draft", REPRESENTATIVE, "X", "1", listed)["alreadyAdded"])
        self.assertEqual(sent, [])
        no_link = f"<html><body>{STATE}<form id='other'></form></body></html>"
        client, _ = self.client([*LOOKUP, "representative_found.html", no_link])
        checkpoint = {"manualSteps": []}
        result = client.link_representative("draft", checkpoint, AUTHOR, "52998224725", panel=panel())
        self.assertTrue(result["manual"])
        self.assertRegex(result["reason"], r"Automatic link stopped: PJe offered no Vincular .*forms \['other'\]")
        self.assertEqual(len(checkpoint["manualSteps"]), 1)

    def test_vincular_must_show_the_representative_in_the_active_pole(self):
        client, _ = self.client([*LOOKUP, "representative_found.html", linking_page(), "<html></html>"])
        with self.assertRaisesRegex(RuntimeError, "did not list the legal representative"):
            client.add_representative("draft", REPRESENTATIVE, AUTHOR["name"], "52998224725", panel())

    def test_trf5_keeps_the_manual_step(self):
        client = DraftClient(None, portal="trf5-1g")
        client.add_representative = lambda *args, **kwargs: self.fail("TRF5 does not link representatives")
        self.assertTrue(client.link_representative("draft", {}, AUTHOR, "52998224725")["manual"])


if __name__ == "__main__":
    unittest.main()
