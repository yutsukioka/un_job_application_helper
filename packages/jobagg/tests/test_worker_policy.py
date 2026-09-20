from datetime import UTC, datetime
import hashlib
import json
import sqlite3
import time

import pytest

from jobagg.pipelines.worker_policy import SharedPolicy
from jobagg.prepare_worker_policy import prepare, reconcile_attachment_holds


def bootstrap(tmp_path):
    owner = tmp_path / "owner.lock"
    value = {
        "schema_version": 1,
        "shared_lock": str(owner),
        "scope_source_ids": ["example"],
        "reviewed_at": datetime.now(UTC).isoformat(),
        "prior_writers_reviewed": True,
        "no_unmigrated_policy_state": True,
        "evidence": [],
        "detail_attempts": [],
        "host_states": {
            "api.example": {
                "stopped": True,
                "eligible_at": time.time() + 100,
                "last_request_at": time.time() - 5,
            }
        },
        "source_holds": {},
    }
    path = tmp_path / "bootstrap.json"
    path.write_text(json.dumps(value))
    return SharedPolicy(owner, ["example"], path), path, value


def test_first_initialization_requires_explicit_review_and_preserves_host_state(tmp_path):
    policy, path, value = bootstrap(tmp_path)
    no_proposal = SharedPolicy(policy.owner, ["example"])
    assert no_proposal.inspect()["status"] == "reviewed_bootstrap_required"
    with pytest.raises(ValueError, match="requires"):
        no_proposal.initialize()
    value["prior_writers_reviewed"] = False
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="explicit"):
        policy.initialize()
    value["prior_writers_reviewed"] = True
    path.write_text(json.dumps(value))
    policy.initialize()
    state = json.loads(next((policy.root / "hosts").glob("*.json")).read_text())
    assert state == value["host_states"]["api.example"]
    assert no_proposal.inspect()["status"] == "existing"


def test_missing_attempt_or_bootstrap_cannot_reset_quota(tmp_path):
    policy, path, value = bootstrap(tmp_path)
    policy.initialize()
    policy.reserve("one", "example", "detail", time.time(), tmp_path, "job")
    event = next((policy.root / "attempts").glob("*.json"))
    event.unlink()
    with pytest.raises(ValueError, match="history removed"):
        policy.events("example")
    (policy.root / "bootstrap.json").unlink()
    with pytest.raises(ValueError, match="bootstrap missing"):
        policy.inspect()


def test_changed_bootstrap_evidence_and_uncovered_source_fail(tmp_path):
    policy, path, value = bootstrap(tmp_path)
    evidence = tmp_path / "original-state.json"
    evidence.write_text("original")
    value["evidence"] = [
        {"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}
    ]
    path.write_text(json.dumps(value))
    evidence.write_text("changed")
    with pytest.raises(ValueError, match="evidence changed"):
        policy.initialize()
    evidence.write_text("original")
    policy.initialize()
    with pytest.raises(ValueError, match="scope"):
        SharedPolicy(policy.owner, ["different"]).inspect()


def test_manual_migration_preserves_original_times_deduplicates_and_carries_real_holds(tmp_path):
    run = tmp_path / "manual-run"
    (run / "locks").mkdir(parents=True)
    (run / "sources/unicef_pageup/pass").mkdir(parents=True)
    (run / "output").mkdir()
    registry = tmp_path / "registry.yaml"
    registry.write_text(
        "sources:\n  - id: unicef_pageup\n    name: UNICEF\n    ats_family: pageup\n    base_url: https://jobs.unicef.org\n"
    )
    (run / "manifest.json").write_text(
        json.dumps({"run_dir": str(run), "scope": [{"id": "unicef_pageup", "slug": "unicef"}]})
    )
    start = datetime.fromtimestamp(time.time() - 120, UTC).isoformat()
    end = datetime.fromtimestamp(time.time() - 100, UTC).isoformat()
    ledger = {
        "source_id": "unicef_pageup",
        "reservations": [
            {"reservation_id": "one", "reserved_at": start, "external_id": "123"},
            {"reservation_id": "two", "reserved_at": start, "external_id": "124"},
        ],
    }
    (run / "locks/unicef_pageup-detail-hourly-reservations.json").write_text(json.dumps(ledger))
    (run / "sources/unicef_pageup/pass/details.jsonl").write_text(
        json.dumps(
            {
                "reservation_id": "one",
                "started_at": start,
                "finished_at": end,
                "listing_identity": "123",
            }
        )
        + "\n"
    )
    host = "host-" + hashlib.sha256(b"jobs.unicef.org").hexdigest()[:24]
    (run / "locks" / (host + ".json")).write_text(
        json.dumps({"stopped": True, "reason": "A real unresolved access hold"})
    )
    (run / "locks" / (host + ".lock")).write_text(str(time.time() - 30))
    db = run / "output/unicef_jobs.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE source_circuit_breakers(source_id TEXT,state TEXT)")
        conn.execute("INSERT INTO source_circuit_breakers VALUES('unicef_pageup','open')")
    owner = tmp_path / "remediation/manual-fetch-owner.lock"
    value = prepare(run, registry, owner)
    assert len(value["detail_attempts"]) == 2
    one = next(e for e in value["detail_attempts"] if e["attempt_id"] == "one")
    two = next(e for e in value["detail_attempts"] if e["attempt_id"] == "two")
    assert one["started_at"] == datetime.fromisoformat(start).timestamp()
    assert one["finished_at"] == datetime.fromisoformat(end).timestamp()
    assert two["finished_at"] is None and two["eligibility_accounting_until"] > one["finished_at"]
    assert value["host_states_by_digest"][host]["stopped"] is True
    assert "unicef_pageup" in value["source_holds"]
    with pytest.raises(ValueError, match="actual manual-fetch"):
        prepare(run, registry, tmp_path / "unrelated.lock")


def test_attachment_only_hold_must_have_same_shared_mirror(tmp_path):
    directory = tmp_path / "evidence/attachments"
    directory.mkdir(parents=True)
    host = "files.example"
    key = "host-" + hashlib.sha256(host.encode()).hexdigest()[:24]
    (directory / "host_blocks.json").write_text(json.dumps({host: {"status": 403}}))
    with pytest.raises(ValueError, match="lacks shared-state"):
        reconcile_attachment_holds(tmp_path, {}, {})
    with pytest.raises(ValueError, match="conflict"):
        reconcile_attachment_holds(tmp_path, {key: {"stopped": False}}, {})
    evidence = {}
    reconcile_attachment_holds(tmp_path, {key: {"stopped": True}}, evidence)
    assert str(directory / "host_blocks.json") in evidence


def test_exact_reviewed_historical_attachment_hold_is_not_reapplied(tmp_path):
    import gzip

    directory = tmp_path / "evidence/attachments"
    directory.mkdir(parents=True)
    block = {"status": 202, "reason": "Historical empty response"}
    host = "files.example"
    key = "host-" + hashlib.sha256(host.encode()).hexdigest()[:24]
    (directory / "host_blocks.json").write_text(json.dumps({host: block}))
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"stopped": True, "inherited_evidence": block}))
    browser = tmp_path / "browser.json"
    browser.write_text('{"public_observation":true}')
    body = tmp_path / "body.gz"
    body.write_bytes(gzip.compress(b"Exact public document"))
    bodysha = hashlib.sha256(b"Exact public document").hexdigest()
    metadata = tmp_path / "capture.json"
    metadata.write_text(
        json.dumps(
            {
                "status_code": 200,
                "body_captured": True,
                "url": "https://files.example/notice",
                "response_url": "https://files.example/notice",
                "artifact": str(body),
                "body_sha256": bodysha,
                "started_at": "2026-01-01T12:00:01+00:00",
            }
        )
    )
    transition = tmp_path / "transition.json"
    transition.write_text(
        json.dumps(
            {
                "prior_host_state_path": str(prior),
                "prior_host_state_sha256": hashlib.sha256(prior.read_bytes()).hexdigest(),
                "browser": {
                    "path": str(browser),
                    "sha256": hashlib.sha256(browser.read_bytes()).hexdigest(),
                },
                "allowed_public_url": "https://files.example/notice",
                "authorized_at": "2026-01-01T12:00:00+00:00",
            }
        )
    )
    state = {
        "stopped": False,
        "historical_stop_reviewed": True,
        "inherited_stop": block,
        "historical_stop_review_basis": {
            "transition_path": str(transition),
            "transition_sha256": hashlib.sha256(transition.read_bytes()).hexdigest(),
            "fresh_success_capture": str(metadata),
            "fresh_success_body_sha256": bodysha,
        },
    }
    evidence = {}
    reconcile_attachment_holds(tmp_path, {key: state}, evidence)
    assert state["stopped"] is False and len(evidence) == 6
    state["inherited_stop"] = {"status": 403}
    with pytest.raises(ValueError, match="conflict"):
        reconcile_attachment_holds(tmp_path, {key: state}, {})
