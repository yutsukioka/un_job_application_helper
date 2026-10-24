import time
import pytest

from jobagg import accepted_evidence, runtime_authority as authority
from jobagg.remediation_worker import shared_owner
from test_live_publication import setup, add_detail  # noqa: F401
from test_publication_work import prepare
from test_legacy_wrapper_reconciliation import setup_catalog

# ruff: noqa: F811


@pytest.mark.parametrize("successful_exit", [True, False])
def test_native_backfill_requires_idle_owner_and_retained_capture_without_manufacturing_exit(
    setup, successful_exit
):
    add_detail(setup)
    owner = setup["root"] / "owner.lock"
    with shared_owner(owner) as fd:
        prepare(setup, owner_path=owner, owner_fd=fd, import_exits=successful_exit)
        with setup["worker"].connect() as conn:
            conn.execute("DELETE FROM jobagg_accepted_evidence")
            conn.execute("DELETE FROM jobagg_evidence_backfill")
            evidence = setup["root"] / "controls"
            evidence.mkdir()
            setup_catalog(evidence, conn, fd, owner)
            from jobagg.runtime_control_catalog import receipt

            authority.activate(
                conn,
                epoch="fixture",
                owner_fd=fd,
                owner_path=owner,
                bootstrap_receipt={
                    "reconciled": True,
                    "source_sha256": "a" * 64,
                    "control_catalogue_sha256": receipt(conn)["sha256"],
                },
            )
            conn.commit()
            before = authority.validate(conn)
            with pytest.raises(ValueError, match="idle_owner_review"):
                accepted_evidence.backfill(conn, limit=1, deadline_at=time.monotonic() + 5)
            with authority.controller(conn, run_id=None, owner_fd=fd):
                result = accepted_evidence.backfill(
                    conn,
                    limit=1,
                    deadline_at=time.monotonic() + 5,
                    owner_fd=fd,
                    require_retained_captures=True,
                )
                conn.commit()
            assert result["complete"]
            disposition = conn.execute(
                "SELECT disposition FROM jobagg_evidence_backfill"
            ).fetchone()[0]
            assert disposition == (
                "matched" if successful_exit else "missing_successful_exit_proof"
            )
            assert conn.execute("SELECT count(*) FROM jobagg_accepted_evidence").fetchone()[
                0
            ] == int(successful_exit)
            after = authority.validate(conn)
            assert after["phase"] == before["phase"] == "idle"
            assert after["epoch"] == before["epoch"]
            assert after["revision"] > before["revision"]
            assert (
                result["records"][0]["retained_evidence"][0]["disposition"]
                == "retained_detail_bytes_and_task_crosswalk_verified"
            )
            assert conn.execute("SELECT status FROM remediation_attempts").fetchone()[0] == "done"


@pytest.mark.parametrize("changed", [False, True])
def test_retained_listing_head_survives_later_task_failure_but_changed_frame_cannot_import(
    setup, changed
):
    import json
    import hashlib
    from jobagg.remediation_worker import epoch

    add_detail(setup)
    observed = "2026-10-01T00:00:00+00:00"
    frame = setup["root"] / "listing.json"
    frame.write_text(json.dumps({"source_id": "test_custom_html", "observed_at": observed}))
    receipt = {
        "frame_path": str(frame),
        "frame_sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
        "enumeration": {"completeness_certified": False},
    }
    with setup["worker"].connect() as conn:
        conn.execute(
            "CREATE TABLE remediation_sources(source_id TEXT PRIMARY KEY,last_list_at REAL,listing_ids TEXT,listing_proof TEXT)"
        )
        conn.execute(
            "INSERT INTO remediation_sources VALUES(?,?,?,?)",
            ("test_custom_html", epoch(observed), "[]", json.dumps(receipt["enumeration"])),
        )
        conn.execute(
            "INSERT INTO remediation_tasks VALUES('listing-1','test_custom_html','listing','','pending',?, '{}')",
            (json.dumps({"error": "later refresh failed"}),),
        )
        conn.execute(
            "INSERT INTO remediation_attempts VALUES('listing-accepted','listing-1','test_custom_html','listing',1,2,'done',?)",
            (json.dumps(receipt),),
        )
    owner = setup["root"] / "owner.lock"
    with shared_owner(owner) as fd:
        prepare(setup, owner_path=owner, owner_fd=fd)
        with setup["worker"].connect() as conn:
            conn.execute("DELETE FROM jobagg_accepted_events")
            conn.execute("DELETE FROM jobagg_event_backfill")
            evidence = setup["root"] / "controls"
            evidence.mkdir()
            setup_catalog(evidence, conn, fd, owner)
            from jobagg.runtime_control_catalog import receipt as catalogue

            authority.activate(
                conn,
                epoch="fixture",
                owner_fd=fd,
                owner_path=owner,
                bootstrap_receipt={
                    "reconciled": True,
                    "source_sha256": "a" * 64,
                    "control_catalogue_sha256": catalogue(conn)["sha256"],
                },
            )
            conn.commit()
            if changed:
                frame.write_text("{}")
            with authority.controller(conn, run_id=None, owner_fd=fd):
                result = accepted_evidence.backfill_events(
                    conn, limit=10, deadline_at=time.monotonic() + 5, owner_fd=fd
                )
                conn.commit()
            assert result["complete"]
            head = conn.execute("SELECT listing_event_version FROM remediation_sources").fetchone()[
                0
            ]
            if changed:
                assert head is None
                assert (
                    result["records"][0]["disposition"] == "current_event_evidence_requires_review"
                )
                assert (
                    conn.execute("SELECT count(*) FROM jobagg_accepted_events").fetchone()[0] == 0
                )
            else:
                assert head
                proof = json.loads(
                    conn.execute("SELECT reference_json FROM jobagg_accepted_events").fetchone()[0]
                )
                assert proof["contract"] == "legacy-accepted-event-1"
                assert proof["transaction_id"] is None
                assert proof["acceptance_proof"]["kind"] == "verified_worker_exit"
            assert (
                conn.execute(
                    "SELECT status FROM remediation_tasks WHERE task_id='listing-1'"
                ).fetchone()[0]
                == "pending"
            )


def test_explicit_current_evidence_rereview_preserves_old_checkpoint(setup):
    import json

    add_detail(setup)
    owner = setup["root"] / "owner.lock"
    with shared_owner(owner) as fd:
        prepare(setup, owner_path=owner, owner_fd=fd)
        with setup["worker"].connect() as conn:
            conn.execute(
                "UPDATE jobagg_evidence_backfill SET disposition='retained_capture_review_required'"
            )
            job_key = conn.execute("SELECT job_key FROM remediation_observations").fetchone()[0]
            evidence = setup["root"] / "controls"
            evidence.mkdir()
            setup_catalog(evidence, conn, fd, owner)
            from jobagg.runtime_control_catalog import receipt

            authority.activate(
                conn,
                epoch="fixture",
                owner_fd=fd,
                owner_path=owner,
                bootstrap_receipt={
                    "reconciled": True,
                    "source_sha256": "a" * 64,
                    "control_catalogue_sha256": receipt(conn)["sha256"],
                },
            )
            conn.commit()
            with authority.controller(conn, run_id=None, owner_fd=fd):
                result = accepted_evidence.backfill(
                    conn,
                    limit=1,
                    deadline_at=time.monotonic() + 5,
                    owner_fd=fd,
                    require_retained_captures=True,
                    review_job_key=job_key,
                )
                conn.commit()
            assert result["records"][0]["disposition"] == "matched"
            assert (
                result["records"][0]["previous_backfill"][2] == "retained_capture_review_required"
            )
            history = json.loads(
                conn.execute(
                    "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id LIKE 'evidence-backfill-review/%'"
                ).fetchone()[0]
            )
            assert history["previous"][2] == "retained_capture_review_required"
            assert history["current"][2] == "matched"
