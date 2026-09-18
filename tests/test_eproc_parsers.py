import unittest

from integrations.eproc.parsers import parse_process_detail, parse_process_list


class EprocParserTests(unittest.TestCase):
    def test_list_and_detail_enriched_fields(self):
        listing = '''<table class="infraTable"><tr class="infraTrClara"><td></td><td><input class="infraCheckbox" value="7"><a href="controlador.php?acao=processo_selecionar&num_processo=123">0000001-23.2026.4.04.7207</a><b>Juízo: </b>SCUABI011</td><td>Classe</td><td>Assunto</td><td>Evento</td><td>01/01</td><td>02/01</td><td>03/01</td></tr></table>'''
        detail = '''<table id="tblEventos"><tr id="trEvento9" class="infraEventoImportante"><td>9</td><td>01/01/2026</td><td><label class="infraEventoDescricao">Publicado</label></td><td><label class="infraEventoUsuario">sistema</label></td><td><a class="infraLinkDocumento" data-doc="8" data-nome="A" data-mimetype="pdf" href="controlador.php?acao=acessar_documento&doc=8">A1</a></td></tr></table>'''
        process = parse_process_list(listing)[0]
        parsed = parse_process_detail(detail)
        self.assertEqual((process["id"], process["juizo"], process["classeCodigo"]), ("7", "SCUABI011", None))
        self.assertEqual((parsed["eventCount"], parsed["documentCount"], parsed["eventos"][0]["documentos"][0]["tipo"]), (1, 1, "pdf"))


if __name__ == "__main__":
    unittest.main()
