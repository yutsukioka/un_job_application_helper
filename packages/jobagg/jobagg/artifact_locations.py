"""Exact owner-reviewed artifact locators in the existing native control ledger.

A locator certifies byte identity only. It cannot create an accepted attempt,
worker exit, source success, publication proof or retirement permission.
"""

import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from jobagg import runtime_authority
from jobagg.publication_snapshot import stable_file, _stamp, _record_stamp
from jobagg.runtime_source_head import owner

PREFIX = "artifact-location/"


def _key(original, expected, domain):
    return (
        PREFIX + hashlib.sha256(json.dumps([str(original), expected, domain]).encode()).hexdigest()
    )


def _table(conn):
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='jobagg_legacy_review_holds'"
        ).fetchone()
    )


def resolve(conn, original, expected, *, domain="raw", strict=True):
    original = Path(original).absolute()
    row = None
    if _table(conn):
        row = conn.execute(
            "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?",
            (_key(original, expected, domain),),
        ).fetchone()
    if row is None:
        if strict and original.resolve() != original:
            raise ValueError("artifact_alias_requires_exact_reviewed_locator")
        return original
    value = json.loads(row[0])
    state = runtime_authority.state(conn)
    path = Path(value["physical_path"])
    if (
        value.get("schema") != "native-artifact-location-1"
        or value.get("original") != str(original)
        or value.get("sha256") != expected
        or value.get("domain") != domain
        or state is None
        or value.get("epoch") != state["epoch"]
        or value.get("owner") != state["owner_path"]
        or value.get("worker_database") != conn.execute("PRAGMA database_list").fetchone()[2]
        or path.resolve() != path
        or list(_stamp(path.stat())) != value["identity"]
        or original.resolve() != path
    ):
        raise ValueError("artifact_reviewed_locator_changed")
    return path


def reference(conn, attempt_id, role, *, capture_index=None, deadline_at):
    """Derive the expected digest from native receipts, never from target bytes."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    row = cursor.execute(
        "SELECT * FROM remediation_attempts WHERE attempt_id=?", (attempt_id,)
    ).fetchone()
    if row is None:
        raise ValueError("artifact_locator_attempt_unknown")
    receipt = json.loads(row["evidence"])
    path_key, hash_key = {
        "detail": ("detail_path", "detail_sha256"),
        "listing": ("frame_path", "frame_sha256"),
        "document": ("manifest", "sha256"),
    }[row["kind"]]
    original, expected = receipt[path_key], receipt[hash_key]
    if role == "artifact":
        return original, expected, "raw"
    if row["kind"] != "detail" or type(capture_index) is not int or not 0 <= capture_index < 64:
        raise ValueError("artifact_locator_capture_scope_required")
    parent = resolve(conn, original, expected)
    value = read_control(parent, expected, deadline_at)
    captures = value["proof"]["captures"]
    ref = captures[capture_index]
    if role == "capture":
        return ref["path"], ref["sha256"], "raw"
    if role != "body":
        raise ValueError("artifact_locator_role_unknown")
    metadata = read_control(resolve(conn, ref["path"], ref["sha256"]), ref["sha256"], deadline_at)
    if metadata.get("body_captured") is not True:
        raise ValueError("artifact_locator_body_not_recorded")
    return metadata["artifact"], metadata["body_sha256"], "gzip_body"


def read_control(path, expected, deadline):
    from jobagg.publication_snapshot import _json_document

    path = Path(path)
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError("artifact_locator_control_oversized")
    value, record = _json_document(path, deadline_at=deadline)
    if record["sha256"] != expected:
        raise ValueError("artifact_locator_parent_changed")
    return value


def bind(
    conn,
    *,
    attempt_id,
    role,
    physical_path,
    owner_fd,
    review_sha256,
    deadline_at,
    capture_index=None,
    max_bytes=64 * 1024**2,
):
    if (
        not re.fullmatch("[a-f0-9]{64}", str(review_sha256))
        or not math.isfinite(deadline_at)
        or type(max_bytes) is not int
        or not 0 < max_bytes <= 128 * 1024**2
    ):
        raise ValueError("bounded_artifact_locator_review_required")
    state = runtime_authority.validate(conn)
    owner(owner_fd, state["owner_path"])
    if state["phase"] != "idle":
        raise ValueError("artifact_locator_requires_idle_native_source")
    original, expected, domain = reference(
        conn, attempt_id, role, capture_index=capture_index, deadline_at=deadline_at
    )
    original, path = Path(original).absolute(), Path(physical_path).absolute()
    if path.resolve() != path or original.resolve() != path or path.stat().st_size > max_bytes:
        raise ValueError("artifact_locator_exact_existing_alias_required")
    record = stable_file(path, deadline_at=deadline_at)
    actual = record["sha256"]
    if domain == "gzip_body":
        digest = hashlib.sha256()
        total = 0
        with gzip.open(path, "rb") as stream:
            while block := stream.read(1024**2):
                if time.monotonic() >= deadline_at:
                    raise TimeoutError("artifact_locator_deadline")
                total += len(block)
                if total > max_bytes:
                    raise ValueError("artifact_locator_body_bound")
                digest.update(block)
        actual = digest.hexdigest()
    if actual != expected or tuple(_stamp(path.stat())) != _record_stamp(record):
        raise ValueError("artifact_locator_original_digest_mismatch")
    value = {
        "schema": "native-artifact-location-1",
        "epoch": state["epoch"],
        "owner": state["owner_path"],
        "worker_database": conn.execute("PRAGMA database_list").fetchone()[2],
        "original": str(original),
        "physical_path": str(path),
        "sha256": expected,
        "domain": domain,
        "identity": list(_record_stamp(record)),
        "physical_sha256": record["sha256"],
        "review_sha256": review_sha256,
        "attempt_id": attempt_id,
        "role": role,
        "capture_index": capture_index,
        "certifies_acceptance_or_publication": False,
    }
    key = _key(original, expected, domain)
    prior = conn.execute(
        "SELECT review_json FROM jobagg_legacy_review_holds WHERE run_id=?", (key,)
    ).fetchone()
    if prior:
        old = json.loads(prior[0])
        if any(
            old[k] != value[k]
            for k in (
                "original",
                "physical_path",
                "sha256",
                "domain",
                "identity",
                "epoch",
                "owner",
                "worker_database",
            )
        ):
            raise ValueError("artifact_locator_prior_binding_differs")
        resolve(conn, original, expected, domain=domain)
        return old
    conn.execute(
        "INSERT INTO jobagg_legacy_review_holds VALUES(?,?)",
        (key, json.dumps(value, sort_keys=True)),
    )
    return value
