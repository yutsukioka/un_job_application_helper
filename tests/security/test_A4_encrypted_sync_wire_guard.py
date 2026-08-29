from __future__ import annotations

import subprocess
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
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI()

class AliasedSyncRequest(BaseModel):
    encrypted_payload: str = Field(alias="vault_key")
    wrapped_input: str = Field(validation_alias="recovery_key")

@app.post("/api/encrypted-sync")
def sync(request: AliasedSyncRequest) -> dict[str, bool]:
    return {"ok": True}
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
from fastapi import FastAPI
from pydantic import BaseModel as PydanticModel

app = FastAPI()

class ProjectRequest(PydanticModel):
    request_id: str

class BadSyncRequest(ProjectRequest):
    raw_vault_key: str

@app.post("/api/encrypted-sync")
def sync(request: BadSyncRequest) -> dict[str, bool]:
    return {"ok": True}
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
from fastapi import FastAPI

from .models import ProjectRequest as APIModel

app = FastAPI()

class BadSyncRequest(APIModel):
    raw_vault_key: str

@app.post("/api/encrypted-sync")
def sync(request: BadSyncRequest) -> dict[str, bool]:
    return {"ok": True}
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
def bad_sync(
    request: BadSyncRequest,
    recoveryKey: str,
    encrypted: str = Field(alias="vault-key"),
) -> dict[str, bool]:
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


def test_A4_guard_supports_module_qualified_pydantic_bases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "qualified_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
import pydantic as pd

app = FastAPI()

class SyncRequest(pd.BaseModel):
    vault_key: str

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_inspects_only_request_bound_pydantic_models(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "internal_state.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class LocalUnlockState(BaseModel):
    passphrase: str

@app.post("/api/encrypted-sync")
def sync(encrypted_payload: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_detects_permissive_request_model_extras(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "permissive_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

app = FastAPI()

class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    encrypted_payload: str

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("extra" in violation for violation in violations)


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


def test_A4_guard_preserves_nested_function_lexical_identity(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "factories.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Depends, FastAPI

def load_secret() -> None:
    pass

def create_app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/encrypted-sync")
    def sync(_: None = Depends(load_secret)) -> dict[str, bool]:
        return {"ok": True}

    return app

def unrelated_factory() -> None:
    def load_secret(vault_key: str) -> None:
        pass
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_ignores_non_web_decorators_with_route_method_names(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "registry.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
class Registry:
    def get(self, name):
        def decorate(function):
            return function
        return decorate

registry = Registry()

@registry.get("decoder")
def decode(passphrase: str) -> str:
    return passphrase
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_resolves_imported_framework_route_owners(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "router.py").write_text(
        """
from fastapi import APIRouter

router = APIRouter()
""",
        encoding="utf-8",
    )
    (service_root / "routes.py").write_text(
        """
from .router import router

@router.post("/api/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_excludes_dependency_injected_parameter_names(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "injected.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Depends, FastAPI

app = FastAPI()

def load_vault_key() -> bytes:
    return b"local-only"

@app.post("/api/encrypted-sync")
def sync(vault_key: bytes = Depends(load_vault_key)) -> dict[str, bool]:
    return {"ok": bool(vault_key)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


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


def test_A4_guard_follows_request_models_reexported_by_packages(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    service_root = repository_root / "services"
    shared_root = repository_root / "shared_api"
    service_root.mkdir(parents=True)
    shared_root.mkdir()
    (shared_root / "__init__.py").write_text(
        "from .models import SyncRequest\n",
        encoding="utf-8",
    )
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

from shared_api import SyncRequest

app = FastAPI()

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_follows_nested_pydantic_request_models(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "nested_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class SecretInput(BaseModel):
    vault_key: str

class SyncRequest(BaseModel):
    secret: SecretInput

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_resolves_quoted_request_model_annotations(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "quoted_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class SyncRequest(BaseModel):
    vault_key: str

@app.post("/api/encrypted-sync")
def sync(request: "SyncRequest") -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

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


def test_A4_guard_recognizes_fastapi_requests_module_import(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "raw_module_request.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from fastapi.requests import Request

app = FastAPI()

@app.post("/api/encrypted-sync/raw")
async def raw_sync(request: Request) -> dict[str, bool]:
    body = await request.json()
    return {"has_vault_key": bool(body["vault_key"])}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_rejects_unconstrained_mapping_request_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "mapping_body.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import Any

from fastapi import Depends, FastAPI

app = FastAPI()

def local_settings() -> dict[str, Any]:
    return {"vault_key": "local-only"}

@app.post("/api/encrypted-sync")
def sync(payload: dict[str, Any]) -> dict[str, bool]:
    return {"ok": bool(payload)}

@app.get("/api/local-settings")
def safe(settings: dict[str, Any] = Depends(local_settings)) -> dict[str, bool]:
    return {"ok": bool(settings)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "sync.payload" in violation and "unconstrained mapping" in violation
        for violation in violations
    )
    assert not any("safe.settings" in violation for violation in violations)


def test_A4_guard_resolves_module_qualified_imported_route_owners(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "router.py").write_text(
        """
from fastapi import APIRouter

router = APIRouter()
""",
        encoding="utf-8",
    )
    (service_root / "routes.py").write_text(
        """
from . import router as routing

@routing.router.post("/api/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_recognizes_module_qualified_depends_imports(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "qualified_depends.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import fastapi.params
from fastapi import Body, FastAPI

app = FastAPI()

def load_secret(vault_key: str = Body()) -> None:
    pass

@app.post("/api/encrypted-sync")
def sync(payload: str, _: None = fastapi.params.Depends(load_secret)) -> dict[str, bool]:
    return {"ok": bool(payload)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_indexes_request_models_inside_factories(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "nested_request_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

def create_app() -> FastAPI:
    app = FastAPI()

    class SyncRequest(BaseModel):
        vault_key: str

    @app.post("/api/encrypted-sync")
    def sync(request: SyncRequest) -> dict[str, bool]:
        return {"ok": True}

    return app
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("SyncRequest.vault_key" in violation for violation in violations)


def test_A4_guard_inspects_typed_dict_request_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "typed_dict_body.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import TypedDict

from fastapi import FastAPI

app = FastAPI()

class InternalState(TypedDict):
    vault_key: str

class SyncRequest(TypedDict):
    encrypted_payload: str
    recovery_key: str

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("SyncRequest.recovery_key" in violation for violation in violations)
    assert not any("InternalState.vault_key" in violation for violation in violations)


def test_A4_guard_recognizes_module_qualified_pydantic_fields(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "qualified_field.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import pydantic as pd
from fastapi import FastAPI

app = FastAPI()

class SyncRequest(pd.BaseModel):
    encrypted_payload: str = pd.Field(alias="vault_key")

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_scans_shared_routers_mounted_by_services(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    service_root = repository_root / "services"
    shared_root = repository_root / "shared_api"
    service_root.mkdir(parents=True)
    shared_root.mkdir()
    (shared_root / "__init__.py").write_text("", encoding="utf-8")
    (shared_root / "routes.py").write_text(
        """
from fastapi import APIRouter

router = APIRouter()

@router.post("/api/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )
    (service_root / "app.py").write_text(
        """
from fastapi import FastAPI

from shared_api.routes import router

app = FastAPI()
app.include_router(router)
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_inspects_functional_typed_dict_request_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "functional_typed_dict.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import TypedDict

from fastapi import FastAPI

app = FastAPI()

SyncRequest = TypedDict(
    "SyncRequest",
    {"encrypted_payload": str, "vault_key": str},
)

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("SyncRequest.vault_key" in violation for violation in violations)


def test_A4_guard_follows_fastapi_security_dependencies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "security_dependency.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Body, FastAPI, Security

app = FastAPI()

def load_secret(vault_key: str = Body()) -> None:
    pass

@app.post("/api/encrypted-sync")
def sync(_: None = Security(load_secret)) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_resolves_constant_valued_wire_aliases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "constant_aliases.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import ClassVar

from fastapi import Body, FastAPI
from pydantic import BaseModel, Field

MODEL_ALIAS = "vault_key"
ROUTE_ALIAS = "passphrase"

app = FastAPI()

class SyncRequest(BaseModel):
    CLASS_ALIAS: ClassVar[str] = "recovery_key"
    encrypted_payload: str = Field(alias=MODEL_ALIAS)
    wrapped_payload: str = Field(validation_alias=CLASS_ALIAS)

@app.post("/api/encrypted-sync")
def sync(
    request: SyncRequest,
    encrypted_input: str = Body(alias=ROUTE_ALIAS),
) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)
    assert any("passphrase" in violation for violation in violations)


def test_A4_guard_resolves_local_request_model_type_aliases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "model_aliases.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import Annotated, TypeAlias

from fastapi import Body, FastAPI
from pydantic import BaseModel

app = FastAPI()

class SyncRequest(BaseModel):
    vault_key: str

RequestPayload: TypeAlias = SyncRequest
AnnotatedPayload: TypeAlias = Annotated[SyncRequest, Body()]

@app.post("/api/encrypted-sync/plain")
def plain_sync(request: RequestPayload) -> dict[str, bool]:
    return {"ok": True}

@app.post("/api/encrypted-sync/annotated")
def annotated_sync(request: AnnotatedPayload) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert sum("SyncRequest.vault_key" in violation for violation in violations) == 1


def test_A4_guard_fails_closed_for_missing_or_empty_service_roots(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing-services"
    empty_root = tmp_path / "empty-services"
    empty_root.mkdir()

    missing_violations = find_raw_secret_wire_contract_violations(missing_root)
    empty_violations = find_raw_secret_wire_contract_violations(empty_root)

    assert any("service root" in violation for violation in missing_violations)
    assert any("no Python service modules" in violation for violation in empty_violations)


def test_A4_guard_uses_stable_source_order_for_reassigned_constants(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "reassigned_alias.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel, Field

ALIAS = "passphrase"
ALIAS = "vault_key"

app = FastAPI()

class SyncRequest(BaseModel):
    encrypted_payload: str = Field(alias=ALIAS)

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )
    probe = """
import importlib.util
import sys
from pathlib import Path

guard_path = Path(sys.argv[2]) / "vaultsync" / "service_contract_guard.py"
spec = importlib.util.spec_from_file_location("_isolated_service_contract_guard", guard_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"could not load guard from {guard_path}")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
print("\\n".join(guard.find_raw_secret_wire_contract_violations(sys.argv[1])))
"""

    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            probe,
            str(service_file.parent),
            str(ROOT / "packages" / "vaultsync"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert completed.returncode == 0, completed.stderr
    assert "vault_key" in completed.stdout


def test_A4_guard_resolves_type_alias_type_request_models(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "type_alias_type.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing_extensions import TypeAliasType

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class SyncRequest(BaseModel):
    vault_key: str

RequestPayload = TypeAliasType("RequestPayload", SyncRequest)

@app.post("/api/encrypted-sync")
def sync(request: RequestPayload) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("SyncRequest.vault_key" in violation for violation in violations)


def test_A4_guard_rejects_mapping_pydantic_root_models(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "root_models.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import RootModel

app = FastAPI()

class MappingRequest(RootModel[dict[str, str]]):
    pass

class ScalarRequest(RootModel[str]):
    pass

@app.post("/api/encrypted-sync/mapping")
def mapping_sync(request: MappingRequest) -> dict[str, bool]:
    return {"ok": True}

@app.post("/api/encrypted-sync/scalar")
def scalar_sync(request: ScalarRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "MappingRequest" in violation and "unconstrained mapping root" in violation
        for violation in violations
    )
    assert not any("ScalarRequest" in violation for violation in violations)


def test_A4_guard_inspects_direct_request_mapping_attributes(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "request_mappings.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/api/encrypted-sync/{vault_id}")
def sync(request: Request, vault_id: str) -> dict[str, object]:
    return {
        "query": request.query_params["vault_key"],
        "header": request.headers.get("passphrase"),
        "cookie": request.cookies["recovery_key"],
        "path": request.path_params.get("unwrapped_key"),
    }
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    for wire_name in {"vault_key", "passphrase", "recovery_key", "unwrapped_key"}:
        assert any(wire_name in violation for violation in violations)


def test_A4_guard_recognizes_pydantic_dataclass_request_models(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "pydantic_dataclasses.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import pydantic.dataclasses as pydantic_dataclasses
from fastapi import FastAPI
from pydantic.dataclasses import dataclass as pydantic_dataclass

app = FastAPI()

@pydantic_dataclass
class DirectRequest:
    vault_key: str

@pydantic_dataclasses.dataclass
class QualifiedRequest:
    recovery_key: str

@app.post("/api/encrypted-sync/direct")
def direct_sync(request: DirectRequest) -> dict[str, bool]:
    return {"ok": True}

@app.post("/api/encrypted-sync/qualified")
def qualified_sync(request: QualifiedRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("DirectRequest.vault_key" in violation for violation in violations)
    assert any("QualifiedRequest.recovery_key" in violation for violation in violations)


def test_A4_guard_recognizes_create_model_request_schemas(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "created_models.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import pydantic as pd
from fastapi import FastAPI
from pydantic import Field, create_model

app = FastAPI()

DirectRequest = create_model(
    "DirectRequest",
    vault_key=(str, ...),
    encrypted_payload=(str, Field(alias="passphrase")),
)
QualifiedRequest = pd.create_model(
    "QualifiedRequest",
    recovery_key=(str, ...),
)

@app.post("/api/encrypted-sync/direct")
def direct_sync(request: DirectRequest) -> dict[str, bool]:
    return {"ok": True}

@app.post("/api/encrypted-sync/qualified")
def qualified_sync(request: QualifiedRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("DirectRequest.vault_key" in violation for violation in violations)
    assert any("DirectRequest.passphrase" in violation for violation in violations)
    assert any("QualifiedRequest.recovery_key" in violation for violation in violations)


def test_A4_guard_indexes_starlette_route_constructor_handlers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "starlette_routes.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

async def sync(request: Request) -> JSONResponse:
    return JSONResponse({"value": request.query_params["vault_key"]})

app = Starlette(routes=[Route("/api/encrypted-sync", sync, methods=["POST"])])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync['vault_key']" in violation for violation in violations)


def test_A4_guard_follows_dataclass_request_model_inheritance(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "inherited_dataclass.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from dataclasses import dataclass
from fastapi import FastAPI

app = FastAPI()

@dataclass
class SecretFields:
    vault_key: str

class SyncRequest(SecretFields):
    pass

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("SecretFields.vault_key" in violation for violation in violations)


def test_A4_guard_indexes_bound_method_route_handlers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "bound_method.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()

class Controller:
    def sync(self, vault_key: str) -> dict[str, bool]:
        return {"ok": True}

controller = Controller()
app.add_api_route("/api/encrypted-sync", controller.sync, methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync.vault_key" in violation for violation in violations)


def test_A4_guard_treats_unannotated_starlette_request_as_wire_input(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "unannotated_starlette.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from starlette.applications import Starlette
from starlette.responses import JSONResponse

app = Starlette()

@app.route("/api/encrypted-sync", methods=["POST"])
async def sync(request):
    payload = await request.json()
    return JSONResponse({"value": payload["vault_key"]})
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync['vault_key']" in violation for violation in violations)


def test_A4_guard_inspects_websocket_json_payloads(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "websocket_payload.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, WebSocket

app = FastAPI()

@app.websocket("/api/encrypted-sync")
async def sync(socket: WebSocket) -> None:
    payload = await socket.receive_json()
    await socket.send_json({"value": payload["vault_key"]})
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync['vault_key']" in violation for violation in violations)


def test_A4_guard_indexes_inherited_bound_method_route_handlers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "inherited_bound_method.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()

class BaseController:
    def sync(self, vault_key: str) -> dict[str, bool]:
        return {"ok": True}

class Controller(BaseController):
    pass

controller = Controller()
app.add_api_route("/api/encrypted-sync", controller.sync, methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync.vault_key" in violation for violation in violations)


def test_A4_guard_inspects_websocket_text_frame_json(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "websocket_text.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import json
from fastapi import FastAPI, WebSocket

app = FastAPI()

@app.websocket("/api/encrypted-sync")
async def sync(socket: WebSocket) -> None:
    payload = json.loads(await socket.receive_text())
    await socket.send_json({"value": payload["vault_key"]})
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync['vault_key']" in violation for violation in violations)


def test_A4_guard_rejects_pydantic_request_alias_generators(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "alias_generators.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

app = FastAPI()

class SyncRequest(BaseModel):
    secret: str
    model_config = ConfigDict(alias_generator=lambda _: "vault_key")

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "SyncRequest" in violation and "alias generator" in violation
        for violation in violations
    )


def test_A4_guard_follows_create_model_base_fields(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "created_model_base.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel, create_model

app = FastAPI()

class SecretBase(BaseModel):
    vault_key: str

SyncRequest = create_model("SyncRequest", __base__=SecretBase)

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("SecretBase.vault_key" in violation for violation in violations)


def test_A4_guard_follows_python_mro_for_inherited_handlers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "mro_bound_method.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()

class RootController:
    def sync(self, payload: str) -> dict[str, bool]:
        return {"ok": True}

class LeftController(RootController):
    pass

class RightController(RootController):
    def sync(self, vault_key: str) -> dict[str, bool]:
        return {"ok": True}

class Controller(LeftController, RightController):
    pass

controller = Controller()
app.add_api_route("/api/encrypted-sync", controller.sync, methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync.vault_key" in violation for violation in violations)


def test_A4_guard_rejects_pydantic_class_keyword_alias_generators(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "class_keyword_alias.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class SyncRequest(BaseModel, alias_generator=lambda _: "vault_key"):
    value: str

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "SyncRequest" in violation and "alias generator" in violation
        for violation in violations
    )


def test_A4_guard_ignores_dependency_lists_on_ordinary_calls(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "ordinary_dependencies.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Depends, FastAPI

app = FastAPI()

def load_local(vault_key: str) -> None:
    return None

def configure(*, dependencies: list[object]) -> None:
    return None

configure(dependencies=[Depends(load_local)])

@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_recognizes_framework_subclasses_as_route_owners(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "framework_subclass.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

class ServiceApp(FastAPI):
    pass

app = ServiceApp()

@app.post("/api/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync.vault_key" in violation for violation in violations)


def test_A4_guard_tracks_json_decoded_from_http_request_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "request_body_json.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import json
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/api/encrypted-sync")
async def sync(request: Request) -> dict[str, str]:
    payload = json.loads(await request.body())
    return {"value": payload["vault_key"]}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync['vault_key']" in violation for violation in violations)


def test_A4_guard_scans_shared_applications_mounted_by_services(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    service_root = repository_root / "services"
    shared_root = repository_root / "shared_api"
    service_root.mkdir(parents=True)
    shared_root.mkdir()
    (shared_root / "__init__.py").write_text("", encoding="utf-8")
    (shared_root / "app.py").write_text(
        """
from fastapi import FastAPI

subapp = FastAPI()

@subapp.post("/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )
    (service_root / "app.py").write_text(
        """
from fastapi import FastAPI

from shared_api.app import subapp

app = FastAPI()
app.mount("/api", subapp)
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("sync.vault_key" in violation for violation in violations)


def test_A4_guard_rejects_pydantic_class_keyword_extra_allow(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "class_keyword_extra.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class SyncRequest(BaseModel, extra="allow"):
    value: str

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "SyncRequest" in violation and "extra wire fields" in violation
        for violation in violations
    )


def test_A4_guard_rejects_permissive_create_model_configuration(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "created_model_config.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import ConfigDict, create_model

app = FastAPI()

ExtraRequest = create_model(
    "ExtraRequest",
    value=(str, ...),
    __config__=ConfigDict(extra="allow"),
)
AliasRequest = create_model(
    "AliasRequest",
    value=(str, ...),
    __config__=ConfigDict(alias_generator=lambda _: "vault_key"),
)

@app.post("/api/encrypted-sync/extra")
def extra(request: ExtraRequest) -> dict[str, bool]:
    return {"ok": True}

@app.post("/api/encrypted-sync/alias")
def alias(request: AliasRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "ExtraRequest" in violation and "extra wire fields" in violation
        for violation in violations
    )
    assert any(
        "AliasRequest" in violation and "alias generator" in violation
        for violation in violations
    )


def test_A4_guard_resolves_callable_object_route_endpoints(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "callable_endpoint.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()

class SyncEndpoint:
    def __call__(self, vault_key: str) -> dict[str, bool]:
        return {"ok": True}

app.add_api_route("/api/encrypted-sync", SyncEndpoint(), methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("__call__.vault_key" in violation for violation in violations)


def test_A4_guard_rejects_untyped_explicit_request_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "untyped_body.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import Any
from fastapi import Body, FastAPI

app = FastAPI()

@app.post("/api/encrypted-sync/any")
def any_body(payload: Any = Body()) -> dict[str, bool]:
    return {"ok": True}

@app.post("/api/encrypted-sync/object")
def object_body(payload: object = Body()) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("any_body.payload" in violation for violation in violations)
    assert any("object_body.payload" in violation for violation in violations)


def test_A4_guard_indexes_fastapi_route_constructors(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "fastapi_route_constructor.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute

def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}

def socket(recovery_key: str) -> None:
    return None

app = FastAPI(
    routes=[
        APIRoute("/api/encrypted-sync", sync, methods=["POST"]),
        APIWebSocketRoute("/api/encrypted-sync/socket", socket),
    ]
)
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync.vault_key" in violation for violation in violations)
    assert any("socket.recovery_key" in violation for violation in violations)


def test_A4_guard_inspects_http_middleware_request_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "http_middleware.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request

app = FastAPI()

@app.middleware("http")
async def inspect_body(request: Request, call_next):
    payload = await request.json()
    _ = payload["vault_key"]
    return await call_next(request)
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("inspect_body['vault_key']" in violation for violation in violations)


def test_A4_guard_rejects_annotated_untyped_request_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "annotated_untyped_body.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import Annotated, Any
from fastapi import Body, FastAPI

app = FastAPI()

@app.post("/api/encrypted-sync")
def sync(payload: Annotated[Any, Body()]) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync.payload" in violation for violation in violations)


def test_A4_guard_resolves_named_create_model_configuration(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "named_created_model_config.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import ConfigDict, create_model

app = FastAPI()
EXTRA_CONFIG = ConfigDict(extra="allow")
ALIAS_CONFIG = ConfigDict(alias_generator=lambda _: "vault_key")
ExtraRequest = create_model(
    "ExtraRequest",
    value=(str, ...),
    __config__=EXTRA_CONFIG,
)
AliasRequest = create_model(
    "AliasRequest",
    value=(str, ...),
    __config__=ALIAS_CONFIG,
)

@app.post("/api/encrypted-sync/extra")
def extra(request: ExtraRequest) -> dict[str, bool]:
    return {"ok": True}

@app.post("/api/encrypted-sync/alias")
def alias(request: AliasRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "ExtraRequest" in violation and "extra wire fields" in violation
        for violation in violations
    )
    assert any(
        "AliasRequest" in violation and "alias generator" in violation
        for violation in violations
    )


def test_A4_guard_discovers_programmatically_installed_http_middleware(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "programmatic_middleware.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI()

async def inspect_decorator(request: Request, call_next):
    payload = await request.json()
    _ = payload["vault_key"]
    return await call_next(request)

async def inspect_dispatch(request: Request, call_next):
    payload = await request.json()
    _ = payload["recovery_key"]
    return await call_next(request)

app.middleware("http")(inspect_decorator)
app.add_middleware(BaseHTTPMiddleware, dispatch=inspect_dispatch)
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "inspect_decorator['vault_key']" in violation for violation in violations
    )
    assert any(
        "inspect_dispatch['recovery_key']" in violation for violation in violations
    )


def test_A4_guard_evaluates_constant_wire_alias_expressions(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "constant_aliases.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Body, FastAPI
from pydantic import BaseModel, Field

app = FastAPI()

class SyncRequest(BaseModel):
    value: str = Field(alias=f"recovery_{'key'}")

@app.post("/api/encrypted-sync/model")
def model(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}

@app.post("/api/encrypted-sync/body")
def body(payload: str = Body(alias="vault_" + "key")) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("recovery_key" in violation for violation in violations)
    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_rejects_before_model_validators_on_request_models(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "before_model_validator.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel, model_validator

app = FastAPI()

class SyncRequest(BaseModel):
    value: str

    @model_validator(mode="before")
    @classmethod
    def translate(cls, data):
        return {"value": data["vault_key"]}

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "SyncRequest" in violation and "before model validator" in violation
        for violation in violations
    )


def test_A4_guard_propagates_route_owners_from_app_factories(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "app_factory.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

def create_app():
    api = FastAPI()
    return api

app = create_app()

@app.post("/api/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_inspects_class_based_http_middleware(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "class_middleware.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI()

class InspectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        payload = await request.json()
        _ = payload["vault_key"]
        return await call_next(request)

app.add_middleware(InspectMiddleware)
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_rejects_wrap_model_validators_on_request_models(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "wrap_model_validator.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel, model_validator

app = FastAPI()

class SyncRequest(BaseModel):
    value: str

    @model_validator(mode="wrap")
    @classmethod
    def translate(cls, data, handler):
        return handler({"value": data["vault_key"]})

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "SyncRequest" in violation and "wrap model validator" in violation
        for violation in violations
    )


def test_A4_guard_resolves_callable_dependency_instances(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "callable_dependency.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Depends, FastAPI

app = FastAPI()

class SecretDependency:
    def __call__(self, vault_key: str) -> bool:
        return bool(vault_key)

@app.post("/api/encrypted-sync")
def sync(allowed: bool = Depends(SecretDependency())) -> dict[str, bool]:
    return {"ok": allowed}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_resolves_named_functional_typed_dict_fields(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "named_typed_dict_fields.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import TypedDict
from fastapi import FastAPI

app = FastAPI()
FIELDS = {"vault_key": str}
SyncRequest = TypedDict("SyncRequest", FIELDS)

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_rejects_nested_unconstrained_request_model_mappings(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "nested_mapping_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class SyncRequest(BaseModel):
    payload: dict[str, str]

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "SyncRequest.payload" in violation and "mapping" in violation
        for violation in violations
    )


def test_A4_guard_recognizes_fastapi_trace_routes(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "trace_route.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()

@app.trace("/api/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_recognizes_application_router_registrations(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "application_router.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()

def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}

app.router.add_api_route("/api/encrypted-sync", sync, methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_inspects_exception_handlers_that_read_request_bodies(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "exception_handler.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

app = FastAPI()

@app.exception_handler(404)
async def not_found(request: Request, exc):
    payload = await request.json()
    _ = payload["vault_key"]
    return JSONResponse({"ok": False}, status_code=404)
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_follows_imported_application_factories(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "factory.py").write_text(
        """
from fastapi import FastAPI

def create_app():
    return FastAPI()
""",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from services.factory import create_app

app = create_app()

@app.post("/api/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_rejects_pre_root_validators_on_request_models(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "pre_root_validator.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel, root_validator

app = FastAPI()

class SyncRequest(BaseModel):
    value: str

    @root_validator(pre=True)
    def translate(cls, values):
        return {"value": values.pop("vault_key")}

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "SyncRequest" in violation and "pre root validator" in violation
        for violation in violations
    )


def test_A4_guard_resolves_constant_raw_body_keys(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "constant_body_keys.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request

app = FastAPI()
WIRE_KEY = "vault_key"

@app.post("/api/encrypted-sync/module")
async def module_key(request: Request) -> dict[str, bool]:
    payload = await request.json()
    _ = payload[WIRE_KEY]
    return {"ok": True}

@app.post("/api/encrypted-sync/local")
async def local_key(request: Request) -> dict[str, bool]:
    local_key = "recovery_key"
    payload = await request.json()
    _ = payload.get(local_key)
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_indexes_class_endpoint_models_and_dependencies(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "class_endpoint_contracts.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Depends, FastAPI
from pydantic import BaseModel

app = FastAPI()

class SecretRequest(BaseModel):
    vault_key: str

class SecretDependency:
    def __call__(self, recovery_key: str) -> bool:
        return bool(recovery_key)

class SyncEndpoint:
    def __call__(
        self,
        body: SecretRequest,
        allowed: bool = Depends(SecretDependency()),
    ) -> dict[str, bool]:
        return {"ok": allowed}

app.add_api_route("/api/encrypted-sync", SyncEndpoint(), methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_ignores_nested_returns_during_app_factory_discovery(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "nested_factory_return.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

class LocalCallbacks:
    def post(self, name):
        def register(callback):
            return callback
        return register

def build_callbacks():
    def unused_app_factory():
        return FastAPI()
    return LocalCallbacks()

callbacks = build_callbacks()

@callbacks.post("local")
def local_callback(vault_key: str) -> str:
    return vault_key
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_resolves_named_framework_dependency_lists(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "named_dependencies.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Depends, FastAPI

def load_app_secret(vault_key: str) -> None:
    pass

def load_route_secret(recovery_key: str) -> None:
    pass

APP_DEPENDENCIES = [Depends(load_app_secret)]
ROUTE_DEPENDENCIES = (Depends(load_route_secret),)

app = FastAPI(dependencies=APP_DEPENDENCIES)

@app.post("/api/encrypted-sync", dependencies=ROUTE_DEPENDENCIES)
def sync() -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_resolves_imported_framework_subclasses(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "framework.py").write_text(
        """
from fastapi import FastAPI

class ServiceApp(FastAPI):
    pass
""",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from services.framework import ServiceApp

app = ServiceApp()

@app.post("/api/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_resolves_imported_wire_name_constants(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "wire_names.py").write_text(
        """
VAULT_KEY = "vault_key"
RECOVERY_KEY = "recovery_key"
""",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from fastapi import Body, FastAPI, Request

from services.wire_names import RECOVERY_KEY, VAULT_KEY

app = FastAPI()

@app.post("/api/encrypted-sync/body")
def body(value: str = Body(alias=VAULT_KEY)) -> dict[str, bool]:
    return {"ok": bool(value)}

@app.post("/api/encrypted-sync/raw")
async def raw(request: Request) -> dict[str, bool]:
    payload = await request.json()
    return {"ok": bool(payload[RECOVERY_KEY])}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_scopes_route_owners_to_visible_assignments(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "reassigned_owner.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

class LocalCallbacks:
    def post(self, name):
        def register(callback):
            return callback
        return register

def create_app():
    app = FastAPI()
    return app

app = LocalCallbacks()

@app.post("local")
def local_callback(vault_key: str) -> str:
    return vault_key
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_detects_pydantic_v1_config_field_aliases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "pydantic_v1_config_fields.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class SyncRequest(BaseModel):
    encrypted_payload: str

    class Config:
        fields = {"encrypted_payload": {"alias": "vault_key"}}

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": bool(request.encrypted_payload)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_preserves_local_shadowing_of_imported_wire_constants(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "wire_names.py").write_text(
        'KEY = "vault_key"\n',
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from fastapi import Body, FastAPI

from services.wire_names import KEY

KEY = "encrypted_payload"
app = FastAPI()

@app.post("/api/encrypted-sync")
def sync(value: str = Body(alias=KEY)) -> dict[str, bool]:
    return {"ok": bool(value)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert violations == []


def test_A4_guard_considers_conditional_route_owner_assignments(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "conditional_owner.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

class LocalCallbacks:
    def post(self, name):
        def register(callback):
            return callback
        return register

if use_fastapi:
    app = FastAPI()
else:
    app = LocalCallbacks()

@app.post("/api/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": bool(vault_key)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_resolves_named_pydantic_v1_config_field_maps(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "named_config_fields.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

ALIASES = {"encrypted_payload": {"alias": "vault_key"}}
app = FastAPI()

class SyncRequest(BaseModel):
    encrypted_payload: str

    class Config:
        fields = ALIASES

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": bool(request.encrypted_payload)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_matches_qualified_framework_classes_by_identity(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "framework.py").write_text(
        """
from fastapi import FastAPI

class ServiceApp(FastAPI):
    pass
""",
        encoding="utf-8",
    )
    (service_root / "callbacks.py").write_text(
        """
class ServiceApp:
    def post(self, name):
        def register(callback):
            return callback
        return register
""",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from services import callbacks
from services.framework import ServiceApp

class Local(callbacks.ServiceApp):
    pass

app = Local()

@app.post("local")
def local_callback(vault_key: str) -> str:
    return vault_key
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert violations == []


def test_A4_guard_resolves_route_decorator_method_aliases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "decorator_alias.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()
post = app.post

@post("/api/encrypted-sync")
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": bool(vault_key)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_resolves_named_pydantic_model_configurations(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "named_model_config.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

CONFIG = ConfigDict(extra="allow")
app = FastAPI()

class SyncRequest(BaseModel):
    model_config = CONFIG
    encrypted_payload: str

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": bool(request.encrypted_payload)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "SyncRequest" in violation and "extra wire fields" in violation
        for violation in violations
    )


def test_A4_guard_keeps_consumed_untyped_bodies_guarded_after_validation(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "ignored_validation.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import Annotated, Any

from fastapi import Body, FastAPI
from pydantic import BaseModel

app = FastAPI()

class SafeRequest(BaseModel):
    encrypted_payload: str

@app.post("/api/encrypted-sync")
def sync(payload: Annotated[Any, Body()]) -> dict[str, bool]:
    SafeRequest.model_validate(payload)
    return {"ok": bool(payload["vault_key"])}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "sync.payload" in violation and "unconstrained mapping" in violation
        for violation in violations
    )


def test_A4_guard_inspects_unpacked_create_model_fields(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "unpacked_created_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI
from pydantic import create_model

FIELDS = {"vault_key": (str, ...)}
SyncRequest = create_model("SyncRequest", **FIELDS)
app = FastAPI()

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": True}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("SyncRequest.vault_key" in violation for violation in violations)


def test_A4_guard_inspects_pydantic_dataclass_configurations(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "pydantic_dataclass_config.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import pydantic.dataclasses as pydantic_dataclasses
from fastapi import FastAPI
from pydantic import ConfigDict

CONFIG = ConfigDict(extra="allow")
app = FastAPI()

@pydantic_dataclasses.dataclass(config=CONFIG)
class SyncRequest:
    encrypted_payload: str

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": bool(request.encrypted_payload)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "SyncRequest" in violation and "extra wire fields" in violation
        for violation in violations
    )


def test_A4_guard_resolves_inferred_annotated_dependencies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "inferred_dependency.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import Annotated

from fastapi import Depends, FastAPI

app = FastAPI()

class SecretDependency:
    def __init__(self, vault_key: str):
        self.vault_key = vault_key

@app.post("/api/encrypted-sync")
def sync(dependency: Annotated[SecretDependency, Depends()]) -> dict[str, bool]:
    return {"ok": bool(dependency.vault_key)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("__init__.vault_key" in violation for violation in violations)


def test_A4_guard_follows_reexported_request_types(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "request_types.py").write_text(
        "from fastapi import Request\n",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from fastapi import FastAPI

from services.request_types import Request

app = FastAPI()

@app.post("/api/encrypted-sync")
async def sync(req: Request) -> dict[str, bool]:
    payload = await req.json()
    return {"ok": bool(payload["vault_key"])}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_accepts_mappings_constrained_to_safe_literal_keys(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "literal_mapping.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class SyncRequest(BaseModel):
    values: dict[Literal["ciphertext"], str]

@app.post("/api/encrypted-sync")
def sync(
    payload: dict[Literal["ciphertext"], str],
    request: SyncRequest,
) -> dict[str, bool]:
    return {"ok": bool(payload) and bool(request.values)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_inspects_multivalue_request_mapping_accessors(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "multivalue_request.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/api/encrypted-sync")
async def sync(request: Request) -> dict[str, bool]:
    query_values = request.query_params.getlist("vault_key")
    form_values = (await request.form()).getlist("recovery_key")
    return {"ok": bool(query_values) and bool(form_values)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_inspects_dependency_override_callables(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "dependency_overrides.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Depends, FastAPI

app = FastAPI()

def safe_dependency() -> bool:
    return True

def secret_dependency(vault_key: str) -> bool:
    return bool(vault_key)

@app.post("/api/encrypted-sync")
def sync(allowed: bool = Depends(safe_dependency)) -> dict[str, bool]:
    return {"ok": allowed}

app.dependency_overrides[safe_dependency] = secret_dependency
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("secret_dependency.vault_key" in violation for violation in violations)


def test_A4_guard_follows_reexported_websocket_types(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "socket_types.py").write_text(
        "from fastapi import WebSocket\n",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from fastapi import FastAPI

from services.socket_types import WebSocket

app = FastAPI()

@app.websocket("/api/encrypted-sync/socket")
async def sync(socket: WebSocket) -> None:
    payload = await socket.receive_json()
    _ = payload["vault_key"]
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_tracks_alternate_raw_body_decoders(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "alternate_decoder.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import orjson
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/api/encrypted-sync")
async def sync(request: Request) -> dict[str, bool]:
    payload = orjson.loads(await request.body())
    return {"ok": bool(payload["vault_key"])}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_inspects_starlette_endpoint_classes(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "class_endpoint.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from starlette.endpoints import HTTPEndpoint
from starlette.routing import Route

class SyncEndpoint(HTTPEndpoint):
    async def post(self, request):
        payload = await request.json()
        return payload["vault_key"]

routes = [Route("/api/encrypted-sync", SyncEndpoint)]
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_inspects_inline_lambda_route_handlers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "lambda_handler.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI

app = FastAPI()
app.add_api_route(
    "/api/encrypted-sync",
    lambda vault_key: vault_key,
    methods=["POST"],
)
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("lambda.vault_key" in violation for violation in violations)


def test_A4_guard_inspects_every_mapping_branch_in_unions(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "union_mapping.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from typing import Literal

from fastapi import FastAPI

app = FastAPI()

@app.post("/api/encrypted-sync")
def sync(
    payload: dict[str, str] | dict[Literal["ciphertext"], str],
) -> dict[str, bool]:
    return {"ok": bool(payload)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync.payload" in violation for violation in violations)


def test_A4_guard_follows_imported_dependency_lists(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "dependencies.py").write_text(
        """
from fastapi import Depends

def load_secret(vault_key: str) -> None:
    pass

COMMON_DEPENDENCIES = [Depends(load_secret)]
""",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from fastapi import FastAPI

from services.dependencies import COMMON_DEPENDENCIES

app = FastAPI(dependencies=COMMON_DEPENDENCIES)
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("load_secret.vault_key" in violation for violation in violations)


def test_A4_guard_resolves_imported_pydantic_configurations(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "model_config.py").write_text(
        """
from pydantic import ConfigDict

REQUEST_CONFIG = ConfigDict(extra="allow")
""",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

from services.model_config import REQUEST_CONFIG

app = FastAPI()

class SyncRequest(BaseModel):
    model_config = REQUEST_CONFIG
    encrypted_payload: str

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": bool(request.encrypted_payload)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any(
        "SyncRequest" in violation and "extra wire fields" in violation
        for violation in violations
    )


def test_A4_guard_follows_request_objects_into_helpers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "request_helper.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request

app = FastAPI()

async def parse_payload(inbound):
    payload = await inbound.json()
    return payload["vault_key"]

@app.post("/api/encrypted-sync")
async def sync(request: Request) -> dict[str, bool]:
    return {"ok": bool(await parse_payload(request))}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_tracks_streamed_request_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "streamed_body.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import json
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/api/encrypted-sync")
async def sync(request: Request) -> dict[str, bool]:
    chunks = [chunk async for chunk in request.stream()]
    body = b"".join(chunks)
    payload = json.loads(body)
    return {"ok": bool(payload["vault_key"])}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_rejects_pydantic_dataclass_alias_generators(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "dataclass_alias_generator.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import pydantic.dataclasses as pydantic_dataclasses
from fastapi import FastAPI
from pydantic import ConfigDict

app = FastAPI()

@pydantic_dataclasses.dataclass(
    config=ConfigDict(alias_generator=lambda _: "vault_key"),
)
class SyncRequest:
    safe: str

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": bool(request.safe)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any(
        "SyncRequest" in violation and "alias generator" in violation
        for violation in violations
    )


def test_A4_guard_propagates_local_request_type_aliases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "local_request_alias.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request

HTTP_REQUEST = Request
app = FastAPI()

@app.post("/api/encrypted-sync")
async def sync(req: HTTP_REQUEST) -> dict[str, bool]:
    payload = await req.json()
    return {"ok": bool(payload["vault_key"])}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_resolves_indirect_imported_pydantic_configurations(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "model_config.py").write_text(
        """
from pydantic import ConfigDict

BASE_CONFIG = ConfigDict(extra="allow")
REQUEST_CONFIG = BASE_CONFIG
""",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from fastapi import FastAPI
from pydantic import BaseModel

from services.model_config import REQUEST_CONFIG

app = FastAPI()

class SyncRequest(BaseModel):
    model_config = REQUEST_CONFIG
    encrypted_payload: str

@app.post("/api/encrypted-sync")
def sync(request: SyncRequest) -> dict[str, bool]:
    return {"ok": bool(request.encrypted_payload)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any(
        "SyncRequest" in violation and "extra wire fields" in violation
        for violation in violations
    )


def test_A4_guard_propagates_requests_into_bound_methods(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "bound_request_helper.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request

app = FastAPI()

class Parser:
    async def parse(self, inbound):
        payload = await inbound.json()
        return payload["vault_key"]

parser = Parser()

@app.post("/api/encrypted-sync")
async def sync(request: Request) -> dict[str, bool]:
    return {"ok": bool(await parser.parse(request))}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_follows_reexported_dependency_lists(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "dependencies.py").write_text(
        """
from fastapi import Depends

def load_secret(vault_key: str) -> None:
    pass

COMMON_DEPENDENCIES = [Depends(load_secret)]
""",
        encoding="utf-8",
    )
    (service_root / "shared.py").write_text(
        "from services.dependencies import COMMON_DEPENDENCIES\n",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from fastapi import FastAPI

from services.shared import COMMON_DEPENDENCIES

app = FastAPI(dependencies=COMMON_DEPENDENCIES)
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("load_secret.vault_key" in violation for violation in violations)


def test_A4_guard_unwraps_partial_route_handlers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "partial_handler.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from functools import partial

from fastapi import FastAPI

app = FastAPI()

def sync(prefix: str, vault_key: str) -> dict[str, bool]:
    return {"ok": bool(prefix) and bool(vault_key)}

def fully_bound(vault_key: str) -> dict[str, bool]:
    return {"ok": bool(vault_key)}

handler = partial(sync, "fixed")
safe_handler = partial(fully_bound, "internal")
app.add_api_route("/api/encrypted-sync", handler, methods=["POST"])
app.add_api_route("/api/internal", safe_handler, methods=["POST"])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("sync.vault_key" in violation for violation in violations)
    assert not any("fully_bound.vault_key" in violation for violation in violations)


def test_A4_guard_invalidates_reassigned_request_aliases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "reassigned_request_alias.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import FastAPI, Request

app = FastAPI()

class LocalPayload:
    async def json(self):
        return {"vault_key": "internal-test-value"}

async def parse_payload(inbound):
    payload = await inbound.json()
    return payload["vault_key"]

@app.post("/api/encrypted-sync")
async def sync(request: Request) -> dict[str, bool]:
    inbound = request
    inbound = LocalPayload()
    return {"ok": bool(await parse_payload(inbound))}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert violations == []


def test_A4_guard_inspects_direct_route_constructor_dependencies(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "route_dependencies.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute

def load_secret(vault_key: str) -> None:
    pass

def sync() -> dict[str, bool]:
    return {"ok": True}

route = APIRoute(
    "/api/encrypted-sync",
    sync,
    methods=["POST"],
    dependencies=[Depends(load_secret)],
)
app = FastAPI(routes=[route])
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("load_secret.vault_key" in violation for violation in violations)


def test_A4_guard_resolves_imported_functional_typed_dict_fields(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "fields.py").write_text(
        'FIELDS = {"vault_key": str}\n',
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        """
from typing import TypedDict

from fastapi import FastAPI

from services.fields import FIELDS

Payload = TypedDict("Payload", FIELDS)
app = FastAPI()

@app.post("/api/encrypted-sync")
def sync(payload: Payload) -> dict[str, bool]:
    return {"ok": bool(payload)}
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_root)

    assert any("Payload.vault_key" in violation for violation in violations)


def test_A4_guard_tracks_low_level_websocket_receive_results(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "websocket_receive.py"
    service_file.parent.mkdir()
    service_file.write_text(
        """
import json
from fastapi import FastAPI, WebSocket

app = FastAPI()

@app.websocket("/api/encrypted-sync/socket")
async def sync(socket: WebSocket) -> None:
    message = await socket.receive()
    payload = json.loads(message["text"])
    _ = payload["vault_key"]
""",
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)


def test_A4_guard_keeps_keyword_bound_partial_parameters(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "partial_keyword.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from functools import partial
from fastapi import FastAPI
app = FastAPI()
def sync(vault_key: str) -> dict[str, bool]:
    return {"ok": bool(vault_key)}
app.add_api_route("/sync", partial(sync, vault_key="internal"), methods=["POST"])
''',
        encoding="utf-8",
    )
    assert any(
        "sync.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_preserves_conditional_request_aliases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "conditional_alias.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
async def parse_payload(inbound):
    return (await inbound.json())["vault_key"]
@app.post("/sync")
async def sync(request: Request, use_local: bool):
    inbound = request
    if use_local:
        inbound = object()
    return await parse_payload(inbound)
''',
        encoding="utf-8",
    )
    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_traverses_imported_typed_dict_value_types(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "models.py").write_text(
        'from pydantic import BaseModel\nclass SecretInput(BaseModel):\n    vault_key: str\nFIELDS = {"payload": SecretInput}\n',
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        '''
from typing import TypedDict
from fastapi import FastAPI
from services.models import FIELDS
Payload = TypedDict("Payload", FIELDS)
app = FastAPI()
@app.post("/sync")
def sync(payload: Payload):
    return bool(payload)
''',
        encoding="utf-8",
    )
    assert any(
        "SecretInput.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_root)
    )


def test_A4_guard_inspects_websocket_connection_mappings(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "socket_query.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, WebSocket
app = FastAPI()
@app.websocket("/sync")
async def sync(socket: WebSocket):
    _ = socket.query_params["vault_key"]
''',
        encoding="utf-8",
    )
    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_rejects_wildcard_service_imports(tmp_path: Path) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "__init__.py").write_text("", encoding="utf-8")
    (service_root / "models.py").write_text(
        "from pydantic import BaseModel\nclass Payload(BaseModel):\n    vault_key: str\n",
        encoding="utf-8",
    )
    (service_root / "api.py").write_text(
        'from fastapi import FastAPI\nfrom services.models import *\napp = FastAPI()\n@app.post("/sync")\ndef sync(payload: Payload): return bool(payload)\n',
        encoding="utf-8",
    )
    assert any(
        "wildcard import" in violation
        for violation in find_raw_secret_wire_contract_violations(service_root)
    )


def test_A4_guard_rejects_opaque_wire_aliases(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "opaque_alias.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Query
app = FastAPI()
def wire_name(): return "vault_key"
@app.post("/sync")
def sync(value: str = Query(alias=wire_name())): return bool(value)
''',
        encoding="utf-8",
    )
    assert any(
        "raw vault secret" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_rejects_signature_changing_route_decorators(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "decorated_route.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
def expose_secret(function):
    def wrapper(vault_key: str): return function()
    return wrapper
@app.post("/sync")
@expose_secret
def sync(): return True
''',
        encoding="utf-8",
    )
    assert any(
        "decorated route signature" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_rejects_unresolved_mounted_asgi_callables(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "mounted_asgi.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
async def raw_app(scope, receive, send):
    _ = scope["query_string"]
app.mount("/raw", raw_app)
''',
        encoding="utf-8",
    )
    assert any(
        "mounted ASGI callable" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_scans_programmatic_lambda_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "lambda_body.py"
    service_file.parent.mkdir()
    service_file.write_text(
        'from starlette.applications import Starlette\nfrom starlette.routing import Route\napp = Starlette(routes=[Route("/sync", lambda request: request.query_params["vault_key"])])\n',
        encoding="utf-8",
    )
    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_propagates_helper_returned_raw_mappings(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "helper_return.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
async def parse(request): return await request.json()
@app.post("/sync")
async def sync(request: Request):
    payload = await parse(request)
    return payload["vault_key"]
''',
        encoding="utf-8",
    )
    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_preserves_qualified_route_owner_targets(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "qualified_owner.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from types import SimpleNamespace
from fastapi import FastAPI
state = SimpleNamespace()
state.app = FastAPI()
@state.app.post("/sync")
def sync(vault_key: str): return bool(vault_key)
''',
        encoding="utf-8",
    )
    assert any(
        "sync.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_inline_dependency_lambdas(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "lambda_dependency.py"
    service_file.parent.mkdir()
    service_file.write_text(
        'from fastapi import Depends, FastAPI\napp = FastAPI()\n@app.post("/sync")\ndef sync(value=Depends(lambda vault_key: vault_key)): return bool(value)\n',
        encoding="utf-8",
    )
    assert any(
        "lambda.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_rejects_unapproved_asgi_middleware(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "asgi_middleware.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
class SecretMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        _ = scope["query_string"]
        await self.app(scope, receive, send)
app.add_middleware(SecretMiddleware)
''',
        encoding="utf-8",
    )
    assert any(
        "ASGI middleware" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_propagates_asgi_receive_into_helpers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "asgi_receive_helper.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
async def parse(receive):
    payload = await receive()
    return payload["vault_key"]
class SecretMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        _ = await parse(receive)
        await self.app(scope, receive, send)
app.add_middleware(SecretMiddleware)
''',
        encoding="utf-8",
    )
    assert any(
        "ASGI middleware" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_propagates_only_request_derived_helper_returns(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "safe_helper_return.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
def build_context(request):
    _ = request.url.path
    return {"vault_key": "internal-only"}
@app.post("/sync")
async def sync(request: Request):
    context = build_context(request)
    return context["vault_key"]
''',
        encoding="utf-8",
    )
    assert find_raw_secret_wire_contract_violations(service_file.parent) == []


def test_A4_guard_allows_decorators_applied_after_route_registration(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "post_registration_decorator.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
def audit(function): return function
@audit
@app.get("/health")
def health(): return {"ok": True}
''',
        encoding="utf-8",
    )
    assert find_raw_secret_wire_contract_violations(service_file.parent) == []


def test_A4_guard_rejects_route_template_secret_placeholders(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "route_templates.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.get("/sync/{vault_key}")
def decorated(request: Request): return request.url.path
def programmatic(request: Request): return request.url.path
app.add_api_route("/other/{recovery_key:path}", programmatic)
''',
        encoding="utf-8",
    )
    violations = find_raw_secret_wire_contract_violations(service_file.parent)
    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_rejects_composed_router_prefix_secret_placeholders(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "router_prefixes.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import APIRouter, FastAPI
app = FastAPI()
secret_router = APIRouter(prefix="/{vault_key}")
included_router = APIRouter()
@secret_router.get("/sync")
def prefixed(): return True
@included_router.get("/sync")
def included(): return True
app.include_router(secret_router)
app.include_router(included_router, prefix="/{recovery_key}")
''',
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_binds_asgi_receive_for_instance_helpers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "bound_asgi_helper.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
class SecretMiddleware:
    def __init__(self, app): self.app = app
    async def parse(self, inbound):
        payload = await inbound()
        return payload["vault_key"]
    async def __call__(self, scope, receive, send):
        _ = await self.parse(receive)
        await self.app(scope, receive, send)
app.add_middleware(SecretMiddleware)
''',
        encoding="utf-8",
    )

    assert any(
        "ASGI middleware" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_binds_request_returns_for_instance_helpers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "bound_request_helper.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
class Parser:
    async def parse(self, inbound):
        return await inbound.json()
parser = Parser()
@app.post("/sync")
async def sync(request: Request):
    payload = await parser.parse(request)
    return payload["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_allows_internal_secret_literals_in_pass_through_asgi_middleware(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "internal_asgi_literal.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
class InternalMiddleware:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        internal = {"vault_key": "server-owned"}
        assert internal["vault_key"]
        await self.app(scope, receive, send)
app.add_middleware(InternalMiddleware)
''',
        encoding="utf-8",
    )

    assert find_raw_secret_wire_contract_violations(service_file.parent) == []


def test_A4_guard_inspects_functional_dataclass_request_models(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "functional_dataclass.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from dataclasses import make_dataclass
from fastapi import FastAPI
app = FastAPI()
Payload = make_dataclass("Payload", [("vault_key", str)])
@app.post("/sync")
def sync(payload: Payload): return bool(payload)
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_positional_framework_request_callbacks(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "positional_request_callbacks.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
@app.middleware("http")
async def inspect(req, call_next):
    _ = req.query_params["vault_key"]
    return await call_next(req)
@app.exception_handler(404)
async def errors(req, exc):
    _ = req.query_params["recovery_key"]
    return {"ok": False}
''',
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_preserves_receivers_for_class_qualified_helper_calls(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "unbound_request_helper.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
class Parser:
    async def parse(self, inbound):
        return await inbound.json()
parser = Parser()
@app.post("/sync")
async def sync(request: Request):
    payload = await Parser.parse(parser, request)
    return payload["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_resolves_aliased_functional_dataclass_fields(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "aliased_functional_dataclass.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from dataclasses import dataclass, make_dataclass
from fastapi import FastAPI
app = FastAPI()
@dataclass
class SecretInput:
    vault_key: str
NESTED = ("payload", SecretInput)
Payload = make_dataclass("Payload", [NESTED])
@app.post("/sync")
def sync(payload: Payload): return bool(payload)
''',
        encoding="utf-8",
    )

    assert any(
        "SecretInput.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_programmatic_exception_handler_requests(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "registered_exception_handler.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
async def errors(req, exc):
    _ = req.query_params["vault_key"]
    return {"ok": False}
app.add_exception_handler(404, errors)
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_unwraps_partial_dependency_callables(tmp_path: Path) -> None:
    bad_root = tmp_path / "bad" / "services"
    bad_root.mkdir(parents=True)
    (bad_root / "partial_dependency.py").write_text(
        '''
from functools import partial
from fastapi import Depends, FastAPI
app = FastAPI()
def secret(prefix, vault_key): return prefix + vault_key
@app.get("/sync")
def sync(value=Depends(partial(secret, "prefix"))): return bool(value)
''',
        encoding="utf-8",
    )
    safe_root = tmp_path / "safe" / "services"
    safe_root.mkdir(parents=True)
    (safe_root / "partial_dependency.py").write_text(
        '''
from functools import partial
from fastapi import Depends, FastAPI
app = FastAPI()
def secret(vault_key): return bool(vault_key)
@app.get("/sync")
def sync(value=Depends(partial(secret, "internal"))): return bool(value)
''',
        encoding="utf-8",
    )

    assert any(
        "secret.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(bad_root)
    )
    assert find_raw_secret_wire_contract_violations(safe_root) == []


def test_A4_guard_unwraps_bound_method_partial_dependencies(tmp_path: Path) -> None:
    bad_root = tmp_path / "bad" / "services"
    bad_root.mkdir(parents=True)
    (bad_root / "bound_partial_dependency.py").write_text(
        '''
from functools import partial
from fastapi import Depends, FastAPI
app = FastAPI()
class Parser:
    def secret(self, prefix, vault_key): return prefix + vault_key
parser = Parser()
@app.get("/sync")
def sync(value=Depends(partial(parser.secret, "prefix"))): return bool(value)
''',
        encoding="utf-8",
    )
    safe_root = tmp_path / "safe" / "services"
    safe_root.mkdir(parents=True)
    (safe_root / "bound_partial_dependency.py").write_text(
        '''
from functools import partial
from fastapi import Depends, FastAPI
app = FastAPI()
class Parser:
    def secret(self, prefix, vault_key): return prefix + vault_key
parser = Parser()
@app.get("/sync")
def sync(value=Depends(partial(parser.secret, "prefix", "internal"))):
    return bool(value)
''',
        encoding="utf-8",
    )

    assert any(
        "secret.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(bad_root)
    )
    assert find_raw_secret_wire_contract_violations(safe_root) == []


def test_A4_guard_tracks_wrappers_created_from_request_scope(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "request_scope.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
from starlette.datastructures import Headers
app = FastAPI()
@app.get("/sync")
def sync(request: Request):
    headers = Headers(scope=request.scope)
    return headers["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_non_loads_request_body_decoders(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "form_decoder.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from urllib.parse import parse_qs
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    payload = parse_qs((await request.body()).decode())
    return payload["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_rejects_unapproved_custom_route_classes(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "custom_route_class.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import APIRouter, FastAPI, Request
from fastapi.routing import APIRoute
class SecretRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request: Request):
            _ = request.query_params["vault_key"]
            return await original(request)
        return handler
router = APIRouter(route_class=SecretRoute)
@router.get("/sync")
def sync(): return {"ok": True}
app = FastAPI()
app.include_router(router)
''',
        encoding="utf-8",
    )

    assert any(
        "route_class" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_seeds_decoded_websocket_endpoint_payloads(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "websocket_endpoint.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from starlette.applications import Starlette
from starlette.endpoints import WebSocketEndpoint
from starlette.routing import WebSocketRoute
class SyncEndpoint(WebSocketEndpoint):
    encoding = "json"
    async def on_receive(self, websocket, data):
        _ = data["vault_key"]
routes = [WebSocketRoute("/sync", SyncEndpoint)]
app = Starlette(routes=routes)
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_rejects_route_classes_assigned_after_construction(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "assigned_route_class.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
class SecretRoute(APIRoute): pass
router = APIRouter()
router.route_class = SecretRoute
@router.get("/sync")
def sync(): return {"ok": True}
app = FastAPI()
app.include_router(router)
''',
        encoding="utf-8",
    )

    assert any(
        "route_class" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_rejects_per_route_class_overrides(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "route_class_override.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
class SecretRoute(APIRoute): pass
router = APIRouter()
def sync(): return {"ok": True}
router.add_api_route(
    "/sync",
    sync,
    methods=["GET"],
    route_class_override=SecretRoute,
)
app = FastAPI()
app.include_router(router)
''',
        encoding="utf-8",
    )

    assert any(
        "route_class_override" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_resolves_class_qualified_partial_dependencies(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "unbound_partial_dependency.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from functools import partial
from fastapi import Depends, FastAPI
app = FastAPI()
class Parser:
    def secret(self, prefix, vault_key): return prefix + vault_key
parser = Parser()
@app.get("/sync")
def sync(value=Depends(partial(Parser.secret, parser, "prefix"))):
    return bool(value)
''',
        encoding="utf-8",
    )

    assert any(
        "secret.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_does_not_taint_safe_body_consuming_helper_returns(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "safe_body_consumer.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
def internal_context(raw_body):
    _ = len(raw_body)
    return {"vault_key": "server-owned"}
@app.post("/sync")
async def sync(request: Request):
    context = internal_context(await request.body())
    return context["vault_key"]
''',
        encoding="utf-8",
    )

    assert find_raw_secret_wire_contract_violations(service_file.parent) == []


def test_A4_guard_tracks_explicit_typed_body_parameters(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "typed_body.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import json
from fastapi import Body, FastAPI
app = FastAPI()
@app.post("/sync")
def sync(payload: bytes = Body()):
    return json.loads(payload)["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_explicit_uploaded_file_contents(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "uploaded_body.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import json
from fastapi import FastAPI, File, UploadFile
app = FastAPI()
@app.post("/sync")
async def sync(payload: UploadFile = File()):
    return json.loads(await payload.read())["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_propagates_explicit_body_inputs_through_parsing_helpers(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "body_helper.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import json
from fastapi import Body, FastAPI
app = FastAPI()
def parse(raw):
    return json.loads(raw)
@app.post("/sync")
def sync(payload: bytes = Body()):
    decoded = parse(payload)
    return decoded["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_upload_file_handle_reads(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "uploaded_file_handle.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import json
from fastapi import FastAPI, File, UploadFile
app = FastAPI()
@app.post("/sync")
def sync(payload: UploadFile = File()):
    return json.loads(payload.file.read())["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_resolves_programmatic_route_registrar_aliases(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "aliased_registrar.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
def sync(vault_key: str):
    return {"ok": bool(vault_key)}
register = app.add_api_route
register("/sync", sync, methods=["POST"])
''',
        encoding="utf-8",
    )

    assert any(
        "sync.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_preserves_request_provenance_through_mapping_copies(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "request_mapping_copies.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.get("/sync")
def sync(request: Request):
    fields = {**request.query_params}
    copied = {
        key: value
        for key, value in request.headers.items()
    }
    return fields["vault_key"], copied["recovery_key"]
''',
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_inspects_dependency_override_mapping_updates(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "dependency_override_update.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import Depends, FastAPI
app = FastAPI()
def safe_dependency():
    return True
def secret_dependency(vault_key: str):
    return bool(vault_key)
@app.post("/sync")
def sync(allowed: bool = Depends(safe_dependency)):
    return {"ok": allowed}
app.dependency_overrides.update({safe_dependency: secret_dependency})
''',
        encoding="utf-8",
    )

    assert any(
        "secret_dependency.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_route_templates_through_registrar_aliases(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "aliased_registrar_template.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
def sync():
    return {"ok": True}
register = app.add_api_route
register("/sync/{vault_key}", sync, methods=["POST"])
''',
        encoding="utf-8",
    )

    assert any(
        "route template field vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_dependency_override_lambdas(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "dependency_override_lambda.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import Depends, FastAPI
app = FastAPI()
def safe_dependency():
    return True
@app.post("/sync")
def sync(allowed: bool = Depends(safe_dependency)):
    return {"ok": allowed}
app.dependency_overrides.update(
    {safe_dependency: lambda vault_key: bool(vault_key)}
)
''',
        encoding="utf-8",
    )

    assert any(
        "lambda.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_preserves_query_provenance_through_multi_items(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "query_multi_items.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.get("/sync")
def sync(request: Request):
    fields = {
        key: value
        for key, value in request.query_params.multi_items()
    }
    return fields["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_raw_request_url_query_strings(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "url_query.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from urllib.parse import parse_qs
from fastapi import FastAPI, Request
app = FastAPI()
@app.get("/sync")
def sync(request: Request):
    fields = parse_qs(request.url.query)
    return fields["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_low_level_request_receive_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "request_receive.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import json
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    message = await request.receive()
    fields = json.loads(message["body"])
    return fields["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_websocket_iterator_payloads(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "websocket_iterators.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import json
from fastapi import FastAPI, WebSocket
app = FastAPI()
@app.websocket("/sync")
async def sync(websocket: WebSocket):
    async for payload in websocket.iter_json():
        _ = payload["vault_key"]
    async for text in websocket.iter_text():
        _ = json.loads(text)["recovery_key"]
    async for data in websocket.iter_bytes():
        _ = json.loads(data)["passphrase"]
''',
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)
    assert any("passphrase" in violation for violation in violations)


def test_A4_guard_inspects_framework_middleware_backends(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "authentication_middleware.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
from starlette.authentication import AuthenticationBackend
from starlette.middleware.authentication import AuthenticationMiddleware
class SecretBackend(AuthenticationBackend):
    async def authenticate(self, conn):
        return conn.headers["vault_key"]
app = FastAPI()
app.add_middleware(AuthenticationMiddleware, backend=SecretBackend())
''',
        encoding="utf-8",
    )

    assert any(
        "middleware backend" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_rejects_dynamic_raw_request_mapping_keys(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "dynamic_request_key.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import os
from fastapi import FastAPI, Request
app = FastAPI()
@app.get("/sync")
def sync(request: Request):
    field = os.environ["ATLAS_FIELD"]
    return request.query_params[field]
''',
        encoding="utf-8",
    )

    assert any(
        "dynamic request mapping key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_positional_framework_middleware_options(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "positional_authentication_backend.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
from starlette.authentication import AuthenticationBackend
from starlette.middleware.authentication import AuthenticationMiddleware
class SecretBackend(AuthenticationBackend):
    async def authenticate(self, conn):
        return conn.headers["vault_key"]
app = FastAPI()
app.add_middleware(AuthenticationMiddleware, SecretBackend())
''',
        encoding="utf-8",
    )

    assert any(
        "middleware positional option" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_request_receive_mapping_accessors(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "request_receive_get.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import json
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    message = await request.receive()
    body = message.get("body", b"{}")
    fields = json.loads(body)
    return fields["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_websocket_comprehension_payloads(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "websocket_comprehension.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, WebSocket
app = FastAPI()
@app.websocket("/sync")
async def sync(websocket: WebSocket):
    return [
        payload["vault_key"]
        async for payload in websocket.iter_json()
    ]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_allows_server_managed_request_scope_state(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "request_scope_state.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.get("/sync")
def sync(request: Request):
    return request.scope["state"]["vault_key"]
''',
        encoding="utf-8",
    )

    assert find_raw_secret_wire_contract_violations(service_file.parent) == []


def test_A4_guard_tracks_http_connection_request_mappings(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "http_connection.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
from starlette.requests import HTTPConnection
app = FastAPI()
@app.get("/sync")
def sync(conn: HTTPConnection):
    return conn.headers["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_allows_safe_positional_framework_middleware_options(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "gzip_middleware.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware
app = FastAPI()
app.add_middleware(GZipMiddleware, 1000)
''',
        encoding="utf-8",
    )

    assert find_raw_secret_wire_contract_violations(service_file.parent) == []


def test_A4_guard_filters_server_scope_aliases_and_mapping_accessors(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "scope_state_aliases.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.get("/sync")
def sync(request: Request):
    scope = request.scope
    direct_state = request.scope.get("state", {})
    aliased_state = scope.get("state", {})
    return direct_state["vault_key"], aliased_state["recovery_key"]
''',
        encoding="utf-8",
    )

    assert find_raw_secret_wire_contract_violations(service_file.parent) == []


def test_A4_guard_tracks_decoded_scope_query_strings(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "scope_query_string.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from urllib.parse import parse_qs
from fastapi import FastAPI, Request
app = FastAPI()
@app.get("/sync")
def sync(request: Request):
    payload = parse_qs(request.scope["query_string"].decode())
    return payload["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_rejects_unresolved_route_templates(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "dynamic_route_template.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import os
from fastapi import FastAPI
app = FastAPI()
PREFIX = os.getenv("API_PREFIX", "/{vault_key}")
@app.get(PREFIX)
def sync():
    return {"ok": True}
''',
        encoding="utf-8",
    )

    assert any(
        "route template" in violation and "statically approved" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_resolves_dependency_factories(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "dependency_factory.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import Depends, FastAPI
app = FastAPI()
def secret(vault_key: str):
    return bool(vault_key)
def select_dependency():
    return secret
@app.get("/sync")
def sync(value=Depends(select_dependency())):
    return {"ok": value}
''',
        encoding="utf-8",
    )

    assert any(
        "secret.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_keeps_nested_function_provenance_lexically_scoped(
    tmp_path: Path,
) -> None:
    safe_root = tmp_path / "safe" / "services"
    safe_root.mkdir(parents=True)
    (safe_root / "nested_shadow.py").write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    payload = await request.json()
    def internal_value():
        payload = {"vault_key": "server-owned"}
        return payload["vault_key"]
    return internal_value()
''',
        encoding="utf-8",
    )
    bad_root = tmp_path / "bad" / "services"
    bad_root.mkdir(parents=True)
    (bad_root / "nested_capture.py").write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    payload = await request.json()
    def captured_value():
        return payload["vault_key"]
    return captured_value()
''',
        encoding="utf-8",
    )

    assert find_raw_secret_wire_contract_violations(safe_root) == []
    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(bad_root)
    )


def test_A4_guard_propagates_parsed_payloads_into_consumer_helpers(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "payload_consumer.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
def consume(payload):
    return payload["vault_key"]
@app.post("/sync")
async def sync(request: Request):
    payload = await request.json()
    return consume(payload)
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_request_payloads_captured_by_lambdas(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "payload_lambda.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    payload = await request.json()
    pick = lambda: payload["vault_key"]
    return pick()
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_resolves_programmatic_route_wrapper_endpoints(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "route_wrapper.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
def secret(vault_key: str):
    return {"ok": bool(vault_key)}
def register(endpoint):
    app.add_api_route("/sync", endpoint, methods=["POST"])
register(secret)
''',
        encoding="utf-8",
    )

    assert any(
        "secret.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_request_payloads_stored_on_attributes(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "attribute_payload.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
class State:
    pass
state = State()
@app.post("/sync")
async def sync(request: Request):
    state.payload = await request.json()
    return state.payload["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_seeds_starlette_route_decorator_positional_requests(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "starlette_route.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from starlette.applications import Starlette
app = Starlette()
@app.route("/sync")
def sync(req):
    return req.query_params["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_constructor_installed_middleware(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "constructor_middleware.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
class SecretMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, req, call_next):
        _ = req.headers["vault_key"]
        return await call_next(req)
app = Starlette(middleware=[Middleware(SecretMiddleware)])
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_constructor_installed_exception_handlers(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "constructor_exception_handler.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from starlette.applications import Starlette
def handle(req, exc):
    return req.headers["vault_key"]
app = Starlette(exception_handlers={Exception: handle})
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_propagates_parsed_payloads_into_imported_helpers(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "services"
    service_root.mkdir()
    (service_root / "handlers.py").write_text(
        '''
def consume(payload):
    return payload["vault_key"]
''',
        encoding="utf-8",
    )
    (service_root / "routes.py").write_text(
        '''
from fastapi import FastAPI, Request
from .handlers import consume
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    payload = await request.json()
    return consume(payload)
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_root)
    )


def test_A4_guard_invalidates_reassigned_parsed_payloads(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "reassigned_payload.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    payload = await request.json()
    payload = {"vault_key": "server-owned"}
    return payload["vault_key"]
''',
        encoding="utf-8",
    )

    assert find_raw_secret_wire_contract_violations(service_file.parent) == []


def test_A4_guard_tracks_non_json_request_body_decoders(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "yaml_body.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import yaml
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    payload = yaml.safe_load(await request.body())
    return payload["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_keys_selected_while_iterating_request_mappings(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "iterated_headers.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    for key, value in request.headers.items():
        if key == "vault_key":
            return value
    return None
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_propagates_request_payloads_into_background_tasks(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "background_task.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import BackgroundTasks, FastAPI, Request
app = FastAPI()
def consume(payload):
    return payload["vault_key"]
@app.post("/sync")
async def sync(request: Request, tasks: BackgroundTasks):
    payload = await request.json()
    tasks.add_task(consume, payload)
    return {"ok": True}
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_models_validated_from_request_mappings(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "manual_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
from pydantic import BaseModel
app = FastAPI()
class Payload(BaseModel):
    vault_key: str
@app.post("/sync")
async def sync(request: Request):
    return Payload.model_validate(await request.json())
''',
        encoding="utf-8",
    )

    assert any(
        "Payload.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_decoded_asgi_headers(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "asgi_headers.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
class SecretMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        headers = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope["headers"]
        }
        _ = headers["vault_key"]
        await self.app(scope, receive, send)
app.add_middleware(SecretMiddleware)
''',
        encoding="utf-8",
    )

    assert any(
        "ASGI middleware" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_resolves_keyword_background_task_callbacks(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "keyword_background_task.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import BackgroundTasks, FastAPI, Request
app = FastAPI()
def consume(payload):
    return payload["vault_key"]
@app.post("/sync")
async def sync(request: Request, tasks: BackgroundTasks):
    payload = await request.json()
    tasks.add_task(func=consume, payload=payload)
    return {"ok": True}
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_models_validated_from_raw_request_json(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "manual_json_model.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
from pydantic import BaseModel
app = FastAPI()
class Payload(BaseModel):
    vault_key: str
@app.post("/sync")
async def sync(request: Request):
    return Payload.model_validate_json(await request.body())
''',
        encoding="utf-8",
    )

    assert any(
        "Payload.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_allows_numeric_indexes_into_raw_asgi_header_sequences(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "asgi_header_sequence.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
class HeaderMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        headers = list(scope["headers"])
        first = headers[0] if headers else None
        await self.app(scope, receive, send)
        return first
app.add_middleware(HeaderMiddleware)
''',
        encoding="utf-8",
    )

    assert find_raw_secret_wire_contract_violations(service_file.parent) == []


def test_A4_guard_checks_byte_keys_in_raw_asgi_header_mappings(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "asgi_byte_header_key.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
class HeaderMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        headers = dict(scope["headers"])
        _ = headers[b"vault_key"]
        await self.app(scope, receive, send)
app.add_middleware(HeaderMiddleware)
''',
        encoding="utf-8",
    )

    assert any(
        "ASGI middleware" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_raw_parser_keyword_payloads(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "raw_parser_keywords.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
from pydantic import BaseModel
app = FastAPI()
class FirstPayload(BaseModel):
    vault_key: str
class SecondPayload(BaseModel):
    recovery_key: str
@app.post("/sync")
async def sync(request: Request):
    first = FirstPayload.model_validate_json(json_data=await request.body())
    second = SecondPayload.parse_raw(b=await request.body())
    return first, second
''',
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("FirstPayload.vault_key" in violation for violation in violations)
    assert any("SecondPayload.recovery_key" in violation for violation in violations)


def test_A4_guard_tracks_header_wrappers_constructed_from_asgi_scope(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "asgi_header_wrapper.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
from starlette.datastructures import Headers
app = FastAPI()
class HeaderMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        headers = Headers(scope=scope)
        _ = headers["vault_key"]
        await self.app(scope, receive, send)
app.add_middleware(HeaderMiddleware)
''',
        encoding="utf-8",
    )

    assert any(
        "ASGI middleware" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_mapping_patterns_on_request_payloads(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "request_mapping_pattern.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    payload = await request.json()
    match payload:
        case {"vault_key": secret}:
            return secret
        case _:
            return None
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_whole_dependency_override_mappings(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "whole_dependency_overrides.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import Depends, FastAPI
app = FastAPI()
def safe_dependency():
    return True
def secret_dependency(vault_key: str):
    return bool(vault_key)
@app.get("/sync")
def sync(value=Depends(safe_dependency)):
    return {"ok": value}
app.dependency_overrides = {safe_dependency: secret_dependency}
''',
        encoding="utf-8",
    )

    assert any(
        "secret_dependency.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_preserves_client_scope_provenance_through_copies(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "copied_request_scope.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from urllib.parse import parse_qs
from fastapi import FastAPI, Request
app = FastAPI()
@app.get("/sync")
def sync(request: Request):
    scope = request.scope.copy()
    payload = parse_qs(scope["query_string"].decode())
    return payload["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_resolves_dependency_override_mapping_constructors(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "constructed_dependency_overrides.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import Depends, FastAPI
app = FastAPI()
def safe_dependency():
    return True
def secret_dependency(vault_key: str):
    return bool(vault_key)
@app.get("/sync")
def sync(value=Depends(safe_dependency)):
    return {"ok": value}
app.dependency_overrides = dict({safe_dependency: secret_dependency})
''',
        encoding="utf-8",
    )

    assert any(
        "secret_dependency.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_nested_asgi_path_parameter_mappings(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "asgi_path_params.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI
app = FastAPI()
class PathMiddleware:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        path_params = scope["path_params"]
        _ = path_params["vault_key"]
        await self.app(scope, receive, send)
app.add_middleware(PathMiddleware)
''',
        encoding="utf-8",
    )

    assert any(
        "ASGI middleware" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_inspects_dependencies_appended_to_active_routers(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "appended_router_dependency.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import Depends, FastAPI
app = FastAPI()
def secret_dependency(vault_key: str):
    return bool(vault_key)
app.router.dependencies.append(Depends(secret_dependency))
@app.get("/sync")
def sync():
    return {"ok": True}
''',
        encoding="utf-8",
    )

    assert any(
        "secret_dependency.vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_uploaded_file_header_mappings(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "uploaded_file_headers.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, File, UploadFile
app = FastAPI()
@app.post("/sync")
async def sync(payload: UploadFile = File()):
    return payload.headers["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_tracks_implicit_uploaded_file_bodies(tmp_path: Path) -> None:
    service_file = tmp_path / "services" / "implicit_uploaded_file.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
import json
from fastapi import FastAPI, UploadFile
app = FastAPI()
@app.post("/sync")
async def sync(payload: UploadFile):
    return json.loads(await payload.read())["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(service_file.parent)
    )


def test_A4_guard_propagates_dependency_return_provenance(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "dependency_return_provenance.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import Depends, FastAPI, Request
app = FastAPI()
async def get_payload(request: Request):
    return await request.json()
def get_request(request: Request):
    return request
@app.post("/payload")
async def payload_route(payload=Depends(get_payload)):
    return payload["vault_key"]
@app.get("/request")
def request_route(injected=Depends(get_request)):
    return injected.headers["recovery_key"]
''',
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_propagates_dependency_yield_provenance(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "dependency_yield_provenance.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import Depends, FastAPI, Request
app = FastAPI()
async def get_payload(request: Request):
    yield await request.json()
@app.post("/payload")
async def payload_route(payload=Depends(get_payload)):
    return payload["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(
            service_file.parent
        )
    )


def test_A4_guard_tracks_uploaded_file_collection_elements(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "uploaded_file_collections.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, UploadFile
app = FastAPI()
@app.post("/indexed")
async def indexed(payloads: list[UploadFile]):
    return payloads[0].headers["vault_key"]
@app.post("/iterated")
async def iterated(payloads: list[UploadFile]):
    for payload in payloads:
        return payload.headers["recovery_key"]
    return None
''',
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_propagates_dependency_yield_from_elements(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "dependency_yield_from.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import Depends, FastAPI, Request
app = FastAPI()
def get_payload(request: Request):
    yield from [request.query_params]
@app.get("/payload")
def payload_route(payload=Depends(get_payload)):
    return payload["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(
            service_file.parent
        )
    )


def test_A4_guard_tracks_uploaded_file_iterator_adapters(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "uploaded_file_iterators.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, UploadFile
app = FastAPI()
@app.post("/enumerated")
async def enumerated(payloads: list[UploadFile]):
    for _, payload in enumerate(payloads):
        return payload.headers["vault_key"]
    return None
@app.post("/reversed")
async def reversed_payloads(payloads: list[UploadFile]):
    for payload in reversed(payloads):
        return payload.headers["recovery_key"]
    return None
''',
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_preserves_request_mapping_union_provenance(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "request_mapping_union.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/left")
async def left(request: Request):
    payload = await request.json()
    copied = payload | {"mode": "sync"}
    return copied["vault_key"]
@app.post("/right")
async def right(request: Request):
    payload = await request.json()
    copied = {"mode": "sync"} | payload
    return copied["recovery_key"]
''',
        encoding="utf-8",
    )

    violations = find_raw_secret_wire_contract_violations(service_file.parent)

    assert any("vault_key" in violation for violation in violations)
    assert any("recovery_key" in violation for violation in violations)


def test_A4_guard_rejects_request_mapping_helper_keyword_expansion(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "request_keyword_expansion.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
def consume(vault_key=None):
    return vault_key
@app.post("/sync")
async def sync(request: Request):
    payload = await request.json()
    return consume(**payload)
''',
        encoding="utf-8",
    )

    assert any(
        "dynamic request mapping key" in violation
        for violation in find_raw_secret_wire_contract_violations(
            service_file.parent
        )
    )


def test_A4_guard_tracks_async_context_manager_form_bindings(
    tmp_path: Path,
) -> None:
    service_file = tmp_path / "services" / "async_form_context.py"
    service_file.parent.mkdir()
    service_file.write_text(
        '''
from fastapi import FastAPI, Request
app = FastAPI()
@app.post("/sync")
async def sync(request: Request):
    async with request.form() as form:
        return form["vault_key"]
''',
        encoding="utf-8",
    )

    assert any(
        "vault_key" in violation
        for violation in find_raw_secret_wire_contract_violations(
            service_file.parent
        )
    )
