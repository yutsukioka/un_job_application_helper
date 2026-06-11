from jobagg.adapters.base import AdapterContext
from jobagg.adapters.workday import WorkdayAdapter
from jobagg.hashing import ensure_job_hash
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeHTTPClient:
    def __init__(self, *, pages=None, details=None):
        self.pages = pages or {}
        self.details = details or {}
        self.post_calls = []
        self.get_calls = []

    def post_json(self, url, payload):
        self.post_calls.append((url, payload))
        return FakeResponse(self.pages[payload["offset"]])

    def get(self, url, *, headers=None):
        self.get_calls.append((url, headers or {}))
        return FakeResponse(self.details[url])


def wfp_source(**extra):
    data = {
        "cxs_base_url": "https://wd3.myworkdaysite.com/wday/cxs/wfp/job_openings",
        **extra,
    }
    return OrganizationSource(
        id="wfp_workday",
        name="World Food Programme",
        ats_family="workday",
        base_url="https://wd3.myworkdaysite.com/recruiting/wfp/job_openings",
        extra=data,
    )


def test_workday_parses_cxs_payload():
    source = OrganizationSource(
        id="un_example",
        name="UN Example",
        ats_family="workday",
        base_url="https://example.wd1.myworkdayjobs.com",
        extra={},
    )
    adapter = WorkdayAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))
    jobs = adapter.parse_jobs(
        {
            "jobPostings": [
                {
                    "title": "Programme Officer",
                    "externalPath": "programme-officer_JR100",
                    "locationsText": "Nairobi, Kenya",
                    "timeType": "Full time",
                    "postedOn": "2026-05-01T00:00:00Z",
                }
            ]
        }
    )

    assert len(jobs) == 1
    assert jobs[0].title == "Programme Officer"
    assert jobs[0].external_id == "programme-officer_JR100"
    assert jobs[0].location == "Nairobi, Kenya"
    assert jobs[0].apply_url == "https://example.wd1.myworkdayjobs.com/job/programme-officer_JR100"
    assert ensure_job_hash(jobs[0]).normalized_hash


def test_workday_parses_wfp_listing_payload():
    adapter = WorkdayAdapter(AdapterContext(source=wfp_source(), http=JobAggHTTPClient()))

    jobs = adapter.parse_jobs(
        {
            "total": 130,
            "jobPostings": [
                {
                    "title": "Asociado/a de Alianzas",
                    "externalPath": "/job/San-Salvador-El-Salvador-The-Republic/Asociado-a-de-Alianzas_JR123076",
                    "locationsText": "San Salvador, El Salvador, The Republic",
                    "postedOn": "Posted Today",
                    "bulletFields": ["JR123076"],
                }
            ],
            "facets": [
                {
                    "facetParameter": "jobFamily",
                    "descriptor": "Job Family",
                    "values": [],
                }
            ],
            "userAuthenticated": False,
        }
    )

    assert len(jobs) == 1
    assert jobs[0].external_id == "JR123076"
    assert jobs[0].title == "Asociado/a de Alianzas"
    assert jobs[0].location == "San Salvador, El Salvador, The Republic"
    assert jobs[0].apply_url == (
        "https://wd3.myworkdaysite.com/recruiting/wfp/job_openings"
        "/job/San-Salvador-El-Salvador-The-Republic/Asociado-a-de-Alianzas_JR123076"
    )


def test_workday_marks_source_reported_zero_as_verified_empty():
    http = FakeHTTPClient(pages={0: {"total": 0, "jobPostings": []}})
    adapter = WorkdayAdapter(AdapterContext(source=wfp_source(), http=http))

    assert adapter.fetch_jobs() == []
    assert adapter.run_diagnostics.health_status == "ok_empty"
    assert adapter.run_diagnostics.empty_reason == "verified_total_zero"
    assert adapter.run_diagnostics.zero_fetched_evidence == {"total_reported_by_source": 0}


def test_workday_parses_wfp_detail_payload():
    adapter = WorkdayAdapter(AdapterContext(source=wfp_source(), http=JobAggHTTPClient()))

    jobs = adapter.parse_jobs(
        {
            "jobPostingInfo": {
                "id": "291cdca1fa2b1000feba61fe773f0000",
                "title": "Human Resources Officer - Performance Management (P4)",
                "jobDescription": "<p>Lead performance management work.</p>",
                "location": "Rome, Italy",
                "postedOn": "Posted 15 Days Ago",
                "startDate": "2026-04-28",
                "timeType": "Full time",
                "jobReqId": "JR122560",
                "jobPostingId": "Human-Resources-Officer---Performance-Management--P4-_JR122560",
                "country": {"descriptor": "Italy", "id": "8cd04a563fd94da7b06857a79faaf815"},
                "canApply": True,
                "posted": True,
                "externalUrl": (
                    "https://wd3.myworkdaysite.com/recruiting/wfp/job_openings/job/"
                    "Rome-Italy/Human-Resources-Officer---Performance-Management--P4-_JR122560"
                ),
                "endDate": "2026-05-18",
            },
            "hiringOrganization": {"name": "World Food Programme"},
            "similarJobs": [],
            "userAuthenticated": False,
        }
    )

    assert len(jobs) == 1
    assert jobs[0].external_id == "JR122560"
    assert jobs[0].title == "Human Resources Officer - Performance Management (P4)"
    assert jobs[0].employment_type == "Full time"
    assert jobs[0].posted_at.isoformat() == "2026-04-28T00:00:00+00:00"
    assert jobs[0].closes_at.isoformat() == "2026-05-18T00:00:00+00:00"
    assert jobs[0].description == "Lead performance management work."


def test_workday_detail_url_normalizes_external_path_forms():
    adapter = WorkdayAdapter(AdapterContext(source=wfp_source(), http=JobAggHTTPClient()))
    cxs_base = adapter.source.extra["cxs_base_url"]

    assert adapter._detail_url({"externalPath": "Senior_Economist_JR123"}) == (
        f"{cxs_base}/job/Senior_Economist_JR123"
    )
    assert adapter._detail_url({"externalPath": "/job/WFP_External/Senior_Economist_JR123"}) == (
        f"{cxs_base}/job/WFP_External/Senior_Economist_JR123"
    )
    assert adapter._detail_url({"externalPath": "job/WFP_External/Senior_Economist_JR123"}) == (
        f"{cxs_base}/job/WFP_External/Senior_Economist_JR123"
    )


def test_workday_fetches_paginated_cxs_jobs_with_detail_enrichment():
    source = wfp_source(page_size=2, max_pages=5, fetch_details=True)
    cxs_base = source.extra["cxs_base_url"]
    detail_url_1 = f"{cxs_base}/job/Rome-Italy/Finance---Innovation-Capital-Expert--P4-_JR122345"
    detail_url_2 = f"{cxs_base}/job/Rome-Italy/Human-Resources-Officer---Performance-Management--P4-_JR122560"
    detail_url_3 = f"{cxs_base}/job/Nairobi-Kenya/Budget-and-Programming-Associate--G6_JR123042-1"
    http = FakeHTTPClient(
        pages={
            0: {
                "total": 3,
                "jobPostings": [
                    {
                        "title": "Finance & Innovation Capital Expert (P4)",
                        "externalPath": "/job/Rome-Italy/Finance---Innovation-Capital-Expert--P4-_JR122345",
                        "locationsText": "Rome, Italy",
                        "postedOn": "Posted 2 Days Ago",
                        "bulletFields": ["JR122345"],
                    },
                    {
                        "title": "Human Resources Officer - Performance Management (P4)",
                        "externalPath": "/job/Rome-Italy/Human-Resources-Officer---Performance-Management--P4-_JR122560",
                        "locationsText": "Rome, Italy",
                        "postedOn": "Posted 15 Days Ago",
                        "bulletFields": ["JR122560"],
                    },
                ],
            },
            2: {
                # Workday can report total=0 on later pages while still returning
                # jobs, so the adapter must preserve the first valid total.
                "total": 0,
                "jobPostings": [
                    {
                        "title": "Budget and Programming Associate, G6",
                        "externalPath": "/job/Nairobi-Kenya/Budget-and-Programming-Associate--G6_JR123042-1",
                        "locationsText": "Nairobi, Kenya",
                        "postedOn": "Posted 4 Days Ago",
                        "bulletFields": ["JR123042"],
                    }
                ],
            },
        },
        details={
            detail_url_1: {
                "jobPostingInfo": {
                    "title": "Finance & Innovation Capital Expert (P4)",
                    "jobReqId": "JR122345",
                    "jobPostingId": "Finance---Innovation-Capital-Expert--P4-_JR122345",
                    "location": "Rome, Italy",
                    "startDate": "2026-05-12",
                    "endDate": "2026-05-30",
                    "timeType": "Full time",
                    "jobDescription": "Finance role description",
                    "posted": True,
                    "externalUrl": (
                        "https://wd3.myworkdaysite.com/recruiting/wfp/job_openings/job/"
                        "Rome-Italy/Finance---Innovation-Capital-Expert--P4-_JR122345"
                    ),
                }
            },
            detail_url_2: {
                "jobPostingInfo": {
                    "title": "Human Resources Officer - Performance Management (P4)",
                    "jobReqId": "JR122560",
                    "jobPostingId": "Human-Resources-Officer---Performance-Management--P4-_JR122560",
                    "location": "Rome, Italy",
                    "startDate": "2026-04-28",
                    "endDate": "2026-05-18",
                    "timeType": "Full time",
                    "jobDescription": "HR role description",
                    "posted": True,
                    "externalUrl": (
                        "https://wd3.myworkdaysite.com/recruiting/wfp/job_openings/job/"
                        "Rome-Italy/Human-Resources-Officer---Performance-Management--P4-_JR122560"
                    ),
                }
            },
            detail_url_3: {
                "jobPostingInfo": {
                    "title": "Budget and Programming Associate, G6",
                    "jobReqId": "JR123042",
                    "jobPostingId": "Budget-and-Programming-Associate--G6_JR123042-1",
                    "location": "Nairobi, Kenya",
                    "startDate": "2026-05-10",
                    "endDate": "2026-06-01",
                    "timeType": "Fixed term",
                    "jobDescription": "Budget role description",
                    "posted": True,
                    "externalUrl": (
                        "https://wd3.myworkdaysite.com/recruiting/wfp/job_openings/job/"
                        "Nairobi-Kenya/Budget-and-Programming-Associate--G6_JR123042-1"
                    ),
                }
            },
        },
    )
    adapter = WorkdayAdapter(AdapterContext(source=source, http=http))

    jobs = adapter.fetch_jobs()

    assert [call[1]["offset"] for call in http.post_calls] == [0, 2]
    assert len(http.get_calls) == 3
    assert len(jobs) == 3
    assert {job.external_id for job in jobs} == {"JR122345", "JR122560", "JR123042"}
    assert jobs[0].description == "Finance role description"
    assert jobs[2].employment_type == "Fixed term"
