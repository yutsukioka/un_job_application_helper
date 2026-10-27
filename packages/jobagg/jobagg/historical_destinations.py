"""Explicit exact original→copy destinations, bound to the native bootstrap.

No prefix or basename rebasing. The original snapshot proves copy lineage;
current destination fences and export boundary prove the continuously held copy.
"""

import hashlib
import json
from pathlib import Path

from jobagg import runtime_authority, publication_fence
from jobagg.publication_snapshot import stable_file
from jobagg.runtime_source_head import owner


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def prepare(conn, snapshot_path, *, boundary, scope, owner_fd, deadline_at):
    state = runtime_authority.validate(conn)
    owner(owner_fd, state["owner_path"])
    if state["phase"] != "idle":
        raise ValueError("historical_copy_requires_idle_authority")
    path = Path(snapshot_path).absolute()
    if path.resolve() != path or path.stat().st_size > 64 * 1024**2:
        raise ValueError("historical_copy_snapshot_aliased_or_oversized")
    record = stable_file(path, deadline_at=deadline_at)
    bootstrap = json.loads(state["bootstrap_json"])
    if bootstrap.get("source_sha256") != record["sha256"]:
        raise ValueError("historical_copy_snapshot_not_native_bootstrap")
    snapshot = json.loads(path.read_bytes())
    if stable_file(path, deadline_at=deadline_at)["sha256"] != record["sha256"]:
        raise ValueError("historical_copy_snapshot_changed")
    root = path.parent
    if (
        snapshot.get("schema") != "jobagg-fenced-rehearsal-snapshot-1"
        or snapshot.get("complete") is not True
        or snapshot.get("snapshot_root") != str(root)
        or snapshot.get("owner") != state["owner_path"]
        or not publication_fence.verify_boundary(boundary, expected_scope=scope)
    ):
        raise ValueError("historical_copy_boundary_invalid")
    targets = {v["path"] for v in boundary["databases"]}
    rows, copies = {}, set()
    for row in snapshot["files"]:
        relative = Path(row["copy"])
        if relative.parent != Path("output") or relative.suffix != ".sqlite3":
            continue
        original = Path(row["source"])
        copy = root / relative
        if (
            not original.is_absolute()
            or ".." in original.parts
            or original.name != copy.name
            or copy.resolve() != copy
            or original == copy
            or original.is_relative_to(root)
            or root.is_relative_to(original.parent)
            or str(original) in rows
            or str(copy) in copies
            or str(copy) not in targets
            or not isinstance(row.get("sha256"), str)
            or len(row["sha256"]) != 64
        ):
            raise ValueError("historical_copy_mapping_not_exact_and_disjoint")
        copies.add(str(copy))
        rows[str(original)] = {
            "copy": str(copy),
            "snapshot_file_sha256": row["sha256"],
            "source_identity": row["source_identity"],
            "binding": publication_fence.binding(copy),
        }
    if copies != targets:
        raise ValueError("historical_copy_mapping_incomplete")
    value = {
        "schema": "historical-destination-copy-map-1",
        "snapshot_sha256": record["sha256"],
        "snapshot_path": str(path),
        "source_epoch": state["epoch"],
        "owner": state["owner_path"],
        "worker_database": conn.execute("PRAGMA database_list").fetchone()[2],
        "boundary_sha256": boundary["sha256"],
        "scope": scope,
        "destinations": rows,
    }
    value["sha256"] = digest(value)
    return value


class Destinations:
    def __init__(self, conn, receipt, *, boundary, owner_fd, deadline_at):
        state = runtime_authority.validate(conn)
        owner(owner_fd, state["owner_path"])
        if (
            receipt.get("schema") != "historical-destination-copy-map-1"
            or receipt.get("sha256") != digest({k: v for k, v in receipt.items() if k != "sha256"})
            or receipt.get("source_epoch") != state["epoch"]
            or receipt.get("owner") != state["owner_path"]
            or receipt.get("worker_database") != conn.execute("PRAGMA database_list").fetchone()[2]
            or receipt.get("snapshot_sha256")
            != json.loads(state["bootstrap_json"]).get("source_sha256")
            or receipt.get("boundary_sha256") != boundary["sha256"]
            or not publication_fence.verify_boundary(boundary, expected_scope=receipt["scope"])
        ):
            raise ValueError("historical_copy_mapping_binding_changed")
        rebuilt = prepare(
            conn,
            receipt["snapshot_path"],
            boundary=boundary,
            scope=receipt["scope"],
            owner_fd=owner_fd,
            deadline_at=deadline_at,
        )
        if rebuilt != receipt:
            raise ValueError("historical_copy_mapping_not_snapshot_derived")
        self.rows, self.receipt = receipt["destinations"], receipt
        copies = [row["copy"] for row in self.rows.values()]
        if len(copies) != len(set(copies)) or set(copies) != {
            v["path"] for v in boundary["databases"]
        }:
            raise ValueError("historical_copy_mapping_incomplete")
        self.validate()

    def validate(self):
        for row in self.rows.values():
            if publication_fence.binding(row["copy"]) != row["binding"]:
                raise ValueError("historical_copy_destination_fence_changed")

    def resolve(self, original):
        row = self.rows.get(str(original))
        if row is None:
            raise ValueError("historical_copy_destination_not_reviewed:" + str(original))
        if publication_fence.binding(row["copy"]) != row["binding"]:
            raise ValueError("historical_copy_destination_fence_changed")
        return Path(row["copy"])
