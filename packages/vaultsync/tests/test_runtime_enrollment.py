"""C30 admission uses real encrypted history stores, never shared registry state."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from test_epoch_activation import accept, backend_accept, device, initialize
from vaultsync.device_enrollment import create_enrollment
from vaultsync.epoch_rotation import RotationError
from vaultsync.revocation import registry_root

VECTOR = json.loads(
    (Path(__file__).resolve().parents[3]
     / "contracts/sync/test_vectors/atlasvault_device_enrollment_v1.json").read_text()
)


def enrollment(owner, signer):
    c = owner.enrollment_context()
    registry = owner.enrollment_registry()
    target = VECTOR["target"]
    unsigned = {k: v for k, v in VECTOR["proof"].items() if k not in ("root", "signature_b64")}
    unsigned.update(c)
    unsigned.update(
        issuer_device_id=signer.device_id,
        next_registry_generation=c["registry_generation"] + 1,
        prior_registry_root=registry_root(registry),
        resulting_registry_root=registry_root([*registry, target]),
    )
    return create_enrollment(unsigned, registry=registry, context=c,
                             confirmed_transcript=unsigned["transcript_sha256"],
                             status="ACTIVE", signing_key=signer)


def test_enrollment_adds_signed_current_member_without_rewriting_activation(tmp_path):
    (a, b, _), env = initialize(tmp_path)
    record = backend_accept(env)
    for i, client in enumerate((a, b)):
        accept(client, i, env[4], record)
    proof = enrollment(a, env[2][0])
    original_record_hash = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    for i, client in enumerate((a, b)):
        assert client.accept_enrollment(proof, confirmed_transcript=proof["transcript_sha256"])
        assert not client.accept_enrollment(proof, confirmed_transcript=proof["transcript_sha256"])
        reopened = device(tmp_path, i, env[4])
        assert reopened.enrollment_context()["registry_generation"] == 5
        assert reopened.observation()["key_epoch"] == 4
        assert proof["target_device_id"] in reopened.observation()["recipients"]
        assert reopened.observation() == client.observation()
        assert reopened._load()["journal"]["record"] == record
    assert a.observation()["registry_root"] == b.observation()["registry_root"]
    assert hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest() == original_record_hash
    # Publishing the next history view uses the existing pinned authority and
    # the additive registry root; it does not rewrite an old signed view.
    published = a.create_commitment(b'{"format":"atlasvault-guarded-collection","version":1,"route":"patch","records":[]}', signing_key=env[2][0])
    assert published["view"]["registry_root"] == proof["resulting_registry_root"]
    assert device(tmp_path, 0, env[4]).observation()["key_epoch"] == 4


@pytest.mark.parametrize("attack", ["transcript", "epoch", "registry", "state", "fork"])
def test_enrollment_rejection_never_publishes_membership(tmp_path, attack):
    (a, _, _), env = initialize(tmp_path)
    accept(a, 0, env[4], backend_accept(env))
    proof = enrollment(a, env[2][0])
    transcript = proof["transcript_sha256"]
    if attack == "transcript":
        transcript = "00" * 32
    elif attack == "fork":
        s = a._load()
        s["status"] = "RECOVERY_PENDING"
        a._file.write(s)
    else:
        proof = copy.deepcopy(proof)
        proof[{"epoch": "key_epoch", "registry": "prior_registry_root", "state": "state_root"}[attack]] = "substitution"
    before = (tmp_path / "0/activation").read_bytes()
    with pytest.raises(RotationError):
        a.accept_enrollment(proof, confirmed_transcript=transcript)
    assert (tmp_path / "0/activation").read_bytes() == before
