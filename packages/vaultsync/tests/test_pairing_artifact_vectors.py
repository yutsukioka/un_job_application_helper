from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from vaultsync.pairing_artifacts import PairingArtifact, PairingArtifactError


REPO_ROOT = Path(__file__).resolve().parents[3]
VECTOR_PATH = REPO_ROOT / "contracts/sync/test_vectors/atlasvault_trusted_pairing_delivery_vectors_v1.json"


def test_all_pairing_artifacts_match_strict_canonical_vector_bytes() -> None:
    root = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    artifacts = root["artifacts"]
    for kind in ("offer", "acceptance", "delivery", "acknowledgement"):
        value = artifacts[kind]
        encoded = base64.b64decode(value["canonical_b64"], validate=True)
        artifact = PairingArtifact.from_canonical_bytes(encoded)
        assert artifact.kind == kind
        assert artifact.to_dict() == value["value"]
        assert artifact.canonical_bytes() == encoded
        assert artifact.sha256_hex() == value["sha256"]


def test_pairing_artifacts_reject_unknown_fields_kinds_and_noncanonical_bytes() -> None:
    root = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    original = dict(root["artifacts"]["offer"]["value"])
    original["private_path"] = "/must/not/appear"
    with pytest.raises(PairingArtifactError):
        PairingArtifact.from_dict(original)

    encoded = base64.b64decode(root["artifacts"]["offer"]["canonical_b64"], validate=True)
    with pytest.raises(PairingArtifactError):
        PairingArtifact.from_canonical_bytes(b" " + encoded)
