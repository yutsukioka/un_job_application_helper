"""C24 hostile HTTP delivery proof; temporary synthetic state, hash-only report."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import multiprocessing
import os
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import atlasvault_two_device_convergence as transport

ROOT = transport.ROOT
VECTOR = ROOT / "contracts/sync/test_vectors/atlasvault_sync_recovery_vectors_v1.json"
CLIENTS = {
    "python": ROOT / "packages/vaultsync/tests/support/malicious_server_client.py",
    "dart": ROOT
    / "apps/atlas_flutter/test/support/atlas_vault_malicious_server_client.dart",
    "swift": ROOT / "scripts/ci/support/AtlasVaultMaliciousServerClient.swift",
}
ZERO = "0" * 64
ACCOUNT = "ava1-" + hashlib.sha256(b"c20-two-device-account").hexdigest()
VAULT = "c24_hostile"
LIMITATIONS = [
    "First-contact freshness requires a trusted checkpoint.",
    "An isolated client cannot detect globally unseen withheld updates.",
    "Withholding all peer evidence can delay equivocation discovery.",
    "Local-filesystem rollback is outside this proof.",
    "Safe fork resolution may require P7 revocation and key rotation.",
]


def digest(value):
    return hashlib.sha256(transport._canonical(value)).hexdigest()


def packets():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from vaultsync.authenticated_state_view import (
        EMPTY_REGISTRY,
        registry_root,
        sign_state_view,
    )
    from vaultsync.device_identity import device_identity_from_private_keys
    from vaultsync.sync_queue import SignedStateCommitment

    identities = []
    for name in ("device_a", "device_b"):
        v = json.loads(transport.IDENTITY_VECTOR.read_bytes())[name]
        identities.append(
            device_identity_from_private_keys(
                signing_private_seed=base64.b64decode(v["signing_private_seed"]),
                agreement_private_key=base64.b64decode(v["agreement_private_key"]),
                created_at=v["descriptor"]["created_at"],
                key_epoch=2,
            )
        )
    device = identities[0]
    seed = json.loads(transport.IDENTITY_VECTOR.read_bytes())["device_a"][
        "signing_private_seed"
    ]
    signer = Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed))
    descriptor = device.sign_descriptor().to_dict()["descriptor"]
    registry = [
        {
            "device_id": hashlib.sha256(d.device_id.encode()).hexdigest(),
            "descriptor_sha256": digest(d.sign_descriptor().to_dict()["descriptor"]),
        }
        for d in identities
    ]
    templates = json.loads(VECTOR.read_bytes())["packets"]
    result = {}

    def make(name, template, parent, **changes):
        t = templates[template]
        body = json.loads(base64.b64decode(t["opaque_b64"]))
        for record in body["records"]:
            record["key_epoch"] = descriptor["key_epoch"]
        if name == "old_snapshot":
            body["route"] = "snapshot"
        raw = transport._canonical(body)
        previous = result[parent] if parent else None
        collection = SignedStateCommitment.sign(
            raw,
            collection_id="collection_c21",
            sequence=t["view"]["sequence"],
            previous_root=previous["collection"]["root"] if previous else ZERO,
            signing_key=device,
        ).to_dict()
        entries = changes.pop("registry", registry)
        view = {
            "format": "atlasvault-authenticated-state-view",
            "version": 2,
            "account_id": ACCOUNT,
            "vault_id": VAULT,
            "sequence": collection["sequence"],
            "previous_root": previous["view"]["root"] if previous else ZERO,
            "previous_registry_root": previous["view"]["registry_root"]
            if previous
            else EMPTY_REGISTRY,
            "collection_root": collection["root"],
            "registry_root": registry_root(entries),
            "key_epoch": descriptor["key_epoch"],
            **changes,
        }
        result[name] = {
            "view": sign_state_view(view, signer),
            "collection": collection,
            "registry": entries,
            "opaque_b64": transport._b64(raw),
        }

    make("one", "one", None)
    make("two", "two", "one")
    make("three", "three", "two")
    make("fork_two", "fork_two", "one")
    make("registry_fork", "two", "one", registry=registry[:1])
    make("old_snapshot", "one", None)
    for name in (
        "stale_create",
        "stale_edit",
        "pre_delete_compaction",
        "missing_tombstone",
    ):
        make(name, name, "two")
    make("wrong_predecessor", "three", "two", previous_root="f" * 64)
    make("wrong_account", "three", "two", account_id="other_account")
    make("wrong_vault", "three", "two", vault_id="other_vault")
    make("wrong_epoch", "three", "two", key_epoch=descriptor["key_epoch"] + 1)
    make("epoch_regression", "three", "two", key_epoch=1)
    make("unknown_envelope", "unknown_envelope", "two")
    for name, entries in (
        ("removed_device", registry[:1]),
        (
            "added_device",
            registry + [{"device_id": "a" * 64, "descriptor_sha256": "b" * 64}],
        ),
    ):
        result[name] = copy.deepcopy(result["three"])
        result[name]["registry"] = entries
    for name, p in result.items():
        p["operation"] = transport._transport_operation(
            {
                "operation_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "c24:" + name)),
                "packet": p,
            },
            vault_id="c24_" + name,
            index=p["view"]["sequence"],
            parent_revision=None
            if p["view"]["sequence"] == 1
            else f"transport-{p['view']['sequence'] - 1:03d}",
        )
        p["operation"]["author_device_id"] = "c24_transport"
    return result, signer, descriptor, identities


def scenarios():
    rejected = {
        "old_commitment": ("one", "ATLAS_ROLLBACK_REJECTED"),
        "old_snapshot": ("old_snapshot", "ATLAS_ROLLBACK_REJECTED"),
        "pre_delete_replay": ("one", "ATLAS_ROLLBACK_REJECTED"),
        "stale_create": ("stale_create", "ATLAS_TOMBSTONE_RESURRECTION"),
        "stale_edit": ("stale_edit", "ATLAS_TOMBSTONE_RESURRECTION"),
        "pre_delete_compaction": (
            "pre_delete_compaction",
            "ATLAS_TOMBSTONE_RESURRECTION",
        ),
        "omitted_record": ("missing_tombstone", "ATLAS_TOMBSTONE_RESURRECTION"),
        "omitted_known_update": ("one", "ATLAS_ROLLBACK_REJECTED"),
        "same_sequence": ("fork_two", "ATLAS_STATE_EQUIVOCATION"),
        "wrong_predecessor": ("wrong_predecessor", "ATLAS_STATE_VIEW_REJECTED"),
        "registry_rollback": ("removed_device", "ATLAS_REGISTRY_SUBSTITUTION"),
        "removed_device": ("removed_device", "ATLAS_REGISTRY_SUBSTITUTION"),
        "added_device": ("added_device", "ATLAS_REGISTRY_SUBSTITUTION"),
        "cross_account": ("wrong_account", "ATLAS_STATE_VIEW_REJECTED"),
        "cross_vault": ("wrong_vault", "ATLAS_STATE_VIEW_REJECTED"),
        "wrong_epoch": ("wrong_epoch", "ATLAS_STATE_VIEW_REJECTED"),
        "epoch_regression": ("epoch_regression", "ATLAS_STATE_VIEW_REJECTED"),
        "unknown_envelope": ("unknown_envelope", "ATLAS_STATE_VIEW_REJECTED"),
    }
    cases = {
        name: {"baseline": ["one", "two"], "attack": p, "expected": category}
        for name, (p, category) in rejected.items()
    }
    cases["exact_retry"] = {
        "baseline": ["one", "two"],
        "attack": "two",
        "expected": "IDEMPOTENT",
    }
    for name, right in (
        ("fork_exchange", "fork_two"),
        ("registry_exchange", "registry_fork"),
    ):
        cases[name] = {
            "baseline": ["one", "two"],
            "right": ["one", right],
            "peer": True,
            "expected": "ATLAS_STATE_EQUIVOCATION",
        }
    cases["withheld_peer"] = {
        "baseline": ["one", "two"],
        "right": ["one", "fork_two"],
        "expected": None,
    }
    cases["unseen_withholding"] = {"baseline": ["one"], "expected": None}
    return cases


def compile_swift(directory):
    binary = directory / "c24-swift"
    sources = [
        ROOT / "apps/apple/Sources/AtlasUI" / name
        for name in (
            "AtlasVaultSyncQueue.swift",
            "AtlasVaultAuthenticatedStateView.swift",
            "AtlasVaultSyncRecovery.swift",
            "AtlasVaultEpochRotation.swift",
            "AtlasVaultRevocation.swift",
            "AtlasVaultKeyEpochs.swift",
            "AtlasVaultHPKEKeyDelivery.swift",
            "AtlasVaultRecordCrypto.swift",
        )
    ]
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-parse-as-library",
            *map(str, sources),
            str(CLIENTS["swift"]),
            "-o",
            str(binary),
        ],
        check=True,
    )
    return binary


def client(language, binary, mode, root, plan, output):
    arguments = [mode, str(root), str(plan), str(output)]
    env = dict(
        os.environ, PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"
    )
    if language == "python":
        # Preserve R019: no site startup or editable-primary import path.
        paths = [
            str(ROOT / "packages/vaultsync"),
            *[p for p in sys.path if "site-packages" in p],
        ]
        loader = f"import sys,runpy;sys.path[:0]={paths!r};sys.argv=[{str(CLIENTS[language])!r},*sys.argv[1:]];runpy.run_path(sys.argv[0],run_name='__main__')"
        command = [sys.executable, "-S", "-c", loader, *arguments]
    elif language == "dart":
        command = ["dart", "run", str(CLIENTS[language]), *arguments]
    else:
        command = [str(binary), *arguments]
    with tempfile.TemporaryFile() as log:
        p = subprocess.Popen(
            command,
            cwd=ROOT / "apps/atlas_flutter",
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 120
            while (
                not output.exists() and p.poll() is None and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            if not output.exists():
                log.seek(0)
                raise RuntimeError(
                    f"{language} adapter failed: {log.read().decode(errors='replace')}"
                )
            result = json.loads(output.read_bytes())
            if mode != "inspect":
                os.killpg(p.pid, signal.SIGKILL)
                assert p.wait(timeout=10) == -signal.SIGKILL
            else:
                assert p.wait(timeout=10) == 0
            return result, p.pid
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=10)


def backend_proof(request, all_packets, signer):
    from vaultsync.authenticated_state_view import sign_state_view

    path = f"/v1/vaults/{VAULT}/commitments"
    one = all_packets["one"]["view"]
    assert request("POST", path, body=one)[0] == 200
    barrier = threading.Barrier(2)

    def race(view):
        barrier.wait()
        return request("POST", path, body=view)[0]

    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(race, all_packets[n]["view"]) for n in ("two", "fork_two")
        ]
        codes = [f.result() for f in futures]
    assert sorted(codes) == [200, 409], codes
    history = request("GET", path)[1]
    winner = history[-1]
    status, retry = request("POST", path, body=winner)
    assert status == 200 and retry["appended"] is False
    attacks = {}
    # Use the actual winning history for each attack so an unrelated predecessor
    # conflict cannot mask missing registry/epoch enforcement.
    valid_next = {
        k: v
        for k, v in all_packets["three"]["view"].items()
        if k not in ("root", "signature_b64")
    }
    valid_next.update(
        previous_root=winner["root"], previous_registry_root=winner["registry_root"]
    )
    changes = {
        "regression": {"sequence": 1},
        "same_sequence": {"sequence": 2},
        "wrong_predecessor": {"previous_root": "f" * 64},
        "registry_substitution": {"registry_root": "e" * 64},
        "registry_predecessor": {"previous_registry_root": "e" * 64},
        "wrong_epoch": {"key_epoch": 3},
        "epoch_regression": {"key_epoch": 1},
    }
    for name, change in changes.items():
        view = sign_state_view(dict(valid_next, **change), signer)
        status, _ = request("POST", path, body=view)
        assert status == 409, (name, status)
        assert request("GET", path)[1] == history
        attacks[name] = status
    altered_identifier = dict(winner, signature_b64=transport._b64(bytes(64)))
    assert request("POST", path, body=altered_identifier)[0] == 409
    assert request("GET", path)[1] == history
    attacks["duplicate_identifier_changed_content"] = 409
    return {
        "conflicting_child_statuses": codes,
        "exact_retry_status": 200,
        "exact_retry_appended": False,
        "atomic_rejections": attacks,
        "stored_roots": [v["root"] for v in history],
    }


def receipt_crashes(directory, binary, all_packets, descriptor):
    """Kill after each durable handoff and require an exact retry to finish receipts."""
    results = []
    for language in CLIENTS:
        for point in ("admission", "outbox", "inbox", "receipt"):
            folder = directory / f"receipt-{language}-{point}"
            folder.mkdir()
            plan = {
                "public_b64": descriptor["signing_public_key"],
                "context": {
                    "account_id": ACCOUNT,
                    "vault_id": VAULT,
                    "collection_id": "collection_c21",
                    "key_epoch": descriptor["key_epoch"],
                },
                "packets": all_packets,
                "scenarios": {
                    "receipt": {
                        "baseline": [{"packet": "one"}, {"packet": "two"}],
                        "attack": [{"packet": "three", "stop_after": point}],
                    }
                },
            }
            path = folder / "plan.json"
            path.write_bytes(transport._canonical(plan))
            state = folder / "device"
            client(language, binary, "prepare", state, path, folder / "prepared.json")
            marker, killed = client(
                language, binary, "attack", state, path, folder / "interrupted.json"
            )
            assert marker == {"interrupted_after": point}
            del plan["scenarios"]["receipt"]["attack"][0]["stop_after"]
            path.write_bytes(transport._canonical(plan))
            resumed, resumed_pid = client(
                language, binary, "attack", state, path, folder / "resumed.json"
            )
            reopened, reopened_pid = client(
                language, binary, "inspect", state, path, folder / "reopened.json"
            )
            r, s = resumed["receipt"], reopened["receipt"]
            passed = (
                r["categories"] == ["IDEMPOTENT"]
                and r["cursor"]
                == r["checkpoint"]["cursor"]
                == all_packets["three"]["view"]["root"]
                and r["pending_outbox"] == 0
                and r["recovery"]["status"] == "ACTIVE"
                and all(
                    r[k] == s[k]
                    for k in ("checkpoint", "cursor", "pending_outbox", "recovery")
                )
            )
            results.append(
                {
                    "language": language,
                    "killed_after": point,
                    "passed": passed,
                    "process_ids": [killed, resumed_pid, reopened_pid],
                    "final_state_sha256": s["state_sha256"],
                    "cursor": s["cursor"],
                    "accepted_root": s["checkpoint"]["cursor"],
                    "pending_outbox": s["pending_outbox"],
                }
            )
    return results


def proof(directory, binary, tls, tokens, all_packets, descriptor, signer):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    def request(method, path, **kwargs):
        return transport._request(method, path, context=tls, token=tokens[0], **kwargs)

    # All adversarial packages traverse the production ciphertext object API. The
    # malicious delivery controller selects archived signed bytes, never modifies a validator.
    for name, packet in all_packets.items():
        envelope = dict(packet["operation"]["envelope"], parent_revision=None)
        status, _ = request(
            "PUT",
            f"/v1/vaults/{VAULT}_{name}/objects/transport_log",
            body=envelope,
            headers={"If-Match": "*", "Idempotency-Key": name},
        )
        # Each hostile response gets its own opaque storage namespace.
        if status != 200:
            raise RuntimeError(f"C24 upload {name} failed: {status}")
    backend = backend_proof(request, all_packets, signer)
    pairings = []
    cases = scenarios()
    for label, left, right in transport.PAIRINGS:
        pair = directory / label
        pair.mkdir()
        process_ids = []
        plans, outputs, roots = [], [], []
        for index, language in enumerate((left, right)):
            downloaded = {}
            for name, original in all_packets.items():
                status, envelope = transport._request(
                    "GET",
                    f"/v1/vaults/{VAULT}_{name}/objects/transport_log",
                    context=tls,
                    token=tokens[index],
                )
                assert status == 200 and envelope == dict(
                    original["operation"]["envelope"], parent_revision=None
                )
                clear = AESGCM(transport._transport_key()).decrypt(
                    base64.b64decode(envelope["nonce_b64"]),
                    base64.b64decode(envelope["ciphertext_b64"]),
                    base64.b64decode(envelope["aad_b64"]),
                )
                packet = json.loads(clear)["packet"]
                packet["operation"] = original["operation"]
                assert packet == original
                downloaded[name] = packet
            plan = {
                "public_b64": descriptor["signing_public_key"],
                "context": {
                    "account_id": ACCOUNT,
                    "vault_id": VAULT,
                    "collection_id": "collection_c21",
                    "key_epoch": descriptor["key_epoch"],
                },
                "packets": downloaded,
                "scenarios": {},
            }
            for name, case in cases.items():
                baseline = (
                    case.get("right", case["baseline"]) if index else case["baseline"]
                )
                attack = (
                    [{"peer": str(pair / f"prepared-{1 - index}.json")}]
                    if case.get("peer")
                    else [{"packet": case["attack"]}]
                    if "attack" in case
                    else []
                )
                plan["scenarios"][name] = {
                    "baseline": [{"packet": p} for p in baseline],
                    "attack": attack,
                }
            path = pair / f"plan-{index}.json"
            path.write_bytes(transport._canonical(plan))
            plans.append(path)
            roots.append(pair / f"device-{index}")
            out = pair / f"prepared-{index}.json"
            prepared, pid = client(language, binary, "prepare", roots[-1], path, out)
            process_ids.append(pid)
            outputs.append(prepared)
        attacked, reopened = [], []
        for index, language in enumerate((left, right)):
            for mode, results in (("attack", attacked), ("inspect", reopened)):
                out = pair / f"{mode}-{index}.json"
                result, pid = client(
                    language, binary, mode, roots[index], plans[index], out
                )
                process_ids.append(pid)
                results.append(result)
        records = []
        for name, case in cases.items():
            devices = []
            for index, language in enumerate((left, right)):
                before, after, restart = (
                    outputs[index][name],
                    attacked[index][name],
                    reopened[index][name],
                )
                assert after["checkpoint"] == before["checkpoint"], (
                    label,
                    name,
                    "accepted state advanced",
                )
                assert (
                    after["cursor"] == before["cursor"] and after["pending_outbox"] == 0
                )
                assert all(
                    after[k] == restart[k]
                    for k in (
                        "checkpoint",
                        "recovery",
                        "evidence",
                        "state_sha256",
                        "recovery_sha256",
                        "automatic_sync_fenced",
                        "cursor",
                    )
                ), (label, name, "restart loss")
                expected = case["expected"]
                assert after["categories"] == ([expected] if expected else []), (
                    label,
                    name,
                    after["categories"],
                    expected,
                )
                blocked = bool(expected and expected.startswith("ATLAS_"))
                assert after["automatic_sync_fenced"] is blocked
                assert after["recovery"]["status"] == (
                    "MANUAL_REQUIRED" if blocked else "ACTIVE"
                )
                if case.get("peer"):
                    assert after["evidence"]["local"] == before["history"]
                    assert (
                        after["evidence"]["peer"] == outputs[1 - index][name]["history"]
                    )
                text = json.dumps(after["recovery"])
                assert len(text) < 160000
                assert not any(
                    word in text
                    for word in (
                        "ciphertext_b64",
                        "opaque_b64",
                        "vault_key",
                        "passphrase",
                        "access_token",
                    )
                )
                if len(case["baseline"]) == 2 and "right" not in case:
                    assert all(r["tombstone"] for r in after["checkpoint"]["records"])
                malicious = (
                    all_packets[case["attack"]]["view"]
                    if "attack" in case
                    else outputs[1 - index][name]["history"][-1]
                )
                devices.append(
                    {
                        "language": language,
                        "accepted_sequence_before": before["checkpoint"]["sequence"],
                        "accepted_root_before": before["checkpoint"]["cursor"],
                        "served_sequence": malicious["sequence"],
                        "served_root": malicious["root"],
                        "served_registry_root": malicious["registry_root"],
                        "rejection": expected,
                        "recovery_status": after["recovery"]["status"],
                        "final_state_sha256": after["state_sha256"],
                        "recovery_sha256": after["recovery_sha256"],
                        "both_branch_evidence_sha256": digest(after["evidence"]),
                        "automatic_sync_fenced": after["automatic_sync_fenced"],
                        "restart_preserved": True,
                    }
                )
            matching = (
                devices[0]["final_state_sha256"] == devices[1]["final_state_sha256"]
            )
            assert matching is ("right" not in case), (
                label,
                name,
                "unexpected state equality",
            )
            records.append(
                {
                    "attack": name,
                    "devices": devices,
                    "matching_accepted_state": matching,
                    "detection_trigger": "authenticated peer history exchange"
                    if case.get("peer")
                    else "served authenticated response"
                    if case.get("attack")
                    else "not detectable without new evidence",
                }
            )
        state_files = [p for root in roots for p in root.rglob("*.state")]
        assert len(state_files) == len(cases) * 2 * 3
        pairings.append(
            {
                "pair": label,
                "process_ids": process_ids,
                "separate_encrypted_state_files": len(state_files),
                "attacks": records,
            }
        )
    receipts = receipt_crashes(directory, binary, all_packets, descriptor)
    assert all(r["passed"] for r in receipts), receipts
    return {
        "format": "atlasvault-c24-malicious-server-evidence",
        "version": 1,
        "topology": {
            "uvicorn_instances": 1,
            "tls_proxy": 1,
            "endpoint": transport.PUBLIC_URL,
            "http_ciphertext_roundtrips": len(all_packets) * 6,
            "separate_processes": True,
        },
        "backend": backend,
        "receipt_crashes": receipts,
        "pairings": pairings,
        "limitations": LIMITATIONS,
        "protected_artifacts_retained": False,
        "temporary_state_and_tls_removed": True,
    }


def run(output):
    transport._assert_ports_free()
    all_packets, signer, descriptor, identities = packets()
    app, tokens = configured_app(identities)
    context = multiprocessing.get_context("fork")
    with tempfile.TemporaryDirectory(prefix="atlasvault-c24-") as temporary:
        directory = Path(temporary)
        cert, key = transport._generate_certificate(directory)
        binary = compile_swift(directory)
        server = context.Process(target=transport._serve, args=(app,))
        proxy = context.Process(target=transport._tls_proxy, args=(str(cert), str(key)))
        server.start()
        try:
            transport._wait_port(transport.UPSTREAM_PORT, server)
            proxy.start()
            transport._wait_port(transport.TLS_PORT, proxy)
            report = proof(
                directory,
                binary,
                ssl.create_default_context(cafile=str(cert)),
                tokens,
                all_packets,
                descriptor,
                signer,
            )
        finally:
            for process in (proxy, server):
                if process.pid is not None:
                    process.terminate()
                    process.join(timeout=10)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
    transport._assert_ports_free()
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def configured_app(identities):
    from atlasvault_api.controls import AbuseControlPolicy

    TestClient, session_domain, registry_domain, Backend, create_app, _ = (
        transport._imports()
    )
    backend = Backend(
        abuse_policy=AbuseControlPolicy(
            account_request_limit=512, device_request_limit=256
        )
    )
    app = create_app(backend)
    tokens = []
    previous = None
    with TestClient(app) as http:
        for index, device in enumerate(identities):
            revision = str(uuid.uuid5(uuid.NAMESPACE_URL, f"c24-registry-{index}"))
            path = f"/v1/accounts/{ACCOUNT}/devices" + (
                "/bootstrap" if not index else ""
            )
            headers = (
                {
                    "X-AtlasVault-Bootstrap-Admission": backend.issue_bootstrap_admission(
                        ACCOUNT
                    )
                }
                if not index
                else {"Authorization": f"Bearer {tokens[0]}"}
            )
            response = http.post(
                path,
                headers=headers,
                json=transport._signed_transition(
                    account_id=ACCOUNT,
                    revision=revision,
                    parent_revision=previous,
                    device=device,
                    signer=identities[0],
                    domain=registry_domain,
                ),
            )
            assert response.status_code == (201 if not index else 200)
            tokens.append(transport._session(http, ACCOUNT, device, session_domain))
            previous = revision
    return app, tokens


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
