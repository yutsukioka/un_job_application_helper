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
    for path, tree in parsed_modules:
        visitor = _ServiceContractVisitor(
            path,
            model_names=model_names,
            field_factory_names=_pydantic_symbol_aliases(tree, "Field"),
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
    ) -> None:
        self.path = path
        self.model_names = model_names
        self.field_factory_names = field_factory_names
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
        if not _is_route_handler(node):
            return
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            if _is_banned_wire_name(arg.arg):
                self.violations.append(
                    f"{self.path}:{arg.lineno}: route parameter {node.name}.{arg.arg} "
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
    classes: list[tuple[ast.ClassDef, frozenset[str]]] = []
    for _, tree in parsed_modules:
        base_aliases = _pydantic_symbol_aliases(tree, "BaseModel")
        classes.extend(
            (node, base_aliases)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        )

    model_names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node, base_aliases in classes:
            if node.name in model_names:
                continue
            if any(
                _name(base) in base_aliases or _name(base) in model_names
                for base in node.bases
            ):
                model_names.add(node.name)
                changed = True
    return frozenset(model_names)


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


def _is_route_handler(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        func = call.func if call is not None else decorator
        if isinstance(func, ast.Attribute) and (
            func.attr in ROUTE_METHODS or func.attr in GENERIC_ROUTE_DECORATORS
        ):
            return True
    return False


def _is_banned_wire_name(name: str) -> bool:
    return name.strip("_").casefold() in BANNED_WIRE_FIELD_NAMES


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _name(node.value)
    return None
