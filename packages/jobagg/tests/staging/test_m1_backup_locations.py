import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/m1_backup_locations.py"
spec = importlib.util.spec_from_file_location("m1_backup_locations", SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_location_search_does_not_read_evidence_or_certify_integrity(tmp_path, monkeypatch):
    original = tmp_path / "original"
    backup = tmp_path / "backup"
    backup.mkdir()
    evidence = backup / "proof.jsonl"
    evidence.write_bytes(b"original payload")

    def forbid_open(*args, **kwargs):
        pytest.fail("evidence content opened by metadata-only search")

    monkeypatch.setattr(Path, "open", forbid_open)
    result = helper.locate(
        [dict(path=str(original / "proof.jsonl"), expected_sha256="a" * 64)],
        original_root=original,
        backup_root=backup,
        max_files=1,
        seconds=10,
    )
    assert result["references"] == 1
    assert result["evidence_payloads_read"] == 0
    assert result["ready"] is False and result["integrity_verified"] is False
    assert result["files"][0]["original_expected_sha256"] == "a" * 64


def test_location_search_rejects_path_escape_and_symlink(tmp_path):
    original, backup = tmp_path / "original", tmp_path / "backup"
    backup.mkdir()
    (tmp_path / "outside").write_bytes(b"private")
    (backup / "alias").symlink_to(tmp_path / "outside")
    for target in [original / "alias", original / "../outside"]:
        with pytest.raises(ValueError):
            helper.locate(
                [dict(path=str(target), sha256="a" * 64)],
                original_root=original,
                backup_root=backup,
                max_files=1,
                seconds=10,
            )


def test_location_search_rejects_duplicate_or_unbounded_inventory(tmp_path):
    original, backup = tmp_path / "original", tmp_path / "backup"
    backup.mkdir()
    (backup / "proof").write_bytes(b"proof")
    row = dict(path=str(original / "proof"), sha256="a" * 64)
    with pytest.raises(ValueError, match="duplicate"):
        helper.locate(
            [row, row], original_root=original, backup_root=backup, max_files=2, seconds=10
        )
    with pytest.raises(ValueError, match="finite positive"):
        helper.locate(
            [row], original_root=original, backup_root=backup, max_files=1, seconds=float("inf")
        )
