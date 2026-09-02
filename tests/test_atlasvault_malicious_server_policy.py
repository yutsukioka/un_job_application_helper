"""Keep the P6 release gate tied to the actual process/HTTP proof."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_c24_hosted_gate_runs_and_removes_hash_only_report():
    workflow = (
        ROOT / ".github/workflows/atlasvault-cross-platform-security.yml"
    ).read_text()
    assert "Run C24 multi-device malicious-server proof" in workflow
    assert "python scripts/ci/atlasvault_malicious_server.py" in workflow
    assert 'rm -f -- "$report"' in workflow


def test_c24_hostile_topology_and_all_attack_families_are_retained():
    source = (ROOT / "scripts/ci/atlasvault_malicious_server.py").read_text()
    for required in (
        "transport.PAIRINGS",
        "SIGKILL",
        'mode != "inspect"',
        "backend_proof",
        "ThreadPoolExecutor",
        'request("POST", path',
        'request("GET", path',
        "old_commitment",
        "old_snapshot",
        "pre_delete_replay",
        "exact_retry",
        "omitted_known_update",
        "withheld_peer",
        "unseen_withholding",
        "fork_exchange",
        "registry_exchange",
        "wrong_predecessor",
        "registry_rollback",
        "removed_device",
        "added_device",
        "cross_account",
        "cross_vault",
        "wrong_epoch",
        "stale_create",
        "stale_edit",
        "pre_delete_compaction",
        "MANUAL_REQUIRED",
        "restart_preserved",
        "Local-filesystem rollback",
        "P7 revocation",
        "First-contact freshness",
    ):
        assert required in source, required


def test_c24_each_language_owns_real_persistent_guards_and_queues():
    for path in (
        "packages/vaultsync/tests/support/malicious_server_client.py",
        "apps/atlas_flutter/test/support/atlas_vault_malicious_server_client.dart",
        "scripts/ci/support/AtlasVaultMaliciousServerClient.swift",
    ):
        source = (ROOT / path).read_text()
        for required in (
            "GuardedSyncState",
            "DurableEncryptedInbox",
            "DurableEncryptedOutbox",
            "accepted-recovery.state",
            "inbox.state",
            "outbox.state",
            "ATLAS_RECOVERY_PENDING",
        ):
            assert required in source, (path, required)
