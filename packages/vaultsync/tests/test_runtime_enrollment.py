"""C30 admission uses real encrypted history stores, never shared registry state."""

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_epoch_activation import accept, backend_accept, device, initialize

from vaultsync.device_delivery import create_device_delivery
from vaultsync.device_enrollment import create_enrollment
from vaultsync.epoch_rotation import RotationError
from vaultsync.revocation import registry_root

VECTOR = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "contracts/sync/test_vectors/atlasvault_device_enrollment_v1.json"
    ).read_text()
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
    return create_enrollment(
        unsigned,
        registry=registry,
        context=c,
        confirmed_transcript=unsigned["transcript_sha256"],
        status="ACTIVE",
        signing_key=signer,
    )


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
    assert (
        hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
        == original_record_hash
    )
    # Publishing the next history view uses the existing pinned authority and
    # the additive registry root; it does not rewrite an old signed view.
    published = a.create_commitment(
        b'{"format":"atlasvault-guarded-collection","version":1,"route":"patch","records":[]}',
        signing_key=env[2][0],
    )
    assert published["view"]["registry_root"] == proof["resulting_registry_root"]
    assert device(tmp_path, 0, env[4]).observation()["key_epoch"] == 4
    packet = create_device_delivery(
        record,
        recipient_device_id=env[2][0].device_id,
        issuer_device_id=env[2][0].device_id,
        signing_key=env[2][0],
        current_registry=a.enrollment_registry(),
        recovery_pending=False,
    )
    assert a.catch_up(
        [packet],
        current_activation_id=record["transition_id"],
        agreement_private_key=bytes([20]) * 32,
    )
    assert not a.catch_up(
        [packet],
        current_activation_id=record["transition_id"],
        agreement_private_key=bytes([20]) * 32,
    )
    assert a.observation()["registry_root"] == proof["resulting_registry_root"]
    assert device(tmp_path, 0, env[4]).enrollment_context()["registry_generation"] == 5


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
        proof[
            {"epoch": "key_epoch", "registry": "prior_registry_root", "state": "state_root"}[attack]
        ] = "substitution"
    before = (tmp_path / "0/activation").read_bytes()
    with pytest.raises(RotationError):
        a.accept_enrollment(proof, confirmed_transcript=transcript)
    assert (tmp_path / "0/activation").read_bytes() == before


def test_backend_enrollment_cas_and_exact_retry_preserve_activation(tmp_path):
    (a, _, _), env = initialize(tmp_path)
    record = backend_accept(env)
    accept(a, 0, env[4], record)
    proof = enrollment(a, env[2][0])
    backend, http = env[:2]
    path = "/v1/vaults/vault-c26/enrollments"
    assert http.post(path, json=proof).status_code == 401
    assert http.post(path, json=proof, headers=env[3][2]).status_code == 403
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: http.post(path, json=proof, headers=env[3][0]), range(2)))
    assert [r.status_code for r in replies] == [200, 200]
    assert sorted(r.json()["appended"] for r in replies) == [False, True]
    assert backend.commitments.activation(proof["account_id"], "vault-c26") == record
    before = backend.commitments.read(proof["account_id"], "vault-c26")
    for field in (
        "key_epoch",
        "activation_id",
        "state_root",
        "prior_registry_root",
        "issuer_device_id",
    ):
        bad = dict(proof, **{field: 3 if field == "key_epoch" else "bad"})
        assert http.post(path, json=bad, headers=env[3][0]).status_code in (409, 422)
    assert backend.commitments.read(proof["account_id"], "vault-c26") == before
    from atlasvault_api.commitments import CommitmentLog

    reopened = CommitmentLog(tmp_path / "backend.sqlite")
    membership = reopened.enrollment_membership(proof["account_id"], "vault-c26")
    assert membership["context"]["registry_generation"] == 5
    assert registry_root(membership["registry"]) == proof["resulting_registry_root"]
    reopened.require_active_epoch(proof["account_id"], "vault-c26", 4, proof["target_device_id"])


def test_backend_conflicting_signed_enrollments_have_one_winner(tmp_path):
    (owner, _, _), env = initialize(tmp_path)
    accept(owner, 0, env[4], backend_accept(env))
    first = enrollment(owner, env[2][0])
    unsigned = {k: v for k, v in first.items() if k not in ("root", "signature_b64")}
    unsigned["transcript_sha256"] = "a1" * 32
    second = create_enrollment(
        unsigned,
        registry=owner.enrollment_registry(),
        context=owner.enrollment_context(),
        confirmed_transcript=unsigned["transcript_sha256"],
        status="ACTIVE",
        signing_key=env[2][0],
    )
    backend, http = env[:2]
    path = "/v1/vaults/vault-c26/enrollments"
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(lambda proof: http.post(path, json=proof, headers=env[3][0]), [first, second])
        )
    assert sorted(r.status_code for r in replies) == [200, 409]
    membership = backend.commitments.enrollment_membership(first["account_id"], "vault-c26")
    assert len(membership["records"]) == 1
    accepted = next(
        proof for proof, reply in zip([first, second], replies) if reply.status_code == 200
    )
    assert membership["records"] == [accepted]
    assert http.post(path, json=accepted, headers=env[3][0]).json()["appended"] is False
