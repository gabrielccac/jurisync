"""Offline contract tests for the publication orchestration seam."""

import json
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from integrations.juridiq.publications import PublicationError, prepare_publication, validate_decision


NUMBER = "1024853-70.2023.4.01.3400"


def publication(publication_id="pub-1", handled=False, text=None):
    return {
        "id": publication_id,
        "processNumber": NUMBER,
        "lawsuitId": "law-1",
        "officialDiary": "Synthetic diary",
        "type": "oab",
        "term": "123/XX",
        "publicationDate": "2026-09-08",
        "availabilityDate": "2026-09-08",
        "isHandled": handled,
        "readAt": "2026-09-08T11:00:00Z" if handled else None,
        "content": text if text is not None else '<p>Intime-se a parte autora para apresentar réplica.</p><div hidden><span>secret hidden</span></div>',
    }


def process(portal="1g", number=NUMBER, attachments=None):
    value = {
        "success": True,
        "numero_processo": number,
        "portal": portal,
        "jurisdiction": "TRF1",
        "processo_id": "999",
        "timeline": [
            {
                "date": "08/09/2026",
                "movements": [
                    {
                        "time": "10:00",
                        "movement": "Juntada de peça",
                        "movement_texts": ["Juntada de peça", "Disponibilizado no diário"],
                        "documents": [{"id": "1", "title": "Decisão"}],
                    },
                    {
                        "time": "10:00",
                        "movement": "Intimação",
                        "movement_texts": ["Intimação"],
                        "documents": [{"id": "2", "title": "Laudo"}, {"id": "3", "title": "Certidão"}],
                    },
                ],
            }
        ],
    }
    if attachments is not None:
        value["attachments"] = attachments
    return value


def extracted(doc_id="1", title="Decisão", text="O prazo para réplica é cinco dias.", status=200, extraction="extracted"):
    return {
        "id": doc_id,
        "title": title,
        "status": status,
        "content_type": "text/html",
        "bytes": len(text),
        "extractionStatus": extraction,
        "text": text,
        "sha256": "stable-test-hash",
    }


class PublicationChecks(unittest.TestCase):
    def bundle(self, **kwargs):
        return prepare_publication([publication()], "pub-1", **kwargs)

    def test_prepare_cleans_html_and_keeps_all_labels_and_distinct_occurrences(self):
        bundle = prepare_publication(
            [publication(text='<p>Visible <b>text</b></p><script>alert(1)</script><form><input value="x"></form>')],
            "pub-1",
            process=process(),
        )
        self.assertEqual(bundle["publication"]["text"], "Visible text")
        self.assertNotIn("form", json.dumps(bundle["publication"]).lower())
        self.assertEqual(bundle["timeline"][0]["movements"][0]["labels"], ["Juntada de peça", "Disponibilizado no diário"])
        self.assertEqual(bundle["timeline"][0]["movements"][1]["time"], "10:00")
        movement_sources = [item for item in bundle["sources"] if item["kind"] == "movement"]
        self.assertEqual(len(movement_sources), 3)
        self.assertEqual(len({item["sourceId"] for item in movement_sources}), 3)

    def test_complete_sufficient(self):
        bundle = self.bundle()
        result = validate_decision(bundle, {
            "publicationId": "pub-1",
            "status": "sufficient",
            "reason": "The publication states the requested act.",
            "missingInformation": [],
            "evidence": [{"sourceId": "publication:pub-1", "quote": "apresentar réplica"}],
            "selectedDocumentIds": [],
        })
        self.assertTrue(result["valid"])
        self.assertEqual(result["decision"]["status"], "sufficient")

    def test_empty_publication_can_request_context_without_impossible_quote(self):
        bundle = prepare_publication([publication(text="")], "pub-1")
        result = validate_decision(bundle, {
            "publicationId": "pub-1",
            "status": "needs_context",
            "reason": "The operative act must be retrieved from PJe.",
            "missingInformation": ["Retrieve the operative act."],
            "evidence": [],
            "selectedDocumentIds": [],
        })
        self.assertTrue(result["valid"])
        with self.assertRaises(PublicationError):
            validate_decision(self.bundle(), {
                "publicationId": "pub-1", "status": "needs_context", "reason": "Need context.",
                "missingInformation": ["Retrieve the operative act."], "evidence": [], "selectedDocumentIds": [],
            })

    def test_missing_context_can_select_document_and_document_wrapper_is_checked(self):
        docs = {"attachments": [extracted()], "numero_processo": NUMBER, "portal": "1g"}
        bundle = self.bundle(process=process(), documents=docs)
        self.assertIn("document:1g:1", {source["sourceId"] for source in bundle["sources"]})
        result = validate_decision(bundle, {
            "publicationId": "pub-1",
            "status": "needs_context",
            "reason": "The operative order is in the decision document.",
            "missingInformation": ["Confirm the operative order."],
            "evidence": [{"sourceId": "publication:pub-1", "quote": "apresentar réplica"}],
            "selectedDocumentIds": ["1"],
        })
        self.assertTrue(result["valid"])
        with self.assertRaises(PublicationError):
            self.bundle(process=process(), documents={"attachments": [], "numero_processo": NUMBER, "portal": "2g"})

    def test_no_action_and_review(self):
        no_action = prepare_publication([publication(text="Intime-se o INSS.")], "pub-1")
        self.assertTrue(validate_decision(no_action, {
            "publicationId": "pub-1", "status": "no_action", "reason": "It is directed to another recipient.",
            "missingInformation": [], "evidence": [{"sourceId": "publication:pub-1", "quote": "Intime-se o INSS."}],
            "selectedDocumentIds": [],
        })["valid"])
        review = self.bundle()
        self.assertTrue(validate_decision(review, {
            "publicationId": "pub-1", "status": "review", "reason": "The act and recipient are ambiguous.",
            "missingInformation": ["Confirm the recipient."], "evidence": [], "selectedDocumentIds": [],
        })["valid"])

    def test_invalid_decision_references_types_and_required_fields(self):
        bundle = self.bundle(process=process())
        valid = {
            "publicationId": "pub-1", "status": "needs_context", "reason": "Need the order.",
            "missingInformation": ["Confirm the order."],
            "evidence": [{"sourceId": "publication:pub-1", "quote": "réplica"}],
            "selectedDocumentIds": ["1"],
        }
        cases = [
            {**valid, "publicationId": "wrong"},
            {**valid, "publicationId": True},
            {**valid, "selectedDocumentIds": ["unknown"]},
            {**valid, "evidence": [{"sourceId": "publication:pub-1", "quote": "not present"}]},
            {**valid, "reason": 5},
            {**valid, "missingInformation": "not-array"},
            {**valid, "extra": "reject"},
            {key: value for key, value in valid.items() if key != "reason"},
        ]
        for decision in cases:
            with self.subTest(decision=decision):
                with self.assertRaises(PublicationError):
                    validate_decision(bundle, decision)

    def test_process_and_document_safety(self):
        for bad_process in (
            process(number="1024853-70.2023.4.01.3401"),
            process(portal="3g"),
        ):
            with self.subTest(bad_process=bad_process):
                with self.assertRaises(PublicationError):
                    self.bundle(process=bad_process)
        failed = extracted(status=500, extraction="http_error", text="")
        bundle = self.bundle(process=process(attachments=[failed]))
        self.assertNotIn("document:1g:1", {source["sourceId"] for source in bundle["sources"]})
        self.assertFalse(bundle["documents"][0]["textAvailable"])
        with self.assertRaises(PublicationError):
            self.bundle(process=process(), documents=[{**extracted(), "id": "unknown"}])

    def test_handled_requires_opt_in(self):
        with self.assertRaises(PublicationError):
            prepare_publication([publication(handled=True)], "pub-1")
        self.assertEqual(prepare_publication([publication(handled=True)], "pub-1", include_handled=True)["state"], "awaiting_assessment")

    def test_cli_workflow_and_output_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot.json"
            decision = root / "decision.json"
            bundle_path = root / "bundle.json"
            result_path = root / "validated.json"
            snapshot.write_text(json.dumps([publication()]), encoding="utf-8-sig")
            command = [sys.executable, "-m", "integrations.juridiq.publications"]
            prepared = subprocess.run(command + ["prepare", "--snapshot", str(snapshot), "--publication-id", "pub-1", "--output", str(bundle_path)], capture_output=True, text=True)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            decision.write_text(json.dumps({
                "publicationId": "pub-1", "status": "sufficient", "reason": "Enough publication context.",
                "missingInformation": [], "evidence": [{"sourceId": "publication:pub-1", "quote": "apresentar réplica"}],
                "selectedDocumentIds": [],
            }), encoding="utf-8")
            validated = subprocess.run(command + ["validate", "--bundle", str(bundle_path), "--decision", str(decision), "--output", str(result_path)], capture_output=True, text=True)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertTrue(json.loads(result_path.read_text(encoding="utf-8"))["valid"])
            overwrite = subprocess.run(command + ["prepare", "--snapshot", str(snapshot), "--publication-id", "pub-1", "--output", str(snapshot)], capture_output=True, text=True)
            self.assertNotEqual(overwrite.returncode, 0)
            self.assertEqual(json.loads(snapshot.read_text(encoding="utf-8-sig"))[0]["id"], "pub-1")
            overwrite = subprocess.run(command + ["validate", "--bundle", str(bundle_path), "--decision", str(decision), "--output", str(result_path)], capture_output=True, text=True)
            self.assertNotEqual(overwrite.returncode, 0)

    def test_source_ids_survive_new_rows_and_url_changes_without_merging_duplicates(self):
        original = process()
        original["timeline"][0]["movements"].append(copy.deepcopy(original["timeline"][0]["movements"][0]))
        first = self.bundle(process=original)
        changed = copy.deepcopy(original)
        changed["timeline"].insert(0, {"date": "09/09/2026", "movements": [{"time": "12:00", "movement": "New act", "documents": []}]})
        changed["timeline"][1]["movements"][0]["documents"][0]["download_url"] = "https://ignored.invalid/rotating-token"
        second = self.bundle(process=changed)
        self.assertEqual(first["timeline"][0], second["timeline"][1])
        ids = [source["sourceId"] for source in first["sources"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn("rotating-token", json.dumps(second))
        self.assertEqual(first["publication"]["lawsuitId"], "law-1")
        self.assertEqual(first["publication"]["sourceIds"]["officialDiary"], "Synthetic diary")
        self.assertEqual(first["publication"]["monitoringMetadata"]["term"], "123/XX")
        self.assertNotIn("secret hidden", first["publication"]["text"])

    def test_malformed_inputs_fail_closed(self):
        for value in (None, {}, "not an array"):
            bad = process()
            bad["attachments"] = value
            with self.subTest(value=value), self.assertRaises(PublicationError):
                self.bundle(process=bad)
        for publication_id in (None, "", True):
            with self.subTest(id=publication_id), self.assertRaises(PublicationError):
                prepare_publication([publication()], publication_id)
        with self.assertRaises(PublicationError):
            prepare_publication([publication()], "pub-1", include_handled="false")
        with self.assertRaises(PublicationError):
            prepare_publication([{**publication(), "isHandled": True, "readAt": None}], "pub-1")


if __name__ == "__main__":
    unittest.main()
