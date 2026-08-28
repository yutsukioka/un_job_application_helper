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
    service_paths = frozenset(
        path for path in sorted(root.rglob("*.py")) if not _skip_path(path)
    )
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

    pydantic_models = _pydantic_model_keys(
        module_root,
        parsed_modules,
        paths_by_module,
    )
    dataclass_models = _dataclass_model_keys(parsed_modules)
    programmatic_handlers = _programmatic_route_handler_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        service_paths,
    )
    boundary_functions = _boundary_function_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        programmatic_handlers,
        service_paths,
    )
    request_pydantic_models = _referenced_model_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        boundary_functions,
        pydantic_models,
    )
    service_pydantic_models = frozenset(
        key for key in pydantic_models if key[0] in service_paths
    )
    inspected_pydantic_models = _model_base_closure(
        module_root,
        parsed_modules,
        paths_by_module,
        service_pydantic_models | request_pydantic_models,
        pydantic_models,
    )
    request_dataclasses = _referenced_model_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        boundary_functions,
        dataclass_models,
    )
    for path, tree in parsed_modules:
        if not (
            path in service_paths
            or any(model_path == path for model_path, _ in inspected_pydantic_models)
            or any(model_path == path for model_path, _ in request_dataclasses)
            or any(function_path == path for function_path, _ in boundary_functions)
        ):
            continue
        visitor = _ServiceContractVisitor(
            path,
            pydantic_model_names=frozenset(
                name
                for model_path, name in inspected_pydantic_models
                if model_path == path
            ),
            dataclass_model_names=frozenset(
                name for model_path, name in request_dataclasses if model_path == path
            ),
            field_factory_names=_pydantic_symbol_aliases(tree, "Field"),
            request_type_names=_request_type_aliases(tree),
            request_module_names=_request_module_aliases(tree),
            boundary_function_names=frozenset(
                name for function_path, name in boundary_functions if function_path == path
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
        pydantic_model_names: frozenset[str],
        dataclass_model_names: frozenset[str],
        field_factory_names: frozenset[str],
        request_type_names: frozenset[str],
        request_module_names: frozenset[str],
        boundary_function_names: frozenset[str],
    ) -> None:
        self.path = path
        self.pydantic_model_names = pydantic_model_names
        self.dataclass_model_names = dataclass_model_names
        self.field_factory_names = field_factory_names
        self.request_type_names = request_type_names
        self.request_module_names = request_module_names
        self.boundary_function_names = boundary_function_names
        self.violations: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name in self.pydantic_model_names or node.name in self.dataclass_model_names:
            model_kind = (
                "Pydantic model"
                if node.name in self.pydantic_model_names
                else "dataclass request model"
            )
            for field_name, line_number in _model_fields(
                node,
                field_factory_names=self.field_factory_names,
                exclude_private=node.name in self.pydantic_model_names,
                include_unannotated_assignments=node.name in self.pydantic_model_names,
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
        if not _is_route_handler(node, self.boundary_function_names):
            return
        for wire_name, line_number in _route_parameter_wire_names(node):
            if _is_banned_wire_name(wire_name):
                self.violations.append(
                    f"{self.path}:{line_number}: route parameter {node.name}.{wire_name} "
                    "would accept raw vault secret material over the wire"
                )
        for wire_name, line_number in _route_body_wire_names(
            node,
            request_type_names=self.request_type_names,
            request_module_names=self.request_module_names,
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
) -> dict[tuple[Path, str], ast.ClassDef]:
    return {
        (path, node.name): node
        for path, tree in parsed_modules
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }


def _pydantic_model_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> frozenset[tuple[Path, str]]:
    classes = _class_definitions(parsed_modules)
    trees_by_path = dict(parsed_modules)
    base_aliases = {
        path: _pydantic_symbol_aliases(tree, "BaseModel")
        for path, tree in parsed_modules
    }
    module_aliases = {
        path: _pydantic_module_aliases(tree) for path, tree in parsed_modules
    }

    model_keys: set[tuple[Path, str]] = set()
    changed = True
    while changed:
        changed = False
        for key, node in classes.items():
            if key in model_keys:
                continue
            path, _ = key
            is_model = False
            for base in node.bases:
                if _is_pydantic_base_reference(
                    base,
                    base_aliases[path],
                    module_aliases[path],
                ):
                    is_model = True
                    break
                target = _resolve_reference_target(
                    root,
                    path,
                    trees_by_path[path],
                    base,
                    paths_by_module,
                )
                if target in model_keys:
                    is_model = True
                    break
            if is_model:
                model_keys.add(key)
                changed = True
    return frozenset(model_keys)


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
    parsed_modules: list[tuple[Path, ast.Module]],
) -> frozenset[tuple[Path, str]]:
    keys: set[tuple[Path, str]] = set()
    for path, tree in parsed_modules:
        direct_aliases = {
            imported.asname or imported.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "dataclasses"
            for imported in node.names
            if imported.name == "dataclass"
        }
        module_aliases = {
            imported.asname or imported.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for imported in node.names
            if imported.name == "dataclasses"
        }
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                _is_dataclass_decorator(decorator, direct_aliases, module_aliases)
                for decorator in node.decorator_list
            ):
                keys.add((path, node.name))
    return frozenset(keys)


def _is_dataclass_decorator(
    decorator: ast.expr,
    direct_aliases: set[str],
    module_aliases: set[str],
) -> bool:
    candidate = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(candidate, ast.Name):
        return candidate.id in direct_aliases
    return (
        isinstance(candidate, ast.Attribute)
        and candidate.attr == "dataclass"
        and isinstance(candidate.value, ast.Name)
        and candidate.value.id in module_aliases
    )


def _model_fields(
    node: ast.ClassDef,
    *,
    field_factory_names: frozenset[str],
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
                    )
                )
    return fields


def _is_class_variable(annotation: ast.expr) -> bool:
    candidate = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    return _name(candidate) == "ClassVar"


def _pydantic_field_aliases(
    *nodes: ast.AST | None,
    field_factory_names: frozenset[str],
) -> list[tuple[str, int]]:
    aliases: list[tuple[str, int]] = []
    for root in nodes:
        if root is None:
            continue
        for node in ast.walk(root):
            if not isinstance(node, ast.Call) or _name(node.func) not in field_factory_names:
                continue
            for keyword in node.keywords:
                if keyword.arg not in {"alias", "validation_alias"}:
                    continue
                aliases.extend(
                    (value.value, value.lineno)
                    for value in ast.walk(keyword.value)
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                )
    return aliases


def _route_parameter_wire_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[str, int]]:
    positional = [*node.args.posonlyargs, *node.args.args]
    positional_with_defaults = positional[-len(node.args.defaults) :] if node.args.defaults else []
    defaults_by_argument = {
        id(argument): default
        for argument, default in zip(
            positional_with_defaults,
            node.args.defaults,
            strict=True,
        )
    }
    defaults_by_argument.update(
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

    parameters = [*positional, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        parameters.append(node.args.vararg)
    if node.args.kwarg is not None:
        parameters.append(node.args.kwarg)

    wire_names: list[tuple[str, int]] = []
    for argument in parameters:
        wire_names.append((argument.arg, argument.lineno))
        wire_names.extend(
            _keyword_wire_aliases(
                argument.annotation,
                defaults_by_argument.get(id(argument)),
            )
        )
    return wire_names


def _keyword_wire_aliases(*nodes: ast.AST | None) -> list[tuple[str, int]]:
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
                aliases.extend(
                    (value.value, value.lineno)
                    for value in ast.walk(keyword.value)
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                )
    return aliases


def _programmatic_route_handler_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    service_paths: frozenset[Path],
) -> frozenset[tuple[Path, str]]:
    functions = _function_definitions(parsed_modules)
    handlers: set[tuple[Path, str]] = set()
    for path, tree in parsed_modules:
        if path not in service_paths:
            continue
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr not in PROGRAMMATIC_ROUTE_REGISTRARS
            ):
                continue
            endpoint = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "endpoint"),
                node.args[1] if len(node.args) > 1 else None,
            )
            if endpoint is None:
                continue
            target = _resolve_reference_target(
                root,
                path,
                tree,
                endpoint,
                paths_by_module,
            )
            if target in functions:
                handlers.add(target)
    return frozenset(handlers)


def _function_definitions(
    parsed_modules: list[tuple[Path, ast.Module]],
) -> dict[tuple[Path, str], ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        (path, node.name): node
        for path, tree in parsed_modules
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _boundary_function_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    programmatic_handlers: frozenset[tuple[Path, str]],
    service_paths: frozenset[Path],
) -> frozenset[tuple[Path, str]]:
    trees_by_path = dict(parsed_modules)
    functions = _function_definitions(parsed_modules)
    boundary: set[tuple[Path, str]] = set(programmatic_handlers)
    for key, node in functions.items():
        if key[0] in service_paths and _is_route_handler(node, frozenset()):
            boundary.add(key)

    for path, tree in parsed_modules:
        if path not in service_paths:
            continue
        for call in (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        ):
            for keyword in call.keywords:
                if keyword.arg == "dependencies":
                    boundary.update(
                        _dependency_targets(
                            root,
                            path,
                            tree,
                            (keyword.value,),
                            paths_by_module,
                            functions,
                        )
                    )

    pending = list(boundary)
    while pending:
        key = pending.pop()
        function = functions.get(key)
        if function is None:
            continue
        path, _ = key
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
            functions,
        )
        for target in discovered - boundary:
            boundary.add(target)
            pending.append(target)
    return frozenset(boundary)


def _dependency_targets(
    root: Path,
    path: Path,
    tree: ast.Module,
    nodes: object,
    paths_by_module: dict[str, Path],
    functions: dict[tuple[Path, str], ast.FunctionDef | ast.AsyncFunctionDef],
) -> set[tuple[Path, str]]:
    depends_aliases, fastapi_module_aliases = _depends_aliases(tree)
    targets: set[tuple[Path, str]] = set()
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
            target = _resolve_reference_target(
                root,
                path,
                tree,
                dependency,
                paths_by_module,
            )
            if target in functions:
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
                if imported.name == "Depends"
            )
        elif isinstance(node, ast.Import):
            modules.update(
                imported.asname or imported.name.split(".")[0]
                for imported in node.names
                if imported.name == "fastapi"
            )
    return direct, modules


def _is_depends_call(
    node: ast.Call,
    direct_aliases: set[str],
    module_aliases: set[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id in direct_aliases
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "Depends"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in module_aliases
    )


def _referenced_model_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    boundary_functions: frozenset[tuple[Path, str]],
    model_keys: frozenset[tuple[Path, str]],
) -> frozenset[tuple[Path, str]]:
    trees_by_path = dict(parsed_modules)
    functions = _function_definitions(parsed_modules)
    referenced: set[tuple[Path, str]] = set()
    for key in boundary_functions:
        function = functions.get(key)
        if function is None:
            continue
        path, _ = key
        for argument in _function_arguments(function):
            if argument.annotation is None:
                continue
            for candidate in ast.walk(argument.annotation):
                if not isinstance(candidate, (ast.Name, ast.Attribute)):
                    continue
                target = _resolve_reference_target(
                    root,
                    path,
                    trees_by_path[path],
                    candidate,
                    paths_by_module,
                )
                if target in model_keys:
                    referenced.add(target)
    return _model_base_closure(
        root,
        parsed_modules,
        paths_by_module,
        frozenset(referenced),
        model_keys,
    )


def _model_base_closure(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    initial: frozenset[tuple[Path, str]],
    model_keys: frozenset[tuple[Path, str]],
) -> frozenset[tuple[Path, str]]:
    classes = _class_definitions(parsed_modules)
    trees_by_path = dict(parsed_modules)
    closure = set(initial)
    pending = list(initial)
    while pending:
        key = pending.pop()
        model = classes.get(key)
        if model is None:
            continue
        path, _ = key
        for base in model.bases:
            target = _resolve_reference_target(
                root,
                path,
                trees_by_path[path],
                base,
                paths_by_module,
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
) -> tuple[Path, str] | None:
    parts = _qualified_name_parts(node)
    if not parts:
        return None
    symbol_targets, module_targets = _import_targets(root, path, tree, paths_by_module)
    if len(parts) == 1:
        return symbol_targets.get(parts[0], (path, parts[0]))
    module_path = module_targets.get(tuple(parts[:-1]))
    if module_path is not None:
        return module_path, parts[-1]
    return None


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
) -> list[tuple[str, int]]:
    request_names = {
        argument.arg
        for argument in _function_arguments(node)
        if argument.annotation is not None
        and _annotation_contains_request_type(
            argument.annotation,
            request_type_names,
            request_module_names,
        )
    }
    if not request_names:
        return []

    request_aliases = set(request_names)
    raw_mapping_names: set[str] = set()
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
            if _is_raw_request_mapping(value, request_aliases, raw_mapping_names):
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
                    raw_mapping_names,
                )
            ):
                wire_names.append((key.value, key.lineno))
    return wire_names


def _request_type_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module in {"fastapi", "starlette.requests"}
        for imported in node.names
        if imported.name == "Request"
    )


def _request_module_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name in {"fastapi", "starlette", "starlette.requests"}
    )


def _annotation_contains_request_type(
    annotation: ast.expr,
    request_type_names: frozenset[str],
    request_module_names: frozenset[str],
) -> bool:
    for candidate in ast.walk(annotation):
        if isinstance(candidate, ast.Name) and candidate.id in request_type_names:
            return True
        if isinstance(candidate, ast.Attribute) and candidate.attr == "Request":
            parts = _qualified_name_parts(candidate)
            if parts and parts[0] in request_module_names:
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
    raw_mapping_names: set[str],
) -> bool:
    if isinstance(node, ast.Await):
        return _is_raw_request_mapping(node.value, request_aliases, raw_mapping_names)
    if isinstance(node, ast.Name):
        return node.id in raw_mapping_names
    if isinstance(node, ast.Subscript):
        return _is_raw_request_mapping(node.value, request_aliases, raw_mapping_names)
    if not isinstance(node, ast.Call):
        return False
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"json", "form"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in request_aliases
    ):
        return True
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"get", "pop", "setdefault", "copy"}
        and _is_raw_request_mapping(
            node.func.value,
            request_aliases,
            raw_mapping_names,
        )
    ):
        return True
    return _name(node.func) == "dict" and any(
        _is_raw_request_mapping(argument, request_aliases, raw_mapping_names)
        for argument in node.args
    )


def _is_route_handler(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    programmatic_route_handlers: frozenset[str],
) -> bool:
    if node.name in programmatic_route_handlers:
        return True
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        func = call.func if call is not None else decorator
        if isinstance(func, ast.Attribute) and (
            func.attr in ROUTE_METHODS or func.attr in GENERIC_ROUTE_DECORATORS
        ):
            return True
    return False


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
