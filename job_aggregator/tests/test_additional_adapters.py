import json
import re
from pathlib import Path

from jobagg.adapters.avature import AvatureAdapter
from jobagg.adapters.base import AdapterContext
from jobagg.adapters.csod import CSODAdapter, _extract_balanced_json_object
from jobagg.adapters.icddrb import ICDDRBAdapter, _vacancy_links
from jobagg.adapters.imo import IMOAPIAdapter
from jobagg.adapters.oracle_hcm import OracleHCMAdapter, classify_fetch_error
from jobagg.adapters.pageup import PageUpAdapter
from jobagg.adapters.peoplesoft import PeopleSoftAdapter
from jobagg.adapters.smartrecruiters import SmartRecruitersAdapter
from jobagg.adapters.static_html import StaticHTMLAdapter, parse_detail_page, parse_unssc_jobs
from jobagg.adapters.successfactors_rmk import SuccessFactorsRMKAdapter
from jobagg.adapters.successfactors_rmk import SuccessFactorsLegacyAdapter
from jobagg.adapters.successfactors_rmk import _next_page_url
from jobagg.adapters.unv import UNVAdapter
from jobagg.adapters.workable import WorkableAdapter
from jobagg.classification.extractors import OracleHCMExtractor
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource


class FakeResponse:
    def __init__(self, payload=None, *, text=None):
        self.payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self.payload


class FakeHTTP:
    def __init__(self, pages):
        self.pages = pages
        self.post_calls = []

    def post_json(self, url, payload, *, headers=None):
        self.post_calls.append((url, payload, headers or {}))
        return FakeResponse(self.pages[payload["pageNumber"]])


class FakeTextHTTP:
    def __init__(self, text):
        self.text = text

    def get(self, url, *, headers=None):
        return FakeResponse(text=self.text)


class FakeOracleCEHTTP:
    def __init__(self, pages, *, site_settings=None, detail_pages=None):
        self.pages = pages
        self.site_settings = site_settings or {"siteNumber": "CX_1", "siteName": "UNDP"}
        self.detail_pages = detail_pages or {}
        self.get_calls = []

    def get(self, url, *, headers=None):
        self.get_calls.append((url, headers or {}))
        if "/siteSettings/" in url:
            return FakeResponse(self.site_settings)
        if "recruitingCEJobRequisitionDetails" in url:
            for job_id, payload in self.detail_pages.items():
                if str(job_id) in url:
                    return FakeResponse(payload)
            raise AssertionError(f"Unexpected detail URL: {url}")
        offset = 0
        match = re.search(r"offset=(\d+)", url)
        if match:
            offset = int(match.group(1))
        return FakeResponse(self.pages[offset])


def source(family, **extra):
    return OrganizationSource(
        id=f"{family}_org",
        name="Example Org",
        ats_family=family,
        base_url="https://example.org",
        extra=extra,
    )


def fixture_json(*parts):
    return json.loads((Path(__file__).parent / "fixtures" / Path(*parts)).read_text())


def test_csod_posts_paginated_search_payload():
    org = source(
        "csod",
        api_url="https://api.example.org/jobs",
        detail_url_template="https://example.org/requisition/{job_id}",
        page_size=1,
        max_pages=5,
        search_payload={"careerSiteId": 1},
    )
    http = FakeHTTP(
        {
            1: {
                "data": {
                    "totalCount": 2,
                    "requisitions": [
                        {
                            "requisitionId": 36648,
                            "displayJobTitle": "E T Consultant, Analyst",
                            "postingEffectiveDate": "5/14/2026",
                            "postingExpirationDate": "5/21/2026",
                            "locations": [{"city": "Dhaka", "country": "BD"}],
                            "externalDescription": "<p>Role text</p>",
                        }
                    ],
                }
            },
            2: {
                "data": {
                    "totalCount": 2,
                    "requisitions": [
                        {
                            "requisitionId": 36585,
                            "displayJobTitle": "Digital Specialist",
                            "postingExpirationDate": "5/28/2026",
                        }
                    ],
                }
            },
        }
    )
    adapter = CSODAdapter(AdapterContext(source=org, http=http))

    jobs = adapter.fetch_jobs()

    assert [call[1]["pageNumber"] for call in http.post_calls] == [1, 2]
    assert [job.external_id for job in jobs] == ["36648", "36585"]
    assert jobs[0].location == "Dhaka, BD"
    assert jobs[0].closes_at.isoformat() == "2026-05-21T00:00:00+00:00"


def test_csod_extracts_balanced_context_json():
    html = """
    <script>
    window.csod.context = {
      "token": "anon-token",
      "endpoints": {"cloud": "https://us.api.csod.com"},
      "metadata": {"text": "brace } inside string"}
    };
    </script>
    """

    extracted = _extract_balanced_json_object(html, "csod.context")

    assert extracted is not None
    parsed = json.loads(extracted)
    assert parsed["token"] == "anon-token"
    assert parsed["metadata"]["text"] == "brace } inside string"


def test_csod_discovers_anonymous_token_before_listing_post():
    class FakeCSODHTTP:
        def __init__(self):
            self.get_calls = []
            self.post_calls = []

        def get(self, url, *, headers=None):
            self.get_calls.append((url, headers or {}))
            return FakeResponse(
                text="""
                <script>
                csod.context = {
                  "token": "anon-token",
                  "endpoints": {"cloud": "https://us.api.csod.com"}
                };
                </script>
                """
            )

        def post_json(self, url, payload, *, headers=None):
            self.post_calls.append((url, payload, headers or {}))
            return FakeResponse(
                {
                    "data": {
                        "totalCount": 1,
                        "requisitions": [
                            {
                                "requisitionId": 36648,
                                "displayJobTitle": "E T Consultant, Analyst",
                                "postingExpirationDate": "5/21/2026",
                            }
                        ],
                    }
                }
            )

    context_url = "https://worldbankgroup.csod.com/ux/ats/careersite/1/home?c=worldbankgroup&lang=en-US"
    org = source(
        "csod",
        requires_bearer_token=True,
        context_url=context_url,
        api_url="https://configured.example.org/rec-job-search/external/jobs",
        legacy_api_url="https://worldbankgroup.csod.com/services/x/career-site/v1/search",
        page_size=25,
        max_pages=2,
        search_payload={"careerSiteId": 1, "cultureName": "en-US"},
    )
    http = FakeCSODHTTP()
    adapter = CSODAdapter(AdapterContext(source=org, http=http))

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 1
    assert http.get_calls[0][0] == context_url
    assert http.post_calls[0][0] == "https://us.api.csod.com/rec-job-search/external/jobs"
    headers = http.post_calls[0][2]
    assert headers["Authorization"] == "Bearer anon-token"
    assert headers["Origin"] == "https://worldbankgroup.csod.com"
    assert headers["Referer"] == context_url
    assert headers["Csod-Accept-Language"] == "en-US"


def test_oracle_hcm_parses_nested_requisition_list():
    adapter = OracleHCMAdapter(
        AdapterContext(
            source=source("oracle_hcm", site_number="CX_1"),
            http=JobAggHTTPClient(),
        )
    )

    jobs = adapter.parse_jobs(
        {
            "items": [
                {
                    "TotalJobsCount": 1,
                    "requisitionList": [
                        {
                            "Id": "33995",
                            "Title": "Human Resources Analyst",
                            "PostedDate": "2026-05-14",
                            "PostingEndDate": "2026-06-01",
                            "PrimaryLocation": "Bangkok, Thailand",
                            "ShortDescriptionStr": "Human Resources",
                        }
                    ],
                }
            ]
        }
    )

    assert len(jobs) == 1
    assert jobs[0].external_id == "33995"
    assert jobs[0].apply_url.endswith("/job/33995")
    assert jobs[0].description == "Human Resources"


def test_oracle_hcm_classifies_dns_resolution_errors():
    assert classify_fetch_error(OSError("getaddrinfo failed")) == "dns_resolution_failed"
    assert classify_fetch_error(RuntimeError("GET failed with HTTP 401: no auth")) == "auth_or_forbidden"


def test_oracle_hcm_validates_site_settings_and_paginates_to_total(monkeypatch):
    monkeypatch.setattr("jobagg.adapters.oracle_hcm._host_resolves", lambda url: True)
    org = source(
        "oracle_hcm",
        site_number="CX_1",
        expected_site_name="UNDP",
        page_size=2,
        max_pages=5,
    )
    http = FakeOracleCEHTTP(
        {
            0: {
                "items": [
                    {
                        "TotalJobsCount": 3,
                        "requisitionList": [
                            {"Id": "1", "Title": "Role 1"},
                            {"Id": "2", "Title": "Role 2"},
                        ],
                    }
                ]
            },
            2: {
                "items": [
                    {
                        "TotalJobsCount": 3,
                        "requisitionList": [
                            {"Id": "3", "Title": "Role 3"},
                        ],
                    }
                ]
            },
        },
        site_settings={"siteNumber": "CX_1", "siteName": "UNDP Careers"},
    )
    adapter = OracleHCMAdapter(AdapterContext(source=org, http=http))

    jobs = adapter.fetch_jobs()

    assert [job.external_id for job in jobs] == ["1", "2", "3"]
    assert any("/siteSettings/CX_1" in call[0] for call in http.get_calls)
    assert adapter.run_diagnostics.scope_validation_status == "passed"
    assert adapter.run_diagnostics.total_reported_by_source == 3
    assert adapter.run_diagnostics.pages_fetched == 2
    assert adapter.run_diagnostics.pagination_complete is True


def test_oracle_hcm_records_hosted_agency_scope_from_facets(monkeypatch):
    monkeypatch.setattr("jobagg.adapters.oracle_hcm._host_resolves", lambda url: True)
    org = source(
        "oracle_hcm",
        site_number="CX_1",
        expected_site_name="UNDP",
        page_size=25,
        max_pages=2,
    )
    http = FakeOracleCEHTTP(
        {0: fixture_json("oracle", "cx1_undp_hosted_agencies_list.json")},
        site_settings={"siteNumber": "CX_1", "siteName": "UNDP"},
    )
    adapter = OracleHCMAdapter(AdapterContext(source=org, http=http))

    jobs = adapter.fetch_jobs()

    assert [job.external_id for job in jobs] == ["34287", "33995", "34141"]
    assert adapter.run_diagnostics.observed_agency_counts == {
        "UNCDF": 1,
        "UN Volunteers": 1,
        "UNDP": 17,
    }
    assert adapter.run_diagnostics.observed_organization_counts == {
        "United Nations Capital Development Fund": 1,
        "United Nations Development Programme": 166,
    }
    assert jobs[0].raw["oracle_site_number"] == "CX_1"
    assert jobs[0].raw["oracle_site_name"] == "UNDP"


def test_oracle_hcm_detail_uses_har_backed_flex_fields_for_contract_and_grade():
    org = source("oracle_hcm", site_number="CX_1")
    adapter = OracleHCMAdapter(AdapterContext(source=org, http=JobAggHTTPClient()))

    job = adapter.parse_jobs(fixture_json("oracle", "cx1_undp_hosted_agencies_detail.json"))[0]
    features = OracleHCMExtractor().extract(
        {
            "job_key": job.identity_key(),
            "source_id": job.source_id,
            "ats_family": job.ats_family,
            "title": job.title,
            "description": job.description,
            "location": job.location,
            "department": job.department,
            "employment_type": job.employment_type,
            "raw": job.raw,
        }
    )

    assert job.external_id == "34287"
    assert job.closes_at.isoformat() == "2026-05-24T07:34:00+00:00"
    assert job.employment_type == "International Personnel Service Agreement"
    assert features.grade_raw == "IPSA-8"
    assert features.contract_raw == "International Personnel Service Agreement"
    assert features.contract_source_field == "requisitionFlexFields.Vacancy Type"
    assert features.evidence["Agency"] == "UNDP"
    assert features.evidence["Bureau"] == "Regional Bureau for Arab States"


def test_oracle_hcm_detail_url_quotes_id_like_candidate_experience_har():
    org = source("oracle_hcm", site_number="CX_1")
    detail_payload = fixture_json("oracle", "cx1_undp_hosted_agencies_detail.json")
    http = FakeOracleCEHTTP({}, detail_pages={"34287": detail_payload})
    adapter = OracleHCMAdapter(AdapterContext(source=org, http=http))

    adapter.fetch_detail_for_listing_item({"Id": "34287"})

    detail_url = next(call[0] for call in http.get_calls if "recruitingCEJobRequisitionDetails" in call[0])
    assert "Id=%2234287%22,siteNumber=CX_1" in detail_url


def test_oracle_hcm_site_name_mismatch_aborts_without_jobs(monkeypatch):
    monkeypatch.setattr("jobagg.adapters.oracle_hcm._host_resolves", lambda url: True)
    org = source(
        "oracle_hcm",
        site_number="CX_1001",
        expected_site_name="UN Women",
    )
    adapter = OracleHCMAdapter(
        AdapterContext(
            source=org,
            http=FakeOracleCEHTTP({}, site_settings={"siteNumber": "CX_1", "siteName": "UNDP"}),
        )
    )

    try:
        adapter.fetch_jobs()
    except RuntimeError as exc:
        assert "site number mismatch" in str(exc)
    else:
        raise AssertionError("Expected site mismatch to raise")
    assert adapter.run_diagnostics.scope_validation_status == "site_number_mismatch"
    assert adapter.run_diagnostics.health_status == "issue"


def test_oracle_hcm_falls_back_to_undp_listing_when_oracle_dns_fails(monkeypatch):
    class FakeOracleHTTP:
        def __init__(self):
            self.get_calls = []

        def get(self, url, *, headers=None):
            self.get_calls.append((url, headers or {}))
            return FakeResponse(
                text="""
                <a href="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/33853">
                  Job Title Project Associate in Kugitangtau landscape
                  Post level NPSA-6
                  Apply by May-15-26
                  Agency UNDP
                  Location Uzbekistan
                </a>
                """
            )

    monkeypatch.setattr("jobagg.adapters.oracle_hcm._host_resolves", lambda url: False)
    org = source(
        "oracle_hcm",
        site_number="CX_1",
        fallback_listing_url="https://jobs.undp.org/cj_view_jobs.cfm",
        fallback_parser="undp_official",
    )
    adapter = OracleHCMAdapter(AdapterContext(source=org, http=FakeOracleHTTP()))

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].external_id == "33853"
    assert jobs[0].title == "Project Associate in Kugitangtau landscape"
    assert jobs[0].employment_type == "NPSA-6"
    assert jobs[0].location == "Uzbekistan"
    assert jobs[0].closes_at.isoformat() == "2026-05-15T00:00:00+00:00"
    assert jobs[0].raw["fallback_reason"] == "dns_resolution_failed"


def test_oracle_hcm_falls_back_to_unfpa_current_jobs_when_oracle_dns_fails(monkeypatch):
    class FakeOracleHTTP:
        def __init__(self):
            self.get_calls = []

        def get(self, url, *, headers=None):
            self.get_calls.append((url, headers or {}))
            return FakeResponse(
                text="""
                <h2>Current Jobs</h2>
                <div>2 results found</div>
                <a href="/jobs/national-consultant-rmncah-monitoring-and-evaluation-me-consultant">
                  National Consultant: RMNCAH Monitoring and Evaluation (M&E) Consultant
                </a>
                <div>Closing date</div>
                <div>16 Jun 2026 20:56(America/New_York)</div>
                <div>Location</div>
                <div>Suva</div>
                <div>Staff grade/level</div>
                <div>Consultant</div>
                <div>Contract type</div>
                <div>Consultancy</div>
                <a href="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2003/job/34001">Apply</a>
                <a href="/jobs/national-consultant-rmncah-monitoring-and-evaluation-me-consultant">View Job</a>
                <h4>Pagination</h4>
                """
            )

    monkeypatch.setattr("jobagg.adapters.oracle_hcm._host_resolves", lambda url: False)
    org = source(
        "oracle_hcm",
        site_number="CX_2003",
        fallback_listing_url="https://www.unfpa.org/jobs",
        fallback_parser="unfpa_official",
        fallback_max_pages=1,
    )
    adapter = OracleHCMAdapter(AdapterContext(source=org, http=FakeOracleHTTP()))

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].external_id == "34001"
    assert jobs[0].location == "Suva"
    assert jobs[0].employment_type == "Consultancy"
    assert jobs[0].closes_at.isoformat() == "2026-06-16T00:00:00+00:00"
    assert jobs[0].source_url == (
        "https://www.unfpa.org/jobs/"
        "national-consultant-rmncah-monitoring-and-evaluation-me-consultant"
    )
    assert jobs[0].raw["listing_status"] == "public_listing_available_oracle_apply_link_unverified"


def test_oracle_hcm_generic_official_fallback(monkeypatch):
    class FakeOracleHTTP:
        def get(self, url, *, headers=None):
            return FakeResponse(
                text="""
                <a href="/about">About us</a>
                <a href="/careers/procurement-officer-123">Procurement Officer</a>
                <a href="/news/latest">Latest news</a>
                """
            )

    monkeypatch.setattr("jobagg.adapters.oracle_hcm._host_resolves", lambda url: False)
    org = source(
        "oracle_hcm",
        site_number="CX_3001",
        fallback_listing_url="https://icaocareers.icao.int/careers",
    )
    adapter = OracleHCMAdapter(AdapterContext(source=org, http=FakeOracleHTTP()))

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].title == "Procurement Officer"
    assert jobs[0].external_id == "procurement-officer-123"
    assert jobs[0].apply_url == "https://icaocareers.icao.int/careers/procurement-officer-123"
    assert jobs[0].raw["fallback_parser"] == "generic_official"


def test_oracle_hcm_generic_fallback_normalizes_unwomen_requisition_links(monkeypatch):
    class FakeOracleHTTP:
        def get(self, url, *, headers=None):
            return FakeResponse(
                text="""
                <a href="https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions/job/33077">
                  [RE-ADVERTISEMENT] UN Women: Communications Analyst (Retainer)
                </a>
                """
            )

    monkeypatch.setattr("jobagg.adapters.oracle_hcm._host_resolves", lambda url: False)
    org = source(
        "oracle_hcm",
        site_number="CX_1001",
        fallback_listing_url="https://www.unwomen.org/en/jobs/unwomen",
        fallback_parser="generic_official",
    )
    adapter = OracleHCMAdapter(AdapterContext(source=org, http=FakeOracleHTTP()))

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].external_id == "33077"
    assert jobs[0].apply_url == (
        "https://estm.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/33077"
    )


def test_workable_parses_v3_results():
    adapter = WorkableAdapter(
        AdapterContext(
            source=source("workable", account="ide-global"),
            http=JobAggHTTPClient(),
        )
    )

    jobs = adapter.parse_jobs(
        {
            "total": 1,
            "results": [
                {
                    "id": 5801742,
                    "shortcode": "7DE71DFD5E",
                    "title": "Google Gemini Strategy & Governance Intern",
                    "remote": True,
                    "location": {
                        "country": "United States",
                        "city": "Denver",
                        "region": "Colorado",
                    },
                    "department": [],
                    "published": "2026-05-08T00:00:00.000Z",
                    "workplace": "remote",
                }
            ],
        }
    )

    assert jobs[0].external_id == "7DE71DFD5E"
    assert jobs[0].location == "Denver, Colorado, United States"
    assert jobs[0].apply_url == "https://apply.workable.com/ide-global/j/7DE71DFD5E/"


def test_smartrecruiters_parses_listing_without_detail():
    adapter = SmartRecruitersAdapter(
        AdapterContext(
            source=source("smartrecruiters", company="CERN"),
            http=JobAggHTTPClient(),
        )
    )

    jobs = adapter.parse_jobs(
        {
            "content": [
                {
                    "id": "744000126465909",
                    "refNumber": "HSE-FRS-2026-104-LD",
                    "name": "Professional Firefighter",
                    "releasedDate": "2026-05-14T06:20:29.918Z",
                    "location": {"fullLocation": "Geneva, GENEVA, Switzerland"},
                    "department": {"label": "HSE"},
                    "typeOfEmployment": {"label": "Contract"},
                }
            ]
        }
    )

    assert jobs[0].external_id == "HSE-FRS-2026-104-LD"
    assert jobs[0].apply_url == "https://jobs.smartrecruiters.com/CERN/744000126465909"


def test_successfactors_parses_api_payload():
    adapter = SuccessFactorsRMKAdapter(
        AdapterContext(
            source=source(
                "successfactors_rmk",
                detail_url_template="https://jobs.example.org/job/{url_title}/{job_id}/",
            ),
            http=JobAggHTTPClient(),
        )
    )

    jobs = adapter.parse_jobs_from_api(
        {
            "totalJobs": 1,
            "jobSearchResult": [
                {
                    "response": {
                        "id": "13646",
                        "unifiedStandardTitle": "Principal Auditor",
                        "unifiedStandardStart": "12/05/2026",
                        "unifiedStandardEnd": "15/06/2026",
                        "jobLocationShort": ["Geneva, Switzerland "],
                        "filter3": ["Fixed Term"],
                        "urlTitle": "Principal-Auditor",
                    }
                }
            ],
        }
    )

    assert jobs[0].external_id == "13646"
    assert jobs[0].location == "Geneva, Switzerland"
    assert jobs[0].closes_at.isoformat() == "2026-06-15T00:00:00+00:00"


def test_successfactors_parses_table_html():
    adapter = SuccessFactorsRMKAdapter(
        AdapterContext(source=source("successfactors_rmk"), http=JobAggHTTPClient())
    )

    jobs = adapter.parse_jobs_from_html(
        """
        <tr class="data-row">
          <td><span class="jobTitle hidden-phone">
            <a href="/job/Vienna-Chief%2C-Internal-Oversight/1352289055/"
               class="jobTitle-link">Chief, Internal Oversight</a>
          </span></td>
          <td><span class="jobLocation">Vienna, Austria</span></td>
          <td><span class="jobDepartment">International Professionals</span></td>
          <td><span class="jobFacility">P5</span></td>
          <td><span class="jobDate">May 10, 2026</span></td>
          <td><span class="jobShifttype">4-Jun-26</span></td>
        </tr>
        """
    )

    assert jobs[0].external_id == "1352289055"
    assert jobs[0].employment_type == "P5"
    assert jobs[0].posted_at.isoformat() == "2026-05-10T00:00:00+00:00"
    assert jobs[0].closes_at.isoformat() == "2026-06-04T00:00:00+00:00"


def test_successfactors_follows_go_path_pagination():
    next_url = _next_page_url(
        """
        <a href="/go/View-all-categories/8942455/">1</a>
        <a href="/go/View-all-categories/8942455/25/?q=&sortColumn=referencedate">2</a>
        """,
        "https://jobs.itu.int/go/View-all-categories/8942455/",
    )

    assert next_url == "https://jobs.itu.int/go/View-all-categories/8942455/25/?q=&sortColumn=referencedate"


def test_unv_parses_search_payload():
    adapter = UNVAdapter(AdapterContext(source=source("unv"), http=JobAggHTTPClient()))

    jobs = adapter.parse_jobs(
        {
            "value": {
                "total": 1,
                "result": [
                    {
                        "id": "1784888021269034",
                        "name": "Content Creation and Social Media Management",
                        "publishDate": "2026-05-14T10:51:35.004Z",
                        "sourcingEndDate": "2026-05-28T00:00:00",
                        "country": {"label": "Malawi"},
                        "hostEntity": {"name": "United Nations Volunteers"},
                        "taskDescription": "Create social media content.",
                    }
                ],
            }
        }
    )

    assert jobs[0].external_id == "1784888021269034"
    assert jobs[0].location == "Malawi"
    assert jobs[0].description == "Create social media content."


def test_avature_parses_unops_listing_html():
    adapter = AvatureAdapter(AdapterContext(source=source("avature"), http=JobAggHTTPClient()))

    jobs = adapter.parse_listing_html(
        """
        <article class="article article--result 1" id="article--1">
          <h3><a class="link"
             href="https://careers.unops.org/careersmarketplace/JobDetail/Administration-Assistant/3112">
             Administration Assistant
          </a></h3>
          <div class="article__header__text__subtitle">
            <span>Islamabad</span> <span>•</span> <span>Associate</span>
            <span>•</span> <span>14-May-2026</span>
          </div>
          <div class="article__content">Support administration work.</div>
        </article>
        """
    )

    assert jobs[0].external_id == "3112"
    assert jobs[0].location == "Islamabad"
    assert jobs[0].posted_at.isoformat() == "2026-05-14T00:00:00+00:00"


def test_pageup_listing_percent_encodes_unicode_detail_urls():
    adapter = PageUpAdapter(
        AdapterContext(
            source=OrganizationSource(
                id="unicef_pageup",
                name="UNICEF",
                ats_family="pageup",
                base_url="https://jobs.unicef.org",
            ),
            http=JobAggHTTPClient(),
        )
    )

    jobs = adapter.parse_listing_html(
        """
        <div class="list-view--item">
          <a class="job-link"
             href="/en-us/job/593073/spécialiste-éducation">
             Spécialiste éducation #593073
          </a>
          <span class="location">Dakar</span>
          <time datetime="2026-06-01"></time>
        </div>
        """
    )

    assert jobs[0].external_id == "593073"
    assert jobs[0].raw["_pageup_detail_url"] == (
        "https://jobs.unicef.org/en-us/job/593073/sp%C3%A9cialiste-%C3%A9ducation"
    )


def test_imo_api_parses_current_vacancies_payload():
    adapter = IMOAPIAdapter(
        AdapterContext(
            source=source(
                "imo_api",
                detail_url_template="https://recruit.imo.org/vacancies/{job_id}",
            ),
            http=JobAggHTTPClient(),
        )
    )

    jobs = adapter.parse_jobs(
        [
            {
                "jobVacancyId": 101,
                "title": "Associate Programme Officer",
                "vacancyReference": "VN 26-01",
                "location": "London",
                "contractType": "Fixed-term",
                "dateofissue": "2026-05-01T00:00:00",
                "deadlineforapplications": "2026-05-30T23:59:00",
                "jobDescription": "<p>Coordinate technical cooperation projects.</p>",
            }
        ]
    )

    assert jobs[0].external_id == "101"
    assert jobs[0].location == "London"
    assert jobs[0].closes_at.isoformat() == "2026-05-30T23:59:00+00:00"
    assert jobs[0].apply_url == "https://recruit.imo.org/vacancies/101"
    assert jobs[0].description == "Coordinate technical cooperation projects."


def test_icddrb_detail_parser_extracts_apply_url_and_deadline():
    adapter = ICDDRBAdapter(
        AdapterContext(source=source("icddrb_custom_html"), http=JobAggHTTPClient())
    )

    job = adapter.parse_detail_page(
        """
        <html><head><title>Senior Technical Assistant</title></head>
        <body>
          <h1 class="job-title">Senior Technical Assistant (Adv#59/2026)</h1>
          <span class="posted-date">Posted on : May 09, 2026</span>
          <span class="deadline-date">Application deadline : May 16, 2026</span>
          <div class="icddrb-invites">
            Location: Dhaka
            Contract Type and Duration: Fixed Term
            icddr,b invites applications from suitable candidates.
          </div>
          <a href="/apply-for-job/32217/22320">Apply</a>
        </body></html>
        """,
        "https://career.icddrb.org/vacancy-preview/32217",
    )

    assert job.external_id == "32217"
    assert job.title == "Senior Technical Assistant (Adv#59/2026)"
    assert job.location == "Dhaka"
    assert job.employment_type == "Fixed Term"
    assert job.closes_at.isoformat() == "2026-05-16T00:00:00+00:00"
    assert job.apply_url == "https://career.icddrb.org/apply-for-job/32217/22320"


def test_icddrb_listing_accepts_unquoted_vacancy_links():
    links = _vacancy_links(
        """
        <tr>
          <td>
            <a href=https://career.icddrb.org/vacancy-preview/32222>
              Technical Assistant (Internal#64/2026).
            </a>
          </td>
        </tr>
        """,
        "https://career.icddrb.org/",
    )

    assert links == [
        {
            "href": "https://career.icddrb.org/vacancy-preview/32222",
            "title": "Technical Assistant (Internal#64/2026).",
        }
    ]


def test_peoplesoft_parser_prefers_value_spans_over_labels():
    adapter = PeopleSoftAdapter(
        AdapterContext(source=source("peoplesoft"), http=JobAggHTTPClient())
    )

    jobs = adapter.parse_listing_html(
        """
        <li id='HRS_AGNT_RSLT_I$0_row_0'>
          <span class="ps-label" id="SCH_JOB_TITLElbl$0">Job Title</span>
          <span class="ps_box-value" id="SCH_JOB_TITLE$0">Country Programme Officer</span>
          <span class="ps-label" id="HRS_JOB_OPENING_IDlbl$0">Job ID</span>
          <span class="ps_box-value" id="HRS_JOB_OPENING_ID$0">IFAD/2026/01</span>
          <span class="ps_box-value" id="LOCATION$0">Rome, Italy</span>
          <span class="ps_box-value" id="HRS_DEPT_DESCR$0">Programme Management</span>
          <span class="ps_box-value" id="SCH_OPENED$0">01-May-2026</span>
          <span class="ps_box-value" id="HRS_JO_PST_CLS_DT$0">31-May-2026</span>
        </li>
        """,
        listing_url="https://job.ifad.org/psc/IFHRPRDE/CAREERS/JOBS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL",
    )

    assert len(jobs) == 1
    assert jobs[0].title == "Country Programme Officer"
    assert jobs[0].external_id == "IFAD/2026/01"
    assert jobs[0].location == "Rome, Italy"
    assert jobs[0].closes_at.isoformat() == "2026-05-31T00:00:00+00:00"


def test_unssc_drupal_table_parser_extracts_codes_and_links():
    org = source("static_html")

    jobs = parse_unssc_jobs(
        org,
        """
        <table>
          <tr>
            <td class="views-field views-field-field-vacancy-code-1">IC_005_2026</td>
            <td class="views-field views-field-title">
              <a href="/sites/default/files/vacancy.pdf">Learning Specialist</a>
            </td>
            <td class="views-field views-field-field-issue-date">
              <time datetime="2026-05-04T12:00:00Z">04 May 2026</time>
            </td>
            <td class="views-field views-field-field-application-deadline">
              <time datetime="2026-05-24T12:00:00Z">24 May 2026</time>
            </td>
            <td class="views-field views-field-nid">
              <a href="/employment/application/ic-005-2026">Apply</a>
            </td>
          </tr>
        </table>
        """,
        "https://www.unssc.org/about/employment-opportunities",
    )

    assert jobs[0].external_id == "IC_005_2026"
    assert jobs[0].title == "Learning Specialist"
    assert jobs[0].source_url == "https://www.unssc.org/sites/default/files/vacancy.pdf"
    assert jobs[0].apply_url == "https://www.unssc.org/employment/application/ic-005-2026"
    assert jobs[0].closes_at.isoformat() == "2026-05-24T12:00:00+00:00"


def test_static_detail_parser_uses_json_ld_and_deadline_text():
    org = source("static_html")

    jobs = StaticHTMLAdapter(
        AdapterContext(source=org, http=JobAggHTTPClient())
    )._parse_generic_links(
        """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Geospatial Application and Data Analytics Associate (PSA-5)",
          "identifier": {"value": "unu-123"},
          "datePosted": "2026-05-01",
          "description": "<p>Application Deadline 24 May 2026</p>",
          "hiringOrganization": {"name": "United Nations University"},
          "jobLocation": {"address": {"addressLocality": "Bonn", "addressCountry": "Germany"}}
        }
        </script>
        """,
        "https://careers.unu.edu/o/geospatial-application-and-data-analytics-associate-psa5",
    )

    assert jobs[0].external_id == "unu-123"
    assert jobs[0].location == "Bonn, Germany"
    assert jobs[0].posted_at.isoformat() == "2026-05-01T00:00:00+00:00"
    assert jobs[0].closes_at.isoformat() == "2026-05-24T00:00:00+00:00"


def test_static_html_adapter_reports_cloudflare_block_page():
    adapter = StaticHTMLAdapter(
        AdapterContext(
            source=source("static_html", parser="public_links"),
            http=FakeTextHTTP(
                """
                <html>
                  <head><title>Attention Required! | Cloudflare</title></head>
                  <body><h1>Sorry, you have been blocked</h1></body>
                </html>
                """
            ),
        )
    )

    try:
        adapter.fetch_jobs()
    except RuntimeError as exc:
        assert "blocked by Cloudflare" in str(exc)
    else:
        raise AssertionError("Expected blocked static page to raise")


def test_static_html_adapter_accepts_explicit_empty_board():
    adapter = StaticHTMLAdapter(
        AdapterContext(
            source=source("static_html", parser="public_links"),
            http=FakeTextHTTP(
                """
                <html>
                  <body><p>There are no vacancies available at this time.</p></body>
                </html>
                """
            ),
        )
    )

    assert adapter.fetch_jobs() == []


def test_static_html_adapter_rejects_zero_links_without_empty_marker():
    adapter = StaticHTMLAdapter(
        AdapterContext(
            source=source("static_html", parser="public_links"),
            http=FakeTextHTTP("<html><body><h1>Work with us</h1></body></html>"),
        )
    )

    try:
        adapter.fetch_jobs()
    except RuntimeError as exc:
        assert "no job links found" in str(exc)
    else:
        raise AssertionError("Expected zero-link static page to raise")


def test_ipu_static_html_accepts_structural_empty_fixture():
    fixture = Path("tests/fixtures/ipu/current_empty_2026_05.html").read_text(encoding="utf-8")
    adapter = StaticHTMLAdapter(
        AdapterContext(
            source=_ipu_source(),
            http=FakeTextHTTP(fixture),
        )
    )

    assert adapter.fetch_jobs() == []
    assert adapter.run_diagnostics.health_status == "ok_empty"
    assert adapter.run_diagnostics.empty_reason == "verified_structural_empty"
    assert adapter.run_diagnostics.zero_fetched_evidence["job_nodes_found"] == 0


def test_ipu_static_html_parses_synthetic_positive_fixture():
    fixture = Path("tests/fixtures/ipu/synthetic_one_vacancy.html").read_text(encoding="utf-8")
    adapter = StaticHTMLAdapter(
        AdapterContext(
            source=_ipu_source(),
            http=FakeTextHTTP(fixture),
        )
    )

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].title == "Programme Officer"
    assert jobs[0].source_url == (
        "https://www.ipu.org/about-ipu/work-with-us/vacancies/programme-officer"
    )


def test_ipu_static_html_rejects_missing_structural_empty_markers():
    adapter = StaticHTMLAdapter(
        AdapterContext(
            source=_ipu_source(),
            http=FakeTextHTTP("<html><body><h1>Vacancies</h1></body></html>"),
        )
    )

    try:
        adapter.fetch_jobs()
    except RuntimeError as exc:
        assert "structural empty markers were not verified" in str(exc)
    else:
        raise AssertionError("Expected missing structural markers to raise")
    assert adapter.run_diagnostics.empty_reason == "parser_no_match"


def test_static_detail_parser_extracts_common_labels():
    org = source("static_html")

    job = parse_detail_page(
        org,
        """
        <html>
          <head><meta property="og:title" content="Project Officer - OSCE"></head>
          <body>
            <main>
              <span>Grade</span><span>P3</span>
              <span>Location</span><span>Vienna</span>
              <span>Closing Date</span><span>20 May 2026</span>
              <article>Coordinate project implementation and reporting.</article>
            </main>
          </body>
        </html>
        """,
        "https://vacancies.osce.org/jobs/project-officer-p3-4695",
    )

    assert job.external_id == "project-officer-p3-4695"
    assert job.title == "Project Officer"
    assert job.location == "Vienna"
    assert job.employment_type == "P3"
    assert job.closes_at.isoformat() == "2026-05-20T00:00:00+00:00"


def test_successfactors_legacy_parses_query_job_links():
    adapter = SuccessFactorsLegacyAdapter(
        AdapterContext(source=source("successfactors_legacy"), http=JobAggHTTPClient())
    )

    jobs = adapter.parse_jobs_from_html(
        """
        <a href="/career?company=ctbtoprepa&career_job_req_id=12345">
          Monitoring Officer
        </a>
        """
    )

    assert jobs[0].external_id == "12345"
    assert jobs[0].title == "Monitoring Officer"


def test_successfactors_legacy_xml_feed_parses_jobs():
    org = OrganizationSource(
        id="icc_successfactors_legacy",
        name="International Criminal Court",
        ats_family="successfactors_legacy",
        base_url="https://career5.successfactors.eu/career?company=1657261P",
        extra={
            "company_id": "1657261P",
            "locale": "en_GB",
            "primary_fetch_method": "successfactors_xml",
        },
    )
    adapter = SuccessFactorsLegacyAdapter(
        AdapterContext(
            source=org,
            http=FakeTextHTTP(
                """
                <source totalJobs="1">
                  <job>
                    <jobReqId>24421</jobReqId>
                    <title>Associate Legal Officer</title>
                    <location>The Hague</location>
                    <department>Registry</department>
                    <closingDate>31 May 2026</closingDate>
                    <description>Legal role.</description>
                  </job>
                </source>
                """
            ),
        )
    )

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].external_id == "24421"
    assert jobs[0].title == "Associate Legal Officer"
    assert jobs[0].location == "The Hague"
    assert jobs[0].source_url == (
        "https://career5.successfactors.eu/career?company=1657261P&career_ns=job_listing&"
        "career_job_req_id=24421&rcm_site_locale=en_GB&selected_lang=en_GB"
    )
    assert adapter.run_diagnostics.health_status == "ok"
    assert adapter.run_diagnostics.fetch_method == "successfactors_xml"


def test_successfactors_legacy_xml_feed_accepts_explicit_zero():
    org = OrganizationSource(
        id="icc_successfactors_legacy",
        name="International Criminal Court",
        ats_family="successfactors_legacy",
        base_url="https://career5.successfactors.eu/career?company=1657261P",
        extra={
            "company_id": "1657261P",
            "locale": "en_GB",
            "primary_fetch_method": "successfactors_xml",
        },
    )
    adapter = SuccessFactorsLegacyAdapter(
        AdapterContext(source=org, http=FakeTextHTTP("<source totalJobs=\"0\" />"))
    )

    assert adapter.fetch_jobs() == []
    assert adapter.run_diagnostics.health_status == "ok_empty"
    assert adapter.run_diagnostics.empty_reason == "verified_total_zero"


def test_successfactors_legacy_xml_feed_rejects_unverified_zero():
    org = OrganizationSource(
        id="icc_successfactors_legacy",
        name="International Criminal Court",
        ats_family="successfactors_legacy",
        base_url="https://career5.successfactors.eu/career?company=1657261P",
        extra={
            "company_id": "1657261P",
            "locale": "en_GB",
            "primary_fetch_method": "successfactors_xml",
        },
    )
    adapter = SuccessFactorsLegacyAdapter(
        AdapterContext(source=org, http=FakeTextHTTP("<source />"))
    )

    try:
        adapter.fetch_jobs()
    except RuntimeError as exc:
        assert "without verified zero evidence" in str(exc)
    else:
        raise AssertionError("Expected unverified zero XML feed to raise")


def _ipu_source():
    return OrganizationSource(
        id="ipu_static_html",
        name="Inter-Parliamentary Union",
        ats_family="ipu_static_html",
        adapter="static_html",
        base_url="https://www.ipu.org/about-ipu/work-with-us/vacancies",
        extra={
            "listing_url": "https://www.ipu.org/about-ipu/work-with-us/vacancies",
            "parser": "public_links",
            "job_link_selector_hint": "/work-with-us/vacancies/",
            "fetch_details": False,
            "empty_policy": {
                "mode": "verified_structural_empty",
                "required_page_markers": [
                    "Vacancies",
                    "Vacancies list",
                    "IPU job application",
                    "Roster",
                ],
                "section_start_text": "Vacancies list",
                "section_end_any_text": ["IPU job application", "Roster"],
                "job_link_patterns": [
                    "/about-ipu/work-with-us/vacancies/",
                    "/vacancies/",
                ],
                "ignore_link_text": ["IPU job application", "Roster"],
            },
        },
    )
