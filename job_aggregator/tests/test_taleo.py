from jobagg.adapters.base import AdapterContext
from jobagg.adapters.taleo import TaleoAdapter
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource


def test_taleo_parses_legacy_job_links():
    source = OrganizationSource(
        id="legacy_taleo",
        name="Legacy Taleo",
        ats_family="taleo",
        base_url="https://example.taleo.net/careersection/external",
    )
    adapter = TaleoAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))

    jobs = adapter.parse_jobs_from_html(
        """
        <html>
          <a href="/careersection/external/jobdetail.ftl?job=12345">
            Monitoring and Evaluation Specialist
          </a>
        </html>
        """
    )

    assert len(jobs) == 1
    assert jobs[0].external_id == "12345"
    assert jobs[0].title == "Monitoring and Evaluation Specialist"
    assert jobs[0].apply_url.endswith("/careersection/external/jobdetail.ftl?job=12345")


def test_taleo_parses_rest_requisition_columns():
    source = OrganizationSource(
        id="who_taleo",
        name="WHO",
        ats_family="taleo",
        base_url="https://careers.who.int/careersection/ex/jobsearch.ftl",
        extra={
            "detail_url_template": (
                "https://careers.who.int/careersection/ex/jobdetail.ftl?job={job_id_url}"
            )
        },
    )
    adapter = TaleoAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))

    jobs = adapter.parse_jobs(
        {
            "requisitionList": [
                {
                    "contestNo": "2601506",
                    "column": [
                        {"name": "Requisition Title", "value": "International Consultant"},
                        {"name": "Location", "value": "Geneva, Switzerland"},
                        {"name": "Closing Date", "value": "2026-06-01"},
                    ],
                }
            ],
            "pagingData": {"pageNo": 1, "numberOfPages": 1},
        }
    )

    assert len(jobs) == 1
    assert jobs[0].external_id == "2601506"
    assert jobs[0].title == "International Consultant"
    assert jobs[0].location == "Geneva, Switzerland"
    assert jobs[0].closes_at.isoformat() == "2026-06-01T00:00:00+00:00"
    assert jobs[0].apply_url == "https://careers.who.int/careersection/ex/jobdetail.ftl?job=2601506"


def test_taleo_parses_compact_rest_column_array():
    source = OrganizationSource(
        id="fao_taleo",
        name="FAO",
        ats_family="taleo",
        base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
        extra={
            "detail_url_template": (
                "https://jobs.fao.org/careersection/fao_external/jobdetail.ftl?job={job_id_url}"
            ),
            "column_fields": [
                "title",
                "external_id",
                "job_category",
                "employment_type",
                "location",
                "postedDate",
                "closingDate",
            ],
        },
    )
    adapter = TaleoAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))

    jobs = adapter.parse_jobs(
        {
            "requisitionList": [
                {
                    "contestNo": "2600823",
                    "jobId": "3490611",
                    "linkedColumn": 0,
                    "locationsColumns": [4],
                    "column": [
                        "National Field Specialist",
                        "2600823",
                        "Non-staff opportunities",
                        "NPP (National Project Personnel)",
                        "[\"Various Locations\"]",
                        "14/May/2026",
                        "29/May/2026, 12:59:00 AM",
                    ],
                }
            ]
        }
    )

    assert len(jobs) == 1
    assert jobs[0].title == "National Field Specialist"
    assert jobs[0].external_id == "2600823"
    assert jobs[0].location == "Various Locations"
    assert jobs[0].employment_type == "NPP (National Project Personnel)"
    assert jobs[0].posted_at.isoformat() == "2026-05-14T00:00:00+00:00"
    assert jobs[0].closes_at.isoformat() == "2026-05-29T00:59:00+00:00"


def test_taleo_parses_adb_detail_fill_list_payload():
    source = OrganizationSource(
        id="adb_taleo",
        name="ADB",
        ats_family="taleo",
        base_url="https://adb.taleo.net/careersection/1/jobsearch.ftl",
    )
    adapter = TaleoAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))

    job = adapter.parse_detail_html(
        """
        <html><head><meta property="og:title" content="Home" /></head>
        <body>
        <script>
        api.fillList('requisitionDescriptionInterface', 'descRequisition', [
          '83990','true','83990','false',
          'Submission for the position: Senior Social Protection Specialist - (Job Number: 260497)',
          'false','83990','false','true',
          'Senior Social Protection Specialist ','260497','','','','',
          '!*!!*!!*!%3Cp%3EFull ADB role description for social protection.%3C/p%3E',
          'Asian Development Bank-India Resident Mission-India-New Delhi',
          'Asian Development Bank-India Resident Mission-India-New Delhi',
          'Sectors Department 3','Sectors Department 3',
          'Human and Social Development Sector Office',
          'Human and Social Development Sector Office',
          '','','Technical International (Field Office)',
          'Technical International (Field Office)','TI2','TI2',
          '06-May-2026, 7:56:15 AM','06-May-2026, 7:56:15 AM',
          'Ongoing','03-Jun-2026, 11:59:00 PM',
          'false','83990','83990','true'
        ]);
        </script>
        </body></html>
        """,
        "https://adb.taleo.net/careersection/1/jobdetail.ftl?job=260497",
    )

    flat = job.raw["_taleo_flat"]
    assert job.external_id == "260497"
    assert job.title == "Senior Social Protection Specialist"
    assert job.location == "Asian Development Bank-India Resident Mission-India-New Delhi"
    assert job.department == "Sectors Department 3"
    assert job.employment_type == "Technical International (Field Office)"
    assert job.description == "Full ADB role description for social protection."
    assert job.closes_at.isoformat() == "2026-06-03T23:59:00+00:00"
    assert flat["JOB_LEVEL"] == "TI2"
    assert flat["Position Level"] == "TI2"


def test_taleo_rest_fetches_configured_search_pages():
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
            page_no = payload["pageNo"]
            return Response(
                {
                    "requisitionList": [
                        {
                            "contestNo": f"JOB-{page_no}",
                            "title": f"Role {page_no}",
                        }
                    ],
                    "pagingData": {"pageNo": page_no, "numberOfPages": 2},
                }
            )

    source = OrganizationSource(
        id="fao_taleo",
        name="FAO",
        ats_family="taleo",
        base_url="https://jobs.fao.org/careersection/fao_external/jobsearch.ftl",
        extra={
            "search_api_url": (
                "https://jobs.fao.org/careersection/rest/jobboard/searchjobs?lang=en&portal=1"
            ),
            "search_payload": {"fieldData": {"fields": {}, "valid": True}, "pageNo": 1},
            "max_pages": 3,
            "warmup_search_page": False,
        },
    )
    http = FakeHTTP()
    adapter = TaleoAdapter(AdapterContext(source=source, http=http))

    jobs = adapter.fetch_jobs()

    assert [job.external_id for job in jobs] == ["JOB-1", "JOB-2"]
    assert [request[1]["pageNo"] for request in http.requests] == [1, 2]
    assert http.requests[0][2]["Referer"] == source.base_url
