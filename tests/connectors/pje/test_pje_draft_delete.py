import unittest

from jurisync.connectors.pje.filing import DraftClient

SUMMARY = "PJEC - PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL Auxílio por Incapacidade Temporária {name} X INSS"


def row(index, draft_id, name="EDVONALDO BARBOSA DA SILVA"):
    form = f"consultaProcessoNaoProtocoladoGridList:{index}:j_id265:j_id266"
    remover = (
        f"if (!confirm('Deseja realmente excluir o processo?')) return false;;"
        f"A4J.AJAX.Submit('{form}',event,{{'similarityGroupingId':'{form}:j_id279',"
        f"'containerId':'{form}:j_id267','parameters':{{'{form}:j_id279':'{form}:j_id279',"
        f"'firstResult':0,'page':1}} ,'status':'_viewRoot:status'}} );return false;"
    )
    return f"""<tr><td><a href="/pje/Processo/update.seam?idProcesso={draft_id}&id={draft_id}">{draft_id}</a></td>
    <td>{SUMMARY.format(name=name)}</td>
    <td><form id="{form}"><input type="hidden" name="{form}" value="{form}"/>
    <input type="hidden" name="autoScroll" value=""/>
    <a id="{form}:j_id269" title="Editar" href="#"></a>
    <a id="{form}:j_id279" title="Remover" href="#" onclick="{remover}"></a></form></td></tr>"""


def page(*rows):
    return (
        '<html><input type="hidden" name="javax.faces.ViewState" value="j_id9"/>'
        f'<table id="consultaProcessoNaoProtocoladoGridList">{"".join(rows)}</table></html>'
    )


class Client(DraftClient):
    """A client whose search shows `pages` in turn and which records the delete POST."""

    def __init__(self, pages):
        super().__init__(None)
        self.pages = list(pages)
        self.posted = []

    def _pending_results(self, name=None, cpf=None):
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    def ajax(self, url, state, fields, container, action):
        self.posted.append((container, fields, action))
        return ""


class DeleteDraftTests(unittest.TestCase):
    def test_the_rows_own_remover_link_is_posted_with_its_paging_values(self):
        before = page(row(0, "4181708"), row(1, "4181839"), row(2, "4182004"))
        after = page(row(0, "4181708"), row(1, "4181839"))
        client = Client([before, after])
        result = client.delete_draft("4182004", "EDVONALDO BARBOSA DA SILVA")
        container, fields, action = client.posted[0]
        form = "consultaProcessoNaoProtocoladoGridList:2:j_id265:j_id266"
        self.assertEqual(result, {"draftId": "4182004", "deleted": True})
        self.assertEqual(container, f"{form}:j_id267")
        self.assertEqual(fields[form], form)
        self.assertEqual(action[f"{form}:j_id279"], f"{form}:j_id279")
        self.assertEqual((action["firstResult"], action["page"]), ("0", "1"))
        self.assertEqual(len(client.posted), 1)

    def test_a_draft_of_another_author_is_never_deleted(self):
        client = Client([page(row(0, "4181708", name="OUTRA PESSOA DA SILVA"))])
        with self.assertRaisesRegex(RuntimeError, "does not belong"):
            client.delete_draft("4181708", "EDVONALDO BARBOSA DA SILVA")
        self.assertEqual(client.posted, [])

    def test_a_missing_or_repeated_draft_deletes_nothing(self):
        for pages in ([page(row(0, "1"))], [page(row(0, "4182004"), row(1, "4182004"))]):
            client = Client(pages)
            with self.assertRaisesRegex(RuntimeError, "Expected one draft"):
                client.delete_draft("4182004", "EDVONALDO BARBOSA DA SILVA")
            self.assertEqual(client.posted, [])

    def test_the_delete_fails_loudly_if_the_draft_is_still_listed(self):
        still = page(row(0, "4182004"))
        client = Client([still, still])
        with self.assertRaisesRegex(RuntimeError, "still lists"):
            client.delete_draft("4182004", "EDVONALDO BARBOSA DA SILVA")

    def test_bad_input_is_rejected_before_any_request(self):
        client = Client([page()])
        for draft_id, name in (("4182004; DROP", "EDVONALDO"), ("", "EDVONALDO"), ("4182004", "  ")):
            with self.assertRaises(ValueError):
                client.delete_draft(draft_id, name)
        self.assertEqual(client.posted, [])


if __name__ == "__main__":
    unittest.main()
