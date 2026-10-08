"""A reviewed incident release cannot grant unrelated fresh retry budgets."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from jobagg.adapters.idb_api import IDBInventoryChanged
from jobagg import remediation_worker as runtime
from jobagg.remediation_worker import (
    RETRY_EQUIVALENT_IMPLEMENTATIONS, Worker, dump, implementation_hash, reviewed_retry_successor,
)
from jobagg.vacancy_outcomes import IncompleteDetailResponse
import test_remediation_worker as fixtures


PREDECESSOR = RETRY_EQUIVALENT_IMPLEMENTATIONS[0]
setup = fixtures.setup


@pytest.fixture(autouse=True, params=RETRY_EQUIVALENT_IMPLEMENTATIONS)
def deployed_predecessor(request, monkeypatch):
    # Both legacy receipts and receipts produced after the incident rollout
    # must retain their retry budgets through this inventory-only successor.
    monkeypatch.setitem(globals(), "PREDECESSOR", request.param)


def comparison_worker():
    worker = object.__new__(Worker)
    worker.binding = {
        "version": "deterministic-fetch-v1", "implementation_sha256": implementation_hash(),
        "registry_sha256": "registry", "robots_sha256": "robots", "shared_lock": "/owner",
    }
    worker._retry_compatible_successor = reviewed_retry_successor()
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
    assert worker.binding["implementation_sha256"] == implementation_hash()


def test_unrelated_successor_cannot_inherit_predecessor_equivalence():
    worker = comparison_worker()
    payload = {"listing": {"source_url": "https://example.org/job/1"}}
    saved = old_digest(worker, payload)
    worker.binding["implementation_sha256"] = "unrelated-later-implementation"
    assert not worker.retry_input_matches(saved, payload)
    task = {"payload": dump(payload), "receipt": dump({
        "retry_input_sha256": saved, "incomplete_response_count": 3, "inventory_change_count": 3,
    })}
    assert worker.incomplete_response_count(task) == worker.inventory_change_count(task) == 0


@pytest.fixture
def package_copy(tmp_path, monkeypatch):
    original = Path(runtime.__file__).resolve().parent
    root = tmp_path / "jobagg"
    for path in original.rglob("*.py"):
        target = root / path.relative_to(original)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    monkeypatch.setattr(runtime, "__file__", str(root / "remediation_worker.py"))
    return root


@pytest.mark.parametrize("change", ["edit", "add", "remove", "rename", "literal", "missing_literal", "duplicate_literal"])
def test_later_package_changes_require_an_explicit_new_compatibility_scope(package_copy, change):
    worker = comparison_worker()
    assert worker._retry_compatible_successor == implementation_hash()
    payload = {"listing": {"source_url": "https://example.org/job/1"}}
    saved = old_digest(worker, payload)
    module = package_copy / "vacancy_outcomes.py"
    if change == "edit":
        module.write_bytes(module.read_bytes() + b"\n# Later parser change.\n")
    elif change == "add":
        (package_copy / "future_module.py").write_text("# Later implementation.\n")
    elif change == "remove":
        module.unlink()
    elif change == "rename":
        module.rename(package_copy / "renamed_outcomes.py")
    else:
        module = package_copy / "remediation_worker.py"
        declaration = 'RETRY_SUCCESSOR_SCOPE_SHA256 = "' + runtime.RETRY_SUCCESSOR_SCOPE_SHA256 + '"'
        text = module.read_text()
        if change == "literal":
            text = text.replace(declaration, 'RETRY_SUCCESSOR_SCOPE_SHA256 = "' + "1" * 64 + '"')
        elif change == "missing_literal":
            text = text.replace(declaration, "")
        else:
            text += "\n# " + declaration + "\n"
        module.write_text(text)
    assert implementation_hash() != worker.binding["implementation_sha256"]
    assert reviewed_retry_successor() is None
    later = comparison_worker()
    assert not later.retry_input_matches(saved, payload)
    receipt = {"retry_input_sha256": saved, "incomplete_response_count": 3, "inventory_change_count": 3}
    task = {"payload": dump(payload), "receipt": dump(receipt)}
    assert later.incomplete_response_count(task) == later.inventory_change_count(task) == 0
    # A receipt genuinely produced by the later code still retains its own counts.
    receipt["retry_input_sha256"] = later.retry_input_fingerprint(payload)
    task["receipt"] = dump(receipt)
    assert later.incomplete_response_count(task) == later.inventory_change_count(task) == 3


def test_scope_cannot_bless_a_hash_from_a_later_file_snapshot(package_copy, monkeypatch):
    before = implementation_hash()
    paths = sorted(package_copy.rglob("*.py"))
    first, last = paths[0], paths[-1]
    read = Path.read_bytes

    def mutate_after_last_read(path):
        content = read(path)
        if path == last:
            first.write_bytes(read(first) + b"\n# Changed after captured scope snapshot.\n")
        return content

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", mutate_after_last_read)
        successor = reviewed_retry_successor()
    assert successor == before
    assert successor != implementation_hash()
    assert reviewed_retry_successor() is None


@pytest.mark.parametrize("status", ["blocked", "dead_letter"])
def test_later_reseal_reopens_predecessor_failure_without_resetting_history(setup, status):
    worker, _, _, _ = setup
    worker.max_tasks = 1
    worker.tick(execute=True)
    with worker.db.connection_scope() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
        payload = json.loads(task["payload"])
        receipt = {"retry_input_sha256": old_digest(worker, payload), "incomplete_response_count": 3}
        conn.execute("UPDATE remediation_tasks SET status=?,receipt=?,last_error=?,attempts=7 WHERE task_id=?",
                     (status, dump(receipt), "Retained failure", task["task_id"]))
        before = dict(conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (task["task_id"],)).fetchone())
        # Simulate an explicitly resealed future release against the cached approved successor.
        worker.binding["implementation_sha256"] = "future-reviewed-release"
        worker.enqueue(conn, task["source_id"], "detail", task["external_id"], payload, refresh=True)
        after = dict(conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (task["task_id"],)).fetchone())
    assert after["status"] == "pending"
    assert worker.incomplete_response_count(after) == 0
    for key in ("attempts", "claim", "receipt", "last_error", "discovered_at"):
        assert after[key] == before[key]


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
