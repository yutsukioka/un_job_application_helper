"""Synthetic tests only: no live JobAgg paths, payloads, or network access."""

import importlib.util
import hashlib
import json
from pathlib import Path
import sqlite3
import shutil
import sys
import stat
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_pending_publication.py"
spec = importlib.util.spec_from_file_location("pending_inspector", SCRIPT)
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)
comparison_spec = importlib.util.spec_from_file_location(
    "pending_comparison", SCRIPT.with_name("compare_pending_publication.py")
)
comparison = importlib.util.module_from_spec(comparison_spec)
with patch.dict("sys.modules", {"inspect_pending_publication": inspector}):
    comparison_spec.loader.exec_module(comparison)
verification_spec = importlib.util.spec_from_file_location(
    "pending_verification", SCRIPT.with_name("verify_pending_publication.py")
)
verification = importlib.util.module_from_spec(verification_spec)
with patch.dict("sys.modules", {"inspect_pending_publication": inspector,
                                "compare_pending_publication": comparison}):
    verification_spec.loader.exec_module(verification)


@pytest.fixture(autouse=True)
def private_reports(tmp_path, monkeypatch):
    report_root = tmp_path / "reports"
    report_root.mkdir()
    monkeypatch.setattr(inspector, "REPORT_ROOT", report_root)
    if sys.platform != "darwin":
        # Tiny fixture copies simulate CoW on CI filesystems without reflinks.
        # Production clone_file never falls back to copying payload bytes.
        monkeypatch.setattr(inspector, "clone_file", shutil.copyfile)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE jobagg_runtime_state(id INTEGER PRIMARY KEY,schema,epoch,revision,
            phase,config_sha256,source_transaction_id);
        INSERT INTO jobagg_runtime_state VALUES
            (1,'jobagg-runtime-authority-3','epoch',10,'accepted','config','tx1');
        CREATE TABLE jobagg_dirty_state(id INTEGER PRIMARY KEY,schema,epoch,
            contract_sha256,bootstrap_complete);
        INSERT INTO jobagg_dirty_state VALUES (1,'publication-work-3','epoch','contract',1);
        CREATE TABLE jobagg_dirty_current(unit TEXT PRIMARY KEY,version INTEGER,source_id,
            kind,first_changed_at REAL,disposition,reason);
        CREATE INDEX jobagg_dirty_pending
            ON jobagg_dirty_current(disposition,first_changed_at,unit);
        CREATE TABLE jobagg_dirty_versions(version INTEGER PRIMARY KEY,unit,operation);
    """)
    for i in range(11):
        conn.execute("INSERT INTO jobagg_dirty_versions VALUES (?,?,?)",
                     (i, f"unit-{i:02}", "contract_revalidation" if i < 5 else "time_transition"))
        conn.execute("INSERT INTO jobagg_dirty_current VALUES (?,?,?,?,?,?,?)",
                     (f"unit-{i:02}", i, "a" if i < 5 else "b",
                      "listing" if i == 0 else "job", i // 3,
                      "pending" if i < 9 else ("held" if i == 9 else "acknowledged"),
                      "needs_publication"))
    conn.commit()
    yield path, conn
    conn.close()


def test_complete_counts_and_bounded_sample_preserve_database_and_wal(database):
    path, writer = database
    wal = Path(str(path) + "-wal")
    before = path.read_bytes(), wal.read_bytes()
    result = inspector.inspect(path, page_size=2, sample=3)
    assert result["complete"] and result["atomic_observation"]
    assert result["observed_pending_units"] == 9
    assert sum(g["observed_units"] for g in result["groups"]) == 9
    assert {(g["source_id"], g["kind"], g["operation"], g["observed_units"])
            for g in result["groups"]} == {
        ("a", "listing", "contract_revalidation", 1),
        ("a", "job", "contract_revalidation", 4), ("b", "job", "time_transition", 4),
    }
    assert [r["version"] for r in result["sample"]] == [0, 1, 2]
    assert result["oldest_pending_at_unix"] == 0
    assert len(result["units"]) == 9
    assert [row["version"] for row in result["units"]] == list(range(9))
    assert not result["publication_verified"] and not result["acknowledgement_authorized"]
    assert (path.read_bytes(), wal.read_bytes()) == before
    assert writer.execute("SELECT disposition FROM jobagg_dirty_current WHERE unit='unit-09'").fetchone() == ("held",)


def test_limit_is_partial_not_false_zero(database):
    result = inspector.inspect(database[0], max_units=3, page_size=2)
    assert result["observed_pending_units"] == 3
    assert not result["complete"] and result["stop_reason"] == "unit_limit"
    assert result["sample"] == []


def test_exact_limit_can_complete(database):
    assert inspector.inspect(database[0], max_units=9, page_size=3)["complete"]


def test_writer_commits_and_checkpoints_between_pages(database, monkeypatch):
    path, writer = database
    writes = []

    def between_pages(seconds):
        # Metadata acknowledgement changes the transaction token without changing revision.
        writer.execute("UPDATE jobagg_runtime_state SET source_transaction_id='tx2'")
        writer.commit()
        busy, _, _ = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert busy == 0  # No inspector snapshot pins the writer's WAL.
        writes.append(True)

    monkeypatch.setattr(inspector.time, "sleep", between_pages)
    result = inspector.inspect(path, page_size=2)
    assert writes == [True]
    assert result["stop_reason"] == "database_changed" and not result["complete"]
    assert result["observed_pending_units"] == 2


def test_time_limit_during_query_is_partial(database, monkeypatch):
    ticks = iter([0, 0, 10, 10, 10])
    monkeypatch.setattr(inspector.time, "monotonic", lambda: next(ticks, 10))
    result = inspector.inspect(database[0], max_seconds=1)
    assert result["stop_reason"] == "time_limit" and not result["complete"]


@pytest.mark.parametrize("sql", [
    "UPDATE jobagg_dirty_current SET disposition='acknowledged'",
    "DELETE FROM jobagg_dirty_versions", "CREATE TABLE oops(x)",
    "ATTACH DATABASE ':memory:' AS other", "PRAGMA wal_checkpoint(TRUNCATE)",
    "SELECT * FROM sqlite_master",
])
def test_sql_authorizer_rejects_noninspection_operations(database, sql):
    conn = sqlite3.connect(database[0].as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.set_authorizer(inspector.authorize)
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)
    finally:
        conn.close()


@pytest.mark.parametrize("damage", [
    "DROP TABLE jobagg_runtime_state", "DROP INDEX jobagg_dirty_pending",
    "UPDATE jobagg_dirty_current SET first_changed_at=NULL WHERE unit='unit-00'",
    "DELETE FROM jobagg_dirty_versions WHERE version=0",
    "UPDATE jobagg_runtime_state SET schema='unknown'",
])
def test_missing_or_incompatible_metadata_fails_closed(database, damage):
    path, writer = database
    writer.execute(damage)
    writer.commit()
    result = inspector.inspect(path)
    assert not result["complete"] and "error" in result


def test_cli_config_is_read_only_and_missing_db_is_not_created(database, tmp_path, capsys):
    path, _ = database
    config = tmp_path / "dispatcher.json"
    config.write_text(json.dumps({"publication_worker_database": path.name}))
    assert inspector.main(["--config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out)["observed_pending_units"] == 9
    missing = tmp_path / "missing.sqlite3"
    assert inspector.main(["--database", str(missing)]) == 2
    assert not missing.exists()
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "input_error"


def test_zero_is_only_complete_empty_pending_set(database):
    path, writer = database
    writer.execute("UPDATE jobagg_dirty_current SET disposition='held'")
    writer.commit()
    result = inspector.inspect(path)
    assert result["complete"] and result["observed_pending_units"] == 0
    assert result["groups"] == []


@pytest.mark.parametrize("limits", [{"max_units": 0}, {"page_size": 501},
                                     {"sample": 101}, {"max_seconds": 31}])
def test_invalid_limits_rejected_before_open(limits):
    with pytest.raises(ValueError, match="Limits"):
        inspector.inspect("missing.sqlite3", **limits)


def test_locked_database_returns_prompt_partial_error(tmp_path):
    path = tmp_path / "locked.sqlite3"
    writer = sqlite3.connect(path)
    try:
        writer.execute("CREATE TABLE placeholder(x)")
        writer.commit()
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("INSERT INTO placeholder VALUES (1)")
        result = inspector.inspect(path, max_seconds=1)
        assert result["stop_reason"] == "invalid_metadata"
        assert not result["complete"] and "journal" in result["error"]
    finally:
        writer.close()


def test_corrupt_database_is_not_repaired(tmp_path):
    path = tmp_path / "corrupt.sqlite3"
    path.write_bytes(b"not a database")
    result = inspector.inspect(path)
    assert not result["complete"] and result["stop_reason"] == "database_error"
    assert path.read_bytes() == b"not a database"


def test_missing_wal_sidecars_are_not_created(database, tmp_path, capsys):
    # A checkpointed WAL-mode database with no open writer has no sidecars.
    path, writer = database
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    closed = tmp_path / "closed.sqlite3"
    closed.write_bytes(path.read_bytes())
    before = inspector.source_signature(closed)
    assert inspector.main(["--database", str(closed)]) == 0
    assert json.loads(capsys.readouterr().out)["observed_pending_units"] == 9
    assert inspector.source_signature(closed) == before
    assert not Path(str(closed) + "-wal").exists()
    assert not Path(str(closed) + "-shm").exists()


def saved_inspection(path, **limits):
    report = inspector.inspect(path, **limits)
    return inspector.save_report(report, "synthetic-inventory-")


def test_cli_saves_full_private_inventory_and_summary(database, capsys):
    assert inspector.main(["--database", str(database[0]), "--sample", "1"]) == 0
    summary = json.loads(capsys.readouterr().out)
    report_file = Path(summary["report_file"])
    saved = json.loads(report_file.read_text())
    assert len(saved["units"]) == 9 and len(saved["sample"]) == 1
    assert "units" not in summary
    assert stat.S_IMODE(report_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_file.parent.stat().st_mode) == 0o700
    assert report_file.parent.parent == inspector.REPORT_ROOT


def test_partial_inventory_is_also_saved(database, capsys):
    assert inspector.main(["--database", str(database[0]), "--max-units", "2"]) == 2
    summary = json.loads(capsys.readouterr().out)
    report = json.loads(Path(summary["report_file"]).read_text())
    assert not report["complete"] and len(report["units"]) == 2


def test_step2_compares_exact_versions_and_keeps_queue_untouched(database):
    path, writer = database
    source = saved_inspection(path)
    writer.execute("UPDATE jobagg_dirty_current SET disposition='acknowledged' WHERE version=0")
    writer.execute("UPDATE jobagg_dirty_current SET disposition='held',reason='integrity_review' WHERE version=1")
    writer.execute("UPDATE jobagg_dirty_current SET version=100 WHERE version=2")
    writer.execute("DELETE FROM jobagg_dirty_current WHERE version=3")
    writer.execute("UPDATE jobagg_dirty_current SET source_id='other' WHERE version=4")
    writer.execute("UPDATE jobagg_dirty_current SET disposition='unknown' WHERE version=5")
    writer.execute("UPDATE jobagg_runtime_state SET source_transaction_id='tx-after-step1'")
    writer.commit()
    before = path.read_bytes(), Path(str(path) + "-wal").read_bytes()
    report = comparison.compare(source, page_size=2)
    assert report["complete"] and report["compared_units"] == 9
    assert report["counts"] == {
        "already_acknowledged_in_native_queue": 1, "held_requires_review": 1,
        "version_changed_reinspect": 1, "missing_requires_review": 1,
        "identity_changed_requires_review": 1, "unknown_disposition_requires_review": 1,
        "pending_requires_publication_or_verified_readback": 3,
    }
    assert not report["publication_verified"] and not report["acknowledgement_authorized"]
    assert report["comparisons"][2]["inspected_version"] == 2
    assert report["comparisons"][2]["current_version"] == 100
    assert (path.read_bytes(), Path(str(path) + "-wal").read_bytes()) == before


def test_step2_partial_input_remains_scoped_not_global(database):
    source = saved_inspection(database[0], max_units=2)
    report = comparison.compare(source)
    assert report["complete"] and not report["input_inventory_complete"]
    assert report["input_observed_units"] == report["compared_units"] == 2


def test_step2_row_limit_retains_unvisited_suffix(database):
    source = saved_inspection(database[0])
    report = comparison.compare(source, max_units=2)
    assert not report["complete"] and report["stop_reason"] == "unit_limit"
    assert report["compared_units"] == len(report["comparisons"]) == 2


@pytest.mark.parametrize("sql", [
    "UPDATE jobagg_runtime_state SET epoch='other'",
    "UPDATE jobagg_runtime_state SET config_sha256='changed'",
    "UPDATE jobagg_dirty_state SET contract_sha256='changed'",
    "UPDATE jobagg_dirty_state SET bootstrap_complete=0",
])
def test_step2_binding_changes_require_new_inspection(database, sql):
    source = saved_inspection(database[0])
    database[1].execute(sql)
    database[1].commit()
    report = comparison.compare(source)
    assert not report["complete"] and report["stop_reason"] == "binding_error"
    assert report["compared_units"] == 0


def test_step2_concurrent_commit_stops_without_long_read_lock(database, monkeypatch):
    source = saved_inspection(database[0])
    writer = database[1]

    def commit_during_comparison(seconds):
        writer.execute("UPDATE jobagg_runtime_state SET source_transaction_id='changed'")
        writer.commit()
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0

    monkeypatch.setattr(inspector.time, "sleep", commit_during_comparison)
    result = comparison.compare(source, page_size=2)
    assert not result["complete"] and result["stop_reason"] == "database_changed"
    assert result["compared_units"] == 2


@pytest.mark.parametrize("damage", ["old_schema", "sample_only", "duplicate", "count", "identity"])
def test_step2_rejects_invalid_step1_input(database, damage, capsys):
    source = saved_inspection(database[0])
    report = json.loads(source.read_text())
    if damage == "old_schema":
        report["schema"] = "jobagg-pending-inspection-1"
    elif damage == "sample_only":
        del report["units"]
    elif damage == "duplicate":
        report["units"][1] = report["units"][0]
    elif damage == "count":
        report["observed_pending_units"] = 100
    elif damage == "identity":
        report["database_identity"]["inode"] += 1
    source.write_text(json.dumps(report))
    assert comparison.main(["--inspection", str(source)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert not result["complete"]
    assert result["stop_reason"] in {"input_error", "binding_error"}


def test_step2_cli_saves_report_and_does_not_overwrite_step1(database, capsys):
    source = saved_inspection(database[0])
    before = source.read_bytes()
    assert comparison.main(["--inspection", str(source)]) == 0
    summary = json.loads(capsys.readouterr().out)
    saved = json.loads(Path(summary["report_file"]).read_text())
    assert saved["input_inventory_complete"] and saved["compared_units"] == 9
    assert len(saved["comparisons"]) == 9
    assert "comparisons" not in summary
    assert source.read_bytes() == before


def test_report_write_error_is_visible(database, monkeypatch, capsys):
    monkeypatch.setattr(inspector, "REPORT_ROOT", database[0] / "not-a-directory")
    assert inspector.main(["--database", str(database[0])]) == 2
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "report_write_error"


def test_snapshot_capture_race_is_rejected_and_private_clone_removed(database, monkeypatch):
    native_clone = inspector.clone_file

    def clone_then_commit(source, target):
        native_clone(source, target)
        database[1].execute("UPDATE jobagg_runtime_state SET source_transaction_id='capture-race'")
        database[1].commit()

    monkeypatch.setattr(inspector, "clone_file", clone_then_commit)
    result = inspector.inspect(database[0])
    assert not result["complete"] and result["observed_pending_units"] == 0
    assert "changed during snapshot" in result["error"]
    assert not list(inspector.REPORT_ROOT.glob(".jobagg-readonly-*"))


def test_no_sqlite_connection_opens_production_path(database, monkeypatch):
    native_connect = inspector.sqlite3.connect
    opened = []

    def private_only(location, *args, **kwargs):
        assert str(database[0]) not in location
        assert ".jobagg-readonly-" in location
        opened.append(location)
        return native_connect(location, *args, **kwargs)

    monkeypatch.setattr(inspector.sqlite3, "connect", private_only)
    before = inspector.source_signature(database[0])
    result = inspector.inspect(database[0])
    assert result["complete"] and opened
    assert inspector.source_signature(database[0]) == before
    assert not list(inspector.REPORT_ROOT.glob(".jobagg-readonly-*"))


def test_clone_unsupported_does_not_fall_back_to_byte_copy(database, monkeypatch, capsys):
    def unavailable(source, target):
        raise OSError("CoW unavailable")

    monkeypatch.setattr(inspector, "clone_file", unavailable)
    assert inspector.main(["--database", str(database[0])]) == 2
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "input_error"
    assert not list(inspector.REPORT_ROOT.glob(".jobagg-readonly-*"))


def test_wal_without_shm_is_read_from_private_snapshot(database, tmp_path):
    path, _ = database
    isolated = tmp_path / "without-shm.sqlite3"
    isolated.write_bytes(path.read_bytes())
    Path(str(isolated) + "-wal").write_bytes(Path(str(path) + "-wal").read_bytes())
    before = inspector.source_signature(isolated)
    result = inspector.inspect(isolated)
    assert result["complete"] and result["observed_pending_units"] == 9
    assert inspector.source_signature(isolated) == before
    assert not Path(str(isolated) + "-shm").exists()


def digest_record(record, compact=False):
    options = {"sort_keys": True}
    if compact:
        options["separators"] = (",", ":")
    record["sha256"] = hashlib.sha256(json.dumps(record, **options).encode()).hexdigest()
    return record


@pytest.fixture
def native_publication(database, tmp_path):
    original = saved_inspection(database[0])
    compared = comparison.compare(original)
    compared_file = inspector.save_report(compared, "synthetic-comparison-")
    root = (tmp_path / "output").resolve()
    root.mkdir()
    destination = root / "all_jobs.sqlite3"
    conn = sqlite3.connect(destination)
    conn.executescript("""
        CREATE TABLE jobs(job_key TEXT PRIMARY KEY,description TEXT);
        CREATE TABLE jobagg_publication_receipts(publication_key TEXT PRIMARY KEY,generation_id TEXT);
        INSERT INTO jobagg_publication_receipts VALUES ('key','generation');
        CREATE TABLE jobagg_publication_revision(id INTEGER PRIMARY KEY,schema,epoch,store_id,
            revision,schema_sha256);
    """)
    schema = list(conn.execute("SELECT name,type,sql FROM sqlite_master ORDER BY name"))
    schema_digest = hashlib.sha256(json.dumps(schema).encode()).hexdigest()
    conn.execute("INSERT INTO jobagg_publication_revision VALUES (1,?,?,?,?,?)",
                 ("publication-revision-2", "public-epoch", "store", 7, schema_digest))
    conn.commit()
    conn.close()
    store = {"path": str(destination), **inspector.file_identity(destination),
             "schema": "publication-revision-2", "epoch": "public-epoch", "store_id": "store",
             "revision": 7, "schema_sha256": schema_digest}
    exports = []
    for suffix in ("current.json", "current.csv", "history.json", "history.csv"):
        path = root / ("all_jobs_" + suffix)
        path.write_bytes(b"verified export\n")
        path.chmod(0o400)
        exports.append({**verification.export_identity(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    inventory_report = json.loads(original.read_text())
    selected = [{**row, "job_key": "job" if row["kind"] == "job" else None}
                for row in inventory_report["units"][:2]]
    selection = digest_record({"schema": "publication-work-3", "epoch": "epoch",
                               "contract_sha256": "contract", "units": selected,
                               "target_paths": [str(destination)]}, compact=True)
    processed = [{"unit": row["unit"], "version": row["version"]} for row in selected]
    plan = {"generation_id": "generation", "publication_selection": selection,
            "processed_units": processed, "output_dir": str(root),
            "changes": [{"publication_key": "key", "destinations": [{"path": str(destination)}]}],
            "listing_frames": []}
    plan_dir = tmp_path / "generations" / "generation"
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "plan.json"
    plan_path.write_text(json.dumps(plan))
    gate = {"generation_id": "generation", "state": "complete", "status": "published",
            "readback_verified": True, "database_transactions_complete": True,
            "publication_selection": selection, "processed_units": processed, "rejected": [],
            "plan_path": str(plan_path), "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            "publication_boundary": digest_record({"schema": "publication-boundary-1",
                                                   "output_root": str(root),
                                                   "databases": [store], "exports": exports})}
    gate_path = root / ".jobagg-publication-state.json"
    gate_path.write_text(json.dumps(gate))
    return compared_file, root, gate_path, destination


def test_step3_checks_native_store_receipts_and_export_bytes_without_writes(native_publication, database):
    source, root, gate_path, destination = native_publication
    paths = [database[0], destination, gate_path, *root.glob("*.json"), *root.glob("*.csv")]
    before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
              for path in paths}
    result = verification.verify(source, root)
    assert result["complete"] and result["preview_count"] == 2
    assert result["checked_stores"] == 1 and result["checked_exports"] == 4
    assert result["checked_publication_receipts"] == 1
    assert result["destination_boundary_verified"] and result["export_hashes_verified"]
    assert not result["acknowledgement_authorized"] and result["owner_held_recheck_required"]
    assert result["counts"]["no_exact_version_readback_proof_retain_pending"] == 7
    assert {str(path): (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
            for path in paths} == before


@pytest.mark.parametrize("damage", ["gate_in_progress", "plan", "export_hash", "export_missing",
                                    "revision", "receipt", "scope", "selection", "processed"])
def test_step3_never_previews_with_missing_or_changed_proof(native_publication, damage):
    source, root, gate_path, destination = native_publication
    gate = json.loads(gate_path.read_text())
    if damage == "gate_in_progress":
        gate["state"] = "publishing"
    elif damage == "plan":
        Path(gate["plan_path"]).write_text("{}")
    elif damage == "export_hash":
        gate["publication_boundary"]["exports"][0]["sha256"] = "0" * 64
        del gate["publication_boundary"]["sha256"]
        digest_record(gate["publication_boundary"])
    elif damage == "export_missing":
        Path(gate["publication_boundary"]["exports"][0]["path"]).unlink()
    elif damage in {"revision", "receipt"}:
        conn = sqlite3.connect(destination)
        conn.execute("UPDATE jobagg_publication_revision SET revision=8" if damage == "revision"
                     else "DELETE FROM jobagg_publication_receipts")
        conn.commit()
        conn.close()
    elif damage == "scope":
        gate["publication_boundary"]["exports"].pop()
        del gate["publication_boundary"]["sha256"]
        digest_record(gate["publication_boundary"])
    elif damage == "selection":
        gate["publication_selection"]["sha256"] = "0" * 64
    elif damage == "processed":
        gate["processed_units"].append({"unit": "forged", "version": 100})
    gate_path.write_text(json.dumps(gate))
    result = verification.verify(source, root)
    assert not result["complete"] and result["preview"] == []
    assert not result["acknowledgement_authorized"]


def test_step3_changed_current_version_is_not_in_preview(native_publication, database):
    source, root, _, _ = native_publication
    database[1].execute("UPDATE jobagg_dirty_current SET version=100 WHERE version=0")
    database[1].commit()
    result = verification.verify(source, root)
    assert result["complete"] and result["preview_count"] == 1
    assert result["counts"]["needs_fresh_inspection"] == 1


def test_step3_byte_limit_keeps_zero_preview(native_publication):
    source, root, _, _ = native_publication
    result = verification.verify(source, root, max_bytes=1)
    assert not result["complete"] and result["preview_count"] == 0
    assert "byte limit" in result["error"]


def test_step3_worker_change_during_verification_clears_preview(native_publication, database, monkeypatch):
    source, root, _, _ = native_publication
    original_identity = verification.export_identity
    changed = []

    def mutate_once(path):
        if not changed:
            database[1].execute("UPDATE jobagg_runtime_state SET source_transaction_id='new'")
            database[1].commit()
            changed.append(True)
        return original_identity(path)

    monkeypatch.setattr(verification, "export_identity", mutate_once)
    result = verification.verify(source, root)
    assert not result["complete"] and result["preview_count"] == 0
    assert "changed during verification" in result["error"]


def test_step3_cli_saves_private_preview(native_publication, capsys):
    source, root, _, _ = native_publication
    assert verification.main(["--comparison", str(source), "--output-root", str(root)]) == 0
    summary = json.loads(capsys.readouterr().out)
    result = json.loads(Path(summary["report_file"]).read_text())
    assert result["preview_count"] == 2 and len(result["units"]) == 9
    assert "preview" not in summary and "units" not in summary


def test_step3_time_limit_is_explicit_and_preview_empty(native_publication):
    source, root, _, _ = native_publication
    result = verification.verify(source, root, max_seconds=0.000001)
    assert not result["complete"] and result["stop_reason"] == "time_limit"
    assert result["preview"] == []
