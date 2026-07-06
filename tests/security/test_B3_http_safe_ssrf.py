from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))

from jobagg.http import ResponseTooLargeError, _decode_content_encoding  # noqa: E402
from jobagg.http_safe import SafeHTTPPolicy, SSRFProtectionError, safe_urljoin  # noqa: E402


def test_B3_http_safe_allows_configured_public_host() -> None:
    policy = SafeHTTPPolicy(allowed_hosts={"jobs.example.org"}, resolver=lambda host: ["203.0.113.10"])

    assert policy.validate_url("https://jobs.example.org/list") == "jobs.example.org"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://jobs.example.org/list",
        "file:///etc/passwd",
        "http://127.0.0.1/admin",
        "http://10.1.2.3/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/admin",
        "http://[fd00::1]/admin",
    ],
)
def test_B3_http_safe_rejects_schemes_and_private_targets(url: str) -> None:
    policy = SafeHTTPPolicy(allowed_hosts={"jobs.example.org"})

    with pytest.raises(SSRFProtectionError):
        policy.validate_url(url)


def test_B3_http_safe_rejects_host_not_in_organization_allowlist() -> None:
    policy = SafeHTTPPolicy(allowed_hosts={"jobs.example.org"}, resolver=lambda host: ["203.0.113.10"])

    with pytest.raises(SSRFProtectionError):
        policy.validate_url("https://evil.example.net/list")


def test_B3_http_safe_rejects_dns_rebind_to_private_address() -> None:
    policy = SafeHTTPPolicy(allowed_hosts={"jobs.example.org"}, resolver=lambda host: ["10.0.0.5"])

    with pytest.raises(SSRFProtectionError):
        policy.validate_url("https://jobs.example.org/list")


def test_B3_safe_urljoin_validates_joined_target() -> None:
    policy = SafeHTTPPolicy(allowed_hosts={"jobs.example.org"}, resolver=lambda host: ["203.0.113.10"])

    with pytest.raises(SSRFProtectionError):
        safe_urljoin("https://jobs.example.org/list", "//169.254.169.254/latest", policy=policy)


def test_B3_http_safe_rejects_redirect_to_private_host() -> None:
    policy = SafeHTTPPolicy(allowed_hosts={"jobs.example.org"}, resolver=lambda host: ["203.0.113.10"])

    with pytest.raises(SSRFProtectionError):
        policy.validate_redirect(
            "https://jobs.example.org/list",
            "http://169.254.169.254/latest",
            redirect_count=1,
        )


def test_B3_http_safe_rejects_too_many_redirects() -> None:
    policy = SafeHTTPPolicy(allowed_hosts={"jobs.example.org"}, resolver=lambda host: ["203.0.113.10"])

    with pytest.raises(SSRFProtectionError):
        policy.validate_redirect(
            "https://jobs.example.org/1",
            "https://jobs.example.org/2",
            redirect_count=6,
        )


def test_B3_decode_content_encoding_enforces_decompressed_cap() -> None:
    compressed = gzip.compress(b"x" * 1024)

    with pytest.raises(ResponseTooLargeError):
        _decode_content_encoding(compressed, "gzip", max_bytes=128)
