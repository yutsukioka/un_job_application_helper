from datetime import UTC, datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from jobagg.http import HttpResponse, JobAggHTTPClient
from jobagg.remediation_worker import Worker, dump, sha, validate_request


class FixtureClient(JobAggHTTPClient):
    def __init__(self, replies, calls):
        super().__init__(min_delay_seconds=0, max_retries=0)
        self.replies = replies
        self.calls = calls

    def _request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.replies[url]
        if isinstance(value, Exception):
            raise value
        content = json.dumps(value).encode() if isinstance(value, dict) else value
        return HttpResponse(
            url,
            200,
            {"Content-Type": "application/json" if isinstance(value, dict) else "application/pdf"},
            content.decode(errors="replace"),
            content,
        )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from jobagg.pipelines import http_checkpoint

    monkeypatch.setattr(http_checkpoint.time, "sleep", lambda seconds: None)
    registry = tmp_path / "sources.yaml"
    registry.write_text("""sources:
  - id: demo_workday
    name: Demo
    ats_family: workday
    base_url: https://demo.example/External
    extra:
      cxs_base_url: https://demo.example/wday/cxs/demo/External
      page_size: 20
      max_pages: 2
  - id: disabled_example
    name: Disabled
    ats_family: workday
    base_url: https://disabled.example
    enabled: false
""")
    robots = tmp_path / "robots.yaml"
    robots.write_text("default:\n  honor_robots_txt: false\n  min_delay_seconds: 0\n")
    path = "/job/City/Analyst_R1"
    url = "https://demo.example/wday/cxs/demo/External"
    description = (
        "<h2>Responsibilities</h2><p>"
        + ("Conduct analysis and prepare complete reports. " * 30)
        + "</p><h2>Qualifications</h2><p>Relevant experience and advanced degree.</p>"
    )
    listing = {
        "total": 1,
        "jobPostings": [
            {
                "title": "Analyst",
                "externalPath": path,
                "locationsText": "City",
                "postedOn": "Posted Today",
                "bulletFields": ["R1"],
            }
        ],
    }
    detail = {
        "jobPostingInfo": {
            "jobReqId": "R1",
            "title": "Analyst",
            "jobDescription": description,
            "location": "City",
            "externalUrl": "https://demo.example/External" + path,
            "timeType": "Full time",
        }
    }
    replies = {url + "/jobs": listing, url + path: detail}
    calls = []
    bootstrap = tmp_path / "policy-bootstrap.json"
    bootstrap.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "shared_lock": str(tmp_path / "shared.lock"),
                "reviewed_at": datetime.now(UTC).isoformat(),
                "prior_writers_reviewed": True,
                "no_unmigrated_policy_state": True,
                "scope_source_ids": ["demo_workday", "unicef_pageup"],
                "evidence": [],
                "detail_attempts": [],
                "host_states": {},
                "source_holds": {},
                "review_note": "Temporary test environment has no previous writers",
            }
        )
    )
    worker = Worker(
        registry=registry,
        robots=robots,
        workspace=tmp_path / "workspace",
        shared_lock=tmp_path / "shared.lock",
        max_tasks=3,
        client_factory=lambda source, policy: FixtureClient(replies, calls),
        policy_bootstrap=bootstrap,
    )
    return worker, replies, calls, tmp_path


def test_preview_does_not_create_database_lock_workspace_or_fetch(setup):
    worker, replies, calls, tmp = setup
    result = worker.tick()
    assert result["status"] == "dry_run" and not calls
    assert not worker.workspace.exists() and not worker.shared_lock.exists()
    assert [s["source_id"] for s in result["disabled_sources"]] == ["disabled_example"]


def test_actual_workday_api_listing_then_detail_and_resume_without_refetch(setup):
    worker, replies, calls, tmp = setup
    result = worker.tick(execute=True)
    assert len(calls) == 2
    assert result["status"] == "incomplete" and result["completeness_certified"] is False
    source = result["sources"][0]
    assert source["enumeration"]["complete"] is True
    row = worker.db.get_job("demo_workday:R1")
    assert row and "Qualifications" in row["description"]
    assert worker.db.get_detail_backlog("demo_workday:R1")["detail_status"] == "complete"
    assert row["raw"]["attachment_verification"]["complete"] is False
    before = len(calls)
    worker.tick(execute=True)
    assert len(calls) == before
    with worker.db.connect() as conn:
        assert (
            conn.execute(
                "select count(*) from remediation_tasks where kind='detail' and status='done'"
            ).fetchone()[0]
            == 1
        )
        observation = json.loads(
            conn.execute("select proof from remediation_observations").fetchone()[0]
        )
        assert observation["independent_whole_public_text_verified"] is False


def test_truncated_api_never_certifies_population_but_observed_details_still_queue(setup):
    worker, replies, calls, tmp = setup
    key = next(k for k in replies if k.endswith("/jobs"))
    replies[key]["total"] = 1000
    result = worker.tick(execute=True)
    assert result["sources"][0]["enumeration"]["complete"] is False
    assert worker.db.get_job("demo_workday:R1") is not None
    assert result["sources"][0]["listed_current"] == 1


def test_identity_mismatch_preserves_listing_only_and_blocks_no_automatic_retries(setup):
    worker, replies, calls, tmp = setup
    detail = next(value for value in replies.values() if "jobPostingInfo" in value)
    detail["jobPostingInfo"]["jobReqId"] = "OTHER"
    worker.tick(execute=True)
    row = worker.db.get_job("demo_workday:R1")
    assert row["description"] is None or "Qualifications" not in row["description"]
    with worker.db.connect() as conn:
        task = conn.execute("select * from remediation_tasks where kind='detail'").fetchone()
        assert task["status"] == "blocked"
    count = len(calls)
    worker.tick(execute=True)
    assert len(calls) == count


def test_job_and_completion_and_document_queue_rollback_together(setup, monkeypatch):
    worker, replies, calls, tmp = setup
    from jobagg.pipelines import document_tasks

    monkeypatch.setattr(
        document_tasks,
        "discover_document_inventory",
        lambda job: {
            "candidates": [{"attachment_id": "doc", "url": "https://demo.example/tor.pdf"}],
            "excluded_links": [],
            "discovery_complete": False,
        },
    )
    original = worker.enqueue

    def fail(conn, source, kind, identity, payload, **kwargs):
        if kind == "document":
            raise RuntimeError("simulated queue storage failure")
        return original(conn, source, kind, identity, payload, **kwargs)

    monkeypatch.setattr(worker, "enqueue", fail)
    worker.tick(execute=True)
    row = worker.db.get_job("demo_workday:R1")
    assert "Qualifications" not in (row["description"] or "")
    backlog = worker.db.get_detail_backlog("demo_workday:R1")
    assert backlog is None or backlog["detail_status"] != "complete"
    with worker.db.connect() as conn:
        assert conn.execute("select count(*) from remediation_observations").fetchone()[0] == 0


def test_crash_claim_stays_counted_and_requires_review(setup):
    worker, replies, calls, tmp = setup
    worker.initialize()
    worker.seed_listings()
    task = worker.choose()
    token = worker.claim(task)
    worker.initialize()
    with worker.db.connect() as conn:
        assert (
            conn.execute(
                "select status from remediation_tasks where task_id=?", (task["task_id"],)
            ).fetchone()[0]
            == "interrupted"
        )
        assert (
            conn.execute(
                "select status from remediation_attempts where attempt_id=?", (token,)
            ).fetchone()[0]
            == "reserved"
        )
    assert not calls


def test_unicef_normal_ten_rolling_hour_starts_and_cross_tick_pause(setup):
    worker, replies, calls, tmp = setup
    worker.initialize()
    from jobagg.models import OrganizationSource

    source = OrganizationSource(
        "unicef_pageup",
        "UNICEF",
        "pageup",
        "https://jobs.unicef.org",
        extra={
            "detail_min_delay_seconds": 30,
            "detail_jitter_seconds": 15,
            "detail_batch_size": 3,
            "detail_batch_pause_seconds": 300,
        },
    )
    now = time.time()
    with worker.db.connect() as conn:
        for i in range(10):
            start = now - 1800 + i * 50
            conn.execute(
                "insert into remediation_attempts values(?,?,?,?,?,?,?,?)",
                (str(i), "task", source.id, "detail", start, start + 5, "failed", "{}"),
            )
            worker.shared_policy.reserve(
                str(i), source.id, "detail", start, worker.workspace, "task"
            )
            path = next(
                p
                for p in (worker.shared_policy.root / "attempts").glob("*.json")
                if json.loads(p.read_text())["attempt_id"] == str(i)
            )
            value = json.loads(path.read_text())
            value["finished_at"] = start + 5
            path.write_text(json.dumps(value))
    assert worker.policy_due(source, "detail", now) == pytest.approx(now - 1795 + 3600)
    with worker.db.connect() as conn:
        conn.execute("delete from remediation_attempts")
        for path in (worker.shared_policy.root / "attempts").glob("*.json"):
            path.unlink()
        (worker.shared_policy.root / "attempt_index.json").write_text("[]")
        for i in range(3):
            start = now - 120 + i * 50
            conn.execute(
                "insert into remediation_attempts values(?,?,?,?,?,?,?,?)",
                (str(i), "task", source.id, "detail", start, start + 10, "done", "{}"),
            )
            worker.shared_policy.reserve(
                str(i), source.id, "detail", start, worker.workspace, "task"
            )
            path = next(
                p
                for p in (worker.shared_policy.root / "attempts").glob("*.json")
                if json.loads(p.read_text())["attempt_id"] == str(i)
            )
            value = json.loads(path.read_text())
            value["finished_at"] = start + 10
            path.write_text(json.dumps(value))
    assert worker.policy_due(source, "detail", now) == pytest.approx(now - 10 + 300)


def test_same_owner_new_workspace_inherits_quota_and_host_hold(setup):
    worker, replies, calls, tmp = setup
    worker.initialize()
    from jobagg.models import OrganizationSource

    source = OrganizationSource("unicef_pageup", "UNICEF", "pageup", "https://jobs.unicef.org")
    for n in range(10):
        worker.shared_policy.reserve(
            str(n), source.id, "detail", time.time() - 100, worker.workspace, "job"
        )
    other = Worker(
        registry=worker.registry,
        robots=worker.robots_path,
        workspace=tmp / "new-generation",
        shared_lock=worker.shared_lock,
    )
    other.initialize()
    assert other.policy_due(source, "detail", time.time()) > time.time() + 3400
    import hashlib

    state = (
        other.shared_policy.root
        / "hosts"
        / ("host-" + hashlib.sha256(b"demo.example").hexdigest()[:24] + ".json")
    )
    state.write_text(json.dumps({"stopped": True, "reason": "Prior real access challenge"}))
    other.seed_listings()
    assert other.choose() is None
    assert not calls


def test_two_budget_deferrals_remain_selectable_and_then_complete(setup, monkeypatch):
    worker, replies, calls, tmp = setup
    worker.initialize()
    worker.seed_listings()
    original = worker.context
    from jobagg.pipelines.http_checkpoint import HostIneligible

    def defer(*args, **kwargs):
        raise HostIneligible("No tick time left", category="budget")

    monkeypatch.setattr(worker, "context", defer)
    for _ in range(2):
        task = worker.choose()
        assert task is not None
        worker.perform(task, time.time() + 10)
        with worker.db.connect() as conn:
            conn.execute("UPDATE remediation_tasks SET eligible_at=0 WHERE status='pending'")
    assert worker.choose() is not None and not calls
    monkeypatch.setattr(worker, "context", original)
    worker.tick(execute=True)
    assert worker.db.get_detail_backlog("demo_workday:R1")["detail_status"] == "complete"


def test_document_pdf_pages_children_and_binary_are_atomic_current_associations(setup):
    worker, replies, calls, tmp = setup
    from test_document_tasks import pdf_bytes

    detail = next(value for value in replies.values() if "jobPostingInfo" in value)
    detail["jobPostingInfo"]["jobDescription"] += (
        '<a href="https://demo.example/main.pdf">Terms of reference</a>'
    )
    replies["https://demo.example/main.pdf"] = pdf_bytes(link="https://demo.example/child.pdf")
    replies["https://demo.example/child.pdf"] = pdf_bytes(("Required child",))
    worker.max_tasks = 4
    worker.tick(execute=True)
    row = worker.db.get_job("demo_workday:R1")
    documents = row["raw"]["attachments"]
    assert len(documents) == 2
    assert sorted(d["page_count"] for d in documents) == [1, 2]
    assert all(d["fidelity_complete"] is False for d in documents)
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM attachment_blobs").fetchone()[0] == 2
        tasks = conn.execute(
            "SELECT receipt FROM remediation_tasks WHERE kind='document'"
        ).fetchall()
    assert len(tasks) == 2
    metadata = [json.loads(p.read_text()) for p in worker.workspace.glob("captures/*/http/*.json")]
    assert all(m["phase"]["job_id"] == "R1" for m in metadata if m["phase"]["kind"] == "document")
    assert row["raw"]["attachment_verification"]["complete"] is False


def test_printed_url_is_visible_blocked_work_without_network_dispatch(setup):
    worker, replies, calls, tmp = setup
    detail = next(value for value in replies.values() if "jobPostingInfo" in value)
    detail["jobPostingInfo"]["jobDescription"] += " See https://demo.example/terms.pdf?section=2)."
    worker.tick(execute=True)
    assert len(calls) == 2
    with worker.db.connect() as conn:
        row = conn.execute(
            "SELECT status,last_error FROM remediation_tasks WHERE kind='document'"
        ).fetchone()
    assert row["status"] == "blocked" and "Printed URL" in row["last_error"]


def test_changed_listing_gets_immediate_detail_and_absent_verified_id_is_retired(setup):
    worker, replies, calls, tmp = setup
    worker.tick(execute=True)
    listing = next(value for value in replies.values() if "jobPostings" in value)
    detail = next(value for value in replies.values() if "jobPostingInfo" in value)
    listing["jobPostings"][0]["title"] = "Senior Analyst"
    detail["jobPostingInfo"]["title"] = "Senior Analyst"
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=0")
    worker.tick(execute=True)
    assert len(calls) == 4 and worker.db.get_job("demo_workday:R1")["title"] == "Senior Analyst"
    with worker.db.connect() as conn:
        conn.execute(
            "UPDATE remediation_tasks SET status='pending',eligible_at=0 WHERE kind='detail'"
        )
        conn.execute("UPDATE remediation_sources SET next_list_at=0")
    listing.update(total=0, jobPostings=[])
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        assert (
            conn.execute("SELECT status FROM remediation_tasks WHERE kind='detail'").fetchone()[0]
            == "not_observed"
        )
    assert len(calls) == 5


def test_inherited_owner_requires_that_exact_open_description(setup, monkeypatch):
    import fcntl
    from jobagg.remediation_worker import shared_owner

    worker, replies, calls, tmp = setup
    with worker.shared_lock.open("a+") as unlocked:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys,fcntl; f=open(sys.argv[1],'a+'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read()",
                str(worker.shared_lock),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout.readline().strip() == "ready"
            monkeypatch.setenv("JOBAGG_SHARED_LOCK_FD", str(unlocked.fileno()))
            with pytest.raises(BlockingIOError):
                with shared_owner(worker.shared_lock):
                    pytest.fail("unlocked descriptor borrowed another process ownership")
        finally:
            process.communicate("", timeout=5)
        fcntl.flock(unlocked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with shared_owner(worker.shared_lock) as fd:
            assert fd == unlocked.fileno()


def test_rejects_live_nonworkspace_directory_and_registry_drift(setup):
    worker, replies, calls, tmp = setup
    worker.workspace.mkdir()
    (worker.workspace / "live.sqlite3").write_bytes(b"not ours")
    with pytest.raises(ValueError, match="Nonempty directory"):
        worker.tick(execute=True)
    (worker.workspace / "live.sqlite3").unlink()
    worker.initialize()
    worker.registry.write_text(worker.registry.read_text() + "\n# changed\n")
    changed = Worker(
        registry=worker.registry,
        robots=worker.robots_path,
        workspace=worker.workspace,
        shared_lock=worker.shared_lock,
    )
    with pytest.raises(ValueError, match="drift"):
        changed.preview()


def test_dispatcher_request_and_real_incomplete_report_protocol(setup):
    worker, replies, calls, tmp = setup
    manifest = tmp / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {"source_id": "demo_workday", "enabled": True},
                    {"source_id": "disabled_example", "enabled": False},
                ],
            }
        )
    )
    report = tmp / "acceptance.json"
    request_path = tmp / "request.json"
    now = datetime.now(UTC)
    request = {
        "schema_version": 1,
        "run_id": "test-tick",
        "started_at": now.isoformat(),
        "deadline_at": datetime.fromtimestamp(time.time() + 300, UTC).isoformat(),
        "source_manifest_path": str(manifest),
        "source_manifest_sha256": sha(manifest),
        "source_registry": {"path": str(worker.registry), "sha256": sha(worker.registry)},
        "expected_source_ids": ["demo_workday"],
        "report_path": str(report),
        "freshness_limits": {"listing_max_age_seconds": 3600, "detail_max_age_seconds": 86400},
    }
    request_path.write_text(json.dumps(request))
    validated = validate_request(request_path, report, worker.registry)
    result = worker.tick(execute=True, request=validated)
    report.write_text(dump(result))
    root = Path(__file__).resolve().parents[3]
    module_path = root / "reports/jobagg-remediation-plan-2026-09-10/proposed_runner/runner.py"
    spec = importlib.util.spec_from_file_location("prototype_outer_contract", module_path)
    outer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(outer)
    status, problems = outer.validate_report(report, request, datetime.now(UTC))
    assert status == "incomplete" and problems


def test_cli_default_preview_runs_without_worker_side_effects(setup):
    worker, replies, calls, tmp = setup
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "jobagg.remediation_worker",
            "--registry",
            str(worker.registry),
            "--robots",
            str(worker.robots_path),
            "--workspace",
            str(worker.workspace),
            "--shared-lock",
            str(worker.shared_lock),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["status"] == "dry_run"
    assert not worker.workspace.exists() and not worker.shared_lock.exists()


def test_pageup_empty_fallback_never_constructs_unguarded_stateless_client(setup, monkeypatch):
    worker, replies, calls, tmp = setup
    from jobagg.models import OrganizationSource
    from jobagg.pipelines.sync_source import register_builtin_adapters
    from jobagg.adapters import pageup

    register_builtin_adapters()
    worker.initialize()
    source = OrganizationSource(
        "unicef_pageup", "UNICEF", "pageup", "https://jobs.unicef.org/en-us/listing/"
    )
    url = "https://jobs.unicef.org/en-us/job/123/public-notice"
    replies[url] = b""
    monkeypatch.setattr(
        pageup, "JobAggHTTPClient", lambda **kwargs: pytest.fail("unguarded client constructed")
    )
    adapter, client, capture = worker.context(
        source, tmp / "capture", {"kind": "detail", "external_id": "123"}, time.time() + 300
    )
    with pytest.raises(ValueError, match="no unguarded"):
        adapter.fetch_detail_for_listing_item({"_pageup_detail_url": url})
    assert len(calls) == 2
    assert len(list((tmp / "capture/http").glob("*.json"))) == 2


def test_queue_dispatch_keeps_exact_frame_after_existing_detail_merge(setup):
    from jobagg.remediation_worker import frame_listing

    worker, _, calls, _ = setup
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=0")
    worker.max_tasks = 1
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
    payload = json.loads(task["payload"])
    listing = frame_listing(payload, "demo_workday", "R1")
    assert listing.raw["externalPath"] == "/job/City/Analyst_R1"
    assert "jobPostingInfo" not in listing.raw
    assert len(calls) == 3


@pytest.mark.parametrize("field", ["department", "employment_type"])
def test_absent_metadata_retention_does_not_reject_complete_fresh_body(setup, field):
    from jobagg.remediation_worker import record

    worker, replies, _, _ = setup
    detail = next(v for v in replies.values() if "jobPostingInfo" in v)
    detail["jobPostingInfo"].pop("timeType", None)
    worker.max_tasks = 1
    worker.tick(execute=True)
    prior = record(worker.db.get_job("demo_workday:R1"))
    setattr(prior, field, "Previously observed value")
    worker.db.upsert_job(prior)
    worker.max_tasks = 2
    report = worker.tick(execute=True)
    row = worker.db.get_job("demo_workday:R1")
    assert "Qualifications" in row["description"]
    assert row[field] == "Previously observed value"
    assert report["completeness_certified"] is False
    proof = row["raw"]["_deterministic_fetch_observation"]
    assert proof["retained_metadata_fields"][field]["parsed_value"] is None
    assert proof["required_public_metadata_verified"] is False
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
        assert task["status"] == "done"
        receipt = json.loads(task["receipt"])
    artifact = json.loads(Path(receipt["detail_path"]).read_text())
    assert artifact["proof"] == proof
    assert artifact["job"][field] == row[field]
    assert artifact["parsed_job"][field] is None


def test_queue_payload_tamper_fails_before_any_detail_http(setup):
    worker, _, calls, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
        payload = json.loads(task["payload"])
        payload["listing"]["raw"]["externalPath"] = "/job/Wrong/Other"
        conn.execute(
            "UPDATE remediation_tasks SET payload=? WHERE task_id=?",
            (dump(payload), task["task_id"]),
        )
    result = worker.tick(execute=True)
    assert result["tick_outcomes"][0]["status"] == "blocked"
    assert "exact original frame" in result["tick_outcomes"][0]["reason"]
    assert len(calls) == 1


def test_actual_readback_mismatch_still_rolls_back_detail(setup, monkeypatch):
    worker, _, _, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    original = worker.db.get_job

    def corrupted_readback(key):
        row = original(key)
        row["department"] = "Unexpected storage value"
        return row

    monkeypatch.setattr(worker.db, "get_job", corrupted_readback)
    report = worker.tick(execute=True)
    assert "Atomic detail readback differs" in report["tick_outcomes"][0]["reason"]
    assert "Qualifications" not in (original("demo_workday:R1")["description"] or "")
    with worker.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM remediation_observations").fetchone()[0] == 0


def test_one_guarded_timeout_remains_pending_and_later_retries_without_refund(setup, monkeypatch):
    worker, replies, calls, _ = setup
    url = next(url for url in replies if not url.endswith("/jobs"))
    good = replies[url]
    replies[url] = TimeoutError("Temporary fixture transport timeout")
    result = worker.tick(execute=True)
    assert result["tick_outcomes"][-1]["status"] == "pending"
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
    retry = json.loads(task["receipt"])["retry_decision"]
    assert retry["category"] == "guarded_transient_transport"
    assert task["attempts"] == 1
    assert worker.host_state("demo.example")["stopped"] is False
    old_events = list(worker.shared_policy.events("demo_workday"))
    replies[url] = good
    future = task["eligible_at"] + 1
    monkeypatch.setattr(time, "time", lambda: future)
    worker.tick(execute=True)
    assert "Qualifications" in worker.db.get_job("demo_workday:R1")["description"]
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
    assert task["status"] == "done" and task["attempts"] == 2
    assert len(worker.shared_policy.events("demo_workday")) == len(old_events) + 1
    assert len(calls) == 3


def test_tls_failure_never_becomes_automatic_transport_retry(setup):
    import ssl

    worker, replies, _, _ = setup
    url = next(url for url in replies if not url.endswith("/jobs"))
    replies[url] = ssl.SSLCertVerificationError("Unverified test certificate")
    report = worker.tick(execute=True)
    assert report["tick_outcomes"][-1]["status"] == "blocked"
    with worker.db.connect() as conn:
        task = conn.execute("SELECT receipt FROM remediation_tasks WHERE kind='detail'").fetchone()
    assert json.loads(task["receipt"])["retry_decision"] is None


def test_not_required_history_does_not_exhaust_current_document_dispatch_cap(setup):
    worker, _, calls, _ = setup
    worker.initialize()
    with worker.db.connection_scope() as conn:
        for n in range(100):
            worker.enqueue_document(
                conn,
                "demo_workday",
                {
                    "attachment_id": f"nav-{n}",
                    "job_key": "demo_workday:R1",
                    "url": f"https://demo.example/nav-{n}.pdf",
                },
            )
        conn.execute("UPDATE remediation_tasks SET status='not_required' WHERE kind='document'")
        key = worker.enqueue_document(
            conn,
            "demo_workday",
            {
                "attachment_id": "actual-tor",
                "job_key": "demo_workday:R1",
                "url": "https://demo.example/tor.pdf",
            },
        )
        assert (
            conn.execute("SELECT status FROM remediation_tasks WHERE task_id=?", (key,)).fetchone()[
                0
            ]
            == "pending"
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM remediation_tasks WHERE status='not_required'"
            ).fetchone()[0]
            == 100
        )
    assert not calls


def test_browser_receipt_binds_both_rendered_text_and_html(tmp_path):
    from types import SimpleNamespace
    from jobagg.remediation_worker import browser_receipt

    html = tmp_path / "rendered.html"
    html.write_text("<p>Public prose</p>")
    text = tmp_path / "public_text.txt"
    text.write_text("Public prose")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        dump(
            {
                "html_path": str(html),
                "html_sha256": sha(html),
                "text_path": str(text),
                "text_sha256": sha(text),
            }
        )
    )
    capture = SimpleNamespace(browser_renderer=SimpleNamespace(last_receipt=receipt))
    assert browser_receipt(capture) == {"path": str(receipt), "sha256": sha(receipt)}
    text.write_text("Changed text")
    with pytest.raises(ValueError, match="evidence bytes changed"):
        browser_receipt(capture)
