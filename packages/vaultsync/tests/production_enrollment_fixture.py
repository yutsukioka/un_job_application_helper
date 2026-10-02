"""Synthetic ciphertext-only input for production-runtime enrollment tests.

The source has an authenticated nonempty genesis, then one accepted revocation.
No private keys, decrypted payloads, or session responses leave this helper.
"""

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
import test_atlasvault_activation_c26 as backend_fixture
import test_epoch_activation as client_fixture


def build(root: Path):
    seed = root / "seed"
    seed.mkdir()
    clients, env = client_fixture.initialize(seed)
    contracts = Path(__file__).resolve().parents[3] / "contracts/sync/test_vectors"
    payload = json.loads((contracts / "atlasvault_payload_vectors_v1.json").read_text())[
        "payloads"
    ]["profile_snippet"]
    operations = []
    for number, (author, name, deleted) in enumerate(
        [
            (0, "retained-active-author", False),
            (2, "retained-revoked-author", False),
            (0, "terminal-delete", True),
        ],
        1,
    ):
        operation_id = f"10000000-0000-4000-8000-{number:012d}"
        metadata = {
            "operation_id": operation_id,
            "author_device_id": env[2][author].device_id,
            "author_sequence": number,
            "lamport": number,
            "object_id": name,
            "revision": operation_id,
            "parent_revision": None,
            "tombstone": deleted,
        }
        clear = json.dumps(
            dict(
                format="atlasvault-runtime-record",
                version=1,
                **metadata,
                payload=None if deleted else payload,
            ),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        sealed = (
            clients[author]
            .seal("patch", clear, object_id=name, revision=operation_id, signing_key=env[2][author])
            .to_dict()
        )
        sealed["tombstone"] = deleted
        operations.append(
            dict(
                format="atlasvault-encrypted-patch-operation",
                version=1,
                operation_type="delete" if deleted else "upsert",
                **{
                    k: metadata[k]
                    for k in ("operation_id", "author_device_id", "author_sequence", "lamport")
                },
                envelope=sealed,
            )
        )
    body = json.dumps(
        {
            "format": "atlasvault-guarded-collection",
            "version": 1,
            "route": "patch",
            "records": [op["envelope"] for op in operations],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    committed = root / "committed"
    committed.mkdir()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(backend_fixture, "initial_body", lambda: body)
        patch.setattr(client_fixture, "initial_body", lambda: body)
        _, current = client_fixture.initialize(committed)
        collection = backend_fixture.initial_collection(current[2][0]).to_dict()
    record = client_fixture.backend_accept(current)
    return {
        "_warning": "Synthetic test-only ciphertext and public metadata; never production material.",
        "synthetic_test_only": True,
        "format": "atlasvault-production-enrollment-fixture",
        "version": 1,
        "initial_view": current[5],
        "initial_collection": collection,
        "initial_registry": backend_fixture.history_registry(current[2]),
        "signed_descriptors": [d.sign_descriptor().to_dict() for d in current[2]],
        "record": record,
        "device_ids": [d.device_id for d in current[2]],
        "opaque_state_b64": base64.b64encode(body).decode(),
        "operations": operations,
    }


def attach_delivery(value, root):
    import test_runtime_enrollment

    from vaultsync.device_identity import device_identity_from_private_keys
    from vaultsync.enrollment_bootstrap import create_history_bootstrap
    from vaultsync.enrollment_delivery import create_enrollment_delivery
    from vaultsync.epoch_vault import EpochVault
    from vaultsync.historical_authority import build_authority
    from vaultsync.sync_recovery import GuardedSyncState

    issuer = device_identity_from_private_keys(
        signing_private_seed=bytes([10]) * 32,
        agreement_private_key=bytes([20]) * 32,
        created_at="2026-01-01T00:00:00Z",
        key_epoch=3,
    )
    view, record = value["initial_view"], value["record"]
    body = base64.b64decode(value["opaque_state_b64"])
    history = GuardedSyncState(
        root / "history",
        encryption_key=bytes([60]) * 32,
        account_id=view["account_id"],
        vault_id="vault-c26",
        collection_id="collection-c26",
        key_epoch=3,
        trusted_signer=issuer.signing_public_key,
    )
    history.initialize()
    history.ingest(view, value["initial_registry"], value["initial_collection"], body)
    owner = EpochVault(
        root / "sender",
        storage_key=bytes([50]) * 32,
        device_id=issuer.device_id,
        registry=record["proof"]["registry"],
        account_id=view["account_id"],
        vault_id="vault-c26",
        key_epoch=3,
        state_root=view["root"],
    )
    owner.initialize({3: bytes([30]) * 32}, history=history)
    owner.accept_rotation(
        record["proof"], accepted_record=record, agreement_private_key=bytes([20]) * 32
    )
    target = device_identity_from_private_keys(
        signing_private_seed=bytes([90]) * 32,
        agreement_private_key=bytes([100]) * 32,
        created_at="2026-01-01T00:00:00Z",
        key_epoch=3,
    )
    vector = copy.deepcopy(test_runtime_enrollment.VECTOR)
    vector["target"] = {
        "device_id": target.device_id,
        "state": "ACTIVE",
        "signing_public_b64": base64.b64encode(target.signing_public_key).decode(),
        "agreement_public_b64": base64.b64encode(target.agreement_public_key).decode(),
    }
    vector["proof"].update(
        target_device_id=target.device_id,
        target_signing_public_b64=vector["target"]["signing_public_b64"],
        target_agreement_public_b64=vector["target"]["agreement_public_b64"],
        target_agreement_sha256=hashlib.sha256(target.agreement_public_key).hexdigest(),
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(test_runtime_enrollment, "VECTOR", vector)
        admission = test_runtime_enrollment.enrollment(owner, issuer)
    owner.accept_enrollment(admission, confirmed_transcript=admission["transcript_sha256"])
    page = owner.create_commitment(body, signing_key=issuer)
    checkpoint = create_history_bootstrap(
        owner,
        enrollment=admission,
        signing_key=issuer,
        view=page["view"],
        collection=page["collection"],
        opaque_state=body,
    )
    proof = build_authority(
        checkpoint,
        signed_descriptors=value["signed_descriptors"],
        prior_registry=record["proof"]["registry"],
        revocation=record["proof"]["revocation"],
        views=owner._history(owner._load()).export_evidence(),
    )
    anchor = {"checkpoint": checkpoint, "enrollment": admission, "view": page["view"]}
    packet = create_enrollment_delivery(
        owner,
        signing_key=issuer,
        anchor=anchor,
        collection=page["collection"],
        opaque_state=body,
        historical_authority=proof,
    )
    value["delivery_case"] = {
        "packet": packet,
        "pins": {
            "anchor_root": checkpoint["root"],
            "recipient_device_id": checkpoint["recipient_device_id"],
            "confirmed_transcript": admission["transcript_sha256"],
            "current_context": owner.enrollment_context(),
        },
        "trusted_signer_b64": base64.b64encode(issuer.signing_public_key).decode(),
    }
    return value


if __name__ == "__main__":
    import sys
    import tempfile

    augment = sys.argv[1] in ("--attach", "--ack")
    destination = sys.argv[2] if augment else sys.argv[1]
    with tempfile.TemporaryDirectory(prefix="c30-production-input-") as temp:
        if augment:
            value = json.loads(Path(destination).read_text())
            if sys.argv[1] == "--ack":
                from test_enrollment_delivery import identity

                from vaultsync.enrollment_delivery import acknowledge_enrollment
                from vaultsync.epoch_rotation import _canonical

                case = value["delivery_case"]
                assert "acknowledgement" not in case
                case["acknowledgement"] = acknowledge_enrollment(
                    case["packet"], "a1" * 32, identity(80)
                )
                case["acknowledgement_unsigned_sha256"] = hashlib.sha256(
                    _canonical(
                        {k: v for k, v in case["acknowledgement"].items() if k != "signature_b64"}
                    )
                ).hexdigest()
            else:
                assert "delivery_case" not in value
                value = attach_delivery(value, Path(temp))
        else:
            value = build(Path(temp))
    with open(destination, "w" if augment else "x") as output:
        json.dump(value, output, sort_keys=True, indent=2)
        output.write("\n")
    print("synthetic public/ciphertext fixture generated; three records; two author states")
