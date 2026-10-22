import os
import unittest
from unittest import mock

from jurisync import portals


class PortalRegistryTest(unittest.TestCase):
    def test_urls_derive_from_host_and_root(self):
        trf5 = portals.pje("trf5-1g")
        self.assertEqual(trf5.login_url, "https://pje1g.trf5.jus.br/pje/login.seam")
        self.assertEqual(trf5.search_url, "https://pje1g.trf5.jus.br/pje/Processo/ConsultaProcesso/listView.seam")
        tjap = portals.pje("tjap-1g")
        self.assertEqual(tjap.login_url, "https://pje.tjap.jus.br/1g/login.seam")
        self.assertEqual(tjap.search_url, "https://pje.tjap.jus.br/1g/Processo/ConsultaProcesso/listView.seam")
        self.assertEqual(portals.eproc("jfsc").login_url, "https://eproc.jfsc.jus.br/eprocV2/")

    def test_lookup_is_strict_about_system_and_wiring(self):
        self.assertIs(portals.pje(portals.pje("trf1-1g")), portals.pje("trf1-1g"))
        for system, key in (
            ("pje", "trf4-1g-sc"),
            ("eproc", "trf1-1g"),
            ("pje", "1g"),
            ("pje", None),
        ):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "portal must be one of"):
                portals.get(key, system)
        self.assertEqual(
            portals.keys("pje"),
            (
                "trf1-1g",
                "trf1-2g",
                "trf5-1g",
                "trf5-2g",
                "trf3-1g",
                "trf3-2g",
                "trf5-tru",
                "tjap-1g",
                "tjpe-1g",
                "tjpe-2g",
            ),
        )
        self.assertEqual(
            portals.keys("eproc"),
            ("trf4-1g-sc", "trf4-1g-rs", "trf6-1g", "trf6-2g", "trf4-1g-pr", "trf4-2g", "tjrs-1g"),
        )

    def test_old_names_are_aliases_and_ids_carry_the_system(self):
        self.assertIs(portals.eproc("jfsc"), portals.eproc("trf4-1g-sc"))
        self.assertEqual(portals.eproc("jfrs").key, "trf4-1g-rs")
        self.assertEqual((portals.pje("trf1-1g").id, portals.eproc("jfsc").id), ("pje-trf1-1g", "eproc-trf4-1g-sc"))

    def test_env_reads_prefix_then_legacy_prefix(self):
        jfrs = portals.eproc("jfrs")
        with mock.patch.dict(os.environ, {"JRFS_USUARIO": "legacy"}, clear=True):
            self.assertEqual(jfrs.env("USUARIO"), "legacy")
        with mock.patch.dict(os.environ, {"JFRS_USUARIO": "legacy", "TRF4_1G_RS_USUARIO": "current"}, clear=True):
            self.assertEqual(jfrs.env("USUARIO"), "current")
        self.assertEqual(jfrs.env_name("USUARIO"), "TRF4_1G_RS_USUARIO")

    def test_env_accepts_canonical_names_and_the_earlier_portuguese_ones(self):
        tjrs = portals.eproc("tjrs-1g")
        with mock.patch.dict(os.environ, {"TJRS_1G_USERNAME": "new"}, clear=True):
            self.assertEqual(tjrs.env("USERNAME"), "new")
        with mock.patch.dict(os.environ, {"TJRS_1G_USUARIO": "old", "TJRS_1G_EXPORT_DATA": "qr"}, clear=True):
            self.assertEqual((tjrs.env("USERNAME"), tjrs.env("2FA_DATA")), ("old", "qr"))
        with mock.patch.dict(os.environ, {"TJRS_1G_USUARIO": "old", "TJRS_1G_USERNAME": "new"}, clear=True):
            self.assertEqual(tjrs.env("USERNAME"), "new")
        self.assertEqual(tjrs.env_name("2FA_DATA"), "TJRS_1G_2FA_DATA")

    def test_host_and_process_routing(self):
        self.assertEqual(portals.for_host("eproc1g.tjrs.jus.br").key, "tjrs-1g")
        self.assertEqual(portals.for_host("pje.tjap.jus.br").key, "tjap-1g")
        self.assertIsNone(portals.for_host("evil.example"))
        self.assertEqual([p.key for p in portals.for_process("5012366-88.2025.4.04.7104")], ["trf4-1g-rs"])
        self.assertEqual([p.key for p in portals.for_process("5012366-88.2025.4.04.7204")], ["trf4-1g-sc"])
        self.assertEqual([p.key for p in portals.for_process("5012366-88.2025.4.04.0000")], ["trf4-2g"])
        self.assertEqual([p.key for p in portals.for_process("0001271-74.2026.4.05.8304")], ["trf5-1g", "trf5-2g"])
        self.assertEqual(portals.for_host("pje2g.trf5.jus.br").key, "trf5-2g")
        # TJPE serves both instances on one host: the application path tells them apart.
        self.assertEqual(portals.for_host("pje.cloud.tjpe.jus.br", "/2g/Processo/x.seam").key, "tjpe-2g")
        self.assertEqual(portals.for_host("pje.cloud.tjpe.jus.br", "/1g/Painel/x.seam").key, "tjpe-1g")
        self.assertEqual(portals.for_host("pje.cloud.tjpe.jus.br").key, "tjpe-1g")
        self.assertEqual([p.key for p in portals.for_process("5001234-56.2026.4.03.6100")], ["trf3-1g", "trf3-2g"])
        # The TRU keeps the JEF number: only its own host points to it, never the number alone.
        self.assertEqual(portals.for_host("pjett.trf5.jus.br").key, "trf5-tru")
        self.assertEqual(portals.for_host("pje2g.trf3.jus.br").key, "trf3-2g")
        self.assertEqual([p.key for p in portals.for_process("1024853-70.2023.4.01.3400")], ["trf1-1g", "trf1-2g"])
        self.assertEqual([p.key for p in portals.for_process("0001234-56.2026.8.03.0001")], ["tjap-1g"])
        self.assertEqual([p.key for p in portals.for_process("0001271-74.2026.8.17.0001")], ["tjpe-1g", "tjpe-2g"])
        self.assertEqual(portals.for_process("0001271-74.2026.8.06.0001"), [])  # no TJCE connector


if __name__ == "__main__":
    unittest.main()
