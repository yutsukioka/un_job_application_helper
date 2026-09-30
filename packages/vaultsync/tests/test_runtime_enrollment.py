"""C30 admission uses real encrypted history stores, never shared registry state."""

import copy
import hashlib
import json
import sqlite3
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


def test_enrolled_member_receives_next_rotation_delivery(tmp_path):
    import base64
    from test_atlasvault_activation_c26 import activation_record
    from vaultsync.revocation import (
        RevocationRegistry,
        _message as removal_message,
        _root as removal_root,
    )
    from vaultsync.epoch_rotation import create_epoch_rotation

    (owner, _, _), env = initialize(tmp_path)
    accept(owner, 0, env[4], backend_accept(env))
    addition = enrollment(owner, env[2][0])
    backend, http, devices, headers = env[:4]
    assert (
        http.post("/v1/vaults/vault-c26/enrollments", json=addition, headers=headers[0]).status_code
        == 200
    )
    current = backend.commitments.enrollment_membership(addition["account_id"], "vault-c26")
    registry = current["registry"]
    removal = RevocationRegistry(
        tmp_path / "next-removal",
        bytes([50]) * 32,
        addition["account_id"],
        "vault-c26",
        4,
        registry,
        current["context"]["state_root"],
    )
    removal.initialize()
    unsigned = removal.prepare(devices[1].device_id, devices[0].device_id)
    root = removal_root(unsigned)
    transition = dict(
        unsigned,
        root=root,
        signature_b64=base64.b64encode(devices[0].sign(removal_message(root))).decode(),
    )
    rotation = create_epoch_rotation(
        transition,
        registry=registry,
        state_root=current["context"]["state_root"],
        signing_key=devices[0],
    )
    record = activation_record(rotation)
    for recipient in rotation["plan"]["recipients"]:
        packet = create_device_delivery(
            record,
            recipient_device_id=recipient,
            issuer_device_id=devices[0].device_id,
            signing_key=devices[0],
            current_registry=registry,
            recovery_pending=False,
        )
        response = http.post(
            "/v1/vaults/vault-c26/activations/5/delivery-proofs", json=packet, headers=headers[0]
        )
        assert response.status_code == 200, response.text
    response = http.post("/v1/vaults/vault-c26/activations", json=rotation, headers=headers[0])
    assert response.status_code == 200, response.text
    members = backend.commitments.enrollment_membership(addition["account_id"], "vault-c26")[
        "registry"
    ]
    assert any(
        e["device_id"] == addition["target_device_id"] and e["state"] == "ACTIVE" for e in members
    )
    assert http.get("/v1/vaults/vault-c26/activations", headers=headers[1]).status_code == 403


class FailingEnrollmentConnection:
    def __init__(self, connection, statement):
        self.connection, self.statement = connection, statement
        self.failed = False

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, *args):
        if sql.startswith(self.statement) and not self.failed:
            self.failed = True
            raise sqlite3.OperationalError("synthetic disk/busy failure with private detail")
        return self.connection.execute(sql, *args)


@pytest.mark.parametrize(
    "statement",
    ["BEGIN IMMEDIATE", "SELECT body FROM activations", "INSERT INTO enrollments", "COMMIT"],
)
def test_enrollment_storage_fault_is_retryable_and_never_publishes(tmp_path, statement):
    (owner, _, _), env = initialize(tmp_path)
    accept(owner, 0, env[4], backend_accept(env))
    proof = enrollment(owner, env[2][0])
    backend, http = env[:2]
    original = backend.commitments._db
    backend.commitments._db = FailingEnrollmentConnection(original, statement)
    response = http.post("/v1/vaults/vault-c26/enrollments", json=proof, headers=env[3][0])
    assert response.status_code == 503
    assert response.json() == {"detail": "ATLAS_ACTIVATION_STORAGE_UNAVAILABLE"}
    events = backend.telemetry.snapshot()["events"]
    assert events[-1] == {"category": "storage", "outcome": "error", "status_code": 503}
    assert "private detail" not in json.dumps(backend.telemetry.snapshot())
    assert not original.in_transaction
    assert original.execute("SELECT COUNT(*) FROM enrollments").fetchone()[0] == 0
    assert (
        http.post("/v1/vaults/vault-c26/enrollments", json=proof, headers=env[3][0]).status_code
        == 200
    )


def test_verified_membership_is_cached_and_external_changes_fail_closed(tmp_path, monkeypatch):
    import atlasvault_api.enrollments as admissions

    (owner, _, _), env = initialize(tmp_path)
    accept(owner, 0, env[4], backend_accept(env))
    proof = enrollment(owner, env[2][0])
    backend, http = env[:2]
    assert (
        http.post("/v1/vaults/vault-c26/enrollments", json=proof, headers=env[3][0]).status_code
        == 200
    )
    calls = []
    original = admissions.verify_enrollment_attestation

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(admissions, "verify_enrollment_attestation", counted)
    for _ in range(10):
        value = backend.commitments.enrollment_membership(proof["account_id"], "vault-c26")
        value["registry"][0]["state"] = "REVOKED"
        assert http.get("/v1/vaults/vault-c26/activations", headers=env[3][0]).status_code == 200
    assert len(calls) <= 1
    external = sqlite3.connect(tmp_path / "backend.sqlite")
    bad = dict(proof, signature_b64="AA" * 43 + "==")
    external.execute("UPDATE enrollments SET body=?", (json.dumps(bad),))
    external.commit()
    external.close()
    from atlasvault_api.commitments import CommitmentConflict

    with pytest.raises(CommitmentConflict):
        backend.commitments.enrollment_membership(proof["account_id"], "vault-c26")


def test_external_commit_immediately_after_admission_invalidates_cache(tmp_path):
    from atlasvault_api.commitments import CommitmentConflict

    (owner, _, _), env = initialize(tmp_path)
    accept(owner, 0, env[4], backend_accept(env))
    proof = enrollment(owner, env[2][0])
    backend = env[0]
    original = backend.commitments._db

    class ConcurrentWriter:
        def __getattr__(self, name):
            return getattr(original, name)

        def execute(self, sql, *args):
            result = original.execute(sql, *args)
            if sql == "COMMIT":
                with sqlite3.connect(tmp_path / "backend.sqlite") as external:
                    external.execute(
                        "UPDATE enrollments SET body=?", (json.dumps(dict(proof, root="ab" * 32)),)
                    )
            return result

    backend.commitments._db = ConcurrentWriter()
    assert backend.commitments.accept_enrollment(
        proof["account_id"], "vault-c26", proof, env[2][0].device_id
    )
    with pytest.raises(CommitmentConflict):
        backend.commitments.enrollment_membership(proof["account_id"], "vault-c26")


def test_each_successor_verifies_only_the_new_enrollment(tmp_path, monkeypatch):
    import base64
    import atlasvault_api.enrollments as admissions
    from vaultsync.device_identity import device_identity_from_private_keys

    (owner, _, _), env = initialize(tmp_path)
    accept(owner, 0, env[4], backend_accept(env))
    backend = env[0]
    issuer = env[2][0]
    calls = []
    original = admissions.verify_enrollment_attestation

    def counted(proof, **kwargs):
        calls.append(proof["root"])
        return original(proof, **kwargs)

    monkeypatch.setattr(admissions, "verify_enrollment_attestation", counted)
    for i in range(3):
        current = backend.commitments.enrollment_membership(
            env[4]["plan"]["account_id"], "vault-c26"
        )
        target = device_identity_from_private_keys(
            signing_private_seed=bytes([80 + i]) * 32,
            agreement_private_key=bytes([90 + i]) * 32,
            created_at="2026-01-01T00:00:00Z",
            key_epoch=4,
        )
        entry = dict(
            device_id=target.device_id,
            signing_public_b64=base64.b64encode(target.signing_public_key).decode(),
            agreement_public_b64=base64.b64encode(target.agreement_public_key).decode(),
            state="ACTIVE",
        )
        unsigned = {k: v for k, v in VECTOR["proof"].items() if k not in ("root", "signature_b64")}
        unsigned.update(current["context"])
        unsigned.update(
            next_registry_generation=current["context"]["registry_generation"] + 1,
            prior_registry_root=registry_root(current["registry"]),
            resulting_registry_root=registry_root([*current["registry"], entry]),
            target_device_id=target.device_id,
            target_signing_public_b64=entry["signing_public_b64"],
            target_agreement_public_b64=entry["agreement_public_b64"],
            target_agreement_sha256=hashlib.sha256(target.agreement_public_key).hexdigest(),
            issuer_device_id=issuer.device_id,
        )
        proof = create_enrollment(
            unsigned,
            registry=current["registry"],
            context=current["context"],
            confirmed_transcript=unsigned["transcript_sha256"],
            status="ACTIVE",
            signing_key=issuer,
        )
        assert backend.commitments.accept_enrollment(
            proof["account_id"], "vault-c26", proof, issuer.device_id
        )
        assert (
            backend.commitments.enrollment_membership(proof["account_id"], "vault-c26")["records"][
                -1
            ]
            == proof
        )
        assert len(calls) == i + 1


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
    admitted = [
        e for e in backend.telemetry.snapshot()["events"] if e["category"] == "enrollment_admitted"
    ]
    assert admitted == [
        {"category": "enrollment_admitted", "outcome": "success", "status_code": 200}
    ]
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
