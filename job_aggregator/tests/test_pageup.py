from jobagg.adapters.base import AdapterContext
from jobagg.adapters.pageup import PageUpAdapter
from jobagg.http import JobAggHTTPClient
from jobagg.models import OrganizationSource


def test_pageup_parses_search_payload():
    source = OrganizationSource(
        id="pageup_org",
        name="PageUp Org",
        ats_family="pageup",
        base_url="https://example.pageuppeople.com",
    )
    adapter = PageUpAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))

    jobs = adapter.parse_jobs(
        {
            "jobs": [
                {
                    "jobId": "PU-777",
                    "title": "Human Resources Analyst",
                    "location": "Remote",
                    "workType": "Fixed term",
                    "closingDate": "2026-06-30T23:59:00+00:00",
                    "url": "/jobs/human-resources-analyst",
                }
            ]
        }
    )

    assert len(jobs) == 1
    assert jobs[0].external_id == "PU-777"
    assert jobs[0].employment_type == "Fixed term"
    assert jobs[0].apply_url == "https://example.pageuppeople.com/jobs/human-resources-analyst"


def test_pageup_parses_unicef_listing_html():
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing/",
    )
    adapter = PageUpAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))

    jobs = adapter.parse_jobs(
        {
            "results": """
              <div class="list-view--item">
                <a class="job-link" href="/en-us/job/593036/example-role-593036">
                  Development and Delivery Consultancy #593036
                </a>
                <div class="row--teaser">
                  <p></p>
                  <p>The assignment aims to design and deliver training.</p>
                  <p><b>Location:</b> <span class="location">Lesotho</span></p>
                  <p><b>Deadline:</b>
                    <span class="close-date">
                      <time datetime="2026-05-27T21:55:00Z">27 May 2026</time>
                    </span>
                  </p>
                </div>
              </div>
            """
        }
    )

    assert len(jobs) == 1
    assert jobs[0].external_id == "593036"
    assert jobs[0].title == "Development and Delivery Consultancy #593036"
    assert jobs[0].location == "Lesotho"
    assert jobs[0].closes_at.isoformat() == "2026-05-27T21:55:00+00:00"


def test_pageup_parses_unicef_detail_html():
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing/",
    )
    adapter = PageUpAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))

    job = adapter.parse_detail_html(
        """
        <h2>Technical Manager, P-4, Nairobi #00131731</h2>
        <p>
          <a class="apply-link button"
             href="https://secure.dc7.pageuppeople.com/apply/671/gateway/default.aspx?lJobID=589086">
            Apply now
          </a>
          <b>Job no:</b> <span class="job-externalJobNo">589086</span><br>
          <b>Contract type:</b> <span class="work-type fixed-term-appointment">
            Fixed Term Appointment
          </span><br>
          <b>Location:</b> <span class="location">Kenya</span><br>
          <b>Categories:</b> <span class="categories">Programme Management</span><br>
        </p>
        <div id="job-details"><p>Full role text.</p></div>
        """,
        "https://jobs.unicef.org/en-us/job/589086/example",
    )

    assert job.external_id == "589086"
    assert job.title == "Technical Manager, P-4, Nairobi #00131731"
    assert job.location == "Kenya"
    assert job.department == "Programme Management"
    assert job.employment_type == "Fixed Term Appointment"
    assert job.apply_url.startswith("https://secure.dc7.pageuppeople.com/apply/671/")


def test_pageup_fetches_paginated_filter_results():
    class Response:
        def __init__(self, text):
            self.text = text

    class FakeHTTP:
        def __init__(self):
            self.urls = []

        def post_form(self, url, payload=None, *, headers=None):
            self.urls.append(url)
            page = 2 if "page=2" in url else 1
            job_id = 593000 + page
            return Response(
                f"""{{
                  "results": "<div class=\\"list-view--item\\"><a class=\\"job-link\\" href=\\"/en-us/job/{job_id}/role-{job_id}\\">Role {page} #{job_id}</a><span class=\\"location\\">Remote</span><time datetime=\\"2026-06-0{page}T00:00:00Z\\">date</time></div>",
                  "page": {page},
                  "pageitems": 1,
                  "count": 2
                }}"""
            )

    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing/",
        extra={
            "filter_url": "https://jobs.unicef.org/en-us/filter/",
            "page_size": 1,
            "max_pages": 5,
            "query": {"search-keyword": ""},
        },
    )
    http = FakeHTTP()
    adapter = PageUpAdapter(AdapterContext(source=source, http=http))

    jobs = adapter.fetch_jobs()

    assert [job.external_id for job in jobs] == ["593001", "593002"]
    assert "page=1" in http.urls[0]
    assert "page=2" in http.urls[1]
