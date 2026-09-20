#!/usr/bin/env python3
"""Run the C20 two-device proof through one D065 local TLS backend."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import multiprocessing
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VECTOR = (
    ROOT
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_two_device_convergence_vectors_v1.json"
)
PYTHON_CLIENT = (
    ROOT
    / "packages"
    / "vaultsync"
    / "tests"
    / "support"
    / "two_device_convergence_client.py"
)
DART_CLIENT = (
    ROOT
    / "apps"
    / "atlas_flutter"
    / "test"
    / "support"
    / "atlas_vault_two_device_convergence_client.dart"
)
SWIFT_CLIENT = (
    ROOT / "scripts" / "ci" / "support" / "AtlasVaultTwoDeviceConvergenceClient.swift"
)
SWIFT_QUEUE = ROOT / "apps" / "apple" / "Sources" / "AtlasUI" / "AtlasVaultSyncQueue.swift"
IDENTITY_VECTOR = (
    ROOT
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_device_identity_pairing_vectors_v1.json"
)
UPSTREAM_PORT = 18080
TLS_PORT = 8443
PUBLIC_URL = f"https://127.0.0.1:{TLS_PORT}"
CLIENT_STATE_CONTRACT = ("DurableEncryptedOutbox", "DurableEncryptedInbox")
PAIRINGS = (
    ("python-dart", "python", "dart"),
    ("dart-swift", "dart", "swift"),
    ("swift-python", "swift", "python"),
)
UPLOAD_ORDERS = (
    ("create_a", "create_b", "edit_a", "edit_b", "delete_a", "late_edit_b"),
    ("create_b", "create_a", "edit_b", "edit_a", "late_edit_b", "delete_a"),
    ("create_a", "create_b", "edit_b", "edit_a", "delete_a", "late_edit_b"),
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _transport_key() -> bytes:
    return hashlib.sha256(b"atlasvault-c20-synthetic-transport").digest()


def _imports() -> tuple[Any, ...]:
    sys.path.insert(0, str(ROOT / "packages" / "vaultsync"))
    sys.path.insert(0, str(ROOT / "services" / "atlasvault-api"))
    from fastapi.testclient import TestClient
    from atlasvault_api.app import (
        ACCOUNT_SESSION_PROOF_DOMAIN,
        DEVICE_REGISTRY_TRANSITION_DOMAIN,
        AtlasVaultBackend,
        create_app,
    )
    from vaultsync.device_identity import device_identity_from_private_keys

    return (
        TestClient,
        ACCOUNT_SESSION_PROOF_DOMAIN,
        DEVICE_REGISTRY_TRANSITION_DOMAIN,
        AtlasVaultBackend,
        create_app,
        device_identity_from_private_keys,
    )


def _signed_transition(
    *,
    account_id: str,
    revision: str,
    parent_revision: str | None,
    device: Any,
    signer: Any,
    domain: bytes,
) -> dict[str, Any]:
    transition = {
        "format": "atlasvault-device-registry-transition",
        "version": 1,
        "account_id": account_id,
        "revision": revision,
        "parent_revision": parent_revision,
        "operation": "add",
        "device": device.sign_descriptor().to_dict(),
        "signer_device_id": signer.device_id,
    }
    return {
        "format": "atlasvault-signed-device-registry-transition",
        "version": 1,
        "transition": transition,
        "signature": _b64(signer.sign(domain + _canonical(transition))),
    }


def _session(client: Any, account_id: str, identity: Any, domain: bytes) -> str:
    challenge_response = client.post(
        f"/v1/accounts/{account_id}/auth/challenges",
        json={"device_id": identity.device_id},
    )
    if challenge_response.status_code != 201:
        raise RuntimeError("account challenge failed")
    challenge = challenge_response.json()
    proof_payload = {
        "format": "atlasvault-account-session-proof",
        "version": 1,
        "account_id": account_id,
        "device_id": identity.device_id,
        "challenge_id": challenge["challenge_id"],
        "challenge": challenge["challenge"],
    }
    response = client.post(
        f"/v1/accounts/{account_id}/sessions",
        json={
            "device_id": identity.device_id,
            "challenge_id": challenge["challenge_id"],
            "signature": _b64(identity.sign(domain + _canonical(proof_payload))),
        },
    )
    if response.status_code != 201:
        raise RuntimeError("account session failed")
    return response.json()["access_token"]


def _configured_app() -> tuple[Any, tuple[str, str]]:
    (
        TestClient,
        session_domain,
        registry_domain,
        AtlasVaultBackend,
        create_app,
        identity_from_keys,
    ) = _imports()
    from atlasvault_api.controls import AbuseControlPolicy

    vectors = json.loads(IDENTITY_VECTOR.read_text(encoding="utf-8"))
    identities = []
    for name in ("device_a", "device_b"):
        value = vectors[name]
        identities.append(
            identity_from_keys(
                signing_private_seed=base64.b64decode(value["signing_private_seed"]),
                agreement_private_key=base64.b64decode(value["agreement_private_key"]),
                created_at=value["descriptor"]["created_at"],
                key_epoch=value["descriptor"]["key_epoch"],
            )
        )
    device_a, device_b = identities
    account_id = f"ava1-{hashlib.sha256(b'c20-two-device-account').hexdigest()}"
    revision_a = "20000000-0000-4000-8000-000000000101"
    revision_b = "20000000-0000-4000-8000-000000000102"
    backend = AtlasVaultBackend(
        abuse_policy=AbuseControlPolicy(
            account_request_limit=512,
            device_request_limit=256,
        )
    )
    app = create_app(backend)
    with TestClient(app) as client:
        admission = backend.issue_bootstrap_admission(account_id)
        response = client.post(
            f"/v1/accounts/{account_id}/devices/bootstrap",
            headers={"X-AtlasVault-Bootstrap-Admission": admission},
            json=_signed_transition(
                account_id=account_id,
                revision=revision_a,
                parent_revision=None,
                device=device_a,
                signer=device_a,
                domain=registry_domain,
            ),
        )
        if response.status_code != 201:
            raise RuntimeError("account bootstrap failed")
        token_a = _session(client, account_id, device_a, session_domain)
        response = client.post(
            f"/v1/accounts/{account_id}/devices",
            headers={"Authorization": f"Bearer {token_a}"},
            json=_signed_transition(
                account_id=account_id,
                revision=revision_b,
                parent_revision=revision_a,
                device=device_b,
                signer=device_a,
                domain=registry_domain,
            ),
        )
        if response.status_code != 200:
            raise RuntimeError("second device registration failed")
        token_b = _session(client, account_id, device_b, session_domain)
    return app, (token_a, token_b)


def _serve(app: Any) -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=UPSTREAM_PORT,
        workers=1,
        reload=False,
        access_log=False,
        log_level="warning",
    )


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65_536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def _proxy_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    upstream_reader, upstream_writer = await asyncio.open_connection(
        "127.0.0.1", UPSTREAM_PORT
    )
    await asyncio.gather(
        _pump(reader, upstream_writer),
        _pump(upstream_reader, writer),
        return_exceptions=True,
    )


def _tls_proxy(cert: str, key: str) -> None:
    # TLS terminates in this one loopback-only proxy process.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)

    async def run() -> None:
        server = await asyncio.start_server(
            _proxy_connection,
            "127.0.0.1",
            TLS_PORT,
            ssl=context,
        )
        async with server:
            await server.serve_forever()

    asyncio.run(run())


def _wait_port(port: int, process: multiprocessing.Process) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not process.is_alive():
            raise RuntimeError(f"process exited before port {port} became ready")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"port {port} did not become ready")


def _generate_certificate(directory: Path) -> tuple[Path, Path]:
    cert = directory / "loopback-cert.pem"
    key = directory / "loopback-key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return cert, key


def _request(
    method: str,
    path: str,
    *,
    context: ssl.SSLContext,
    token: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    request_headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "atlasvault-c20-two-device-proof/1",
        **(headers or {}),
    }
    data = None if body is None else _canonical(body)
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{PUBLIC_URL}{path}",
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _transport_operation(
    operation: dict[str, Any],
    *,
    vault_id: str,
    index: int,
    parent_revision: str | None,
) -> dict[str, Any]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aad = f"atlasvault-c20-transport:{vault_id}".encode("ascii")
    nonce = hashlib.sha256(
        f"{vault_id}:{operation['operation_id']}".encode("ascii")
    ).digest()[:12]
    encrypted = AESGCM(_transport_key()).encrypt(nonce, _canonical(operation), aad)
    revision = f"transport-{index:03d}"
    envelope = {
        "format": "atlasvault-opaque-ciphertext-envelope",
        "version": 1,
        "object_id": "transport_log",
        "revision": revision,
        "parent_revision": parent_revision,
        "key_epoch": 1,
        "nonce_b64": _b64(nonce),
        "ciphertext_b64": _b64(encrypted),
        "aad_b64": _b64(aad),
        "signature_b64": _b64(
            hmac.new(_transport_key(), encrypted, hashlib.sha512).digest()
        ),
        "tombstone": False,
        "content_sha256": hashlib.sha256(encrypted).hexdigest(),
    }
    return {
        "format": "atlasvault-encrypted-patch-operation",
        "version": 1,
        "operation_id": str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"atlasvault-c20:{vault_id}:{revision}")
        ),
        "operation_type": "upsert",
        "author_device_id": f"transport_{vault_id}",
        "author_sequence": index,
        "lamport": index,
        "envelope": envelope,
    }


def _upload(
    vault_id: str,
    order: tuple[str, ...],
    operations: dict[str, tuple[dict[str, Any], str]],
    *,
    context: ssl.SSLContext,
) -> tuple[list[dict[str, Any]], int]:
    transport: list[dict[str, Any]] = []
    parent: str | None = None
    retries = 0
    for index, name in enumerate(order, 1):
        operation, token = operations[name]
        wrapped = _transport_operation(
            operation,
            vault_id=vault_id,
            index=index,
            parent_revision=parent,
        )
        headers = {
            "If-Match": "*" if parent is None else f'"{parent}"',
            "Idempotency-Key": operation["operation_id"],
        }
        for _ in range(2):
            status, response = _request(
                "POST",
                f"/v1/vaults/{vault_id}/patches",
                context=context,
                token=token,
                body=wrapped["envelope"],
                headers=headers,
            )
            if status != 201 or response != wrapped["envelope"]:
                raise RuntimeError(f"opaque patch upload failed with {status}")
            retries += 1
        transport.append(wrapped)
        parent = wrapped["envelope"]["revision"]
    return transport, retries


def _download_pages(
    vault_id: str,
    transport: list[dict[str, Any]],
    *,
    context: ssl.SSLContext,
    token: str,
) -> list[dict[str, Any]]:
    pages = []
    expected_cursor: str | None = None
    offset = 0
    while True:
        query = "page_size=2" if expected_cursor is None else urllib.parse.urlencode(
            {"cursor": expected_cursor}
        )
        status, response = _request(
            "GET",
            f"/v1/vaults/{vault_id}/patches?{query}",
            context=context,
            token=token,
        )
        if status != 200:
            raise RuntimeError(f"opaque patch page failed with {status}")
        count = len(response["objects"])
        selected = transport[offset : offset + count]
        if [item["envelope"] for item in selected] != response["objects"]:
            raise RuntimeError("backend changed opaque transport bytes")
        pages.append(
            {
                "expected_cursor": expected_cursor,
                "next_cursor": response["next_cursor"],
                "transport_operations": selected,
            }
        )
        offset += count
        expected_cursor = response["next_cursor"]
        if expected_cursor is None:
            break
    if offset != len(transport):
        raise RuntimeError("backend cursor omitted transport operations")
    return pages


def _compile_swift(directory: Path) -> Path:
    binary = directory / "atlasvault-c20-swift-client"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-parse-as-library",
            str(SWIFT_QUEUE),
            str(SWIFT_CLIENT),
            "-o",
            str(binary),
        ],
        check=True,
        cwd=ROOT,
    )
    return binary


def _client_command(
    language: str,
    swift_binary: Path,
    arguments: list[str],
) -> tuple[list[str], Path, dict[str, str]]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(ROOT / "packages" / "vaultsync")
    if language == "python":
        return [sys.executable, str(PYTHON_CLIENT), *arguments], ROOT, environment
    if language == "dart":
        dart = shutil.which("dart")
        if dart is None:
            raise RuntimeError("Dart executable is unavailable")
        return (
            [dart, "run", "test/support/atlas_vault_two_device_convergence_client.dart", *arguments],
            ROOT / "apps" / "atlas_flutter",
            environment,
        )
    if language == "swift":
        return [str(swift_binary), *arguments], ROOT, environment
    raise RuntimeError("unsupported C20 language")


def _run_clients(
    specs: list[tuple[str, list[str]]],
    *,
    swift_binary: Path,
) -> list[dict[str, Any]]:
    running = []
    for language, arguments in specs:
        command, cwd, environment = _client_command(language, swift_binary, arguments)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        running.append((language, process))
    results = []
    for language, process in running:
        stdout, stderr = process.communicate(timeout=120)
        if process.returncode != 0:
            raise RuntimeError(
                f"{language} C20 client failed ({process.returncode}): {stderr or stdout}"
            )
        results.append({"language": language, "pid": process.pid})
    return results


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_pair(
    root: Path,
    label: str,
    language_a: str,
    language_b: str,
    upload_order: tuple[str, ...],
    *,
    swift_binary: Path,
    context: ssl.SSLContext,
    tokens: tuple[str, str],
    expected: dict[str, Any],
) -> dict[str, Any]:
    pair_root = root / label
    state_a = pair_root / "device-a"
    state_b = pair_root / "device-b"
    prepare_a = pair_root / "prepare-a.json"
    prepare_b = pair_root / "prepare-b.json"
    pair_root.mkdir(parents=True)
    process_evidence = _run_clients(
        [
            (
                language_a,
                [
                    "prepare",
                    str(state_a),
                    str(VECTOR),
                    ",".join(expected["device_a_operations"]),
                    str(prepare_a),
                ],
            ),
            (
                language_b,
                [
                    "prepare",
                    str(state_b),
                    str(VECTOR),
                    ",".join(expected["device_b_operations"]),
                    str(prepare_b),
                ],
            ),
        ],
        swift_binary=swift_binary,
    )
    output_a = _read(prepare_a)
    output_b = _read(prepare_b)
    by_name: dict[str, tuple[dict[str, Any], str]] = {}
    operation_names = {
        value["operation_id"]: name for name, value in expected["operations"].items()
    }
    for operation in output_a["operations"]:
        by_name[operation_names[operation["operation_id"]]] = (operation, tokens[0])
    for operation in output_b["operations"]:
        by_name[operation_names[operation["operation_id"]]] = (operation, tokens[1])
    vault_id = f"c20_{label.replace('-', '_')}"
    transport, retry_count = _upload(
        vault_id,
        upload_order,
        by_name,
        context=context,
    )
    pages_a = _download_pages(
        vault_id, transport, context=context, token=tokens[0]
    )
    pages_b = _download_pages(
        vault_id, transport, context=context, token=tokens[1]
    )
    accepted_a = [item["operation_id"] for item in output_a["operations"]]
    accepted_b = [item["operation_id"] for item in output_b["operations"]]
    last_a = prepare_a
    last_b = prepare_b
    for page_index, (page_a, page_b) in enumerate(zip(pages_a, pages_b, strict=True), 1):
        input_a = pair_root / f"page-{page_index}-a.json"
        input_b = pair_root / f"page-{page_index}-b.json"
        result_a = pair_root / f"result-{page_index}-a.json"
        result_b = pair_root / f"result-{page_index}-b.json"
        page_a["accepted_operation_ids"] = accepted_a
        page_b["accepted_operation_ids"] = accepted_b
        input_a.write_bytes(_canonical(page_a))
        input_b.write_bytes(_canonical(page_b))
        process_evidence.extend(
            _run_clients(
                [
                    (language_a, ["apply", str(state_a), str(input_a), str(result_a)]),
                    (language_b, ["apply", str(state_b), str(input_b), str(result_b)]),
                ],
                swift_binary=swift_binary,
            )
        )
        last_a, last_b = result_a, result_b
    duplicate_a = pair_root / "duplicate-a.json"
    duplicate_b = pair_root / "duplicate-b.json"
    duplicate_result_a = pair_root / "duplicate-result-a.json"
    duplicate_result_b = pair_root / "duplicate-result-b.json"
    replay = {
        "expected_cursor": _read(last_a)["cursor"],
        "next_cursor": _read(last_a)["cursor"],
        "transport_operations": [transport[0]],
        "accepted_operation_ids": accepted_a,
    }
    duplicate_a.write_bytes(_canonical(replay))
    replay_b = dict(replay)
    replay_b["expected_cursor"] = _read(last_b)["cursor"]
    replay_b["next_cursor"] = _read(last_b)["cursor"]
    replay_b["accepted_operation_ids"] = accepted_b
    duplicate_b.write_bytes(_canonical(replay_b))
    process_evidence.extend(
        _run_clients(
            [
                (
                    language_a,
                    ["apply", str(state_a), str(duplicate_a), str(duplicate_result_a)],
                ),
                (
                    language_b,
                    ["apply", str(state_b), str(duplicate_b), str(duplicate_result_b)],
                ),
            ],
            swift_binary=swift_binary,
        )
    )
    final_a = _read(duplicate_result_a)
    final_b = _read(duplicate_result_b)
    if final_a["state_sha256"] != final_b["state_sha256"]:
        raise RuntimeError(f"{label} device state hashes diverged")
    for final in (final_a, final_b):
        if (
            final["accepted_operation_count"] != expected["expected_operation_count"]
            or final["pending_replica_count"] != 0
            or final["pending_outbox_count"] != 0
            or len(final["records"]) != 1
            or final["records"][0]["revision"] != expected["expected_final_revision"]
            or final["records"][0]["tombstone"] is not True
        ):
            raise RuntimeError(f"{label} did not reach terminal convergent state")
    state_files = sorted((*state_a.glob("*.state"), *state_b.glob("*.state")))
    for state_file in state_files:
        raw = state_file.read_bytes()
        for value in expected["operations"].values():
            if value["operation_id"].encode("ascii") in raw:
                raise RuntimeError("encrypted client state exposed operation metadata")
    return {
        "pair": label,
        "languages": [language_a, language_b],
        "upload_order": list(upload_order),
        "device_a_state_sha256": final_a["state_sha256"],
        "device_b_state_sha256": final_b["state_sha256"],
        "matching_state_hashes": True,
        "terminal_tombstone": True,
        "accepted_operation_count": final_a["accepted_operation_count"],
        "idempotent_upload_attempts": retry_count,
        "backend_pages_per_device": len(pages_a),
        "duplicate_replay_noop": True,
        "separate_state_files": len(state_files),
        "processes": process_evidence,
    }


def _assert_ports_free() -> None:
    for port in (UPSTREAM_PORT, TLS_PORT):
        with socket.socket() as candidate:
            if candidate.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"required loopback port {port} is already in use")


def run(output: Path) -> None:
    if sys.platform != "darwin":
        raise RuntimeError("C20 cross-language proof requires the Apple/Swift host")
    _assert_ports_free()
    expected = json.loads(VECTOR.read_text(encoding="utf-8"))
    app, tokens = _configured_app()
    context = multiprocessing.get_context("fork")
    with tempfile.TemporaryDirectory(prefix="atlasvault-c20-") as temporary:
        temporary_root = Path(temporary)
        cert, key = _generate_certificate(temporary_root)
        swift_binary = _compile_swift(temporary_root)
        server = context.Process(target=_serve, args=(app,), name="c20-uvicorn")
        proxy = context.Process(
            target=_tls_proxy,
            args=(str(cert), str(key)),
            name="c20-tls-proxy",
        )
        server.start()
        try:
            _wait_port(UPSTREAM_PORT, server)
            proxy.start()
            _wait_port(TLS_PORT, proxy)
            tls_context = ssl.create_default_context(cafile=str(cert))
            results = []
            for pairing, order in zip(PAIRINGS, UPLOAD_ORDERS, strict=True):
                label, language_a, language_b = pairing
                results.append(
                    _run_pair(
                        temporary_root,
                        label,
                        language_a,
                        language_b,
                        order,
                        swift_binary=swift_binary,
                        context=tls_context,
                        tokens=tokens,
                        expected=expected,
                    )
                )
            hashes = {item["device_a_state_sha256"] for item in results}
            if len(hashes) != 1:
                raise RuntimeError("cross-language final-state hashes differ")
            safe_report = {
                "format": "atlasvault-c20-two-device-convergence-evidence",
                "version": 1,
                "topology": {
                    "public_endpoint": PUBLIC_URL,
                    "uvicorn_processes": 1,
                    "uvicorn_workers": 1,
                    "tls_proxy_processes": 1,
                    "cloud_resources": 0,
                    "credentials_queried": False,
                },
                "pairings": results,
                "all_pair_state_sha256": next(iter(hashes)),
                "data_plane_findings": [],
                "record_plaintext_transmitted": False,
                "raw_or_wrapped_vault_key_transmitted": False,
                "r024_single_instance": True,
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(safe_report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        finally:
            if proxy.pid is not None:
                proxy.terminate()
                proxy.join(timeout=10)
                if proxy.is_alive():
                    proxy.kill()
                    proxy.join(timeout=5)
            server.terminate()
            server.join(timeout=10)
            if server.is_alive():
                server.kill()
                server.join(timeout=5)
    _assert_ports_free()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.output)


if __name__ == "__main__":
    main()
