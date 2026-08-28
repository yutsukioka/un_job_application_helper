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


def test_A4_guard_detects_route_parameter_wire_aliases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "parameter_aliases.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import Annotated

from fastapi import Body, FastAPI, Query

app = FastAPI()

@app.post("/api/encrypted-sync/aliases")
def aliased_sync(
    encrypted_payload: str = Body(alias="vault_key"),
    wrapped_input: Annotated[str, Query(alias="recovery_key")] = "",
) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_resolves_imported_project_model_base_aliases(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "models.py").write_text(
        """
from pydantic import BaseModel

class ProjectRequest(BaseModel):
    request_id: str
""",
        encoding="utf-8",
    )
    (service_root / "bad_api.py").write_text(
        """
from .models import ProjectRequest as APIModel

class BadSyncRequest(APIModel):
    raw_vault_key: str
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("raw_vault_key" in violation for violation in violations)


def test_A4_guard_normalizes_common_wire_name_casing_and_separators(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "wire_names.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI()

class BadSyncRequest(BaseModel):
    encrypted_payload: str = Field(alias="rawVaultKey")

@app.post("/api/encrypted-sync/bad")
def bad_sync(recoveryKey: str, encrypted: str = Field(alias="vault-key")) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("rawVaultKey" in violation for violation in violations)
    assert any("recoveryKey" in violation for violation in violations)
    assert any("vault-key" in violation for violation in violations)


def test_A4_guard_scans_programmatically_registered_route_handlers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "registered_route.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()

def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}

app.add_api_route("/api/encrypted-sync", sync, methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_resolves_programmatic_handlers_across_modules(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "handlers.py").write_text(
        """
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )
    (service_root / "routes.py").write_text(
        """
from fastapi import FastAPI

from .handlers import sync

app = FastAPI()
app.add_api_route("/api/encrypted-sync", sync, methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_resolves_module_qualified_programmatic_handlers(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "handlers.py").write_text(
        """
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )
    (service_root / "routes.py").write_text(
        """
from fastapi import FastAPI

from . import handlers

app = FastAPI()
app.add_api_route("/api/encrypted-sync", handlers.sync, methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_indexes_nested_service_package_roots(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    package_root = service_root / "job-api" / "job_api"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "handlers.py").write_text(
        """
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )
    (package_root / "routes.py").write_text(
        """
from fastapi import FastAPI

from job_api.handlers import sync

app = FastAPI()
app.add_api_route("/api/encrypted-sync", sync, methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_scopes_pydantic_model_identity_to_its_module(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "api_models.py").write_text(
        """
from pydantic import BaseModel

class Request(BaseModel):
    encrypted_payload: str
""",
        encoding="utf-8",
    )
    (service_root / "internal_state.py").write_text(
        """
class Request:
    passphrase: str
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert violations == []


def test_A4_guard_excludes_pydantic_private_and_class_variables(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "internal_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import ClassVar

from pydantic import BaseModel, PrivateAttr

class SyncRequest(BaseModel):
    encrypted_payload: str
    _vault_key: bytes = PrivateAttr()
    passphrase: ClassVar[str] = "internal-only"
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_requires_an_actual_pydantic_base_import(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "domain_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
class BaseModel:
    pass

class InternalState(BaseModel):
    vault_key: bytes
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_follows_fastapi_dependency_callables(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "dependencies.py").write_text(
        """
from fastapi import Body

def load_route_secret(vault_key: str = Body()) -> None:
    pass

def load_decorator_secret(recovery_key: str = Body()) -> None:
    pass

def load_app_secret(passphrase: str = Body()) -> None:
    pass
""",
        encoding="utf-8",
    )
    (service_root / "routes.py").write_text(
        """
from fastapi import Depends, FastAPI

from .dependencies import load_app_secret, load_decorator_secret, load_route_secret

app = FastAPI(dependencies=[Depends(load_app_secret)])

@app.post(
    "/api/encrypted-sync",
    dependencies=[Depends(load_decorator_secret)],
)
def sync(_: None = Depends(load_route_secret)) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)
    assert any("passphrase" in violation for violation in violations)


def test_A4_guard_follows_dependencies_on_nested_factory_routes(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "factory.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Body, Depends, FastAPI

def load_secret(vault_key: str = Body()) -> None:
    pass

def create_app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/encrypted-sync")
    def sync(_: None = Depends(load_secret)) -> dict[str, bool]:
        return {"ok": True}

    return app
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_detects_imported_dataclass_request_body_fields(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "models.py").write_text(
        """
from dataclasses import dataclass

@dataclass
class SyncRequest:
    vault_key: str
""",
        encoding="utf-8",
    )
    (service_root / "routes.py").write_text(
        """
from fastapi import FastAPI

from .models import SyncRequest

app = FastAPI()

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_detects_inherited_dataclass_request_fields(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "models.py").write_text(
        """
from dataclasses import dataclass

@dataclass
class BaseRequest:
    vault_key: str

@dataclass
class SyncRequest(BaseRequest):
    encrypted_payload: str
""",
        encoding="utf-8",
    )
    (service_root / "routes.py").write_text(
        """
from fastapi import FastAPI

from .models import SyncRequest

app = FastAPI()

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_ignores_unannotated_dataclass_class_state(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "dataclass_state.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from dataclasses import dataclass

from fastapi import FastAPI

app = FastAPI()

@dataclass
class SyncRequest:
    encrypted_payload: str
    vault_key = b"internal-only"

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_follows_request_models_from_repository_packages(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    service_root = repository_root / "services"
    shared_root = repository_root / "shared_api"
    service_root.mkdir(parents=True)
    shared_root.mkdir()
    (shared_root / "__init__.py").write_text("", encoding="utf-8")
    (shared_root / "models.py").write_text(
        """
from pydantic import BaseModel

class SyncRequest(BaseModel):
    vault_key: str
""",
        encoding="utf-8",
    )
    (service_root / "routes.py").write_text(
        """
from fastapi import FastAPI

from shared_api.models import SyncRequest

app = FastAPI()

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_detects_raw_request_body_secret_keys(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "raw_request.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/api/encrypted-sync/raw")
async def raw_sync(request: Request) -> dict[str, bool]:
    body = await request.json()
    return {
        "has_vault_key": bool(body["vault_key"]),
        "has_passphrase": bool(body.get("passphrase")),
    }
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("passphrase" in violation for violation in violations)


def test_A4_guard_ignores_banned_names_in_internal_route_mappings(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "internal_mapping.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()

@app.post("/api/encrypted-sync/internal")
def internal_sync() -> dict[str, bool]:
    settings = {"vault_key": "internal-only"}
    return {"configured": bool(settings.get("vault_key"))}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_detects_raw_request_form_secret_keys(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "raw_form.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/api/encrypted-sync/form")
async def form_sync(request: Request) -> dict[str, bool]:
    form = await request.form()
    return {"has_vault_key": bool(form["vault_key"])}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
