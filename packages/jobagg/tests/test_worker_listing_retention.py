"""Listing summaries and dismissed document history cannot replace accepted detail."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

import pytest

from jobagg.remediation_worker import PUBLIC_FIELDS, dump, shared_owner
import test_remediation_worker as worker_fixtures

setup = worker_fixtures.setup


def refresh_listing(worker):
    # Exercise exactly the listing phase while an already-due detail remains
    # queued; scheduler fairness may legitimately choose that detail first.
    with shared_owner(worker.shared_lock):
        worker.initialize()
        with worker.db.connect() as conn:
            conn.execute("UPDATE remediation_sources SET next_list_at=0")
        worker.seed_listings()
        with worker.db.connect() as conn:
            task = dict(
                conn.execute("SELECT * FROM remediation_tasks WHERE kind='listing'").fetchone()
            )
        return {"tick_outcomes": [worker.perform(task, time.time() + 300)]}


def test_listing_preserves_accepted_fields_while_pending_then_new_detail_wins(setup):
    worker, replies, calls, _ = setup
    info = next(v["jobPostingInfo"] for v in replies.values() if "jobPostingInfo" in v)
    info.update(
        endDate="2026-10-01T15:59:00Z",
        department="Public Operations",
        externalUrl="https://demo.example/application/R1",
    )
    worker.tick(execute=True)
    accepted = worker.db.get_job("demo_workday:R1")
    assert accepted["closes_at"] == "2026-10-01T15:59:00+00:00"
    assert accepted["apply_url"] == "https://demo.example/application/R1"
    listing = next(v for v in replies.values() if "jobPostings" in v)
    listing["jobPostings"][0].update(title="New summary title", endDate="2026-10-01T00:00:00Z")
    listing["jobPostings"].append(
        {"title": "New job", "externalPath": "/job/City/New_R2", "bulletFields": ["R2"]}
    )
    listing["total"] = 2
    first_due = None
    for _ in range(2):
        before = time.time()
        result = refresh_listing(worker)
        assert result["tick_outcomes"][0].get("status") != "blocked"
        current = worker.db.get_job("demo_workday:R1")
        assert {f: current[f] for f in PUBLIC_FIELDS} == {f: accepted[f] for f in PUBLIC_FIELDS}
        assert worker.db.get_job("demo_workday:R2")["title"] == "New job"
        with worker.db.connect() as conn:
            task = dict(
                conn.execute(
                    "SELECT * FROM remediation_tasks WHERE kind='detail' AND external_id='R1'"
                ).fetchone()
            )
        assert task["status"] == "pending" and task["eligible_at"] <= before + 1
        if first_due is not None:
            assert task["eligible_at"] == first_due
        first_due = task["eligible_at"]
        path = Path(json.loads(task["payload"])["frame_path"])
        assert json.loads(path.read_text())["jobs"][0]["title"] == "New summary title"
        assert (
            json.loads(path.with_name("listing_retention.json").read_text())["retained_details"][0][
                "existing_metadata_drift"
            ]
            == []
        )
    assert len(calls) == 4
    info.update(title="New accepted title", endDate="2026-10-02T15:59:00Z")
    with worker.db.connect() as conn:
        conn.execute(
            "UPDATE remediation_tasks SET eligible_at=? WHERE kind='detail' AND external_id='R2'",
            (time.time() + 86400,),
        )
    worker.tick(execute=True)
    updated = worker.db.get_job("demo_workday:R1")
    assert updated["title"] == "New accepted title"
    assert updated["closes_at"] == "2026-10-02T15:59:00+00:00"


def test_existing_metadata_drift_audited_and_scheduled_not_silently_repaired(setup):
    worker, _, _, _ = setup
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET department='Earlier listing corruption' WHERE job_key='demo_workday:R1'"
        )
    refresh_listing(worker)
    assert worker.db.get_job("demo_workday:R1")["department"] == "Earlier listing corruption"
    with worker.db.connect() as conn:
        task = dict(conn.execute("SELECT * FROM remediation_tasks WHERE kind='detail'").fetchone())
    path = Path(json.loads(task["payload"])["frame_path"])
    audit = json.loads(path.with_name("listing_retention.json").read_text())
    assert audit["retained_details"][0]["existing_metadata_drift"] == ["department"]
    assert task["eligible_at"] <= time.time()


@pytest.mark.parametrize("tamper", ["body", "proof", "artifact", "capture", "attempt"])
def test_unbound_accepted_observation_stops_listing_write(setup, tamper):
    worker, replies, _, _ = setup
    worker.tick(execute=True)
    with worker.db.connect() as conn:
        attempt = dict(
            conn.execute("SELECT * FROM remediation_attempts WHERE kind='detail'").fetchone()
        )
        receipt = json.loads(attempt["evidence"])
        if tamper == "body":
            conn.execute("UPDATE jobs SET description='unbound body'")
        elif tamper == "proof":
            proof = json.loads(
                conn.execute("SELECT proof FROM remediation_observations").fetchone()[0]
            )
            proof["external_id"] = "OTHER"
            conn.execute("UPDATE remediation_observations SET proof=?", (dump(proof),))
        elif tamper == "attempt":
            conn.execute("UPDATE remediation_attempts SET status='blocked' WHERE kind='detail'")
        elif tamper == "artifact":
            Path(receipt["detail_path"]).write_text("{}")
        else:
            artifact = json.loads(Path(receipt["detail_path"]).read_text())
            meta = json.loads(Path(artifact["proof"]["captures"][0]["path"]).read_text())
            Path(meta["artifact"]).write_bytes(b"corrupted bytes")
    listing = next(v for v in replies.values() if "jobPostings" in v)
    listing["jobPostings"][0]["title"] = "Unsafe summary"
    result = refresh_listing(worker)
    assert result["tick_outcomes"][0]["status"] == "blocked"
    assert worker.db.get_job("demo_workday:R1")["title"] == "Analyst"


def payload():
    return {
        "attachment_id": "dismissed",
        "job_key": "demo_workday:R1",
        "url": "https://demo.example/history.pdf",
        "parent_description_sha256": "a" * 64,
        "classification": {
            "decision": "excluded",
            "reason": "site_navigation_link",
            "region": "navigation",
        },
        "depth": 0,
    }


@pytest.mark.parametrize(
    "change", ["none", "cosmetic", "content_replay", "body", "content", "declared"]
)
def test_dismissed_replay_stays_dismissed_new_evidence_reopens_without_refresh(setup, change):
    worker, _, calls, _ = setup
    worker.initialize()
    prior = payload()
    if change == "content_replay":
        prior["classification"] = {
            "decision": "candidate",
            "reason": "explicit_job_document",
            "region": "content",
        }
    with worker.db.connection_scope() as conn:
        key = worker.enqueue_document(conn, "demo_workday", prior)
        conn.execute(
            "UPDATE remediation_tasks SET status='not_required',attempts=7,claim='old-claim',last_error='reviewed dismissal',receipt='{}'"
        )
        conn.execute("CREATE TABLE remediation_document_dispositions(task_id TEXT,reason TEXT)")
        conn.execute(
            "INSERT INTO remediation_document_dispositions VALUES(?, 'source-reviewed navigation')",
            (key,),
        )
        current = deepcopy(prior)
        if change == "cosmetic":
            current["label"] = "Updated page footer label"
        elif change == "body":
            current["parent_description_sha256"] = "b" * 64
        elif change == "content":
            current["classification"] = {
                "decision": "candidate",
                "reason": "explicit_job_document",
                "region": "content",
            }
        elif change == "declared":
            current["provenance"] = [
                {
                    "path": "raw.required_attachment_urls",
                    "field_sha256": "c" * 64,
                    "kind": "declared_required_url",
                    "source": "supplied_job_record_current_top_level_declaration",
                }
            ]
        repeated = change in {"none", "cosmetic", "content_replay"}
        worker.enqueue_document(conn, "demo_workday", current, refresh=repeated)
        task = dict(
            conn.execute("SELECT * FROM remediation_tasks WHERE task_id=?", (key,)).fetchone()
        )
        assert task["status"] == ("not_required" if repeated else "pending")
        assert (task["attempts"], task["claim"], task["last_error"], task["receipt"]) == (
            7,
            "old-claim",
            "reviewed dismissal",
            "{}",
        )
        assert (
            conn.execute("SELECT reason FROM remediation_document_dispositions").fetchone()[0]
            == "source-reviewed navigation"
        )
    assert not calls


def test_new_document_completion_excludes_dismissed_manifest_preserves_history(setup):
    from test_document_tasks import pdf_bytes

    worker, replies, _, _ = setup
    worker.tick(execute=True)
    job = worker.db.get_job("demo_workday:R1")
    body = hashlib.sha256(job["description"].encode()).hexdigest()
    old = {**payload(), "parent_description_sha256": body}
    new = {**old, "attachment_id": "new", "url": "https://demo.example/current.pdf"}
    replies[new["url"]] = pdf_bytes(("Current job-specific document",))
    with worker.db.connection_scope() as conn:
        old_key = worker.enqueue_document(conn, "demo_workday", old)
        conn.execute(
            "UPDATE remediation_tasks SET status='not_required' WHERE task_id=?", (old_key,)
        )
        manifest = {**old, "content_sha256": "historical", "text_sha256": "oldtext"}
        conn.execute(
            "INSERT INTO remediation_documents VALUES(?,?,?,?,?,?,?)",
            (
                old_key,
                old["job_key"],
                "demo_workday",
                old["url"],
                "historical",
                "oldtext",
                dump(manifest),
            ),
        )
        conn.execute(
            "INSERT INTO attachment_blobs VALUES('historical','application/pdf',3,?)", (b"old",)
        )
        worker.enqueue_document(conn, "demo_workday", new)
    worker.max_tasks = 1
    worker.tick(execute=True)
    after = worker.db.get_job(old["job_key"])
    assert [d["url"] for d in after["raw"]["attachments"]] == [new["url"]]
    assert (
        after["raw"]["attachment_verification"]["dismissed_document_associations_retained_in_queue"]
        == 1
    )
    with worker.db.connect() as conn:
        assert conn.execute(
            "SELECT manifest FROM remediation_documents WHERE task_id=?", (old_key,)
        ).fetchone()[0] == dump(manifest)
        assert (
            conn.execute(
                "SELECT content FROM attachment_blobs WHERE content_sha256='historical'"
            ).fetchone()[0]
            == b"old"
        )
        assert (
            conn.execute(
                "SELECT status FROM remediation_tasks WHERE task_id=?", (old_key,)
            ).fetchone()[0]
            == "not_required"
        )
