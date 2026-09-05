"""Consume real Swift/Dart runtime exports with the unchanged Python P3-P7 core.

Exports are synthetic ciphertext in temporary files, never repository artifacts.
The optional producer paths are supplied by the focused C29 cross-language run.
"""

import base64
import json
import os
from pathlib import Path

import jsonschema
import pytest
from vaultsync.epoch_rotation import EpochVault
from vaultsync.records import PlaintextRecord
from vaultsync.sync_queue import EncryptedPatchOperation
from vaultsync.sync_recovery import GuardedSyncState

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("language", ["SWIFT", "DART"])
def test_native_runtime_ciphertext_and_history_agree_with_python(tmp_path, language):
    path = os.environ.get(f"ATLAS_C29_{language}_PAGE")
    if not path:
        pytest.skip("C29 runtime producer export not supplied")
    page = json.loads(Path(path).read_text())
    vector = json.loads(
        (ROOT / "contracts/sync/test_vectors/atlasvault_activation_v1.json").read_text()
    )
    proof = vector["record"]["proof"]
    initial = vector["initial_view"]
    public = base64.b64decode(proof["registry"][0]["signing_public_b64"])
    history = GuardedSyncState(
        tmp_path / "history",
        encryption_key=bytes([60]) * 32,
        account_id=initial["account_id"],
        vault_id="vault-c26",
        collection_id="collection-c26",
        key_epoch=3,
        trusted_signer=public,
    )
    history.initialize()
    history.ingest(
        initial,
        vector["initial_registry"],
        vector["initial_collection"],
        base64.b64decode(vector["opaque_state_b64"]),
    )
    owner = EpochVault(
        tmp_path / "runtime",
        storage_key=bytes([50]) * 32,
        device_id=vector["device_ids"][0],
        registry=proof["registry"],
        account_id=initial["account_id"],
        vault_id="vault-c26",
        key_epoch=3,
        state_root=initial["root"],
    )
    owner.initialize({3: bytes([30]) * 32}, history=history)
    owner.accept_rotation(
        proof, accepted_record=vector["record"], agreement_private_key=bytes([20]) * 32
    )
    schema = json.loads(
        (ROOT / "contracts/sync/atlasvault_runtime_record_v1.schema.json").read_text()
    )
    types = set()
    for raw in page["operations"]:
        operation = EncryptedPatchOperation.from_dict(raw)
        body = json.loads(owner.open(operation.envelope))
        try:
            jsonschema.validate(body, schema, format_checker=jsonschema.FormatChecker())
        except jsonschema.ValidationError:
            pytest.fail("Runtime body schema disagreement", pytrace=False)
        outer = {**raw, **raw["envelope"]}
        fields = (
            "operation_id",
            "author_device_id",
            "author_sequence",
            "lamport",
            "object_id",
            "revision",
            "parent_revision",
            "tombstone",
        )
        assert all(body[name] == outer[name] for name in fields)
        types.add(PlaintextRecord.from_dict(body["payload"]).type)
    assert types == {
        "saved_search",
        "saved_job",
        "application_note",
        "profile_snippet",
        "draft_metadata",
    }
    accepted = owner._history(owner._load())
    assert accepted.ingest(
        page["view"],
        page["registry"],
        page["collection"],
        base64.b64decode(page["opaque_state_b64"]),
    )
    assert not accepted.ingest(
        page["view"],
        page["registry"],
        page["collection"],
        base64.b64decode(page["opaque_state_b64"]),
    )
    assert owner.observation()["state_root"] == page["view"]["root"]
    print(f"C29_RUNTIME_INTEROP source={language} families=5 history_verified=true")
