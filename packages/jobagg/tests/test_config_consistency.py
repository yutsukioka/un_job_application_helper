import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from jobagg.pipelines.sync_source import fetch_schedule_policy, load_sources


ROOT = Path(__file__).resolve().parents[1]


def test_robots_policy_hosts_match_organization_urls():
    organizations = yaml.safe_load((ROOT / "config/organizations.yaml").read_text())
    policy = yaml.safe_load((ROOT / "config/robots_policy.yaml").read_text())

    organization_hosts = {
        host
        for source in organizations["sources"]
        for host in _url_hosts(source)
    }
    policy_hosts = set((policy.get("domains") or {}).keys())

    assert sorted(organization_hosts - policy_hosts) == []
    assert sorted(policy_hosts - organization_hosts) == []


def test_runtime_rule_files_are_packaged():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    package_data = pyproject["tool"]["setuptools"]["package-data"]["jobagg"]
    assert "classification/rules/*.json" in package_data
    assert "classification/rules/taxonomies/*.yaml" in package_data

    config_taxonomies = {path.name for path in (ROOT / "config/taxonomies").glob("*.yaml")}
    packaged_taxonomies = {
        path.name for path in (ROOT / "jobagg/classification/rules/taxonomies").glob("*.yaml")
    }
    assert packaged_taxonomies == config_taxonomies


def test_detail_fetch_schedule_defaults_for_degraded_sources():
    sources = {source.id: source for source in load_sources(ROOT / "config/organizations.yaml")}

    undp_policy = fetch_schedule_policy(sources["undp_oracle_hcm"])
    assert undp_policy["list_fetch_interval_minutes"] == 180
    assert undp_policy["oracle_detail_concurrency"] == 1
    assert undp_policy["oracle_detail_min_delay_seconds"] == 8
    assert undp_policy["oracle_detail_batch_size"] == 10
    assert undp_policy["oracle_detail_batch_pause_seconds"] == 120
    assert undp_policy["oracle_detail_stop_after_transient_failures"] == 3
    assert undp_policy["max_detail_pages_per_run"] == 20

    unicef_policy = fetch_schedule_policy(sources["unicef_pageup"])
    assert unicef_policy["detail_concurrency"] == 1
    assert unicef_policy["detail_none_is_transient"] is True
    assert unicef_policy["detail_min_delay_seconds"] == 30
    assert unicef_policy["detail_jitter_seconds"] == 15
    assert unicef_policy["detail_batch_size"] == 3
    assert unicef_policy["detail_batch_pause_seconds"] == 300
    assert unicef_policy["max_detail_pages_per_run"] == 10
    assert unicef_policy["stop_after_transient_failures"] == 2
    assert unicef_policy["host_cooldown_seconds"] == 1800
    assert unicef_policy["detail_permanent_failure_quarantine_days"] == 7

    for source_id in {
        "ctbto_successfactors_legacy",
        "icc_successfactors_legacy",
    }:
        policy = fetch_schedule_policy(sources[source_id])
        assert policy["detail_concurrency"] == 1
        assert policy["detail_min_delay_seconds"] == 30
        assert policy["detail_jitter_seconds"] == 10
        assert policy["detail_batch_size"] == 5
        assert policy["detail_batch_pause_seconds"] == 300
        assert policy["max_detail_pages_per_run"] == 10
        assert policy["stop_after_transient_failures"] == 2
        assert policy["host_cooldown_seconds"] == 1800


def _url_hosts(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _url_hosts(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _url_hosts(item)
        return
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        host = urlsplit(value).netloc.lower()
        if host:
            yield host
