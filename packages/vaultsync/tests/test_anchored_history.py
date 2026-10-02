"""D104: malicious signed suffixes against independently persisted anchored stores."""

import base64
import copy
import json
import multiprocessing
import time
from pathlib import Path

import pytest
from test_enrollment_bootstrap import scenario
from test_sync_recovery import P

from vaultsync.anchored_history import AnchoredSyncState
from vaultsync.authenticated_state_view import EMPTY_REGISTRY, StateViewError, _message, _root
from vaultsync.enrollment_bootstrap import BootstrapError
from vaultsync.revocation import registry_root
from vaultsync.sync_queue import SignedStateCommitment
from vaultsync.sync_recovery import GuardedSyncState


def packet(args, signer, *, previous=None, sequence=None, records=(), route="patch", **changes):
    prior = previous or args["view"]
    next_sequence = sequence or prior["sequence"] + 1
    body = json.dumps(
        {
            "format": "atlasvault-guarded-collection",
            "version": 1,
            "route": route,
            "records": list(records),
        },
        sort_keys=True,
    ).encode()
    c = SignedStateCommitment.sign(
        body,
        collection_id="collection-c26",
        sequence=next_sequence,
        previous_root="0" * 64 if next_sequence == 1 else prior["collection_root"],
        signing_key=signer,
    )
    unsigned = {k: v for k, v in prior.items() if k not in ("root", "signature_b64")}
    unsigned.update(
        sequence=c.sequence,
        collection_root=c.root,
        previous_root="0" * 64 if next_sequence == 1 else prior["root"],
        previous_registry_root=EMPTY_REGISTRY if next_sequence == 1 else prior["registry_root"],
        registry_root=registry_root(args["registry"]),
    )
    unsigned.update(changes)
    root = _root(unsigned)
    view = dict(
        unsigned, root=root, signature_b64=base64.b64encode(signer.sign(_message(root))).decode()
    )
    return view, args["registry"], c.to_dict(), body


@pytest.mark.parametrize(
    "attack", ["sub_anchor", "predecessor", "same_sequence", "registry", "epoch", "gap"]
)
def test_anchored_replay_and_non_chaining_roots_fence_after_restart(tmp_path, attack):
    _, _, env, args, fresh = scenario(tmp_path)
    fresh().bootstrap(**args)
    c = fresh()
    before = c.checkpoint()
    options = {
        "sub_anchor": {"sequence": args["view"]["sequence"] - 1},
        "predecessor": {"previous_root": "ab" * 32},
        "same_sequence": {"sequence": args["view"]["sequence"]},
        "registry": {"registry_root": "ab" * 32},
        "epoch": {"key_epoch": 3},
        "gap": {"sequence": args["view"]["sequence"] + 2},
    }
    with pytest.raises(StateViewError):
        c.ingest(*packet(args, env[2][0], **options[attack]))
    assert fresh().checkpoint() == before
    assert fresh().recovery()["status"] == "RECOVERY_PENDING"
    with pytest.raises(StateViewError, match="ATLAS_RECOVERY_PENDING"):
        fresh().automatic_sync(lambda: pytest.fail("unfenced write"))


def test_independent_fork_evidence_never_selects_a_branch(tmp_path):
    _, _, env, args, fresh = scenario(tmp_path / "A")
    a = fresh()
    a.bootstrap(**args)
    b = AnchoredSyncState(
        tmp_path / "B" / "history", encryption_key=bytes([97]) * 32, **a.open_context()
    )
    b.bootstrap(**args)
    left = packet(args, env[2][0], route="patch")
    right = packet(args, env[2][0], route="snapshot")
    a.ingest(*left)
    b.ingest(*right)
    ae, be = a.export_evidence(), b.export_evidence()
    for client, peer in ((a, be), (b, ae)):
        before = client.checkpoint()
        with pytest.raises(StateViewError, match="ATLAS_STATE_EQUIVOCATION"):
            client.compare_evidence(peer)
        assert client.checkpoint() == before
        assert client.recovery()["status"] == "RECOVERY_PENDING"
        assert client.evidence()["peer"] == peer
    assert a.checkpoint() != b.checkpoint()


@pytest.mark.parametrize("route", ["patch", "snapshot", "compaction"])
@pytest.mark.parametrize("replacement", ["edit", "create", "drop"])
def test_tombstone_is_terminal_in_anchored_suffix(tmp_path, route, replacement):
    _, _, env, args, fresh = scenario(tmp_path)
    client = fresh()
    client.bootstrap(**args)
    tombstone = copy.deepcopy(json.loads(base64.b64decode(P["two"]["opaque_b64"]))["records"][0])
    tombstone["key_epoch"] = 4
    deleted = packet(args, env[2][0], records=[tombstone])
    client.ingest(*deleted)
    before = client.checkpoint()
    stale = copy.deepcopy(tombstone)
    stale["tombstone"] = False
    if replacement == "edit":
        stale["revision"] = "stale-edit"
    records = [] if replacement == "drop" else [stale]
    with pytest.raises(StateViewError, match="ATLAS_TOMBSTONE_RESURRECTION"):
        fresh().ingest(*packet(args, env[2][0], previous=deleted[0], records=records, route=route))
    assert fresh().checkpoint() == before
    assert before["records"][0]["tombstone"]


def test_store_type_and_anchor_cannot_be_changed(tmp_path):
    _, _, _, args, fresh = scenario(tmp_path)
    client = fresh()
    client.bootstrap(**args)
    context = client.open_context()
    before = client._store.path.read_bytes()
    for name in ("anchor_root", "confirmed_transcript"):
        altered = copy.deepcopy(context)
        altered[name] = "ab" * 32
        with pytest.raises(StateViewError):
            AnchoredSyncState(
                client._store.path, encryption_key=bytes([96]) * 32, **altered
            ).checkpoint()
    origin = {
        k: v
        for k, v in context.items()
        if k
        not in ("anchor_root", "recipient_device_id", "confirmed_transcript", "current_context")
    }
    old_reader = GuardedSyncState(client._store.path, encryption_key=bytes([96]) * 32, **origin)
    with pytest.raises(StateViewError):
        old_reader.checkpoint()
    with pytest.raises(StateViewError):
        old_reader.initialize()
    with pytest.raises(BootstrapError):
        client.initialize()
    assert client._store.path.read_bytes() == before


def alarm_process(root, ready):
    _, _, env, args, fresh = scenario(Path(root))
    fresh().bootstrap(**args)
    try:
        fresh().ingest(*packet(args, env[2][0], previous_root="ab" * 32))
    except StateViewError:
        pass
    context = fresh().open_context()
    context["trusted_signer"] = base64.b64encode(context["trusted_signer"]).decode()
    Path(ready).write_text(json.dumps(context))
    while True:
        time.sleep(60)


def test_anchored_fork_survives_real_process_kill(tmp_path):
    # Deterministic test keys stay in memory; readiness contains no protected data.
    ready = tmp_path / "ready"
    worker = multiprocessing.get_context("spawn").Process(
        target=alarm_process, args=(str(tmp_path / "child"), str(ready))
    )
    worker.start()
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists()
        worker.kill()
        worker.join(10)
        assert worker.exitcode is not None
        context = json.loads(ready.read_text())
        context["trusted_signer"] = base64.b64decode(context["trusted_signer"])
        reopened = AnchoredSyncState(
            tmp_path / "child" / "recipient-history", encryption_key=bytes([96]) * 32, **context
        )
        assert reopened.recovery()["status"] == "RECOVERY_PENDING"
        assert reopened.checkpoint()["sequence"] == 2
        assert len(reopened.evidence()["peer"]) == 1
        with pytest.raises(StateViewError, match="ATLAS_RECOVERY_PENDING"):
            reopened.automatic_sync(lambda: pytest.fail("unfenced after SIGKILL"))
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(10)
