from __future__ import annotations

import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from vaultsync.hpke_key_delivery import (
    HPKEKeyDeliveryError,
    _seal_vault_key_hpke_v2_for_testing,
    open_vault_key_hpke_v2,
    seal_vault_key_hpke_v2,
)


VECTOR_PATH = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_hpke_key_delivery_vectors_v2.json"
)


def _vector() -> dict[str, str]:
    root = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    assert root["format"] == "atlasvault-hpke-key-delivery-vectors"
    assert root["version"] == 2
    return root["single_shot"]


def _bytes(vector: dict[str, str], field: str) -> bytes:
    return bytes.fromhex(vector[field])


def _seal(_: int):
    vector = _vector()
    return seal_vault_key_hpke_v2(
        recipient_public_key=_bytes(vector, "recipient_public_key_hex"),
        vault_key=_bytes(vector, "vault_key_hex"),
        context=_bytes(vector, "context_hex"),
    )


def test_hpke_v2_matches_cross_language_single_shot_vector() -> None:
    vector = _vector()
    sealed = _seal_vault_key_hpke_v2_for_testing(
        recipient_public_key=_bytes(vector, "recipient_public_key_hex"),
        vault_key=_bytes(vector, "vault_key_hex"),
        context=_bytes(vector, "context_hex"),
        ephemeral_private_key=_bytes(vector, "sender_ephemeral_private_key_hex"),
    )

    assert sealed.encapsulated_key == _bytes(vector, "encapsulated_key_hex")
    assert sealed.ciphertext == _bytes(vector, "ciphertext_hex")
    assert open_vault_key_hpke_v2(
        recipient_private_key=_bytes(vector, "recipient_private_key_hex"),
        sealed=sealed,
        context=_bytes(vector, "context_hex"),
    ) == _bytes(vector, "vault_key_hex")


def test_hpke_v2_production_api_owns_all_sealing_entropy() -> None:
    parameters = inspect.signature(seal_vault_key_hpke_v2).parameters
    assert "ephemeral_private_key" not in parameters
    assert "nonce" not in parameters


def test_hpke_v2_revision_stress_and_crash_retry_are_unique() -> None:
    attempts = [_seal(index) for index in range(96)]
    assert len({value.encapsulated_key for value in attempts}) == 96
    assert len({value.ciphertext for value in attempts}) == 96

    abandoned = _seal(0)
    recovered = _seal(0)
    assert abandoned.encapsulated_key != recovered.encapsulated_key
    assert abandoned.ciphertext != recovered.ciphertext


def test_hpke_v2_concurrent_attempts_are_unique_and_open() -> None:
    vector = _vector()
    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(_seal, range(48)))

    assert len({value.encapsulated_key for value in attempts}) == 48
    assert len({value.ciphertext for value in attempts}) == 48
    for value in attempts:
        assert open_vault_key_hpke_v2(
            recipient_private_key=_bytes(vector, "recipient_private_key_hex"),
            sealed=value,
            context=_bytes(vector, "context_hex"),
        ) == _bytes(vector, "vault_key_hex")


def test_hpke_v2_fails_closed_for_wrong_context_and_tamper() -> None:
    vector = _vector()
    sealed = _seal(0)

    with pytest.raises(HPKEKeyDeliveryError):
        open_vault_key_hpke_v2(
            recipient_private_key=_bytes(vector, "recipient_private_key_hex"),
            sealed=sealed,
            context=b"wrong-context",
        )

    tampered = sealed.with_ciphertext(
        sealed.ciphertext[:-1] + bytes([sealed.ciphertext[-1] ^ 1])
    )
    with pytest.raises(HPKEKeyDeliveryError):
        open_vault_key_hpke_v2(
            recipient_private_key=_bytes(vector, "recipient_private_key_hex"),
            sealed=tampered,
            context=_bytes(vector, "context_hex"),
        )
