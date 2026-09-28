from __future__ import annotations

from pathlib import Path

import yaml

from jobagg.robots import explicit_robots_stance_gaps


ROOT = Path(__file__).resolve().parents[2]


def test_D4_every_organization_source_host_has_explicit_robots_stance() -> None:
    organizations = yaml.safe_load(
        (ROOT / "packages/jobagg/config/organizations.yaml").read_text()
    )
    policy = yaml.safe_load((ROOT / "packages/jobagg/config/robots_policy.yaml").read_text())

    gaps = explicit_robots_stance_gaps(organizations, policy)

    assert gaps == []


def test_D4_reports_missing_explicit_robots_stance_for_source_host() -> None:
    organizations = {
        "sources": [
            {
                "id": "example_source",
                "base_url": "https://jobs.example.org/list",
            }
        ]
    }
    policy = {"domains": {"jobs.example.org": {"min_delay_seconds": 2.0}}}

    assert explicit_robots_stance_gaps(organizations, policy) == [
        "example_source: host 'jobs.example.org' lacks explicit honor_robots_txt"
    ]
