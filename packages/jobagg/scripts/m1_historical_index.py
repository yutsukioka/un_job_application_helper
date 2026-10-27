#!/usr/bin/env python3
"""Build/resume a bounded historical plan index on an existing fenced copy.

Run under the network/production-write-denying rehearsal sandbox and an outer
supervisor. Original generation payloads are read in place; none are copied.
"""

from contextlib import closing
import argparse
import json
import math
from pathlib import Path
import sqlite3
import time

from m1_stage_snapshot import disjoint, fenced_owner, sha
from m1_reconcile_history import save
from jobagg import runtime_authority
from jobagg import historical_destinations as destinations, historical_occurrences as occurrences
from jobagg.publication_snapshot import _stamp, stable_file


def sealed_receipt(path, *, review, review_sha256, mapping, total, deadline):
    """Certify an already completed immutable index, without any SQLite writes."""
    record = stable_file(path, deadline_at=deadline)
    index = occurrences.Index(
        path, expected_sha256=record["sha256"], mapping=mapping, deadline_at=deadline
    )
    expected = {
        "schema": occurrences.SCHEMA,
        "references": review["references"],
        "mapping_sha256": mapping.receipt["sha256"],
    }
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 256)
        contract = json.loads(
            conn.execute("SELECT contract FROM index_state WHERE id=1").fetchone()[0]
        )
        if contract != expected:
            raise ValueError("historical_index_sealed_contract_changed")
        plans = dict(conn.execute("SELECT generation_id,proof_json FROM plans"))
        references = {Path(ref["plan_path"]).parent.name: ref for ref in review["references"]}
        if (
            not references
            or len(references) != len(review["references"])
            or plans.keys() != references.keys()
            or any(json.loads(plans[key])["reference"] != ref for key, ref in references.items())
        ):
            raise ValueError("historical_index_sealed_plan_inventory_changed")
        counts = dict(
            conn.execute(
                "SELECT journal_verified,count(*) FROM occurrences GROUP BY journal_verified"
            )
        )
    mapping.validate()
    if (
        _stamp(path.stat()) != index.signature
        or stable_file(path, deadline_at=deadline)["sha256"] != index.sha256
    ):
        raise ValueError("historical_index_changed_during_finalization")
    return {
        "schema": occurrences.SCHEMA,
        "review_sha256": review_sha256,
        "mapping_sha256": mapping.receipt["sha256"],
        "database_sha256": index.sha256,
        "plans": len(plans),
        "original_plan_bytes": total,
        "journal_status_counts": counts,
        "production_activated": False,
        "payloads_copied": 0,
    }


def execute(
    root,
    operation,
    review_path,
    review_sha256,
    *,
    limit,
    seconds,
    max_plan_bytes,
    max_total_plan_bytes,
    resume=False,
):
    root, operation, review_path = (
        Path(root).absolute(),
        Path(operation).absolute(),
        Path(review_path).absolute(),
    )
    if (
        type(limit) is not int
        or not 0 < limit <= 64
        or not math.isfinite(seconds)
        or not 0 < seconds <= 300
        or type(max_plan_bytes) is not int
        or not 0 < max_plan_bytes <= 1024**3
        or type(max_total_plan_bytes) is not int
        or not 0 < max_total_plan_bytes <= 32 * 1024**3
        or root.resolve() != root
        or operation.parent != root
        or operation.resolve() != operation
        or review_path.resolve() != review_path
        or not review_path.is_relative_to(root)
        or review_path.stat().st_size > 1024**2
        or sha(review_path) != review_sha256
    ):
        raise ValueError("bounded_exact_historical_index_operation_required")
    deadline = time.monotonic() + seconds
    review = json.loads(review_path.read_text())
    if review.get("schema") != "reviewed-historical-generations-1":
        raise ValueError("historical_index_review_required")
    snapshot = json.loads((root / "snapshot.json").read_text())
    config_path = Path(snapshot["production_config"])
    if (
        snapshot.get("complete") is not True
        or snapshot.get("snapshot_root") != str(root)
        or config_path.resolve() != config_path
        or sha(config_path) != snapshot["production_config_sha256"]
    ):
        raise ValueError("historical_index_original_boundary_changed")
    config = json.loads(config_path.read_text())
    source = root / "worker/jobs.sqlite3"
    production = Path(config["publication_worker_database"])
    disjoint(
        root,
        [
            production.parent,
            Path(config["state_dir"]),
            Path(config["attempt_state_dir"]),
            Path(config["shared_lock_path"]).parent,
        ],
    )
    if source.resolve() != source or source.samefile(production) or source.stat().st_nlink != 1:
        raise ValueError("historical_index_source_aliases_production")
    boundary = json.loads((root / "pr143-reboot-renewal/publication-boundary.json").read_text())
    with fenced_owner(config, snapshot["fence_sha256"]) as fd:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as c:
            state = runtime_authority.validate(c)
            if state["phase"] != "idle":
                raise ValueError("historical_index_requires_idle_copy")
            if (
                c.execute("SELECT inventory_sha256 FROM jobagg_legacy_control_state").fetchone()[0]
                != review["inventory_sha256"]
            ):
                raise ValueError("historical_index_catalogue_changed")
            total = 0
            for reference in review["references"]:
                for label in ("plan", "result"):
                    p = occurrences.location(reference, label)
                    original = Path(reference[label + "_path"])
                    if p != original:
                        anchor = reference.get("locator_anchor")
                        if not isinstance(anchor, dict):
                            raise ValueError("historical_index_relocation_anchor_required")
                        anchor_path = Path(anchor["path"])
                        row = c.execute("SELECT sha256,identity_json FROM jobagg_legacy_dependencies WHERE path=?",
                                        (str(anchor_path),)).fetchone()
                        if (row is None or row[0] != anchor["sha256"] or
                                json.loads(row[1]) != list(_stamp(anchor_path.stat()))):
                            raise ValueError("historical_index_relocation_anchor_changed")
                        from jobagg.publication_snapshot import _json_document
                        value, record = _json_document(anchor_path, deadline_at=deadline)
                        if (record["sha256"] != anchor["sha256"] or
                                value.get("plan_path") != reference["plan_path"] or
                                value.get("plan_sha256") != reference["plan_sha256"]):
                            raise ValueError("historical_index_relocation_original_plan_unbound")
                        # A result is independently pinned by the original recovered
                        # wrapper result; it must equal that wrapper's publication object.
                        resolution_path = Path(anchor["resolution_path"])
                        row = c.execute("SELECT sha256,identity_json FROM jobagg_legacy_dependencies WHERE path=?",
                                        (str(resolution_path),)).fetchone()
                        if row is None or row[0] != anchor["resolution_sha256"]:
                            raise ValueError("historical_index_relocation_resolution_unbound")
                        resolution, rec = _json_document(resolution_path, deadline_at=deadline)
                        if rec["sha256"] != row[0] or list(_stamp(resolution_path.stat())) != json.loads(row[1]):
                            raise ValueError("historical_index_relocation_resolution_changed")
                        recovered_path = resolution_path.parent / resolution["result"]["path"]
                        recovered, rec = _json_document(recovered_path, deadline_at=deadline)
                        if rec["sha256"] != resolution["result"]["sha256"] or recovered.get("status") != "published":
                            raise ValueError("historical_index_relocation_recovery_changed")
                        producer, rec = _json_document(occurrences.location(reference, "result"), deadline_at=deadline)
                        if rec["sha256"] != reference["result_sha256"] or recovered.get("publication") != producer:
                            raise ValueError("historical_index_relocation_producer_differs")
                        continue
                    if p.resolve() != p:
                        raise ValueError("historical_index_control_alias")
                    row = c.execute(
                        "SELECT sha256,identity_json FROM jobagg_legacy_dependencies WHERE path=?",
                        (str(p),),
                    ).fetchone()
                    if (
                        row is None
                        or json.loads(row[1]) != list(_stamp(p.stat()))
                        or (row[0] is not None and row[0] != reference[label + "_sha256"])
                    ):
                        raise ValueError("historical_index_original_dependency_changed")
                size = occurrences.location(reference, "plan").stat().st_size
                total += size
                if size > max_plan_bytes or total > max_total_plan_bytes:
                    raise ValueError("historical_index_declared_byte_bound")
            mapping_receipt = destinations.prepare(
                c,
                root / "snapshot.json",
                boundary=boundary,
                scope=review["publication_scope"],
                owner_fd=fd,
                deadline_at=deadline,
            )
            mapping = destinations.Destinations(
                c, mapping_receipt, boundary=boundary, owner_fd=fd, deadline_at=deadline
            )
        if resume:
            if (
                not operation.is_dir()
                or json.loads((operation / "mapping.json").read_text()) != mapping_receipt
            ):
                raise ValueError("historical_index_resume_mapping_changed")
        else:
            operation.mkdir(mode=0o700, exist_ok=False)
            save(operation / "mapping.json", mapping_receipt)
            (operation / "mapping.json").chmod(0o400)
        path = operation / "occurrences.sqlite3"
        if (operation / "index-receipt.json").exists():
            receipt = json.loads((operation / "index-receipt.json").read_text())
            if receipt["review_sha256"] != review_sha256 or receipt["database_sha256"] != sha(path):
                raise ValueError("historical_index_sealed_receipt_changed")
            return {"complete": True, "already_sealed": True, "receipt": receipt}
        if resume and path.exists() and not path.stat().st_mode & 0o222:
            result = {"processed": 0, "complete": True, "plan_bytes_read": 0}
        else:
            with closing(sqlite3.connect(path)) as c:
                occurrences.prepare(
                    c, references=review["references"], mapping_sha256=mapping_receipt["sha256"]
                )
                result = occurrences.step(
                    c, mapping=mapping, limit=limit, deadline_at=deadline, max_plan_bytes=max_plan_bytes
                )
        if result["complete"]:
            if path.stat().st_mode & 0o222:
                path.chmod(0o400)
            receipt = sealed_receipt(
                path, review=review, review_sha256=review_sha256, mapping=mapping,
                total=total, deadline=deadline,
            )
            save(operation / "index-receipt.json", receipt)
            (operation / "index-receipt.json").chmod(0o400)
            result["receipt"] = receipt
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("snapshot", "operation", "review"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--review-sha256", required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--max-plan-bytes", type=int, required=True)
    parser.add_argument("--max-total-plan-bytes", type=int, required=True)
    parser.add_argument("--resume", action="store_true")
    a = parser.parse_args()
    print(
        json.dumps(
            execute(
                a.snapshot,
                a.operation,
                a.review,
                a.review_sha256,
                limit=a.limit,
                seconds=a.seconds,
                max_plan_bytes=a.max_plan_bytes,
                max_total_plan_bytes=a.max_total_plan_bytes,
                resume=a.resume,
            )
        )
    )
