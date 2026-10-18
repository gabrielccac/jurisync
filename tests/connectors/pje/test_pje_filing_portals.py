import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jurisync.connectors.pje.filing import WRITE_STAGES, DraftClient, run_stage
from jurisync.connectors.pje.filing.constants import FILING_PORTALS, filing_portal
from jurisync.connectors.pje.filing.store import LocalRunStore

PAYLOAD = {
    "subject": {"search": "s", "code": "1", "name": "S"},
    "authorCpf": "12345678909",
    "author": {"name": "EXAMPLE"},
}

REGISTER = """<form id="processoTrfForm"><input type="hidden" name="javax.faces.ViewState" value="s1"/>
<select name="processoTrfForm:areaDireitoCombo" onchange="A4J.AJAX.Submit('processoTrfForm',event,{'similarityGroupingId':'a1','containerId':'c1'})">
<option value="org.jboss.seam.ui.NoSelectionConverter.noSelectionValue">Selecione</option>
<option value="7">DIREITO PREVIDENCIÁRIO</option><option value="8">DIREITO CIVIL</option></select>
<input name="processoTrfForm:incluiProcessoButton" type="submit"/></form>"""
JURISDICTIONS = """<form id="processoTrfForm"><input type="hidden" name="javax.faces.ViewState" value="s2"/>
<select name="processoTrfForm:jurisdicaoCombo" onchange="A4J.AJAX.Submit('processoTrfForm',event,{'similarityGroupingId':'j1'})">
<option value="org.jboss.seam.ui.NoSelectionConverter.noSelectionValue">Selecione</option>
<option value="31">AP / Macapá - JEF</option></select></form>"""


class PortalConfigurationTests(unittest.TestCase):
    def test_each_portal_has_its_own_urls_and_only_trf5_writes(self):
        trf1, trf5 = filing_portal("trf1-1g"), filing_portal("trf5-1g")
        self.assertEqual(trf1.register_url, "https://pje1g.trf1.jus.br/pje/Processo/cadastrar.seam")
        self.assertEqual(trf5.update_url, "https://pje1g.trf5.jus.br/pje/Processo/update.seam")
        self.assertEqual(
            {key: config.writes for key, config in FILING_PORTALS.items()}, {"trf5-1g": True, "trf1-1g": False}
        )
        client = DraftClient(None, portal="trf1-1g")
        self.assertEqual((client.portal.host, client.pending_url), ("pje1g.trf1.jus.br", trf1.pending_url))
        with self.assertRaisesRegex(ValueError, "does not support trf1-2g"):
            filing_portal("trf1-2g")

    def test_trf1_payloads_never_reach_a_writing_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalRunStore(tmp)
            Path(tmp, "payload.json").write_text(json.dumps({**PAYLOAD, "portal": "trf1-1g"}))
            with mock.patch("jurisync.connectors.pje.filing.run._login", side_effect=AssertionError):
                for stage in sorted(WRITE_STAGES):
                    with self.subTest(stage), self.assertRaisesRegex(ValueError, "not enabled for trf1-1g"):
                        run_stage(stage, run=store, portal="trf1-1g", name="X", draft_id="1")
                with self.assertRaisesRegex(ValueError, "payload is for trf1-1g, not trf5-1g"):
                    run_stage("classification", run=store, portal="trf5-1g")

    def test_register_options_reads_choices_without_including(self):
        client = DraftClient(None, portal="trf1-1g")
        posts = []
        client.get = lambda url: REGISTER
        client.post = lambda url, data: posts.append((url, data)) or JURISDICTIONS
        result = client.register_options(area="DIREITO PREVIDENCIÁRIO")
        self.assertEqual(result["areas"], ["DIREITO PREVIDENCIÁRIO", "DIREITO CIVIL"])
        self.assertEqual(result["jurisdictions"], ["AP / Macapá - JEF"])
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0][0].startswith("https://pje1g.trf1.jus.br/"))
        self.assertNotIn("processoTrfForm:incluiProcessoButton", {k for k, v in posts[0][1].items() if v == k})


if __name__ == "__main__":
    unittest.main()
