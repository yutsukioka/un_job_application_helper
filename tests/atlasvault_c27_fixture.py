"""Five independently keyed synthetic devices; real C26 backend admission."""

import base64
from fastapi.testclient import TestClient
from atlasvault_api.app import AtlasVaultBackend, create_app
from test_atlasvault_backend_c13 import (
    ACCOUNT_A,
    _bootstrap,
    _session,
    _signed_transition,
)
from test_atlasvault_activation_c26 import (
    VAULT,
    history_registry,
    initial_collection,
    initial_body,
    stage_activation_deliveries,
)
from vaultsync.authenticated_state_view import (
    EMPTY_REGISTRY,
    _root,
    _message,
    registry_root,
)
from vaultsync.device_identity import device_identity_from_private_keys
from vaultsync.epoch_rotation import EpochVault, create_epoch_rotation
from vaultsync.revocation import (
    RevocationRegistry,
    verify_transition,
    _root as removal_root,
    _message as removal_message,
)
from vaultsync.sync_recovery import GuardedSyncState


def setup(root, *, inbox=None):
    devices = [
        device_identity_from_private_keys(
            signing_private_seed=bytes([10 + i]) * 32,
            agreement_private_key=bytes([20 + i]) * 32,
            created_at="2026-01-01T00:00:00Z",
            key_epoch=3,
        )
        for i in range(5)
    ]
    backend = AtlasVaultBackend(commitments_path=root / "backend.sqlite")
    http = TestClient(create_app(backend))
    parent = _bootstrap(http, devices[0])["revision"]
    headers = [{"Authorization": "Bearer " + _session(http, devices[0])[0]}]
    for i in range(1, 5):
        t = _signed_transition(
            account_id=ACCOUNT_A,
            revision=f"10000000-0000-4000-8000-{i + 1:012d}",
            parent_revision=parent,
            device=devices[i],
            signer=devices[0],
        )
        response = http.post(
            f"/v1/accounts/{ACCOUNT_A}/devices", json=t, headers=headers[0]
        )
        assert response.status_code == 200
        parent = response.json()["revision"]
        headers.append({"Authorization": "Bearer " + _session(http, devices[i])[0]})
    registry = [
        dict(
            device_id=d.device_id,
            signing_public_b64=base64.b64encode(d.signing_public_key).decode(),
            agreement_public_b64=base64.b64encode(d.agreement_public_key).decode(),
            state="ACTIVE",
        )
        for d in devices
    ]
    view = dict(
        format="atlasvault-authenticated-state-view",
        version=2,
        account_id=ACCOUNT_A,
        vault_id=VAULT,
        sequence=1,
        previous_root="0" * 64,
        collection_root=initial_collection(devices[0]).root,
        registry_root=registry_root(history_registry(devices)),
        previous_registry_root=EMPTY_REGISTRY,
        key_epoch=3,
    )
    digest = _root(view)
    view.update(
        root=digest,
        signature_b64=base64.b64encode(devices[0].sign(_message(digest))).decode(),
    )
    assert (
        http.post(
            f"/v1/vaults/{VAULT}/commitments", json=view, headers=headers[0]
        ).status_code
        == 200
    )
    clients = []
    for i in range(5):
        h = GuardedSyncState(
            root / str(i) / "prior-history",
            encryption_key=bytes([50 + i]) * 32,
            account_id=ACCOUNT_A,
            vault_id=VAULT,
            collection_id="collection-c26",
            key_epoch=3,
            trusted_signer=devices[0].signing_public_key,
        )
        h.initialize()
        h.ingest(
            view,
            history_registry(devices),
            initial_collection(devices[0]).to_dict(),
            initial_body(),
        )
        c = client(root, i, registry, view)
        c.initialize({3: bytes([30]) * 32}, history=h, inbox=inbox)
        clients.append(c)
    return dict(
        backend=backend,
        http=http,
        devices=devices,
        headers=headers,
        registry=registry,
        view=view,
        clients=clients,
        root=root,
    )


def client(root, i, registry, view):
    return EpochVault(
        root / str(i),
        storage_key=bytes([50 + i]) * 32,
        device_id=registry[i]["device_id"],
        registry=registry,
        account_id=ACCOUNT_A,
        vault_id=VAULT,
        key_epoch=3,
        state_root=view["root"],
    )


def rotate(env, registry, epoch, target):
    d = env["devices"]
    r = RevocationRegistry(
        env["root"] / f"removal-{epoch}",
        bytes([50]) * 32,
        ACCOUNT_A,
        VAULT,
        epoch,
        registry,
        env["view"]["root"],
    )
    r.initialize()
    proposal = r.prepare(d[target].device_id, d[0].device_id)
    digest = removal_root(proposal)
    transition = dict(
        proposal,
        root=digest,
        signature_b64=base64.b64encode(d[0].sign(removal_message(digest))).decode(),
    )
    r.commit(transition)
    proof = create_epoch_rotation(
        transition, registry=registry, state_root=env["view"]["root"], signing_key=d[0]
    )
    route = f"/v1/vaults/{VAULT}/activations"
    prepared = stage_activation_deliveries(env["http"], env["headers"], proof, d)
    response = env["http"].post(route, json=proof, headers=env["headers"][0])
    assert response.status_code == 200
    record = env["backend"].commitments.activation(ACCOUNT_A, VAULT)
    after = verify_transition(transition, registry)
    packets = {}
    for i, identity in enumerate(d):
        if identity.device_id not in proof["plan"]["recipients"]:
            continue
        packet = next(
            item
            for item in prepared
            if item["proof"]["recipient_device_id"] == identity.device_id
        )
        delivered = env["http"].get(
            f"{route}/{epoch + 1}/delivery", headers=env["headers"][i]
        )
        assert delivered.status_code == 200
        assert delivered.json() == packet
        packets[i] = delivered.json()
    return after, packets, record


def advance_history(env, first):
    """An actual authorized writer commits state between two rotations."""
    env["clients"][0].catch_up(
        [first[1][0]],
        current_activation_id=first[2]["transition_id"],
        agreement_private_key=bytes([20]) * 32,
    )
    body = initial_body()
    update = env["clients"][0].create_commitment(body, signing_key=env["devices"][0])
    response = env["http"].post(
        f"/v1/vaults/{VAULT}/commitments",
        json=update["view"],
        headers=env["headers"][0],
    )
    assert response.status_code == 200
    env["view"] = update["view"]
    return dict(
        update, registry=first[0], opaque_state_b64=base64.b64encode(body).decode()
    )


def shared_vector(env, rounds):
    return dict(
        synthetic_test_material=True,
        initial_registry=env["registry"],
        initial_view=env["view"],
        initial_history_registry=history_registry(env["devices"]),
        initial_collection=initial_collection(env["devices"][0]).to_dict(),
        opaque_state_b64=base64.b64encode(initial_body()).decode(),
        device_ids=[d.device_id for d in env["devices"]],
        packets=[[r[1][i] for r in rounds] for i in range(3)],
        target_activation_id=rounds[-1][2]["transition_id"],
    )
