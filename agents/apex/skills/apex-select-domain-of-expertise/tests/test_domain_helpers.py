import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


query = module("query_domain_reference")
experience = module("calculate_experience")


def small_reference():
    return {"schema_version": 1, "organization": "Test portal", "snapshot_date": "2026-09-13",
            "coverage_complete": False, "counts": {"areas": 1, "subareas": 1},
            "areas": [{"id": "A", "label": "Exact area", "disabled": False, "children_complete": False,
                       "subareas": [{"id": "C", "label": "Exact child", "parent_id": "A", "disabled": False}]}],
            "years_of_experience": {"complete": False, "options": [{"id": "Y", "label": "Observed band", "disabled": False}]}}


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.ref = query.load_reference()

    def test_packaged_catalog_matches_pinned_source_record_digest_and_years(self):
        manifest = json.loads((ROOT / "references" / "unesco-source-manifest-2026-09-13.json").read_text())
        self.assertEqual(query.canonical_digest(self.ref), manifest["canonical_records_sha256"])
        self.assertEqual(len(self.ref["areas"]), 19)
        self.assertEqual(sum(len(a["subareas"]) for a in self.ref["areas"]), 420)
        self.assertEqual({(c["id"], c["label"]) for c in self.ref["years_of_experience"]["options"]},
                         {("40051", "1-3"), ("40052", "4-7"), ("40053", "8-10"), ("40050", "10 +")})
        self.assertEqual(len(manifest["taxonomy_responses"]), 61)

    def test_valid_accounting_row_retains_exact_choices(self):
        result = query.query(self.ref, "38743", "41155", "40052")
        self.assertEqual((result["area"]["label"], result["subarea"]["label"], result["experience"]["label"]),
                         ("Finances", "Accounting", "4-7"))
        self.assertTrue(result["row_choice_valid"])
        self.assertFalse(result["evidence_and_duration_assessed"])

    def test_wrong_parent_and_proficiency_instead_of_experience_are_rejected(self):
        with self.assertRaises(ValueError):
            query.query(self.ref, "38747", "41155", "40052")
        with self.assertRaises(ValueError):
            query.query(self.ref, "38743", "41155", "High")

    def test_partial_catalog_does_not_block_an_observed_enabled_pair(self):
        reference = query.validate_reference(small_reference())
        result = query.query(reference, "A", "C", "Y")
        self.assertTrue(result["row_choice_valid"])
        self.assertFalse(result["catalog_complete"])
        self.assertFalse(result["area"]["children_complete"])

    def test_disabled_choices_and_parent_corruption_are_rejected(self):
        reference = small_reference()
        reference["areas"][0]["subareas"][0]["disabled"] = True
        with self.assertRaises(ValueError):
            query.query(query.validate_reference(reference), "A", "C")
        reference = small_reference()
        reference["areas"][0]["subareas"][0]["parent_id"] = "WRONG"
        with self.assertRaises(ValueError):
            query.validate_reference(reference)

    def test_reference_label_tampering_breaks_source_digest(self):
        changed = copy.deepcopy(self.ref)
        changed["areas"][0]["label"] = "Invented replacement"
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            query.validate_reference(changed)

    def test_row_validation_flags_label_changes_and_duplicate_pairs(self):
        good = {"area_id": "38743", "subarea_id": "41155", "experience_id": "40052",
                "area_label": "Finances", "subarea_label": "Accounting", "experience_label": "4-7"}
        self.assertTrue(query.validate_rows(self.ref, [good])["valid"])
        wrong = {**good, "subarea_label": "Accountancy"}
        self.assertFalse(query.validate_rows(self.ref, [wrong])["valid"])
        result = query.validate_rows(self.ref, [good, good])
        self.assertTrue(result["rows"][0]["valid"])
        self.assertFalse(result["rows"][1]["valid"])

    def test_default_reference_works_outside_skill_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "query_domain_reference.py"),
                                     "--area", "38743", "--subarea-id", "41155", "--experience-id", "40052"],
                                    cwd=temp, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["row_choice_valid"])


class ExperienceTests(unittest.TestCase):
    def test_overlap_is_counted_once_and_disjoint_work_is_retained(self):
        result = experience.calculate({"as_of_date": "2020-12-31", "intervals": [
            {"start": "2020-01-01", "end": "2020-01-10", "source": "Role A"},
            {"start": "2020-01-05", "end": "2020-01-20", "source": "Project B"},
            {"start": "2020-02-01", "end": "2020-02-05", "source": "Project C"}]})
        self.assertEqual(result["known_covered_days_min"], 25)
        self.assertEqual(result["known_covered_days_max"], 25)
        self.assertEqual(len(result["maximum_coverage_periods"]), 2)
        self.assertIsNone(result["portal_experience_band"])

    def test_month_precision_yields_bounds_without_claiming_exact_dates(self):
        result = experience.calculate({"as_of_date": "2020-12-31", "intervals": [
            {"start": "2020-01", "end": "2020-03", "source": "Month-only source"}]})
        self.assertEqual((result["known_covered_days_min"], result["known_covered_days_max"]), (31, 91))
        self.assertTrue(result["date_precision_uncertainty"])
        self.assertEqual(result["source_ledger"][0]["start_as_supplied"], "2020-01")

    def test_current_role_needs_current_status_source_and_uses_explicit_cutoff(self):
        interval = {"start": "2020-01-01", "end": None, "source": "Old CV says present"}
        held = experience.calculate({"as_of_date": "2020-01-20", "intervals": [interval]})
        self.assertFalse(held["all_intervals_resolved"])
        self.assertEqual(held["known_covered_days_max"], 0)
        confirmed = {**interval, "current_confirmed": True, "current_status_source": "Current applicant statement"}
        ready = experience.calculate({"as_of_date": "2020-01-20", "intervals": [confirmed]})
        self.assertTrue(ready["all_intervals_resolved"])
        self.assertEqual(ready["known_covered_days_min"], 20)

    def test_invalid_interval_does_not_erase_independent_supported_period(self):
        result = experience.calculate({"as_of_date": "2020-01-20", "intervals": [
            {"start": "2020-01-01", "end": "2020-01-05", "source": "Supported project"},
            {"start": "2020-02-01", "end": "2020-03-01", "source": "Future project"}]})
        self.assertFalse(result["all_intervals_resolved"])
        self.assertEqual(result["known_covered_days_min"], 5)
        self.assertEqual(result["issues"][0]["interval_index"], 1)
        self.assertIsNone(result["portal_experience_band"])

    def test_exact_ten_year_interval_never_auto_selects_an_ambiguous_band(self):
        result = experience.calculate({"as_of_date": "2025-12-31", "intervals": [
            {"start": "2016-01-01", "end": "2025-12-31", "source": "Exact dated role"}]})
        self.assertTrue(result["all_intervals_resolved"])
        self.assertEqual(result["known_covered_days_min"], 3653)
        self.assertIsNone(result["portal_experience_band"])

    def test_missing_source_or_invalid_dates_are_held(self):
        result = experience.calculate({"as_of_date": "2026-09-13", "intervals": [
            {"start": "2020-01-01", "end": "2021-01-01"},
            {"start": "2020-02-30", "end": "2021-01-01", "source": "Invalid source date"}]})
        self.assertFalse(result["all_intervals_resolved"])
        self.assertEqual(result["known_covered_days_max"], 0)
        self.assertEqual(len(result["issues"]), 2)


if __name__ == "__main__":
    unittest.main()
