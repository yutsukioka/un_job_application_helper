from argparse import Namespace

from jobagg.models import OrganizationSource
from jobagg.scheduler import _apply_browser_cookie_assist, _normalize_cookie_header


def test_normalize_cookie_header_accepts_raw_header_and_curl_header():
    assert _normalize_cookie_header("Cookie: aws-waf-token=abc; session=def") == (
        "aws-waf-token=abc; session=def"
    )
    assert _normalize_cookie_header("curl -H 'Cookie: aws-waf-token=abc; session=def' https://example.org") == (
        "aws-waf-token=abc; session=def"
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
