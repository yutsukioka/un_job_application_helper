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


def test_pageup_parses_full_unicef_template_detail_without_page_heading():
    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing/",
    )
    adapter = PageUpAdapter(AdapterContext(source=source, http=JobAggHTTPClient()))

    job = adapter.parse_detail_html(
        """
        <h2>Vacancies</h2>
        <div id="job">
          <h2>Youth Digital Mobilizer, Rome, Italy, National Response Italy, 6.5 months, remote work</h2>
          <p>
            <a class="apply-link button" href="https://secure.dc7.pageuppeople.com/apply/671/gateway/default.aspx?lJobID=593348">Apply now</a>
            <b>Job no:</b> <span class="job-externalJobNo">593348</span><br>
            <b>Contract type:</b> <span class="work-type consultant">Consultant</span><br>
            <b>Duty Station:</b> Rome<br>
            <b>Level:</b> Consultancy<br>
            <b>Location:</b> <span class="location">Italy</span><br>
            <b>Categories:</b> <span class="categories">Adolescent Development</span><br>
          </p>
          <div id="job-details"><p>Full UNICEF consultancy terms of reference.</p></div>
          <p><b>Advertised:</b> 02 Jun 2026<br><b>Deadline:</b> <span class="close-date"><time datetime="2026-06-09T21:55:00Z">09 Jun 2026</time></span></p>
        </div>
        <div id="search-results"><h2>Vacancies</h2></div>
        """,
        "https://jobs.unicef.org/en-us/job/593348/youth-digital-mobilizer-rome-italy-national-response-italy-65-months-remote-work",
    )

    assert job.external_id == "593348"
    assert job.title == "Youth Digital Mobilizer, Rome, Italy, National Response Italy, 6.5 months, remote work"
    assert job.employment_type == "Consultant"
    assert job.location == "Italy"
    assert job.department == "Adolescent Development"
    assert job.description == "Full UNICEF consultancy terms of reference."
    assert "Vacancies" not in job.description


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


def test_pageup_fetches_detail_html_with_get_for_listing_url():
    class Response:
        def __init__(self, text):
            self.text = text

    class FakeHTTP:
        def __init__(self):
            self.post_urls = []
            self.get_urls = []

        def post_form(self, url, payload=None, *, headers=None):
            self.post_urls.append(url)
            return Response(
                """{
                  "results": "<div class=\\"list-view--item\\"><a class=\\"job-link\\" href=\\"/en-us/job/589086/example\\">Listing Role #589086</a><span class=\\"location\\">Kenya</span></div>",
                  "page": 1,
                  "pageitems": 1,
                  "count": 1
                }"""
            )

        def get(self, url, *, headers=None, timeout_seconds=None):
            self.get_urls.append(url)
            return Response(
                """
                <h2>Technical Manager, P-4, Nairobi #589086</h2>
                <p>
                  <b>Job no:</b> <span class="job-externalJobNo">589086</span><br>
                  <b>Contract type:</b> <span class="work-type">Fixed Term Appointment</span><br>
                  <b>Location:</b> <span class="location">Kenya</span><br>
                </p>
                <div id="job-details"><p>Full role text.</p></div>
                """
            )

    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing/",
        extra={
            "filter_url": "https://jobs.unicef.org/en-us/filter/",
            "fetch_details": True,
            "page_size": 1,
            "max_pages": 1,
        },
    )
    http = FakeHTTP()
    adapter = PageUpAdapter(AdapterContext(source=source, http=http))

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 1
    assert http.get_urls == ["https://jobs.unicef.org/en-us/job/589086/example"]
    assert jobs[0].title == "Technical Manager, P-4, Nairobi #589086"
    assert jobs[0].employment_type == "Fixed Term Appointment"
    assert jobs[0].description == "Technical Manager, P-4, Nairobi #589086 Job no: 589086 Contract type: Fixed Term Appointment Location: Kenya Full role text."


def test_pageup_retries_empty_ajax_detail_as_public_html():
    class Response:
        def __init__(self, text):
            self.text = text

    class FakeHTTP:
        user_agent = "test"
        timeout_seconds = 30
        max_retries = 0
        backoff_base_seconds = 0
        jitter_ratio = 0
        max_response_bytes = 1024 * 1024

        def __init__(self):
            self.headers = []

        def get(self, url, *, headers=None, timeout_seconds=None):
            self.headers.append(headers or {})
            if len(self.headers) == 1:
                return Response('{"results": ""}')
            return Response(
                """
                <h2>Programme Officer #593218</h2>
                <p><b>Job no:</b> <span class="job-externalJobNo">593218</span></p>
                <div id="job-details">Full UNICEF detail text.</div>
                """
            )

    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing/",
        extra={"listing_url": "https://jobs.unicef.org/en-us/listing/"},
    )
    adapter = PageUpAdapter(AdapterContext(source=source, http=FakeHTTP()))

    job = adapter.fetch_detail_for_listing_item(
        {
            "_pageup_detail_url": "https://jobs.unicef.org/en-us/job/593218/example",
        }
    )

    assert job is not None
    assert job.external_id == "593218"
    assert "Full UNICEF detail text" in (job.description or "")
    assert adapter.context.http.headers[1]["Accept"].startswith("text/html")


def test_pageup_ignores_aws_waf_challenge_detail_response():
    class Response:
        def __init__(self, text):
            self.text = text

    class FakeHTTP:
        def get(self, url, *, headers=None, timeout_seconds=None):
            return Response(
                """
                <!DOCTYPE html>
                <script>
                window.awsWafCookieDomainList = ['clinchtalent.com'];
                </script>
                <noscript>In order to continue, we need to verify that you're not a robot.</noscript>
                """
            )

    source = OrganizationSource(
        id="unicef_pageup",
        name="UNICEF",
        ats_family="pageup",
        base_url="https://jobs.unicef.org/en-us/listing/",
    )
    adapter = PageUpAdapter(AdapterContext(source=source, http=FakeHTTP()))

    job = adapter.fetch_detail_for_listing_item(
        {
            "_pageup_detail_url": "https://jobs.unicef.org/en-us/job/593607/example",
        }
    )

    assert job is None
