"""Publication admission is exercised with real local ledgers and SQLite."""
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_live_publication import add_detail, add_document, add_frame, body
from test_live_publication import setup as _publication_setup

from jobagg import semantic_history
from jobagg.pipelines.live_publication import publish_incremental
from jobagg.publication_storage import charge, write_scope
from jobagg.storage_budget import (
    CATEGORY_LIMITS,
    CONTROL_ENVELOPE_BYTES,
    BudgetLedger,
    assess,
    inventory,
    parse_budget_config,
)


@pytest.fixture
def live_fixture(tmp_path):
    return _publication_setup.__wrapped__(tmp_path)


def budget_config(root, state):
    state.mkdir(exist_ok=True)
    diagnostics = root / "diagnostics"
    diagnostics.mkdir(exist_ok=True)
    roots = [
            {"name": "essential", "path": str(root), "category": "essential", "writers": ["runner", "publisher"]},
            {"name": "publication", "path": str(state), "category": "publication", "writers": ["runner", "publisher"]},
            {"name": "diagnostics", "path": str(diagnostics), "category": "diagnostics", "writers": ["runner", "publisher"]},
    ]
    rules = []
    for name in ("output", "live"):
        output = root / name
        if output.is_dir():
            roots.append({"name": name, "path": str(output), "category": "publication", "writers": ["runner", "publisher"]})
            rules.extend({"root": name, "pattern": pattern, "category": "essential"}
                         for pattern in ("*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm", "*.sqlite3-journal"))
    return parse_budget_config({"enabled": True,
        "roots": roots, "category_rules": rules,
        "writer_roles": {"runner": "controller", "publisher": "publisher"},
        "ledger_path": str(diagnostics / "budget.json"),
    })


def admitted(root, state, *, cycle_id="publication-fixture", essential_limit=40_000_000, config=None):
    config = config or budget_config(root, state)
    observed = inventory(config)
    limits = {key: 0 for key in CATEGORY_LIMITS}
    limits.update(essential=essential_limit, publication=20_000_000, diagnostics=1_000_000)
    envelope = {**limits, "diagnostics": limits["diagnostics"] + CONTROL_ENVELOPE_BYTES + 20_000_000}
    admission = assess(config, observed, projected_growth=envelope,
                       physical_free_bytes={device: 200_000_000_000 for device in observed.device_charged_bytes})
    assert admission.ready, admission.reasons
    controller = BudgetLedger.create(admission, cycle_id=cycle_id)
    writer = controller.for_writer("publisher")
    controller.transition("worker_admitted", "accepted_pending")
    controller.transition("accepted_pending", "publication_admitted")
    policy = {"schema_version": 1, "policy_epoch": "fixture-epoch", "export_storage": "record_chunks_v1",
              "max_history_bytes": 20_000_000, "transaction_growth_limit_bytes": 2_000_000,
              "write_limits": limits}
    return controller, writer, policy


def run(case, **kwargs):
    return publish_incremental(case["worker"].path, case["registry"], case["output"], case["state"], **kwargs)


def test_escrow_does_not_consume_controller_control_allowance(tmp_path):
    controller, writer, policy = admitted(tmp_path, tmp_path / "state")
    before = controller.remaining_envelope()["diagnostics"]
    with write_scope(writer.wire(), policy["write_limits"]):
        charge(tmp_path / "diagnostics" / "publisher-report", 100)
    after = controller.remaining_envelope()["diagnostics"]
    assert before - after == policy["write_limits"]["diagnostics"]
    assert after >= 20_000_000
    assert controller.snapshot()["operations"] == {}


def test_scope_checks_paths_and_cumulative_cap_before_write(tmp_path):
    _, writer, policy = admitted(tmp_path, tmp_path / "state")
    policy["write_limits"]["publication"] = 20
    with write_scope(writer.wire(), policy["write_limits"]):
        charge(tmp_path / "state" / "one", 12)
        with pytest.raises(ValueError, match="envelope_exhausted"):
            charge(tmp_path / "state" / "two", 9)
        with pytest.raises(ValueError, match="unregistered"):
            charge(tmp_path.parent / "outside", 1)
    assert not (tmp_path / "state" / "one").exists()


def test_activation_requires_lease_and_capacity_before_generation(live_fixture):
    case = live_fixture
    add_detail(case)
    _, writer, policy = admitted(case["root"], case["state"])
    with pytest.raises(ValueError, match="required_together"):
        run(case, publication_policy=policy, execute=True)
    policy["write_limits"]["publication"] = 1
    with pytest.raises(ValueError, match="envelope_exhausted"):
        run(case, publication_policy=policy, storage_budget_lease=writer.wire(), execute=True)
    assert not (case["output"] / ".jobagg-publication-state.json").exists()
    with sqlite3.connect(case["output"] / "all_jobs.sqlite3") as conn:
        assert conn.execute("SELECT description FROM jobs").fetchone()[0] == body("Old")
        assert conn.execute("SELECT policy_epoch FROM semantic_history_state").fetchone()[0] is None


def test_activated_publication_retains_before_final_history_and_coverage(live_fixture):
    case = live_fixture
    detail, _ = add_detail(case)
    controller, writer, policy = admitted(case["root"], case["state"])
    result = run(case, publication_policy=policy, storage_budget_lease=writer.wire(), execute=True)
    assert result["status"] == "published"
    generation = Path(result["plan_path"]).parent
    reference = json.loads((generation / "semantic-coverage.json").read_text())
    verified = semantic_history.validate_semantic_coverage(
        reference, subject_kind="generation", subject_id=result["generation_id"],
        policy_epoch=policy["policy_epoch"], allowed_roots=[case["output"]],
    )
    assert len(verified["destinations"]) == 2
    assert len(list((generation / "exports-v2").iterdir())) == 8
    assert all(path.name.endswith(".publication.json") for path in (generation / "exports-v2").iterdir())
    for path in (case["output"] / "test_jobs.sqlite3", case["output"] / "all_jobs.sqlite3"):
        with sqlite3.connect(path) as conn:
            versions = [semantic_history.read_semantic_version(conn, row[0]) for row in conn.execute("SELECT version_id FROM semantic_versions WHERE entity_kind='job'")]
            serialized = json.dumps(versions)
            assert body("Old") in serialized and detail.description in serialized
            assert conn.execute("SELECT description FROM jobs").fetchone()[0] == detail.description
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("UPDATE jobs SET description='unauthorized'")
    assert controller.snapshot()["operations"] == {}


def test_failed_transaction_keeps_old_live_data_and_gate_pending(live_fixture):
    case = live_fixture
    add_detail(case)
    _, writer, policy = admitted(case["root"], case["state"])
    def fail(stage, key):
        if stage == "before_database_commit":
            raise RuntimeError("fixture crash")
    with pytest.raises(RuntimeError, match="fixture crash"):
        run(case, publication_policy=policy, storage_budget_lease=writer.wire(), execute=True, fault=fail)
    gate = json.loads((case["output"] / ".jobagg-publication-state.json").read_text())
    assert gate["state"] != "complete"
    for name in ("test_jobs.sqlite3", "all_jobs.sqlite3"):
        with sqlite3.connect(case["output"] / name) as conn:
            assert conn.execute("SELECT description FROM jobs").fetchone()[0] == body("Old")


@pytest.mark.parametrize("sufficient", [False, True])
def test_existing_committed_wal_is_admitted_before_writable_connection(live_fixture, monkeypatch, sufficient):
    from test_live_publication import sha

    from jobagg.db import JobDatabase
    from jobagg.pipelines import live_publication
    from jobagg.storage_budget import StorageBudgetDeferred

    case = live_fixture
    add_detail(case)
    target = case["output"] / "test_jobs.sqlite3"
    wal = Path(str(target) + "-wal")
    with closing(sqlite3.connect(target)) as keeper:
        keeper.execute("PRAGMA journal_mode=WAL")
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("CREATE TABLE review_alloc(payload BLOB)")
        keeper.execute("INSERT INTO review_alloc VALUES(zeroblob(16000000))")
        keeper.commit()
        initial = {path: (path.stat().st_size, sha(path)) for path in (target, wal)}
        logical_bytes = keeper.execute("PRAGMA page_count").fetchone()[0] * keeper.execute("PRAGMA page_size").fetchone()[0]
        assert logical_bytes > initial[target][0] + 15_000_000
        controller, writer, policy = admitted(case["root"], case["state"],
                                              essential_limit=100_000_000 if sufficient else 40_000_000)
        charged, opened, transaction_checks = [], [], []
        original_charge, original_open = live_publication.charge, JobDatabase._open_connection

        def record_charge(path, count):
            if Path(path) == target:
                charged.append(count)
            return original_charge(path, count)

        def record_open(database):
            connection = original_open(database)
            if database.path == target and not database.read_only:
                assert charged, "reservation must precede any writable connection"
                opened.append(connection)
            return connection

        def check_transaction(stage, key):
            if stage == "before_database_commit" and Path(key) == target:
                connection = opened[-1]
                assert connection.in_transaction
                assert connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 0
                assert connection.execute("PRAGMA cache_spill").fetchone()[0] == 0
                transaction_checks.append(stage)

        monkeypatch.setattr(live_publication, "charge", record_charge)
        monkeypatch.setattr(JobDatabase, "_open_connection", record_open)
        arguments = {"publication_policy": policy, "storage_budget_lease": writer.wire(),
                     "execute": True, "fault": check_transaction}
        if not sufficient:
            with pytest.raises(StorageBudgetDeferred, match="envelope_exhausted"):
                run(case, **arguments)
            assert not opened
            assert {path: (path.stat().st_size, sha(path)) for path in (target, wal)} == initial
        else:
            result = run(case, **arguments)
            assert result["status"] == "published" and transaction_checks
            assert target.stat().st_size == initial[target][0], "publisher must not auto-checkpoint the retained WAL"
            growth = sum(path.stat().st_size - initial[path][0] for path in (target, wal))
            assert 0 <= growth <= charged[0]
            assert controller.snapshot()["operations"] == {}
        assert charged[0] >= 2 * logical_bytes + initial[wal][0]


def test_interrupted_escrow_resumes_without_new_or_unbounded_grant(tmp_path):
    controller, writer, policy = admitted(tmp_path, tmp_path / "state")
    with pytest.raises(RuntimeError), write_scope(writer.wire(), policy["write_limits"]):
        charge(tmp_path / "state" / "first", 100)
        raise RuntimeError("crash")
    first = controller.snapshot()["operations"]
    assert len(first) == 1
    with write_scope(writer.wire(), policy["write_limits"]):
        charge(tmp_path / "state" / "second", 100)
    assert controller.snapshot()["operations"] == {}
    assert controller.snapshot()["settled"]["publication"] == 2 * 1024**2


def test_database_family_category_mismatch_denies_before_writable_open(live_fixture, monkeypatch):
    from jobagg.db import JobDatabase

    case = live_fixture
    add_detail(case)
    value = budget_config(case["root"], case["state"]).to_dict()
    value["category_rules"] = [rule for rule in value["category_rules"] if rule["pattern"] == "*.sqlite3"]
    _, writer, policy = admitted(case["root"], case["state"], config=parse_budget_config(value))
    target = case["output"] / "test_jobs.sqlite3"
    assert writer.category_for(target) == "essential"
    assert writer.category_for(str(target) + "-wal") == "publication"
    original = JobDatabase._open_connection
    writable = []

    def observe(database):
        if not database.read_only:
            writable.append(str(database.path))
        return original(database)

    monkeypatch.setattr(JobDatabase, "_open_connection", observe)
    with pytest.raises(ValueError, match="database_family_categories_differ"):
        run(case, publication_policy=policy, storage_budget_lease=writer.wire(), execute=True)
    assert writable == []


def test_document_semantics_and_blob_coverage_read_back(live_fixture):
    case = live_fixture
    detail, proof = add_detail(case)
    document, data = add_document(case, detail, proof)
    _, writer, policy = admitted(case["root"], case["state"])
    result = run(case, publication_policy=policy, storage_budget_lease=writer.wire(), execute=True)
    root = Path(result["plan_path"]).parent
    ref = json.loads((root / "semantic-coverage.json").read_text())
    coverage = semantic_history.validate_semantic_coverage(
        ref, subject_kind="generation", subject_id=result["generation_id"],
        policy_epoch=policy["policy_epoch"], allowed_roots=[case["output"]],
    )
    assert any(a["kind"] == "blob" and a["sha256"] == document["content_sha256"] for a in coverage["artifacts"])
    for destination in coverage["destinations"]:
        with sqlite3.connect(destination["database"]) as conn:
            versions = [semantic_history.read_semantic_version(conn, version) for version in destination["version_ids"]]
            docs = [item for item in versions if item["entity_kind"] == "document_association"]
            assert any(item["payload"]["extracted_text"] == data.decode() for item in docs)


def test_recovery_after_committed_transaction_preserves_policy_and_history(live_fixture):
    case = live_fixture
    add_detail(case)
    _, writer, policy = admitted(case["root"], case["state"])
    once = []
    def fail(stage, key):
        if stage == "after_database_commit" and not once:
            once.append(key)
            raise RuntimeError("after commit")
    with pytest.raises(RuntimeError, match="after commit"):
        run(case, publication_policy=policy, storage_budget_lease=writer.wire(), execute=True, fault=fail)
    changed = {**policy, "policy_epoch": "different"}
    with pytest.raises(ValueError, match="policy_changed"):
        run(case, publication_policy=changed, storage_budget_lease=writer.wire(), execute=True)
    result = run(case, publication_policy=policy, storage_budget_lease=writer.wire(), execute=True)
    assert result["status"] == "published"
    assert (Path(result["plan_path"]).parent / "semantic-coverage.json").exists()


def test_coverage_precedes_receipts_for_listing_and_detail(live_fixture, monkeypatch):
    from jobagg.pipelines import live_publication
    case = live_fixture
    add_detail(case)
    add_frame(case)
    _, writer, policy = admitted(case["root"], case["state"])
    real = live_publication._record_generation_coverage
    inspected = []
    def inspect(conn, database_path, plan, versions):
        assert conn.execute("SELECT count(*) FROM jobagg_publication_receipts").fetchone()[0] == 0
        inspected.append(str(database_path))
        return real(conn, database_path, plan, versions)
    monkeypatch.setattr(live_publication, "_record_generation_coverage", inspect)
    result = run(case, publication_policy=policy, storage_budget_lease=writer.wire(), execute=True)
    assert result["status"] == "published" and result["published_listing_frames"] == 1
    assert len(inspected) == 2


def test_twelve_cycles_retire_payload_keep_semantics_and_bound_storage(live_fixture):
    import fcntl

    from jobagg import publication_artifacts, terminal_history
    case = live_fixture
    case["output"].rename(case["root"] / "output")
    case["output"] = case["root"] / "output"
    case["state"] = case["root"] / "deterministic-live-publication"
    (case["root"] / "deterministic-dispatcher-proposal" / "runs").mkdir(parents=True)
    lock = case["root"] / "remediation" / "manual-fetch-owner.lock"
    lock.parent.mkdir()
    lock.touch()
    generations, cycles, peaks = [], [], []
    config = budget_config(case["root"], case["state"])
    object_root = case["state"] / "publication-objects"

    def objects():
        return set(object_root.iterdir()) if object_root.exists() else set()

    def measure(stage, key=None):
        observed = inventory(config)
        assert observed.complete, observed.reasons
        counts = dict(observed.charged_bytes)
        assert all(counts[category] <= cap for category, cap in CATEGORY_LIMITS.items())
        assert sum(counts.values()) <= 100_000_000_000
        sample = {"stage": stage, "charged_bytes": counts, "total_bytes": sum(counts.values())}
        if stage == "before_export_target_seal":
            temporary = list(case["output"].glob(".*.*/copy"))
            assert temporary, "sample must observe the full temporary export"
            assert all(config.root_for(path).category == "publication" for path in temporary)
            peaks.append(sample)
        return sample

    def peak(stage, key):
        if stage == "before_export_target_seal":
            measure(stage, key)

    with lock.open("r+") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        for n in range(12):
            tick = datetime(2025, 9, 14, 12, tzinfo=UTC) + timedelta(minutes=15 * n)
            changed = n % 4 != 3
            if changed:
                record, proof = add_detail(case, description=body(f"Generation{n:02d}"), observed=tick.isoformat())
                if n == 0:
                    document, data = add_document(case, record, proof)
            previous_objects = objects()
            controller, writer, policy = admitted(case["root"], case["state"], cycle_id=f"cycle-{n}")
            envelope = controller.snapshot()["envelope"]
            initial = measure("admission")["charged_bytes"]
            bound = {category: initial[category] + envelope[category] for category in CATEGORY_LIMITS}
            assert all(bound[category] <= cap for category, cap in CATEGORY_LIMITS.items())
            assert sum(bound.values()) <= 100_000_000_000
            peak_start = len(peaks)
            result = run(case, publication_policy=policy, storage_budget_lease=writer.wire(), execute=True, fault=peak)
            if changed:
                assert result["status"] == "published"
                generations.append(Path(result["plan_path"]).parent)
                assert len(peaks) > peak_start
                if n == 0:
                    saved_ref = json.loads((generations[0] / "semantic-coverage.json").read_text())
            else:
                assert result["status"] == "no_changes"
                assert objects() == previous_objects
                assert len(peaks) == peak_start
            maintenance = []
            if len(generations) >= 3 and changed:
                maintenance_policy = {"enabled": True, "policy_epoch": policy["policy_epoch"],
                                      "activated_at": "2020-01-01T00:00:00+00:00", "allowed_roots": [str(case["output"])]}
                for expected in ("retired", "collected"):
                    outcome = terminal_history.maintain_history(
                        case["root"], policy=maintenance_policy, execute=True, owner_fd=owner.fileno(),
                        shared_lock=lock, budget=controller, max_groups=1,
                    )
                    assert outcome["selected"][0]["status"] == expected
                    if expected == "retired":
                        assert outcome["selected"][0]["subject_id"] == generations[-3].name
                    else:
                        assert (generations[-3] / "terminal-object-gc.json").exists()
                    maintenance.append(outcome["selected"][0])
            if len(generations) >= 2:
                closure = terminal_history.operational_closure(case["root"])
                assert closure["current"] == generations[-1].name
                assert closure["previous"] == generations[-2].name
                assert set(closure["generations"]) == {path.name for path in generations[-2:]}
                assert all((generation / "plan.json").exists() for generation in generations[-2:])
                assert all(not (generation / "plan.json").exists() for generation in generations[:-2])
                shared = set()
                for generation in generations[-2:]:
                    journal = json.loads((generation / "export-checkpoints.json").read_text())
                    for entry in journal["entries"].values():
                        for path in (entry["prepared"], entry.get("backup_path")):
                            if path:
                                shared.update(publication_artifacts.referenced_objects(path))
                assert objects() == shared, "only current/previous reachable chunks remain"
            steady = measure("after_maintenance")
            for sample in [*peaks[peak_start:], steady]:
                assert all(sample["charged_bytes"][category] <= bound[category] for category in CATEGORY_LIMITS)
            cycles.append({"tick": tick.isoformat(), "status": result["status"],
                           "objects": len(objects()), "admitted_bound": bound,
                           "steady": steady, "retirement": maintenance})
            controller.transition("publication_admitted", "completed")
    oldest = generations[0]
    assert len(generations) == 9 and len(cycles) == 12
    assert all(not (case["output"] / directory).exists() for directory in ("daily", "monthly", "history"))
    assert len(list((case["state"] / "generations").glob("*/plan.json"))) == 2
    assert sum(cycle["status"] == "no_changes" for cycle in cycles) == 3
    (case["root"] / "cycle-acceptance.json").write_text(json.dumps({
        "scope": "bounded synthetic fixture, not production capacity validation", "cycles": cycles,
        "temporary_coexistence_samples": peaks,
        "max_observed_total_bytes": max(sample["total_bytes"] for sample in peaks),
    }, indent=2))
    verified = semantic_history.validate_semantic_coverage(
        saved_ref, subject_kind="generation", subject_id=oldest.name,
        policy_epoch=policy["policy_epoch"], allowed_roots=[case["output"]],
    )
    for destination in verified["destinations"]:
        with sqlite3.connect(destination["database"]) as conn:
            versions = [semantic_history.read_semantic_version(conn, version) for version in destination["version_ids"]]
            assert body("Generation00") in json.dumps(versions)
            assert any(item["blob_sha256"] == document["content_sha256"] for item in versions)
            assert conn.execute("SELECT content FROM attachment_blobs WHERE content_sha256=?", (document["content_sha256"],)).fetchone()[0] == data


def test_cli_report_stays_inside_escrow_and_echoes_bound_policy(live_fixture, monkeypatch, capsys):
    from test_live_publication import sha, write

    from jobagg import publish_worker
    from jobagg.publication_snapshot import _recovery_identity
    case = live_fixture
    add_detail(case)
    controller, writer, policy = admitted(case["root"], case["state"])
    acceptance = case["root"] / "worker-acceptance.json"
    write(acceptance, {"status": "accepted"})
    lock = case["root"] / "owner.lock"
    lock.touch()
    request = case["root"] / "request.json"
    value = {
        "schema_version": 1, "run_id": writer.cycle_id,
        "expected_source_ids": ["test_custom_html"], "registry_sha256": sha(case["registry"]),
        "worker_acceptance_path": str(acceptance), "worker_acceptance_sha256": sha(acceptance),
        "worker_database": str(case["worker"].path),
        "worker_database_files": [{"path": str(case["worker"].path), "sha256": sha(case["worker"].path)}],
        "shared_lock_path": str(lock), "source_manifest_sha256": "fixture-manifest",
        "worker_status": "incomplete", "publication_policy": policy,
    }
    write(request, value)
    report = case["root"] / "diagnostics" / "publisher-report.json"
    monkeypatch.setenv("JOBAGG_STORAGE_BUDGET_LEASE", json.dumps(writer.wire()))
    args = ["--worker-database", str(case["worker"].path), "--registry", str(case["registry"]),
            "--output-dir", str(case["output"]), "--state-dir", str(case["state"]),
            "--shared-lock", str(lock), "--request", str(request), "--report", str(report), "--execute"]
    assert publish_worker.main(args) == 0
    result = json.loads(report.read_text())
    assert result["status"] == "published" and result["publication_policy"] == policy
    assert controller.snapshot()["operations"] == {}
    assert controller.snapshot()["settled"]["diagnostics"] > 0
    assert _recovery_identity(value) != _recovery_identity({**value, "publication_policy": {**policy, "policy_epoch": "other"}})
    assert result["publication"]["request_identity_sha256"] == _recovery_identity(value)
    capsys.readouterr()
