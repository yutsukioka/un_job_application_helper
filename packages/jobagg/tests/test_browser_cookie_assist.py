from argparse import Namespace

import pytest

from jobagg.models import OrganizationSource
from jobagg.scheduler import _apply_browser_cookie_assist, _normalize_cookie_header


def test_normalize_cookie_header_accepts_raw_header_and_curl_header():
    assert _normalize_cookie_header("Cookie: aws-waf-token=abc; session=def") == (
        "aws-waf-token=abc; session=def"
    )
    assert _normalize_cookie_header("curl -H 'Cookie: aws-waf-token=abc; session=def' https://example.org") == (
        "aws-waf-token=abc; session=def"
    )


def test_normalize_cookie_header_rejects_multiline_input():
    with pytest.raises(RuntimeError, match="single header line"):
        _normalize_cookie_header(
            "Cookie: aws-waf-token=abc; session=def\n"
            "User-Agent: copied from devtools"
        )


def test_browser_cookie_assist_injects_cookie_header_from_file(tmp_path, monkeypatch):
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("Cookie: aws-waf-token=abc; session=def\n", encoding="utf-8")
    opened = []
    monkeypatch.setattr("jobagg.scheduler.webbrowser.open", opened.append)
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing",
        extra={
            "browser_cookie_assist": True,
            "browser_cookie_url": "https://jobs.unicef.org/en-us/listing/",
        },
    )
    args = Namespace(
        browser_cookie_assist=True,
        browser_cookie_source_id=[],
        browser_cookie_file=str(cookie_file),
        browser_cookie_env="JOBAGG_TEST_COOKIE_HEADER",
        no_browser_open=False,
    )

    _apply_browser_cookie_assist(args, [source])

    assert opened == ["https://jobs.unicef.org/en-us/listing/"]
    assert source.extra["cookie_header"] == "aws-waf-token=abc; session=def"
    assert source.extra["browser_cookie_assist_active"] is True


def test_browser_cookie_assist_target_override_ignores_other_configured_ids(tmp_path, monkeypatch):
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("Cookie: aws-waf-token=abc; session=def\n", encoding="utf-8")
    monkeypatch.setattr("jobagg.scheduler.webbrowser.open", lambda url: None)
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing",
        extra={"browser_cookie_assist": True},
    )
    args = Namespace(
        browser_cookie_assist=False,
        browser_cookie_assist_on_block=True,
        browser_cookie_source_id=["unicef_pageup", "other_source"],
        browser_cookie_file=str(cookie_file),
        browser_cookie_env="JOBAGG_TEST_COOKIE_HEADER",
        no_browser_open=True,
    )

    _apply_browser_cookie_assist(args, [source], target_ids={source.id})

    assert source.extra["cookie_header"] == "aws-waf-token=abc; session=def"
    assert source.extra["browser_cookie_assist_active"] is True
