from pathlib import Path
from urllib.parse import urlsplit

import yaml


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
