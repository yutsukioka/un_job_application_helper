from __future__ import annotations

import logging
import sys
from argparse import Namespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "jobagg"))

from jobagg.models import OrganizationSource  # noqa: E402
from jobagg.scheduler import _apply_browser_cookie_assist  # noqa: E402


def test_B4_cookie_assist_rejects_cross_domain_cookie_without_logging_secret(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text(
        "Cookie: aws-waf-token=secret-cookie-value; Domain=evil.example; session=also-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("jobagg.scheduler.webbrowser.open", lambda url: None)
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
        browser_cookie_assist_on_block=False,
        browser_cookie_source_id=[],
        browser_cookie_file=str(cookie_file),
        browser_cookie_env="JOBAGG_TEST_COOKIE_HEADER",
        no_browser_open=True,
    )

    with caplog.at_level(logging.WARNING):
        _apply_browser_cookie_assist(args, [source])

    assert "cookie_header" not in source.extra
    assert source.extra.get("browser_cookie_assist_active") is not True
    assert "declared cookie domain does not match target host" in caplog.text
    assert "secret-cookie-value" not in caplog.text
    assert "also-secret" not in caplog.text
