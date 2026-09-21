import json
from pathlib import Path
import time

import pytest

from jobagg.browser_fetch import BrowserContractError, GuardedBrowser
from jobagg.http import HttpResponse, JobAggHTTPClient
from jobagg.http_safe import SafeHTTPPolicy
from jobagg.pipelines.http_checkpoint import DurableCapture
from jobagg.robots import load_policy


class Replies(JobAggHTTPClient):
    def __init__(self, replies):
        super().__init__(
            max_retries=0,
            safe_policy=SafeHTTPPolicy({"jobs.example.test"}, resolver=lambda host: ["8.8.8.8"]),
        )
        self.replies, self.calls = replies, []

    def _request(self, url, **kwargs):
        self.calls.append(url)
        value = self.replies[url]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, bytes):
            return HttpResponse(url, 200, {"Content-Type": "application/pdf"}, "", value)
        media = "application/json" if isinstance(value, dict) else "text/html"
        body = json.dumps(value) if isinstance(value, dict) else value
        return HttpResponse(url, 200, {"Content-Type": media}, body, body.encode())


def make_browser(tmp_path, monkeypatch, replies, selector="main.ready"):
    # The actual browser has no network fallback. Fixture transports only.
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    policy = tmp_path / "robots.yaml"
    policy.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    client = Replies(replies)
    capture = DurableCapture(
        client,
        load_policy(policy),
        tmp_path / "capture",
        {},
        lock_root=tmp_path / "locks",
        max_requests=20,
        default_header_origin="https://jobs.example.test",
        phase={"kind": "detail", "job_id": "101"},
        deadline_at=time.time() + 30,
    )
    browser = GuardedBrowser(
        client,
        capture,
        {
            "url_patterns": [r"^https://jobs\.example\.test/.*$"],
            "ready_selector": selector,
            "content_selector": "main",
            "timeout_seconds": 20,
        },
    )
    return browser, client


def test_real_chromium_renders_script_and_captures_full_text_and_attachment_links(
    tmp_path, monkeypatch
):
    pytest.importorskip("playwright.sync_api")
    public = "All responsibilities, requirements and conditions are retained. " * 40
    url = "https://jobs.example.test/jobs/101"
    browser, client = make_browser(
        tmp_path,
        monkeypatch,
        {
            url: """<html><body><main></main><script>
        fetch('/api/101').then(r=>r.json()).then(j=>{
          document.querySelector('main').innerHTML='<h1>Research Analyst</h1><p>'+j.text+'</p>'
            +'<a href="/docs/101.pdf">Terms of Reference</a>';
          document.querySelector('main').className='ready';
        });</script></body></html>""",
            "https://jobs.example.test/api/101": {"text": public},
        },
    )
    response = browser.render(url)
    assert public in response.text
    assert client.calls == [url, "https://jobs.example.test/api/101"]
    receipt = json.loads(browser.last_receipt.read_text())
    assert public.strip() in Path(receipt["text_path"]).read_text()
    assert receipt["links"] == [
        {"url": "https://jobs.example.test/docs/101.pdf", "label": "Terms of Reference"}
    ]
    assert receipt["complete"] is False and receipt["llm_calls"] == 0
    captures = [json.loads(p.read_text()) for p in (browser.capture.target / "http").glob("*.json")]
    assert len(captures) == 2
    assert all(r["body_captured"] and r["phase"]["job_id"] == "101" for r in captures)


def test_real_browser_never_dispatches_private_or_unreviewed_host(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    url = "https://jobs.example.test/jobs/101"
    browser, client = make_browser(
        tmp_path,
        monkeypatch,
        {
            url: '<main></main><script>fetch("https://127.0.0.1/admin");</script>',
        },
    )
    with pytest.raises(ValueError, match="allowlist"):
        browser.render(url)
    assert client.calls == [url]
    assert browser.last_receipt is None


def test_api_requests_stay_direct_and_do_not_start_a_browser(tmp_path, monkeypatch):
    url = "https://jobs.example.test/api"
    browser, client = make_browser(tmp_path, monkeypatch, {url: {"total": 0}})
    browser.patterns = []
    assert browser.request(url, method="GET").json() == {"total": 0}
    assert client.calls == [url]


def test_requires_explicit_contract(tmp_path, monkeypatch):
    browser, client = make_browser(tmp_path, monkeypatch, {})
    with pytest.raises(BrowserContractError, match="anchored"):
        GuardedBrowser(client, browser.capture, {"url_patterns": [".*"], "ready_selector": "body"})
    with pytest.raises(BrowserContractError, match="outside"):
        browser.render("https://other.example/jobs")


def test_real_browser_page_cannot_submit_unreviewed_forms(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    url = "https://jobs.example.test/jobs/101"
    browser, client = make_browser(
        tmp_path,
        monkeypatch,
        {
            url: """<main>Public content</main><script>
        fetch('/apply', {method:'POST',body:'submission'}).catch(()=>{
        document.querySelector('main').className='ready';});</script>""",
        },
    )
    browser.render(url)
    assert client.calls == [url]


def test_browser_adapter_worker_persists_listing_full_detail_and_pdf(tmp_path, monkeypatch):
    """Real JS rendering -> existing parser -> queue -> binary/text SQLite persistence."""
    from datetime import UTC, datetime
    from jobagg.remediation_worker import Worker

    pytest.importorskip("playwright.sync_api")
    monkeypatch.setattr("jobagg.pipelines.http_checkpoint.time.sleep", lambda _: None)
    registry = tmp_path / "sources.yaml"
    registry.write_text("""sources:
  - id: browser_fixture
    name: Browser fixture
    ats_family: static_html
    base_url: https://jobs.example.test/list
    extra:
      parser: public_links
      listing_url: https://jobs.example.test/list
      job_link_selector_hint: /jobs/
      browser_render:
        url_patterns: ['^https://jobs\\.example\\.test/(list|jobs/[0-9]+)$']
        ready_selector: main.ready
        content_selector: main
        timeout_seconds: 30
""")
    policy = tmp_path / "robots.yaml"
    policy.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    body = "Responsibilities and qualifications: conduct research and prepare reports. " * 35
    pdf = Path(__file__).parent / "fixtures/eu_careers/enisa_14_public_notice_20260913.pdf"
    responses = {
        "https://jobs.example.test/list": '<main class="ready"><a href="/jobs/101">Research Analyst</a></main>',
        "https://jobs.example.test/jobs/101": """<main></main><script>
        fetch('/api/101').then(r=>r.json()).then(j=>{
        document.querySelector('main').innerHTML='<h1>Research Analyst</h1><p>'+j.text+'</p>'
        +'<a href="/docs/101.pdf">Terms of Reference</a>';
        document.querySelector('main').className='ready';});</script>""",
        "https://jobs.example.test/api/101": {"text": body},
        "https://jobs.example.test/docs/101.pdf": pdf.read_bytes(),
    }
    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "shared_lock": str(tmp_path / "owner.lock"),
                "reviewed_at": datetime.now(UTC).isoformat(),
                "prior_writers_reviewed": True,
                "no_unmigrated_policy_state": True,
                "scope_source_ids": ["browser_fixture"],
                "evidence": [],
                "detail_attempts": [],
                "host_states": {},
                "source_holds": {},
            }
        )
    )
    worker = Worker(
        registry=registry,
        robots=policy,
        workspace=tmp_path / "worker",
        shared_lock=tmp_path / "owner.lock",
        policy_bootstrap=bootstrap,
        client_factory=lambda source, policy: Replies(responses),
        max_tasks=3,
        max_seconds=180,
    )
    result = worker.tick(execute=True)
    assert not any("blocked" in outcome for outcome in result["tick_outcomes"]), result[
        "tick_outcomes"
    ]
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        job = conn.execute("SELECT * FROM jobs").fetchone()
        assert body.strip() in job["description"]
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 1
        assert (
            conn.execute("SELECT content FROM attachment_blobs").fetchone()[0] == pdf.read_bytes()
        )
        assert conn.execute("SELECT count(*) FROM remediation_documents").fetchone()[0] == 1
    assert result["completeness_certified"] is False


def test_real_browser_serializes_job_links_in_open_shadow_dom(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    url = "https://jobs.example.test/list"
    browser, client = make_browser(
        tmp_path,
        monkeypatch,
        {
            url: """<main><job-widget></job-widget></main><script>
        document.querySelector('job-widget').attachShadow({mode:'open'}).innerHTML=
        '2 results <a href="/jobs/123">Policy specialist</a>';
        document.querySelector('main').className='ready';</script>""",
        },
    )
    response = browser.render(url)
    assert '<a href="/jobs/123">Policy specialist</a>' in response.text
    assert 'data-jobagg-open-shadow-root="serialized"' in response.text
    value = json.loads(browser.last_receipt.read_text())
    assert value["links"] == [
        {"url": "https://jobs.example.test/jobs/123", "label": "Policy specialist"}
    ]


def test_inspection_watchdog_terminates_hung_process_without_retry(monkeypatch):
    import signal
    import subprocess
    from jobagg.browser_fetch import bounded_inspection

    commands, kills, waits = [], [], []

    class Hung:
        pid = 12345

        def wait(self, timeout):
            waits.append(timeout)
            if len(waits) < 3:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return -9

    def launch(command, **kwargs):
        commands.append((command, kwargs))
        return Hung()

    monkeypatch.setattr("jobagg.browser_fetch.subprocess.Popen", launch)
    monkeypatch.setattr("jobagg.browser_fetch.os.killpg", lambda pid, sig: kills.append((pid, sig)))
    assert bounded_inspection(["--execute", "--source", "fixture"]) == 75
    assert len(commands) == 1 and commands[0][1] == {"start_new_session": True}
    assert commands[0][0][-1] == "--_child"
    assert kills == [(12345, signal.SIGTERM), (12345, signal.SIGKILL)]
    assert waits == [210, 10, 10]
