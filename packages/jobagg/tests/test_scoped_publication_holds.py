import time
import sqlite3
import pytest

from jobagg import runtime_holds as holds, runtime_authority as authority
from jobagg.publication_projection import build_projection
from jobagg import publication_work as work
from jobagg.remediation_worker import shared_owner
from test_live_publication import setup, add_detail  # noqa: F401
from test_publication_work import prepare, select
from test_legacy_wrapper_reconciliation import setup_catalog

# ruff: noqa: F811


def test_publication_holds_preserve_debt_and_independent_unit_and_reject_stale_selection(setup):
    add_detail(setup, external_id="001")
    add_detail(setup, external_id="002")
    owner = setup["root"] / "owner.lock"
    with shared_owner(owner) as fd:
        prepare(setup, owner_path=owner, owner_fd=fd)
        with setup["worker"].connect() as conn:
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
            original = select(setup, conn, max_units=10)
            chosen = next(item for item in original["units"] if item["kind"] == "job")
            with authority.controller(conn, run_id=None, owner_fd=fd):
                holds.put(
                    conn,
                    scope="unit",
                    identity=chosen["unit"],
                    reason="current evidence unresolved",
                    evidence_sha256="a" * 64,
                    next_condition="exact current acceptance and body revalidated",
                    owner_fd=fd,
                )
                conn.commit()
            from jobagg.pipelines.live_publication import plan_publication
            plan = plan_publication(setup["worker"].path, setup["registry"], setup["output"],
                                    deadline_at=time.monotonic()+10)
            assert any(item["job_key"] == chosen["job_key"] and "publication_scope_integrity_hold" in item["reason"] for item in plan["rejected"])
            current = select(setup, conn, max_units=10)
            assert chosen["unit"] in {item["unit"] for item in current["held"]}
            assert any(
                item["kind"] == "job" and item["unit"] != chosen["unit"]
                for item in current["units"]
            )
            assert not work.vector(conn, contract_sha256="contract", now=time.time())["empty"]
            with pytest.raises(ValueError, match="publication_scope_integrity_hold"):
                with sqlite3.connect(":memory:") as target:
                    build_projection(
                        conn, target, selection=original, deadline_at=time.monotonic() + 5
                    )
            with pytest.raises(ValueError, match="publication_scope_integrity_hold"):
                work.acknowledge(
                    conn, original, result_sha256="b" * 64, processed_units=original["units"]
                )
            assert (
                conn.execute(
                    "SELECT disposition FROM jobagg_dirty_current WHERE unit=?", (chosen["unit"],)
                ).fetchone()[0]
                == "pending"
            )
