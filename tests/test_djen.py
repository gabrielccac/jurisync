"""Offline checks DJEN: sem credenciais, sem rede (urllib mockado)."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from integrations.portals.djen import client, normalize, store

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
        {"id": 1388173015, "comunicacao_id": 740120612, "advogado_id": 1458369,
         "advogado": {"id": 1458369, "nome": "VICTOR HUGO VALERIANO PINTO", "numero_oab": "14663", "uf_oab": "PB"}},
        {"id": 1388173016, "comunicacao_id": 740120612, "advogado_id": 1579254,
         "advogado": {"id": 1579254, "nome": "GRAZIELE ALVES DA SILVA", "numero_oab": "48647", "uf_oab": "PE"}},
    ],
}


class DjenChecks(unittest.TestCase):
    def test_normalize_fixture(self):
        c = normalize.normalize_item(FIXTURE)
        self.assertEqual(c["id"], 740120612)
        self.assertEqual(c["numero_processo_mascara"], "0003527-90.2026.4.05.8303")
        self.assertEqual(c["numero_processo"], "00035279020264058303")
        self.assertTrue(c["is_pe"])
        self.assertEqual(c["advogados"][0]["numero_digits"], "14663")
        self.assertEqual(c["advogados"][1]["uf_oab"], "PE")
        self.assertEqual(c["hash"], "lx8NKdwzYKdioGfPTKe7QWRbD1mrEX")

    def test_oab_digits_ignora_variante(self):
        self.assertEqual(normalize.normalize_oab_digits("14663-A"), "14663")
        self.assertEqual(normalize.normalize_oab_digits("PB14663"), "14663")

    def test_pe_filter(self):
        self.assertTrue(normalize.is_pe_process("00035279020264058303", "38ª Vara Federal PE"))
        self.assertFalse(normalize.is_pe_process("00000000000000000000", "30ª Vara Federal CE"))

    def test_classify_laudo_sem_pje(self):
        fam, secao, detalhar, _ = normalize.classify_family(FIXTURE["texto"])
        self.assertEqual(fam, "laudo")
        self.assertEqual(secao, "POP-§2")
        self.assertFalse(detalhar)

    def test_classify_generico_pede_pje(self):
        fam, _, detalhar, motivo = normalize.classify_family("Intimação sobre o último ato (ID de origem).")
        self.assertTrue(detalhar)
        self.assertEqual(motivo, "referencia_generica")
        self.assertEqual(fam, "indefinido")

    def test_route_hint_cancelada_pede_pje(self):
        raw = dict(FIXTURE, data_cancelamento="2026-09-29")
        hint = normalize.route_hint(normalize.normalize_item(raw))
        self.assertTrue(hint["detalhar_pje"])

    def test_build_url(self):
        url = client.build_url("14663", "PB", "2026-09-28", "2026-09-29", "TRF5", 1, 50)
        self.assertIn("numeroOab=14663", url)
        self.assertIn("ufOab=PB", url)
        self.assertIn("siglaTribunal=TRF5", url)

    def test_itens_limite(self):
        with self.assertRaises(ValueError):
            client.fetch_page("14663", "PB", "2026-09-28", "2026-09-29", None, 1, 1000)
        with self.assertRaises(ValueError):
            client.fetch_page("", "PB", "2026-09-28", "2026-09-29")

    def test_fetch_page_dead_letter_apos_retries(self):
        with patch.object(client, "_read_json", return_value=({"status": "error"}, 500)):
            with patch.object(client.time, "sleep", return_value=None):
                with self.assertRaises(client.DjenError):
                    client.fetch_page("14663", "PB", "2026-09-28", "2026-09-29")

    def test_fetch_all_pagina(self):
        p1 = {"status": "success", "count": 3, "items": [{"id": 1}, {"id": 2}]}
        p2 = {"status": "success", "count": 3, "items": [{"id": 3}]}
        with patch.object(client, "fetch_page", side_effect=[p1, p2]) as fp:
            count, items = client.fetch_all("14663", "PB", "2026-09-28", "2026-09-29", "TRF5", 2)
        self.assertEqual(count, 3)
        self.assertEqual([i["id"] for i in items], [1, 2, 3])
        self.assertEqual(fp.call_count, 2)

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

    def test_stats_por_orgao(self):
        items = [normalize.normalize_item(FIXTURE), normalize.normalize_item(FIXTURE)]
        s = store.stats(items)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["por_orgao"]["38ª Vara Federal PE"], 2)


if __name__ == "__main__":
    unittest.main()
