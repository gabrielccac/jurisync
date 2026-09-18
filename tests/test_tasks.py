import unittest

from integrations.juridiq.tasks import TASK_TEMPLATE_TITLES, TaskDraftError, build_task_drafts


def template(title):
    return {
        "id": "template-1", "title": title, "columnId": "column-1",
        "priority": "Baixa", "officeId": "office-1",
        "responsibles": [{"id": "user-1"}], "tags": [{"id": "tag-1"}],
    }


def bundle():
    return {"publicationId": "pub-1", "publication": {"lawSuitId": "law-1"}}


def routing(**overrides):
    value = {
        "publicationId": "pub-1", "status": "sufficient",
        "evidence": [{"sourceId": "publication:pub-1", "quote": "ato"}],
        "tasks": [{
            "type": "manifestacao", "clientName": "Cliente",
            "description": "Verificar o ato da publicação pub-1.",
            "initialDate": "2026-09-08", "obligationKey": "pub-1:manifestacao",
        }],
    }
    value.update(overrides)
    return value


class TaskDraftChecks(unittest.TestCase):
    def test_all_skill_models_are_supported(self):
        self.assertEqual(len(TASK_TEMPLATE_TITLES), 17)

    def test_builds_grounded_payload_with_exact_date(self):
        result = build_task_drafts(
            bundle(), routing(), [template(TASK_TEMPLATE_TITLES["manifestacao"])],
        )
        self.assertEqual(result["drafts"][0]["payload"]["initialDate"], "2026-09-08")
        self.assertEqual(result["drafts"][0]["obligationKey"], "pub-1:manifestacao")

    def test_rejects_missing_evidence_invalid_date_and_missing_obligation_key(self):
        cases = [
            routing(evidence=[]),
            routing(tasks=[{**routing()["tasks"][0], "initialDate": "tomorrow"}]),
            routing(tasks=[{key: value for key, value in routing()["tasks"][0].items() if key != "obligationKey"}]),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(TaskDraftError):
                build_task_drafts(bundle(), value, [template(TASK_TEMPLATE_TITLES["manifestacao"])])

    def test_old_same_title_task_does_not_suppress_new_obligation(self):
        result = build_task_drafts(
            bundle(), routing(), [template(TASK_TEMPLATE_TITLES["manifestacao"])],
            existing_tasks=[{"id": "old", "lawSuitId": "law-1", "title": "Manifestação - Cliente"}],
        )
        self.assertEqual(result["status"], "draft_only")

    def test_no_action_does_not_require_lawsuit(self):
        result = build_task_drafts(
            {"publicationId": "pub-1", "publication": {}},
            {"publicationId": "pub-1", "status": "no_action", "tasks": []},
            [],
        )
        self.assertEqual(result["status"], "no_action_or_existing")


if __name__ == "__main__":
    unittest.main()
