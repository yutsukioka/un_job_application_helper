from pathlib import Path

import pytest

from jobagg.adapters.base import AdapterContext
from jobagg.adapters.peoplesoft import PeopleSoftAdapter
from jobagg.http import HttpResponse
from jobagg.models import OrganizationSource


def listing(ids=("1", "2"), total=None):
    rows = "".join(f'''<li id='HRS_AGNT_RSLT_I$0_row_{index}'>
        <span class='ps_box-value' id='SCH_JOB_TITLE${index}'>Role {job_id}</span>
        <span class='ps_box-value' id='HRS_JOB_OPENING_ID${index}'>{job_id}</span>
        <a id='HRS_VIEW_DETAILSPB${index}'>View Job Description</a></li>'''
                   for index, job_id in enumerate(ids))
    count = len(ids) if total is None else total
    return f'''<form name='win0' action='https://job.ifad.org/search'>
        <input type='hidden' name='ICSID' value='synthetic-guest-token'>
        <input type='hidden' name='ICStateNum' value='1'>
        {count} jobs found. {rows}</form>'''


def detail(job_id="1"):
    return f'''<?xml version='1.0'?><PAGE id='HRS_APP_JBPST_FL'>
        <GENSCRIPT><![CDATA[ignoredScript();]]></GENSCRIPT>
        <FIELD id='win0divPAGECONTAINER'><![CDATA[
        <span class='ps_box-value' id='HRS_SCH_WRK2_HRS_JOB_OPENING_ID'>{job_id}</span>
        <span class='ps_box-value' id='HRS_SCH_WRK2_POSTING_TITLE'>Role {job_id}</span>
        <span class='ps-text' id='HRS_SCH_WRK_DESCR100$0lbl'>Job Role</span>
        <span class='ps_box-value' id=HRS_SCH_PSTDSC_DESCRLONG$0>
            <p>First <span>nested content</span> important tail.</p><p>Second paragraph.</p>
        </span>
        <span class='ps-text' id='HRS_SCH_WRK_DESCR100$1lbl'>Other Information</span>
        <span class='ps_box-value' id=HRS_SCH_PSTDSC_DESCRLONG$1>Final requirements.</span>
        ]]></FIELD></PAGE>'''


class FakeHTTP:
    def __init__(self, gets, posts=()):
        self.gets = iter(gets)
        self.posts = iter(posts)
        self.actions = []

    def get(self, url):
        return HttpResponse(url, 200, {}, next(self.gets))

    def post_form(self, url, payload, **kwargs):
        self.actions.append((url, payload.copy()))
        return HttpResponse(url, 200, {}, next(self.posts))


def adapter(http, fetch_details=False):
    source = OrganizationSource("ifad_peoplesoft", "IFAD", "peoplesoft", "https://job.ifad.org/search",
                                extra={"listing_url": "https://job.ifad.org/search", "fetch_details": fetch_details})
    return PeopleSoftAdapter(AdapterContext(source, http))


def test_public_guest_post_re_resolves_reordered_rows_and_does_not_persist_tokens():
    http = FakeHTTP([listing(), listing(("2", "1"))], [detail("1"), detail("2")])
    a = adapter(http)
    jobs = a.fetch_jobs()
    first = a.fetch_detail_for_listing_item(jobs[0].raw)
    second = a.fetch_detail_for_listing_item(jobs[1].raw)
    assert [first.external_id, second.external_id] == ["1", "2"]
    assert [fields["ICAction"] for _, fields in http.actions] == ["HRS_VIEW_DETAILSPB$0"] * 2
    assert all(fields["ICSID"] == "synthetic-guest-token" for _, fields in http.actions)
    assert "synthetic-guest-token" not in str(first.raw)
    assert "job_id=" not in first.apply_url
    assert a.run_diagnostics.total_reported_by_source == 2
    assert a.run_diagnostics.pagination_complete is True


def test_nested_values_and_all_sections_preserve_the_final_text():
    job = adapter(FakeHTTP([])).parse_detail_html(detail(), item={"job_id": "1"}, detail_url="https://job.ifad.org/search")
    assert "First nested content important tail. Second paragraph." in job.description
    assert job.description.endswith("Other Information Final requirements.")
    assert len(job.raw["detail_sections"]) == 2
    assert "ignoredScript" not in job.description


def test_roster_without_id_requires_fresh_guest_action_and_unique_exact_title():
    response = detail().replace("<span class='ps_box-value' id='HRS_SCH_WRK2_HRS_JOB_OPENING_ID'>1</span>", "")
    with pytest.raises(ValueError):
        adapter(FakeHTTP([])).parse_detail_html(response, item={"job_id": "1", "title": "Role 1"}, detail_url="https://job.ifad.org/search")
    a = adapter(FakeHTTP([listing()], [response]))
    job = a.fetch_detail_for_listing_item(a.fetch_jobs()[0].raw)
    assert job.external_id == "1"
    assert job.raw["response_job_id"] is None
    assert job.raw["identity_verification"] == "guest_row_id_and_exact_unique_title"


def test_roster_without_id_rejects_ambiguous_listing_titles():
    response = detail().replace("<span class='ps_box-value' id='HRS_SCH_WRK2_HRS_JOB_OPENING_ID'>1</span>", "")
    a = adapter(FakeHTTP([listing().replace("Role 2", "Role 1")], [response]))
    with pytest.raises(ValueError):
        a.fetch_detail_for_listing_item(a.fetch_jobs()[0].raw)


@pytest.mark.parametrize("response", ["<html><body><main>Search Jobs and a long listing.</main></body></html>",
                                     "<PAGE id='HRS_APP_SCHJOB_FL'><FIELD id='win0divPAGECONTAINER'/></PAGE>",
                                     detail("999")])
def test_wrong_page_or_identity_is_rejected(response):
    with pytest.raises(ValueError):
        adapter(FakeHTTP([])).parse_detail_html(response, item={"job_id": "1"}, detail_url="https://job.ifad.org/search")


def test_empty_query_hundred_row_cap_is_never_certified():
    a = adapter(FakeHTTP([listing(tuple(str(i) for i in range(100)))]))
    assert len(a.fetch_jobs()) == 100
    assert a.run_diagnostics.pagination_complete is False


def test_explicit_zero_is_verified():
    a = adapter(FakeHTTP([listing(())]))
    assert a.fetch_jobs() == []
    assert a.run_diagnostics.pagination_complete is True
    assert a.run_diagnostics.empty_reason == "verified_total_zero"


def test_fetch_details_option_actually_retrieves_details():
    a = adapter(FakeHTTP([listing(("1",))], [detail()]), fetch_details=True)
    assert a.fetch_jobs()[0].description.endswith("Final requirements.")


def test_real_ifad_office_associate_retains_all_seven_sections():
    response = (Path(__file__).parent / "fixtures/ifad/37861_detail.xml").read_text()
    job = adapter(FakeHTTP([])).parse_detail_html(response, item={"job_id": "37861"}, detail_url="https://job.ifad.org/search")
    assert job.title == "Office Associate (Resource Management)"
    assert len(job.raw["detail_sections"]) == 7
    assert "Job Profile Requirements" in job.description
    assert "Managing time, resources and information" in job.description
    assert job.description.endswith("legal status to live and work in the country of recruitment.")
    assert len(job.description) > 13000


def direct_adapter(http):
    a = adapter(http)
    a.source.extra['public_job_deeplinks'] = True
    return a


def test_direct_public_route_uses_job_opening_id_and_posting_page():
    from urllib.parse import urlsplit, parse_qs
    a = direct_adapter(FakeHTTP([detail('38079')]))
    job = a.fetch_detail_for_listing_item({'job_id':'38079'})
    query = parse_qs(urlsplit(job.apply_url).query)
    assert query['JobOpeningId'] == ['38079']
    assert query['Page'] == ['HRS_APP_JBPST_FL']
    assert job.raw['detail_fetch_method'] == 'public_job_deeplink_get'
    assert job.raw['identity_verification'] == 'explicit_detail_job_id'


def test_direct_roster_requires_fresh_unique_listing_identity():
    response = detail('2095').replace("<span class='ps_box-value' id='HRS_SCH_WRK2_HRS_JOB_OPENING_ID'>2095</span>", '')
    job = direct_adapter(FakeHTTP([response,listing(('2095',))])).fetch_detail_for_listing_item({'job_id':'2095'})
    assert job.raw['identity_verification'] == 'exact_deeplink_and_fresh_unique_listing_title'
    with pytest.raises(ValueError, match='uniquely bound'):
        direct_adapter(FakeHTTP([response,listing(('2096',))])).fetch_detail_for_listing_item({'job_id':'2095'})


def test_direct_route_rejects_wrong_response_id_and_search_redirect():
    from jobagg.adapters.peoplesoft import ifad_detail_url
    with pytest.raises(ValueError, match='identity mismatch'):
        direct_adapter(FakeHTTP([detail('999')])).fetch_detail_for_listing_item({'job_id':'38079'})
    class Redirected:
        def get(self,url):
            return HttpResponse('https://job.ifad.org/search',200,{},detail('38079'))
    with pytest.raises(ValueError, match='redirected'):
        direct_adapter(Redirected()).fetch_detail_for_listing_item({'job_id':'38079'})
    with pytest.raises(ValueError):
        ifad_detail_url('38079&SiteId=2000')


def test_full_document_excludes_session_form_from_saved_public_html():
    from jobagg.adapters.peoplesoft import _detail_page_html
    body = _detail_page_html(detail('38079'))
    full = "<html><input name='ICSID' value='private-session'><div id='win0divPAGECONTAINER'>" + body + '</div></html>'
    job = direct_adapter(FakeHTTP([full])).fetch_detail_for_listing_item({'job_id':'38079'})
    assert 'private-session' not in str(job.raw)
    assert 'nested content' in job.description


def test_nine_observed_public_deeplink_containers_parse_all_sections():
    import gzip
    import json
    data = json.loads(gzip.decompress((Path(__file__).parent/'fixtures/ifad/public_deeplinks_20260921.json.gz').read_bytes()))
    assert len(data) == 9
    for row in data:
        fresh = listing((row['id'],)).replace('Role '+row['id'],row['title'])
        full = "<html><div id='win0divPAGECONTAINER'>" + row['public_container'] + '</div></html>'
        job = direct_adapter(FakeHTTP([full,fresh])).fetch_detail_for_listing_item({'job_id':row['id']})
        assert job.title == row['title']
        assert len(job.raw['detail_sections']) == row['sections']
        assert len(job.description) > 1000
        assert 'ICSID' not in str(job.raw)
