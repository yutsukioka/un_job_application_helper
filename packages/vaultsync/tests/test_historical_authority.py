"""D106 bounded current-projection witnesses, synthetic devices only."""

import base64
import copy
import hashlib
import json
import multiprocessing
import os
import time
from pathlib import Path

import pytest
from test_enrollment_historical_authority import historical_scenario

from vaultsync.anchored_history import AnchoredSyncState
from vaultsync.historical_authority import build_authority, verify_authority
from vaultsync.sync_queue import OpaqueCiphertextEnvelope, _canonical_json

VECTORS = Path(__file__).resolve().parents[3] / "contracts/sync/test_vectors"


def vector_store(path, v):
    p = v["checkpoint"]
    return AnchoredSyncState(
        path,
        encryption_key=bytes([96]) * 32,
        **{k: p[k] for k in ("account_id", "vault_id", "collection_id", "key_epoch")},
        trusted_signer=base64.b64decode(v["trusted_signer_b64"]),
        rotation_registry=v["registry"],
        anchor_root=p["root"],
        recipient_device_id=v["recipient_device_id"],
        confirmed_transcript=v["confirmed_transcript"],
        current_context=v["current_context"],
    )


def bootstrap_args(v):
    return {
        **{
            k: v[k]
            for k in (
                "checkpoint",
                "enrollment",
                "registry",
                "current_context",
                "recipient_device_id",
                "confirmed_transcript",
                "view",
                "collection",
            )
        },
        "opaque_state": base64.b64decode(v["opaque_b64"]),
    }


def vector_case():
    v = json.loads((VECTORS / "atlasvault_bootstrap_authority_diagnostic.json").read_text())[
        "cases"
    ][1]
    proof = json.loads((VECTORS / "atlasvault_historical_authority_v1.json").read_text())["cases"][
        1
    ]
    return v, proof


def test_shared_public_vector_result_digest():
    v, s = vector_case()
    result = verify_authority(
        s["proof"],
        anchor={k: v[k] for k in ("checkpoint", "enrollment", "view")},
        registry=v["registry"],
        pins={
            "anchor_root": v["checkpoint"]["root"],
            "recipient_device_id": v["recipient_device_id"],
            "confirmed_transcript": v["confirmed_transcript"],
            "current_context": v["current_context"],
        },
        trusted_signer=base64.b64decode(v["trusted_signer_b64"]),
        collection=v["collection"],
        opaque_state=base64.b64decode(v["opaque_b64"]),
    )
    assert hashlib.sha256(_canonical_json(result)).hexdigest() == s["expected_records_sha256"]


def test_shared_schema_resolves_locally_and_rejects_extra_material():
    import jsonschema
    from referencing import Registry, Resource

    root = VECTORS.parent
    schemas = {p.name: json.loads(p.read_text()) for p in root.glob("*.schema.json")}
    registry = Registry().with_resources(
        ("https://atlasvault.invalid/sync/" + name, Resource.from_contents(schema))
        for name, schema in schemas.items()
    )
    schema = schemas["atlasvault_historical_authority_v1.schema.json"]
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema, registry=registry)
    for case in json.loads((VECTORS / "atlasvault_historical_authority_v1.json").read_text())[
        "cases"
    ]:
        assert validator.is_valid(case["proof"])
        assert not validator.is_valid({**case["proof"], "untrusted": "forbidden"})


def test_exact_bootstrap_retry_preserves_installed_supplement(tmp_path):
    v, s = vector_case()
    h = vector_store(tmp_path / "history", v)
    h.bootstrap(**bootstrap_args(v))
    h.install_historical_authority(
        s["proof"], collection=v["collection"], opaque_state=base64.b64decode(v["opaque_b64"])
    )
    before = (tmp_path / "history").read_bytes()
    assert not h.bootstrap(**bootstrap_args(v))
    assert (tmp_path / "history").read_bytes() == before


def test_signed_fork_stays_durable_and_fenced_after_authority_install(tmp_path):
    v, s = vector_case()
    h = vector_store(tmp_path / "history", v)
    h.bootstrap(**bootstrap_args(v))
    h.install_historical_authority(
        s["proof"], collection=v["collection"], opaque_state=base64.b64decode(v["opaque_b64"])
    )
    before = h.checkpoint()
    fork = json.loads((VECTORS / "atlasvault_historical_authority_attacks_v1.json").read_text())[
        "fork_views"
    ][1]
    with pytest.raises(ValueError):
        h.compare_evidence([fork])
    reopened = vector_store(tmp_path / "history", v)
    assert reopened.checkpoint() == before
    assert reopened.recovery()["status"] == "RECOVERY_PENDING"
    assert reopened.evidence()["peer"] == [fork]
    with pytest.raises(ValueError):
        reopened.install_historical_authority(
            s["proof"], collection=v["collection"], opaque_state=base64.b64decode(v["opaque_b64"])
        )
    with pytest.raises(ValueError):
        reopened.automatic_sync(lambda: pytest.fail("unfenced fork"))


def test_historical_authority_cannot_admit_an_uncovered_late_record(tmp_path):
    _, owner, _, _, _ = historical_scenario(tmp_path, 2, install_authority=True)
    raw = json.loads((VECTORS / "atlasvault_historical_authority_attacks_v1.json").read_text())[
        "uncovered_late_envelope"
    ]
    before = owner.observation()
    with pytest.raises(ValueError):
        owner.open(OpaqueCiphertextEnvelope.from_dict(raw))
    assert owner.observation() == before


def install_worker(root, point):
    v, s = vector_case()
    path = Path(root) / "history"
    h = vector_store(path, v)
    original = os.replace

    def interrupt(source, target):
        if Path(target) == path and point == "before":
            Path(root, "ready").touch()
            while True:
                time.sleep(60)
        original(source, target)

    os.replace = interrupt
    h.install_historical_authority(
        s["proof"], collection=v["collection"], opaque_state=base64.b64decode(v["opaque_b64"])
    )
    Path(root, "ready").touch()
    while True:
        time.sleep(60)


@pytest.mark.parametrize("point", ["before", "after"])
def test_authority_install_sigkill_atomicity(tmp_path, point):
    v, s = vector_case()
    h = vector_store(tmp_path / "history", v)
    h.bootstrap(**bootstrap_args(v))
    before = h.checkpoint()
    worker = multiprocessing.get_context("spawn").Process(
        target=install_worker, args=(str(tmp_path), point)
    )
    worker.start()
    try:
        deadline = time.monotonic() + 30
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (tmp_path / "ready").exists()
        worker.kill()
        worker.join(10)
        assert worker.exitcode is not None and worker.exitcode < 0
        reopened = vector_store(tmp_path / "history", v)
        assert reopened.checkpoint() == before
        assert ("historical_authority" in reopened.publication_origin()["anchor"]) == (
            point == "after"
        )
        assert reopened.install_historical_authority(
            s["proof"], collection=v["collection"], opaque_state=base64.b64decode(v["opaque_b64"])
        ) == (point == "before")
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(10)


def fixture(root, *, changed=False):
    issuer, owner, record, expected, p = historical_scenario(
        root,
        2,
        changed_projection=changed,
    )
    origin = owner._history_origin
    h = owner._history(owner._load())
    return issuer, owner, record, expected, p, origin, h


def verify(proof, p, origin):
    return verify_authority(
        proof,
        anchor=origin["anchor"],
        registry=origin["registry"],
        pins=origin["pins"],
        trusted_signer=base64.b64decode(p["trusted_signer_b64"]),
        collection=p["collection"],
        opaque_state=base64.b64decode(p["opaque_b64"]),
    )


def test_bounded_proof_public_inputs_and_record_scoped_result(tmp_path):
    issuer, owner, record, _, p, origin, _ = fixture(tmp_path)
    proof = build_authority(p["checkpoint"], **p["authority_inputs"])
    before = (issuer.observation(), owner.observation())
    result = verify(proof, p, origin)
    assert set(result) == {record.object_id}
    assert result[record.object_id]["key_epoch"] == 3
    assert result[record.object_id]["author"]["state"] == "ACTIVE"
    assert any(
        e["device_id"] == result[record.object_id]["author"]["device_id"]
        and e["state"] == "REVOKED"
        for e in p["registry"]
    )
    assert (issuer.observation(), owner.observation()) == before
    assert "deliveries" not in proof and "keys" not in proof


@pytest.mark.parametrize(
    "field",
    [
        "version",
        "account_id",
        "vault_id",
        "anchor_root",
        "registry_root",
        "key_epoch",
        "collection_sha256",
        "recipient_device_id",
        "transcript_sha256",
        "registry_generation",
    ],
)
def test_bound_context_substitution_rejected(tmp_path, field):
    _, owner, _, _, p, origin, _ = fixture(tmp_path)
    proof = build_authority(p["checkpoint"], **p["authority_inputs"])
    value = proof[field]
    proof[field] = value + 1 if type(value) is int else "ff" * 32
    before = owner.observation()
    with pytest.raises(ValueError):
        verify(proof, p, origin)
    assert owner.observation() == before


@pytest.mark.parametrize(
    "attack",
    [
        "missing-view",
        "duplicate-view",
        "reordered",
        "view-root",
        "view-signature",
        "missing-descriptor",
        "duplicate-descriptor",
        "descriptor-key",
        "missing-revocation",
        "revoked-signer",
        "historical-author-state",
        "registry-entry",
        "extra-field",
        "oversized",
    ],
)
def test_complete_authenticated_lineage_required(tmp_path, attack):
    _, owner, _, _, p, origin, _ = fixture(tmp_path)
    proof = build_authority(p["checkpoint"], **p["authority_inputs"])
    if attack == "missing-view":
        proof["views"].pop(0)
    elif attack == "duplicate-view":
        proof["views"][0] = copy.deepcopy(proof["views"][1])
    elif attack == "reordered":
        proof["views"].reverse()
    elif attack == "view-root":
        proof["views"][0]["collection_root"] = "ff" * 32
    elif attack == "view-signature":
        proof["views"][0]["signature_b64"] = base64.b64encode(bytes(64)).decode()
    elif attack == "missing-descriptor":
        proof["signed_descriptors"].pop(0)
    elif attack == "duplicate-descriptor":
        proof["signed_descriptors"].append(copy.deepcopy(proof["signed_descriptors"][0]))
    elif attack == "descriptor-key":
        proof["signed_descriptors"][0]["descriptor"]["signing_public_key"] = "substituted"
    elif attack == "missing-revocation":
        proof["revocation"] = {}
    elif attack in ("revoked-signer", "historical-author-state"):
        target = proof["revocation"][
            "initiator_device_id" if attack == "revoked-signer" else "target_device_id"
        ]
        next(e for e in proof["prior_registry"] if e["device_id"] == target)["state"] = "REVOKED"
    elif attack == "registry-entry":
        proof["prior_registry"].pop(0)
    elif attack == "extra-field":
        proof["asserted_authority"] = True
    else:
        proof["views"] *= 257
    before = owner.observation()
    with pytest.raises(ValueError):
        verify(proof, p, origin)
    assert owner.observation() == before


def test_changed_prior_projection_is_a_stable_fail_closed_result(tmp_path):
    _, owner, _, _, p, origin, _ = fixture(tmp_path, changed=True)
    proof = build_authority(p["checkpoint"], **p["authority_inputs"])
    before = owner.observation()
    with pytest.raises(ValueError, match="ATLAS_HISTORY_PREIMAGE_REQUIRED"):
        verify(proof, p, origin)
    assert owner.observation() == before


def test_same_signed_evidence_cannot_bootstrap_another_anchor(tmp_path):
    *_, p, _origin, _ = fixture(tmp_path / "first")
    proof = build_authority(p["checkpoint"], **p["authority_inputs"])
    *_, other, other_origin, _ = fixture(tmp_path / "second")
    with pytest.raises(ValueError):
        verify(proof, other, other_origin)


def test_install_is_once_durable_and_idempotent(tmp_path):
    _, _, _, _, p, origin, _ = fixture(tmp_path)
    context = dict(
        account_id=p["checkpoint"]["account_id"],
        vault_id=p["checkpoint"]["vault_id"],
        collection_id=p["checkpoint"]["collection_id"],
        key_epoch=4,
        trusted_signer=base64.b64decode(p["trusted_signer_b64"]),
        rotation_registry=p["registry"],
        **origin["pins"],
    )
    history = AnchoredSyncState(
        tmp_path / "recipient-history", encryption_key=bytes([96]) * 32, **context
    )
    proof = build_authority(p["checkpoint"], **p["authority_inputs"])
    args = {"collection": p["collection"], "opaque_state": base64.b64decode(p["opaque_b64"])}
    before = history.checkpoint()
    assert history.install_historical_authority(proof, **args)
    reopened = AnchoredSyncState(
        tmp_path / "recipient-history", encryption_key=bytes([96]) * 32, **context
    )
    assert not reopened.install_historical_authority(proof, **args)
    assert reopened.checkpoint() == before
    assert reopened.publication_origin()["anchor"]["historical_authority"]["proof"] == proof


@pytest.mark.parametrize("status", ["MANUAL_REQUIRED", "RECOVERY_PENDING"])
def test_pending_history_blocks_installation(tmp_path, status):
    _, _, _, _, p, origin, _ = fixture(tmp_path)
    context = dict(
        account_id=p["checkpoint"]["account_id"],
        vault_id=p["checkpoint"]["vault_id"],
        collection_id=p["checkpoint"]["collection_id"],
        key_epoch=4,
        trusted_signer=base64.b64decode(p["trusted_signer_b64"]),
        rotation_registry=p["registry"],
        **origin["pins"],
    )
    history = AnchoredSyncState(
        tmp_path / "recipient-history", encryption_key=bytes([96]) * 32, **context
    )
    state = history._load()
    state["status"] = status
    history._store.write(state)
    proof = build_authority(p["checkpoint"], **p["authority_inputs"])
    with pytest.raises(ValueError):
        history.install_historical_authority(
            proof, collection=p["collection"], opaque_state=base64.b64decode(p["opaque_b64"])
        )
    assert history._load()["status"] == status
