from pathlib import Path

import pytest

import vaultsync.trusted_devices as trusted_devices
from vaultsync.format import VaultImportTooLargeError, read_vault_import_bytes
from vaultsync.protected_state_bounds import (
    MAXIMUM_IMPORTED_ENCRYPTED_STATE_BYTES,
    MAXIMUM_PAIRING_BOOTSTRAP_BYTES,
    MAXIMUM_PAIRING_REPLAY_STATE_BYTES,
    MAXIMUM_PAIRING_TRANSACTION_JOURNAL_BYTES,
    MAXIMUM_STAGED_PAIRING_ARTIFACT_BYTES,
    MAXIMUM_TRUSTED_DEVICE_REGISTRY_BYTES,
    ProtectedStateBoundsError,
    ProtectedStateCategory,
    maximum_protected_state_byte_count,
    require_protected_state_byte_count,
    require_staged_pairing_artifact_byte_counts,
)
from vaultsync.trusted_devices import (
    PairingReplayStore,
    TrustedDeviceRegistry,
    TrustedDeviceStateError,
)


@pytest.mark.parametrize(
    ("category", "expected"),
    (
        (
            ProtectedStateCategory.trusted_device_registry,
            MAXIMUM_TRUSTED_DEVICE_REGISTRY_BYTES,
        ),
        (
            ProtectedStateCategory.pairing_replay_state,
            MAXIMUM_PAIRING_REPLAY_STATE_BYTES,
        ),
        (
            ProtectedStateCategory.pairing_transaction_journal,
            MAXIMUM_PAIRING_TRANSACTION_JOURNAL_BYTES,
        ),
        (
            ProtectedStateCategory.pairing_bootstrap,
            MAXIMUM_PAIRING_BOOTSTRAP_BYTES,
        ),
        (
            ProtectedStateCategory.imported_encrypted_state,
            MAXIMUM_IMPORTED_ENCRYPTED_STATE_BYTES,
        ),
    ),
)
def test_protected_state_category_bounds_are_exact(
    category: ProtectedStateCategory,
    expected: int,
) -> None:
    assert maximum_protected_state_byte_count(category) == expected
    assert require_protected_state_byte_count(category, expected) == expected
    with pytest.raises(ProtectedStateBoundsError):
        require_protected_state_byte_count(category, expected + 1)


@pytest.mark.parametrize("invalid", (0, -1, True, 1.5))
def test_protected_state_byte_counts_are_positive_integers(invalid: object) -> None:
    with pytest.raises(ProtectedStateBoundsError):
        require_protected_state_byte_count(
            ProtectedStateCategory.trusted_device_registry,
            invalid,  # type: ignore[arg-type]
        )


def test_staged_artifact_total_is_bounded_without_allocating_artifacts() -> None:
    half = MAXIMUM_STAGED_PAIRING_ARTIFACT_BYTES // 2
    assert (
        require_staged_pairing_artifact_byte_counts((half, half))
        == MAXIMUM_STAGED_PAIRING_ARTIFACT_BYTES
    )
    with pytest.raises(ProtectedStateBoundsError):
        require_staged_pairing_artifact_byte_counts((half, half + 1))
    with pytest.raises(ProtectedStateBoundsError):
        require_staged_pairing_artifact_byte_counts((1, 1, 1, 1, 1))


@pytest.mark.parametrize(
    ("decoder", "category"),
    (
        (
            TrustedDeviceRegistry.from_canonical_bytes,
            ProtectedStateCategory.trusted_device_registry,
        ),
        (
            PairingReplayStore.from_canonical_bytes,
            ProtectedStateCategory.pairing_replay_state,
        ),
    ),
)
def test_oversized_registry_and_replay_fail_before_json_decode(
    monkeypatch: pytest.MonkeyPatch,
    decoder: object,
    category: ProtectedStateCategory,
) -> None:
    decoded = False

    def unexpected_decode(_: object) -> object:
        nonlocal decoded
        decoded = True
        raise AssertionError("oversized protected state reached JSON decoding")

    monkeypatch.setattr(trusted_devices.json, "loads", unexpected_decode)
    limit = maximum_protected_state_byte_count(category)
    with pytest.raises(TrustedDeviceStateError):
        decoder(b"x" * (limit + 1))  # type: ignore[operator]
    assert decoded is False


def test_import_hard_ceiling_fails_before_sparse_file_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "oversized.atlasvault"
    with source.open("wb") as handle:
        handle.seek(MAXIMUM_IMPORTED_ENCRYPTED_STATE_BYTES)
        handle.write(b"x")

    read_attempted = False

    def unexpected_read(_: Path) -> bytes:
        nonlocal read_attempted
        read_attempted = True
        raise AssertionError("oversized import reached file allocation")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    with pytest.raises(VaultImportTooLargeError):
        read_vault_import_bytes(
            source,
            max_bytes=MAXIMUM_IMPORTED_ENCRYPTED_STATE_BYTES + 1,
        )
    assert read_attempted is False
