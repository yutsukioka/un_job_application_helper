"""Explicit paged export bootstrap from fenced destinations, without fetching.

This produces a current export certificate, never historical acceptance or an
empty-backlog claim. Callers retain maintenance and the actual shared owner.
"""

import hashlib
import json
import math
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import time

from jobagg import publication_fence
from jobagg.atomic_files import atomic_write_text
from jobagg.pipelines.publication_exports import publish_exports
from jobagg.runtime_source_head import owner
from jobagg.verification_budget import check, scope

SCHEMA = "publication-export-bootstrap-1"


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _save(path, value):
    atomic_write_text(path, json.dumps(value, sort_keys=True) + "\n")


def _stores(target_scope, owner_fd, owner_path):
    identity = os.fstat(owner_fd)
    stores = []
    for path in target_scope["databases"]:
        check()
        store = publication_fence.binding(path)
        if store is None:
            raise ValueError("bootstrap_export_requires_native_destination_fences")
        with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as conn:
            bound = conn.execute(
                "SELECT owner_path,owner_device,owner_inode FROM jobagg_publication_revision WHERE id=1"
            ).fetchone()
        if bound != (str(Path(owner_path).absolute()), identity.st_dev, identity.st_ino):
            raise ValueError("bootstrap_export_actual_destination_owner_required")
        stores.append(store)
    return stores


def prepare(
    root,
    *,
    registry,
    output_root,
    owner_fd,
    owner_path,
    max_export_bytes,
    max_total_export_bytes,
    deadline_at,
):
    if not math.isfinite(deadline_at):
        raise ValueError("bootstrap_export_finite_deadline_required")
    with scope(deadline_at):
        return _prepare(
            root,
            registry=registry,
            output_root=output_root,
            owner_fd=owner_fd,
            owner_path=owner_path,
            max_export_bytes=max_export_bytes,
            max_total_export_bytes=max_total_export_bytes,
        )


def _prepare(
    root, *, registry, output_root, owner_fd, owner_path, max_export_bytes, max_total_export_bytes
):
    owner(owner_fd, owner_path)
    root, output_root = Path(root).absolute(), Path(output_root).absolute()
    if (
        root.resolve() != root
        or output_root.resolve() != output_root
        or (
            root == output_root
            or root.is_relative_to(output_root)
            or output_root.is_relative_to(root)
        )
    ):
        raise ValueError("bootstrap_export_roots_must_be_direct_and_disjoint")
    if any(type(n) is not int or n <= 0 for n in (max_export_bytes, max_total_export_bytes)):
        raise ValueError("bootstrap_export_finite_byte_limits_required")
    target_scope = publication_fence.publication_scope(registry, output_root)
    stores = _stores(target_scope, owner_fd, owner_path)
    contract = {
        "schema": SCHEMA,
        "scope": target_scope,
        "stores": stores,
        "shared_owner": str(Path(owner_path).absolute()),
        "max_export_bytes": max_export_bytes,
        "max_total_export_bytes": max_total_export_bytes,
    }
    path = root / "bootstrap.json"
    if path.exists():
        value = json.loads(path.read_text())
        if value["contract"] != contract or value["contract_sha256"] != _sha(contract):
            raise ValueError("bootstrap_export_contract_changed")
        return value
    root.mkdir(parents=True, exist_ok=False)
    value = {
        "contract": contract,
        "contract_sha256": _sha(contract),
        "after": 0,
        "exports": {},
        "page_boundaries": [],
        "complete": False,
    }
    _save(path, value)
    return value


def step(root, *, owner_fd, owner_path, limit, deadline_at):
    owner(owner_fd, owner_path)
    if type(limit) is not int or limit <= 0 or not math.isfinite(deadline_at):
        raise ValueError("bootstrap_export_finite_page_and_deadline_required")
    root = Path(root).absolute()
    if root.resolve() != root:
        raise ValueError("bootstrap_export_root_alias")
    with scope(deadline_at):
        value = json.loads((root / "bootstrap.json").read_text())
        contract = value["contract"]
        if (
            contract["schema"] != SCHEMA
            or _sha(contract) != value["contract_sha256"]
            or contract["shared_owner"] != str(Path(owner_path).absolute())
        ):
            raise ValueError("bootstrap_export_contract_changed")
        # Recheck the complete fixed destination set, including prior pages.
        if _stores(contract["scope"], owner_fd, owner_path) != contract["stores"]:
            raise ValueError("bootstrap_export_destination_changed")
        if len(value["page_boundaries"]) != value["after"]:
            raise ValueError("bootstrap_export_checkpoint_incomplete")
        expected_exports = {
            path
            for store in contract["stores"][: value["after"]]
            for path in contract["scope"]["exports"][store["path"]]
        }
        if set(value["exports"]) != expected_exports:
            raise ValueError("bootstrap_export_checkpoint_scope_changed")
        for index, boundary in enumerate(value["page_boundaries"]):
            database = contract["stores"][index]["path"]
            expected_scope = {
                "output_root": contract["scope"]["output_root"],
                "databases": [database],
                "exports": {database: contract["scope"]["exports"][database]},
            }
            if not publication_fence.verify_boundary(boundary, expected_scope=expected_scope):
                raise ValueError("bootstrap_export_prior_page_changed")
            for receipt in boundary["exports"]:
                expected = {"sha256": receipt["sha256"], "size": receipt["size"]}
                if value["exports"].get(receipt["path"]) != expected:
                    raise ValueError("bootstrap_export_receipt_changed")
        if value["complete"]:
            if not publication_fence.verify_boundary(
                value["boundary"], expected_scope=contract["scope"]
            ):
                raise ValueError("bootstrap_export_completed_boundary_changed")
            return value
        for index in range(value["after"], min(value["after"] + limit, len(contract["stores"]))):
            check()
            database = Path(contract["stores"][index]["path"])
            paths = contract["scope"]["exports"][str(database)]
            remaining = contract["max_total_export_bytes"] - sum(
                r["size"] for r in value["exports"].values()
            )
            maximum = min(contract["max_export_bytes"], remaining // 4)
            if maximum <= 0:
                raise ValueError("bootstrap_export_total_byte_limit")
            generation = root / "generations" / ("bootstrap-" + str(index))
            if generation.resolve() != generation:
                raise ValueError("bootstrap_export_generation_alias")
            generation.mkdir(parents=True, exist_ok=True)
            plan = {
                "generation_root": str(generation),
                "generation_id": generation.name,
                "bootstrap_max_export_bytes": maximum,
            }
            items = [
                {
                    "database": str(database),
                    "path": path,
                    "format": "json" if path.endswith(".json") else "csv",
                    "status": "open" if "_current." in path else None,
                    "history_only": database.name == "all_jobs.sqlite3" and "_history." in path,
                }
                for path in paths
            ]
            result = publish_exports(plan, items, deadline_at=deadline_at)
            if not result["complete"]:
                return value
            # Native exporter verifies prepared and replaced bytes, with a
            # durable preimage/replacement journal. Do not adopt legacy exports.
            receipts = json.loads((generation / "exports.json").read_text())
            checkpoints = json.loads((generation / "export-checkpoints.json").read_text())[
                "entries"
            ]
            for path, receipt in receipts.items():
                if checkpoints[path]["phase"] != "replaced_verified":
                    raise ValueError("bootstrap_export_readback_incomplete")
                value["exports"][path] = {
                    "sha256": receipt["sha256"],
                    "size": checkpoints[path]["new"]["size"],
                }
            if publication_fence.binding(database) != contract["stores"][index]:
                raise ValueError("bootstrap_export_destination_changed")
            boundary = publication_fence.seal_boundary(
                [str(database)], receipts, output_root=contract["scope"]["output_root"]
            )
            if boundary is None:
                raise ValueError("bootstrap_export_page_boundary_unproven")
            value["page_boundaries"].append(boundary)
            value["after"] = index + 1
            _save(root / "bootstrap.json", value)
        if value["after"] == len(contract["stores"]):
            check()
            boundary = publication_fence.seal_boundary(
                contract["scope"]["databases"],
                value["exports"],
                output_root=contract["scope"]["output_root"],
            )
            if boundary is None or not publication_fence.verify_boundary(
                boundary, expected_scope=contract["scope"]
            ):
                raise ValueError("bootstrap_export_full_boundary_unproven")
            value.update(complete=True, boundary=boundary, completed_at_epoch=time.time())
            _save(root / "bootstrap.json", value)
        return value
