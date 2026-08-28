from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages" / "vaultsync"))

from vaultsync.service_contract_guard import find_raw_secret_wire_contract_violations  # noqa: E402


def test_A4_services_do_not_accept_raw_vault_secret_material() -> None:
    violations = find_raw_secret_wire_contract_violations(ROOT / "services")

    assert violations == []


def test_A4_guard_detects_raw_passphrase_and_unwrapped_key_fields(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "bad_api.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class BadSyncRequest(BaseModel):
    passphrase: str
    raw_vault_key_b64: str

@app.post("/api/encrypted-sync/bad")
def bad_sync(request: BadSyncRequest, vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("passphrase" in violation for violation in violations)
    assert any("raw_vault_key_b64" in violation for violation in violations)
    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_detects_pydantic_wire_aliases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "aliased_api.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from pydantic import BaseModel, Field

class AliasedSyncRequest(BaseModel):
    encrypted_payload: str = Field(alias="vault_key")
    wrapped_input: str = Field(validation_alias="recovery_key")
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_detects_generic_api_route_parameters(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "generic_route.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()

@app.api_route("/api/encrypted-sync/generic", methods=["POST"])
def generic_sync(unwrapped_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("unwrapped_key" in violation for violation in violations)


def test_A4_guard_resolves_aliased_and_local_pydantic_bases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "model_bases.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from pydantic import BaseModel as PydanticModel

class ProjectRequest(PydanticModel):
    request_id: str

class BadSyncRequest(ProjectRequest):
    raw_vault_key: str
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("raw_vault_key" in violation for violation in violations)
