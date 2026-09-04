from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PARITY = ROOT / "contracts/sync/atlasvault_private_record_parity_v1.json"
INTENDED_FAMILIES = {
    "saved_search",
    "saved_job",
    "application_note",
    "profile_snippet",
    "draft_metadata",
}


def test_private_record_parity_contract_is_complete() -> None:
    contract = json.loads(PARITY.read_text(encoding="utf-8"))

    assert contract["format"] == "atlasvault-private-record-parity"
    assert contract["version"] == 1
    assert {entry["record_type"] for entry in contract["record_families"]} == (
        INTENDED_FAMILIES
    )
    assert contract["excluded_record_families"] == [
        {
            "record_type": "saved_text",
            "reason": "legacy_reference_fixture_only",
        }
    ]
    for entry in contract["record_families"]:
        for host in entry["hosts"].values():
            assert set(host) == {
                "create",
                "read_list",
                "update",
                "delete_tombstone",
                "encrypted_persistence",
                "sync_encoding_decoding",
                "conflict_handling",
                "recovery_display",
            }
            assert all(host.values())


def test_macos_and_flutter_apple_production_compositions_are_explicit() -> None:
    package = (ROOT / "apps/apple/Package.swift").read_text(encoding="utf-8")
    flutter = (
        ROOT / "apps/atlas_flutter/lib/features/app_shell/atlas_app.dart"
    ).read_text(encoding="utf-8")
    apple_adapter = ROOT / "apps/atlas_flutter/lib/src/atlas_vault/apple_storage.dart"

    assert '.executable(name: "AtlasMacHost"' in package
    assert "AtlasMacAppProcessOwner" in package
    assert apple_adapter.is_file()
    assert "Platform.isIOS || Platform.isMacOS" in flutter
    assert "AtlasAppleVaultSecureKeyStore" in flutter
    assert "AtlasAppleVaultLocalStoreIO" in flutter
    assert "AtlasAppleSelectedVaultStore" in flutter
    assert "_AtlasFailClosedPlaintextAuthorityAdmission" in flutter


def test_no_supported_production_composition_uses_plaintext_fallback() -> None:
    flutter = (
        ROOT / "apps/atlas_flutter/lib/features/app_shell/atlas_app.dart"
    ).read_text(encoding="utf-8")
    apple_branch = flutter.split("if (Platform.isIOS || Platform.isMacOS)", 1)[1]
    apple_branch = apple_branch.split("if (!Platform.isAndroid)", 1)[0]

    assert "AtlasVaultPrivateStateRuntime(" in apple_branch
    assert "_AtlasFailClosedPlaintextAuthorityAdmission" in apple_branch
    assert "localCacheStoreFactory: _noPersistentPlaintextCache" in apple_branch
    assert "AtlasLocalCacheStore(" not in apple_branch
    assert "resolveAtlasLegacyTemporaryCacheFile" not in apple_branch

