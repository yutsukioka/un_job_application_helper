import base64
import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "tests"),
    str(ROOT / "services/atlasvault-api"),
    str(ROOT / "packages/vaultsync"),
]
from atlasvault_api.app import AtlasVaultBackend, create_app
from atlasvault_api.commitments import CommitmentConflict, CommitmentLog
from test_atlasvault_backend_c13 import ACCOUNT_A, _bootstrap, _identities, _session
from test_atlasvault_backend_c14 import _opaque_envelope
from vaultsync.authenticated_state_view import (
    EMPTY_REGISTRY,
    StateViewError,
    _message,
    _root,
    registry_root,
)
from vaultsync.sync_queue import SignedStateCommitment
from vaultsync.sync_recovery import GuardedSyncState

DEVICE = _identities()[0]
DESCRIPTOR = DEVICE.sign_descriptor().to_dict()["descriptor"]
PUBLIC = base64.b64decode(DESCRIPTOR["signing_public_key"])
REGISTRY = registry_root(
    [
        {
            "device_id": hashlib.sha256(DEVICE.device_id.encode()).hexdigest(),
            "descriptor_sha256": hashlib.sha256(
                json.dumps(DESCRIPTOR, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
    ]
)
VAULT = "vault_c23"


def signed(sequence, previous=None, **changes):
    v = {
        "format": "atlasvault-authenticated-state-view",
        "version": 2,
        "account_id": ACCOUNT_A,
        "vault_id": VAULT,
        "sequence": sequence,
        "previous_root": previous["root"] if previous else "0" * 64,
        "collection_root": hashlib.sha256(
            f"ciphertext-state-{sequence}".encode()
        ).hexdigest(),
        "registry_root": REGISTRY,
        "previous_registry_root": previous["registry_root"]
        if previous
        else EMPTY_REGISTRY,
        "key_epoch": DESCRIPTOR["key_epoch"],
    }
    v.update(changes)
    r = _root(v)
    return dict(
        v, root=r, signature_b64=base64.b64encode(DEVICE.sign(_message(r))).decode()
    )


def append(log, v):
    return log.append(ACCOUNT_A, VAULT, v, REGISTRY, PUBLIC, DESCRIPTOR["key_epoch"])


def test_backend_atomic_conflicting_children_exact_retry_and_reopen(tmp_path):
    path = tmp_path / "commitments.sqlite"
    a = CommitmentLog(path)
    b = CommitmentLog(path)
    one = signed(1)
    assert append(a, one)
    barrier = threading.Barrier(2)

    def race(log, child):
        barrier.wait()
        try:
            return append(log, child)
        except CommitmentConflict:
            return False

    left = signed(2, one)
    right = signed(2, one, collection_root="f" * 64)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(race, a, left), pool.submit(race, b, right)]
        assert sorted(f.result() for f in futures) == [False, True]
    stored = a.read(ACCOUNT_A, VAULT)
    assert len(stored) == 2
    assert not append(b, stored[-1])
    assert not append(b, one)
    assert CommitmentLog(path).read(ACCOUNT_A, VAULT) == stored


@pytest.mark.parametrize(
    "attack",
    [
        "regression",
        "same_sequence",
        "predecessor",
        "registry",
        "registry_parent",
        "epoch",
        "account",
        "vault",
        "identifier",
    ],
)
def test_backend_rejects_hostile_append_without_altering_log(tmp_path, attack):
    log = CommitmentLog(tmp_path / "log.sqlite")
    one = signed(1)
    two = signed(2, one)
    append(log, one)
    append(log, two)
    v = signed(3, two)
    if attack == "regression":
        v = signed(1, collection_root="f" * 64)
    elif attack == "same_sequence":
        v = signed(2, one, collection_root="f" * 64)
    elif attack == "predecessor":
        v = signed(3, two, previous_root="f" * 64)
    elif attack == "registry":
        v = signed(3, two, registry_root="f" * 64)
    elif attack == "registry_parent":
        v = signed(3, two, previous_registry_root="f" * 64)
    elif attack == "epoch":
        v = signed(3, two, key_epoch=DESCRIPTOR["key_epoch"] + 1)
    elif attack == "account":
        v = signed(3, two, account_id="other")
    elif attack == "vault":
        v = signed(3, two, vault_id="other")
    else:
        v = dict(two, signature_b64=base64.b64encode(b"\0" * 64).decode())
    before = log.read(ACCOUNT_A, VAULT)
    with pytest.raises(CommitmentConflict):
        append(log, v)
    assert log.read(ACCOUNT_A, VAULT) == before


def test_real_auth_http_commitment_path_and_secret_free_errors(caplog):
    backend = AtlasVaultBackend()
    c = TestClient(create_app(backend))
    _bootstrap(c, DEVICE)
    token, _ = _session(c, DEVICE)
    headers = {"Authorization": f"Bearer {token}"}
    path = f"/v1/vaults/{VAULT}/commitments"
    one = signed(1)
    assert c.post(path, json=one).status_code == 401
    assert c.post(path, json=one, headers=headers).json()["appended"] is True
    assert c.post(path, json=one, headers=headers).json()["appended"] is False
    bad = signed(2, one, registry_root="f" * 64)
    r = c.post(path, json=bad, headers=headers)
    assert r.status_code == 409
    assert r.json() == {"detail": "Commitment conflict."}
    malformed = dict(one, plaintext="forbidden-field-sentinel")
    r = c.post(path, json=malformed, headers=headers)
    assert r.status_code == 422
    assert c.get(path, headers=headers).json() == [one]
    assert (
        token not in caplog.text
        and "forbidden-field-sentinel" not in caplog.text + r.text
    )
    assert backend.telemetry.snapshot()


def test_two_persisted_clients_receive_hostile_http_backend_history(tmp_path):
    """An adversarial transport serves actual stored bytes, not a merge-function assertion."""
    now = [0.0]
    backend = AtlasVaultBackend(monotonic=lambda: now[0])
    http = TestClient(create_app(backend))
    _bootstrap(http, DEVICE)
    token, _ = _session(http, DEVICE)
    headers = {"Authorization": f"Bearer {token}"}
    path = f"/v1/vaults/{VAULT}/commitments"
    vectors = json.loads(
        (
            ROOT
            / "contracts/sync/test_vectors/atlasvault_sync_recovery_vectors_v1.json"
        ).read_text()
    )
    registry = [
        {
            "device_id": hashlib.sha256(DEVICE.device_id.encode()).hexdigest(),
            "descriptor_sha256": hashlib.sha256(
                json.dumps(DESCRIPTOR, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
    ]
    packets = {}
    for name, parent in [
        ("one", None),
        ("two", "one"),
        ("stale_edit", "two"),
        ("pre_delete_compaction", "two"),
        ("fork_two", "one"),
    ]:
        template = vectors["packets"][name]
        body = json.loads(base64.b64decode(template["opaque_b64"]))
        for record in body["records"]:
            record["key_epoch"] = DESCRIPTOR["key_epoch"]
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        previous = packets[parent] if parent else None
        c = SignedStateCommitment.sign(
            raw,
            collection_id="collection_c21",
            sequence=template["view"]["sequence"],
            previous_root=previous["collection"]["root"] if previous else "0" * 64,
            signing_key=DEVICE,
        ).to_dict()
        v = signed(
            c["sequence"],
            previous["view"] if previous else None,
            collection_root=c["root"],
        )
        obj = _opaque_envelope(
            object_id=name, revision=name, parent_revision=None, payload=raw
        )
        r = http.put(
            f"/v1/vaults/{VAULT}/objects/{name}",
            json=obj,
            headers=dict(headers, **{"If-Match": "*", "Idempotency-Key": name}),
        )
        assert r.status_code == 200
        packets[name] = {"view": v, "collection": c}
    for name in ["one", "two"]:
        assert http.post(path, json=packets[name]["view"], headers=headers).json()[
            "appended"
        ]
    assert (
        http.post(path, json=packets["fork_two"]["view"], headers=headers).status_code
        == 409
    )
    assert (
        http.post(path, json=packets["two"]["view"], headers=headers).json()["appended"]
        is False
    )

    def client(file):
        return GuardedSyncState(
            file,
            encryption_key=bytes(range(32)),
            account_id=ACCOUNT_A,
            vault_id=VAULT,
            collection_id="collection_c21",
            key_epoch=DESCRIPTOR["key_epoch"],
            trusted_signer=PUBLIC,
        )

    def serve(c, name):
        response = http.get(f"/v1/vaults/{VAULT}/objects/{name}", headers=headers)
        assert response.status_code == 200
        p = packets[name]
        # Honest historical views are read back from the endpoint. The malicious server
        # can also fabricate transport from valid signatures it previously observed.
        views = http.get(path, headers=headers).json()
        view = next((v for v in views if v["root"] == p["view"]["root"]), p["view"])
        return c.ingest(
            view,
            registry,
            p["collection"],
            base64.b64decode(response.json()["ciphertext_b64"]),
        )

    for attack, reason in [
        ("one", "ATLAS_ROLLBACK_REJECTED"),
        ("stale_edit", "ATLAS_TOMBSTONE_RESURRECTION"),
        ("pre_delete_compaction", "ATLAS_TOMBSTONE_RESURRECTION"),
        ("fork_two", "ATLAS_STATE_EQUIVOCATION"),
    ]:
        now[0] += 61
        a = client(tmp_path / attack / "A")
        b = client(tmp_path / attack / "B")
        for c in [a, b]:
            c.initialize()
            serve(c, "one")
            serve(c, "two")
        before = a.checkpoint()
        with pytest.raises(StateViewError, match=reason):
            serve(b, attack)
        reopened = client(tmp_path / attack / "B")
        assert reopened.checkpoint() == a.checkpoint() == before
        assert reopened.recovery()["status"] == "MANUAL_REQUIRED"
        with pytest.raises(StateViewError, match="ATLAS_RECOVERY_PENDING"):
            reopened.automatic_sync(lambda c=reopened: serve(c, "two"))


def test_backend_epoch_regression(tmp_path):
    log = CommitmentLog(tmp_path / "epochs.sqlite")
    one = signed(1, key_epoch=2)
    assert log.append(ACCOUNT_A, VAULT, one, REGISTRY, PUBLIC, 2)
    with pytest.raises(CommitmentConflict):
        log.append(ACCOUNT_A, VAULT, signed(2, one, key_epoch=1), REGISTRY, PUBLIC, 2)
    assert log.read(ACCOUNT_A, VAULT) == [one]
