from urllib.parse import parse_qs, urlsplit

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.models import OrganizationSource


class HTTP:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def post_json(self, url, payload, headers=None):
        locale = parse_qs(urlsplit(url).query)["lang"][0]
        self.requests.append((locale, payload, headers))
        data = self.pages[locale][payload["pageNo"]]
        class Response:
            def json(self):
                return data
        return Response()


def page(ids, total, locales=("en", "fr_FR", "es"), supported=None):
    return {
        "pagingData": {"totalCount": total, "pageSize": 2},
        "requisitionList": [{"contestNo": i, "column": [f"Title {i}"], "linkedColumn": 0} for i in ids],
        "facetResults": [{"id": "JOB_LOCALE", "facetValueResults": [{"id": x, "text": x} for x in locales]}],
        "supportedLanguages": supported or [],
    }


def adapter(pages, **extra):
    source = OrganizationSource(id="test_taleo", name="Taleo", ats_family="taleo", base_url="https://example.taleo.net/careersection/ex/jobsearch.ftl",
        extra={"search_api_url": "https://example.taleo.net/search?lang=en&portal=123", "warmup_search_page": False,
               "enumerate_job_locales": True, "max_pages": 3, **extra})
    return TaleoAdapter(AdapterContext(source, HTTP(pages)))


def test_all_posting_locales_union_ids_and_preserve_overlap_provenance():
    obj = adapter({"en": {1: page(["1", "2"], 2)}, "fr_FR": {1: page(["2", "3"], 2)}, "es": {1: page(["4"], 1)}})
    jobs = obj.fetch_jobs()
    assert {j.external_id for j in jobs} == {"1", "2", "3", "4"}
    duplicate = next(j for j in jobs if j.external_id == "2")
    assert duplicate.raw["_taleo_available_locales"] == ["en", "fr_FR"]
    assert set(duplicate.raw["_taleo_locale_listings"]) == {"en", "fr_FR"}
    assert parse_qs(urlsplit(duplicate.apply_url).query)["lang"] == ["en"]
    spanish = next(j for j in jobs if j.external_id == "4")
    assert parse_qs(urlsplit(spanish.apply_url).query)["lang"] == ["es"]
    assert spanish.source_url == spanish.apply_url == spanish.raw["_taleo_detail_url"]
    assert "lang=" not in spanish.raw["_taleo_original_urls"]["apply_url"]
    assert obj.run_diagnostics.total_reported_by_source is None
    assert obj.run_diagnostics.pagination_complete is True
    assert obj.language_inventory["distinct_count"] == 4
    assert {r[0] for r in obj.context.http.requests} == {"en", "fr_FR", "es"}
    assert all(not r[1]["filterSelectionParam"]["searchFilterSelections"] for r in obj.context.http.requests)


def test_inflated_locale_total_keeps_union_incomplete():
    obj = adapter({"en": {1: page(["1"], 1)}, "fr_FR": {1: page(["2"], 2)}, "es": {1: page(["3"], 1)}})
    assert len(obj.fetch_jobs()) == 3
    assert obj.run_diagnostics.pagination_complete is False
    assert obj.language_inventory["locales"]["fr_FR"]["reported_total"] == 2
    assert obj.language_inventory["locales"]["fr_FR"]["unique_count"] == 1


def test_interface_languages_do_not_create_unadvertised_job_locales():
    obj = adapter({"en": {1: page(["1"], 1, locales=("en",), supported=[{"code": "fr_FR"}])}})
    obj.fetch_jobs()
    assert list(obj.language_inventory["locales"]) == ["en"]


def test_language_bound_fails_closed_instead_of_claiming_complete():
    obj = adapter({"en": {1: page(["1"], 1)}}, max_job_locales=1)
    with pytest.raises(ValueError, match="language limit"):
        obj.fetch_jobs()


def test_language_url_rejects_host_or_path_injection():
    with pytest.raises(ValueError, match="language code"):
        TaleoAdapter._language_url("https://example.taleo.net/search", "es&url=https://evil.test")


def test_detail_rejects_returned_other_requisition_and_warms_posting_language():
    obj = adapter({})
    requests = []
    obj.fetch_text = lambda url: requests.append(url) or "captured detail"
    obj._parse_taleo_detail_payload = lambda _: {"external_id": "other"}
    obj.parse_detail_html = lambda *_: obj.parse_listing_item({"contestNo": "other", "title": "Wrong"})
    with pytest.raises(ValueError, match="identity differs"):
        obj.fetch_detail_for_listing_item({"contestNo": "wanted", "_taleo_posting_locale": "fr_FR",
            "_taleo_available_locales": ["fr_FR"], "_taleo_locale_listings": {"fr_FR": {"contestNo": "wanted"}},
            "_taleo_detail_url": "https://example.taleo.net/jobdetail.ftl?job=wanted"})
    assert parse_qs(urlsplit(requests[0]).query)["lang"] == ["fr_FR"]
    assert requests[1] == "https://example.taleo.net/jobdetail.ftl?job=wanted&lang=fr_FR"


@pytest.mark.parametrize('text,expected', [('23/sept./2026, 00:59:00', '2026-09-23T00:59:00'), ('21/août/2026', '2026-08-21T00:00:00'), ('22/sep/2026, 00:59:00', '2026-09-22T00:59:00')])
def test_localized_dates_use_explicit_named_month_and_24_hour_clock(text, expected):
    assert TaleoAdapter._localized_taleo_date(text).isoformat() == expected


def test_localized_deadline_uses_observed_url_timezone_and_preserves_wall_clock():
    source = OrganizationSource(id='fao_taleo', name='FAO', ats_family='taleo', base_url='https://jobs.fao.org')
    obj = TaleoAdapter(AdapterContext(source, HTTP({})))
    values = [''] * 33
    values[4] = 'Candidature pour le poste : Expert - (Numéro de lemploi : {1})'
    values[10:15] = ['2601901', 'Expert', '08/sept./2026', '08/sept./2026', '23/sept./2026, 00:59:00']
    values[32] = '%3Cp%3EDescription complète%3C/p%3E'
    body = "api.fillList('requisitionDescriptionInterface', 'descRequisition', " + repr(values) + ');'
    job = obj.parse_detail_html(body, 'https://jobs.fao.org/jobdetail.ftl?job=2601901&tz=GMT%2B03%3A00&tzname=Africa%2FNairobi')
    assert job.closes_at.isoformat() == '2026-09-22T21:59:00+00:00'
    assert job.closes_at_local == '23/sept./2026, 00:59:00'
    assert job.closes_tz == 'Africa/Nairobi'


def test_localized_deadline_without_timezone_evidence_stays_unknown():
    assert TaleoAdapter._localized_taleo_date('31/févr./2026') is None


def test_explicit_ongoing_posting_does_not_invent_a_deadline():
    source = OrganizationSource(id='fao_taleo', name='FAO', ats_family='taleo', base_url='https://jobs.fao.org')
    obj = TaleoAdapter(AdapterContext(source, HTTP({})))
    values = [''] * 33
    values[4] = 'Candidatura para la posición: Consultor - (Número de puesto: 2303829)'
    values[10:15] = ['2303829', 'Consultor', '06/mar/2024', '06/mar/2024', 'Continuo']
    values[32] = '%3Cp%3ETexto completo%3C/p%3E'
    body = "api.fillList('requisitionDescriptionInterface', 'descRequisition', " + repr(values) + ');'
    job = obj.parse_detail_html(body, 'https://jobs.fao.org/jobdetail.ftl?job=2303829&tzname=Africa%2FNairobi')
    assert job.posted_at is None
    assert job.raw['_taleo_posting_time_resolution']['public_value'] == '06/mar/2024'
    assert job.closes_at is None
    assert job.raw['_taleo_deadline_open_ended']['public_value'] == 'Continuo'
