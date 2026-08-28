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
    violations: list[str] = []
    parsed_modules: list[tuple[Path, ast.Module]] = []
    for path in sorted(root.rglob("*.py")):
        if _skip_path(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            violations.append(f"{path}:{exc.lineno or 1}: cannot parse service module")
            continue
        parsed_modules.append((path, tree))

    model_names = _pydantic_model_names(parsed_modules)
    programmatic_handlers = _programmatic_route_handler_keys(root, parsed_modules)
    for path, tree in parsed_modules:
        visitor = _ServiceContractVisitor(
            path,
            model_names=model_names,
            field_factory_names=_pydantic_symbol_aliases(tree, "Field"),
            programmatic_route_handlers=frozenset(
                name for handler_path, name in programmatic_handlers if handler_path == path
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
        model_names: frozenset[str],
        field_factory_names: frozenset[str],
        programmatic_route_handlers: frozenset[str],
    ) -> None:
        self.path = path
        self.model_names = model_names
        self.field_factory_names = field_factory_names
        self.programmatic_route_handlers = programmatic_route_handlers
        self.violations: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name in self.model_names:
            for field_name, line_number in _model_fields(
                node,
                field_factory_names=self.field_factory_names,
            ):
                if _is_banned_wire_name(field_name):
                    self.violations.append(
                        f"{self.path}:{line_number}: Pydantic model {node.name}.{field_name} "
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
        if not _is_route_handler(node, self.programmatic_route_handlers):
            return
        for wire_name, line_number in _route_parameter_wire_names(node):
            if _is_banned_wire_name(wire_name):
                self.violations.append(
                    f"{self.path}:{line_number}: route parameter {node.name}.{wire_name} "
                    "would accept raw vault secret material over the wire"
                )
        for wire_name, line_number in _route_body_wire_names(node):
            if _is_banned_wire_name(wire_name):
                self.violations.append(
                    f"{self.path}:{line_number}: route body {node.name}[{wire_name!r}] "
                    "would accept raw vault secret material over the wire"
                )


def _skip_path(path: Path) -> bool:
    return any(part in {".venv", "__pycache__", ".pytest_cache", ".ruff_cache"} for part in path.parts)


def _pydantic_symbol_aliases(tree: ast.Module, symbol: str) -> frozenset[str]:
    aliases = {symbol}
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


def _pydantic_model_names(
    parsed_modules: list[tuple[Path, ast.Module]],
) -> frozenset[str]:
    classes: list[tuple[ast.ClassDef, frozenset[str], dict[str, str]]] = []
    for _, tree in parsed_modules:
        base_aliases = _pydantic_symbol_aliases(tree, "BaseModel")
        imported_symbols = _imported_symbol_aliases(tree)
        classes.extend(
            (node, base_aliases, imported_symbols)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        )

    model_names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node, base_aliases, imported_symbols in classes:
            if node.name in model_names:
                continue
            base_names = [_name(base) for base in node.bases]
            if any(base_name in base_aliases for base_name in base_names) or any(
                imported_symbols.get(base_name, base_name) in model_names
                for base_name in base_names
                if base_name is not None
            ):
                model_names.add(node.name)
                changed = True
    return frozenset(model_names)


def _imported_symbol_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for imported in node.names:
            if imported.name != "*":
                aliases[imported.asname or imported.name] = imported.name
    return aliases


def _model_fields(
    node: ast.ClassDef,
    *,
    field_factory_names: frozenset[str],
) -> list[tuple[str, int]]:
    fields: list[tuple[str, int]] = []
    for child in node.body:
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            fields.append((child.target.id, child.lineno))
            fields.extend(
                _pydantic_field_aliases(
                    child.annotation,
                    child.value,
                    field_factory_names=field_factory_names,
                )
            )
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    fields.append((target.id, child.lineno))
            fields.extend(
                _pydantic_field_aliases(
                    child.value,
                    field_factory_names=field_factory_names,
                )
            )
    return fields


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


def _programmatic_route_handlers(tree: ast.Module) -> frozenset[str]:
    handlers: set[str] = set()
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
        if endpoint is not None and (handler_name := _name(endpoint)) is not None:
            handlers.add(handler_name)
    return frozenset(handlers)


def _programmatic_route_handler_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
) -> frozenset[tuple[Path, str]]:
    paths_by_module: dict[str, Path] = {}
    for path, _ in parsed_modules:
        module_name = _module_name(root, path)
        paths_by_module[module_name] = path
        if module_name:
            paths_by_module[f"{root.name}.{module_name}"] = path

    handlers: set[tuple[Path, str]] = set()
    for path, tree in parsed_modules:
        local_functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        imported_functions = _imported_function_targets(
            root,
            path,
            tree,
            paths_by_module,
        )
        for handler_name in _programmatic_route_handlers(tree):
            if handler_name in local_functions:
                handlers.add((path, handler_name))
            elif handler_name in imported_functions:
                handlers.add(imported_functions[handler_name])
    return frozenset(handlers)


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_function_targets(
    root: Path,
    path: Path,
    tree: ast.Module,
    paths_by_module: dict[str, Path],
) -> dict[str, tuple[Path, str]]:
    current_parts = _module_name(root, path).split(".")
    if path.name != "__init__.py":
        current_parts = current_parts[:-1]

    targets: dict[str, tuple[Path, str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.level:
            trim = node.level - 1
            if trim > len(current_parts):
                continue
            module_parts = current_parts[: len(current_parts) - trim]
            module_parts.extend(node.module.split("."))
            module_name = ".".join(module_parts)
        else:
            module_name = node.module
        target_path = paths_by_module.get(module_name)
        if target_path is None:
            continue
        for imported in node.names:
            if imported.name != "*":
                targets[imported.asname or imported.name] = (target_path, imported.name)
    return targets


def _route_body_wire_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[str, int]]:
    wire_names: list[tuple[str, int]] = []
    for statement in node.body:
        for candidate in ast.walk(statement):
            if isinstance(candidate, ast.Subscript):
                key = candidate.slice
            elif (
                isinstance(candidate, ast.Call)
                and isinstance(candidate.func, ast.Attribute)
                and candidate.func.attr in {"get", "pop", "setdefault"}
                and candidate.args
            ):
                key = candidate.args[0]
            else:
                continue
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                wire_names.append((key.value, key.lineno))
    return wire_names


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
