from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CRITERIA = [
    "A1",
    "A2",
    "A3",
    "A4",
    "B1",
    "B2",
    "B3",
    "B4",
    "B5",
    "C1",
    "C2",
    "C3",
    "C4",
    "D1",
    "D2",
    "D3",
    "D4",
]


def test_E4_readme_security_section_links_to_hardening_mitigations() -> None:
    readme = (ROOT / "README.md").read_text()
    section = readme.split("## Security", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]

    for criterion in CRITERIA:
        assert f"[{criterion}]" in section

    required_links = [
        "docs/security/deployment.md",
        "docs/security/hardening_audit.md",
        "contracts/api/encrypted_sync.md",
        "services/job-api/CHANGELOG.md",
        ".github/workflows/ci.yml",
        "packages/jobagg/config/robots_policy.yaml",
    ]
    for link in required_links:
        assert link in section
