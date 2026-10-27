"""Checkpointed offline occurrence index from exact immutable producer plans.

A completed legacy producer plus its exact per-destination commit journals can
prove historical committed acceptance. This does not manufacture a modern
readback flag or imply current exports, completeness, freshness or retirement.
"""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from jobagg.historical_destinations import digest
from jobagg.historical_plans import read_plan
from jobagg.publication_snapshot import _json_document, stable_file, _stamp, _record_stamp

SCHEMA = "historical-committed-occurrences-1"


def _check(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("historical_occurrence_index_deadline")


def identity(kind, artifact, artifact_sha256=None):
    if kind == "detail":
        job = artifact["job"]
        return digest(
            [
                job["source_id"],
                str(job["external_id"]),
                artifact["proof"],
                hashlib.sha256(job["description"].encode()).hexdigest(),
            ]
        )
    if kind == "listing":
        return digest([artifact["source_id"], artifact_sha256])
    return digest(artifact)


def location(reference, label):
    """An exact reviewed physical locator; logical producer bindings stay intact."""
    original = Path(reference[label + "_path"])
    physical = Path(reference.get(label + "_location", original))
    if (
        not original.is_absolute()
        or ".." in original.parts
        or physical.resolve() != physical
        or not physical.is_absolute()
        or ".." in physical.parts
    ):
        raise ValueError("historical_generation_locator_invalid")
    if physical != original and not re.fullmatch(
        "[a-f0-9]{64}", reference.get("locator_review_sha256", "")
    ):
        raise ValueError("historical_generation_relocation_review_required")
    return physical


def _dependency(reference, label, expected_identity, archives, deadline_at, error):
    _check(deadline_at)
    path = location(reference, label)
    grant = (archives or {}).get(str(path))
    if grant is not None:
        if label != "plan" or grant.value["kind"] != "plan" or grant.path != path:
            raise ValueError("historical_occurrence_wrong_representation_scope")
        grant.check(deadline_at=deadline_at, expected_sha256=reference[label + "_sha256"],
                    original_identity=expected_identity)
    elif path.resolve() != path or list(_stamp(path.stat())) != expected_identity:
        raise ValueError(error)


def prepare(conn, *, references, mapping_sha256):
    if not isinstance(references, list) or not 0 < len(references) <= 512:
        raise ValueError("finite_historical_generation_inventory_required")
    if len({r["plan_path"] for r in references}) != len(references) or len(
        {Path(r["plan_path"]).parent.name for r in references}
    ) != len(references):
        raise ValueError("historical_generation_inventory_duplicate")
    for reference in references:
        for label in ("plan", "result"):
            location(reference, label)
    conn.executescript("""CREATE TABLE IF NOT EXISTS index_state(id INTEGER PRIMARY KEY,contract TEXT,complete INTEGER);
    CREATE TABLE IF NOT EXISTS plans(generation_id TEXT PRIMARY KEY,proof_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS occurrences(kind TEXT,identity_sha256 TEXT,generation_id TEXT,publication_key TEXT,
      job_key TEXT,paths_json TEXT,journal_verified INTEGER,reason TEXT,
      PRIMARY KEY(kind,identity_sha256,generation_id,publication_key));
    CREATE INDEX IF NOT EXISTS occurrence_identity ON occurrences(kind,identity_sha256,journal_verified);""")
    contract = json.dumps(
        {"schema": SCHEMA, "references": references, "mapping_sha256": mapping_sha256},
        sort_keys=True,
    )
    old = conn.execute("SELECT contract FROM index_state WHERE id=1").fetchone()
    if old and old[0] != contract:
        raise ValueError("historical_generation_index_contract_changed")
    conn.execute("INSERT OR IGNORE INTO index_state VALUES(1,?,0)", (contract,))


def step(conn, *, mapping, limit, deadline_at, max_plan_bytes, archives=None):
    if type(limit) is not int or not 0 < limit <= 64:
        raise ValueError("finite_historical_generation_page_required")
    contract = json.loads(conn.execute("SELECT contract FROM index_state WHERE id=1").fetchone()[0])
    if contract["mapping_sha256"] != mapping.receipt["sha256"]:
        raise ValueError("historical_generation_mapping_changed")
    done = {row[0] for row in conn.execute("SELECT generation_id FROM plans")}
    connections = {}
    processed, bytes_read = 0, 0
    try:
        for reference in contract["references"]:
            _check(deadline_at)
            generation = Path(reference["plan_path"]).parent.name
            if generation in done:
                continue
            if processed >= limit:
                break
            if location(reference, "result").stat().st_size > 16 * 1024**2:
                raise ValueError("historical_generation_result_oversized")
            result, result_record = _json_document(
                location(reference, "result"), deadline_at=deadline_at
            )
            if result_record["sha256"] != reference["result_sha256"]:
                raise ValueError("historical_generation_result_changed")
            plan, record = read_plan(
                location(reference, "plan"),
                expected_sha256=reference["plan_sha256"],
                deadline_at=deadline_at,
                max_bytes=max_plan_bytes,
                archive=(archives or {}).get(str(location(reference, "plan"))),
            )
            bytes_read += record["bytes"]
            if (
                plan.get("version") != "incremental-live-publication-1"
                or result.get("version") != plan["version"]
                or plan.get("generation_id") != generation
                or result.get("generation_id") != generation
                or not re.fullmatch("[a-f0-9]{32}", generation)
                or Path(plan.get("generation_root", "")) != Path(reference["plan_path"]).parent
                or result.get("plan_sha256") != record["sha256"]
                or result.get("plan_path") != reference["plan_path"]
                or result.get("request_identity_sha256") != plan.get("request_identity_sha256")
                or result.get("observation_set_sha256") != plan.get("observation_set_sha256")
            ):
                raise ValueError("historical_generation_immutable_binding_changed")
            if result.get("changed_jobs") != len(plan.get("changes", [])) or result.get(
                "published_listing_frames"
            ) != len(plan.get("listing_frames", [])):
                raise ValueError("historical_generation_result_counts_changed")
            completed = (
                result.get("status") == "published"
                and result.get("state") == "complete"
                and result.get("database_transactions_complete") is True
                and result.get("readback_verified", True) is True
            )
            records = []
            for kind, candidates in [
                ("detail", plan.get("changes", [])),
                ("listing", plan.get("listing_frames", [])),
            ]:
                for candidate in candidates:
                    _check(deadline_at)
                    if kind == "detail":
                        artifact = {"job": candidate["worker_row"], "proof": candidate["proof"]}
                        key = candidate["worker_row"].get("job_key")
                        if not key:
                            key = (
                                candidate["worker_row"]["source_id"]
                                + ":"
                                + str(candidate["worker_row"]["external_id"])
                            )
                    else:
                        artifact, key = candidate, "source:" + candidate["source_id"]
                    identities = [(kind, identity(kind, artifact, candidate.get("frame_sha256")))]
                    identities.extend(
                        ("document", identity("document", d))
                        for d in candidate.get("documents", [])
                    )
                    original = [row["path"] for row in candidate["destinations"]]
                    paths, reason = [], None
                    try:
                        paths = [str(mapping.resolve(p)) for p in original]
                    except ValueError as error:
                        reason = str(error)
                    verified = completed and reason is None and len(set(paths)) >= 2
                    for path in paths:
                        _check(deadline_at)
                        if path not in connections:
                            db = sqlite3.connect(
                                Path(path).as_uri() + "?mode=ro", uri=True, timeout=1
                            )
                            db.execute("PRAGMA cache_size=-1024")
                            db.set_progress_handler(
                                lambda: int(time.monotonic() >= deadline_at), 256
                            )
                            connections[path] = db
                        row = (
                            connections[path]
                            .execute(
                                "SELECT generation_id,job_key FROM jobagg_publication_receipts WHERE publication_key=?",
                                (candidate["publication_key"],),
                            )
                            .fetchone()
                        )
                        verified &= bool(row and row[0] == generation and row[1] == key)
                    if not verified and reason is None:
                        reason = "producer_incomplete_or_exact_destination_commit_missing"
                    for item_kind, item_identity in identities:
                        records.append(
                            (
                                item_kind,
                                item_identity,
                                generation,
                                candidate["publication_key"],
                                key,
                                json.dumps(sorted(paths)),
                                int(verified),
                                reason,
                            )
                        )
            proof = {
                "reference": reference,
                "plan_identity": record["identity"],
                "result_identity": list(_record_stamp(result_record)),
                "producer_complete": completed,
                "legacy_readback_flag_present": "readback_verified" in result,
                "changes": len(plan.get("changes", [])),
                "listings": len(plan.get("listing_frames", [])),
                "archive_beforeimages": sorted(
                    {
                        (d["beforeimage_path"], d["beforeimage_sha256"])
                        for c in plan.get("changes", []) + plan.get("listing_frames", [])
                        for d in c["destinations"]
                        if "beforeimage_path" in d
                    }
                ),
                "source_ids": sorted(
                    {c["worker_row"]["source_id"] for c in plan.get("changes", [])}
                    | {f["source_id"] for f in plan.get("listing_frames", [])}
                ),
            }
            mapping.validate()
            for label in ("plan", "result"):
                _dependency(reference, label, proof[label + "_identity"], archives, deadline_at,
                            "historical_generation_dependency_changed_before_checkpoint")
            with conn:
                conn.executemany("INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?)", records)
                conn.execute(
                    "INSERT INTO plans VALUES(?,?)", (generation, json.dumps(proof, sort_keys=True))
                )
            processed += 1
        complete = conn.execute("SELECT count(*) FROM plans").fetchone()[0] == len(
            contract["references"]
        )
        with conn:
            conn.execute("UPDATE index_state SET complete=? WHERE id=1", (int(complete),))
        return {"processed": processed, "complete": complete, "plan_bytes_read": bytes_read}
    finally:
        for db in connections.values():
            db.close()


class Index:
    def __init__(self, path, *, expected_sha256, mapping, deadline_at, archives=None):
        self.path, self.mapping, self.deadline = Path(path), mapping, deadline_at
        self.archives = dict(archives or {})
        if self.path.stat().st_size > 256 * 1024**2:
            raise ValueError("historical_occurrence_index_oversized")
        record = stable_file(self.path, deadline_at=deadline_at)
        if (
            record["sha256"] != expected_sha256
            or self.path.resolve() != self.path
            or self.path.stat().st_mode & 0o222
            or any(Path(str(self.path) + s).exists() for s in ("-wal", "-shm", "-journal"))
        ):
            raise ValueError("historical_occurrence_index_not_sealed")
        self.signature = _stamp(self.path.stat())
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True)) as c:
            value = c.execute("SELECT contract,complete FROM index_state WHERE id=1").fetchone()
            if (
                not value
                or value[1] != 1
                or json.loads(value[0])["mapping_sha256"] != mapping.receipt["sha256"]
            ):
                raise ValueError("historical_occurrence_index_incomplete_or_wrong_copy")
            for (raw,) in c.execute("SELECT proof_json FROM plans"):
                _check(deadline_at)
                proof = json.loads(raw)
                for label in ("plan", "result"):
                    _dependency(proof["reference"], label, proof[label + "_identity"],
                                self.archives, deadline_at, "historical_occurrence_index_dependency_changed")
        self.sha256 = expected_sha256

    def generation(self, generation_id, *, deadline_at):
        _check(deadline_at)
        if self.path.resolve() != self.path or _stamp(self.path.stat()) != self.signature:
            raise ValueError("historical_occurrence_index_changed")
        with closing(
            sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as conn:
            row = conn.execute(
                "SELECT proof_json FROM plans WHERE generation_id=?", (generation_id,)
            ).fetchone()
            if row is None:
                raise ValueError("historical_generation_not_indexed")
            proof = json.loads(row[0])
            for label in ("plan", "result"):
                _dependency(proof["reference"], label, proof[label + "_identity"],
                            self.archives, deadline_at, "historical_occurrence_index_dependency_changed")
            count, failures = conn.execute(
                "SELECT count(*),coalesce(sum(journal_verified!=1),0) FROM occurrences WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
            if (
                not proof["producer_complete"]
                or failures
                or count < proof["changes"] + proof["listings"]
            ):
                raise ValueError("historical_generation_commit_incomplete")
            self.mapping.validate()
            return proof

    def matches(self, attempt, artifact, artifact_sha256, destinations, *, deadline_at):
        _check(deadline_at)
        if self.path.resolve() != self.path or _stamp(self.path.stat()) != self.signature:
            raise ValueError("historical_occurrence_index_changed")
        wanted = set(map(str, destinations))
        if len(wanted) < 2:
            return False
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro&immutable=1", uri=True)) as c:
            rows = c.execute(
                "SELECT o.paths_json,p.proof_json FROM occurrences o JOIN plans p USING(generation_id) "
                "WHERE o.kind=? AND o.identity_sha256=? AND o.journal_verified=1",
                (attempt["kind"], identity(attempt["kind"], artifact, artifact_sha256)),
            )
            for paths, raw in rows:
                _check(deadline_at)
                if not wanted.issubset(set(json.loads(paths))):
                    continue
                proof = json.loads(raw)
                for label in ("plan", "result"):
                    _dependency(proof["reference"], label, proof[label + "_identity"],
                                self.archives, deadline_at, "historical_occurrence_index_dependency_changed")
                inverse = {v["copy"]: k for k, v in self.mapping.rows.items()}
                for path in wanted:
                    if path not in inverse or str(self.mapping.resolve(inverse[path])) != path:
                        raise ValueError("historical_occurrence_target_not_in_mapping")
                return True
        return False
