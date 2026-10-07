import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from jurisync.connectors.juridiq.api import JuridiqApi
from jurisync.workflows.publications.pipeline import run_publication_pipeline

TEMPLATE_TITLE = "Manifestação - (Nome Cliente)"
PROFILE = {"id": "synthetic-test", "taskTemplates": {"manifestacao": TEMPLATE_TITLE}}
PUBLICATION_EVIDENCE = [{"sourceId": "publication:pub-1", "quote": "publication"}]


class Response:
    def __init__(self, payload, status=200):
        self.status_code, self.text = status, json.dumps(payload)

    def json(self):
        return json.loads(self.text)


class FakeJuridiq:
    def __init__(self, test):
        self.test, self.created, self.read = test, [], []

    def __call__(self, method, url, headers, body):
        parts = urlsplit(url)
        path, query = parts.path, parse_qs(parts.query)
        if path == "/auth/login":
            return Response({"token": f"token-{json.loads(body)['email']}"})
        self.test.assertIn("pipeline-test", headers["authorization"])  # every call uses the configured account
        if path == "/publication":
            data = (
                [
                    {
                        "id": "pub-1",
                        "processNumber": "1024853-70.2023.4.01.3400",
                        "lawSuitId": "law-1",
                        "content": "publication",
                        "readAt": None,
                    }
                ]
                if query["page"] == ["1"]
                else []
            )
            return Response({"data": data})
        if path == "/task/task-templates":
            return Response(
                {
                    "data": [
                        {
                            "id": "tpl-0",
                            "title": TEMPLATE_TITLE,
                            "columnId": "col-1",
                            "priority": "Baixa",
                            "officeId": "office-1",
                            "responsibles": [{"id": "user-1"}],
                            "tags": [{"id": "tag-1"}],
                        }
                    ]
                }
            )
        if path == "/task/" and method == "GET":
            return Response({"data": []})
        if path == "/task/" and method == "POST":
            self.created.append(json.loads(body))
            return Response({"id": "created-1"}, 201)
        if path == "/task/created-1":
            return Response({"id": "created-1"})
        if path == "/publication/read-movements-many" and method == "PATCH":
            self.read.append(json.loads(body))
            return Response({"ok": True})
        raise AssertionError(f"unexpected {method} {path}")


def deduplicate(*, publications, profile):
    return {
        "groups": [
            {
                "canonicalActKey": f"{item['processNumber']}|{item['id']}",
                "publicationIds": [item["id"]],
                "primaryPublicationId": item["id"],
                "duplicatePublicationIds": [],
                "status": "process",
                "reason": "single publication",
            }
            for item in publications
        ]
    }


def route_with(evidence, **task_fields):
    def route(*, publication, **_):
        return {
            "publicationId": publication["id"],
            "status": "sufficient",
            "classificationRule": "generic deadline directed to claimant",
            "evidence": evidence,
            "tasks": [
                {
                    "type": "manifestacao",
                    "clientName": "Cliente",
                    "description": "Responder a publicação.",
                    "initialDate": "2026-09-08",
                    "obligationKey": "pub-1:manifestacao",
                    **task_fields,
                }
            ],
        }

    return route


class Agent:
    def __init__(self, assess, route, enrich=None):
        self.deduplicate, self.assess, self.route = deduplicate, assess, route
        if enrich:
            self.enrich = enrich


def sufficient(evidence=PUBLICATION_EVIDENCE):
    return lambda *, publication, **_: {
        "publicationId": publication["id"],
        "status": "sufficient",
        "reason": "publication is clear",
        "evidence": evidence,
        "missingInformation": [],
    }


class PipelineTest(unittest.TestCase):
    def run_pipeline(self, agent):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = {
            "JURIDIQ_EMAIL": "pipeline-test@example.invalid",
            "JURIDIQ_PASSWORD": "test-only",
            "JURIDIQ_ALLOW_WRITES": "true",
            "JURIDIQ_API_BASE_URL": "https://pipeline.example.invalid",
            "JURIDIQ_LOGIN_URL": "https://pipeline.example.invalid/auth/login",
            "JURIDIQ_SESSION_FILE": str(Path(directory.name) / "session.json"),
        }
        self.fake = FakeJuridiq(self)
        self.state_file = Path(directory.name) / "state.json"
        with mock.patch.dict(os.environ, env):
            return run_publication_pipeline(
                agent=agent, profile=PROFILE, state_file=self.state_file, api=JuridiqApi(send=self.fake), limit=1
            )

    def test_assesses_routes_creates_and_verifies_a_task(self):
        result = self.run_pipeline(Agent(sufficient(), route_with(PUBLICATION_EVIDENCE)))
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["readMarked"], ["pub-1"])
        self.assertEqual(result["errors"], [])
        payload = self.fake.created[0]
        self.assertEqual(payload["title"], "Manifestação - Cliente")
        self.assertEqual(
            (payload["responsiblesId"], payload["tags"], payload["columnId"]), (["user-1"], ["tag-1"], "col-1")
        )
        self.assertIn("Fonte: publicação pub-1", payload["description"])
        self.assertEqual(self.fake.read, [{"ids": ["pub-1"]}])
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state["publications"]["pub-1"]["status"], "completed")

    def test_validates_evidence_against_an_enriched_source(self):
        enriched = []

        def assess(*, publication, context=None, **_):
            if not enriched:
                return {
                    "publicationId": publication["id"],
                    "status": "needs_context",
                    "reason": "act is missing",
                    "evidence": [],
                    "missingInformation": ["operative act"],
                }
            return {
                "publicationId": publication["id"],
                "status": "sufficient",
                "reason": "act found",
                "evidence": [{"sourceId": "document:pje:1", "quote": "official act"}],
                "missingInformation": [],
            }

        def enrich(**_):
            enriched.append(True)
            return {"sources": [{"sourceId": "document:pje:1", "text": "official act"}]}

        result = self.run_pipeline(
            Agent(assess, route_with([{"sourceId": "document:pje:1", "quote": "official act"}]), enrich)
        )
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["errors"], [])

    def test_rejects_evidence_with_a_wrong_or_unknown_source(self):
        for evidence in (
            [{"sourceId": "document:pje:1", "quote": "publication"}],
            [{"sourceId": "document:pje:missing", "quote": "publication"}],
        ):
            with self.subTest(evidence=evidence):
                result = self.run_pipeline(Agent(sufficient(evidence), route_with(evidence)))
                self.assertEqual(result["created"], [])
                self.assertEqual(len(result["errors"]), 1)
                self.assertIn("evidence", result["errors"][0]["error"])
                self.assertEqual(result["readMarked"], [])

    def test_rejects_an_explicit_task_outside_the_loaded_lawsuit(self):
        result = self.run_pipeline(Agent(sufficient(), route_with(PUBLICATION_EVIDENCE, existingTaskId="task-other")))
        self.assertEqual((result["existing"], result["readMarked"]), ([], []))
        self.assertIn("not loaded for lawsuit", result["errors"][0]["error"])

    def test_requires_absolute_state_path_and_agent_methods(self):
        with self.assertRaisesRegex(ValueError, "absolute"):
            run_publication_pipeline(
                agent=Agent(sufficient(), route_with([])), profile=PROFILE, state_file="relative.json"
            )
        with self.assertRaisesRegex(ValueError, "agent.assess"):
            run_publication_pipeline(agent=object(), profile=PROFILE, state_file="/tmp/state.json")


if __name__ == "__main__":
    unittest.main()
