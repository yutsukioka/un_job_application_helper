"""Static guard for future encrypted-sync service boundaries."""

from __future__ import annotations

import ast
from pathlib import Path


ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
GENERIC_ROUTE_DECORATORS = {
    "api_route",
    "route",
    "websocket",
    "websocket_route",
}
PROGRAMMATIC_ROUTE_REGISTRARS = {
    "add_api_route",
    "add_api_websocket_route",
    "add_route",
    "add_websocket_route",
}
BANNED_WIRE_FIELD_NAMES = frozenset(
    {
        "passphrase",
        "raw_passphrase",
        "recovery_key",
        "recovery_key_b64",
        "vault_key",
        "vault_key_b64",
        "raw_vault_key",
        "raw_vault_key_b64",
        "unwrapped_key",
        "unwrapped_key_b64",
        "unwrapped_vault_key",
        "unwrapped_vault_key_b64",
        "plaintext_vault_key",
        "plaintext_vault_key_b64",
    }
)
_CANONICAL_BANNED_WIRE_FIELD_NAMES = frozenset(
    "".join(character for character in name.casefold() if character.isalnum())
    for name in BANNED_WIRE_FIELD_NAMES
)


def find_raw_secret_wire_contract_violations(service_root: str | Path) -> list[str]:
    root = Path(service_root)
    if not root.is_dir():
        return [f"{root}:1: configured service root does not exist or is not a directory"]
    service_paths = frozenset(
        path for path in sorted(root.rglob("*.py")) if not _skip_path(path)
    )
    if not service_paths:
        return [f"{root}:1: no Python service modules found under configured service root"]
    module_root = _repository_root(root)
    source_paths = frozenset(
        path
        for path in module_root.rglob("*.py")
        if path in service_paths or not _skip_repository_source_path(module_root, path)
    )
    paths_by_module = _paths_by_module(
        module_root,
        [(path, ast.Module(body=[], type_ignores=[])) for path in source_paths],
    )
    parsed_modules, violations = _parse_import_closure(
        module_root,
        service_paths,
        paths_by_module,
    )

    pydantic_class_models = _pydantic_model_keys(
        module_root,
        parsed_modules,
        paths_by_module,
    )
    pydantic_root_models = _pydantic_root_model_keys(
        module_root,
        parsed_modules,
        paths_by_module,
    )
    mapping_root_models = _mapping_pydantic_root_model_keys(
        parsed_modules,
        pydantic_root_models,
    )
    pydantic_class_models = pydantic_class_models | pydantic_root_models
    created_pydantic_models = _pydantic_create_model_definitions(parsed_modules)
    pydantic_models = pydantic_class_models | frozenset(created_pydantic_models)
    dataclass_models = _dataclass_model_keys(
        module_root,
        parsed_modules,
        paths_by_module,
    )
    typed_dict_models = _typed_dict_model_keys(
        module_root,
        parsed_modules,
        paths_by_module,
    )
    functional_typed_dicts = _functional_typed_dict_definitions(parsed_modules)
    functional_typed_dict_models = frozenset(functional_typed_dicts)
    route_owner_names = _framework_route_owner_names_by_path(
        module_root,
        parsed_modules,
        paths_by_module,
    )
    mounted_route_owners = _mounted_route_owner_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        service_paths,
        route_owner_names,
    )
    programmatic_handlers = _programmatic_route_handler_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        service_paths,
        route_owner_names,
        mounted_route_owners,
    )
    boundary_functions = _boundary_function_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        programmatic_handlers,
        service_paths,
        route_owner_names,
        mounted_route_owners,
    )
    request_models = _referenced_model_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        boundary_functions,
        pydantic_models
        | dataclass_models
        | typed_dict_models
        | functional_typed_dict_models,
    )
    inspected_pydantic_models = request_models & pydantic_class_models
    request_created_pydantic_models = request_models & frozenset(
        created_pydantic_models
    )
    request_dataclasses = request_models & dataclass_models
    request_typed_dicts = request_models & typed_dict_models
    request_functional_typed_dicts = request_models & functional_typed_dict_models
    for key in sorted(request_functional_typed_dicts):
        path, _, model_name = key
        for field_name, line_number in _functional_typed_dict_fields(
            functional_typed_dicts[key]
        ):
            if _is_banned_wire_name(field_name):
                violations.append(
                    f"{path}:{line_number}: TypedDict request model "
                    f"{model_name}.{field_name} would accept raw vault secret "
                    "material over the wire"
                )
    for key in sorted(request_created_pydantic_models):
        path, _, model_name = key
        tree = dict(parsed_modules)[path]
        for config_kind, line_number in _pydantic_create_model_config_issues(
            created_pydantic_models[key],
            tree,
        ):
            if config_kind == "extra":
                violations.append(
                    f"{path}:{line_number}: Pydantic request model {model_name} "
                    "allows arbitrary extra wire fields"
                )
            elif config_kind == "alias_generator":
                violations.append(
                    f"{path}:{line_number}: Pydantic request model {model_name} "
                    "defines an alias generator that cannot be statically approved"
                )
            else:
                violations.append(
                    f"{path}:{line_number}: Pydantic request model {model_name} "
                    "uses configuration that cannot be statically approved"
                )
        for field_name, line_number in _pydantic_create_model_fields(
            created_pydantic_models[key],
            field_factory_names=_pydantic_symbol_aliases(tree, "Field"),
            field_factory_module_names=_pydantic_module_aliases(tree),
            wire_alias_constants=_string_constants(tree.body),
        ):
            if _is_banned_wire_name(field_name):
                violations.append(
                    f"{path}:{line_number}: Pydantic model "
                    f"{model_name}.{field_name} would accept raw vault secret "
                    "material over the wire"
                )
    for path, tree in parsed_modules:
        if not (
            path in service_paths
            or any(model_path == path for model_path, _, _ in inspected_pydantic_models)
            or any(model_path == path for model_path, _, _ in request_dataclasses)
            or any(model_path == path for model_path, _, _ in request_typed_dicts)
            or any(
                function_path == path
                for function_path, _, _ in boundary_functions
            )
        ):
            continue
        depends_aliases, depends_module_aliases = _depends_aliases(tree)
        body_aliases, body_module_aliases = _body_aliases(tree)
        visitor = _ServiceContractVisitor(
            path,
            pydantic_model_lines=frozenset(
                line_number
                for model_path, line_number, _ in inspected_pydantic_models
                if model_path == path
            ),
            dataclass_model_lines=frozenset(
                line_number
                for model_path, line_number, _ in request_dataclasses
                if model_path == path
            ),
            typed_dict_model_lines=frozenset(
                line_number
                for model_path, line_number, _ in request_typed_dicts
                if model_path == path
            ),
            mapping_root_model_lines=frozenset(
                line_number
                for model_path, line_number, _ in (
                    request_models & mapping_root_models
                )
                if model_path == path
            ),
            field_factory_names=_pydantic_symbol_aliases(tree, "Field"),
            field_factory_module_names=_pydantic_module_aliases(tree),
            model_validator_names=_pydantic_symbol_aliases(
                tree,
                "model_validator",
            ),
            model_validator_module_names=_pydantic_module_aliases(tree),
            wire_alias_constants=_string_constants(tree.body),
            request_type_names=_request_type_aliases(tree),
            request_module_names=_request_module_aliases(tree),
            websocket_type_names=_websocket_type_aliases(tree),
            websocket_module_names=_websocket_module_aliases(tree),
            json_loads_names=_json_loads_aliases(tree),
            json_module_names=_json_module_aliases(tree),
            route_owner_names=route_owner_names.get(path, frozenset()),
            depends_aliases=frozenset(depends_aliases),
            depends_module_aliases=frozenset(depends_module_aliases),
            body_aliases=frozenset(body_aliases),
            body_module_aliases=frozenset(body_module_aliases),
            unconstrained_type_names=(
                _typing_symbol_aliases(tree, "Any") | {"object"}
            ),
            unconstrained_type_module_names=_typing_module_aliases(tree),
            boundary_function_lines=frozenset(
                line_number
                for function_path, line_number, _ in boundary_functions
                if function_path == path
            ),
        )
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


class _ServiceContractVisitor(ast.NodeVisitor):
    def __init__(
        self,
        path: Path,
        *,
        pydantic_model_lines: frozenset[int],
        dataclass_model_lines: frozenset[int],
        typed_dict_model_lines: frozenset[int],
        mapping_root_model_lines: frozenset[int],
        field_factory_names: frozenset[str],
        field_factory_module_names: frozenset[str],
        model_validator_names: frozenset[str],
        model_validator_module_names: frozenset[str],
        wire_alias_constants: dict[str, str],
        request_type_names: frozenset[str],
        request_module_names: frozenset[str],
        websocket_type_names: frozenset[str],
        websocket_module_names: frozenset[str],
        json_loads_names: frozenset[str],
        json_module_names: frozenset[str],
        route_owner_names: frozenset[str],
        depends_aliases: frozenset[str],
        depends_module_aliases: frozenset[str],
        body_aliases: frozenset[str],
        body_module_aliases: frozenset[str],
        unconstrained_type_names: frozenset[str],
        unconstrained_type_module_names: frozenset[str],
        boundary_function_lines: frozenset[int],
    ) -> None:
        self.path = path
        self.pydantic_model_lines = pydantic_model_lines
        self.dataclass_model_lines = dataclass_model_lines
        self.typed_dict_model_lines = typed_dict_model_lines
        self.mapping_root_model_lines = mapping_root_model_lines
        self.field_factory_names = field_factory_names
        self.field_factory_module_names = field_factory_module_names
        self.model_validator_names = model_validator_names
        self.model_validator_module_names = model_validator_module_names
        self.wire_alias_constants = wire_alias_constants
        self.request_type_names = request_type_names
        self.request_module_names = request_module_names
        self.websocket_type_names = websocket_type_names
        self.websocket_module_names = websocket_module_names
        self.json_loads_names = json_loads_names
        self.json_module_names = json_module_names
        self.route_owner_names = route_owner_names
        self.depends_aliases = depends_aliases
        self.depends_module_aliases = depends_module_aliases
        self.body_aliases = body_aliases
        self.body_module_aliases = body_module_aliases
        self.unconstrained_type_names = unconstrained_type_names
        self.unconstrained_type_module_names = unconstrained_type_module_names
        self.boundary_function_lines = boundary_function_lines
        self.violations: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if (
            node.lineno in self.pydantic_model_lines
            or node.lineno in self.dataclass_model_lines
            or node.lineno in self.typed_dict_model_lines
        ):
            if node.lineno in self.pydantic_model_lines:
                model_kind = "Pydantic model"
            elif node.lineno in self.dataclass_model_lines:
                model_kind = "dataclass request model"
            else:
                model_kind = "TypedDict request model"
            if node.lineno in self.pydantic_model_lines and (
                extra_line := _permissive_model_extra_line(node)
            ) is not None:
                self.violations.append(
                    f"{self.path}:{extra_line}: Pydantic request model {node.name} "
                    "allows arbitrary extra wire fields"
                )
            if node.lineno in self.pydantic_model_lines and (
                alias_generator_line := _model_alias_generator_line(node)
            ) is not None:
                self.violations.append(
                    f"{self.path}:{alias_generator_line}: Pydantic request model "
                    f"{node.name} defines an alias generator that cannot be "
                    "statically approved"
                )
            if node.lineno in self.pydantic_model_lines and (
                validator_line := _before_model_validator_line(
                    node,
                    self.model_validator_names,
                    self.model_validator_module_names,
                )
            ) is not None:
                self.violations.append(
                    f"{self.path}:{validator_line}: Pydantic request model "
                    f"{node.name} defines a before model validator that cannot "
                    "be statically approved"
                )
            if node.lineno in self.mapping_root_model_lines:
                self.violations.append(
                    f"{self.path}:{node.lineno}: Pydantic request model "
                    f"{node.name} has an unconstrained mapping root"
                )
            for field_name, line_number in _model_fields(
                node,
                field_factory_names=self.field_factory_names,
                field_factory_module_names=self.field_factory_module_names,
                wire_alias_constants={
                    **self.wire_alias_constants,
                    **_string_constants(node.body),
                },
                exclude_private=node.lineno in self.pydantic_model_lines,
                include_unannotated_assignments=node.lineno in self.pydantic_model_lines,
            ):
                if _is_banned_wire_name(field_name):
                    self.violations.append(
                        f"{self.path}:{line_number}: {model_kind} {node.name}.{field_name} "
                        "would accept raw vault secret material over the wire"
                    )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_route_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_route_function(node)
        self.generic_visit(node)

    def _visit_route_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not _is_route_handler(
            node,
            self.boundary_function_lines,
            self.route_owner_names,
        ):
            return
        for parameter_name, line_number in _unconstrained_mapping_request_parameters(
            node,
            depends_aliases=self.depends_aliases,
            depends_module_aliases=self.depends_module_aliases,
            body_aliases=self.body_aliases,
            body_module_aliases=self.body_module_aliases,
            unconstrained_type_names=self.unconstrained_type_names,
            unconstrained_type_module_names=self.unconstrained_type_module_names,
        ):
            self.violations.append(
                f"{self.path}:{line_number}: route parameter "
                f"{node.name}.{parameter_name} is an unconstrained mapping request body"
            )
        for wire_name, line_number in _route_parameter_wire_names(
            node,
            depends_aliases=self.depends_aliases,
            depends_module_aliases=self.depends_module_aliases,
            wire_alias_constants=self.wire_alias_constants,
        ):
            if _is_banned_wire_name(wire_name):
                self.violations.append(
                    f"{self.path}:{line_number}: route parameter {node.name}.{wire_name} "
                    "would accept raw vault secret material over the wire"
                )
        for wire_name, line_number in _route_body_wire_names(
            node,
            request_type_names=self.request_type_names,
            request_module_names=self.request_module_names,
            websocket_type_names=self.websocket_type_names,
            websocket_module_names=self.websocket_module_names,
            json_loads_names=self.json_loads_names,
            json_module_names=self.json_module_names,
        ):
            if _is_banned_wire_name(wire_name):
                self.violations.append(
                    f"{self.path}:{line_number}: route body {node.name}[{wire_name!r}] "
                    "would accept raw vault secret material over the wire"
                )


def _skip_path(path: Path) -> bool:
    return any(part in {".venv", "__pycache__", ".pytest_cache", ".ruff_cache"} for part in path.parts)


def _repository_root(service_root: Path) -> Path:
    for candidate in (service_root, *service_root.parents):
        if (candidate / ".git").exists():
            return candidate
        if candidate.name == "services":
            return candidate.parent
    return service_root.parent


def _skip_repository_source_path(root: Path, path: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    return any(
        part in {
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            "private",
            "tests",
        }
        or part.startswith(".")
        for part in relative_parts
    )


def _parse_import_closure(
    module_root: Path,
    service_paths: frozenset[Path],
    paths_by_module: dict[str, Path],
) -> tuple[list[tuple[Path, ast.Module]], list[str]]:
    parsed: dict[Path, ast.Module] = {}
    violations: list[str] = []
    pending = list(sorted(service_paths, reverse=True))
    queued = set(pending)
    source_paths = frozenset(paths_by_module.values())
    while pending:
        path = pending.pop()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            line_number = exc.lineno if isinstance(exc, SyntaxError) else 1
            violations.append(f"{path}:{line_number or 1}: cannot parse service module")
            continue
        parsed[path] = tree
        symbol_targets, module_targets = _import_targets(
            module_root,
            path,
            tree,
            paths_by_module,
        )
        imported_paths = {
            target_path for target_path, _ in symbol_targets.values()
        } | set(module_targets.values())
        for imported_path in sorted(imported_paths, reverse=True):
            if (
                imported_path in source_paths
                and imported_path not in parsed
                and imported_path not in queued
            ):
                pending.append(imported_path)
                queued.add(imported_path)
    return sorted(parsed.items()), violations


def _pydantic_symbol_aliases(tree: ast.Module, symbol: str) -> frozenset[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None or not (
            node.module == "pydantic" or node.module.startswith("pydantic.")
        ):
            continue
        for imported in node.names:
            if imported.name == symbol:
                aliases.add(imported.asname or imported.name)
    return frozenset(aliases)


def _pydantic_module_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name == "pydantic" or imported.name.startswith("pydantic.")
    )


def _framework_route_owner_names(tree: ast.Module) -> frozenset[str]:
    constructor_aliases: set[str] = set()
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and (
            node.module == "fastapi"
            or node.module.startswith("fastapi.")
            or node.module == "starlette"
            or node.module.startswith("starlette.")
        ):
            constructor_aliases.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name in {"FastAPI", "APIRouter", "Starlette"}
            )
        elif isinstance(node, ast.Import):
            module_aliases.update(
                imported.asname or imported.name.split(".")[0]
                for imported in node.names
                if imported.name == "fastapi"
                or imported.name.startswith("fastapi.")
                or imported.name == "starlette"
                or imported.name.startswith("starlette.")
            )

    framework_class_names = set(constructor_aliases)
    class_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    changed = True
    while changed:
        changed = False
        for class_node in class_nodes:
            if class_node.name in framework_class_names:
                continue
            if any(
                _is_framework_class_base(
                    base,
                    framework_class_names,
                    module_aliases,
                )
                for base in class_node.bases
            ):
                framework_class_names.add(class_node.name)
                changed = True

    owners: set[str] = set()
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            targets, value = _assignment_targets_and_value(assignment)
            is_owner = _is_framework_constructor(
                value,
                framework_class_names,
                module_aliases,
            ) or (isinstance(value, ast.Name) and value.id in owners)
            if is_owner:
                previous_size = len(owners)
                owners.update(targets)
                changed = changed or len(owners) != previous_size
    return frozenset(owners)


def _is_framework_class_base(
    node: ast.expr,
    framework_class_names: set[str],
    module_aliases: set[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in framework_class_names
    parts = _qualified_name_parts(node)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] in {"FastAPI", "APIRouter", "Starlette"}
        and parts[0] in module_aliases
    )


def _starlette_route_constructor_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module in {"fastapi.routing", "starlette.routing"}
        for imported in node.names
        if imported.name
        in {"APIRoute", "APIWebSocketRoute", "Route", "WebSocketRoute"}
    )


def _starlette_route_module_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name
        in {"fastapi", "fastapi.routing", "starlette", "starlette.routing"}
    )


def _is_starlette_route_constructor(
    node: ast.Call,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in direct_aliases
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1]
        in {"APIRoute", "APIWebSocketRoute", "Route", "WebSocketRoute"}
        and parts[0] in module_aliases
    )


def _framework_route_owner_names_by_path(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> dict[Path, frozenset[str]]:
    trees_by_path = dict(parsed_modules)
    owners = {
        path: set(_framework_route_owner_names(tree))
        for path, tree in parsed_modules
    }
    changed = True
    while changed:
        changed = False
        owner_keys = {
            (path, name) for path, names in owners.items() for name in names
        }
        for path, tree in parsed_modules:
            symbol_targets, module_targets = _import_targets(
                root,
                path,
                tree,
                paths_by_module,
            )
            for local_name, target in symbol_targets.items():
                target = _follow_symbol_reexports(
                    root,
                    target,
                    paths_by_module,
                    trees_by_path,
                )
                if target in owner_keys and local_name not in owners[path]:
                    owners[path].add(local_name)
                    changed = True
            for local_parts, target_path in module_targets.items():
                for target_owner in owners.get(target_path, set()):
                    if "." in target_owner:
                        continue
                    qualified_owner = ".".join((*local_parts, target_owner))
                    if qualified_owner not in owners[path]:
                        owners[path].add(qualified_owner)
                        changed = True
            for assignment in (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
            ):
                targets, value = _assignment_targets_and_value(assignment)
                if _is_route_owner_reference(value, owners[path]):
                    previous_size = len(owners[path])
                    owners[path].update(targets)
                    changed = changed or len(owners[path]) != previous_size
    return {path: frozenset(names) for path, names in owners.items()}


def _mounted_route_owner_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    service_paths: frozenset[Path],
    route_owner_names: dict[Path, frozenset[str]],
) -> frozenset[tuple[Path, str]]:
    trees_by_path = dict(parsed_modules)
    owner_keys = {
        (path, name)
        for path, names in route_owner_names.items()
        for name in names
        if "." not in name
    }
    mounted: set[tuple[Path, str]] = set()
    changed = True
    while changed:
        changed = False
        for path, tree in parsed_modules:
            for call in (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"include_router", "mount"}
            ):
                caller = _resolve_reference_target(
                    root,
                    path,
                    tree,
                    call.func.value,
                    paths_by_module,
                    trees_by_path,
                )
                if caller not in owner_keys:
                    continue
                if path not in service_paths and caller not in mounted:
                    continue
                if call.func.attr == "include_router":
                    mounted_owner = next(
                        (
                            keyword.value
                            for keyword in call.keywords
                            if keyword.arg == "router"
                        ),
                        call.args[0] if call.args else None,
                    )
                else:
                    mounted_owner = next(
                        (
                            keyword.value
                            for keyword in call.keywords
                            if keyword.arg == "app"
                        ),
                        call.args[1] if len(call.args) > 1 else None,
                    )
                if mounted_owner is None:
                    continue
                target = _resolve_reference_target(
                    root,
                    path,
                    tree,
                    mounted_owner,
                    paths_by_module,
                    trees_by_path,
                )
                if target in owner_keys and target not in mounted:
                    mounted.add(target)
                    changed = True
    return frozenset(mounted)


def _is_route_owner_reference(
    node: ast.AST,
    owner_names: set[str] | frozenset[str],
) -> bool:
    parts = _qualified_name_parts(node)
    if not parts:
        return False
    return parts[-1] in owner_names or ".".join(parts) in owner_names


def _is_framework_constructor(
    node: ast.expr,
    constructor_aliases: set[str],
    module_aliases: set[str],
) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id in constructor_aliases
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] in {"FastAPI", "APIRouter", "Starlette"}
        and parts[0] in module_aliases
    )


def _paths_by_module(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    package_directories = {
        path.parent for path, _ in parsed_modules if path.name == "__init__.py"
    }
    package_roots = {
        directory
        for directory in package_directories
        if directory.parent not in package_directories
    }
    for path, _ in parsed_modules:
        module_name = _module_name(root, path)
        paths[module_name] = path
        if module_name:
            paths[f"{root.name}.{module_name}"] = path
        module_parts = list(path.relative_to(root).with_suffix("").parts)
        if module_parts and module_parts[-1] == "__init__":
            module_parts.pop()
        for package_root in package_roots:
            try:
                package_parts = package_root.relative_to(root).parts
                path.relative_to(package_root)
            except ValueError:
                continue
            if not package_parts:
                continue
            importable_name = ".".join(module_parts[len(package_parts) - 1 :])
            if importable_name:
                paths[importable_name] = path
    return paths


def _class_definitions(
    parsed_modules: list[tuple[Path, ast.Module]],
) -> dict[tuple[Path, int, str], ast.ClassDef]:
    return {
        key: node
        for path, tree in parsed_modules
        for key, node, _ in _module_class_entries(path, tree)
    }


def _class_scopes(
    parsed_modules: list[tuple[Path, ast.Module]],
) -> dict[tuple[Path, int, str], tuple[int, ...]]:
    return {
        key: scope
        for path, tree in parsed_modules
        for key, _, scope in _module_class_entries(path, tree)
    }


def _module_class_entries(
    path: Path,
    tree: ast.Module,
) -> list[tuple[tuple[Path, int, str], ast.ClassDef, tuple[int, ...]]]:
    entries: list[tuple[tuple[Path, int, str], ast.ClassDef, tuple[int, ...]]] = []

    class Collector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[int] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            key = (path, node.lineno, node.name)
            entries.append((key, node, tuple(self.scope)))
            self.scope.append(node.lineno)
            for statement in node.body:
                self.visit(statement)
            self.scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

        def _visit_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            self.scope.append(node.lineno)
            for statement in node.body:
                self.visit(statement)
            self.scope.pop()

    Collector().visit(tree)
    return entries


def _module_assignment_entries(
    path: Path,
    tree: ast.Module,
) -> list[
    tuple[
        tuple[Path, int, str],
        ast.expr,
        tuple[int, ...],
        ast.expr | None,
    ]
]:
    entries: list[
        tuple[
            tuple[Path, int, str],
            ast.expr,
            tuple[int, ...],
            ast.expr | None,
        ]
    ] = []

    class Collector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[int] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._visit_scope(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_scope(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_scope(node)

        def _visit_scope(
            self,
            node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            self.scope.append(node.lineno)
            for statement in node.body:
                self.visit(statement)
            self.scope.pop()

        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    entries.append(
                        (
                            (path, node.lineno, target.id),
                            node.value,
                            tuple(self.scope),
                            None,
                        )
                    )

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if isinstance(node.target, ast.Name) and node.value is not None:
                entries.append(
                    (
                        (path, node.lineno, node.target.id),
                        node.value,
                        tuple(self.scope),
                        node.annotation,
                    )
                )

    Collector().visit(tree)
    return entries


def _pydantic_model_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> frozenset[tuple[Path, int, str]]:
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    trees_by_path = dict(parsed_modules)
    base_aliases = {
        path: _pydantic_symbol_aliases(tree, "BaseModel")
        for path, tree in parsed_modules
    }
    module_aliases = {
        path: _pydantic_module_aliases(tree) for path, tree in parsed_modules
    }

    model_keys: set[tuple[Path, int, str]] = set()
    changed = True
    while changed:
        changed = False
        for key, node in classes.items():
            if key in model_keys:
                continue
            path, _, _ = key
            is_model = False
            for base in node.bases:
                if _is_pydantic_base_reference(
                    base,
                    base_aliases[path],
                    module_aliases[path],
                ):
                    is_model = True
                    break
                target = _resolve_model_key(
                    root,
                    path,
                    trees_by_path[path],
                    base,
                    paths_by_module,
                    trees_by_path,
                    classes,
                    class_scopes,
                    key,
                )
                if target in model_keys:
                    is_model = True
                    break
            if is_model:
                model_keys.add(key)
                changed = True
    return frozenset(model_keys)


def _pydantic_root_model_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> frozenset[tuple[Path, int, str]]:
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    trees_by_path = dict(parsed_modules)
    direct_aliases = {
        path: _pydantic_symbol_aliases(tree, "RootModel")
        for path, tree in parsed_modules
    }
    module_aliases = {
        path: _pydantic_module_aliases(tree) for path, tree in parsed_modules
    }
    model_keys: set[tuple[Path, int, str]] = set()
    changed = True
    while changed:
        changed = False
        for key, node in classes.items():
            if key in model_keys:
                continue
            path, _, _ = key
            is_model = False
            for base in node.bases:
                if _is_pydantic_root_reference(
                    base,
                    direct_aliases[path],
                    module_aliases[path],
                ):
                    is_model = True
                    break
                target = _resolve_model_key(
                    root,
                    path,
                    trees_by_path[path],
                    base,
                    paths_by_module,
                    trees_by_path,
                    classes,
                    class_scopes,
                    key,
                )
                if target in model_keys:
                    is_model = True
                    break
            if is_model:
                model_keys.add(key)
                changed = True
    return frozenset(model_keys)


def _mapping_pydantic_root_model_keys(
    parsed_modules: list[tuple[Path, ast.Module]],
    root_model_keys: frozenset[tuple[Path, int, str]],
) -> frozenset[tuple[Path, int, str]]:
    classes = _class_definitions(parsed_modules)
    trees_by_path = dict(parsed_modules)
    mapping_keys: set[tuple[Path, int, str]] = set()
    for key in root_model_keys:
        node = classes[key]
        path, _, _ = key
        direct_aliases = _pydantic_symbol_aliases(trees_by_path[path], "RootModel")
        module_aliases = _pydantic_module_aliases(trees_by_path[path])
        if any(
            isinstance(base, ast.Subscript)
            and _is_pydantic_root_reference(base, direct_aliases, module_aliases)
            and _annotation_contains_mapping(base.slice)
            for base in node.bases
        ):
            mapping_keys.add(key)
    return frozenset(mapping_keys)


def _is_pydantic_root_reference(
    base: ast.expr,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> bool:
    candidate = base.value if isinstance(base, ast.Subscript) else base
    if isinstance(candidate, ast.Name):
        return candidate.id in direct_aliases
    parts = _qualified_name_parts(candidate)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "RootModel"
        and parts[0] in module_aliases
    )


def _is_pydantic_base_reference(
    base: ast.expr,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> bool:
    if isinstance(base, ast.Name):
        return base.id in direct_aliases
    parts = _qualified_name_parts(base)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "BaseModel"
        and parts[0] in module_aliases
    )


def _dataclass_model_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> frozenset[tuple[Path, int, str]]:
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    trees_by_path = dict(parsed_modules)
    direct_aliases_by_path: dict[Path, set[str]] = {}
    module_aliases_by_path: dict[Path, set[str]] = {}
    for path, tree in parsed_modules:
        direct_aliases_by_path[path] = {
            imported.asname or imported.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module in {"dataclasses", "pydantic.dataclasses"}
            for imported in node.names
            if imported.name == "dataclass"
        }
        module_aliases_by_path[path] = {
            imported.asname or imported.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for imported in node.names
            if imported.name in {
                "dataclasses",
                "pydantic",
                "pydantic.dataclasses",
            }
        }

    model_keys: set[tuple[Path, int, str]] = set()
    changed = True
    while changed:
        changed = False
        for key, node in classes.items():
            if key in model_keys:
                continue
            path, _, _ = key
            is_model = any(
                _is_dataclass_decorator(
                    decorator,
                    direct_aliases_by_path[path],
                    module_aliases_by_path[path],
                )
                for decorator in node.decorator_list
            )
            if not is_model:
                is_model = any(
                    _resolve_model_key(
                        root,
                        path,
                        trees_by_path[path],
                        base,
                        paths_by_module,
                        trees_by_path,
                        classes,
                        class_scopes,
                        key,
                    )
                    in model_keys
                    for base in node.bases
                )
            if is_model:
                model_keys.add(key)
                changed = True
    return frozenset(model_keys)


def _typed_dict_model_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> frozenset[tuple[Path, int, str]]:
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    trees_by_path = dict(parsed_modules)
    direct_aliases = {
        path: _typing_symbol_aliases(tree, "TypedDict")
        for path, tree in parsed_modules
    }
    module_aliases = {
        path: _typing_module_aliases(tree) for path, tree in parsed_modules
    }
    model_keys: set[tuple[Path, int, str]] = set()
    changed = True
    while changed:
        changed = False
        for key, node in classes.items():
            if key in model_keys:
                continue
            path, _, _ = key
            is_model = False
            for base in node.bases:
                if _is_typed_dict_base_reference(
                    base,
                    direct_aliases[path],
                    module_aliases[path],
                ):
                    is_model = True
                    break
                target = _resolve_model_key(
                    root,
                    path,
                    trees_by_path[path],
                    base,
                    paths_by_module,
                    trees_by_path,
                    classes,
                    class_scopes,
                    key,
                )
                if target in model_keys:
                    is_model = True
                    break
            if is_model:
                model_keys.add(key)
                changed = True
    return frozenset(model_keys)


def _functional_typed_dict_definitions(
    parsed_modules: list[tuple[Path, ast.Module]],
) -> dict[tuple[Path, int, str], ast.Call]:
    definitions: dict[tuple[Path, int, str], ast.Call] = {}
    for path, tree in parsed_modules:
        direct_aliases = _typing_symbol_aliases(tree, "TypedDict")
        module_aliases = _typing_module_aliases(tree)
        for key, value, _, _ in _module_assignment_entries(path, tree):
            if isinstance(value, ast.Call) and _is_typed_dict_call(
                value,
                direct_aliases,
                module_aliases,
            ):
                definitions[key] = value
    return definitions


def _pydantic_create_model_definitions(
    parsed_modules: list[tuple[Path, ast.Module]],
) -> dict[tuple[Path, int, str], ast.Call]:
    definitions: dict[tuple[Path, int, str], ast.Call] = {}
    for path, tree in parsed_modules:
        direct_aliases = _pydantic_symbol_aliases(tree, "create_model")
        module_aliases = _pydantic_module_aliases(tree)
        for key, value, _, _ in _module_assignment_entries(path, tree):
            if isinstance(value, ast.Call) and _is_pydantic_create_model_call(
                value,
                direct_aliases,
                module_aliases,
            ):
                definitions[key] = value
    return definitions


def _is_pydantic_create_model_call(
    node: ast.Call,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in direct_aliases
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "create_model"
        and parts[0] in module_aliases
    )


def _pydantic_create_model_fields(
    node: ast.Call,
    *,
    field_factory_names: frozenset[str],
    field_factory_module_names: frozenset[str],
    wire_alias_constants: dict[str, str],
) -> list[tuple[str, int]]:
    fields: list[tuple[str, int]] = []
    reserved = {
        "__base__",
        "__cls_kwargs__",
        "__config__",
        "__doc__",
        "__module__",
        "__validators__",
    }
    for keyword in node.keywords:
        if keyword.arg is None or keyword.arg in reserved:
            continue
        fields.append((keyword.arg, keyword.value.lineno))
        fields.extend(
            _pydantic_field_aliases(
                keyword.value,
                field_factory_names=field_factory_names,
                field_factory_module_names=field_factory_module_names,
                wire_alias_constants=wire_alias_constants,
            )
        )
    return fields


def _pydantic_create_model_config_issues(
    node: ast.Call,
    tree: ast.Module,
) -> list[tuple[str, int]]:
    issues: list[tuple[str, int]] = []
    for keyword in node.keywords:
        if keyword.arg != "__config__":
            continue
        config = _resolve_module_expression(
            keyword.value,
            tree,
            before_line=node.lineno,
        )
        if _expression_allows_extra(config):
            issues.append(("extra", keyword.value.lineno))
        if _expression_defines_config_option(config, "alias_generator"):
            issues.append(("alias_generator", keyword.value.lineno))
        if not _is_inspectable_pydantic_config(config, tree):
            issues.append(("opaque", keyword.value.lineno))
    return issues


def _resolve_module_expression(
    node: ast.expr,
    tree: ast.Module,
    *,
    before_line: int,
    seen: frozenset[str] = frozenset(),
) -> ast.expr:
    if not isinstance(node, ast.Name) or node.id in seen:
        return node
    candidates: list[tuple[int, ast.expr]] = []
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets, value = _assignment_targets_and_value(statement)
        if node.id in targets and statement.lineno < before_line:
            candidates.append((statement.lineno, value))
    if not candidates:
        return node
    line, value = max(candidates, key=lambda entry: entry[0])
    return _resolve_module_expression(
        value,
        tree,
        before_line=line,
        seen=seen | {node.id},
    )


def _is_inspectable_pydantic_config(node: ast.expr, tree: ast.Module) -> bool:
    if isinstance(node, ast.Dict):
        return True
    if not isinstance(node, ast.Call):
        return False
    direct_aliases = _pydantic_symbol_aliases(tree, "ConfigDict")
    module_aliases = _pydantic_module_aliases(tree)
    if isinstance(node.func, ast.Name):
        return node.func.id in direct_aliases
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "ConfigDict"
        and parts[0] in module_aliases
    )


def _pydantic_create_model_type_roots(node: ast.Call) -> list[ast.AST]:
    reserved = {
        "__base__",
        "__cls_kwargs__",
        "__config__",
        "__doc__",
        "__module__",
        "__validators__",
    }
    return [
        keyword.value
        for keyword in node.keywords
        if keyword.arg is not None
        and (keyword.arg == "__base__" or keyword.arg not in reserved)
    ]


def _is_typed_dict_call(
    node: ast.Call,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in direct_aliases
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "TypedDict"
        and parts[0] in module_aliases
    )


def _functional_typed_dict_fields(node: ast.Call) -> list[tuple[str, int]]:
    fields: list[tuple[str, int]] = []
    mapping = node.args[1] if len(node.args) > 1 else None
    if isinstance(mapping, ast.Dict):
        fields.extend(
            (key.value, key.lineno)
            for key in mapping.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        )
    fields.extend(
        (keyword.arg, keyword.value.lineno)
        for keyword in node.keywords
        if keyword.arg not in {None, "total", "closed"}
    )
    return fields


def _functional_typed_dict_type_roots(node: ast.Call) -> list[ast.AST]:
    mapping = node.args[1] if len(node.args) > 1 else None
    roots = list(mapping.values) if isinstance(mapping, ast.Dict) else []
    roots.extend(
        keyword.value
        for keyword in node.keywords
        if keyword.arg not in {None, "total", "closed"}
    )
    return roots


def _typing_symbol_aliases(tree: ast.Module, symbol: str) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module in {"typing", "typing_extensions"}
        for imported in node.names
        if imported.name == symbol
    )


def _typing_module_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name in {"typing", "typing_extensions"}
    )


def _is_typed_dict_base_reference(
    base: ast.expr,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> bool:
    if isinstance(base, ast.Name):
        return base.id in direct_aliases
    parts = _qualified_name_parts(base)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "TypedDict"
        and parts[0] in module_aliases
    )


def _is_dataclass_decorator(
    decorator: ast.expr,
    direct_aliases: set[str],
    module_aliases: set[str],
) -> bool:
    candidate = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(candidate, ast.Name):
        return candidate.id in direct_aliases
    parts = _qualified_name_parts(candidate)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "dataclass"
        and parts[0] in module_aliases
    )


def _model_fields(
    node: ast.ClassDef,
    *,
    field_factory_names: frozenset[str],
    field_factory_module_names: frozenset[str],
    wire_alias_constants: dict[str, str],
    exclude_private: bool,
    include_unannotated_assignments: bool,
) -> list[tuple[str, int]]:
    fields: list[tuple[str, int]] = []
    for child in node.body:
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            if _is_class_variable(child.annotation) or (
                exclude_private and child.target.id.startswith("_")
            ):
                continue
            fields.append((child.target.id, child.lineno))
            fields.extend(
                _pydantic_field_aliases(
                    child.annotation,
                    child.value,
                    field_factory_names=field_factory_names,
                    field_factory_module_names=field_factory_module_names,
                    wire_alias_constants=wire_alias_constants,
                )
            )
        elif isinstance(child, ast.Assign) and include_unannotated_assignments:
            has_field_target = False
            for target in child.targets:
                if isinstance(target, ast.Name) and not (
                    exclude_private and target.id.startswith("_")
                ):
                    fields.append((target.id, child.lineno))
                    has_field_target = True
            if has_field_target:
                fields.extend(
                    _pydantic_field_aliases(
                        child.value,
                        field_factory_names=field_factory_names,
                        field_factory_module_names=field_factory_module_names,
                        wire_alias_constants=wire_alias_constants,
                    )
                )
    return fields


def _permissive_model_extra_line(node: ast.ClassDef) -> int | None:
    for keyword in node.keywords:
        if keyword.arg == "extra" and _is_allow_value(keyword.value):
            return keyword.value.lineno
    for child in node.body:
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            if any(isinstance(target, ast.Name) and target.id == "model_config" for target in targets):
                if _expression_allows_extra(child.value):
                    return child.lineno
        if isinstance(child, ast.ClassDef) and child.name == "Config":
            for setting in child.body:
                if not isinstance(setting, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = setting.targets if isinstance(setting, ast.Assign) else [setting.target]
                if any(isinstance(target, ast.Name) and target.id == "extra" for target in targets):
                    if _is_allow_value(setting.value):
                        return setting.lineno
    return None


def _model_alias_generator_line(node: ast.ClassDef) -> int | None:
    for keyword in node.keywords:
        if keyword.arg == "alias_generator":
            return keyword.value.lineno
    for child in node.body:
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            if any(
                isinstance(target, ast.Name) and target.id == "model_config"
                for target in targets
            ) and _expression_defines_config_option(child.value, "alias_generator"):
                return child.lineno
        if isinstance(child, ast.ClassDef) and child.name == "Config":
            for setting in child.body:
                if not isinstance(setting, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = (
                    setting.targets
                    if isinstance(setting, ast.Assign)
                    else [setting.target]
                )
                if any(
                    isinstance(target, ast.Name) and target.id == "alias_generator"
                    for target in targets
                ):
                    return setting.lineno
    return None


def _before_model_validator_line(
    node: ast.ClassDef,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> int | None:
    for child in node.body:
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in child.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if isinstance(decorator.func, ast.Name):
                is_model_validator = decorator.func.id in direct_aliases
            else:
                parts = _qualified_name_parts(decorator.func)
                is_model_validator = bool(
                    parts
                    and len(parts) >= 2
                    and parts[-1] == "model_validator"
                    and parts[0] in module_aliases
                )
            if not is_model_validator:
                continue
            mode = next(
                (
                    keyword.value
                    for keyword in decorator.keywords
                    if keyword.arg == "mode"
                ),
                None,
            )
            if isinstance(mode, ast.Constant) and mode.value == "before":
                return decorator.lineno
    return None


def _expression_defines_config_option(node: ast.expr, option: str) -> bool:
    if isinstance(node, ast.Call):
        return any(keyword.arg == option for keyword in node.keywords)
    if isinstance(node, ast.Dict):
        return any(
            isinstance(key, ast.Constant) and key.value == option
            for key in node.keys
            if key is not None
        )
    return False


def _expression_allows_extra(node: ast.expr) -> bool:
    if isinstance(node, ast.Call):
        return any(
            keyword.arg == "extra" and _is_allow_value(keyword.value)
            for keyword in node.keywords
        )
    if isinstance(node, ast.Dict):
        return any(
            isinstance(key, ast.Constant)
            and key.value == "extra"
            and _is_allow_value(value)
            for key, value in zip(node.keys, node.values, strict=True)
            if key is not None
        )
    return False


def _is_allow_value(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.casefold() == "allow"
    ) or (isinstance(node, ast.Attribute) and node.attr.casefold() == "allow")


def _is_class_variable(annotation: ast.expr) -> bool:
    candidate = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    return _name(candidate) == "ClassVar"


def _pydantic_field_aliases(
    *nodes: ast.AST | None,
    field_factory_names: frozenset[str],
    field_factory_module_names: frozenset[str],
    wire_alias_constants: dict[str, str],
) -> list[tuple[str, int]]:
    aliases: list[tuple[str, int]] = []
    for root in nodes:
        if root is None:
            continue
        for node in ast.walk(root):
            if not isinstance(node, ast.Call) or not _is_pydantic_field_call(
                node,
                field_factory_names,
                field_factory_module_names,
            ):
                continue
            for keyword in node.keywords:
                if keyword.arg not in {"alias", "validation_alias"}:
                    continue
                aliases.extend(_wire_alias_values(keyword.value, wire_alias_constants))
    return aliases


def _is_pydantic_field_call(
    node: ast.Call,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in direct_aliases
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "Field"
        and parts[0] in module_aliases
    )


def _route_parameter_wire_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    depends_aliases: frozenset[str],
    depends_module_aliases: frozenset[str],
    wire_alias_constants: dict[str, str],
) -> list[tuple[str, int]]:
    parameters = _function_arguments(node)
    defaults_by_argument = _parameter_defaults(node)

    wire_names: list[tuple[str, int]] = []
    for argument in parameters:
        default = defaults_by_argument.get(id(argument))
        dependency_injected = isinstance(default, ast.Call) and _is_depends_call(
            default,
            set(depends_aliases),
            set(depends_module_aliases),
        )
        if not dependency_injected:
            wire_names.append((argument.arg, argument.lineno))
        wire_names.extend(
            _keyword_wire_aliases(
                argument.annotation,
                None if dependency_injected else default,
                wire_alias_constants=wire_alias_constants,
            )
        )
    return wire_names


def _unconstrained_mapping_request_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    depends_aliases: frozenset[str],
    depends_module_aliases: frozenset[str],
    body_aliases: frozenset[str],
    body_module_aliases: frozenset[str],
    unconstrained_type_names: frozenset[str],
    unconstrained_type_module_names: frozenset[str],
) -> list[tuple[str, int]]:
    defaults_by_argument = _parameter_defaults(node)
    mappings: list[tuple[str, int]] = []
    for argument in _function_arguments(node):
        if argument.annotation is None:
            continue
        default = defaults_by_argument.get(id(argument))
        is_mapping = _annotation_contains_mapping(argument.annotation)
        annotation_body_marker = any(
            isinstance(candidate, ast.Call)
            and _is_body_call(
                candidate,
                set(body_aliases),
                set(body_module_aliases),
            )
            for candidate in ast.walk(argument.annotation)
        )
        is_explicit_untyped_body = (
            (
                annotation_body_marker
                or (
                    isinstance(default, ast.Call)
                    and _is_body_call(
                        default,
                        set(body_aliases),
                        set(body_module_aliases),
                    )
                )
            )
            and _annotation_contains_unconstrained_type(
                argument.annotation,
                unconstrained_type_names,
                unconstrained_type_module_names,
            )
        )
        if is_explicit_untyped_body and _body_parameter_is_model_validated(
            node,
            argument.arg,
        ):
            continue
        if not is_mapping and not is_explicit_untyped_body:
            continue
        if isinstance(default, ast.Call) and _is_depends_call(
            default,
            set(depends_aliases),
            set(depends_module_aliases),
        ):
            continue
        mappings.append((argument.arg, argument.lineno))
    return mappings


def _body_parameter_is_model_validated(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parameter_name: str,
) -> bool:
    return any(
        isinstance(candidate, ast.Call)
        and isinstance(candidate.func, ast.Attribute)
        and candidate.func.attr == "model_validate"
        and any(
            isinstance(argument, ast.Name) and argument.id == parameter_name
            for argument in candidate.args
        )
        for statement in node.body
        for candidate in ast.walk(statement)
    )


def _parameter_defaults(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[int, ast.expr]:
    positional = [*node.args.posonlyargs, *node.args.args]
    positional_with_defaults = positional[-len(node.args.defaults) :] if node.args.defaults else []
    defaults = {
        id(argument): default
        for argument, default in zip(
            positional_with_defaults,
            node.args.defaults,
            strict=True,
        )
    }
    defaults.update(
        {
            id(argument): default
            for argument, default in zip(
                node.args.kwonlyargs,
                node.args.kw_defaults,
                strict=True,
            )
            if default is not None
        }
    )
    return defaults


def _annotation_contains_mapping(annotation: ast.expr) -> bool:
    mapping_names = {"dict", "Dict", "Mapping", "MutableMapping"}
    module_names = {"typing", "collections", "collections.abc"}
    for candidate in _annotation_reference_nodes(annotation):
        parts = _qualified_name_parts(candidate)
        if not parts or parts[-1] not in mapping_names:
            continue
        if len(parts) == 1 or ".".join(parts[:-1]) in module_names:
            return True
    return False


def _annotation_contains_unconstrained_type(
    annotation: ast.expr,
    direct_names: frozenset[str],
    module_names: frozenset[str],
) -> bool:
    for candidate in _annotation_reference_nodes(annotation):
        if isinstance(candidate, ast.Name) and candidate.id in direct_names:
            return True
        parts = _qualified_name_parts(candidate)
        if parts and len(parts) >= 2 and parts[-1] == "Any":
            if parts[0] in module_names:
                return True
    return False


def _keyword_wire_aliases(
    *nodes: ast.AST | None,
    wire_alias_constants: dict[str, str],
) -> list[tuple[str, int]]:
    aliases: list[tuple[str, int]] = []
    for root in nodes:
        if root is None:
            continue
        for node in ast.walk(root):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg not in {"alias", "validation_alias"}:
                    continue
                aliases.extend(_wire_alias_values(keyword.value, wire_alias_constants))
    return aliases


def _wire_alias_values(
    node: ast.AST,
    constants: dict[str, str],
) -> list[tuple[str, int]]:
    value = _constant_string_value(node, constants)
    if value is not None:
        return [(value, node.lineno)]
    children: list[ast.expr] = []
    if isinstance(node, ast.Call):
        children.extend(node.args)
        children.extend(keyword.value for keyword in node.keywords)
    elif isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        children.extend(node.elts)
    elif isinstance(node, ast.Dict):
        children.extend(key for key in node.keys if key is not None)
        children.extend(node.values)
    return [
        value
        for child in children
        for value in _wire_alias_values(child, constants)
    ]


def _string_constants(statements: list[ast.stmt]) -> dict[str, str]:
    assignments = [
        statement
        for statement in statements
        if isinstance(statement, (ast.Assign, ast.AnnAssign))
    ]
    constants: dict[str, str] = {}
    for assignment in assignments:
        value = _constant_string_value(assignment.value, constants)
        targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if value is None:
                constants.pop(target.id, None)
            else:
                constants[target.id] = value
    return constants


def _constant_string_value(
    node: ast.expr,
    constants: dict[str, str],
) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string_value(node.left, constants)
        right = _constant_string_value(node.right, constants)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                formatted = _constant_string_value(value.value, constants)
                if formatted is None:
                    return None
                parts.append(formatted)
            else:
                return None
        return "".join(parts)
    return None


def _programmatic_route_handler_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    service_paths: frozenset[Path],
    route_owner_names: dict[Path, frozenset[str]],
    mounted_route_owners: frozenset[tuple[Path, str]],
) -> frozenset[tuple[Path, int, str]]:
    trees_by_path = dict(parsed_modules)
    functions = _function_definitions(parsed_modules)
    scopes = _function_scopes(parsed_modules)
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    handlers: set[tuple[Path, int, str]] = set()
    for path, tree in parsed_modules:
        mounted_names = {
            name for owner_path, name in mounted_route_owners if owner_path == path
        }
        if path not in service_paths and not mounted_names:
            continue
        route_owners = route_owner_names.get(path, frozenset())
        route_constructor_aliases = _starlette_route_constructor_aliases(tree)
        route_module_aliases = _starlette_route_module_aliases(tree)
        middleware_aliases = _base_http_middleware_aliases(tree)
        middleware_module_aliases = _base_http_middleware_module_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _is_starlette_route_constructor(
                node,
                route_constructor_aliases,
                route_module_aliases,
            ):
                endpoint = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "endpoint"
                    ),
                    node.args[1] if len(node.args) > 1 else None,
                )
            elif isinstance(node.func, ast.Call) and _is_http_middleware_decorator(
                node.func,
                route_owners,
            ):
                endpoint = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg in {"func", "handler"}
                    ),
                    node.args[0] if node.args else None,
                )
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_middleware"
                and _active_route_owner_reference(
                    node.func.value,
                    path_in_services=path in service_paths,
                    mounted_names=mounted_names,
                    route_owners=route_owners,
                )
                and node.args
                and _is_base_http_middleware_reference(
                    node.args[0],
                    middleware_aliases,
                    middleware_module_aliases,
                )
            ):
                endpoint = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "dispatch"
                    ),
                    None,
                )
            else:
                if (
                    not isinstance(node.func, ast.Attribute)
                    or node.func.attr not in PROGRAMMATIC_ROUTE_REGISTRARS
                ):
                    continue
                if not _active_route_owner_reference(
                    node.func.value,
                    path_in_services=path in service_paths,
                    mounted_names=mounted_names,
                    route_owners=route_owners,
                ):
                    continue
                endpoint = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "endpoint"
                    ),
                    node.args[1] if len(node.args) > 1 else None,
                )
            if endpoint is None:
                continue
            owner_key = _enclosing_function_key(path, node, functions, scopes)
            target = _resolve_bound_method_key(
                root=root,
                path=path,
                tree=tree,
                reference=endpoint,
                paths_by_module=paths_by_module,
                trees_by_path=trees_by_path,
                classes=classes,
                class_scopes=class_scopes,
                function_scopes=scopes,
                owner_key=owner_key,
            ) or _resolve_callable_instance_key(
                root=root,
                path=path,
                tree=tree,
                reference=endpoint,
                paths_by_module=paths_by_module,
                trees_by_path=trees_by_path,
                classes=classes,
                class_scopes=class_scopes,
                function_scopes=scopes,
                owner_key=owner_key,
            ) or _resolve_function_key(
                root,
                path,
                tree,
                endpoint,
                paths_by_module,
                trees_by_path,
                functions,
                scopes,
                owner_key,
            )
            if target is not None:
                handlers.add(target)
    return frozenset(handlers)


def _active_route_owner_reference(
    node: ast.AST,
    *,
    path_in_services: bool,
    mounted_names: set[str],
    route_owners: frozenset[str],
) -> bool:
    owner_parts = _qualified_name_parts(node)
    return bool(
        owner_parts
        and _is_route_owner_reference(node, route_owners)
        and (
            path_in_services
            or owner_parts[-1] in mounted_names
            or ".".join(owner_parts) in mounted_names
        )
    )


def _base_http_middleware_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "starlette.middleware.base"
        for imported in node.names
        if imported.name == "BaseHTTPMiddleware"
    )


def _base_http_middleware_module_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name in {"starlette", "starlette.middleware.base"}
    )


def _is_base_http_middleware_reference(
    node: ast.AST,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> bool:
    if isinstance(node, ast.Name):
        return node.id in direct_aliases
    parts = _qualified_name_parts(node)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "BaseHTTPMiddleware"
        and parts[0] in module_aliases
    )


def _resolve_bound_method_key(
    *,
    root: Path,
    path: Path,
    tree: ast.Module,
    reference: ast.AST,
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    classes: dict[tuple[Path, int, str], ast.ClassDef],
    class_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    function_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    owner_key: tuple[Path, int, str] | None,
) -> tuple[Path, int, str] | None:
    if not isinstance(reference, ast.Attribute):
        return None

    owner_scope = function_scopes.get(owner_key, ())
    constructor: ast.AST | None = None
    constructor_scope = owner_scope
    constructor_line = reference.lineno
    if isinstance(reference.value, ast.Call):
        constructor = reference.value.func
    elif isinstance(reference.value, ast.Name):
        assignments = [
            (key, value, scope)
            for key, value, scope, _ in _module_assignment_entries(path, tree)
            if key[2] == reference.value.id
            and isinstance(value, ast.Call)
            and len(scope) <= len(owner_scope)
            and owner_scope[: len(scope)] == scope
            and key[1] <= reference.lineno
        ]
        if assignments:
            key, value, constructor_scope = max(
                assignments,
                key=lambda entry: (len(entry[2]), entry[0][1]),
            )
            constructor = value.func
            constructor_line = key[1]
    if constructor is None:
        return None

    class_key = _resolve_model_key(
        root,
        path,
        tree,
        constructor,
        paths_by_module,
        trees_by_path,
        classes,
        class_scopes,
        None,
        owner_scope=constructor_scope,
        reference_line=constructor_line,
    )
    if class_key is None:
        return None
    return _resolve_class_method_key(
        root=root,
        class_key=class_key,
        method_name=reference.attr,
        paths_by_module=paths_by_module,
        trees_by_path=trees_by_path,
        classes=classes,
        class_scopes=class_scopes,
    )


def _resolve_callable_instance_key(
    *,
    root: Path,
    path: Path,
    tree: ast.Module,
    reference: ast.AST,
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    classes: dict[tuple[Path, int, str], ast.ClassDef],
    class_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    function_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    owner_key: tuple[Path, int, str] | None,
) -> tuple[Path, int, str] | None:
    owner_scope = function_scopes.get(owner_key, ())
    constructor: ast.AST | None = None
    constructor_scope = owner_scope
    constructor_line = reference.lineno
    if isinstance(reference, ast.Call):
        constructor = reference.func
    elif isinstance(reference, ast.Name):
        assignments = [
            (key, value, scope)
            for key, value, scope, _ in _module_assignment_entries(path, tree)
            if key[2] == reference.id
            and isinstance(value, ast.Call)
            and len(scope) <= len(owner_scope)
            and owner_scope[: len(scope)] == scope
            and key[1] <= reference.lineno
        ]
        if assignments:
            key, value, constructor_scope = max(
                assignments,
                key=lambda entry: (len(entry[2]), entry[0][1]),
            )
            constructor = value.func
            constructor_line = key[1]
    if constructor is None:
        return None

    class_key = _resolve_model_key(
        root,
        path,
        tree,
        constructor,
        paths_by_module,
        trees_by_path,
        classes,
        class_scopes,
        None,
        owner_scope=constructor_scope,
        reference_line=constructor_line,
    )
    if class_key is None:
        return None
    return _resolve_class_method_key(
        root=root,
        class_key=class_key,
        method_name="__call__",
        paths_by_module=paths_by_module,
        trees_by_path=trees_by_path,
        classes=classes,
        class_scopes=class_scopes,
    )


def _resolve_class_method_key(
    *,
    root: Path,
    class_key: tuple[Path, int, str],
    method_name: str,
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    classes: dict[tuple[Path, int, str], ast.ClassDef],
    class_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
) -> tuple[Path, int, str] | None:
    for candidate_key in _resolve_class_mro_keys(
        root=root,
        class_key=class_key,
        paths_by_module=paths_by_module,
        trees_by_path=trees_by_path,
        classes=classes,
        class_scopes=class_scopes,
    ):
        class_node = classes.get(candidate_key)
        if class_node is None:
            continue
        direct_method = next(
            (
                child
                for child in class_node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == method_name
            ),
            None,
        )
        if direct_method is not None:
            return (candidate_key[0], direct_method.lineno, direct_method.name)
    return None


def _resolve_class_mro_keys(
    *,
    root: Path,
    class_key: tuple[Path, int, str],
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    classes: dict[tuple[Path, int, str], ast.ClassDef],
    class_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    active: frozenset[tuple[Path, int, str]] = frozenset(),
) -> tuple[tuple[Path, int, str], ...]:
    if class_key in active:
        return (class_key,)
    class_node = classes.get(class_key)
    if class_node is None:
        return (class_key,)

    path = class_key[0]
    base_keys = [
        base_key
        for base in class_node.bases
        if (
            base_key := _resolve_model_key(
                root,
                path,
                trees_by_path[path],
                base,
                paths_by_module,
                trees_by_path,
                classes,
                class_scopes,
                class_key,
            )
        )
        is not None
    ]
    sequences = [
        list(
            _resolve_class_mro_keys(
                root=root,
                class_key=base_key,
                paths_by_module=paths_by_module,
                trees_by_path=trees_by_path,
                classes=classes,
                class_scopes=class_scopes,
                active=active | {class_key},
            )
        )
        for base_key in base_keys
    ]
    sequences.append(list(base_keys))

    merged: list[tuple[Path, int, str]] = []
    while any(sequences):
        candidate = next(
            (
                sequence[0]
                for sequence in sequences
                if sequence
                and not any(
                    sequence[0] in other[1:]
                    for other in sequences
                    if other
                )
            ),
            None,
        )
        if candidate is None:
            for sequence in sequences:
                for remaining in sequence:
                    if remaining not in merged:
                        merged.append(remaining)
            break
        if candidate not in merged:
            merged.append(candidate)
        for sequence in sequences:
            if sequence and sequence[0] == candidate:
                sequence.pop(0)

    return (class_key, *merged)


def _function_definitions(
    parsed_modules: list[tuple[Path, ast.Module]],
) -> dict[tuple[Path, int, str], ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        key: node
        for path, tree in parsed_modules
        for key, node, _ in _module_function_entries(path, tree)
    }


def _function_scopes(
    parsed_modules: list[tuple[Path, ast.Module]],
) -> dict[tuple[Path, int, str], tuple[int, ...]]:
    return {
        key: scope
        for path, tree in parsed_modules
        for key, _, scope in _module_function_entries(path, tree)
    }


def _module_function_entries(
    path: Path,
    tree: ast.Module,
) -> list[
    tuple[
        tuple[Path, int, str],
        ast.FunctionDef | ast.AsyncFunctionDef,
        tuple[int, ...],
    ]
]:
    entries: list[
        tuple[
            tuple[Path, int, str],
            ast.FunctionDef | ast.AsyncFunctionDef,
            tuple[int, ...],
        ]
    ] = []

    class Collector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[int] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

        def _visit_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            key = (path, node.lineno, node.name)
            entries.append((key, node, tuple(self.scope)))
            self.scope.append(node.lineno)
            for statement in node.body:
                self.visit(statement)
            self.scope.pop()

    Collector().visit(tree)
    return entries


def _enclosing_function_key(
    path: Path,
    node: ast.AST,
    functions: dict[tuple[Path, int, str], ast.FunctionDef | ast.AsyncFunctionDef],
    scopes: dict[tuple[Path, int, str], tuple[int, ...]],
) -> tuple[Path, int, str] | None:
    candidates = [
        key
        for key, function in functions.items()
        if key[0] == path
        and function.lineno <= node.lineno <= (function.end_lineno or function.lineno)
    ]
    return max(candidates, key=lambda key: (len(scopes[key]), key[1]), default=None)


def _resolve_function_key(
    root: Path,
    path: Path,
    tree: ast.Module,
    reference: ast.AST,
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    functions: dict[tuple[Path, int, str], ast.FunctionDef | ast.AsyncFunctionDef],
    scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    owner_key: tuple[Path, int, str] | None,
) -> tuple[Path, int, str] | None:
    target = _resolve_reference_target(
        root,
        path,
        tree,
        reference,
        paths_by_module,
        trees_by_path,
    )
    if target is None:
        return None
    target_path, target_name = target
    candidates = [
        key for key in functions if key[0] == target_path and key[2] == target_name
    ]
    if not candidates:
        return None

    parts = _qualified_name_parts(reference) or []
    symbol_targets, _ = _import_targets(root, path, tree, paths_by_module)
    imported = target_path != path or len(parts) > 1 or (
        bool(parts) and parts[0] in symbol_targets
    )
    if imported:
        top_level = [key for key in candidates if not scopes[key]]
        return min(top_level or candidates, key=lambda key: key[1])

    owner_scope = scopes.get(owner_key, ())
    visible = [
        key
        for key in candidates
        if len(scopes[key]) <= len(owner_scope)
        and owner_scope[: len(scopes[key])] == scopes[key]
        and key[1] <= reference.lineno
    ]
    return max(
        visible,
        key=lambda key: (len(scopes[key]), key[1]),
        default=None,
    )


def _boundary_function_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    programmatic_handlers: frozenset[tuple[Path, int, str]],
    service_paths: frozenset[Path],
    route_owner_names: dict[Path, frozenset[str]],
    mounted_route_owners: frozenset[tuple[Path, str]],
) -> frozenset[tuple[Path, int, str]]:
    trees_by_path = dict(parsed_modules)
    functions = _function_definitions(parsed_modules)
    scopes = _function_scopes(parsed_modules)
    boundary: set[tuple[Path, int, str]] = set(programmatic_handlers)
    for key, node in functions.items():
        path = key[0]
        mounted_names = {
            name for owner_path, name in mounted_route_owners if owner_path == path
        }
        if _is_route_handler(
            node,
            frozenset(),
            route_owner_names.get(path, frozenset()),
        ) and (
            path in service_paths
            or _route_handler_uses_owner(node, mounted_names)
        ):
            boundary.add(key)

    for path, tree in parsed_modules:
        if path not in service_paths and not any(
            owner_path == path for owner_path, _ in mounted_route_owners
        ):
            continue
        for call in (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        ):
            for keyword in call.keywords:
                if keyword.arg == "dependencies" and _is_framework_dependency_call(
                    call,
                    tree,
                    route_owner_names.get(path, frozenset()),
                ):
                    owner_key = _enclosing_function_key(path, call, functions, scopes)
                    boundary.update(
                        _dependency_targets(
                            root,
                            path,
                            tree,
                            (keyword.value,),
                            paths_by_module,
                            trees_by_path,
                            functions,
                            scopes,
                            owner_key,
                        )
                    )

    pending = list(boundary)
    while pending:
        key = pending.pop()
        function = functions.get(key)
        if function is None:
            continue
        path, _, _ = key
        dependency_nodes: list[ast.AST] = [
            *function.decorator_list,
            *function.args.defaults,
            *(default for default in function.args.kw_defaults if default is not None),
            *(argument.annotation for argument in _function_arguments(function) if argument.annotation),
        ]
        discovered = _dependency_targets(
            root,
            path,
            trees_by_path[path],
            dependency_nodes,
            paths_by_module,
            trees_by_path,
            functions,
            scopes,
            key,
        )
        for target in discovered - boundary:
            boundary.add(target)
            pending.append(target)
    return frozenset(boundary)


def _is_framework_dependency_call(
    call: ast.Call,
    tree: ast.Module,
    route_owner_names: frozenset[str],
) -> bool:
    constructor_aliases: set[str] = set()
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and (
            node.module == "fastapi"
            or node.module.startswith("fastapi.")
            or node.module == "starlette"
            or node.module.startswith("starlette.")
        ):
            constructor_aliases.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name in {"FastAPI", "APIRouter", "Starlette"}
            )
        elif isinstance(node, ast.Import):
            module_aliases.update(
                imported.asname or imported.name.split(".")[0]
                for imported in node.names
                if imported.name == "fastapi"
                or imported.name.startswith("fastapi.")
                or imported.name == "starlette"
                or imported.name.startswith("starlette.")
            )
    if _is_framework_constructor(call, constructor_aliases, module_aliases):
        return True
    if not isinstance(call.func, ast.Attribute):
        return False
    return call.func.attr in (
        ROUTE_METHODS
        | GENERIC_ROUTE_DECORATORS
        | PROGRAMMATIC_ROUTE_REGISTRARS
        | {"include_router"}
    ) and _is_route_owner_reference(call.func.value, route_owner_names)


def _dependency_targets(
    root: Path,
    path: Path,
    tree: ast.Module,
    nodes: object,
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    functions: dict[tuple[Path, int, str], ast.FunctionDef | ast.AsyncFunctionDef],
    scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    owner_key: tuple[Path, int, str] | None,
) -> set[tuple[Path, int, str]]:
    depends_aliases, fastapi_module_aliases = _depends_aliases(tree)
    targets: set[tuple[Path, int, str]] = set()
    for root_node in nodes if isinstance(nodes, (list, tuple)) else (nodes,):
        if not isinstance(root_node, ast.AST):
            continue
        for candidate in ast.walk(root_node):
            if not isinstance(candidate, ast.Call) or not _is_depends_call(
                candidate,
                depends_aliases,
                fastapi_module_aliases,
            ):
                continue
            dependency = next(
                (
                    keyword.value
                    for keyword in candidate.keywords
                    if keyword.arg == "dependency"
                ),
                candidate.args[0] if candidate.args else None,
            )
            if dependency is None:
                continue
            target = _resolve_function_key(
                root,
                path,
                tree,
                dependency,
                paths_by_module,
                trees_by_path,
                functions,
                scopes,
                owner_key,
            )
            if target is not None:
                targets.add(target)
    return targets


def _depends_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    direct: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and (
            node.module == "fastapi" or node.module.startswith("fastapi.")
        ):
            direct.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name in {"Depends", "Security"}
            )
        elif isinstance(node, ast.Import):
            modules.update(
                imported.asname or imported.name.split(".")[0]
                for imported in node.names
                if imported.name == "fastapi" or imported.name.startswith("fastapi.")
            )
    return direct, modules


def _body_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    direct: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None and (
            node.module == "fastapi" or node.module.startswith("fastapi.")
        ):
            direct.update(
                imported.asname or imported.name
                for imported in node.names
                if imported.name == "Body"
            )
        elif isinstance(node, ast.Import):
            modules.update(
                imported.asname or imported.name.split(".")[0]
                for imported in node.names
                if imported.name == "fastapi" or imported.name.startswith("fastapi.")
            )
    return direct, modules


def _is_body_call(
    node: ast.Call,
    direct_aliases: set[str],
    module_aliases: set[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in direct_aliases
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "Body"
        and parts[0] in module_aliases
    )


def _is_depends_call(
    node: ast.Call,
    direct_aliases: set[str],
    module_aliases: set[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in direct_aliases
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] in {"Depends", "Security"}
        and parts[0] in module_aliases
    )


def _referenced_model_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    boundary_functions: frozenset[tuple[Path, int, str]],
    model_keys: frozenset[tuple[Path, int, str]],
) -> frozenset[tuple[Path, int, str]]:
    trees_by_path = dict(parsed_modules)
    functions = _function_definitions(parsed_modules)
    function_scopes = _function_scopes(parsed_modules)
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    functional_typed_dicts = _functional_typed_dict_definitions(parsed_modules)
    created_pydantic_models = _pydantic_create_model_definitions(parsed_modules)
    model_definitions: dict[tuple[Path, int, str], ast.AST] = {
        **classes,
        **functional_typed_dicts,
        **created_pydantic_models,
    }
    model_scopes = {
        **class_scopes,
        **{
            key: scope
            for path, tree in parsed_modules
            for key, _, scope, _ in _module_assignment_entries(path, tree)
            if key in functional_typed_dicts
        },
        **{
            key: scope
            for path, tree in parsed_modules
            for key, _, scope, _ in _module_assignment_entries(path, tree)
            if key in created_pydantic_models
        },
    }
    referenced: set[tuple[Path, int, str]] = set()
    for key in boundary_functions:
        function = functions.get(key)
        if function is None:
            continue
        path, _, _ = key
        for argument in _function_arguments(function):
            if argument.annotation is None:
                continue
            for candidate in _annotation_reference_nodes(argument.annotation):
                if not isinstance(candidate, (ast.Name, ast.Attribute)):
                    continue
                target = _resolve_model_key(
                    root,
                    path,
                    trees_by_path[path],
                    candidate,
                    paths_by_module,
                    trees_by_path,
                    model_definitions,
                    model_scopes,
                    None,
                    owner_scope=function_scopes.get(key, ()),
                    reference_line=argument.lineno,
                )
                if target in model_keys:
                    referenced.add(target)
    return _model_reference_closure(
        root,
        parsed_modules,
        paths_by_module,
        frozenset(referenced),
        model_keys,
    )


def _model_reference_closure(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    initial: frozenset[tuple[Path, int, str]],
    model_keys: frozenset[tuple[Path, int, str]],
) -> frozenset[tuple[Path, int, str]]:
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    functional_typed_dicts = _functional_typed_dict_definitions(parsed_modules)
    created_pydantic_models = _pydantic_create_model_definitions(parsed_modules)
    model_definitions: dict[tuple[Path, int, str], ast.AST] = {
        **classes,
        **functional_typed_dicts,
        **created_pydantic_models,
    }
    model_scopes = {
        **class_scopes,
        **{
            key: scope
            for path, tree in parsed_modules
            for key, _, scope, _ in _module_assignment_entries(path, tree)
            if key in functional_typed_dicts
        },
        **{
            key: scope
            for path, tree in parsed_modules
            for key, _, scope, _ in _module_assignment_entries(path, tree)
            if key in created_pydantic_models
        },
    }
    trees_by_path = dict(parsed_modules)
    closure = set(initial)
    pending = list(initial)
    while pending:
        key = pending.pop()
        model = model_definitions.get(key)
        if model is None:
            continue
        path, _, _ = key
        if isinstance(model, ast.ClassDef):
            reference_roots: list[ast.AST] = [*model.bases]
            reference_roots.extend(
                child.annotation
                for child in model.body
                if isinstance(child, ast.AnnAssign)
            )
        elif key in functional_typed_dicts and isinstance(model, ast.Call):
            reference_roots = _functional_typed_dict_type_roots(model)
        elif key in created_pydantic_models and isinstance(model, ast.Call):
            reference_roots = _pydantic_create_model_type_roots(model)
        else:
            reference_roots = []
        for reference_root in reference_roots:
            for candidate in _annotation_reference_nodes(reference_root):
                if not isinstance(candidate, (ast.Name, ast.Attribute)):
                    continue
                target = _resolve_model_key(
                    root,
                    path,
                    trees_by_path[path],
                    candidate,
                    paths_by_module,
                    trees_by_path,
                    model_definitions,
                    model_scopes,
                    key,
                    reference_line=getattr(reference_root, "lineno", model.lineno),
                )
                if target in model_keys and target not in closure:
                    closure.add(target)
                    pending.append(target)
    return frozenset(closure)


def _function_arguments(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.arg]:
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    return arguments


def _annotation_reference_nodes(annotation: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []
    pending = [annotation]
    seen_strings: set[str] = set()
    while pending:
        root = pending.pop()
        for candidate in ast.walk(root):
            nodes.append(candidate)
            if (
                isinstance(candidate, ast.Constant)
                and isinstance(candidate.value, str)
                and candidate.value not in seen_strings
            ):
                seen_strings.add(candidate.value)
                try:
                    parsed = ast.parse(candidate.value, mode="eval")
                except SyntaxError:
                    continue
                pending.append(parsed.body)
    return nodes


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _import_targets(
    root: Path,
    path: Path,
    tree: ast.Module,
    paths_by_module: dict[str, Path],
) -> tuple[dict[str, tuple[Path, str]], dict[tuple[str, ...], Path]]:
    current_parts = _module_name(root, path).split(".")
    if path.name != "__init__.py":
        current_parts = current_parts[:-1]

    symbol_targets: dict[str, tuple[Path, str]] = {}
    module_targets: dict[tuple[str, ...], Path] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                target_path = paths_by_module.get(imported.name)
                if target_path is None:
                    continue
                local_parts = (
                    (imported.asname,)
                    if imported.asname is not None
                    else tuple(imported.name.split("."))
                )
                module_targets[local_parts] = target_path
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            trim = node.level - 1
            if trim > len(current_parts):
                continue
            module_parts = current_parts[: len(current_parts) - trim]
            if node.module:
                module_parts.extend(node.module.split("."))
        else:
            module_parts = node.module.split(".") if node.module else []
        module_name = ".".join(module_parts)
        target_path = paths_by_module.get(module_name)
        for imported in node.names:
            if imported.name == "*":
                continue
            local_name = imported.asname or imported.name
            imported_module = ".".join([*module_parts, imported.name])
            imported_path = paths_by_module.get(imported_module)
            if imported_path is not None:
                module_targets[(local_name,)] = imported_path
            elif target_path is not None:
                symbol_targets[local_name] = (target_path, imported.name)
    return symbol_targets, module_targets


def _resolve_reference_target(
    root: Path,
    path: Path,
    tree: ast.Module,
    node: ast.AST,
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module] | None = None,
) -> tuple[Path, str] | None:
    parts = _qualified_name_parts(node)
    if not parts:
        return None
    symbol_targets, module_targets = _import_targets(root, path, tree, paths_by_module)
    if len(parts) == 1:
        target = symbol_targets.get(parts[0], (path, parts[0]))
    else:
        module_path = module_targets.get(tuple(parts[:-1]))
        target = (module_path, parts[-1]) if module_path is not None else None
    if target is None or trees_by_path is None:
        return target
    return _follow_symbol_reexports(
        root,
        target,
        paths_by_module,
        trees_by_path,
    )


def _resolve_model_key(
    root: Path,
    path: Path,
    tree: ast.Module,
    reference: ast.AST,
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    definitions: dict[tuple[Path, int, str], ast.AST],
    scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    owner_key: tuple[Path, int, str] | None,
    *,
    owner_scope: tuple[int, ...] | None = None,
    reference_line: int | None = None,
    seen_aliases: frozenset[tuple[Path, int, str]] = frozenset(),
) -> tuple[Path, int, str] | None:
    target = _resolve_reference_target(
        root,
        path,
        tree,
        reference,
        paths_by_module,
        trees_by_path,
    )
    if target is None:
        return None
    target_path, target_name = target
    parts = _qualified_name_parts(reference) or []
    symbol_targets, _ = _import_targets(root, path, tree, paths_by_module)
    imported = target_path != path or len(parts) > 1 or (
        bool(parts) and parts[0] in symbol_targets
    )
    lexical_scope = owner_scope if owner_scope is not None else scopes.get(owner_key, ())
    line_number = reference_line if reference_line is not None else reference.lineno
    candidates = [
        key for key in definitions if key[0] == target_path and key[2] == target_name
    ]
    if candidates:
        if imported:
            top_level = [key for key in candidates if not scopes[key]]
            return min(top_level or candidates, key=lambda key: key[1])
        visible = [
            key
            for key in candidates
            if len(scopes[key]) <= len(lexical_scope)
            and lexical_scope[: len(scopes[key])] == scopes[key]
            and key[1] <= line_number
        ]
        return max(
            visible,
            key=lambda key: (len(scopes[key]), key[1]),
            default=None,
        )

    target_tree = trees_by_path.get(target_path)
    if target_tree is None:
        return None
    aliases = [
        entry
        for entry in _module_assignment_entries(target_path, target_tree)
        if entry[0][2] == target_name
        and entry[0] not in seen_aliases
        and _is_model_type_alias(entry[1], entry[3])
        and (
            (imported and not entry[2])
            or (
                not imported
                and len(entry[2]) <= len(lexical_scope)
                and lexical_scope[: len(entry[2])] == entry[2]
                and entry[0][1] <= line_number
            )
        )
    ]
    aliases.sort(key=lambda entry: (len(entry[2]), entry[0][1]), reverse=True)
    for alias_key, value, alias_scope, _ in aliases:
        for candidate in _annotation_reference_nodes(value):
            if not isinstance(candidate, (ast.Name, ast.Attribute, ast.Subscript)):
                continue
            resolved = _resolve_model_key(
                root,
                target_path,
                target_tree,
                candidate,
                paths_by_module,
                trees_by_path,
                definitions,
                scopes,
                None,
                owner_scope=alias_scope,
                reference_line=alias_key[1],
                seen_aliases=seen_aliases | {alias_key},
            )
            if resolved is not None:
                return resolved
    return None


def _is_model_type_alias(
    value: ast.expr,
    annotation: ast.expr | None,
) -> bool:
    if annotation is not None and any(
        _name(candidate) in {"TypeAlias", "TypeAliasType"}
        for candidate in _annotation_reference_nodes(annotation)
    ):
        return True
    if isinstance(value, ast.Call) and _name(value.func) == "TypeAliasType":
        return len(value.args) > 1 or any(
            keyword.arg == "value" for keyword in value.keywords
        )
    return isinstance(value, (ast.Name, ast.Attribute, ast.Subscript, ast.BinOp))


def _follow_symbol_reexports(
    root: Path,
    target: tuple[Path, str],
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
) -> tuple[Path, str]:
    seen: set[tuple[Path, str]] = set()
    while target not in seen:
        seen.add(target)
        target_path, target_name = target
        tree = trees_by_path.get(target_path)
        if tree is None or any(
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == target_name
            for node in tree.body
        ):
            return target
        symbol_targets, _ = _import_targets(
            root,
            target_path,
            tree,
            paths_by_module,
        )
        next_target = symbol_targets.get(target_name)
        if next_target is None:
            return target
        target = next_target
    return target


def _qualified_name_parts(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name_parts(node.value)
        return [*prefix, node.attr] if prefix is not None else None
    if isinstance(node, ast.Subscript):
        return _qualified_name_parts(node.value)
    return None


def _route_body_wire_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    request_type_names: frozenset[str],
    request_module_names: frozenset[str],
    websocket_type_names: frozenset[str],
    websocket_module_names: frozenset[str],
    json_loads_names: frozenset[str],
    json_module_names: frozenset[str],
) -> list[tuple[str, int]]:
    arguments = _function_arguments(node)
    request_names = {
        argument.arg
        for argument in arguments
        if argument.annotation is not None
        and _annotation_contains_request_type(
            argument.annotation,
            request_type_names,
            request_module_names,
        )
    }
    request_names.update(
        argument.arg
        for argument in arguments
        if argument.annotation is None and argument.arg == "request"
    )
    websocket_names = {
        argument.arg
        for argument in arguments
        if argument.annotation is not None
        and _annotation_contains_websocket_type(
            argument.annotation,
            websocket_type_names,
            websocket_module_names,
        )
    }
    websocket_names.update(
        argument.arg
        for argument in arguments
        if argument.annotation is None and argument.arg in {"socket", "websocket"}
    )
    if not request_names and not websocket_names:
        return []

    request_aliases = set(request_names)
    websocket_aliases = set(websocket_names)
    raw_mapping_names: set[str] = set()
    request_body_names: set[str] = set()
    websocket_text_names: set[str] = set()
    assignments = [
        candidate
        for statement in node.body
        for candidate in ast.walk(statement)
        if isinstance(candidate, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            targets, value = _assignment_targets_and_value(assignment)
            if isinstance(value, ast.Name) and value.id in request_aliases:
                previous_size = len(request_aliases)
                request_aliases.update(targets)
                changed = changed or len(request_aliases) != previous_size
            if isinstance(value, ast.Name) and value.id in websocket_aliases:
                previous_size = len(websocket_aliases)
                websocket_aliases.update(targets)
                changed = changed or len(websocket_aliases) != previous_size
            if _is_websocket_text_value(
                value,
                websocket_aliases,
                websocket_text_names,
            ):
                previous_size = len(websocket_text_names)
                websocket_text_names.update(targets)
                changed = changed or len(websocket_text_names) != previous_size
            if _is_request_body_value(
                value,
                request_aliases,
                request_body_names,
            ):
                previous_size = len(request_body_names)
                request_body_names.update(targets)
                changed = changed or len(request_body_names) != previous_size
            if _is_raw_request_mapping(
                value,
                request_aliases,
                websocket_aliases,
                raw_mapping_names,
                request_body_names,
                websocket_text_names,
                json_loads_names,
                json_module_names,
            ):
                previous_size = len(raw_mapping_names)
                raw_mapping_names.update(targets)
                changed = changed or len(raw_mapping_names) != previous_size

    wire_names: list[tuple[str, int]] = []
    for statement in node.body:
        for candidate in ast.walk(statement):
            if isinstance(candidate, ast.Subscript):
                key = candidate.slice
                container = candidate.value
            elif (
                isinstance(candidate, ast.Call)
                and isinstance(candidate.func, ast.Attribute)
                and candidate.func.attr in {"get", "pop", "setdefault"}
                and candidate.args
            ):
                key = candidate.args[0]
                container = candidate.func.value
            else:
                continue
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and _is_raw_request_mapping(
                    container,
                    request_aliases,
                    websocket_aliases,
                    raw_mapping_names,
                    request_body_names,
                    websocket_text_names,
                    json_loads_names,
                    json_module_names,
                )
            ):
                wire_names.append((key.value, key.lineno))
    return wire_names


def _request_type_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module in {"fastapi", "fastapi.requests", "starlette.requests"}
        for imported in node.names
        if imported.name == "Request"
    )


def _request_module_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name
        in {"fastapi", "fastapi.requests", "starlette", "starlette.requests"}
    )


def _websocket_type_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module in {"fastapi", "fastapi.websockets", "starlette.websockets"}
        for imported in node.names
        if imported.name == "WebSocket"
    )


def _websocket_module_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name
        in {"fastapi", "fastapi.websockets", "starlette", "starlette.websockets"}
    )


def _json_loads_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "json"
        for imported in node.names
        if imported.name == "loads"
    )


def _json_module_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name == "json"
    )


def _is_json_loads_call(
    node: ast.Call,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in direct_aliases
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and len(parts) == 2
        and parts[-1] == "loads"
        and parts[0] in module_aliases
    )


def _annotation_contains_request_type(
    annotation: ast.expr,
    request_type_names: frozenset[str],
    request_module_names: frozenset[str],
) -> bool:
    for candidate in _annotation_reference_nodes(annotation):
        if isinstance(candidate, ast.Name) and candidate.id in request_type_names:
            return True
        if isinstance(candidate, ast.Attribute) and candidate.attr == "Request":
            parts = _qualified_name_parts(candidate)
            if parts and parts[0] in request_module_names:
                return True
    return False


def _annotation_contains_websocket_type(
    annotation: ast.expr,
    websocket_type_names: frozenset[str],
    websocket_module_names: frozenset[str],
) -> bool:
    for candidate in _annotation_reference_nodes(annotation):
        if isinstance(candidate, ast.Name) and candidate.id in websocket_type_names:
            return True
        if isinstance(candidate, ast.Attribute) and candidate.attr == "WebSocket":
            parts = _qualified_name_parts(candidate)
            if parts and parts[0] in websocket_module_names:
                return True
    return False


def _assignment_targets_and_value(
    node: ast.Assign | ast.AnnAssign | ast.NamedExpr,
) -> tuple[set[str], ast.expr]:
    if isinstance(node, ast.Assign):
        target_nodes = node.targets
    else:
        target_nodes = [node.target]
    targets = {
        candidate.id
        for target in target_nodes
        for candidate in ast.walk(target)
        if isinstance(candidate, ast.Name)
    }
    return targets, node.value


def _is_raw_request_mapping(
    node: ast.AST,
    request_aliases: set[str],
    websocket_aliases: set[str],
    raw_mapping_names: set[str],
    request_body_names: set[str],
    websocket_text_names: set[str],
    json_loads_names: frozenset[str],
    json_module_names: frozenset[str],
) -> bool:
    if isinstance(node, ast.Await):
        return _is_raw_request_mapping(
            node.value,
            request_aliases,
            websocket_aliases,
            raw_mapping_names,
            request_body_names,
            websocket_text_names,
            json_loads_names,
            json_module_names,
        )
    if isinstance(node, ast.Name):
        return node.id in raw_mapping_names
    if isinstance(node, ast.Subscript):
        return _is_raw_request_mapping(
            node.value,
            request_aliases,
            websocket_aliases,
            raw_mapping_names,
            request_body_names,
            websocket_text_names,
            json_loads_names,
            json_module_names,
        )
    if (
        isinstance(node, ast.Attribute)
        and node.attr in {"cookies", "headers", "path_params", "query_params"}
        and isinstance(node.value, ast.Name)
        and node.value.id in request_aliases
    ):
        return True
    if not isinstance(node, ast.Call):
        return False
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"json", "form"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in request_aliases
    ):
        return True
    if _is_json_loads_call(node, json_loads_names, json_module_names):
        return any(
            _is_request_body_value(
                argument,
                request_aliases,
                request_body_names,
            )
            or _is_websocket_text_value(
                argument,
                websocket_aliases,
                websocket_text_names,
            )
            for argument in node.args
        )
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "receive_json"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in websocket_aliases
    ):
        return True
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"get", "pop", "setdefault", "copy"}
        and _is_raw_request_mapping(
            node.func.value,
            request_aliases,
            websocket_aliases,
            raw_mapping_names,
            request_body_names,
            websocket_text_names,
            json_loads_names,
            json_module_names,
        )
    ):
        return True
    return _name(node.func) == "dict" and any(
        _is_raw_request_mapping(
            argument,
            request_aliases,
            websocket_aliases,
            raw_mapping_names,
            request_body_names,
            websocket_text_names,
            json_loads_names,
            json_module_names,
        )
        for argument in node.args
    )


def _is_request_body_value(
    node: ast.AST,
    request_aliases: set[str],
    request_body_names: set[str],
) -> bool:
    if isinstance(node, ast.Await):
        return _is_request_body_value(
            node.value,
            request_aliases,
            request_body_names,
        )
    if isinstance(node, ast.Name):
        return node.id in request_body_names
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "body"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in request_aliases
    )


def _is_websocket_text_value(
    node: ast.AST,
    websocket_aliases: set[str],
    websocket_text_names: set[str],
) -> bool:
    if isinstance(node, ast.Await):
        return _is_websocket_text_value(
            node.value,
            websocket_aliases,
            websocket_text_names,
        )
    if isinstance(node, ast.Name):
        return node.id in websocket_text_names
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "receive_text"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in websocket_aliases
    )


def _is_route_handler(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    boundary_function_lines: frozenset[int],
    route_owner_names: frozenset[str],
) -> bool:
    if node.lineno in boundary_function_lines:
        return True
    for decorator in node.decorator_list:
        if _is_http_middleware_decorator(decorator, route_owner_names):
            return True
        call = decorator if isinstance(decorator, ast.Call) else None
        func = call.func if call is not None else decorator
        if not isinstance(func, ast.Attribute) or not (
            func.attr in ROUTE_METHODS or func.attr in GENERIC_ROUTE_DECORATORS
        ):
            continue
        if _is_route_owner_reference(func.value, route_owner_names):
            return True
    return False


def _route_handler_uses_owner(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    owner_names: set[str],
) -> bool:
    for decorator in node.decorator_list:
        if _is_http_middleware_decorator(decorator, owner_names):
            return True
        call = decorator if isinstance(decorator, ast.Call) else None
        func = call.func if call is not None else decorator
        if not isinstance(func, ast.Attribute) or not (
            func.attr in ROUTE_METHODS or func.attr in GENERIC_ROUTE_DECORATORS
        ):
            continue
        if _is_route_owner_reference(func.value, owner_names):
            return True
    return False


def _is_http_middleware_decorator(
    decorator: ast.expr,
    owner_names: set[str] | frozenset[str],
) -> bool:
    if not isinstance(decorator, ast.Call) or not isinstance(
        decorator.func,
        ast.Attribute,
    ):
        return False
    if decorator.func.attr != "middleware" or not _is_route_owner_reference(
        decorator.func.value,
        owner_names,
    ):
        return False
    middleware_type = next(
        (
            keyword.value
            for keyword in decorator.keywords
            if keyword.arg == "middleware_type"
        ),
        decorator.args[0] if decorator.args else None,
    )
    return bool(
        isinstance(middleware_type, ast.Constant)
        and middleware_type.value == "http"
    )


def _is_banned_wire_name(name: str) -> bool:
    canonical_name = "".join(character for character in name.casefold() if character.isalnum())
    return canonical_name in _CANONICAL_BANNED_WIRE_FIELD_NAMES


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _name(node.value)
    return None
