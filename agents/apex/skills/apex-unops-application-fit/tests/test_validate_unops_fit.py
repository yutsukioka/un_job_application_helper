"""Synthetic unit tests; no candidate data or live portal access."""
import copy
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_unops_fit import ValidationError, load_catalog, validate_selection


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "catalog.csv"
        with self.path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows([
                ["Instruction row", "", ""],
                ["No", "Skills List", "Description"],
                ["3", "Program Management", "Manage related projects."],
                ["51", "Monitoring, Evaluation, and Learning", "Monitor, evaluate, and learn."],
            ])
        self.catalog = load_catalog(self.path)
        self.sample = {
            "schema_version": 1, "mode": "reference_portfolio", "vacancy_id": None,
            "catalog_sha256": self.catalog.sha256,
            "requested_selection_budget": 20,
            "platform_skill_limit": None, "platform_skill_limit_status": "UNVERIFIED",
            "platform_skill_limit_scope": "UNKNOWN", "selection_scope": "PROFILE",
            "skills": [{
                "name": "Program Management", "catalog_no": "3",
                "evidence": [{"role_id": "R-1", "status": "SUPPORTED", "source": "synthetic.txt",
                              "locator": "L1-L2", "claim": "Oversaw related projects."}],
                "requirement_ids": [], "selection_reason": "Reference portfolio."
            }]
        }

    def result(self):
        return validate_selection(self.sample, self.catalog)

    def test_reference_valid(self):
        self.assertTrue(self.result()["valid"])

    def test_nonconsecutive_numbers(self):
        self.assertEqual(len(self.catalog.records), 2)
        self.assertEqual(self.catalog.records["Monitoring, Evaluation, and Learning"]["catalog_no"], "51")

    def test_comma_label_is_single_skill(self):
        self.sample["skills"][0].update(name="Monitoring, Evaluation, and Learning", catalog_no="51")
        self.assertTrue(self.result()["valid"])
        self.assertEqual(self.result()["selected_count"], 1)

    def test_wrong_spelling(self):
        self.sample["skills"][0]["name"] = "Programme Management"
        self.assertFalse(self.result()["valid"])

    def test_exact_not_casefolded(self):
        self.sample["skills"][0]["name"] = "program management"
        self.assertFalse(self.result()["valid"])

    def test_duplicate_selection(self):
        self.sample["skills"].append(copy.deepcopy(self.sample["skills"][0]))
        self.assertFalse(self.result()["valid"])

    def test_no_is_not_row_number(self):
        self.sample["skills"][0]["catalog_no"] = "1"
        self.assertFalse(self.result()["valid"])

    def test_hash_mismatch(self):
        self.sample["catalog_sha256"] = "0" * 64
        self.assertFalse(self.result()["valid"])

    def test_unknown_cap_cannot_be_twenty(self):
        self.sample["platform_skill_limit"] = 20
        self.assertFalse(self.result()["valid"])

    def test_budget_not_minimum(self):
        self.sample["skills"] = []
        self.assertTrue(self.result()["valid"])

    def test_exceed_requested_budget(self):
        self.sample["requested_selection_budget"] = 0
        self.assertFalse(self.result()["valid"])

    def test_bool_not_budget(self):
        self.sample["requested_selection_budget"] = True
        self.assertFalse(self.result()["valid"])

    def verify_limit(self, value=0, scope="PROFILE"):
        self.sample.update(platform_skill_limit=value, platform_skill_limit_status="VERIFIED",
                           platform_skill_limit_scope=scope,
                           platform_skill_limit_evidence={"source": "synthetic form", "locator": "Skills help",
                                                          "observed_at": "2026-09-27"})

    def test_verified_zero_cap(self):
        self.verify_limit()
        self.assertFalse(self.result()["valid"])

    def test_verified_limit_requires_evidence(self):
        self.verify_limit(20)
        del self.sample["platform_skill_limit_evidence"]
        self.assertFalse(self.result()["valid"])

    def test_other_scope_limit_not_misapplied(self):
        self.verify_limit(0, "PER_ROLE")
        self.assertTrue(self.result()["valid"])
        self.assertTrue(any("another scope" in w for w in self.result()["warnings"]))

    def test_role_selection_requires_role(self):
        self.sample["selection_scope"] = "PER_ROLE"
        self.assertFalse(self.result()["valid"])
        self.sample["selection_role_id"] = "R-1"
        self.assertTrue(self.result()["valid"])

    def test_other_role_evidence_rejected(self):
        self.sample.update(selection_scope="PER_ROLE", selection_role_id="R-2")
        self.assertFalse(self.result()["valid"])

    def test_missing_evidence(self):
        self.sample["skills"][0]["evidence"] = []
        self.assertFalse(self.result()["valid"])

    def test_unresolved_evidence(self):
        self.sample["skills"][0]["evidence"][0]["status"] = "AMBIGUOUS"
        self.assertFalse(self.result()["valid"])

    def test_missing_locator(self):
        del self.sample["skills"][0]["evidence"][0]["locator"]
        self.assertFalse(self.result()["valid"])

    def test_vacancy_requires_id_and_requirements(self):
        self.sample["mode"] = "vacancy"
        self.assertFalse(self.result()["valid"])
        self.sample["vacancy_id"] = "SYNTHETIC-VACANCY"
        self.assertFalse(self.result()["valid"])
        self.sample["skills"][0]["requirement_ids"] = ["REQ-1"]
        self.assertTrue(self.result()["valid"])

    def test_reference_cannot_claim_vacancy_id(self):
        self.sample["vacancy_id"] = "SYNTHETIC-VACANCY"
        self.assertFalse(self.result()["valid"])

    def test_bad_type_returns_errors(self):
        self.assertFalse(validate_selection([], self.catalog)["valid"])
        self.sample["skills"] = "Program Management"
        self.assertFalse(self.result()["valid"])

    def test_missing_header(self):
        self.path.write_text("Not the correct header\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            load_catalog(self.path)

    def test_duplicate_catalog_name(self):
        with self.path.open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(["100", "Program Management", "Duplicate name."])
        with self.assertRaises(ValidationError):
            load_catalog(self.path)


if __name__ == "__main__":
    unittest.main()
