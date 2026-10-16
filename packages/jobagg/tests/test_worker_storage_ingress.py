"""Small isolated no-network storage admission and preservation regressions."""
from contextlib import contextmanager
from datetime import datetime, UTC, timedelta
import gzip
import hashlib
import io
import json
import sqlite3

import pytest

from jobagg.atomic_files import admitted_file_writes, atomic_write_bytes, atomic_write_text
from jobagg.http import JobAggHTTPClient, ResponseTooLargeError
from jobagg.remediation_worker import WorkerStorage
from jobagg.storage_budget import BudgetLedger, CONTROL_ENVELOPE_BYTES, TOTAL_LIMIT, assess, inventory, parse_budget_config, StorageBudgetDeferred
from test_http import FakeHeaders
from test_remediation_worker import setup


class Stream:
    def __init__(self, payload, headers=None, status=200):
        self.body = io.BytesIO(payload)
        self.headers = FakeHeaders(headers or {})
        self.status, self.reads, self.closed = status, [], False
    def read(self, size=-1):
        self.reads.append(size)
        return self.body.read(size)
    def geturl(self):
        return "https://example.org/body?token=secret"
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.closed = True


def native_client(stream, cap):
    client = JobAggHTTPClient(max_retries=0, max_response_bytes=cap)
    class Opener:
        def open(self, *args, **kwargs):
            return stream
    client._opener = Opener()
    return client


def test_native_overflow_reads_only_cap_plus_one_and_keeps_exact_prefix():
    stream = Stream(b"123456789")
    with pytest.raises(ResponseTooLargeError) as raised:
        native_client(stream, 4).get(stream.geturl())
    exc = raised.value
    assert stream.reads == [5] and stream.closed
    assert exc.prefix == b"1234" and exc.observed_bytes == 5
    assert exc.status_code == 200 and exc.limit_bytes == 4
    assert exc.wire_complete is False and exc.response is None


def test_declared_oversize_does_not_read_body():
    stream = Stream(b"123456789", {"Content-Length": "9"})
    with pytest.raises(ResponseTooLargeError) as raised:
        native_client(stream, 4).get(stream.geturl())
    assert stream.reads == [] and stream.closed
    assert raised.value.prefix == b"" and raised.value.declared_content_length == 9


def test_decoded_overflow_preserves_complete_wire_not_complete_decoded_body():
    payload = gzip.compress(b"a" * 4096)
    stream = Stream(payload, {"Content-Encoding": "gzip"})
    with pytest.raises(ResponseTooLargeError) as raised:
        native_client(stream, 64).get(stream.geturl())
    assert raised.value.prefix == payload and raised.value.wire_complete is True
    assert raised.value.termination_reason == "decoded_body_limit"


def test_atomic_guard_denies_before_creation_and_counts_encoded_bytes(tmp_path):
    calls = []
    @contextmanager
    def guard(path, count):
        calls.append((path, count))
        if count > 3:
            raise StorageBudgetDeferred("fixture_limit")
        yield
    target = tmp_path / "new" / "file"
    with admitted_file_writes(guard):
        with pytest.raises(StorageBudgetDeferred):
            atomic_write_text(target, "éé")
        assert not target.parent.exists()
        atomic_write_bytes(target, b"\r\n\x00")
    assert target.read_bytes() == b"\r\n\x00"
    assert [count for _, count in calls] == [4, 3]
    atomic_write_text(target, "outside scope")
    assert target.read_text() == "outside scope"


def enable_storage(setup, monkeypatch, **changes):
    worker, replies, calls, tmp = setup
    worker.max_tasks = 1
    worker.initialize()
    diagnostics = tmp / "budget-control"
    diagnostics.mkdir()
    config = parse_budget_config({
        "enabled": True,
        "roots": [{"name": "worker", "path": str(tmp), "category": "essential", "writers": ["controller", "worker"]},
                  {"name": "ledger", "path": str(diagnostics), "category": "diagnostics", "writers": ["controller", "worker"]}],
        "writer_roles": {"controller": "controller", "worker": "worker"},
        "ledger_path": str(diagnostics / "ledger.json"),
    })
    limits = {"ingress_cap_bytes": 2 * 1024**2, "accepted_cap_bytes": 2 * 1024**2,
              "database_growth_cap_bytes": 64 * 1024**2, "control_cap_bytes": 4 * 1024**2,
              "response_cap_bytes": 65536, "scratch_dir": str(tmp), **changes}
    observed = inventory(config)
    decision = assess(config, observed, projected_growth={"essential": 90 * 1024**2, "publication": 0,
        "evidence": 0, "work": 0, "diagnostics": CONTROL_ENVELOPE_BYTES},
        physical_free_bytes={device: TOTAL_LIMIT * 2 for device in observed.device_charged_bytes})
    assert decision.ready, decision.reasons
    ledger = BudgetLedger.create(decision, cycle_id="storage-fixture")
    monkeypatch.setenv("JOBAGG_STORAGE_BUDGET_LEASE", json.dumps(ledger.for_writer("worker").wire()))
    monkeypatch.setenv("JOBAGG_STORAGE_CYCLE_LIMITS", json.dumps(limits))
    request = {"run_id": "storage-fixture", "storage_limits": limits,
               "source_manifest_sha256": worker.binding["registry_sha256"],
               "deadline_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
               "semantic_history_policy": {"policy_epoch": "fixture-epoch", "max_history_bytes": 32 * 1024**2}}
    return worker, replies, calls, tmp, request, ledger


def query(worker, sql):
    with sqlite3.connect(worker.db.path.as_uri() + "?mode=ro", uri=True) as conn:
        return conn.execute(sql).fetchall()


def test_storage_success_keeps_complete_capture_and_fenced_database(setup, monkeypatch):
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch)
    result = worker.tick(execute=True, request=request)
    assert result["tick_outcomes"][0]["independent_enumeration"] is True
    assert result["storage"]["physical_hard_cap_enforced"] is False
    assert query(worker, "SELECT policy_epoch FROM semantic_history_state") == [("fixture-epoch",)]
    captures = list((worker.workspace / "captures").glob("*/http/*.json"))
    assert captures and all(json.loads(p.read_text())["body_complete"] for p in captures)
    assert not ledger.snapshot()["operations"]


def test_partial_is_storage_held_not_accepted_and_never_ordinary_refreshed(setup, monkeypatch):
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch, response_cap_bytes=64)
    result = worker.tick(execute=True, request=request)
    assert result["tick_outcomes"][0]["storage_outcome"] == "partial_ingress_storage_held"
    assert query(worker, "SELECT status,attempts FROM remediation_tasks") == [("storage_held", 1)]
    assert query(worker, "SELECT status FROM remediation_attempts") == [("partial_ingress_storage_held",)]
    assert query(worker, "SELECT count(*) FROM jobs") == [(0,)]
    path = next((worker.workspace / "captures").glob("*/http/*.json"))
    receipt = json.loads(path.read_text())
    prefix = gzip.decompress(__import__("pathlib").Path(receipt["partial_artifact"]).read_bytes())
    assert len(prefix) == 64 and receipt["retained_prefix_sha256"] == hashlib.sha256(prefix).hexdigest()
    assert receipt["body_captured"] is False and receipt["body_complete"] is False
    assert receipt["sha256_scope"] == "retained_prefix" and "body_sha256" not in receipt
    host = json.loads(next((worker.shared_policy.root / "hosts").glob("*.json")).read_text())
    assert not host.get("stopped") and not host.get("recovery")
    # Even a refreshed listing payload cannot reopen this storage hold.
    with worker.db.connection_scope() as conn:
        worker.enqueue(conn, "demo_workday", "listing", "", {"changed": True}, refresh=True)
    assert query(worker, "SELECT status,attempts FROM remediation_tasks") == [("storage_held", 1)]
    assert worker.choose() is None and len(calls) == 1
    health = result["sources"][0]["health"]["listing"]
    assert health["next_permitted_attempt_at"] is None and not health["eligible"]
    assert "listing_storage_held" in health["reasons"]


def test_acceptance_shortage_keeps_full_capture_without_semantic_rows(setup, monkeypatch):
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch, accepted_cap_bytes=16)
    result = worker.tick(execute=True, request=request)
    assert result["tick_outcomes"][0]["storage_outcome"] == "complete_capture_storage_held"
    assert query(worker, "SELECT count(*) FROM jobs") == [(0,)]
    meta = json.loads(next((worker.workspace / "captures").glob("*/http/*.json")).read_text())
    assert meta["body_complete"] and meta["body_captured"] and not meta["storage_limited"]
    assert len(calls) == 1


def test_tiny_ingress_defers_before_claim_or_provider(setup, monkeypatch):
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch, ingress_cap_bytes=100)
    result = worker.tick(execute=True, request=request)
    assert result["tick_stop_reason"] == "storage_deferred"
    assert query(worker, "SELECT count(*) FROM remediation_attempts") == [(0,)]
    assert calls == []


def test_global_claim_shortage_cannot_charge_shared_attempt(setup, monkeypatch):
    import time
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch)
    worker.storage = WorkerStorage(worker, ledger.for_writer("worker").wire(), request["storage_limits"])
    worker.storage.activate(request["semantic_history_policy"])
    with admitted_file_writes(worker.storage.file_write):
        worker.seed_listings()
        task = worker.choose()
        before = ledger.remaining_envelope()["essential"]
        worker.storage.preclaim(task)
        complete_cost = before - ledger.remaining_envelope()["essential"]
        claim_grant = worker.storage.completion[task["task_id"]].get("claim_database")
        other_cost = complete_cost - (claim_grant.maximum if claim_grant else 0)
        with sqlite3.connect(worker.db.path.as_uri() + "?mode=ro", uri=True) as conn:
            _, claim_bound = worker.storage.database_bound(conn)
        worker.storage.release_task(task["task_id"])
        # Enough for ingress/hold/control and half the claim transaction: the
        # missing global claim allowance must fail before shared quota history.
        keep = other_cost + claim_bound // 2
        occupied = ledger.reserve("essential", ledger.remaining_envelope()["essential"] - keep,
                                  path=worker.workspace, operation_id="other-admitted-work")
        policy_before = {path: path.read_bytes() for path in worker.shared_policy.root.rglob("*.json")}
        try:
            with pytest.raises(StorageBudgetDeferred):
                worker.admit(task, time.time() + 120)
            assert query(worker, "SELECT count(*) FROM remediation_attempts") == [(0,)]
            assert query(worker, "SELECT status,attempts FROM remediation_tasks") == [("pending", 0)]
            assert {path: path.read_bytes() for path in worker.shared_policy.root.rglob("*.json")} == policy_before
            assert set(ledger.snapshot()["operations"]) == {"other-admitted-work"}
            assert calls == []
        finally:
            occupied.settle(0)


def test_browser_fails_closed_before_claim_and_provider(setup, monkeypatch):
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch)
    worker.by_id["demo_workday"].extra["browser_render"] = {"transport": "native"}
    result = worker.tick(execute=True, request=request)
    assert result["tick_stop_reason"] == "storage_deferred" and not calls
    assert query(worker, "SELECT count(*) FROM remediation_attempts") == [(0,)]


def test_database_page_limit_rolls_back_and_does_not_consume_prior_rows(setup, monkeypatch):
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch)
    worker.storage = WorkerStorage(worker, ledger.for_writer("worker").wire(), request["storage_limits"])
    worker.storage.activate(request["semantic_history_policy"])
    before = query(worker, "SELECT source_id,last_service FROM remediation_sources ORDER BY source_id")
    with pytest.raises(StorageBudgetDeferred, match="database_page_envelope"):
        with worker.db.connection_scope() as conn:
            conn.execute("UPDATE remediation_sources SET listing_proof=zeroblob(?)", (8 * 1024**2,))
    assert query(worker, "SELECT source_id,last_service FROM remediation_sources ORDER BY source_id") == before
    assert query(worker, "SELECT listing_proof FROM remediation_sources") == [(None,)]
    assert worker.storage.used["database"] <= request["storage_limits"]["database_growth_cap_bytes"]
    assert not ledger.snapshot()["operations"] and calls == []


def test_transaction_cannot_disable_bounds_or_commit_twice(setup, monkeypatch):
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch)
    worker.storage = WorkerStorage(worker, ledger.for_writer("worker").wire(), request["storage_limits"])
    worker.storage.activate(request["semantic_history_policy"])
    for sql in ("PRAGMA cache_spill=ON", "PRAGMA max_page_count=1000000", "COMMIT"):
        with pytest.raises(sqlite3.DatabaseError):
            with worker.db.connection_scope() as conn:
                conn.execute("UPDATE remediation_sources SET last_service=100")
                conn.execute(sql)
        assert query(worker, "SELECT last_service FROM remediation_sources") == [(0.0,)]


def test_missing_or_mismatched_lease_refuses_before_mutation(setup, monkeypatch):
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch)
    request["storage_limits"] = {**request["storage_limits"], "response_cap_bytes": 1}
    with pytest.raises(ValueError, match="binding_differs"):
        worker.tick(execute=True, request=request)
    assert query(worker, "SELECT count(*) FROM remediation_attempts") == [(0,)] and not calls
    assert not query(worker, "SELECT name FROM sqlite_master WHERE name='semantic_history_state'") or query(worker, "SELECT policy_epoch FROM semantic_history_state") == [(None,)]


def test_redirect_and_robots_each_require_new_admission(tmp_path, monkeypatch):
    import urllib.request
    from jobagg.pipelines.http_checkpoint import RedirectHop
    from test_http_checkpoint import capture, response
    calls, reservations = [], []
    class Grant:
        body_limit = 16
    class Storage:
        def ensure_directory(self, path, **kwargs):
            path.mkdir(parents=True, exist_ok=True)
        def bind_request(self, path, grant=None, *, host):
            reservations.append(path.name)
            if len(reservations) > 2:
                raise StorageBudgetDeferred("fixture_cycle_exhausted")
            return Grant()
        def release_request(self, *args):
            pass
        def charge_file(self, *args):
            pass
    def transport(url, **kwargs):
        calls.append(url)
        if url.endswith("robots.txt"):
            return response(url, "")
        raise RedirectHop(urllib.request.Request("https://two.example/job"), 302)
    guarded = capture(tmp_path, monkeypatch, transport, robots=True, storage=Storage())
    with pytest.raises(StorageBudgetDeferred):
        guarded.request("https://one.example/job", method="GET")
    assert calls == ["https://one.example/robots.txt", "https://one.example/job"]
    assert len(reservations) == 3  # Destination robots admission failed before HTTP.


def test_request_reservations_compete_atomically_for_finite_cycle(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch,
        ingress_cap_bytes=WorkerStorage.REQUEST_OVERHEAD + 4096, response_cap_bytes=1024)
    budget = WorkerStorage(worker, ledger.for_writer("worker").wire(), request["storage_limits"])
    def reserve():
        try:
            return budget.request_grant()
        except StorageBudgetDeferred:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        grants = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(g is not None for g in grants) == 1
    for grant in grants:
        if grant:
            grant.close()
    assert not ledger.snapshot()["operations"] and calls == []


def test_prior_accepted_text_and_complete_captures_survive_new_partial(setup, monkeypatch):
    worker, replies, calls, tmp = setup
    worker.tick(execute=True)
    before_jobs = query(worker, "SELECT job_key,description,first_seen_at FROM jobs ORDER BY job_key")
    assert before_jobs and before_jobs[0][1]
    artifacts = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in (worker.workspace / "captures").rglob("*") if path.is_file()}
    with worker.db.connection_scope() as conn:
        conn.execute("UPDATE remediation_sources SET next_list_at=0")
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch, response_cap_bytes=64)
    result = worker.tick(execute=True, request=request)
    assert result["tick_outcomes"][0]["storage_outcome"] == "partial_ingress_storage_held"
    assert query(worker, "SELECT job_key,description,first_seen_at FROM jobs ORDER BY job_key") == before_jobs
    assert all(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
               for path, digest in artifacts.items())


def test_oversized_http_error_body_is_closed_and_keeps_prefix():
    import urllib.error
    payload = io.BytesIO(b"123456789")
    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 429, "rate limit", {}, payload)
    client = JobAggHTTPClient(max_retries=3, max_response_bytes=4)
    client._opener = Opener()
    with pytest.raises(ResponseTooLargeError) as raised:
        client.get("https://example.org/limit")
    assert raised.value.prefix == b"1234" and raised.value.status_code == 429
    assert payload.closed


def test_partial_preserves_retry_after_floor_without_new_host_failure(tmp_path, monkeypatch):
    import time
    from test_http_checkpoint import capture
    failure = ResponseTooLargeError("bounded", prefix=b"part", observed_bytes=5, limit_bytes=4)
    failure.status_code = 429
    failure.response_url = "https://one.example/job"
    failure.headers = {"Retry-After": "300"}
    def transport(*args, **kwargs):
        raise failure
    guarded = capture(tmp_path, monkeypatch, transport)
    before = time.time()
    with pytest.raises(ResponseTooLargeError):
        guarded.request(failure.response_url, method="GET")
    host = json.loads(next((tmp_path / "hosts").glob("*.json")).read_text())
    receipt = json.loads((tmp_path / "evidence/http/00001.json").read_text())
    assert host["eligible_at"] >= before + 300
    assert not host.get("recovery") and not host.get("consecutive_transport_failures")
    assert receipt["retry_after"] == "300" and receipt["failure_category"] == "storage_budget"


def test_admitted_detail_persists_complete_text_and_semantic_history(setup, monkeypatch):
    worker, replies, calls, tmp = setup
    worker.max_tasks = 1
    listing = worker.tick(execute=True)
    assert listing["tick_outcomes"][0]["independent_enumeration"]
    worker, replies, calls, tmp, request, ledger = enable_storage(setup, monkeypatch)
    report = worker.tick(execute=True, request=request)
    assert report["tick_outcomes"][0]["detail_stored"] == "demo_workday:R1"
    assert query(worker, "SELECT count(*) FROM remediation_observations") == [(1,)]
    assert query(worker, "SELECT count(*) FROM semantic_versions")[0][0] > 0
    assert "Conduct analysis and prepare complete reports." in query(worker, "SELECT description FROM jobs")[0][0]
    assert not ledger.snapshot()["operations"]
