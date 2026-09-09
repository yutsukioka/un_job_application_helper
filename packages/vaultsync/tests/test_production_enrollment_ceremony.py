"""Synthetic production-ceremony transport: never print protected contents."""

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_enrollment_delivery import identity

from vaultsync import enrollment_delivery as delivery
from vaultsync.epoch_rotation import _canonical
from vaultsync.pairing_artifacts import PairingArtifact, PairingArtifactKind


def fixture():
    root = Path(__file__).resolve().parents[3]
    return json.loads(
        (root / "contracts/sync/test_vectors/atlasvault_production_enrollment_v1.json").read_text()
    )["delivery_case"]


def test_production_enrollment_transport_and_acknowledgement():
    packet = fixture()["packet"]
    recipient = identity(80)
    artifact = PairingArtifact(
        PairingArtifactKind.delivery,
        {
            "enrollment_delivery": packet,
            "inviter_proof": base64.b64encode(bytes([7]) * 32).decode(),
        },
    )
    assert PairingArtifact.from_canonical_bytes(artifact.canonical_bytes()) == artifact
    ack = delivery.acknowledge_enrollment(packet, "a1" * 32, recipient)
    assert hashlib.sha256(_canonical(ack)).hexdigest() == fixture()["acknowledgement_sha256"]
    delivery.verify_enrollment_acknowledgement(packet, "a1" * 32, ack, recipient)
    receipt = PairingArtifact(
        PairingArtifactKind.acknowledgement,
        {
            "enrollment_acknowledgement": ack,
        },
    )
    assert PairingArtifact.from_canonical_bytes(receipt.canonical_bytes()) == receipt


@pytest.mark.parametrize(
    "field",
    [
        "format",
        "version",
        "delivery_sha256",
        "anchor_root",
        "transcript_sha256",
        "recipient_device_id",
        "signature_b64",
    ],
)
def test_production_acknowledgement_substitution_rejected(field):
    packet = fixture()["packet"]
    recipient = identity(80)
    ack = copy.deepcopy(delivery.acknowledge_enrollment(packet, "a1" * 32, recipient))
    ack[field] = 2 if field == "version" else "invalid"
    with pytest.raises(delivery.EnrollmentDeliveryError):
        delivery.verify_enrollment_acknowledgement(packet, "a1" * 32, ack, recipient)


def test_production_acknowledgement_wrong_peer_and_delivery_rejected():
    packet = fixture()["packet"]
    ack = delivery.acknowledge_enrollment(packet, "a1" * 32, identity(80))
    for digest, peer in [("a2" * 32, identity(80)), ("a1" * 32, identity(0))]:
        with pytest.raises(delivery.EnrollmentDeliveryError):
            delivery.verify_enrollment_acknowledgement(packet, digest, ack, peer)


def test_production_install_publishes_nonempty_replica_and_terminal_tombstone(tmp_path):
    v = fixture()
    for _ in range(2):
        owner = delivery.install_enrollment_delivery(
            tmp_path / "recipient",
            v["packet"],
            pins=v["pins"],
            trusted_signer=base64.b64decode(v["trusted_signer_b64"]),
            recipient_identity=identity(80),
            storage_key=bytes([111]) * 32,
            require_runtime_projection=True,
        )
        from vaultsync.enrollment_runtime import runtime_projection

        records = runtime_projection(owner)
        assert len(records) == 3
        assert sum(r.tombstone for r in records) == 1
        for record in records:
            body = json.loads(owner.open(record))
            assert (body["payload"] is None) == record.tombstone
        assert not owner.pending_operations()


def test_authenticated_empty_projection_is_not_a_fallback(tmp_path, monkeypatch):
    import test_runtime_enrollment
    from test_enrollment_bootstrap import scenario

    from vaultsync.enrollment_runtime import runtime_projection

    target = identity(80)
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
    monkeypatch.setattr(test_runtime_enrollment, "VECTOR", vector)
    issuer, _, env, args, _ = scenario(tmp_path / "sender")
    packet = delivery.create_enrollment_delivery(
        issuer,
        signing_key=env[2][0],
        anchor={k: args[k] for k in ("checkpoint", "enrollment", "view")},
        collection=args["collection"],
        opaque_state=args["opaque_state"],
    )
    pins = {k: args[k] for k in ("current_context", "recipient_device_id", "confirmed_transcript")}
    pins["anchor_root"] = args["checkpoint"]["root"]
    owner = delivery.install_enrollment_delivery(
        tmp_path / "empty-recipient",
        packet,
        pins=pins,
        trusted_signer=env[2][0].signing_public_key,
        recipient_identity=target,
        storage_key=bytes([111]) * 32,
        require_runtime_projection=True,
    )
    assert runtime_projection(owner) == ()
    assert not owner.pending_operations()
    assert owner.observation()["state_root"] == args["checkpoint"]["state_root"]


def test_nonempty_anchor_cannot_be_replaced_by_empty_projection(tmp_path):
    v = fixture()
    packet = copy.deepcopy(v["packet"])
    packet["opaque_b64"] = base64.b64encode(
        _canonical(
            {
                "format": "atlasvault-guarded-collection",
                "version": 1,
                "route": "patch",
                "records": [],
            }
        )
    ).decode()
    packet["root"] = delivery._root(packet)
    packet["signature_b64"] = base64.b64encode(
        identity(0).sign(delivery._message(packet["root"]))
    ).decode()
    root = tmp_path / "recipient"
    with pytest.raises(delivery.EnrollmentDeliveryError):
        delivery.install_enrollment_delivery(
            root,
            packet,
            pins=v["pins"],
            trusted_signer=base64.b64decode(v["trusted_signer_b64"]),
            recipient_identity=identity(80),
            storage_key=bytes([111]) * 32,
            require_runtime_projection=True,
        )
    assert not root.exists()
