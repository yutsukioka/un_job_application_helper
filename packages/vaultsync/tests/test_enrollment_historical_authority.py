"""C30 RED: current-view keys alone do not carry historical author authority."""

import base64
import json

import pytest
import test_atlasvault_activation_c26 as backend_fixture
import test_epoch_activation as client_fixture
from test_epoch_activation import accept, backend_accept, initialize
from test_runtime_enrollment import enrollment

from vaultsync.anchored_history import AnchoredSyncState
from vaultsync.enrollment_bootstrap import create_history_bootstrap
from vaultsync.epoch_vault import EpochVault


def historical_scenario(root, author_index=0):
    root.mkdir(parents=True, exist_ok=True)
    seed_root = root / "seed"
    seed_root.mkdir()
    clients, env = initialize(seed_root)
    # The body is deterministic test-only material, never printed or exported.
    plaintext = b"c30-synthetic-current-view"
    ciphertext = clients[author_index].seal(
        "patch",
        plaintext,
        object_id="retained-current-record",
        revision="r1",
        signing_key=env[2][author_index],
    )
    body = json.dumps(
        {
            "format": "atlasvault-guarded-collection",
            "version": 1,
            "route": "patch",
            "records": [ciphertext.to_dict()],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    # Commit this record at the original epoch before the legitimate rotation.
    # Only fixture input is varied; production admission/activation is unchanged.
    real_root = root / "history"
    real_root.mkdir()
    with pytest.MonkeyPatch.context() as fixture_input:
        fixture_input.setattr(backend_fixture, "initial_body", lambda: body)
        fixture_input.setattr(client_fixture, "initial_body", lambda: body)
        clients, env = initialize(real_root)
    issuer = clients[0]
    record = backend_accept(env)
    accept(issuer, 0, env[4], record)
    assert issuer.open(ciphertext) == plaintext
    admission = enrollment(issuer, env[2][0])
    issuer.accept_enrollment(admission, confirmed_transcript=admission["transcript_sha256"])
    page = issuer.create_commitment(body, signing_key=env[2][0])
    checkpoint = create_history_bootstrap(
        issuer,
        enrollment=admission,
        signing_key=env[2][0],
        view=page["view"],
        collection=page["collection"],
        opaque_state=body,
    )
    args = {
        "checkpoint": checkpoint,
        "enrollment": admission,
        "registry": issuer.enrollment_registry(),
        "current_context": issuer.enrollment_context(),
        "recipient_device_id": admission["target_device_id"],
        "confirmed_transcript": admission["transcript_sha256"],
        "view": page["view"],
        "collection": page["collection"],
        "opaque_state": body,
    }
    history = AnchoredSyncState(
        root / "recipient-history",
        encryption_key=bytes([96]) * 32,
        account_id=checkpoint["account_id"],
        vault_id=checkpoint["vault_id"],
        collection_id=checkpoint["collection_id"],
        key_epoch=4,
        trusted_signer=env[2][0].signing_public_key,
        rotation_registry=args["registry"],
        anchor_root=checkpoint["root"],
        recipient_device_id=args["recipient_device_id"],
        confirmed_transcript=args["confirmed_transcript"],
        current_context=args["current_context"],
    )
    assert history.bootstrap(**args)
    recipient = EpochVault(
        root / "recipient-runtime",
        storage_key=bytes([97]) * 32,
        device_id=args["recipient_device_id"],
        registry=args["registry"],
        account_id=checkpoint["account_id"],
        vault_id=checkpoint["vault_id"],
        key_epoch=4,
        state_root=checkpoint["state_root"],
        history_origin=history.publication_origin(),
    )
    # Deliberately supply the correct retained key: a failure is not missing HPKE.
    recipient.initialize({3: bytes([30]) * 32, 4: bytes([98]) * 32}, history=history)
    assert recipient._ring(recipient._load()).vault_key_for_epoch(3) == bytes([30]) * 32
    assert recipient.observation()["state_root"] == issuer.observation()["state_root"]
    public = {
        **{k: v for k, v in args.items() if k != "opaque_state"},
        "opaque_b64": base64.b64encode(body).decode(),
        "trusted_signer_b64": base64.b64encode(env[2][0].signing_public_key).decode(),
        "ciphertext": ciphertext.to_dict(),
        "historical_author_device_id": env[2][author_index].device_id,
        "synthetic_test_only": True,
    }
    return issuer, recipient, ciphertext, plaintext, public


@pytest.mark.parametrize("author_index", [0, 2], ids=["still-active-author", "now-revoked-author"])
def test_current_view_retained_record_is_readable_after_anchored_bootstrap(tmp_path, author_index):
    issuer, recipient, ciphertext, expected, _ = historical_scenario(tmp_path, author_index)
    before = recipient.observation()
    assert issuer.open(ciphertext) == expected
    assert recipient.open(ciphertext) == expected
    assert recipient.observation() == before
