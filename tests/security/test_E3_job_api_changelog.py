from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_E3_job_api_changelog_records_security_hardening() -> None:
    changelog = ROOT / "services/job-api/CHANGELOG.md"

    assert changelog.is_file()
    text = changelog.read_text()

    required_terms = [
        "LAN exposure",
        "JOB_API_ALLOW_LAN",
        "X-Job-Api-Token",
        "score_against",
        "apply_url_trust",
        "source_url_trust",
        "Saved-search",
        "Path disclosure",
    ]
    for term in required_terms:
        assert term in text
