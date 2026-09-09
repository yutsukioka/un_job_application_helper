"""Public signed fixture shared with Dart and Swift; no private material."""

import base64
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from vaultsync.anchored_history import AnchoredSyncState
from vaultsync.authenticated_state_view import StateViewError

ROOT = Path(__file__).resolve().parents[3] / "contracts" / "sync"
VECTOR = json.loads((ROOT / "test_vectors" / "atlasvault_history_bootstrap_v1.json").read_text())


def open_fixture(path):
    v, p = VECTOR, VECTOR["checkpoint"]
    return AnchoredSyncState(
        path,
        encryption_key=bytes([96]) * 32,
        account_id=p["account_id"],
        vault_id=p["vault_id"],
        collection_id=p["collection_id"],
        key_epoch=p["key_epoch"],
        trusted_signer=base64.b64decode(v["trusted_signer_b64"]),
        rotation_registry=v["registry"],
        anchor_root=p["root"],
        recipient_device_id=v["recipient_device_id"],
        confirmed_transcript=v["confirmed_transcript"],
        current_context=v["current_context"],
    )


def arguments():
    v = VECTOR
    return {
        "checkpoint": v["checkpoint"],
        "enrollment": v["enrollment"],
        "registry": v["registry"],
        "current_context": v["current_context"],
        "recipient_device_id": v["recipient_device_id"],
        "confirmed_transcript": v["confirmed_transcript"],
        "view": v["view"],
        "collection": v["collection"],
        "opaque_state": base64.b64decode(v["opaque_b64"]),
    }


def ingest(client, name):
    p = VECTOR["packets"][name]
    return client.ingest(
        p["view"], p["registry"], p["collection"], base64.b64decode(p["opaque_b64"])
    )


def test_shared_signed_anchor_schema_is_closed_and_bounded():
    schema = json.loads((ROOT / "atlasvault_history_bootstrap_v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(VECTOR["checkpoint"])
    for field in schema["required"]:
        missing = copy.deepcopy(VECTOR["checkpoint"])
        del missing[field]
        assert not validator.is_valid(missing)
    for field, value in (("sequence", 0), ("key_epoch", 2**53), ("unexpected", "blocked")):
        bad = dict(VECTOR["checkpoint"], **{field: value})
        assert not validator.is_valid(bad)


def test_shared_signed_vector_forward_and_exact_retry(tmp_path):
    path = tmp_path / "A"
    assert open_fixture(path).bootstrap(**arguments())
    assert not open_fixture(path).bootstrap(**arguments())
    assert open_fixture(path).checkpoint()["sequence"] == 2
    assert ingest(open_fixture(path), "next")
    assert not ingest(open_fixture(path), "next")
    assert open_fixture(path).checkpoint()["sequence"] == 3


@pytest.mark.parametrize("attack", ["sub_anchor", "non_chaining"])
def test_shared_signed_attack_fences_durably(tmp_path, attack):
    path = tmp_path / "A"
    open_fixture(path).bootstrap(**arguments())
    before = open_fixture(path).checkpoint()
    with pytest.raises(StateViewError):
        ingest(open_fixture(path), attack)
    assert open_fixture(path).checkpoint() == before
    assert open_fixture(path).recovery()["status"] == "RECOVERY_PENDING"
    with pytest.raises(StateViewError, match="ATLAS_RECOVERY_PENDING"):
        open_fixture(path).automatic_sync(lambda: pytest.fail("unfenced"))
