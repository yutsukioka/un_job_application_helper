from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VECTOR = (
    ROOT
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_two_device_convergence_vectors_v1.json"
)
ORCHESTRATOR = ROOT / "scripts" / "ci" / "atlasvault_two_device_convergence.py"
CLIENTS = (
    ROOT
    / "packages"
    / "vaultsync"
    / "tests"
    / "support"
    / "two_device_convergence_client.py",
    ROOT
    / "apps"
    / "atlas_flutter"
    / "test"
    / "support"
    / "atlas_vault_two_device_convergence_client.dart",
    ROOT
    / "scripts"
    / "ci"
    / "support"
    / "AtlasVaultTwoDeviceConvergenceClient.swift",
)


def test_c20_vector_covers_concurrent_create_edit_delete() -> None:
    root = json.loads(VECTOR.read_text(encoding="utf-8"))
    assert root["format"] == "atlasvault-two-device-convergence-vectors"
    assert set(root["device_a_operations"]) == {"create_a", "edit_a", "delete_a"}
    assert set(root["device_b_operations"]) == {
        "create_b",
        "edit_b",
        "late_edit_b",
    }
    operations = root["operations"]
    assert operations["create_a"]["envelope"]["parent_revision"] is None
    assert operations["create_b"]["envelope"]["parent_revision"] is None
    assert operations["edit_a"]["envelope"]["object_id"] == "shared_record"
    assert operations["edit_b"]["envelope"]["object_id"] == "shared_record"
    assert operations["delete_a"]["envelope"]["tombstone"] is True
    assert operations["late_edit_b"]["lamport"] > operations["delete_a"]["lamport"]
    assert root["expected_final_revision"] == "c20-delete-a"


def test_c20_proof_requires_real_tls_backend_and_three_language_pairs() -> None:
    missing = [str(path.relative_to(ROOT)) for path in (ORCHESTRATOR, *CLIENTS) if not path.is_file()]
    assert missing == [], f"missing C20 proof clients: {missing}"

    source = ORCHESTRATOR.read_text(encoding="utf-8")
    for required in (
        "uvicorn.run",
        "127.0.0.1",
        "TLS",
        "python-dart",
        "dart-swift",
        "swift-python",
        "DurableEncryptedOutbox",
        "DurableEncryptedInbox",
        "page_size=2",
    ):
        assert required in source
    assert "synchronize_to(" not in source
    assert "synchronizeTo(" not in source
