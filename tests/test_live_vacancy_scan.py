"""Behavioral regression tests for the full-scan review helper (stdlib only)."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "agents/apex/skills/apex-match-live-vacancies/scripts/scan_live_jobs.py"
SPEC = importlib.util.spec_from_file_location("live_vacancy_scan", SCRIPT)
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)
NOW = datetime(2026, 9, 28, 8, tzinfo=timezone.utc)


class LiveVacancyScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile_path = self.root / "profile.md"
        self.profile_path.write_text(
            "# Profile\n## USER_JOB_HISTORY_TEXT\n"
            "Languages: English (full professional); French (basic); Japanese (native).\n"
            "I led project management and grant oversight, accounting and financial reporting.\n"
            "## JOB_DESCRIPTION_TEXT\nThe target needs advanced neurosurgery, procurement and German.\n",
            encoding="utf-8",
        )
        self.profile = scanner.load_profile(self.profile_path)

    def job(self, **changes):
        row = {
            "job_key": "source:1", "external_id": "1", "title": "Finance Specialist",
            "status": "open", "location": "New York", "grade_code": "IICA2",
            "national_international": "international", "closes_at_local": "05-Oct-2026",
            "description": "Position Title Finance Specialist Contract Type ICA - IICA - Regular Contract Level IICA 2 Posting Start Date 01-Sep-2026. "
                           "Education Requirements Master's degree with five years experience. Experience Requirements Required financial reporting and budgeting. "
                           "Language Requirements Language Proficiency Level Requirement English Fluent Required French Fluent Desirable. "
                           "Additional Information UNOPS embraces diversity.",
            "source_latest_observed_at": NOW.isoformat(), "source_listed_current": 1,
            "trusted_current": 1,
        }
        row.update(changes)
        return row

    def database(self, rows, state="complete", complete=True):
        path = self.root / "jobs.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE jobs (job_key TEXT PRIMARY KEY, external_id TEXT, title TEXT, status TEXT, description TEXT, closes_at_local TEXT, posted_at TEXT, source_latest_observed_at TEXT, source_listed_current INTEGER, trusted_current INTEGER)")
            for row in rows:
                fields = ["job_key", "external_id", "title", "status", "description", "closes_at_local", "posted_at", "source_latest_observed_at", "source_listed_current", "trusted_current"]
                db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)", [row.get(k) for k in fields])
        db.close()
        (self.root / ".jobagg-publication-state.json").write_text(json.dumps({
            "state": state, "database_transactions_complete": complete,
            "generation_id": "test-generation", "plan_sha256": "test-plan",
        }), encoding="utf-8")
        return path

    def test_october_text_deadline_survives_september_scan(self):
        result = scanner.deadline(self.job(), NOW)
        self.assertEqual(result["state"], "future_date")
        self.assertEqual(result["published_date"], "2026-10-05")
        self.assertIn("date_only_cutoff_unverified", result["issues"])

    def test_date_only_same_day_and_conflicting_cutoffs_require_review(self):
        same_day = scanner.deadline(self.job(closes_at_local="28-Sep-2026"), NOW)
        self.assertEqual(same_day["state"], "closing_date_review")
        conflict = scanner.deadline(self.job(closes_at="2026-09-27T01:00:00Z", closes_at_local="05-Oct-2026"), NOW)
        self.assertEqual(conflict["state"], "deadline_review")
        self.assertIn("instant_vs_published_date_conflict", conflict["issues"])
        exact_past = scanner.deadline(self.job(closes_at="2026-09-27T01:00:00Z", closes_at_local=None), NOW)
        self.assertEqual(exact_past["state"], "expired")

    def test_timezone_conversion_reconciles_local_date(self):
        result = scanner.deadline(self.job(closes_at="2026-10-04T22:30:00Z", closes_at_local="05-Oct-2026", closes_tz="Africa/Nairobi"), NOW)
        self.assertEqual(result["state"], "future_instant")
        unresolved = scanner.deadline(self.job(closes_at="2026-10-05T23:59:00", closes_at_local=None, closes_tz="unknown-zone"), NOW)
        self.assertEqual(unresolved["state"], "deadline_review")
        self.assertIn("unrecognized_timezone", unresolved["issues"])

    def test_ambiguous_numeric_locale_is_reviewed_never_expired(self):
        result = scanner.deadline(self.job(closes_at_local="10/01/2026"), NOW)
        self.assertEqual(result["state"], "deadline_review")
        summary, _ = scanner.triage(self.job(closes_at_local="10/01/2026"), self.profile, NOW)
        self.assertEqual(summary["triage"]["disposition"], "eligibility_review")
        self.assertTrue(summary["triage"]["human_assessment_required"])
        unambiguous = scanner.deadline(self.job(closes_at_local="31/10/2026"), NOW)
        self.assertEqual(unambiguous["published_date"], "2026-10-31")

    def test_employer_comma_12_hour_deadline_agrees_with_normalized_instant(self):
        result = scanner.deadline(self.job(closes_at="2026-09-18T15:59:00Z", closes_at_local="18-Sep-2026, 3:59:00 PM", closes_tz="Etc/UTC"), NOW)
        self.assertEqual(result["state"], "expired")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["utc_instant"], "2026-09-18T15:59:00+00:00")

    def test_local_contract_overrides_bad_classifier_without_nationality_veto(self):
        row = self.job(title="Senior Programme Manager", grade_code="ICS11", location="Home based",
                       description="Position Title Senior Programme Manager Contract Type ICA - LICA - Specialist - Regular Contract Level LICA 11 Posting Start Date 01-Sep-2026. "
                                   "Experience Requirements Required project management. Language Requirements English Fluent Required Burmese Fluent Required.")
        result = scanner.eligibility(row, self.profile)
        self.assertEqual(result["contract"]["category"], "local")
        self.assertTrue(result["contract"]["classification_conflict"])
        self.assertTrue(result["remote"])
        self.assertIn("local_contract_eligibility_unresolved", result["flags"])
        self.assertEqual(result["local_restriction_excerpts"], [])
        summary, _ = scanner.triage(row, self.profile, NOW)
        self.assertEqual(summary["triage"]["disposition"], "eligibility_review")
        self.assertIsNone(summary["triage"]["final_fit"])

    def test_required_and_desirable_language_rows_are_separate(self):
        languages = scanner.language_requirements("Language Requirements English Fluent Required French Fluent Desirable Burmese Fluent Required", self.profile["settings"]["languages"])
        by_language = {r["language"]: r for r in languages}
        self.assertEqual(by_language["english"]["evidence_status"], "supported")
        self.assertEqual(by_language["french"]["requirement"], "desirable")
        self.assertEqual(by_language["burmese"]["requirement"], "required")
        self.assertEqual(by_language["burmese"]["evidence_status"], "not_established")
        flags = scanner.eligibility(self.job(), self.profile)["flags"]
        self.assertNotIn("required_language_evidence_gap", flags)

    def test_alternative_language_clause_is_not_a_false_veto(self):
        results = scanner.language_requirements("Fluency in English or French is required.", self.profile["settings"]["languages"])
        self.assertTrue(all(r["alternative"] for r in results))
        self.assertEqual({r["language"] for r in results}, {"english", "french"})
        row = self.job(description="Experience Requirements Project management. Fluency in English or French is required.")
        self.assertNotIn("required_language_evidence_gap", scanner.eligibility(row, self.profile)["flags"])

    def test_target_jd_and_unresolved_feedback_never_create_applicant_evidence(self):
        feedback = self.root / "feedback.md"
        feedback.write_text("## APPROVED_UPDATES\nLed evaluations.\n## UPDATES_REQUIRING_CONFIRMATION\nFluent German; expert procurement.\n", encoding="utf-8")
        profile = scanner.load_profile(self.profile_path, feedback)
        self.assertIn("finance_accounting", profile["families"])
        self.assertIn("monitoring_evaluation", profile["families"])
        self.assertNotIn("procurement_supply", profile["families"])
        self.assertNotIn("german", profile["settings"]["languages"])
        old_hash = profile["manifest"]["used_evidence_sha256"]
        self.profile_path.write_text(self.profile_path.read_text() + "\nThe vacancy additionally needs advanced cybersecurity and Portuguese.\n", encoding="utf-8")
        updated = scanner.load_profile(self.profile_path, feedback)
        self.assertEqual(old_hash, updated["manifest"]["used_evidence_sha256"])

    def test_controlled_assertion_subsection_is_withheld(self):
        self.profile_path.write_text("## USER_JOB_HISTORY_TEXT\nAccounting experience.\n#### Additional user-submitted narratives (controlled integration)\nI am a procurement expert and speak German (fluent).\n## JOB_DESCRIPTION_TEXT\nData analysis.\n", encoding="utf-8")
        profile = scanner.load_profile(self.profile_path)
        self.assertNotIn("procurement_supply", profile["families"])
        self.assertNotIn("german", profile["settings"]["languages"])
        self.assertGreater(profile["manifest"]["withheld_lines"], 0)

    def test_spouse_and_household_facts_are_not_applicant_languages(self):
        self.profile_path.write_text(
            "## USER_JOB_HISTORY_TEXT\nLanguages: English (full professional).\nAccounting experience.\n"
            "Spouse: German (native), expert procurement manager.\n"
            "### Household context\nLanguages: Russian (native).\nHusband is a logistics officer.\n"
            "## JOB_DESCRIPTION_TEXT\nGerman Fluent Required\n", encoding="utf-8")
        profile = scanner.load_profile(self.profile_path)
        self.assertEqual(profile["settings"]["languages"], {"english": "full professional"})
        self.assertNotIn("procurement_supply", profile["families"])
        languages = scanner.language_requirements("German Fluent Required", profile["settings"]["languages"])
        self.assertEqual(languages[0]["evidence_status"], "not_established")

    def test_body_only_match_is_reviewed_but_boilerplate_does_not_match(self):
        row = self.job(title="Portfolio Advisor", description="Experience Requirements Required accounting and financial reporting. Language Requirements English Fluent Required.")
        summary, _ = scanner.triage(row, self.profile, NOW)
        finance = next(m for m in summary["family_matches"] if m["family"] == "finance_accounting")
        self.assertFalse(finance["title_signal"])
        self.assertTrue(finance["body_signal"])
        self.assertIn(summary["triage"]["disposition"], {"candidate_review", "eligibility_review"})
        unrelated = self.job(title="Surgeon", description="Required Qualifications Medical degree and surgical registration. Additional Information Everyone works in teams on financial sustainability, grants and project management.")
        self.assertEqual(scanner.family_matches(unrelated, self.profile), [])

    def test_full_rescan_old_rows_and_forced_closed_record_no_database_write(self):
        old = self.job(posted_at=None)
        closed = self.job(job_key="source:2", external_id="2", status="closed")
        irrelevant = self.job(job_key="source:3", external_id="3", title="Surgeon", description="Medical degree and surgical registration. " * 15)
        db = self.database([old, closed, irrelevant], state="exporting")
        before = scanner.digest(db.read_bytes())
        result = scanner.scan(db, self.profile_path, self.root / "out", as_of=NOW, forced_ids=["2", "absent-id"])
        self.assertEqual(scanner.digest(db.read_bytes()), before)
        self.assertEqual(result["open_rows_read"], 2)
        self.assertEqual(result["forced_non_open_rows_read"], 1)
        self.assertEqual(result["missing_force_ids"], ["absent-id"])
        self.assertFalse(result["exports_ready"])
        inventory = json.loads((self.root / "out/inventory.json").read_text())["records"]
        self.assertEqual({r["job_key"] for r in inventory}, {"source:1", "source:2", "source:3"})
        self.assertNotIn("description", inventory[0])
        self.assertEqual(next(r for r in inventory if r["job_key"] == "source:3")["triage"]["disposition"], "not_prioritized")
        candidates = json.loads((self.root / "out/candidates.json").read_text())["records"]
        self.assertEqual({r["job_key"] for r in candidates}, {"source:1", "source:2"})
        self.assertTrue(all("description" in r and "raw_json" not in r for r in candidates))

    def test_unfinished_database_transaction_and_generation_change_refuse_scan(self):
        db = self.database([self.job()], complete=False)
        with self.assertRaisesRegex(ValueError, "database_transactions_complete"):
            scanner.scan(db, self.profile_path, self.root / "out", as_of=NOW)
        self.assertFalse((self.root / "out").exists())
        first = {"generation_id": "one", "database_transactions_complete": True}
        second = {"generation_id": "two", "database_transactions_complete": True}
        with patch.object(scanner, "read_gate", side_effect=[first, second]):
            with self.assertRaisesRegex(ValueError, "generation changed"):
                scanner.snapshot(db)


if __name__ == "__main__":
    unittest.main()
