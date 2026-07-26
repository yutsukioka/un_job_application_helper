from pathlib import Path

from vaultsync import crypto, format as vault_format


REPO_ROOT = Path(__file__).resolve().parents[3]
VECTOR_PATH = (
    REPO_ROOT
    / "contracts"
    / "sync"
    / "test_vectors"
    / "atlasvault_recovery_export_vectors_v2.json"
)


def test_recovery_wrap_v2_reference_surface_exists() -> None:
    required_format_symbols = (
        "RecoveryKeyWrapV2",
        "RecoveryKeyWrapHKDFParams",
    )
    required_crypto_symbols = (
        "encode_recovery_key",
        "parse_recovery_key",
        "recovery_wrap_v2_aad",
        "wrap_vault_key_with_recovery",
        "unwrap_vault_key_with_recovery",
    )

    for symbol in required_format_symbols:
        assert hasattr(vault_format, symbol), symbol
    for symbol in required_crypto_symbols:
        assert hasattr(crypto, symbol), symbol


def test_shared_recovery_export_vector_exists() -> None:
    assert VECTOR_PATH.is_file()
