from pathlib import Path
from types import SimpleNamespace

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.static_html import StaticHTMLAdapter, parse_detail_page
from jobagg.models import OrganizationSource


def test_osce_visible_title_contract_and_date_only_values():
    html = (Path(__file__).parent / "fixtures/eu_careers/osce_4936_public_20260913.html").read_text()
    source = OrganizationSource(id="osce_custom_html", name="OSCE", ats_family="static_html",
                                base_url="https://vacancies.osce.org")
    job = parse_detail_page(source, html, "https://vacancies.osce.org/jobs/adviser-on-gender-issues-s-4936")
    assert job.title == "Adviser on Gender Issues (S)"
    assert job.employment_type == "International Secondment"
    assert job.raw["grade"] == "S"
    assert job.posted_at is None and job.closes_at is None
    proof = job.raw["_osce_public_field_resolution"]
    assert proof["public_issue_date"] == "Jun 30, 2026"
    assert proof["public_closing_date"] == "Sep 21, 2026"
    assert "roster of suitable candidates (valid for three years)" in job.description
    assert "maximum period of service in this post is 10 years" in job.description
    response = SimpleNamespace(text=html, headers={"Content-Type": "text/html"}, content=html.encode())
    adapter = StaticHTMLAdapter(AdapterContext(source=source, http=SimpleNamespace(get=lambda *args, **kwargs: response)))
    adapter.ensure_allowed = lambda url: None
    detail = adapter.fetch_detail_for_listing_item({"external_id": job.external_id, "href": job.apply_url,
                                                   "closes_at": "2026-09-21T00:00:00Z", "posted_at": "2026-06-30T00:00:00Z"})
    assert detail.closes_at is None and detail.posted_at is None
