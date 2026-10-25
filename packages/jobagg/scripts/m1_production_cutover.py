#!/usr/bin/env python3
"""One-time fenced in-place M1 preparation. Never dispatch or remove maintenance.

Run with the pinned release Python and an exact private operation descriptor.
Existing native journals own page resume; this wrapper records completed stages.
"""

from contextlib import closing
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time
from urllib.parse import urlsplit

from m1_stage_snapshot import fenced_owner, sha
from jobagg.atomic_files import atomic_write_text
from jobagg import (
    accepted_evidence,
    artifact_locations,
    publication_work,
    task_headers,
    publication_fence,
    publication_bootstrap,
    policy_migration,
    policy_evidence,
    runtime_authority,
    runtime_control_catalog as catalogue,
    runtime_containment,
    runtime_holds,
)
from jobagg.pipelines.worker_policy import SharedPolicy
from jobagg.pipelines.sync_source import load_sources, fetch_schedule_policy
from jobagg.remediation_scheduling import task_key
from jobagg.remediation_worker import implementation_hash


def save(path, value):
    atomic_write_text(path, json.dumps(value, sort_keys=True, indent=2) + "\n")


def domain(conn):
    result = {}
    for table in (
        "jobs",
        "remediation_tasks",
        "remediation_attempts",
        "remediation_observations",
        "remediation_documents",
        "remediation_sources",
    ):
        columns = [
            r[1]
            for r in conn.execute("PRAGMA table_info(" + table + ")")
            if r[1] not in {"listing_event_version"}
        ]
        digest = hashlib.sha256()
        count = 0
        for row in conn.execute("SELECT " + ",".join(columns) + " FROM " + table + " ORDER BY 1"):
            digest.update(json.dumps(list(row), ensure_ascii=False).encode())
            count += 1
        result[table] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def loop_pages(step, check):
    while True:
        check()
        result = step()
        if result is True or isinstance(result, dict) and result.get("complete"):
            return result


def execute(descriptor):
    operation = json.loads(Path(descriptor).read_text())
    root = Path(operation["operation_root"])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    seconds = operation.get("seconds", 3600)
    if not math.isfinite(seconds) or not 0 < seconds <= 14400:
        raise ValueError("finite_one_time_preparation_allowance_required")
    deadline = time.monotonic() + seconds
    config_path = Path(operation["production_config"])
    config = json.loads(config_path.read_text())
    candidate_path = Path(operation["candidate_config"])
    candidate = json.loads(candidate_path.read_text())
    prior_progress = json.loads((root / "progress.json").read_text()) if (root / "progress.json").exists() else {}
    installed_resume = "handoff_intent" in prior_progress.get("completed", []) and sha(config_path) == operation["candidate_config_sha256"]
    if (sha(config_path) != operation["production_config_sha256"] and not installed_resume) or sha(candidate_path) != operation["candidate_config_sha256"]:
        raise ValueError("cutover_configuration_changed")
    for path, expected in operation["input_sha256"].items():
        if sha(Path(path)) != expected:
            raise ValueError("cutover_prepared_input_changed:" + path)
    if implementation_hash() != operation["implementation_sha256"]:
        raise ValueError("cutover_release_changed")
    database = Path(config["publication_worker_database"])
    output = Path(operation["production_root"]) / "output"
    if (
        root.resolve() != root
        or database.resolve() != database
        or candidate["publication_worker_database"] != str(database)
    ):
        raise ValueError("cutover_exact_paths_required")
    if candidate.get("storage_budget", {}).get("enabled") or candidate.get(
        "storage_retention", {}
    ).get("enabled"):
        raise ValueError("retained_originals_cutover_required")
    journal = root / "progress.json"
    progress = (
        json.loads(journal.read_text())
        if journal.exists()
        else {"descriptor_sha256": sha(Path(descriptor)), "completed": []}
    )
    if progress["descriptor_sha256"] != sha(Path(descriptor)):
        raise ValueError("cutover_resume_descriptor_changed")

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError("one_time_cutover_deadline_keep_fence_and_journals")
        if sha(Path(config["maintenance_file"])) != operation["fence_sha256"]:
            raise ValueError("cutover_maintenance_changed")
        if shutil.disk_usage(database.parent).free < 20 * 1024**3:
            raise ValueError("cutover_physical_reserve")

    def done(name, receipt=None):
        if name not in progress["completed"]:
            progress["completed"].append(name)
        if receipt is not None:
            progress[name] = receipt
        save(journal, progress)
        print(json.dumps({"stage": name, "complete": True}), flush=True)

    with fenced_owner(config, operation["fence_sha256"]) as fd:
        check()
        boundary_path = root / "boundary.json"
        if "backups" not in progress["completed"]:
            backup = root / "backup"
            backup.mkdir(exist_ok=True)
            stores = [database, *sorted(output.glob("*_jobs.sqlite3"))]
            if shutil.disk_usage(database.parent).free < 20 * 1024**3 + sum(
                p.stat().st_size for p in stores
            ):
                raise ValueError("cutover_backup_reserve")
            for index, path in enumerate(stores):
                target = backup / (str(index) + ".sqlite3")
                if not target.exists():
                    temporary = target.with_suffix(".pending")
                    with (
                        closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as src,
                        closing(sqlite3.connect(temporary)) as dst,
                    ):
                        src.backup(dst, pages=4096, progress=lambda *_: check(), sleep=0.01)
                        dst.execute("PRAGMA cache_size=-131072")
                        dst.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                        if dst.execute("PRAGMA quick_check(jobs)").fetchone()[0] != "ok":
                            raise ValueError("cutover_backup_integrity_failed")
                    with temporary.open("rb") as stream:
                        os.fsync(stream.fileno())
                    os.replace(temporary, target)
            policy_root = Path(str(config["shared_lock_path"]) + ".worker-policy")
            files = [
                config_path,
                candidate_path,
                Path(config["maintenance_file"]),
                Path(str(config["shared_lock_path"]) + ".worker-policy.json"),
                policy_root / "bootstrap.json",
                policy_root / "attempt_index.json",
                output / ".jobagg-publication-state.json",
            ]
            files += [p for p in Path(config["state_dir"]).glob("*.json")]
            files += [p for p in Path(config["attempt_state_dir"]).glob("*.json")]
            controls = []
            for index, path in enumerate(files):
                check()
                target = backup / ("control-" + str(index) + ".json")
                if not target.exists():
                    shutil.copyfile(path, target)
                controls.append({"path": str(path), "backup": str(target), "sha256": sha(target)})
            with closing(sqlite3.connect(database)) as conn:
                baseline = domain(conn)
            info = os.fstat(fd)
            boundary = {
                "schema": "jobagg-production-cutover-boundary-1",
                "production_root": operation["production_root"],
                "production_config": str(config_path),
                "production_config_sha256": sha(config_path),
                "fence_sha256": operation["fence_sha256"],
                "owner_identity": {"device": info.st_dev, "inode": info.st_ino},
                "domain": baseline,
                "controls": controls,
                "database_backups": [
                    {"source": str(p), "backup": str(backup / (str(i) + ".sqlite3"))}
                    for i, p in enumerate(stores)
                ],
            }
            save(boundary_path, boundary)
            done("backups")
        boundary = json.loads(boundary_path.read_text())
        registry = Path(operation["registry"])
        sources = {s.id: s for s in load_sources(registry)}
        policy = SharedPolicy(config["shared_lock_path"], set(sources))
        bootstrap = json.loads((policy.root / "bootstrap.json").read_text())
        policy.source_ids = set(bootstrap["scope_source_ids"])
        pauses = {
            sid: float(
                fetch_schedule_policy(sources[sid]).get("detail_batch_pause_seconds")
                or fetch_schedule_policy(sources[sid]).get("oracle_detail_batch_pause_seconds")
                or 0
            )
            for sid in policy.source_ids
        }
        epoch = operation["epoch"]
        try:
            if "policy" not in progress["completed"]:
                if not (policy.root / "evidence_locations.json").exists():
                    mapping = json.loads(Path(operation["evidence_locations"]).read_text())
                    policy_evidence.prepare(
                        policy,
                        owner_fd=fd,
                        location_root=Path(mapping["location_root"]),
                        locations={k: v["path"] for k, v in mapping["files"].items()},
                        check_deadline=check,
                    )
                loop_pages(
                    lambda: policy_migration.step(
                        policy,
                        owner_fd=fd,
                        epoch=epoch,
                        pauses=pauses,
                        reviewed_writers=operation["writers"],
                        limit=1024,
                        check_deadline=check,
                    ),
                    check,
                )
                done("policy")
            with closing(sqlite3.connect(database)) as conn:
                publication_work.configure(conn)
                task_headers.configure(conn)
                conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                if "worker_schema" not in progress["completed"]:
                    with conn:
                        accepted_evidence.prepare(conn)
                        task_headers.prepare(
                            conn,
                            {
                                sid: urlsplit(s.base_url).hostname.lower()
                                for sid, s in sources.items()
                            },
                        )
                        publication_work.prepare(
                            conn,
                            contract_sha256=operation["runtime_contract_sha256"],
                            epoch=epoch,
                            freshness={
                                "listing_max_age_seconds": candidate["listing_max_age_seconds"],
                                "detail_max_age_seconds": candidate["detail_max_age_seconds"],
                            },
                        )

                    def work_page():
                        with conn:
                            return publication_work.bootstrap(
                                conn, limit=1024, deadline_at=deadline
                            )

                    loop_pages(work_page, check)
                    done("worker_schema")
                if "catalogue" not in progress["completed"]:
                    manifest = json.loads(Path(operation["control_manifest"]).read_text())
                    manifest.update(epoch=epoch, writers=operation["writers"])
                    # Native import retains the exact old index under this name.
                    # Keep its auxiliary history held, with an explicit locator.
                    for item in manifest["runs"]:
                        if item["path"] == str(policy.root):
                            files = item.get("unverified_files", {})
                            if "attempt_index.json" in files:
                                original = policy.root / "attempt_index.json"
                                retained = policy.root / "authority_legacy_index.json"
                                before = next(c for c in boundary["controls"] if c["path"] == str(original))
                                if sha(retained) != before["sha256"]:
                                    raise ValueError("cutover_original_policy_index_not_preserved")
                                files.pop("attempt_index.json")
                                st = retained.stat()
                                files[retained.name] = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
                                item["retained_migration_locator"] = {"original":str(original), "path":str(retained), "sha256":before["sha256"]}
                    with conn:
                        inventory = catalogue.prepare(
                            conn,
                            manifest=manifest,
                            owner_fd=fd,
                            owner_path=config["shared_lock_path"],
                        )
                        for hold in operation.get("publication_holds", []):
                            if conn.execute("SELECT 1 FROM jobagg_legacy_review_holds WHERE run_id=?", (hold["run_id"],)).fetchone():
                                continue  # Same sealed descriptor; exact hold already committed.
                            if hold["kind"] == "relocated":
                                catalogue.hold_relocated_publication(
                                    conn,
                                    run_id=hold["run_id"],
                                    original_control_root=hold["original_control_root"],
                                    owner_fd=fd,
                                    owner_path=config["shared_lock_path"],
                                    expected_inventory_sha256=inventory,
                                    deadline_at=deadline,
                                )
                            else:
                                catalogue.hold_publication_contradiction(
                                    conn,
                                    run_id=hold["run_id"],
                                    expected_failure=hold["expected_failure"],
                                    owner_fd=fd,
                                    owner_path=config["shared_lock_path"],
                                    expected_inventory_sha256=inventory,
                                    deadline_at=deadline,
                                )

                    def control_page():
                        with conn:
                            return catalogue.step(
                                conn,
                                owner_fd=fd,
                                owner_path=config["shared_lock_path"],
                                limit=32,
                                deadline_at=deadline,
                                reviewed_implementations=set(
                                    operation["historical_implementations"]
                                ),
                            )

                    loop_pages(control_page, check)
                    done("catalogue", catalogue.receipt(conn))
                if "exports" not in progress["completed"]:
                    for path in sorted(output.glob("*_jobs.sqlite3")):
                        check()
                        with closing(sqlite3.connect(path)) as target, target:
                            publication_fence.prepare_target_schema(target)
                            publication_fence.activate(
                                target,
                                owner_path=config["shared_lock_path"],
                                owner_fd=fd,
                                epoch=epoch,
                            )
                    publication_bootstrap.prepare(
                        root / "exports",
                        registry=registry,
                        output_root=output,
                        owner_fd=fd,
                        owner_path=config["shared_lock_path"],
                        max_export_bytes=1024**3,
                        max_total_export_bytes=4 * 1024**3,
                        deadline_at=deadline,
                    )
                    exports = loop_pages(
                        lambda: publication_bootstrap.step(
                            root / "exports",
                            owner_fd=fd,
                            owner_path=config["shared_lock_path"],
                            limit=5,
                            deadline_at=deadline,
                        ),
                        check,
                    )
                    done("exports", {"boundary": exports["boundary"]})
                if "native_authority" not in progress["completed"]:
                    receipt = {
                        "reconciled": True,
                        "source_sha256": sha(boundary_path),
                        "control_catalogue_sha256": catalogue.receipt(conn)["sha256"],
                        "publication_boundary": progress["exports"]["boundary"],
                        "unresolved_runs": [
                            r[0]
                            for r in conn.execute(
                                "SELECT run_id FROM jobagg_legacy_controls WHERE disposition NOT IN ('terminal_verified','reference_preserved')"
                            )
                        ],
                        "unpublished_identities": [
                            r[0] for r in conn.execute("SELECT unit FROM jobagg_dirty_current")
                        ],
                    }
                    with conn:
                        runtime_authority.activate(
                            conn,
                            epoch=epoch,
                            owner_path=config["shared_lock_path"],
                            owner_fd=fd,
                            bootstrap_receipt=receipt,
                        )
                    done("native_authority")
                with runtime_authority.controller(conn, run_id=None, owner_fd=fd):
                    if "recovered_generations" not in progress["completed"]:
                        index = ReviewedRecovery(
                            operation["recovered_generation_index"],
                            operation["recovered_generation_index_sha256"],
                        )
                        for (run_id,) in conn.execute(
                            "SELECT run_id FROM jobagg_legacy_controls WHERE disposition='generation_pending'"
                        ).fetchall():
                            check()
                            catalogue.reconcile_recovered_generation(
                                conn,
                                run_id,
                                historical_index=index,
                                owner_fd=fd,
                                owner_path=config["shared_lock_path"],
                                deadline_at=deadline,
                            )
                            conn.commit()
                        done("recovered_generations")
                    if "aliases" not in progress["completed"]:
                        aliases = json.loads(Path(operation["alias_review"]).read_text())
                        for item in aliases["records"]:
                            check()
                            artifact_locations.bind(
                                conn,
                                owner_fd=fd,
                                attempt_id=item["attempt_id"],
                                role=item["role"],
                                capture_index=item["capture_index"],
                                physical_path=item["physical_path"],
                                review_sha256=sha(Path(operation["alias_review"])),
                                deadline_at=deadline,
                            )
                        conn.commit()
                        done("aliases")
                    evidence_dir = root / "current-evidence"
                    evidence_dir.mkdir(exist_ok=True)
                    if "accepted_evidence" not in progress["completed"]:

                        def evidence_page():
                            result = accepted_evidence.backfill(
                                conn,
                                limit=32,
                                deadline_at=deadline,
                                owner_fd=fd,
                                require_retained_captures=True,
                            )
                            for record in result["records"]:
                                save(
                                    evidence_dir
                                    / (
                                        hashlib.sha256(record["job_key"].encode()).hexdigest()
                                        + ".json"
                                    ),
                                    record,
                                )
                            conn.commit()
                            return result

                        loop_pages(evidence_page, check)
                        loop_pages(lambda: _event_page(conn, fd, deadline), check)
                        done("accepted_evidence")
                    if "containment" not in progress["completed"]:
                        # Current unresolved identities retain exact task/unit holds.
                        review = json.loads(Path(operation["containment_template"]).read_text())
                        review.update(
                            epoch=epoch,
                            snapshot_root=operation["production_root"],
                            production_boundary_path=str(boundary_path),
                            fence_sha256=operation["fence_sha256"],
                            implementation_sha256=implementation_hash(),
                            configuration_path=str(candidate_path),
                            config_sha256=sha(candidate_path),
                            policy_location=candidate["policy_authority_location"],
                            catalogue_sha256=catalogue.receipt(conn)["sha256"],
                            inventory_sha256=catalogue.receipt(conn)["inventory_sha256"],
                        )
                        review["controls"] = [
                            {
                                "run_id": r[0],
                                "disposition": r[1],
                                "proof_sha256": hashlib.sha256(r[2].encode()).hexdigest(),
                                "action": "retain_history_hold_no_replay",
                                "next_condition": "Review exact original proof; preserve uncertain history without replay or retirement",
                            }
                            for r in conn.execute(
                                "SELECT run_id,disposition,proof_json FROM jobagg_legacy_controls ORDER BY ordinal"
                            )
                        ]
                        review["current_details"] = []
                        for path in sorted(evidence_dir.glob("*.json")):
                            data = json.loads(path.read_text())
                            review["current_details"].append(
                                {"job_key": data["job_key"], "path": str(path), "sha256": sha(path)}
                            )
                        _scope_current_holds(conn, fd, sha(boundary_path))
                        conn.commit()
                        review_path = root / "containment-review.json"
                        save(review_path, review)
                        result = runtime_containment.prepare(
                            conn,
                            review_path=review_path,
                            review_sha256=sha(review_path),
                            owner_fd=fd,
                            deadline_at=min(deadline, time.monotonic() + 299),
                            production_cutover=True,
                        )
                        conn.commit()
                        done("containment", result)
                check()
                if domain(conn) != boundary["domain"]:
                    raise ValueError("cutover_retained_domain_data_changed")
                if conn.execute("PRAGMA quick_check(jobs)").fetchone()[0] != "ok":
                    raise ValueError("cutover_worker_integrity_failed")
                if not runtime_containment.approved(
                    conn,
                    config_sha256=sha(candidate_path),
                    implementation_sha256=implementation_hash(),
                    policy_location=candidate["policy_authority_location"],
                ):
                    raise ValueError("cutover_containment_not_approved")
                if operation.get("install_configuration"):
                    done("handoff_intent", {"before_sha256": operation["production_config_sha256"],
                        "after_sha256": operation["candidate_config_sha256"]})
                    atomic_write_text(config_path, candidate_path.read_text())
                    if sha(config_path) != operation["candidate_config_sha256"]:
                        raise ValueError("cutover_configuration_install_failed")
                done("handoff_verified", {"maintenance_preserved": True, "provider_requests": 0,
                    "writer_enabled": False, "configuration_installed": bool(operation.get("install_configuration")),
                    "runtime": runtime_authority.validate(conn)})
        except BaseException as error:
            progress["failure"] = {"type": type(error).__name__, "message": str(error)}
            save(journal, progress)
            raise
    return progress


class ReviewedRecovery:
    """Use the existing sealed three-generation proof only for original recovery.

    This does not grant current destination/export or acceptance compatibility.
    """

    def __init__(self, path, expected):
        self.path, self.sha256 = Path(path), expected
        if (
            self.path.resolve() != self.path
            or self.path.stat().st_mode & 0o222
            or sha(self.path) != expected
        ):
            raise ValueError("cutover_recovered_generation_index_changed")

    def generation(self, generation_id, *, deadline_at):
        from jobagg.historical_occurrences import _dependency

        if sha(self.path) != self.sha256:
            raise ValueError("cutover_recovered_generation_index_changed")
        with closing(
            sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as conn:
            raw = conn.execute(
                "SELECT proof_json FROM plans WHERE generation_id=?", (generation_id,)
            ).fetchone()
            if raw is None:
                raise ValueError("cutover_original_generation_not_reviewed")
            proof = json.loads(raw[0])
            count, failures = conn.execute(
                "SELECT count(*),coalesce(sum(journal_verified!=1),0) FROM occurrences WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
            if (
                not proof["producer_complete"]
                or failures
                or count < proof["changes"] + proof["listings"]
            ):
                raise ValueError("cutover_original_generation_incomplete")
            for label in ("plan", "result"):
                _dependency(
                    proof["reference"],
                    label,
                    proof[label + "_identity"],
                    {},
                    deadline_at,
                    "cutover_original_recovery_evidence_changed",
                )
            return proof


def _event_page(conn, fd, deadline):
    result = accepted_evidence.backfill_events(conn, limit=64, deadline_at=deadline, owner_fd=fd)
    conn.commit()
    return result


def _scope_current_holds(conn, fd, evidence):
    def put(scope, identity, reason):
        runtime_holds.put(
            conn,
            scope=scope,
            identity=identity,
            reason=reason,
            evidence_sha256=evidence,
            next_condition="Review exact retained evidence before releasing this identity",
            owner_fd=fd,
        )

    for job, source, external in conn.execute(
        "SELECT j.job_key,j.source_id,j.external_id FROM jobs j JOIN jobagg_evidence_backfill b ON b.job_key=j.job_key WHERE b.disposition!='matched'"
    ):
        put("unit", "job:" + job, "current acceptance proof remains unverified")
        put(
            "task",
            task_key(source, "detail", str(external)),
            "current acceptance proof remains unverified",
        )
    for (source,) in conn.execute("SELECT source_id FROM remediation_sources"):
        try:
            accepted_evidence.resolve_listing_event(conn, source_id=source)
        except ValueError:
            put("unit", "listing:" + source, "current listing acceptance remains unverified")
            put(
                "task", task_key(source, "listing"), "current listing acceptance remains unverified"
            )
    for task, job, source, manifest in conn.execute(
        "SELECT task_id,job_key,source_id,manifest FROM remediation_documents"
    ):
        try:
            accepted_evidence.resolve_event(
                conn,
                kind="document",
                source_id=source,
                task_id=task,
                evidence_key=accepted_evidence.digest(json.loads(manifest)),
            )
        except ValueError:
            put("task", task, "current document acceptance remains unverified")
            if json.loads(manifest).get("purpose_state") not in {
                "supplementary",
                "supplementary_excluded",
            }:
                put("unit", "job:" + job, "required document acceptance remains unverified")
    for (task,) in conn.execute(
        "SELECT task_id FROM remediation_attempts WHERE finished_at IS NULL OR status='reserved'"
    ):
        put("task", task, "interrupted claim remains unverified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", type=Path, required=True)
    args = parser.parse_args()
    execute(args.operation)
