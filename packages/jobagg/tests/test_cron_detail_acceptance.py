import pytest
from jobagg.vacancy_outcomes import (
    IncompleteDetailResponse,
    VacancyUnavailable,
    unavailable_template,
)
from test_taleo_public_bindings import fixture, adapter
from test_opcw_public_deadline import SOURCE, URL, HTML
from jobagg.adapters.static_html import parse_detail_page


@pytest.mark.parametrize(
    "source", ["who_taleo", "wipo_taleo", "adb_taleo", "iaea_taleo", "fao_taleo"]
)
def test_returned_native_identity_and_body_required_on_every_taleo_locale(source, monkeypatch):
    obj = adapter(source)
    html, url = fixture(source)
    monkeypatch.setattr(obj, "fetch_text", lambda url: html)
    actual = obj.fetch_detail_for_listing_item({"_taleo_detail_url": url})
    assert actual.raw["_taleo_record_kind"] == "detail"
    html = "<title>Job Search</title><nav>" + "Home Sign In Job Search " * 100 + "</nav>"
    with pytest.raises(IncompleteDetailResponse, match="native requisition"):
        obj.fetch_detail_for_listing_item({"_taleo_detail_url": url})


def test_opcw_unavailable_panel_is_not_a_job_and_requires_matching_route():
    body = '<div id="ctl00_defaultValidationSummary"><ul><li>This vacancy does not exist/no longer exists on this site</li></ul></div>'
    with pytest.raises(VacancyUnavailable):
        parse_detail_page(SOURCE, body, URL)
    assert unavailable_template(SOURCE.id, "582", URL, URL, body)
    assert not unavailable_template(SOURCE.id, "999", URL, URL, body)
    assert not unavailable_template(SOURCE.id, "582", URL, URL + "?error=1", body)
    assert not unavailable_template(SOURCE.id, "582", URL, URL, HTML)
    assert not unavailable_template(
        SOURCE.id,
        "582",
        URL,
        URL,
        "<p>This vacancy does not exist/no longer exists on this site</p>",
    )
