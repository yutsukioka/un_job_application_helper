"""C27 integration RED: separate owners, backend delivery and durable fencing."""

import copy
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import pytest
from atlasvault_c27_fixture import setup, rotate, client, advance_history
from vaultsync.epoch_rotation import RotationError
from vaultsync.authenticated_state_view import _root, _message
import base64
from vaultsync.sync_queue import DurableEncryptedInbox, EncryptedPatchOperation


def test_cleanup_preserves_keys_required_by_pending_inbox(tmp_path):
    vector = Path(__file__).resolve().parents[3] / "contracts/sync/test_vectors/atlasvault_encrypted_patch_queue_vectors_v1.json"
    raw = json.loads(vector.read_text())["operations"][0]
    raw["envelope"]["key_epoch"] = 3
    inbox = DurableEncryptedInbox(tmp_path / "incoming", encryption_key=bytes([81]) * 32)
    inbox.stage_page(expected_cursor=None, next_cursor="next", operations=[EncryptedPatchOperation.from_dict(raw)])
    e = setup(tmp_path, inbox=inbox)
    a = rotate(e, e["registry"], 3, 3)
    c = e["clients"][2]
    c.catch_up([a[1][2]], current_activation_id=a[2]["transition_id"], agreement_private_key=bytes([22]) * 32)
    class Storage:
        def delete_epoch(self, epoch):
            pytest.fail("pending inbox key reached deletion boundary")
        def contains_epoch(self, epoch):
            return True
    with pytest.raises(RotationError, match="ATLAS_CLEANUP_PENDING"):
        c.cleanup_epochs(retain_epochs={4}, storage=Storage())
    assert c.available_epochs() == (3, 4)


def scenario(tmp_path):
    e = setup(tmp_path)
    first = rotate(e, e["registry"], 3, 3)
    second = rotate(e, first[0], 4, 4)
    return e, first, second


def test_existing_c26_publication_accepts_its_own_v2_attestation(tmp_path):
    e = setup(tmp_path)
    a = rotate(e, e["registry"], 3, 3)
    c = e["clients"][1]
    c.accept_rotation(a[2]["proof"], accepted_record=a[2], agreement_private_key=bytes([21]) * 32)
    assert c.catch_up(
        [a[1][1]],
        current_activation_id=a[2]["transition_id"],
        agreement_private_key=bytes([21]) * 32,
    )
    assert c.delivery(e["devices"][1].device_id) == a[1][1]["wrapper"]
    with pytest.raises(RotationError):
        c.delivery(e["devices"][0].device_id)


def test_fork_evidence_survives_and_blocks_catch_up_and_cleanup(tmp_path):
    e, a, b = scenario(tmp_path)
    c = e["clients"][2]
    unsigned = {k: v for k, v in e["view"].items() if k not in ("root", "signature_b64")}
    unsigned["collection_root"] = "ab" * 32
    root = _root(unsigned)
    fork = dict(
        unsigned,
        root=root,
        signature_b64=base64.b64encode(e["devices"][0].sign(_message(root))).decode(),
    )
    with pytest.raises(RotationError):
        c.compare_evidence([fork])
    before = c.recovery()
    with pytest.raises(RotationError):
        c.catch_up(
            [a[1][2], b[1][2]],
            current_activation_id=b[2]["transition_id"],
            agreement_private_key=bytes([22]) * 32,
        )
    reopened = client(tmp_path, 2, e["registry"], e["view"])
    assert reopened.recovery() == before
    assert before["status"] in ("MANUAL_REQUIRED", "RECOVERY_PENDING")
    assert before["local"] and before["peer"]
    assert reopened.observation()["sequence"] == 1
    assert len(json.dumps(before)) < 10000
    assert not any(
        k in json.dumps(before)
        for k in ("ciphertext_b64", "vault_key", "access_token", "private_key")
    )


def test_intervening_authenticated_history_is_required_and_preserved(tmp_path):
    e = setup(tmp_path)
    first = rotate(e, e["registry"], 3, 3)
    update = advance_history(e, first)
    second = rotate(e, first[0], 4, 4)
    c = e["clients"][2]
    packets = [first[1][2], second[1][2]]
    with pytest.raises(RotationError):
        c.catch_up(
            packets,
            current_activation_id=second[2]["transition_id"],
            agreement_private_key=bytes([22]) * 32,
        )
    assert c.observation()["sequence"] == 1
    assert c.catch_up(
        packets,
        history_updates=[update],
        current_activation_id=second[2]["transition_id"],
        agreement_private_key=bytes([22]) * 32,
    )
    assert c.observation()["sequence"] == 2
    assert c.observation()["state_root"] == update["view"]["root"]
    assert c.observation()["key_epoch"] == 5


def test_multiple_epochs_only_own_packets_and_durable_convergence(tmp_path):
    e, a, b = scenario(tmp_path)
    for i in range(3):
        c = e["clients"][i]
        packets = [a[1][i], b[1][i]]
        assert c.catch_up(
            packets,
            current_activation_id=b[2]["transition_id"],
            agreement_private_key=bytes([20 + i]) * 32,
        )
        assert not c.catch_up(
            packets,
            current_activation_id=b[2]["transition_id"],
            agreement_private_key=bytes([20 + i]) * 32,
        )
        reopened = client(tmp_path, i, e["registry"], e["view"])
        assert reopened.observation()["status"] == "ACTIVE"
        assert reopened.observation()["key_epoch"] == 5
        assert reopened.observation()["recipients"] == b[2]["proof"]["plan"]["recipients"]
        assert not reopened.pending_operations()
    roots = [c.observation()["registry_root"] for c in e["clients"][:3]]
    assert len(set(roots)) == 1
    route = "/v1/vaults/vault-c26/activations"
    for i in (3, 4):
        assert e["http"].get(f"{route}/5/delivery", headers=e["headers"][i]).status_code == 403


@pytest.mark.parametrize(
    "attack", ["missing", "reordered", "wrong_recipient", "stale_target", "wrong_root", "v1"]
)
def test_bad_chain_fences_without_partial_key_installation(tmp_path, attack):
    e, a, b = scenario(tmp_path)
    packets = copy.deepcopy([a[1][2], b[1][2]])
    target = b[2]["transition_id"]
    if attack == "missing":
        packets = packets[1:]
    if attack == "reordered":
        packets.reverse()
    if attack == "wrong_recipient":
        packets[1] = b[1][0]
    if attack == "stale_target":
        target = a[2]["transition_id"]
    if attack == "wrong_root":
        packets[0]["proof"]["plan"]["state_root"] = "ab" * 32
    if attack == "v1":
        packets = [a[2]]
    c = e["clients"][2]
    with pytest.raises(RotationError):
        c.catch_up(packets, current_activation_id=target, agreement_private_key=bytes([22]) * 32)
    reopened = client(tmp_path, 2, e["registry"], e["view"])
    assert reopened.observation()["status"] in (
        "CATCH_UP_PENDING",
        "RECOVERY_PENDING",
        "PER_DEVICE_PROOF_REQUIRED",
    )
    assert reopened.observation()["key_epoch"] == 3
    with pytest.raises(RotationError):
        reopened.seal(
            "patch", b"synthetic", object_id="one", revision="r1", signing_key=e["devices"][2]
        )


def test_corrupted_publication_recovery_and_cleanup(tmp_path):
    e, a, b = scenario(tmp_path)
    c = e["clients"][2]
    packets = [a[1][2], b[1][2]]
    c.catch_up(
        packets, current_activation_id=b[2]["transition_id"], agreement_private_key=bytes([22]) * 32
    )
    (tmp_path / "2" / "activation").write_bytes(b"corrupted synthetic publication")
    reopened = client(tmp_path, 2, e["registry"], e["view"])
    reopened.recover_publication()
    assert reopened.observation()["key_epoch"] == 5
    assert reopened.observation()["status"] == "CATCH_UP_PENDING"
    with pytest.raises(RotationError):
        reopened.seal(
            "patch", b"synthetic", object_id="held", revision="r1", signing_key=e["devices"][2]
        )
    reopened.catch_up(
        packets, current_activation_id=b[2]["transition_id"], agreement_private_key=bytes([22]) * 32
    )
    removed = []

    class Deletion:
        def delete_epoch(self, epoch):
            removed.append(epoch)

        def contains_epoch(self, epoch):
            return epoch not in removed

    reopened.cleanup_epochs(retain_epochs={5}, storage=Deletion())
    assert sorted(removed) == [3, 4]


def test_cleanup_intent_is_durable_and_pending_view_is_visible(tmp_path):
    e, a, b = scenario(tmp_path)
    c = e["clients"][2]
    c.catch_up(
        [a[1][2], b[1][2]],
        current_activation_id=b[2]["transition_id"],
        agreement_private_key=bytes([22]) * 32,
    )
    removed = set()

    class Deletion:
        def delete_epoch(self, epoch):
            removed.add(epoch)

        def contains_epoch(self, epoch):
            return epoch not in removed

    def stop(stage):
        if stage == "cleanup_pending":
            raise RuntimeError("synthetic interruption")

    with pytest.raises(RotationError):
        c._cleanup_epochs_for_testing(retain_epochs={5}, storage=Deletion(), checkpoint=stop)
    reopened = client(tmp_path, 2, e["registry"], e["view"])
    assert reopened.recovery()["status"] == "CLEANUP_PENDING"
    with pytest.raises(RotationError):
        reopened.cleanup_epochs(retain_epochs={4, 5}, storage=Deletion())
    reopened.cleanup_epochs(retain_epochs={5}, storage=Deletion())
    assert removed == {3, 4}
    assert reopened.recovery()["status"] == "ACTIVE"
    assert reopened.available_epochs() == [5]
    reopened.cleanup_epochs(retain_epochs={5}, storage=Deletion())
    assert sorted(removed) == [3, 4]


@pytest.mark.parametrize(
    "stage",
    [
        "catch_up_pending",
        "verified_epoch",
        "before_local_commit",
        "after_recovery_record",
        "after_local_commit",
        "cleanup_pending",
        "deleted_epoch",
    ],
)
def test_real_kill_restart(tmp_path, stage):
    e, a, b = scenario(tmp_path)
    inputs = tmp_path / "public-packets.json"
    inputs.write_text(
        json.dumps(
            dict(
                packets=[a[1][2], b[1][2]],
                registry=e["registry"],
                view=e["view"],
                target=b[2]["transition_id"],
            )
        )
    )
    worker = Path(__file__).with_name("epoch_catch_up_worker.py")
    p = subprocess.Popen(
        [sys.executable, "-B", "-S", str(worker), str(tmp_path), stage],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    marker = tmp_path / "ready"
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and p.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "worker did not reach deterministic interruption"
        os.kill(p.pid, signal.SIGKILL)
        p.wait(timeout=10)
        assert p.returncode == -signal.SIGKILL
        c = client(tmp_path, 2, e["registry"], e["view"])
        if stage == "after_recovery_record":
            with pytest.raises(RotationError):
                c.observation()
            c.recover_publication()
        observation = c.observation()
        if stage == "after_local_commit":
            assert observation["key_epoch"] == 5
        else:
            assert observation["status"] in ("CATCH_UP_PENDING", "CLEANUP_PENDING")
        if stage not in ("cleanup_pending", "deleted_epoch"):
            c.catch_up(
                [a[1][2], b[1][2]],
                current_activation_id=b[2]["transition_id"],
                agreement_private_key=bytes([22]) * 32,
            )
            assert c.observation()["key_epoch"] == 5
        else:

            class Deletion:
                def delete_epoch(self, epoch):
                    (tmp_path / f"deleted-{epoch}").write_text("deleted")

                def contains_epoch(self, epoch):
                    return not (tmp_path / f"deleted-{epoch}").exists()

            c.cleanup_epochs(retain_epochs={5}, storage=Deletion())
            assert c.available_epochs() == [5]
        assert c.observation()["status"] == "ACTIVE"
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)
