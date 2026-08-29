from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Iterable, Mapping


MAXIMUM_TRUSTED_DEVICE_REGISTRY_BYTES = 2 * 1024 * 1024
MAXIMUM_PAIRING_REPLAY_STATE_BYTES = 2 * 1024 * 1024
MAXIMUM_PAIRING_TRANSACTION_JOURNAL_BYTES = 64 * 1024
MAXIMUM_PAIRING_BOOTSTRAP_BYTES = 128 * 1024 * 1024
MAXIMUM_IMPORTED_ENCRYPTED_STATE_BYTES = 128 * 1024 * 1024
MAXIMUM_STAGED_PAIRING_ARTIFACT_BYTES = 128 * 1024 * 1024
MAXIMUM_STAGED_PAIRING_ARTIFACT_COUNT = 4


class ProtectedStateCategory(str, Enum):
    trusted_device_registry = "trusted_device_registry"
    pairing_replay_state = "pairing_replay_state"
    pairing_transaction_journal = "pairing_transaction_journal"
    pairing_bootstrap = "pairing_bootstrap"
    imported_encrypted_state = "imported_encrypted_state"


_MAXIMUM_BYTES: Mapping[ProtectedStateCategory, int] = MappingProxyType(
    {
        ProtectedStateCategory.trusted_device_registry: MAXIMUM_TRUSTED_DEVICE_REGISTRY_BYTES,
        ProtectedStateCategory.pairing_replay_state: MAXIMUM_PAIRING_REPLAY_STATE_BYTES,
        ProtectedStateCategory.pairing_transaction_journal: MAXIMUM_PAIRING_TRANSACTION_JOURNAL_BYTES,
        ProtectedStateCategory.pairing_bootstrap: MAXIMUM_PAIRING_BOOTSTRAP_BYTES,
        ProtectedStateCategory.imported_encrypted_state: MAXIMUM_IMPORTED_ENCRYPTED_STATE_BYTES,
    }
)


class ProtectedStateBoundsError(ValueError):
    """Raised before oversized protected state is decoded or persisted."""


def _invalid_bounds() -> ProtectedStateBoundsError:
    return ProtectedStateBoundsError("protected-state size is invalid")


def maximum_protected_state_byte_count(category: ProtectedStateCategory) -> int:
    if not isinstance(category, ProtectedStateCategory):
        raise _invalid_bounds()
    return _MAXIMUM_BYTES[category]


def require_protected_state_byte_count(
    category: ProtectedStateCategory,
    byte_count: int,
) -> int:
    maximum = maximum_protected_state_byte_count(category)
    if type(byte_count) is not int or byte_count <= 0 or byte_count > maximum:
        raise _invalid_bounds()
    return byte_count


def require_staged_pairing_artifact_byte_counts(byte_counts: Iterable[int]) -> int:
    try:
        total = 0
        count = 0
        for byte_count in byte_counts:
            count += 1
            if (
                count > MAXIMUM_STAGED_PAIRING_ARTIFACT_COUNT
                or type(byte_count) is not int
                or byte_count <= 0
                or byte_count > MAXIMUM_STAGED_PAIRING_ARTIFACT_BYTES
                or total > MAXIMUM_STAGED_PAIRING_ARTIFACT_BYTES - byte_count
            ):
                raise _invalid_bounds()
            total += byte_count
        return total
    except ProtectedStateBoundsError:
        raise
    except Exception as exc:
        raise _invalid_bounds() from exc
