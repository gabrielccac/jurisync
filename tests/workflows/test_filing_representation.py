import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from jurisync.connectors.pje.filing.store import LocalRunStore
from jurisync.workflows.filing.prepare import finalize_review, prepare_folder
from jurisync.workflows.filing.profile import load_profile
from jurisync.workflows.filing.representation import representation_problems, representation_signals

PROFILE = load_profile("victor")
TODAY = date(2026, 10, 17)
MOTHER = {"cpf": "99988877714", "name": "MARIA MAE", "kind": "mae"}
FACTS = {
    "authorCpf": "12345678909",
    "author": {"name": "EXAMPLE"},
    "classification": {"jurisdiction": "PE / Petrolina", "judicialClass": "CLASS"},
    "subject": {"search": "TEST", "code": "1", "name": "TEST"},
    "characteristics": {"claimValue": "5.000,00"},
    "fiscalDaLei": False,
}


def with_author(**author):
    return {**FACTS, "author": {"name": "EXAMPLE", **author}}


class RepresentationRuleTests(unittest.TestCase):
    def test_petition_phrases_that_mean_representation(self):
        text = "JOÃO, menor impúbere, neste ato representado por sua genitora MARIA, CPF 999.888.777-14"
        self.assertEqual(representation_signals(text), ["representado por", "neste ato representado", "menor impúbere"])
        self.assertEqual(representation_signals("MARIA DA SILVA, brasileira, solteira, agricultora"), [])
        defendant = "em face do INSS, por intermédio do representante legal deste, na cidade de Itaituba-PA"
        self.assertEqual(representation_signals(defendant), [], "the defendant's representative is not the author's")

    def test_a_minor_needs_a_representative_whatever_the_petition_says(self):
        problems = representation_problems(with_author(birthDate="2015-02-01"), [], [], TODAY)
        self.assertEqual(problems, ["The author is a minor (11 years old) but no legal representative was given"])
        self.assertEqual(representation_problems(with_author(birthDate="17/10/2008"), [], [], TODAY), [])
        silenced = with_author(birthDate="01/02/2015", selfRepresented=True)
        self.assertEqual(len(representation_problems(silenced, [], [], TODAY)), 1, "selfRepresented never covers age")

    def test_a_petition_that_says_represented_needs_one_unless_the_reviewer_confirms_otherwise(self):
        signals = ["representado por"]
        self.assertIn("petition indicates", representation_problems(FACTS, [], signals, TODAY)[0])
        self.assertEqual(representation_problems(with_author(selfRepresented=True), [], signals, TODAY), [])
        self.assertEqual(representation_problems(with_author(representative=MOTHER), [], signals, TODAY), [])


class RepresentationInPrepareTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name, "input")
        self.folder.mkdir()
        (self.folder / "10-PETIÇÃO INICIAL.pdf").write_bytes(b"%PDF-1.4\npetition")
        self.store = LocalRunStore(Path(tmp.name, "run1")).ensure()

    def petition(self, cpfs, signals):
        return mock.patch("jurisync.workflows.filing.prepare._petition_candidates", lambda path: ([], cpfs, signals))

    def test_a_minor_without_representative_is_held_for_review_and_finalize_refuses(self):
        facts = with_author(birthDate="01/02/2015")
        with self.petition(["12345678909", "99988877714"], []):
            result = prepare_folder(self.folder, facts, self.store, profile=PROFILE, jev=False)
            self.assertFalse(result["payloadWritten"])
            self.assertEqual(
                [item["name"] for item in self.store.read_json("review.json")["review"]], ["legal representative"]
            )
            with self.assertRaisesRegex(ValueError, "is a minor"):
                finalize_review(self.store, facts, profile=PROFILE)
            finalize_review(self.store, with_author(birthDate="01/02/2015", representative=MOTHER), profile=PROFILE)
        self.assertEqual(self.store.read_json("payload.json")["author"]["representative"]["cpf"], "99988877714")

    def test_a_representative_whose_cpf_the_petition_does_not_state_is_held_for_review(self):
        facts = with_author(representative=MOTHER)
        with self.petition(["12345678909", "11122233396"], ["representado por"]):
            prepare_folder(self.folder, facts, self.store, profile=PROFILE, jev=False)
            reasons = [item["reason"] for item in self.store.read_json("review.json")["review"]]
            self.assertEqual(
                reasons, ["The legal representative's CPF was not found among the petition's formatted CPFs"]
            )
            self.assertEqual(self.store.read_json("review.json")["candidates"]["representation"], ["representado por"])
            with self.assertRaisesRegex(ValueError, "representative's CPF"):
                finalize_review(self.store, facts, profile=PROFILE)


if __name__ == "__main__":
    unittest.main()
