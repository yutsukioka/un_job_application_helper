"""Exact semantic authority uses only bounded local fixture files."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from jobagg import semantic_history as history
from jobagg.db import JobDatabase
from jobagg.storage_budget import CATEGORY_LIMITS, CONTROL_ENVELOPE_BYTES, TOTAL_LIMIT, BudgetLedger, assess, inventory, parse_budget_config


def authority(tmp_path, *, maximum=2_000_000, full=False, prepare=None):
    roots = []
    for category in CATEGORY_LIMITS:
        path = tmp_path / category
        path.mkdir(parents=True)
        roots.append({"name": category, "path": str(path), "category": category, "writers": ["runner", "worker", "publisher"]})
    config = parse_budget_config({"enabled": True, "roots": roots,
        "writer_roles": {"runner": "controller", "worker": "worker", "publisher": "publisher"},
        "ledger_path": str(tmp_path / "diagnostics" / "budget.json")})
    observed = inventory(config)
    admitted = assess(config, observed, projected_growth={k: CONTROL_ENVELOPE_BYTES + 100_000 for k in CATEGORY_LIMITS}, physical_free_bytes={d: TOTAL_LIMIT * 2 for d in observed.device_charged_bytes})
    ledger = BudgetLedger.create(admitted, cycle_id="cycle")
    path = tmp_path / "essential" / "jobs.sqlite3"
    if full:
        JobDatabase(path).initialize()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if not full:
        conn.execute("CREATE TABLE jobs(job_key TEXT PRIMARY KEY, source_id TEXT, external_id TEXT, description TEXT, first_seen_at TEXT,last_seen_at TEXT,status TEXT,raw_json TEXT)")
        conn.execute("CREATE TABLE attachment_blobs(content_sha256 TEXT PRIMARY KEY,media_type TEXT,size_bytes INTEGER,content BLOB)")
        conn.execute("CREATE TABLE job_attachments(attachment_id TEXT PRIMARY KEY,job_key TEXT,content_sha256 TEXT,extracted_text TEXT,metadata_json TEXT,retrieved_at TEXT)")
    if prepare is not None:
        prepare(conn)
    history.activate_semantic_history(conn, policy_epoch="epoch", max_history_bytes=maximum, ledger_path=config.ledger_path, scope_hash=config.scope_hash)
    conn.commit()
    return path, conn, ledger


def admitted(conn, ledger):
    return history.admitted_semantic_write(conn, policy_epoch="epoch", run_id="cycle", lease=ledger.wire())


def row(text="First  exact\ntext", **overrides):
    return {"job_key": "s:1", "source_id": "s", "external_id": "1", "description": text,
            "first_seen_at": "2026-10-01", "last_seen_at": "2026-10-15", "status": "open", "raw_json": "{}", **overrides}


def test_exact_text_versions_repeat_coalescing_and_reversion(tmp_path):
    path, conn, ledger = authority(tmp_path)
    with conn, admitted(conn, ledger):
        first = history.preserve_job(conn, row())
        initial_objects = conn.execute("SELECT count(*) FROM semantic_objects").fetchone()[0]
        for i in range(20):
            assert history.preserve_job(conn, row(last_seen_at=f"2026-10-15T00:00:{i:02d}")) == first
        assert conn.execute("SELECT count(*) FROM semantic_objects").fetchone()[0] == initial_objects
        second = history.preserve_job(conn, row("First exact text"))
        assert first != second
        assert history.preserve_job(conn, row()) == first
    assert conn.execute("SELECT count(*) FROM semantic_versions").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM semantic_spans").fetchone()[0] == 3
    assert history.read_semantic_version(conn, first)["payload"]["description"] == "First  exact\ntext"
    assert history.read_semantic_version(conn, second)["payload"]["first_seen_at"] == "2026-10-01"
    assert history.semantic_database_binding(path)["writer_fenced"]
    conn.close()


def test_direct_writes_capture_status_delete_and_reject_unknown_writers(tmp_path):
    path, conn, ledger = authority(tmp_path)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", tuple(row().values()))
    conn.rollback()
    with conn, admitted(conn, ledger):
        conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", tuple(row().values()))
        conn.execute("UPDATE jobs SET status='closed' WHERE job_key='s:1'")
        conn.execute("DELETE FROM jobs WHERE job_key='s:1'")
    statuses = {history.read_semantic_version(conn, r[0])["payload"]["status"] for r in conn.execute("SELECT version_id FROM semantic_versions")}
    assert statuses == {"open", "closed"}
    assert history.semantic_database_binding(path)["revision"] > 0
    with sqlite3.connect(path) as other, pytest.raises(sqlite3.DatabaseError):
        other.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", tuple(row().values()))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE semantic_history_state SET revision=0")
    conn.rollback()
    with admitted(conn, ledger):
        ledger.transition("worker_admitted", "accepted_pending")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", tuple(row().values()))
    conn.rollback()
    conn.close()


def test_document_text_corrections_preserve_one_binary(tmp_path):
    path, conn, ledger = authority(tmp_path)
    binary = b"%PDF bounded fixture"
    digest = hashlib.sha256(binary).hexdigest()
    with conn, admitted(conn, ledger):
        conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", tuple(row().values()))
        conn.execute("INSERT INTO attachment_blobs VALUES(?,?,?,?)", (digest,"application/pdf",len(binary),binary))
        conn.execute("INSERT INTO job_attachments VALUES(?,?,?,?,?,?)", ("a", "s:1", digest,"Initial extraction","{}","t1"))
        conn.execute("UPDATE job_attachments SET extracted_text='Corrected exact extraction',retrieved_at='t2'")
        conn.execute("DELETE FROM job_attachments")
        with pytest.raises(sqlite3.IntegrityError,match="retained_blob"):
            conn.execute("DELETE FROM attachment_blobs")
    versions = [history.read_semantic_version(conn, r[0]) for r in conn.execute("SELECT version_id FROM semantic_versions WHERE entity_kind='document_association'")]
    assert {v["payload"]["extracted_text"] for v in versions} == {"Initial extraction", "Corrected exact extraction"}
    assert conn.execute("SELECT count(*) FROM attachment_blobs").fetchone()[0] == 1
    conn.close()


def artifact(path, value, versions, kind="job_beforeimage", **extra):
    path.write_text(json.dumps(value))
    data = path.read_bytes()
    return {"path":str(path),"sha256":hashlib.sha256(data).hexdigest(),"size_bytes":len(data),"kind":kind,"version_ids":versions, **extra}


def test_coverage_exact_payload_commit_and_identity(tmp_path):
    path, conn, ledger = authority(tmp_path)
    with admitted(conn, ledger):
        versions = history.preserve_beforeimage(conn, {"job":row(),"related":{}})
        a = artifact(tmp_path / "before.json", {"job":row(),"related":{}}, versions)
        ref = history.record_semantic_coverage(conn, subject_kind="generation",subject_id="g",policy_epoch="epoch",version_ids=versions,artifacts=[a])
        with pytest.raises(history.SemanticHistoryError, match="missing_or_corrupt"):
            history.validate_semantic_coverage(ref,subject_kind="generation",subject_id="g",policy_epoch="epoch",allowed_roots=[tmp_path])
        conn.commit()
    valid = history.validate_semantic_coverage(ref,subject_kind="generation",subject_id="g",policy_epoch="epoch",allowed_roots=[tmp_path])
    assert valid["artifacts"][0]["sha256"] == a["sha256"]
    with pytest.raises(history.SemanticHistoryError, match="identity_mismatch"):
        history.validate_semantic_coverage(ref,subject_kind="generation",subject_id="other",policy_epoch="epoch",allowed_roots=[tmp_path])
    with conn, admitted(conn, ledger), pytest.raises(history.SemanticHistoryError,match="payload_uncovered"):
        wrong = artifact(tmp_path / "wrong.json", {"job":row("unretained description"),"related":{}},versions)
        history.record_semantic_coverage(conn,subject_kind="generation",subject_id="g",policy_epoch="epoch",version_ids=versions,artifacts=[wrong])
    conn.close()


def test_listing_empty_image_and_exact_presence_payload(tmp_path):
    path, conn, ledger = authority(tmp_path)
    frame = {"source_id":"s","external_ids":["1","2"],"observed_at":"t1","publication_key":"p","destinations":[]}
    before = {"live_listing_frames":[],"live_listing_inventory":[],"job_presence_rows":[{"job_key":"s:1","raw_json":"{}","source_listed_current":1}]}
    with conn, admitted(conn, ledger):
        versions = history.preserve_beforeimage(conn,before,source_id="s")
        versions.append(history.preserve_listing_frame(conn,frame))
        a = artifact(tmp_path / "listing.json", before, versions,source_id="s")
        history.record_semantic_coverage(conn,subject_kind="generation",subject_id="g",policy_epoch="epoch",version_ids=versions,artifacts=[a])
        empty = artifact(tmp_path / "empty.json", {"job":None,"related":{}},versions)
        history.record_semantic_coverage(conn,subject_kind="generation",subject_id="g",policy_epoch="epoch",version_ids=versions,artifacts=[empty])
    conn.close()


def test_capacity_rolls_back_and_unfenced_schema_fails(tmp_path):
    path, conn, ledger = authority(tmp_path,maximum=1024)
    with pytest.raises(history.SemanticCapacityError), conn, admitted(conn, ledger):
        history.preserve_job(conn,row())
    assert conn.execute("SELECT count(*) FROM semantic_versions").fetchone()[0] == 0
    conn.close()
    conn = sqlite3.connect(path)  # Simulate an independent legacy schema writer.
    conn.execute("CREATE TABLE unexpected(value)")
    conn.commit()
    with pytest.raises(history.SemanticHistoryError, match="unfenced"):
        history.semantic_database_binding(path)
    conn.close()


def test_jobdatabase_transaction_integration_and_full_column_capture(tmp_path):
    from jobagg.models import OrganizationSource
    from jobagg.normalize import build_job
    path, setup, ledger = authority(tmp_path, full=True)
    setup.close()
    db = JobDatabase(path)
    source = OrganizationSource(id="fixture",name="Fixture",ats_family="workday",base_url="https://example.org")
    job = build_job(source,title="Role",external_id="1",description="Exact  retained text",apply_url="https://example.org/job/1")
    with db.connection_scope() as conn, admitted(conn, ledger):
        db.upsert_job(job)
        final = dict(conn.execute("SELECT * FROM jobs").fetchone())
        direct_versions = [history.read_semantic_version(conn,r[0])["payload"] for r in conn.execute("SELECT version_id FROM semantic_versions")]
        assert all(set(v) == set(history._job_payload(final)) for v in direct_versions)
    original = db.get_job("fixture:1")
    with pytest.raises(RuntimeError):
        with db.connection_scope() as conn, admitted(conn, ledger):
            conn.execute("UPDATE jobs SET description='Should roll back'")
            raise RuntimeError("fixture rollback")
    assert db.get_job("fixture:1")["description"] == original["description"]
    with db.connect() as conn:
        assert not any(history.read_semantic_version(conn,r[0])["payload"].get("description") == "Should roll back" for r in conn.execute("SELECT version_id FROM semantic_versions"))


def test_consolidation_keeps_prior_only_authority_and_document_versions(tmp_path):
    from jobagg.models import OrganizationSource
    from jobagg.normalize import build_job
    from jobagg.pipelines.consolidation import consolidate_bundle_databases
    root = tmp_path / "bundle"
    root.mkdir()
    source_db = JobDatabase(root / "a_jobs.sqlite3")
    source_db.initialize()
    source = OrganizationSource(id="fixture",name="Fixture",ats_family="workday",base_url="https://example.org")
    source_db.upsert_job(build_job(source,title="New",external_id="new",description="Source text",apply_url="https://example.org/new"))
    result = consolidate_bundle_databases(output_dir=root)
    previous = JobDatabase(result.db_path)
    old = build_job(source,title="Old closed",external_id="old",description="Historical exact  text",apply_url="https://example.org/old",status="closed")
    previous.upsert_job(old)
    binary = b"old stored PDF"
    digest = hashlib.sha256(binary).hexdigest()
    with previous.connect() as conn:
        old_row = dict(conn.execute("SELECT * FROM jobs WHERE job_key='fixture:old'").fetchone())
        history.preserve_job(conn,old_row)
        conn.execute("UPDATE jobs SET first_seen_at='2025-01-01T00:00:00+00:00' WHERE job_key='fixture:new'")
        conn.execute("INSERT INTO attachment_blobs VALUES(?,?,?,?)",(digest,"application/pdf",len(binary),binary))
        doc = ("old-doc","fixture:old","fixture","https://example.org/old.pdf",None,"Role document","description",1,"stored",digest,"Original extraction","{}")
        conn.execute("INSERT INTO job_attachments VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",doc)
        history.preserve_document_association(conn,dict(conn.execute("SELECT * FROM job_attachments").fetchone()),job_row=old_row)
        conn.execute("UPDATE job_attachments SET extracted_text='Corrected extraction'")
        conn.execute("CREATE TABLE personal_fixture_state(id TEXT PRIMARY KEY,value TEXT)")
        conn.execute("INSERT INTO personal_fixture_state VALUES('application','keep exact personal record')")
    for _ in range(2):
        result = consolidate_bundle_databases(output_dir=root)
        with sqlite3.connect(result.db_path) as conn:
            assert conn.execute("SELECT description,first_seen_at FROM jobs WHERE job_key='fixture:old'").fetchone() == (old_row["description"],old_row["first_seen_at"])
            assert conn.execute("SELECT first_seen_at FROM jobs WHERE job_key='fixture:new'").fetchone()[0] == "2025-01-01T00:00:00+00:00"
            assert conn.execute("SELECT value FROM personal_fixture_state").fetchone()[0] == 'keep exact personal record'
            assert conn.execute("SELECT count(*) FROM attachment_blobs WHERE content_sha256=?",(digest,)).fetchone()[0] == 1
            documents = [history.read_semantic_version(conn,r[0])["payload"]["extracted_text"] for r in conn.execute("SELECT version_id FROM semantic_versions WHERE entity_kind='document_association'")]
            assert set(documents) == {"Original extraction","Corrected extraction"}


def test_activated_consolidation_requires_complete_physical_envelope(tmp_path):
    from jobagg.pipelines.consolidation import consolidate_bundle_databases
    from jobagg.storage_budget import StorageBudgetDeferred
    path, conn, ledger = authority(tmp_path,full=True)
    conn.close()
    output = path.parent
    path.rename(output / "all_jobs.sqlite3")
    JobDatabase(output / "a_jobs.sqlite3").initialize()
    exports = [output / ("all_jobs_" + name + suffix) for name in ("current", "history") for suffix in (".json", ".csv")]
    for export in exports:
        export.write_bytes(b"previous coherent export")
    authority_path = output / "all_jobs.sqlite3"
    original = authority_path.read_bytes()
    before = history.semantic_database_binding(authority_path)
    with pytest.raises(ValueError,match="admitted_lease"):
        consolidate_bundle_databases(output_dir=output)
    assert not (output / ".all_jobs.sqlite3.tmp").exists()
    ledger.transition("worker_admitted","accepted_pending")
    ledger.transition("accepted_pending","publication_admitted")
    # A valid identity/phase grant alone must not admit any replacement bytes.
    with pytest.raises(StorageBudgetDeferred,match="physical_envelope_unsupported"):
        consolidate_bundle_databases(output_dir=output,semantic_lease=ledger.wire())
    assert not (output / ".all_jobs.sqlite3.tmp").exists()
    assert authority_path.read_bytes() == original
    assert history.semantic_database_binding(authority_path) == before
    assert all(export.read_bytes() == b"previous coherent export" for export in exports)
    # Exhaust the registered work allowance: the same boundary still writes
    # no temporary database, changes no authority, and consumes no extra grant.
    available = ledger.remaining_envelope()["work"]
    reservation = ledger.reserve("work",available,path=tmp_path / "work" / "already-reserved",operation_id="fixture-full-work")
    reservation.consume(available)
    consumed_before = ledger.snapshot()
    with pytest.raises(StorageBudgetDeferred,match="physical_envelope_unsupported"):
        consolidate_bundle_databases(output_dir=output,semantic_lease=ledger.wire())
    assert ledger.snapshot() == consumed_before
    assert authority_path.read_bytes() == original
    assert not (output / ".all_jobs.sqlite3.tmp").exists()



def test_admission_revocation_fences_pending_commit(tmp_path):
    path, conn, ledger = authority(tmp_path)
    with admitted(conn,ledger):
        conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",tuple(row().values()))
        ledger.transition("worker_admitted","accepted_pending")
    with pytest.raises(sqlite3.DatabaseError,match="authorized"):
        conn.commit()
    conn.rollback()
    with sqlite3.connect(path) as check:
        assert check.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert check.execute("SELECT count(*) FROM semantic_versions").fetchone()[0] == 0
    conn.close()


def test_plan_cannot_certify_unretained_document(tmp_path):
    path,conn,ledger=authority(tmp_path)
    with conn,admitted(conn,ledger):
        version=history.preserve_job(conn,row())
        plan={"generation_id":"g","changes":[{"worker_row":row(),"destinations":[{"path":str(path)}],
            "documents":[{"url":"https://example.org/unique.pdf","content_sha256":"0"*64,"extracted_text":"Unretained unique document"}]}]}
        a=artifact(tmp_path / "plan.json",plan,[version],kind="publication_plan")
        with pytest.raises(history.SemanticHistoryError,match="document_uncovered"):
            history.record_semantic_coverage(conn,subject_kind="generation",subject_id="g",policy_epoch="epoch",version_ids=[version],artifacts=[a])
    conn.close()


def test_corrupt_semantic_copy_aborts_before_replacing_authority(tmp_path):
    from contextlib import closing
    from jobagg.pipelines.consolidation import consolidate_bundle_databases
    source = tmp_path / "a_jobs.sqlite3"
    JobDatabase(source).initialize()
    result = consolidate_bundle_databases(output_dir=tmp_path)
    with closing(sqlite3.connect(result.db_path)) as conn, conn:
        version = history.preserve_job(conn,row())
        conn.execute("UPDATE semantic_objects SET payload=? WHERE object_id=(SELECT object_id FROM semantic_versions WHERE version_id=?)", (b"corrupt", version))
    original = result.db_path.read_bytes()
    with pytest.raises(history.SemanticHistoryError, match="corrupt"):
        consolidate_bundle_databases(output_dir=tmp_path)
    assert result.db_path.read_bytes() == original


def test_authorizer_composition_and_immutable_history(tmp_path):
    path,conn,ledger=authority(tmp_path)
    observed=[]
    def callback(action, first, second, database, trigger):
        observed.append((action,first))
        return sqlite3.SQLITE_OK
    with conn,history.admitted_semantic_write(conn,policy_epoch="epoch",run_id="cycle",lease=ledger.wire(),authorizer=callback):
        key=history.preserve_job(conn,row())
        with pytest.raises(sqlite3.DatabaseError,match="authorized"):
            conn.execute("DROP TRIGGER semantic_state_monotonic")
        with pytest.raises(sqlite3.DatabaseError,match="authorized"):
            conn.execute("PRAGMA writable_schema=ON")
        with pytest.raises(sqlite3.IntegrityError,match="immutable_binding"):
            conn.execute("UPDATE semantic_history_state SET revision=0")
        with pytest.raises(sqlite3.IntegrityError,match="immutable"):
            conn.execute("DELETE FROM semantic_versions WHERE version_id=?",(key,))
    assert (sqlite3.SQLITE_TRANSACTION,"COMMIT") in observed
    conn.close()


def test_known_occurrence_proofs_coalesce_but_unknown_fields_survive(tmp_path):
    path,conn,ledger=authority(tmp_path)
    with conn,admitted(conn,ledger):
        proof={"observed_at":"t1","verified":True,"unknown_timestamp":"fact"}
        first=history.preserve_job(conn,row(raw_json=json.dumps({"_jobagg_listing_verification":proof})))
        repeated={**proof,"observed_at":"t2","previous_verification":proof}
        assert history.preserve_job(conn,row(raw_json=json.dumps({"_jobagg_listing_verification":repeated}))) == first
        changed={**repeated,"unknown_timestamp":"different fact"}
        assert history.preserve_job(conn,row(raw_json=json.dumps({"_jobagg_listing_verification":changed}))) != first
    conn.close()


@pytest.mark.parametrize("savepoint", [False, True])
def test_cached_sql_transaction_boundary_rechecks_lease(tmp_path, savepoint):
    path,conn,ledger=authority(tmp_path)
    begin="SAVEPOINT semantic_boundary" if savepoint else "BEGIN"
    end="RELEASE semantic_boundary" if savepoint else "COMMIT"
    with admitted(conn,ledger):
        conn.execute(begin)
        history.preserve_job(conn,row("first committed"))
        conn.execute(end)
        conn.execute(begin)
        history.preserve_job(conn,row("second must not commit"))
        ledger.transition("worker_admitted","accepted_pending")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(end)
        conn.rollback()
    with sqlite3.connect(path) as check:
        assert check.execute("SELECT count(*) FROM semantic_versions").fetchone()[0] == 1
    conn.close()


def test_insert_replace_cannot_destroy_immutable_authority(tmp_path):
    path,conn,ledger=authority(tmp_path)
    binary=b"original bytes"
    digest=hashlib.sha256(binary).hexdigest()
    with conn,admitted(conn,ledger):
        conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",tuple(row().values()))
        conn.execute("INSERT INTO attachment_blobs VALUES(?,?,?,?)",(digest,"application/pdf",len(binary),binary))
        conn.execute("INSERT INTO job_attachments VALUES(?,?,?,?,?,?)",("doc","s:1",digest,"Exact text","{}","t1"))
        assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError,match="retained_blob"):
            conn.execute("INSERT OR REPLACE INTO attachment_blobs VALUES(?,?,?,?)",(digest,"application/pdf",3,b"bad"))
        key,payload,size=conn.execute("SELECT * FROM semantic_objects LIMIT 1").fetchone()
        with pytest.raises(sqlite3.IntegrityError,match="immutable"):
            conn.execute("INSERT OR REPLACE INTO semantic_objects VALUES(?,?,?)",(key,b"bad",3))
    assert bytes(conn.execute("SELECT content FROM attachment_blobs").fetchone()[0]) == binary
    conn.close()


@pytest.mark.parametrize("conflict", ["primary", "rowid", "unique", "multiple"])
def test_replace_preserves_unversioned_legacy_jobs_before_implicit_delete(tmp_path, conflict):
    prior = row("Prior unique legacy text", external_id="Case")
    def prepare(conn):
        conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", tuple(prior.values()))
        if conflict in {"unique", "multiple"}:
            conn.execute("CREATE UNIQUE INDEX legacy_identity ON jobs(source_id,external_id COLLATE NOCASE)")
        if conflict == "multiple":
            conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)", tuple(row("Second prior text",job_key="s:2",external_id="other").values()))
    path,conn,ledger=authority(tmp_path,prepare=prepare)
    assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM semantic_versions").fetchone()[0] == 0
    incoming=row("Replacement text",job_key="s:1" if conflict in {"primary","multiple"} else "s:3",
                 external_id="OTHER" if conflict == "multiple" else "case")
    with conn,admitted(conn,ledger):
        if conflict == "rowid":
            conn.execute("INSERT OR REPLACE INTO jobs(rowid,job_key,source_id,external_id,description,first_seen_at,last_seen_at,status,raw_json) VALUES(1,?,?,?,?,?,?,?,?)",tuple(incoming.values()))
        else:
            conn.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?,?,?)",tuple(incoming.values()))
    versions=[history.read_semantic_version(conn,r[0])["payload"] for r in conn.execute("SELECT version_id FROM semantic_versions WHERE entity_kind='job'")]
    assert {v["description"] for v in versions} == {"Prior unique legacy text","Replacement text"} | ({"Second prior text"} if conflict == "multiple" else set())
    assert next(v for v in versions if v["description"] == prior["description"])["first_seen_at"] == prior["first_seen_at"]
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert history.semantic_database_binding(path)["writer_fenced"]
    conn.close()


@pytest.mark.parametrize("conflict", ["primary", "rowid", "unique"])
def test_replace_preserves_unversioned_document_association_and_binary(tmp_path, conflict):
    binary=b"Unique legacy document bytes"
    digest=hashlib.sha256(binary).hexdigest()
    prior=("old-doc","s:1",digest,"Prior exact extraction",'{"label":"old label"}',"t1")
    def prepare(conn):
        conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",tuple(row().values()))
        conn.execute("INSERT INTO attachment_blobs VALUES(?,?,?,?)",(digest,"application/pdf",len(binary),binary))
        conn.execute("INSERT INTO job_attachments VALUES(?,?,?,?,?,?)",prior)
        if conflict == "unique":
            conn.execute("CREATE UNIQUE INDEX legacy_document ON job_attachments(job_key,content_sha256)")
    path,conn,ledger=authority(tmp_path,prepare=prepare)
    incoming=("old-doc" if conflict == "primary" else "new-doc","s:1",digest,"Corrected extraction",'{"label":"new label"}',"t2")
    with conn,admitted(conn,ledger):
        if conflict == "rowid":
            conn.execute("INSERT OR REPLACE INTO job_attachments(rowid,attachment_id,job_key,content_sha256,extracted_text,metadata_json,retrieved_at) VALUES(1,?,?,?,?,?,?)",incoming)
        else:
            conn.execute("INSERT OR REPLACE INTO job_attachments VALUES(?,?,?,?,?,?)",incoming)
    versions=[history.read_semantic_version(conn,r[0])["payload"] for r in conn.execute("SELECT version_id FROM semantic_versions WHERE entity_kind='document_association'")]
    assert {(v["extracted_text"],v["metadata"]["label"]) for v in versions} == {("Prior exact extraction","old label"),("Corrected extraction","new label")}
    assert bytes(conn.execute("SELECT content FROM attachment_blobs").fetchone()[0]) == binary
    assert conn.execute("SELECT count(*) FROM attachment_blobs").fetchone()[0] == 1
    conn.close()


def test_replace_capacity_failure_keeps_unversioned_legacy_authority(tmp_path):
    prior=row("Prior legacy text survives rejected replacement")
    path,conn,ledger=authority(tmp_path,maximum=1,prepare=lambda c:c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",tuple(prior.values())))
    with pytest.raises(history.SemanticCapacityError):
        with conn,admitted(conn,ledger):
            conn.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?,?,?)",tuple(row("Never accepted").values()))
    assert dict(conn.execute("SELECT * FROM jobs").fetchone()) == prior
    assert conn.execute("SELECT count(*) FROM semantic_versions").fetchone()[0] == 0
    assert history.semantic_database_binding(path)["revision"] == 0
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?,?,?)",tuple(row("Unadmitted").values()))
    conn.rollback()
    assert dict(conn.execute("SELECT * FROM jobs").fetchone()) == prior
    conn.close()


@pytest.mark.parametrize("conflict", ["primary", "rowid", "update", "delete"])
def test_legacy_referenced_blob_cannot_be_destroyed_before_first_capture(tmp_path,conflict):
    binary=b"Legacy document bytes"
    digest=hashlib.sha256(binary).hexdigest()
    def prepare(conn):
        conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",tuple(row().values()))
        conn.execute("INSERT INTO attachment_blobs VALUES(?,?,?,?)",(digest,"application/pdf",len(binary),binary))
        conn.execute("INSERT INTO job_attachments VALUES(?,?,?,?,?,?)",("doc","s:1",digest,"Legacy extraction","{}","t1"))
    path,conn,ledger=authority(tmp_path,prepare=prepare)
    with conn,admitted(conn,ledger):
        with pytest.raises(sqlite3.IntegrityError,match="retained_blob"):
            if conflict == "primary":
                conn.execute("INSERT OR REPLACE INTO attachment_blobs VALUES(?,?,?,?)",(digest,"application/pdf",3,b"bad"))
            elif conflict == "rowid":
                conn.execute("INSERT OR REPLACE INTO attachment_blobs(rowid,content_sha256,media_type,size_bytes,content) VALUES(1,?,?,?,?)",(hashlib.sha256(b"new").hexdigest(),"application/pdf",3,b"new"))
            elif conflict == "update":
                conn.execute("UPDATE attachment_blobs SET content=?",(b"bad",))
            else:
                conn.execute("DELETE FROM attachment_blobs")
    assert bytes(conn.execute("SELECT content FROM attachment_blobs").fetchone()[0]) == binary
    assert conn.execute("SELECT count(*) FROM semantic_versions").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("table,primary", [("semantic_objects","object_id"),("semantic_subjects","subject_id"),("semantic_versions","version_id"),("semantic_coverage","coverage_id")])
def test_replace_rowid_cannot_destroy_immutable_semantic_authority(tmp_path,table,primary):
    path,conn,ledger=authority(tmp_path)
    with conn,admitted(conn,ledger):
        history.preserve_job(conn,row())
        conn.execute("INSERT INTO semantic_coverage VALUES('fixture','{}')")
        old=dict(conn.execute(f"SELECT rowid,* FROM {table} LIMIT 1").fetchone())
        incoming={**old,primary:"different-primary"}
        columns=','.join(incoming)
        placeholders=','.join('?' for _ in incoming)
        with pytest.raises(sqlite3.IntegrityError,match="immutable"):
            conn.execute(f"INSERT OR REPLACE INTO {table}({columns}) VALUES({placeholders})",tuple(incoming.values()))
        assert dict(conn.execute(f"SELECT rowid,* FROM {table} WHERE rowid=?",(old["rowid"],)).fetchone()) == old
    conn.close()


@pytest.mark.parametrize("index", ["CREATE UNIQUE INDEX unsupported ON jobs(lower(external_id))", "CREATE UNIQUE INDEX unsupported ON jobs(external_id) WHERE status='open'"])
def test_activation_rejects_unproven_replace_conflict_indexes(tmp_path,index):
    with pytest.raises(history.SemanticHistoryError,match="unsupported_unique_conflict_index"):
        authority(tmp_path,prepare=lambda c:c.execute(index))


@pytest.mark.parametrize("conflict", ["primary", "rowid", "unique", "multiple"])
def test_update_replace_preserves_all_displaced_legacy_jobs(tmp_path,conflict):
    originals=[row("Original updated row",external_id="one"),row("Displaced prior text",job_key="s:2",external_id="two")]
    if conflict == "multiple":
        originals.append(row("Second displaced text",job_key="s:3",external_id="three"))
    def prepare(conn):
        conn.executemany("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",[tuple(r.values()) for r in originals])
        if conflict in {"unique","multiple"}:
            conn.execute("CREATE UNIQUE INDEX legacy_identity ON jobs(source_id,external_id COLLATE NOCASE)")
    path,conn,ledger=authority(tmp_path,prepare=prepare)
    assignments={"primary":"job_key='s:2'", "rowid":"rowid=2", "unique":"external_id='TWO'", "multiple":"job_key='s:2',external_id='THREE'"}[conflict]
    with conn,admitted(conn,ledger):
        conn.execute("UPDATE OR REPLACE jobs SET description='Replacement text',"+assignments+" WHERE job_key='s:1'")
    versions=[history.read_semantic_version(conn,r[0])["payload"] for r in conn.execute("SELECT version_id FROM semantic_versions WHERE entity_kind='job'")]
    assert {v["description"] for v in versions} == {r["description"] for r in originals} | {"Replacement text"}
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
    assert history.semantic_database_binding(path)["writer_fenced"]
    conn.close()


@pytest.mark.parametrize("conflict", ["primary", "rowid", "unique", "multiple"])
def test_update_replace_preserves_displaced_legacy_document_associations(tmp_path,conflict):
    binary=b"Legacy shared binary"
    digest=hashlib.sha256(binary).hexdigest()
    originals=[("a","s:1",digest,"Original extraction",'{"label":"a"}',"t1"),
               ("b","s:1",digest,"Displaced extraction",'{"label":"b"}',"t1")]
    if conflict == "multiple":
        originals.append(("c","s:1",digest,"Second displaced extraction",'{"label":"c"}',"t1"))
    def prepare(conn):
        conn.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",tuple(row().values()))
        conn.execute("INSERT INTO attachment_blobs VALUES(?,?,?,?)",(digest,"application/pdf",len(binary),binary))
        conn.executemany("INSERT INTO job_attachments VALUES(?,?,?,?,?,?)",originals)
        if conflict in {"unique","multiple"}:
            conn.execute("CREATE UNIQUE INDEX legacy_document ON job_attachments(job_key,metadata_json)")
    path,conn,ledger=authority(tmp_path,prepare=prepare)
    assignments={"primary":"attachment_id='b'", "rowid":"rowid=2", "unique":"metadata_json='{\"label\":\"b\"}'", "multiple":"attachment_id='b',metadata_json='{\"label\":\"c\"}'"}[conflict]
    with conn,admitted(conn,ledger):
        conn.execute("UPDATE OR REPLACE job_attachments SET extracted_text='Corrected extraction',"+assignments+" WHERE attachment_id='a'")
    versions=[history.read_semantic_version(conn,r[0])["payload"] for r in conn.execute("SELECT version_id FROM semantic_versions WHERE entity_kind='document_association'")]
    assert {v["extracted_text"] for v in versions} == {r[3] for r in originals} | {"Corrected extraction"}
    assert conn.execute("SELECT count(*) FROM job_attachments").fetchone()[0] == 1
    assert bytes(conn.execute("SELECT content FROM attachment_blobs").fetchone()[0]) == binary
    conn.close()


def test_update_replace_capacity_failure_preserves_both_legacy_rows(tmp_path):
    originals=[row("Original target"),row("Displaced victim",job_key="s:2",external_id="2")]
    path,conn,ledger=authority(tmp_path,maximum=1,prepare=lambda c:c.executemany("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",[tuple(r.values()) for r in originals]))
    with pytest.raises(history.SemanticCapacityError):
        with conn,admitted(conn,ledger):
            conn.execute("UPDATE OR REPLACE jobs SET job_key='s:2',description='Never accepted' WHERE job_key='s:1'")
    assert [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY job_key")] == originals
    assert conn.execute("SELECT count(*) FROM semantic_versions").fetchone()[0] == 0
    assert history.semantic_database_binding(path)["revision"] == 0
    conn.close()
