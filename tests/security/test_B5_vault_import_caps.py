from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "vaultsync"))

from vaultsync import (  # noqa: E402
    read_atlasvault_export,
    read_local_store,
    write_atlasvault_export,
    write_local_store,
)
from vaultsync.export import VaultImportTooLargeError  # noqa: E402
from vaultsync.format import (  # noqa: E402
    MAX_ARGON2ID_ITERATIONS,
    MAX_ARGON2ID_MEMORY_KIB,
    MAX_ARGON2ID_PARALLELISM,
    UnsafeKDFParameters,
    deserialize_vault_metadata,
    read_vault_import_bytes,
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _metadata_with_kdf(**overrides: int) -> dict[str, object]:
    kdf = {
        "algorithm": "Argon2id",
        "salt": _b64(b"s" * 16),
        "memory_kib": 1024,
        "iterations": 2,
        "parallelism": 1,
    }
    kdf.update(overrides)
    return {
        "format": "atlas-vault",
        "version": 1,
        "vault_id": "b5000000-0000-4000-8000-000000000001",
        "crypto": {
            "record_aead": "AES-256-GCM",
            "kdf": "Argon2id",
            "subkey_kdf": "HKDF-SHA256",
            "key_wrap_aead": "AES-256-GCM",
        },
        "key_wraps": [
            {
                "id": "primary-passphrase",
                "type": "passphrase",
                "kdf": kdf,
                "nonce": _b64(b"n" * 12),
                "ciphertext": _b64(b"c" * 32),
            }
        ],
    }


@pytest.mark.parametrize(
    "override",
    [
        {"memory_kib": MAX_ARGON2ID_MEMORY_KIB + 1},
        {"iterations": MAX_ARGON2ID_ITERATIONS + 1},
        {"parallelism": MAX_ARGON2ID_PARALLELISM + 1},
    ],
)
def test_B5_vault_metadata_rejects_extreme_argon2id_import_params(
    override: dict[str, int],
) -> None:
    with pytest.raises(UnsafeKDFParameters):
        deserialize_vault_metadata(_metadata_with_kdf(**override))


def test_B5_vault_export_import_rejects_oversized_file_before_reading(
    tmp_path: Path,
) -> None:
    path = tmp_path / "oversized.atlasvault"
    path.write_bytes(b"{}")

    with pytest.raises(VaultImportTooLargeError):
        read_atlasvault_export(path, max_bytes=1)


def test_B5_local_store_import_rejects_oversized_file_before_reading(tmp_path: Path) -> None:
    path = tmp_path / "oversized-local-store.json"
    path.write_text(json.dumps({"format": "atlasvault-local-store"}), encoding="utf-8")

    with pytest.raises(VaultImportTooLargeError):
        read_local_store(path, max_bytes=1)


def test_B5_vault_import_bounds_the_bytes_read_from_one_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "growing.atlasvault"
    path.write_bytes(b"x")
    read_sizes: list[int] = []

    class GrowingReader:
        def __enter__(self) -> GrowingReader:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return b"xx"

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: GrowingReader())

    with pytest.raises(VaultImportTooLargeError):
        read_vault_import_bytes(path, max_bytes=1)

    assert read_sizes == [2]


def test_B5_vault_writers_reject_files_the_default_readers_cannot_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vaultsync.export.serialize_vault_export_bytes",
        lambda export: b"xx",
    )
    export_path = tmp_path / "oversized-export.atlasvault"
    with pytest.raises(VaultImportTooLargeError):
        write_atlasvault_export(object(), export_path, max_bytes=1)
    assert not export_path.exists()

    monkeypatch.setattr(
        "vaultsync.store.serialize_local_store_bytes",
        lambda store: b"xx",
    )
    store_path = tmp_path / "oversized-store.json"
    with pytest.raises(VaultImportTooLargeError):
        write_local_store(object(), store_path, max_bytes=1)
    assert not store_path.exists()
