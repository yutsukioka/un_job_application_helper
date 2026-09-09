"""D100/D106 delivery: synthetic custody, real HPKE and independent stores."""

import base64
import copy
import hashlib
import json
import multiprocessing
import os
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import test_runtime_enrollment
from test_enrollment_historical_authority import historical_scenario

from vaultsync.device_identity import device_identity_from_private_keys
from vaultsync.enrollment_delivery import (
    EnrollmentDeliveryError,
    _message,
    _root,
    create_enrollment_delivery,
    install_enrollment_delivery,
)
from vaultsync.historical_authority import build_authority


def identity(index):
    return device_identity_from_private_keys(
        signing_private_seed=bytes([10 + index]) * 32,
        agreement_private_key=bytes([20 + index]) * 32,
        created_at="2026-01-01T00:00:00Z",
        key_epoch=3,
    )


def scenario(root, author=0):
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
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(test_runtime_enrollment, "VECTOR", vector)
        issuer, _, envelope, expected, public = historical_scenario(root / "issuer", author)
    anchor = {k: public[k] for k in ("checkpoint", "enrollment", "view")}
    authority = build_authority(anchor["checkpoint"], **public["authority_inputs"])
    pins = {
        "anchor_root": public["checkpoint"]["root"],
        "recipient_device_id": public["recipient_device_id"],
        "confirmed_transcript": public["confirmed_transcript"],
        "current_context": public["current_context"],
    }
    package = create_enrollment_delivery(
        issuer,
        signing_key=identity(0),
        anchor=anchor,
        collection=public["collection"],
        opaque_state=base64.b64decode(public["opaque_b64"]),
        historical_authority=authority,
    )
    options = {
        "pins": pins,
        "trusted_signer": identity(0).signing_public_key,
        "recipient_identity": identity(80),
        "storage_key": bytes([111]) * 32,
    }
    return issuer, envelope, expected, package, options


@pytest.mark.parametrize("author", [0, 2], ids=["active-author", "revoked-historical-author"])
def test_recipient_hpke_delivery_installs_only_current_view_epochs(tmp_path, author):
    issuer, envelope, expected, package, options = scenario(tmp_path, author)
    before = issuer.observation()
    installed = install_enrollment_delivery(tmp_path / "new-device", package, **options)
    assert installed.open(envelope) == expected
    assert installed.observation()["key_epoch"] == before["key_epoch"]
    assert installed.observation()["registry_root"] == before["registry_root"]
    assert issuer.observation() == before
    reopened = install_enrollment_delivery(tmp_path / "new-device", package, **options)
    assert reopened.observation() == installed.observation()
    assert reopened.open(envelope) == expected
    assert [p["key_epoch"] for p in package["deliveries"]] == [3, 4]


@pytest.mark.parametrize(
    "attack",
    [
        "wrapper",
        "missing",
        "duplicate",
        "reorder",
        "recipient",
        "anchor",
        "authority",
        "suite",
        "signature",
    ],
)
def test_delivery_rejection_writes_no_recipient_state(tmp_path, attack):
    _, _, _, package, options = scenario(tmp_path)
    bad = copy.deepcopy(package)
    if attack == "wrapper":
        bad["deliveries"][0]["ciphertext_b64"] = base64.b64encode(bytes(48)).decode()
    elif attack == "missing":
        bad["deliveries"].pop()
    elif attack == "duplicate":
        bad["deliveries"].append(bad["deliveries"][0])
    elif attack == "reorder":
        bad["deliveries"].reverse()
    elif attack == "recipient":
        options["recipient_identity"] = identity(81)
    elif attack == "anchor":
        bad["anchor"]["checkpoint"]["root"] = "01" * 32
    elif attack == "authority":
        bad["historical_authority"]["views"].reverse()
    elif attack == "suite":
        bad["hpke_suite"] = "wrong"
    else:
        bad["signature_b64"] = base64.b64encode(bytes(64)).decode()
    destination = tmp_path / "new-device"
    with pytest.raises(EnrollmentDeliveryError):
        install_enrollment_delivery(destination, bad, **options)
    assert not destination.exists()


def test_established_owner_is_not_overwritten_by_delivery(tmp_path):
    _, _, _, package, options = scenario(tmp_path)
    path = tmp_path / "new-device"
    installed = install_enrollment_delivery(path, package, **options)
    state = installed._load()
    state["status"] = "RECOVERY_PENDING"
    installed._file.write(state)
    before = installed._file.path.read_bytes()
    with pytest.raises(EnrollmentDeliveryError):
        install_enrollment_delivery(path, package, **options)
    assert installed._file.path.read_bytes() == before


@pytest.mark.parametrize(
    "attack", ["missing", "duplicate", "reorder", "extra-epoch", "wrong-anchor", "preimage"]
)
def test_valid_delivery_signature_does_not_replace_authority_or_completeness(tmp_path, attack):
    _, _, _, packet, options = scenario(tmp_path)
    if attack == "missing":
        packet["deliveries"].pop()
    elif attack == "duplicate":
        packet["deliveries"].append(packet["deliveries"][0])
    elif attack == "reorder":
        packet["deliveries"].reverse()
    elif attack == "extra-epoch":
        packet["deliveries"].insert(0, dict(packet["deliveries"][0], key_epoch=2))
    elif attack == "wrong-anchor":
        options["pins"]["anchor_root"] = "fe" * 32
    else:
        packet["historical_authority"]["views"].pop(0)
    packet["root"] = _root(packet)
    packet["signature_b64"] = base64.b64encode(identity(0).sign(_message(packet["root"]))).decode()
    destination = tmp_path / "new-device"
    with pytest.raises(EnrollmentDeliveryError):
        install_enrollment_delivery(destination, packet, **options)
    assert not destination.exists()


def test_shared_delivery_vector_schema_and_hpke_install(tmp_path):
    import jsonschema

    root = Path(__file__).resolve().parents[3] / "contracts/sync"
    schema = json.loads((root / "atlasvault_enrollment_delivery_v1.schema.json").read_text())
    vectors = json.loads((root / "test_vectors/atlasvault_enrollment_delivery_v1.json").read_text())
    for index, case in enumerate(vectors["cases"]):
        jsonschema.validate(case["packet"], schema)
        owner = install_enrollment_delivery(
            tmp_path / str(index),
            case["packet"],
            pins=case["pins"],
            trusted_signer=base64.b64decode(case["trusted_signer_b64"]),
            recipient_identity=identity(80),
            storage_key=bytes([111]) * 32,
        )
        from vaultsync.sync_queue import OpaqueCiphertextEnvelope

        assert (
            hashlib.sha256(
                owner.open(OpaqueCiphertextEnvelope.from_dict(case["envelope"]))
            ).hexdigest()
            == case["opened_sha256"]
        )


def test_concurrent_delivery_retry_and_conflict(tmp_path):
    issuer, _, _, package, options = scenario(tmp_path)
    other = create_enrollment_delivery(
        issuer,
        signing_key=identity(0),
        anchor=package["anchor"],
        collection=package["collection"],
        opaque_state=base64.b64decode(package["opaque_b64"]),
        historical_authority=package["historical_authority"],
    )
    assert other["root"] != package["root"]
    destination = tmp_path / "new-device"

    def attempt(value):
        try:
            return install_enrollment_delivery(destination, value, **options).observation()
        except EnrollmentDeliveryError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [package, other]))
    assert sum(result is not None for result in results) == 1
    index = next(i for i, result in enumerate(results) if result is not None)
    winner = [package, other][index]
    with ThreadPoolExecutor(max_workers=2) as pool:
        retried = list(pool.map(attempt, [winner, winner]))
    assert retried[0] == retried[1] == results[index]


def _install_process(path, packet, pins, public, point):
    from vaultsync.epoch_vault import EpochVault

    original = EpochVault.initialize
    marker = Path(path).parent / "install-marker"

    def pause():
        marker.write_text(point)
        while True:
            time.sleep(0.05)

    def interrupted(self, *args, **kwargs):
        if point == "before":
            pause()
        result = original(self, *args, **kwargs)
        pause()
        return result

    EpochVault.initialize = interrupted
    install_enrollment_delivery(
        path,
        packet,
        pins=pins,
        trusted_signer=public,
        recipient_identity=identity(80),
        storage_key=bytes([111]) * 32,
    )


@pytest.mark.parametrize("point", ["before", "after"])
def test_hpke_install_sigkill_restarts_without_replacing_an_anchor(tmp_path, point):
    _, envelope, expected, packet, options = scenario(tmp_path)
    destination = tmp_path / "new-device"
    child = multiprocessing.get_context("spawn").Process(
        target=_install_process,
        args=(str(destination), packet, options["pins"], options["trusted_signer"], point),
    )
    child.start()
    try:
        deadline = time.monotonic() + 15
        marker = tmp_path / "install-marker"
        while not marker.exists() and child.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        assert (destination / "activation").exists() == (point == "after")
        os.kill(child.pid, signal.SIGKILL)
        child.join(5)
        assert child.exitcode == -signal.SIGKILL
    finally:
        if child.is_alive():
            child.kill()
            child.join(5)
    restored = install_enrollment_delivery(destination, packet, **options)
    assert restored.open(envelope) == expected
    assert restored.observation()["status"] == "ACTIVE"
    assert (
        install_enrollment_delivery(destination, packet, **options).observation()
        == restored.observation()
    )
