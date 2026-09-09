"""D102 first contact only; synthetic independent stores, no key diagnostics."""

import copy
import hashlib
import json

import pytest
from test_epoch_activation import accept, backend_accept, initialize
from test_runtime_enrollment import enrollment

from vaultsync.anchored_history import AnchoredSyncState, bootstrap_history
from vaultsync.enrollment_bootstrap import BootstrapError, create_history_bootstrap


def scenario(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (issuer, peer, _), env = initialize(tmp_path)
    record = backend_accept(env)
    for i, owner in enumerate((issuer, peer)):
        accept(owner, i, env[4], record)
    admission = enrollment(issuer, env[2][0])
    for owner in (issuer, peer):
        owner.accept_enrollment(admission, confirmed_transcript=admission["transcript_sha256"])
    body = b'{"format":"atlasvault-guarded-collection","version":1,"route":"snapshot","records":[]}'
    page = issuer.create_commitment(body, signing_key=env[2][0])
    proof = create_history_bootstrap(
        issuer,
        enrollment=admission,
        signing_key=env[2][0],
        view=page["view"],
        collection=page["collection"],
        opaque_state=body,
    )
    arguments = {
        "checkpoint": proof,
        "enrollment": admission,
        "registry": issuer.enrollment_registry(),
        "current_context": issuer.enrollment_context(),
        "recipient_device_id": admission["target_device_id"],
        "confirmed_transcript": admission["transcript_sha256"],
        "view": page["view"],
        "collection": page["collection"],
        "opaque_state": body,
    }

    def fresh():
        return AnchoredSyncState(
            tmp_path / "recipient-history",
            encryption_key=bytes([96]) * 32,
            account_id=admission["account_id"],
            vault_id=admission["vault_id"],
            collection_id="collection-c26",
            key_epoch=4,
            trusted_signer=env[2][0].signing_public_key,
            rotation_registry=issuer.enrollment_registry(),
            anchor_root=proof["root"],
            recipient_device_id=admission["target_device_id"],
            confirmed_transcript=admission["transcript_sha256"],
            current_context=arguments["current_context"].copy(),
        )

    return issuer, peer, env, arguments, fresh


def test_new_store_checkpoint_is_recipient_bound_durable_and_idempotent(tmp_path):
    issuer, _, env, args, fresh = scenario(tmp_path)
    before = json.dumps(issuer._load()["journal"]["record"], sort_keys=True)
    client = fresh()
    assert client.bootstrap(**args)
    assert client.checkpoint()["sequence"] == args["view"]["sequence"] > 1
    assert client.checkpoint()["cursor"] == args["view"]["root"]
    assert fresh().checkpoint() == client.checkpoint()
    persisted = (tmp_path / "recipient-history").read_bytes()
    assert not fresh().bootstrap(**args)
    assert (tmp_path / "recipient-history").read_bytes() == persisted
    body = args["opaque_state"]
    next_page = issuer.create_commitment(body, signing_key=env[2][0])
    assert client.ingest(next_page["view"], args["registry"], next_page["collection"], body)
    assert client.checkpoint()["sequence"] == next_page["view"]["sequence"]
    assert json.dumps(issuer._load()["journal"]["record"], sort_keys=True) == before


@pytest.mark.parametrize(
    "field",
    [
        "recipient_device_id",
        "recipient_agreement_sha256",
        "transcript_sha256",
        "account_id",
        "vault_id",
        "activation_id",
        "key_epoch",
        "registry_generation",
        "registry_root",
        "state_root",
        "sequence",
        "collection_root",
        "collection_sha256",
        "enrollment_root",
        "issuer_device_id",
        "version",
        "signature_algorithm",
    ],
)
def test_each_checkpoint_substitution_is_rejected_before_persistence(tmp_path, field):
    _, _, _, args, fresh = scenario(tmp_path)
    proof = copy.deepcopy(args["checkpoint"])
    old = proof[field]
    proof[field] = old + 1 if type(old) is int else "substituted"
    args["checkpoint"] = proof
    with pytest.raises(BootstrapError, match="^ATLAS_BOOTSTRAP_REJECTED$"):
        fresh().bootstrap(**args)
    assert not (tmp_path / "recipient-history").exists()


@pytest.mark.parametrize(
    "attack", ["recipient", "transcript", "epoch", "generation", "state", "revoked"]
)
def test_valid_certificate_does_not_override_current_local_expectations(tmp_path, attack):
    _, _, _, args, fresh = scenario(tmp_path)
    if attack == "recipient":
        args["recipient_device_id"] = "other-device"
    elif attack == "transcript":
        args["confirmed_transcript"] = "ab" * 32
    elif attack == "revoked":
        for row in args["registry"]:
            if row["device_id"] == args["recipient_device_id"]:
                row["state"] = "REVOKED"
    else:
        field = {"epoch": "key_epoch", "generation": "registry_generation", "state": "state_root"}[
            attack
        ]
        args["current_context"][field] = "ab" * 32 if attack == "state" else 99
    with pytest.raises(BootstrapError):
        fresh().bootstrap(**args)
    assert not (tmp_path / "recipient-history").exists()


def test_established_history_cannot_be_replaced_even_by_valid_checkpoint(tmp_path):
    issuer, peer, _, args, _ = scenario(tmp_path)
    for owner in (issuer, peer):
        history = owner._history(owner._load())
        before = owner._file.path.read_bytes()
        with pytest.raises(BootstrapError, match="^ATLAS_BOOTSTRAP_EXISTING_HISTORY$"):
            bootstrap_history(history, **args)
        assert owner._file.path.read_bytes() == before


def test_preexisting_fork_evidence_is_not_cleared_by_bootstrap(tmp_path):
    _, _, _, args, fresh = scenario(tmp_path)
    client = fresh()
    client.bootstrap(**args)
    s = client._load()
    s["status"] = "RECOVERY_PENDING"
    client._store.write(s)
    before = hashlib.sha256(client._store.path.read_bytes()).hexdigest()
    with pytest.raises(BootstrapError):
        client.bootstrap(**args)
    assert hashlib.sha256(client._store.path.read_bytes()).hexdigest() == before
    assert client.recovery()["status"] == "RECOVERY_PENDING"


def test_production_epoch_owner_retains_anchor_on_initialization_and_reopen(tmp_path):
    from vaultsync.epoch_rotation import RotationError
    from vaultsync.epoch_vault import EpochVault

    _, _, env, args, fresh = scenario(tmp_path)
    history = fresh()
    history.bootstrap(**args)
    directory = tmp_path / "recipient-runtime"
    opening = {
        "storage_key": bytes([97]) * 32,
        "device_id": args["recipient_device_id"],
        "registry": args["registry"],
        "account_id": args["checkpoint"]["account_id"],
        "vault_id": args["checkpoint"]["vault_id"],
        "key_epoch": args["checkpoint"]["key_epoch"],
        "state_root": args["view"]["root"],
        "history_origin": history.publication_origin(),
    }
    owner = EpochVault(directory, **opening)
    owner.initialize({4: bytes([98]) * 32}, history=history)
    before = owner.observation()
    assert before["key_epoch"] == 4
    assert owner._history(owner._load()).checkpoint() == history.checkpoint()
    assert EpochVault(directory, **opening).observation() == before
    assert owner.enrollment_context() == args["current_context"]
    # The origin is a creation-time pin, not an optional reader fallback.
    with pytest.raises(RotationError):
        EpochVault(
            directory, **{k: v for k, v in opening.items() if k != "history_origin"}
        ).observation()
    assert EpochVault(directory, **opening).observation() == before
    assert env[2][0].device_id != args["recipient_device_id"]
