"""A reviewed incident release cannot grant unrelated fresh retry budgets."""
from copy import deepcopy
import json

import pytest

from jobagg.adapters.idb_api import IDBInventoryChanged
from jobagg.remediation_worker import RETRY_EQUIVALENT_IMPLEMENTATIONS, Worker, dump
from jobagg.vacancy_outcomes import IncompleteDetailResponse
import test_remediation_worker as fixtures


PREDECESSOR = RETRY_EQUIVALENT_IMPLEMENTATIONS[0]
setup = fixtures.setup


def comparison_worker():
    worker = object.__new__(Worker)
    worker.binding = {
        "version": "deterministic-fetch-v1", "implementation_sha256": "candidate",
        "registry_sha256": "registry", "robots_sha256": "robots", "shared_lock": "/owner",
    }
    return worker


def old_digest(worker, payload):
    return worker.retry_input_fingerprint(payload, implementation_sha256=PREDECESSOR)


def test_only_exact_reviewed_predecessor_is_retry_equivalent():
    worker = comparison_worker()
    payload = {"listing": {"source_url": "https://example.org/job/1", "title": "Analyst"}}
    saved = old_digest(worker, payload)
    assert saved != worker.retry_input_fingerprint(payload)
    assert worker.retry_input_matches(saved, payload)
    assert not worker.retry_input_matches(None, payload)
    arbitrary = worker.retry_input_fingerprint(payload, implementation_sha256="unreviewed-old-code")
    assert not worker.retry_input_matches(arbitrary, payload)
    assert worker.binding["implementation_sha256"] == "candidate"


@pytest.mark.parametrize("field", ["version", "registry_sha256", "robots_sha256", "shared_lock"])
def test_retry_equivalence_does_not_alias_other_binding_changes(field):
    worker = comparison_worker()
    payload = {"listing": {"source_url": "https://example.org/job/1"}}
    saved = old_digest(worker, payload)
    worker.binding[field] = "changed-" + field
    assert not worker.retry_input_matches(saved, payload)


@pytest.mark.parametrize("field", ["source_url", "title", "external_id"])
def test_actual_semantic_input_change_is_not_aliased(field):
    worker = comparison_worker()
    payload = {"listing": {"source_url": "https://example.org/job/1", "title": "Role", "external_id": "1"}}
    saved = old_digest(worker, payload)
    payload["listing"][field] = "changed"
    assert not worker.retry_input_matches(saved, payload)


@pytest.mark.parametrize("status", ["blocked", "dead_letter"])
def test_reseal_and_new_frame_preserve_entire_unselected_task_and_policy(setup, status):
    worker, _, calls, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    with worker.db.connection_scope() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
        payload = json.loads(task["payload"])
        receipt = {"retry_input_sha256": old_digest(worker, payload), "incomplete_response_count": 3}
        conn.execute("UPDATE remediation_tasks SET status=?,receipt=?,last_error=?,attempts=7,eligible_at=12345 WHERE task_id=?",
                     (status, dump(receipt), "Retained failure", task["task_id"]))
        before = dict(conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (task["task_id"],)).fetchone())
        policy = {str(p): p.read_bytes() for p in worker.shared_policy.root.rglob("*") if p.is_file()}
        new_frame = deepcopy(payload)
        new_frame.update(frame_path="/fresh/listing.json", frame_sha256="fresh")
        new_frame["listing"]["last_seen_at"] = "2026-10-07T12:00:00+00:00"
        worker.enqueue(conn, task["source_id"], "detail", task["external_id"], new_frame, refresh=True)
        after = dict(conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (task["task_id"],)).fetchone())
    assert after == before
    assert policy == {str(p): p.read_bytes() for p in worker.shared_policy.root.rglob("*") if p.is_file()}
    assert len(calls) == 1
    with worker.db.connection_scope() as conn:
        new_frame["listing"]["source_url"] = "https://demo.example/corrected/1"
        worker.enqueue(conn, task["source_id"], "detail", task["external_id"], new_frame, refresh=True)
        changed = dict(conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (task["task_id"],)).fetchone())
    assert changed["status"] == "pending"
    for field in ("attempts", "claim", "receipt", "last_error", "discovered_at"):
        assert changed[field] == before[field]


@pytest.mark.parametrize("kind,counter,error", [
    ("detail", "incomplete_response_count", IncompleteDetailResponse("No native identity")),
    ("listing", "inventory_change_count", IDBInventoryChanged("Changed total")),
])
def test_reseal_retains_semantic_counts_and_exhaustion(tmp_path, kind, counter, error):
    worker = comparison_worker()
    payload = {"listing": {"source_url": "https://example.org/job/1"}} if kind == "detail" else {}
    receipt = {"retry_input_sha256": old_digest(worker, payload), counter: 2}
    task = {"payload": dump(payload), "receipt": dump(receipt), "kind": kind}
    reader = worker.incomplete_response_count if kind == "detail" else worker.inventory_change_count
    assert reader(task) == 2
    assert worker.retry_after_error(task, tmp_path, error) is None
    # A restarted worker still recognizes the reviewed predecessor's counter.
    assert getattr(comparison_worker(), reader.__name__)(task) == 2


def test_new_failure_receipts_keep_current_full_binding(setup):
    worker, replies, _, _ = setup
    detail = next(x for x in replies.values() if "jobPostingInfo" in x)
    detail["jobPostingInfo"]["jobReqId"] = "OTHER"
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
    receipt = json.loads(task["receipt"])
    payload = json.loads(task["payload"])
    assert receipt["retry_input_sha256"] == worker.retry_input_fingerprint(payload)
    assert receipt["retry_input_sha256"] != old_digest(worker, payload)
