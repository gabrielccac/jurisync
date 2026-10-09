import os
import unittest
from unittest import mock

from jurisync import portals


class PortalRegistryTest(unittest.TestCase):
    def test_urls_derive_from_host_and_root(self):
        trf5 = portals.pje("trf5-1g")
        self.assertEqual(trf5.login_url, "https://pje1g.trf5.jus.br/pje/login.seam")
        self.assertEqual(trf5.search_url, "https://pje1g.trf5.jus.br/pje/Processo/ConsultaProcesso/listView.seam")
        self.assertEqual(portals.eproc("jfsc").login_url, "https://eproc.jfsc.jus.br/eprocV2/externo_controlador.php")

    def test_lookup_is_strict_about_system_and_wiring(self):
        self.assertIs(portals.pje(portals.pje("trf1-1g")), portals.pje("trf1-1g"))
        for system, key in (
            ("pje", "trf4-1g-sc"),
            ("eproc", "trf1-1g"),
            ("eproc", "tjrs-1g"),
            ("pje", "1g"),
            ("pje", None),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "portal must be one of"):
                portals.get(key, system)
        self.assertEqual(portals.keys("pje"), ("trf1-1g", "trf1-2g", "trf5-1g"))
        self.assertEqual(portals.keys("eproc"), ("trf4-1g-sc", "trf4-1g-rs", "trf6-1g", "trf6-2g"))

    def test_old_names_are_aliases_and_ids_carry_the_system(self):
        self.assertIs(portals.eproc("jfsc"), portals.eproc("trf4-1g-sc"))
        self.assertEqual(portals.eproc("jfrs").key, "trf4-1g-rs")
        self.assertEqual((portals.pje("trf1-1g").id, portals.eproc("jfsc").id), ("pje-trf1-1g", "eproc-trf4-1g-sc"))

    def test_env_reads_prefix_then_legacy_prefix(self):
        jfrs = portals.eproc("jfrs")
        with mock.patch.dict(os.environ, {"JRFS_USUARIO": "legacy"}, clear=True):
            self.assertEqual(jfrs.env("USUARIO"), "legacy")
        with mock.patch.dict(os.environ, {"JRFS_USUARIO": "legacy", "JFRS_USUARIO": "current"}, clear=True):
            self.assertEqual(jfrs.env("USUARIO"), "current")
        self.assertEqual(jfrs.env_name("USUARIO"), "JFRS_USUARIO")

    def test_host_and_process_routing(self):
        self.assertEqual(portals.for_host("eproc1g.tjrs.jus.br").key, "tjrs-1g")
        self.assertIsNone(portals.for_host("evil.example"))
        self.assertEqual([p.key for p in portals.for_process("5012366-88.2025.4.04.7104")], ["trf4-1g-rs"])
        self.assertEqual([p.key for p in portals.for_process("5012366-88.2025.4.04.7204")], ["trf4-1g-sc"])
        self.assertEqual([p.key for p in portals.for_process("5012366-88.2025.4.04.0000")], ["trf4-2g"])
        self.assertEqual([p.key for p in portals.for_process("0001271-74.2026.4.05.8304")], ["trf5-1g"])
        self.assertEqual([p.key for p in portals.for_process("1024853-70.2023.4.01.3400")], ["trf1-1g", "trf1-2g"])
        self.assertEqual(portals.for_process("0001271-74.2026.8.17.0001"), [])


if __name__ == "__main__":
    unittest.main()
