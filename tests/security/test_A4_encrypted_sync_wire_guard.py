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
