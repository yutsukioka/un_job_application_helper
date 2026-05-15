from jobagg.adapters.base import AdapterContext
from jobagg.adapters.inspira import InspiraAdapter
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource


def inspira_source(**extra):
    return OrganizationSource(
        id="un_inspira",
        name="United Nations Careers",
        ats_family="inspira",
        base_url="https://careers.un.org/jobopening",
        extra={
            "list_url": "https://careers.un.org/api/public/opening/jo/list/filteredV2/en",
            "detail_api_url_template": "https://careers.un.org/api/public/opening/joV2/{job_id}/en",
            "detail_url_template": "https://careers.un.org/jobSearchDescription/{job_id}?language=en",
            **extra,
        },
    )


def test_inspira_parses_filtered_v2_listing_payload():
    adapter = InspiraAdapter(AdapterContext(source=inspira_source(), http=JobAggHTTPClient()))

    jobs = adapter.parse_jobs(
        {
            "status": 1,
            "data": {
                "list": [
                    {
                        "jobId": 276337,
                        "categoryCode": "PD",
                        "jobTitle": "PROGRAMME MANAGEMENT OFFICER",
                        "postingTitle": "PROGRAMME MANAGEMENT OFFICER, P3",
                        "jobDescription": "<div><b>Responsibilities</b> Manage programmes.</div>",
                        "jobLevel": "P-3",
                        "dutyStation": [{"description": "INCHEON CITY"}],
                        "startDate": "2026-05-08T04:00:00.000Z",
                        "endDate": "2026-06-07T03:59:59.000Z",
                        "jc": {"name": "Professional and Higher Categories"},
                        "jl": {"name": "P-3"},
                        "jf": {"Name": "Programme Management"},
                        "dept": {"name": "United Nations Office for Disaster Risk Reduction"},
                    }
                ],
                "count": 1,
            },
        }
    )

    assert len(jobs) == 1
    assert jobs[0].external_id == "276337"
    assert jobs[0].title == "PROGRAMME MANAGEMENT OFFICER, P3"
    assert jobs[0].location == "INCHEON CITY"
    assert jobs[0].department == "United Nations Office for Disaster Risk Reduction"
    assert jobs[0].employment_type == (
        "Professional and Higher Categories / P-3 / Programme Management"
    )
    assert jobs[0].posted_at.isoformat() == "2026-05-08T04:00:00+00:00"
    assert jobs[0].closes_at.isoformat() == "2026-06-07T03:59:59+00:00"
    assert jobs[0].source_url == "https://careers.un.org/jobSearchDescription/276337?language=en"


def test_inspira_fetches_paginated_filtered_v2_jobs():
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class FakeHTTP:
        def __init__(self):
            self.requests = []

        def post_json(self, url, payload, *, headers=None):
            self.requests.append((url, payload, headers))
            page = payload["pagination"]["page"]
            job_id = 277000 + page
            return Response(
                {
                    "status": 1,
                    "data": {
                        "list": [
                            {
                                "jobId": job_id,
                                "postingTitle": f"Role {page}",
                                "endDate": "2026-06-30T03:59:59.000Z",
                            }
                        ],
                        "count": 2,
                    },
                }
            )

    source = inspira_source(page_size=1, max_pages=5, filter_config={"jc": ["PD"]})
    http = FakeHTTP()
    adapter = InspiraAdapter(AdapterContext(source=source, http=http))

    jobs = adapter.fetch_jobs()

    assert [job.external_id for job in jobs] == ["277000", "277001"]
    assert [request[1]["pagination"]["page"] for request in http.requests] == [0, 1]
    assert http.requests[0][1]["filterConfig"] == {"jc": ["PD"]}
    assert http.requests[0][2]["Referer"] == "https://careers.un.org/jobopening"


def test_inspira_fetches_detail_apply_url_when_requested():
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class FakeHTTP:
        def __init__(self):
            self.detail_urls = []

        def get(self, url, *, headers=None):
            self.detail_urls.append(url)
            return Response(
                {
                    "status": 1,
                    "data": {
                        "jobId": 276337,
                        "postingTitle": "PROGRAMME MANAGEMENT OFFICER, P3",
                        "inspiraURL": (
                            "https://inspira.un.org/psp/PUNA1J/EMPLOYEE/HRMS/c/"
                            "UN_CUSTOMIZATIONS.UN_JOB_DETAIL.GBL?JobOpeningId=276337"
                        ),
                        "endDate": "2026-06-07T03:59:59.000Z",
                    },
                }
            )

    adapter = InspiraAdapter(AdapterContext(source=inspira_source(), http=FakeHTTP()))
    listing_job = adapter.parse_listing_item({"jobId": 276337, "postingTitle": "Role"})

    detail_job = adapter.fetch_detail_for_listing_item(listing_job.raw)

    assert detail_job is not None
    assert detail_job.apply_url.startswith("https://inspira.un.org/psp/PUNA1J/")
