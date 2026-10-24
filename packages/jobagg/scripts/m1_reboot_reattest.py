#!/usr/bin/env python3
"""Explicit copy-only reboot renewal; never relax ordinary identity guards.

Run under the existing network/production-write-denying rehearsal sandbox.
Old receipts stay intact. A partial operation resumes its exact immutable plan;
no SQLite backup is restored and no charge, hold, epoch or revision is reset.
"""

from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import time
from types import SimpleNamespace

from m1_stage_snapshot import disjoint, fenced_owner, sha, stamp
from jobagg import publication_fence as fence
from jobagg.atomic_files import atomic_write_text
from jobagg.policy_authority import PolicyAuthority, _schema_hash
from jobagg.runtime_source_head import owner


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write(path, value):
    atomic_write_text(path, json.dumps(value, sort_keys=True, indent=2) + "\n")


def renew_identity(path, expected, actual, *, mount_root, old_device, new_device):
    """Only an exact old-device transition on the independently verified mount."""
    path = Path(path)
    if path.resolve() != path:
        raise ValueError("reattest_aliased_dependency")
    changed = {key for key in expected if expected[key] != actual.get(key)}
    if not changed:
        return actual
    if (
        changed != {"device"}
        or expected.get("device") != old_device
        or actual.get("device") != new_device
        or not path.is_relative_to(mount_root)
    ):
        raise ValueError("reattest_non_device_change_requires_review:" + str(path))
    return actual


def renew_destination(path, expected, *, owner_fd, owner_path, old_device, new_device):
    """Narrow native control grant: update only the admitted owner device."""
    owner(owner_fd, owner_path)
    if fence.binding(path) != {**expected, "device": new_device}:
        raise ValueError("reattest_destination_binding_changed")
    with closing(sqlite3.connect(path)) as conn:
        state = fence._validate(conn)
        if (
            state["owner_path"] != str(owner_path)
            or state["owner_inode"] != os.fstat(owner_fd).st_ino
            or state["owner_device"] not in {old_device, new_device}
        ):
            raise ValueError("reattest_destination_owner_changed")

        # SQL transaction authorizer restricts this preparation grant to the
        # one identity column. Domain mutation clocks/rows cannot be rewritten.
        def authorize(action, first, second, database, trigger):
            if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE}:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_UPDATE and (first, second) != (fence.TABLE, "owner_device"):
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT":
                try:
                    owner(owner_fd, owner_path)
                except (ValueError, OSError):
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        conn.create_function(
            "jobagg_publication_control_allowed", 0, lambda: owner(owner_fd, owner_path) or 1
        )
        try:
            with conn:
                conn.execute(f"UPDATE {fence.TABLE} SET owner_device=? WHERE id=1", (new_device,))
        finally:
            conn.create_function("jobagg_publication_control_allowed", 0, lambda: 0)
    if fence.binding(path) != {**expected, "device": new_device}:
        raise ValueError("reattest_destination_changed_during_commit")


def require_inactive_source(path):
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='jobagg_runtime_state'").fetchone():
            raise ValueError("reattest_requires_inactive_copied_source")


def execute(root, operation, *, seconds, maximum_bytes, resume=False):
    root, operation = Path(root).absolute(), Path(operation).absolute()
    if root.resolve() != root or operation.parent != root or operation.resolve() != operation:
        raise ValueError("reattest_exact_copy_and_operation_root_required")
    snapshot = json.loads((root / "snapshot.json").read_text())
    config = json.loads((root / "production-config.original.json").read_text())
    if snapshot.get("complete") is not True or snapshot["snapshot_root"] != str(root):
        raise ValueError("reattest_exact_complete_copy_required")
    production_output = Path(
        config["publication_argv"][config["publication_argv"].index("--output-dir") + 1]
    )
    disjoint(
        root,
        [
            Path(config["publication_worker_database"]).parent,
            Path(config["shared_lock_path"]).parent,
            production_output,
            Path(config["state_dir"]),
            Path(config["attempt_state_dir"]),
        ],
    )
    if sha(Path(snapshot["production_config"])) != snapshot["production_config_sha256"]:
        raise ValueError("reattest_production_config_changed")
    mount = Path(config["storage_guard"]["mount_root"])
    sentinel = Path(config["storage_guard"]["sentinel_path"])
    if sha(sentinel) != config["storage_guard"]["sentinel_sha256"]:
        raise ValueError("reattest_storage_sentinel_changed")
    expected_uuid = json.loads(sentinel.read_text())["volume_uuid"]
    # Bounded read-only OS query. UUID is independently pinned by the original
    # configuration's sentinel digest, not supplied as a new operator guess.
    current = plistlib.loads(
        subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", str(mount)],
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout
    )
    if current.get("VolumeUUID") != expected_uuid or current.get("MountPoint") != str(mount):
        raise ValueError("reattest_persistent_volume_differs")
    old_device, old_inode = snapshot["owner_identity"]
    owner_path = Path(config["shared_lock_path"])
    observed = owner_path.stat()
    if (
        observed.st_ino != old_inode
        or observed.st_dev != mount.stat().st_dev
        or root.stat().st_dev != observed.st_dev
    ):
        raise ValueError("reattest_actual_owner_or_copy_volume_changed")
    new_device = observed.st_dev
    if old_device == new_device:
        raise ValueError("reattest_requires_observed_device_transition")
    if (
        type(seconds) not in (int, float)
        or not 0 < seconds < 3600
        or type(maximum_bytes) is not int
        or maximum_bytes <= 0
    ):
        raise ValueError("reattest_finite_bounds_required")
    end = time.monotonic() + seconds
    read_bytes = 0

    def check():
        if time.monotonic() >= end:
            raise TimeoutError("reattest_deadline_retain_journal")

    def checked_sha(path):
        nonlocal read_bytes
        check()
        path = Path(path)
        # Every payload read remains on SSD; no HDD copying or rescanning.
        if not (
            path.is_relative_to(mount)
            or path.is_relative_to(Path(snapshot["production_config"]).parents[2])
        ):
            raise ValueError("reattest_payload_read_outside_reviewed_ssd_roots")
        before = stamp(path)
        read_bytes += before[2]
        if read_bytes > maximum_bytes:
            raise ValueError("reattest_read_ceiling_retain_journal")
        result = sha(path, check)
        if before != stamp(path):
            raise ValueError("reattest_content_changed_during_verification")
        return result

    with fenced_owner(config, snapshot["fence_sha256"]) as fd:
        # Resume must reject an active source before any destination/head write.
        require_inactive_source(root / "worker/jobs.sqlite3")
        if resume:
            plan = json.loads((operation / "plan.json").read_text())
            if plan["sha256"] != digest({k: v for k, v in plan.items() if k != "sha256"}):
                raise ValueError("reattest_plan_changed")
        else:
            operation.mkdir(mode=0o700, exist_ok=False)
            boundary = json.loads((root / "full-export-bootstrap/bootstrap.json").read_text())[
                "boundary"
            ]
            head = json.loads((root / "policy/authority.json").read_text())
            with closing(
                sqlite3.connect((root / "worker/jobs.sqlite3").as_uri() + "?mode=ro", uri=True)
            ) as conn:
                if conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='jobagg_runtime_state'"
                ).fetchone():
                    raise ValueError("reattest_requires_inactive_copied_source")
                dependencies = [
                    list(row)
                    for row in conn.execute(
                        "SELECT path,sha256,identity_json FROM jobagg_legacy_dependencies ORDER BY path"
                    )
                ]
                holds = list(
                    conn.execute(
                        "SELECT disposition,count(*) FROM jobagg_legacy_controls GROUP BY disposition"
                    )
                )
            plan = {
                "schema": "jobagg-copy-reboot-renewal-1",
                "copy_root": str(root),
                "volume_uuid": expected_uuid,
                "mount_root": str(mount),
                "old_device": old_device,
                "new_device": new_device,
                "owner_inode": old_inode,
                "snapshot_sha256": sha(root / "snapshot.json"),
                "original_boundary": boundary,
                "original_policy_head": head,
                "dependencies": dependencies,
                "original_dispositions": [list(row) for row in holds],
            }
            plan["sha256"] = digest(plan)
            write(operation / "plan.json", plan)
            (operation / "plan.json").chmod(0o400)
        if any(
            plan[k] != v
            for k, v in {
                "copy_root": str(root),
                "volume_uuid": expected_uuid,
                "mount_root": str(mount),
                "old_device": old_device,
                "new_device": new_device,
                "owner_inode": old_inode,
                "snapshot_sha256": sha(root / "snapshot.json"),
            }.items()
        ):
            raise ValueError("reattest_planned_boundary_changed")

        def renew(path, expected, actual):
            check()
            return renew_identity(
                path,
                expected,
                actual,
                mount_root=mount,
                old_device=old_device,
                new_device=new_device,
            )

        boundary, head = plan["original_boundary"], plan["original_policy_head"]
        if boundary["sha256"] != digest({k: v for k, v in boundary.items() if k != "sha256"}):
            raise ValueError("reattest_original_boundary_changed")
        for mutable in (
            root / "worker",
            root / "worker/jobs.sqlite3",
            root / "output",
            root / "policy",
            root / "policy/authority.sqlite3",
            root / "policy/authority.json",
        ):
            if mutable.resolve(strict=True) != mutable:
                raise ValueError("reattest_mutable_copy_alias")
        production_worker = Path(config["publication_worker_database"])
        if (root / "worker/jobs.sqlite3").samefile(production_worker):
            raise ValueError("reattest_worker_is_production_file")
        if boundary.get("output_root") != str(root / "output"):
            raise ValueError("reattest_output_scope_differs")
        for item in [*boundary["databases"], *boundary["exports"]]:
            path = Path(item["path"])
            if path.parent != root / "output" or path.resolve(strict=True) != path:
                raise ValueError("reattest_destination_outside_copy")
            counterpart = production_output / path.name
            if counterpart.exists() and path.samefile(counterpart):
                raise ValueError("reattest_destination_is_production_file")
        if any(Path(name).name != name for name in head["legacy_boundaries"]):
            raise ValueError("reattest_legacy_policy_path_differs")
        if any(
            not (
                Path(name).is_relative_to(root / "policy")
                or Path(name) == root / "policy-marker.json"
            )
            for name in head["immutable_controls"]
        ):
            raise ValueError("reattest_policy_controls_outside_copy")
        if (
            len(plan["dependencies"]) > 100000
            or len(boundary["databases"]) > 100
            or len(boundary["exports"]) > 400
        ):
            raise ValueError("reattest_inventory_ceiling")
        # Revalidate all original producer hashes, with metadata-only retention
        # for originally unverified auxiliary history. Such rows stay held.
        for store in boundary["databases"]:
            renew(store["path"], store, fence.binding(store["path"]))
        export_manifest = {}
        for item in boundary["exports"]:
            renew(
                item["path"],
                {k: v for k, v in item.items() if k != "sha256"},
                fence._file_identity(item["path"]),
            )
            if checked_sha(item["path"]) != item["sha256"]:
                raise ValueError("reattest_export_content_changed")
            export_manifest[item["path"]] = {"sha256": item["sha256"]}
        renewed_head = json.loads(json.dumps(head))
        for path, identity in head["immutable_controls"].items():
            renewed_head["immutable_controls"][path] = renew(
                path, identity, fence._file_identity(path)
            )
        for name, path in (("database", root / "policy/authority.sqlite3"), ("owner", owner_path)):
            expected = dict(zip(("device", "inode"), head[name + "_identity"]))
            s = path.stat()
            actual = {"device": s.st_dev, "inode": s.st_ino}
            renew(path, expected, actual)
            renewed_head[name + "_identity"] = [s.st_dev, s.st_ino]
        for name, expected in head["legacy_boundaries"].items():
            path = root / "policy" / name
            s = path.stat()
            actual = [s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns]
            renew(
                path,
                dict(zip(("device", "inode", "mtime_ns", "ctime_ns"), expected)),
                dict(zip(("device", "inode", "mtime_ns", "ctime_ns"), actual)),
            )
            renewed_head["legacy_boundaries"][name] = actual
        new_dependencies = []
        for path, expected_hash, identity in plan["dependencies"]:
            s = stamp(Path(path))
            actual = dict(zip(("device", "inode", "size", "mtime_ns", "ctime_ns"), s))
            renew(path, dict(zip(actual, json.loads(identity))), actual)
            if expected_hash is not None and checked_sha(path) != expected_hash:
                raise ValueError("reattest_hashed_control_changed")
            new_dependencies.append((json.dumps(list(s)), path))
        # Original head must still match its SQL transaction/schema. Identity
        # renewal grants no authority to rewind either counter or any charge.
        policy_db = root / "policy/authority.sqlite3"
        policy_sha = checked_sha(policy_db)
        with closing(sqlite3.connect(policy_db.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            state = dict(conn.execute("SELECT * FROM authority WHERE id=1").fetchone())
            if (
                head.get("pending") is not False
                or _schema_hash(conn) != head["schema_sha256"]
                or any(state[k] != head[k] for k in ("epoch", "revision", "transaction_id"))
            ):
                raise ValueError("reattest_policy_transaction_or_schema_changed")
        current_head = json.loads((root / "policy/authority.json").read_text())
        if current_head not in (head, renewed_head):
            raise ValueError("reattest_policy_head_changed")
        write(
            operation / "journal.json",
            {
                "plan_sha256": plan["sha256"],
                "stage": "verified_before_mutation",
                "read_bytes": read_bytes,
            },
        )
        for index, store in enumerate(boundary["databases"]):
            check()
            write(
                operation / "journal.json",
                {
                    "plan_sha256": plan["sha256"],
                    "stage": "destination_identity_pending",
                    "ordinal": index,
                },
            )
            renew_destination(
                store["path"],
                store,
                owner_fd=fd,
                owner_path=owner_path,
                old_device=old_device,
                new_device=new_device,
            )
        write(root / "policy/authority.json", renewed_head)
        if (
            PolicyAuthority(SimpleNamespace(root=root / "policy", owner=owner_path)).revision()
            != head["revision"]
            or checked_sha(policy_db) != policy_sha
        ):
            raise ValueError("reattest_policy_changed")
        with closing(sqlite3.connect(root / "worker/jobs.sqlite3")) as conn:
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='jobagg_runtime_state'"
            ).fetchone():
                raise ValueError("reattest_source_was_activated")
            with conn:
                conn.executemany(
                    "UPDATE jobagg_legacy_dependencies SET identity_json=? WHERE path=?",
                    new_dependencies,
                )
            if [
                list(row)
                for row in conn.execute(
                    "SELECT disposition,count(*) FROM jobagg_legacy_controls GROUP BY disposition"
                )
            ] != plan["original_dispositions"]:
                raise ValueError("reattest_historical_holds_changed")
        renewed_boundary = fence.seal_boundary(
            [v["path"] for v in boundary["databases"]], export_manifest, output_root=root / "output"
        )
        if not renewed_boundary or not fence.verify_boundary(renewed_boundary):
            raise ValueError("reattest_renewed_export_boundary_invalid")
        write(operation / "publication-boundary.json", renewed_boundary)
        result = {
            "schema": plan["schema"],
            "plan_sha256": plan["sha256"],
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "volume_uuid": expected_uuid,
            "old_device": old_device,
            "new_device": new_device,
            "owner_inode": old_inode,
            "read_bytes": read_bytes,
            "old_boundary_sha256": boundary["sha256"],
            "new_boundary_sha256": renewed_boundary["sha256"],
            "policy_database_unchanged_sha256": policy_sha,
            "policy_revision_unchanged": head["revision"],
            "historical_dispositions_unchanged": plan["original_dispositions"],
            "HDD_payload_reads": 0,
            "payload_copies": 0,
            "production_activated": False,
            "source_runtime_activated": False,
            "complete": True,
        }
        write(operation / "receipt.json", result)
        write(
            operation / "journal.json",
            {"plan_sha256": plan["sha256"], "stage": "complete", "receipt_sha256": digest(result)},
        )
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("operation", type=Path)
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--maximum-bytes", type=int, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            execute(
                args.root,
                args.operation,
                seconds=args.seconds,
                maximum_bytes=args.maximum_bytes,
                resume=args.resume,
            ),
            indent=2,
        )
    )
