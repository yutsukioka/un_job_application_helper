from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from pathlib import Path
import threading

import pytest

from jobagg.http import HttpResponse, JobAggHTTPClient
from jobagg.remediation_worker import Worker, interval_peak


def make_worker(tmp_path, monkeypatch, *, count=4, parallel=4, shared_host=False,
                barrier=False, max_tasks=None, max_details=None, delay=0.02):
    from jobagg.pipelines import http_checkpoint

    monkeypatch.setattr(http_checkpoint.time, "sleep", lambda _: None)
    registry = tmp_path / "registry.yaml"
    hosts = ["shared.example" if shared_host else f"host{i}.example" for i in range(count)]
    registry.write_text("sources:\n" + "".join(
        f"  - id: source{i}\n    name: Source{i}\n    ats_family: workday\n"
        f"    base_url: https://{host}/External\n    extra:\n"
        f"      cxs_base_url: https://{host}/wday/cxs/source{i}/External\n"
        "      page_size: 20\n      max_pages: 2\n"
        for i, host in enumerate(hosts)))
    robots = tmp_path / "robots.yaml"
    robots.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    owner = tmp_path / "owner.lock"
    bootstrap = tmp_path / "bootstrap.json"
    bootstrap.write_text(json.dumps({
        "schema_version": 1, "shared_lock": str(owner),
        "reviewed_at": datetime.now(UTC).isoformat(), "prior_writers_reviewed": True,
        "no_unmigrated_policy_state": True, "scope_source_ids": [f"source{i}" for i in range(count)],
        "evidence": [], "detail_attempts": [], "host_states": {}, "source_holds": {},
        "review_note": "Isolated concurrency fixture; no previous writers",
    }))
    lock = threading.Lock()
    gate = threading.Barrier(count) if barrier else None
    log, thread_dbs = [], {}
    description = "<h2>Responsibilities</h2><p>" + "Deliver comprehensive public analysis. " * 35 + "</p>"

    class Client(JobAggHTTPClient):
        def __init__(self, source):
            super().__init__(min_delay_seconds=0, max_retries=0)
            self.source = source

        def _request(self, url, **kwargs):
            with lock:
                log.append((self.source.id, url, threading.get_ident()))
                thread_dbs[threading.get_ident()] = id(worker.db)
                assert worker.db._persistent_conn is None, "No SQLite transaction held during HTTP"
            if gate and url.endswith("/jobs"):
                gate.wait(timeout=5)
            threading.Event().wait(delay)
            if url.endswith("/jobs"):
                payload = {"total": 1, "jobPostings": [{"title": "Analyst", "externalPath": "/job/Analyst_R1", "bulletFields": ["R1"]}]}
            else:
                payload = {"jobPostingInfo": {"jobReqId": "R1", "title": "Analyst", "jobDescription": description,
                    "externalUrl": self.source.base_url + "/job/Analyst_R1", "location": "City"}}
            body = json.dumps(payload).encode()
            return HttpResponse(url, 200, {"Content-Type": "application/json"}, body.decode(), body)

    worker = Worker(registry=registry, robots=robots, workspace=tmp_path / "worker", shared_lock=owner,
        max_tasks=max_tasks or count * 2, max_detail_tasks=max_details,
        parallel_sources=parallel, max_seconds=60, policy_bootstrap=bootstrap,
        client_factory=lambda source, policy: Client(source))
    return worker, log, thread_dbs


def test_four_sources_really_overlap_with_atomic_details_and_durable_order(tmp_path, monkeypatch):
    worker, calls, thread_dbs = make_worker(tmp_path, monkeypatch, barrier=True)
    result = worker.tick(execute=True)
    metrics = result["concurrency"]
    assert metrics["limit_used"] == metrics["peak_active_tasks"] == metrics["peak_active_sources"] == 4
    assert metrics["peak_active_scheduling_hosts"] == 4
    assert metrics["peak_active_hosts"] == metrics["peak_http_requests"] == 4
    assert metrics["attempted_tasks"] == metrics["accepted_progress"] == 8
    assert len(calls) == len(metrics["request_intervals"]) == 8
    assert len(thread_dbs) == len(set(thread_dbs.values())) == 4
    assert id(worker.db) not in thread_dbs.values()
    assert result["status"] == "incomplete" and not result["completeness_certified"]
    assert metrics["publication_status"] == "not_run"
    assert Path(result["concurrency_receipt"]["path"]).is_file()
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 4
        attempts = conn.execute("SELECT * FROM remediation_attempts ORDER BY started_at").fetchall()
        assert len(attempts) == 8 and all(row["status"] == "done" for row in attempts)
        assert conn.execute("SELECT count(*) FROM remediation_tasks WHERE status='inflight'").fetchone()[0] == 0
    for item in metrics["request_intervals"]:
        capture = json.loads(Path(item["capture_path"]).read_text())
        assert item["request_url_sha256"] == capture["request_url_sha256"]
        assert item["started_monotonic"] < item["finished_monotonic"]
    # The next idle tick neither reclaims completed jobs nor resets shared history.
    again = worker.tick(execute=True)
    assert again["concurrency"]["attempted_tasks"] == 0 and len(calls) == 8


def test_shared_initial_host_serializes_even_different_sources(tmp_path, monkeypatch):
    worker, calls, _ = make_worker(tmp_path, monkeypatch, count=3, shared_host=True)
    result = worker.tick(execute=True)
    assert len(calls) == 6
    metrics = result["concurrency"]
    assert metrics["peak_active_tasks"] == metrics["peak_active_sources"] == 1
    assert metrics["peak_active_scheduling_hosts"] == 1
    assert metrics["peak_active_hosts"] == metrics["peak_http_requests"] == 1


def test_parallel_detail_budget_counts_claims_before_completion(tmp_path, monkeypatch):
    worker, _, _ = make_worker(tmp_path, monkeypatch, max_tasks=8, max_details=1)
    result = worker.tick(execute=True)
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_attempts WHERE kind='detail'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM remediation_attempts").fetchone()[0] == 5
    assert result["concurrency"]["attempted_tasks"] == 5


def test_parallel_total_budget_no_speculative_claims(tmp_path, monkeypatch):
    worker, _, _ = make_worker(tmp_path, monkeypatch, count=5, max_tasks=3)
    result = worker.tick(execute=True)
    assert result["concurrency"]["attempted_tasks"] == 3
    assert result["tick_stop_reason"] == "task_budget"
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_attempts").fetchone()[0] == 3


def test_unhandled_thread_failure_joins_other_claims_without_acceptance(tmp_path, monkeypatch):
    worker, _, _ = make_worker(tmp_path, monkeypatch, max_tasks=8)
    original = worker._perform_claimed
    started = threading.Barrier(4)
    finished = []

    class Crash(BaseException):
        pass

    def perform(task, deadline, token):
        started.wait(timeout=5)
        if task["source_id"] == "source0":
            raise Crash("simulated execution interruption")
        result = original(task, deadline, token)
        finished.append(task["source_id"])
        return result

    monkeypatch.setattr(worker, "_perform_claimed", perform)
    with pytest.raises(Crash):
        worker.tick(execute=True)
    assert len(finished) == 3
    assert not list((worker.workspace / "ticks").glob("*.json"))
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_attempts").fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM remediation_tasks WHERE status='inflight'").fetchone()[0] == 1
    assert len(list((worker.shared_policy.root / "attempts").glob("*.json"))) == 4


def test_thread_local_nested_transactions_are_serialized(tmp_path, monkeypatch):
    worker, _, _ = make_worker(tmp_path, monkeypatch, count=2)
    worker.initialize()
    entered = threading.Event()
    release = threading.Event()
    seen = []

    def first():
        with worker.db.connection_scope() as conn:
            with worker.db.connect() as nested:
                assert conn is nested
                seen.append((id(worker.db), id(conn)))
            entered.set()
            assert release.wait(3)

    def second():
        assert entered.wait(3)
        assert worker.db._persistent_conn is None
        with worker.db.connection_scope() as conn:
            seen.append((id(worker.db), id(conn)))

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        b = pool.submit(second)
        assert entered.wait(3)
        threading.Event().wait(0.03)
        assert len(seen) == 1
        release.set()
        a.result()
        b.result()
    assert seen[0][0] != seen[1][0]
    assert worker.db._persistent_conn is None


@pytest.mark.parametrize("value", [0, 33, -1, True, 1.5])
def test_invalid_concurrency_refused_without_writes(tmp_path, monkeypatch, value):
    with pytest.raises(ValueError, match="Parallel"):
        make_worker(tmp_path, monkeypatch, parallel=value)
    assert not (tmp_path / "worker").exists()


def test_serial_default_preview_and_interval_boundary(tmp_path, monkeypatch):
    worker, calls, _ = make_worker(tmp_path, monkeypatch, parallel=1)
    result = worker.tick()
    assert result["tick_limits"]["parallel_sources"] == 1 and not calls
    assert not worker.workspace.exists()
    assert interval_peak([
        {"started_monotonic": 0, "finished_monotonic": 1},
        {"started_monotonic": 1, "finished_monotonic": 2},
    ]) == 1


def test_one_detail_failure_does_not_lose_peer_results_or_claim_history(tmp_path, monkeypatch):
    from jobagg.adapters.workday import WorkdayAdapter

    worker, calls, _ = make_worker(tmp_path, monkeypatch, barrier=True)
    original = WorkdayAdapter.fetch_detail_for_listing_item

    def detail(adapter, raw):
        job = original(adapter, raw)
        return None if adapter.source.id == "source0" else job

    monkeypatch.setattr(WorkdayAdapter, "fetch_detail_for_listing_item", detail)
    report = worker.tick(execute=True)
    assert len(calls) == 8
    metrics = report["concurrency"]
    assert metrics["attempted_tasks"] == 8 and metrics["accepted_progress"] == 7
    assert metrics["runtime_errors"] == metrics["integrity_errors"] == 1
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 3
        assert conn.execute("SELECT count(*) FROM remediation_tasks WHERE status='blocked'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM remediation_tasks WHERE status='inflight'").fetchone()[0] == 0
    assert len(list((worker.shared_policy.root / "attempts").glob("*.json"))) == 8


def test_deadline_stops_new_claims_and_joins_already_running_tasks(tmp_path, monkeypatch):
    worker, _, _ = make_worker(tmp_path, monkeypatch, count=4, max_tasks=8, delay=3)
    worker.max_seconds = 10
    report = worker.tick(execute=True)
    assert report["tick_stop_reason"] == "work_deadline"
    assert 1 <= report["concurrency"]["attempted_tasks"] <= 4
    # Admission stopped early enough to finish and report inside the deadline.
    assert report["concurrency"]["control_cycle_complete"] is True
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_tasks WHERE status='inflight'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM remediation_attempts WHERE kind='detail'").fetchone()[0] == 0
    assert all(item["finished_at"] <= report["generated_at"] for item in report["concurrency"]["task_intervals"])


def test_subsecond_budget_does_not_consume_task_attempts(tmp_path, monkeypatch):
    worker, calls, _ = make_worker(tmp_path, monkeypatch)
    worker.max_seconds = 0.25
    report = worker.tick(execute=True)
    assert report["tick_stop_reason"] == "work_deadline"
    assert report["concurrency"]["attempted_tasks"] == 0 and not calls


def test_two_sources_share_one_immutable_blob_without_partial_write_race(tmp_path, monkeypatch):
    import hashlib
    from jobagg.pipelines import document_tasks
    from jobagg.remediation_worker import dump

    worker, _, _ = make_worker(tmp_path, monkeypatch, count=2, parallel=2)
    worker.tick(execute=True)
    gate = threading.Barrier(2)
    content = b"Same public job document wording. " * 100
    digest = hashlib.sha256(content).hexdigest()

    class DocumentClient(JobAggHTTPClient):
        def __init__(self):
            super().__init__(min_delay_seconds=0, max_retries=0)

        def _request(self, url, **kwargs):
            gate.wait(timeout=5)
            return HttpResponse(url, 200, {"Content-Type": "text/plain"}, content.decode(), content)

    monkeypatch.setattr(document_tasks, "extract_document", lambda data, media, url: {
        "content_sha256": digest, "text_sha256": digest,
        "extracted_text": content.decode(), "document_links": [], "fidelity_status": "unverified",
    })
    worker.client_factory = lambda source, policy: DocumentClient()
    with worker.db.connection_scope() as conn:
        for source in worker.by_id.values():
            row = worker.db.get_job(source.id + ":R1")
            url = source.base_url + "/conditions.txt"
            worker.enqueue_document(conn, source.id, {
                "attachment_id": hashlib.sha256((row["job_key"] + url).encode()).hexdigest(),
                "url": url, "job_key": row["job_key"],
                "parent_description_sha256": hashlib.sha256(row["description"].encode()).hexdigest(),
                "depth": 0,
            })
    real_open = Path.open

    class SlowWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, data):
            assert worker._artifact_lock._is_owned()
            self.stream.write(data[:10])
            self.stream.flush()
            threading.Event().wait(0.04)
            self.stream.write(data[10:])

        def __getattr__(self, name):
            return getattr(self.stream, name)

    def slow_blob(path, mode="r", *args, **kwargs):
        stream = real_open(path, mode, *args, **kwargs)
        return SlowWriter(stream) if path.name == digest and mode == "xb" else stream

    monkeypatch.setattr(Path, "open", slow_blob)
    worker.max_tasks = 2
    report = worker.tick(execute=True)
    assert report["concurrency"]["accepted_progress"] == 2
    assert (worker.workspace / "blobs" / digest).read_bytes() == content
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM attachment_blobs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM remediation_documents").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM remediation_tasks WHERE kind='document' AND status='done'").fetchone()[0] == 2
    assert json.loads(dump(report))["completeness_certified"] is False


def test_baseline_full_fields_survive_listing_without_becoming_fresh_detail(tmp_path, monkeypatch):
    import hashlib
    from jobagg import baseline_inventory
    from jobagg.models import JobRecord

    worker, _, _ = make_worker(tmp_path, monkeypatch, count=1, max_tasks=1)
    worker.initialize()
    text = "Existing full public job description. " * 40
    job = JobRecord(source_id="source0", org_id="Source0", external_id="R1", ats_family="workday",
        title="Existing full title", description=text, department="Public unit",
        employment_type="Fixed term", source_url="https://host0.example/detail/R1",
        apply_url="https://host0.example/application/R1", raw={
            "_jobagg_baseline_inventory": {"baseline_id": "test-baseline"},
            "attachments": [{"attachment_id": "historical-document", "status": "unverified"}],
        })
    worker.db.upsert_job(job)
    digest = hashlib.sha256(text.encode()).hexdigest()
    with worker.db.connection_scope() as conn:
        for sql in baseline_inventory._TABLES:
            conn.execute(sql)
        original = dict(conn.execute("SELECT * FROM jobs").fetchone())
        row_digest = baseline_inventory._digest(original)
        conn.execute("INSERT INTO baseline_inventory_jobs VALUES(?,?,?,?,?,?,?,?,?,?)", (
            "test-baseline", "test-batch", "source0", job.identity_key(), job.identity_key(),
            row_digest, digest, json.dumps(original), "[]", "insert_missing"))
        conn.execute("INSERT INTO baseline_inventory_links VALUES(?,?,?,?,?,?)", (
            job.identity_key(), "test-baseline", "source0", "R1", digest, row_digest))
    report = worker.tick(execute=True)
    current = worker.db.get_job(job.identity_key())
    for key in ("description", "title", "department", "employment_type", "source_url", "apply_url"):
        assert current[key] == getattr(job, key)
    assert current["raw"]["attachments"] == job.raw["attachments"]
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 0
        task = conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone()
        assert task["status"] == "pending"
        payload = json.loads(task["payload"])
        assert payload["listing"]["title"] == "Analyst"
        attempt = conn.execute("SELECT evidence FROM remediation_attempts WHERE kind='listing'").fetchone()
    proof = json.loads(attempt[0])["listing_retention"]
    receipt = json.loads(Path(proof["path"]).read_text())["retained_details"][0]
    assert receipt["baseline_id"] == "test-baseline"
    assert receipt["fresh_source_observation"] is receipt["completeness_certified"] is False
    assert report["concurrency"]["accepted_progress"] == 1


def test_existing_source_holds_do_not_inflate_eligible_pressure(tmp_path, monkeypatch):
    worker, calls, _ = make_worker(tmp_path, monkeypatch)
    worker.initialize()
    (worker.shared_policy.root / "source_holds.json").write_text(json.dumps({"source0": {"reason": "Historical review hold"}}))
    report = worker.tick(execute=True)
    assert all(source != "source0" for source, _, _ in calls)
    metrics = report["concurrency"]
    assert metrics["eligible_distinct_sources"] == metrics["eligible_distinct_hosts"] == 3
    assert metrics["new_access_blocks"] == metrics["transport_failures"] == 0
    assert metrics["accepted_progress"] == metrics["attempted_tasks"] == 6


@pytest.mark.parametrize("dispatch_request", [{}, {"parallel_sources": 8}, {"parallel_sources": True}])
def test_dispatcher_limit_mismatch_refuses_before_any_worker_state(tmp_path, monkeypatch, dispatch_request):
    worker, calls, _ = make_worker(tmp_path, monkeypatch, parallel=4)
    with pytest.raises(ValueError, match="parallel source limit differ"):
        worker.tick(execute=True, request=dispatch_request)
    assert not calls and not worker.workspace.exists() and not worker.shared_lock.exists()


def test_existing_redirect_host_hold_is_policy_not_new_runtime_pressure(tmp_path, monkeypatch):
    import hashlib
    from urllib.request import Request
    from jobagg.pipelines.http_checkpoint import RedirectHop

    worker, _, _ = make_worker(tmp_path, monkeypatch, count=1, parallel=4)
    worker.tick(execute=True)
    held_host = "existing-hold.example"
    held = worker.shared_policy.root / "hosts" / ("host-" + hashlib.sha256(held_host.encode()).hexdigest()[:24] + ".json")
    held.write_text(json.dumps({"stopped": True, "reason": "Prior reviewed access hold"}))
    before = held.read_bytes()
    calls = []

    class RedirectClient(JobAggHTTPClient):
        def __init__(self):
            super().__init__(min_delay_seconds=0, max_retries=0)

        def _request(self, url, **kwargs):
            calls.append(url)
            assert held_host not in url, "Existing held host must never be dispatched"
            raise RedirectHop(Request("https://" + held_host + "/required.pdf"), 302)

    worker.client_factory = lambda source, policy: RedirectClient()
    with worker.db.connection_scope() as conn:
        row = worker.db.get_job("source0:R1")
        worker.enqueue_document(conn, "source0", {
            "attachment_id": "redirected-document", "url": "https://host0.example/public-document",
            "job_key": row["job_key"], "depth": 0,
            "parent_description_sha256": hashlib.sha256(row["description"].encode()).hexdigest(),
        })
    worker.max_tasks = 1
    report = worker.tick(execute=True)
    metrics = report["concurrency"]
    assert len(calls) == 1 and held.read_bytes() == before
    assert metrics["policy_holds"] == 1
    assert metrics["runtime_errors"] == metrics["new_access_blocks"] == metrics["transport_failures"] == 0
    assert report["tick_outcomes"][0]["status"] == "blocked"


@pytest.mark.parametrize("denial", ["allowlist", "private_network", "cached_robots"])
def test_local_policy_denial_is_not_outbound_http_or_transport_pressure(tmp_path, monkeypatch, denial):
    import hashlib
    from jobagg.http_safe import SafeHTTPPolicy
    from jobagg.robots import BlankLineSafeRobotFileParser

    worker, _, _ = make_worker(tmp_path, monkeypatch, count=1)
    worker.tick(execute=True)
    opens = []

    class NoOutbound:
        def open(self, *args, **kwargs):
            opens.append(args)
            raise AssertionError("Local guard must reject before outbound HTTP")

    monkeypatch.setattr(JobAggHTTPClient, "_build_opener", lambda *args: NoOutbound())
    host = "unlisted.example" if denial == "allowlist" else "host0.example"
    url = "https://" + host + "/required.pdf"
    worker.client_factory = lambda source, policy: JobAggHTTPClient(
        min_delay_seconds=0, max_retries=0,
        safe_policy=SafeHTTPPolicy(allowed_hosts={"host0.example"},
            resolver=lambda _: ["127.0.0.1" if denial == "private_network" else "93.184.216.34"]))
    if denial == "cached_robots":
        worker.policy.honor_robots_txt = True
        original_context = worker.context

        def context(*args, **kwargs):
            adapter, client, capture = original_context(*args, **kwargs)
            parser = BlankLineSafeRobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
            capture.checker.parsers["https://host0.example"] = parser
            return adapter, client, capture

        monkeypatch.setattr(worker, "context", context)
    with worker.db.connection_scope() as conn:
        row = worker.db.get_job("source0:R1")
        worker.enqueue_document(conn, "source0", {
            "attachment_id": "locally-denied-document", "url": url,
            "job_key": row["job_key"], "depth": 0,
            "parent_description_sha256": hashlib.sha256(row["description"].encode()).hexdigest(),
        })
    worker.max_tasks = 1
    report = worker.tick(execute=True)
    metrics = report["concurrency"]
    assert not opens
    assert metrics["attempted_tasks"] == metrics["policy_holds"] == 1
    assert metrics["runtime_errors"] == metrics["transport_failures"] == metrics["new_access_blocks"] == 0
    assert metrics["peak_active_hosts"] == metrics["peak_http_requests"] == 0
    assert metrics["local_policy_request_attempts"] == (denial != "cached_robots")
    for item in metrics["request_intervals"]:
        assert item["local_policy_denial"] is True
        assert item["opener_entries"] == 0
        assert item["opener_observation"] == "native_http_client"
        capture = json.loads(Path(item["capture_path"]).read_text())
        assert capture["error_type"] == "SSRFProtectionError"
        assert capture["body_captured"] is False and "status_code" not in capture
    with worker.db.connect() as conn:
        task = conn.execute("SELECT * FROM remediation_tasks WHERE kind='document'").fetchone()
        attempt = conn.execute("SELECT * FROM remediation_attempts WHERE kind='document'").fetchone()
        assert task["status"] == attempt["status"] == ("blocked" if denial == "cached_robots" else "dead_letter")
        assert task["attempts"] == 1


@pytest.mark.parametrize("failure", ["timeout", "403"])
def test_real_outbound_failures_remain_fresh_pressure(tmp_path, monkeypatch, failure):
    from urllib.error import HTTPError as URLHTTPError
    from jobagg.http_safe import SafeHTTPPolicy

    worker, _, _ = make_worker(tmp_path, monkeypatch, count=1, max_tasks=1)
    opens = []

    class FailedOutbound:
        def open(self, request, **kwargs):
            opens.append(request.full_url)
            if failure == "403":
                raise URLHTTPError(request.full_url, 403, "Forbidden", {}, None)
            raise TimeoutError("Actual outbound transport timed out")

    monkeypatch.setattr(JobAggHTTPClient, "_build_opener", lambda *args: FailedOutbound())
    worker.client_factory = lambda source, policy: JobAggHTTPClient(
        min_delay_seconds=0, max_retries=0,
        safe_policy=SafeHTTPPolicy(allowed_hosts={"host0.example"}, resolver=lambda _: ["93.184.216.34"]))
    report = worker.tick(execute=True)
    metrics = report["concurrency"]
    assert len(opens) == 1
    assert metrics["policy_holds"] == metrics["local_policy_request_attempts"] == 0
    assert metrics["peak_active_hosts"] == metrics["peak_http_requests"] == 1
    assert metrics["new_access_blocks"] == (failure == "403")
    assert metrics["transport_failures"] == (failure == "timeout")
    assert metrics["request_intervals"][0]["opener_entries"] == 1


def test_opener_policy_denial_retains_actual_opener_entry(tmp_path, monkeypatch):
    from jobagg.http_safe import SafeHTTPPolicy, SSRFProtectionError

    worker, _, _ = make_worker(tmp_path, monkeypatch, count=1, max_tasks=1)
    opens, resolutions, clients = [], [], []

    class Opener:
        def open(self, request, **kwargs):
            opens.append(request.full_url)
            assert request._jobagg_endpoint.addresses == ("93.184.216.34",)
            # A redirect may fail policy after the original request entered
            # the opener. This still counts as an outbound attempt.
            raise SSRFProtectionError("Redirect resolves to a denied network")

    opener = Opener()
    monkeypatch.setattr(JobAggHTTPClient, "_build_opener", lambda *args: opener)

    def resolve(host):
        resolutions.append(host)
        return ["93.184.216.34"]

    def factory(source, policy):
        client = JobAggHTTPClient(min_delay_seconds=0, max_retries=0,
            safe_policy=SafeHTTPPolicy(allowed_hosts={"host0.example"}, resolver=resolve))
        clients.append(client)
        return client

    worker.client_factory = factory
    report = worker.tick(execute=True)
    metrics = report["concurrency"]
    assert len(opens) == 1 and len(resolutions) == 1
    assert metrics["policy_holds"] == 1
    assert metrics["runtime_errors"] == metrics["transport_failures"] == metrics["new_access_blocks"] == 0
    assert metrics["local_policy_request_attempts"] == 0
    assert metrics["peak_active_hosts"] == metrics["peak_http_requests"] == 1
    interval = metrics["request_intervals"][0]
    assert interval["opener_entries"] == 1 and interval["local_policy_denial"] is False
    assert interval["error_type"] == "SSRFProtectionError"
    assert all(client._opener is opener for client in clients), "Exact original opener restored"
    # Historical/custom intervals lack proven opener-stage data. Even with the
    # same typed policy error they must not be relabeled zero outbound activity.
    for item in worker._request_intervals:
        item.pop("opener_entries")
        item.pop("opener_observation")
    replay = worker.concurrency_report(metrics["started_at"], float("inf"))
    assert replay["local_policy_request_attempts"] == 0 and replay["peak_http_requests"] == 1


@pytest.mark.parametrize("parallel", [16, 32])
def test_high_parallelism_persists_every_detail_without_overlapping_same_host(tmp_path, monkeypatch, parallel):
    worker, calls, _ = make_worker(tmp_path, monkeypatch, count=parallel, parallel=parallel, barrier=True)
    result = worker.tick(execute=True)
    metrics = result['concurrency']
    assert metrics['peak_active_sources'] == parallel
    assert metrics['peak_active_scheduling_hosts'] == parallel
    assert metrics['attempted_tasks'] == metrics['accepted_progress'] == parallel * 2
    assert len(calls) == parallel * 2
    with worker.db.connect() as conn:
        assert conn.execute('SELECT count(*) FROM remediation_observations').fetchone()[0] == parallel
        assert conn.execute("SELECT count(*) FROM remediation_tasks WHERE status='inflight'").fetchone()[0] == 0


def test_high_parallelism_still_serializes_shared_host(tmp_path, monkeypatch):
    worker, calls, _ = make_worker(tmp_path, monkeypatch, count=8, parallel=32, shared_host=True)
    result = worker.tick(execute=True)
    assert result['concurrency']['peak_active_tasks'] == 1
    assert len(calls) == 16
