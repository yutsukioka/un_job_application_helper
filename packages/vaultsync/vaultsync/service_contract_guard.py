"""Static guard for future encrypted-sync service boundaries."""

from __future__ import annotations

import ast
from pathlib import Path
from string import Formatter


ROUTE_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "options",
    "head",
    "trace",
}
GENERIC_ROUTE_DECORATORS = {
    "api_route",
    "exception_handler",
    "route",
    "websocket",
    "websocket_route",
}
PROGRAMMATIC_ROUTE_REGISTRARS = {
    "add_api_route",
    "add_api_websocket_route",
    "add_exception_handler",
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
    for path, tree in parsed_modules:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                imported.name == "*" for imported in node.names
            ):
                violations.append(
                    f"{path}:{node.lineno}: wildcard import at a service boundary "
                    "cannot be statically approved"
                )
    wire_alias_constants = _string_constants_by_path(
        module_root,
        parsed_modules,
        paths_by_module,
    )
    imported_module_values = _imported_module_values_by_path(
        module_root,
        parsed_modules,
        paths_by_module,
    )
    request_type_names = _request_type_names_by_path(
        module_root,
        parsed_modules,
        paths_by_module,
    )
    websocket_type_names = _websocket_type_names_by_path(
        module_root,
        parsed_modules,
        paths_by_module,
    )
    raw_mapping_helpers = _raw_mapping_helper_summaries_by_path(
        module_root,
        parsed_modules,
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
    functional_dataclasses = _functional_dataclass_definitions(parsed_modules)
    functional_dataclass_models = frozenset(functional_dataclasses)
    pydantic_dataclass_models = _pydantic_dataclass_model_keys(parsed_modules)
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
    violations.extend(
        _unapproved_route_class_violations(
            parsed_modules,
            service_paths,
            route_owner_names,
        )
    )
    violations.extend(
        _route_template_wire_violations(
            parsed_modules,
            service_paths,
            route_owner_names,
            mounted_route_owners,
            wire_alias_constants,
        )
    )
    violations.extend(
        _unresolved_mounted_asgi_violations(
            module_root,
            parsed_modules,
            paths_by_module,
            service_paths,
            route_owner_names,
            mounted_route_owners,
        )
    )
    violations.extend(
        _unapproved_asgi_middleware_violations(
            module_root,
            parsed_modules,
            paths_by_module,
            service_paths,
            route_owner_names,
            mounted_route_owners,
        )
    )
    (
        programmatic_handlers,
        programmatic_lambdas,
        partial_programmatic_handlers,
        programmatic_positional_request_handlers,
    ) = _programmatic_route_handler_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        service_paths,
        route_owner_names,
        mounted_route_owners,
    )
    for path, handler in programmatic_lambdas:
        depends_aliases, depends_module_aliases = _depends_aliases(
            dict(parsed_modules)[path]
        )
        body_aliases, body_module_aliases = _body_aliases(
            dict(parsed_modules)[path]
        )
        for wire_name, line_number in _route_parameter_wire_names(
            handler,
            depends_aliases=frozenset(depends_aliases),
            depends_module_aliases=frozenset(depends_module_aliases),
            wire_alias_constants=wire_alias_constants[path],
        ):
            if _is_banned_wire_name(wire_name):
                violations.append(
                    f"{path}:{line_number}: route parameter "
                    f"lambda.{wire_name} would accept raw vault secret "
                    "material over the wire"
                )
        synthetic = ast.FunctionDef(
            name="lambda",
            args=handler.args,
            body=[ast.Return(value=handler.body)],
            decorator_list=[],
            returns=None,
            type_comment=None,
            type_params=[],
        )
        ast.copy_location(synthetic, handler)
        for wire_name, line_number in _route_body_wire_names(
            synthetic,
            request_type_names=request_type_names[path],
            request_module_names=_request_module_aliases(dict(parsed_modules)[path]),
            websocket_type_names=websocket_type_names[path],
            websocket_module_names=_websocket_module_aliases(dict(parsed_modules)[path]),
            json_loads_names=_json_loads_aliases(dict(parsed_modules)[path]),
            json_module_names=_json_module_aliases(dict(parsed_modules)[path]),
            body_aliases=frozenset(body_aliases),
            body_module_aliases=frozenset(body_module_aliases),
            wire_key_constants=wire_alias_constants[path],
            raw_mapping_helpers=raw_mapping_helpers[path],
        ):
            if _is_banned_wire_name(wire_name):
                violations.append(
                    f"{path}:{line_number}: route body lambda[{wire_name!r}] "
                    "would accept raw vault secret material over the wire"
                )
    for path, tree in parsed_modules:
        depends_aliases, depends_modules = _depends_aliases(tree)
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and _is_depends_call(call, depends_aliases, depends_modules)
            ):
                continue
            dependency = call.args[0] if call.args else next(
                (keyword.value for keyword in call.keywords if keyword.arg == "dependency"),
                None,
            )
            if not isinstance(dependency, ast.Lambda):
                continue
            for wire_name, line_number in _route_parameter_wire_names(
                dependency,
                depends_aliases=frozenset(depends_aliases),
                depends_module_aliases=frozenset(depends_modules),
                wire_alias_constants=wire_alias_constants[path],
            ):
                if _is_banned_wire_name(wire_name):
                    violations.append(
                        f"{path}:{line_number}: dependency lambda.{wire_name} would "
                        "accept raw vault secret material over the wire"
                    )
    function_definitions = _function_definitions(parsed_modules)
    trees_by_path = dict(parsed_modules)
    for target, remaining_parameters in partial_programmatic_handlers:
        handler = function_definitions[target]
        path = target[0]
        depends_aliases, depends_module_aliases = _depends_aliases(
            trees_by_path[path]
        )
        for wire_name, line_number in _route_parameter_wire_names(
            handler,
            depends_aliases=frozenset(depends_aliases),
            depends_module_aliases=frozenset(depends_module_aliases),
            wire_alias_constants=wire_alias_constants[path],
            included_parameter_names=remaining_parameters,
        ):
            if _is_banned_wire_name(wire_name):
                violations.append(
                    f"{path}:{line_number}: route parameter "
                    f"{target[2]}.{wire_name} would accept raw vault secret "
                    "material over the wire"
                )
    boundary_functions, partial_dependency_parameters = _boundary_function_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        programmatic_handlers,
        service_paths,
        route_owner_names,
        mounted_route_owners,
    )
    request_body_flows = _request_body_flow_names(
        module_root,
        parsed_modules,
        paths_by_module,
        boundary_functions,
        request_type_names,
        websocket_type_names,
        route_owner_names,
        programmatic_positional_request_handlers,
    )
    request_models = _referenced_model_keys(
        module_root,
        parsed_modules,
        paths_by_module,
        boundary_functions,
        pydantic_models
        | dataclass_models
        | functional_dataclass_models
        | typed_dict_models
        | functional_typed_dict_models,
    )
    inspected_pydantic_models = request_models & pydantic_class_models
    request_created_pydantic_models = request_models & frozenset(
        created_pydantic_models
    )
    request_dataclasses = request_models & dataclass_models
    request_functional_dataclasses = request_models & functional_dataclass_models
    request_typed_dicts = request_models & typed_dict_models
    request_functional_typed_dicts = request_models & functional_typed_dict_models
    for key in sorted(request_functional_typed_dicts):
        path, _, model_name = key
        for field_name, line_number in _functional_typed_dict_fields(
            functional_typed_dicts[key],
            dict(parsed_modules)[path],
            imported_module_values[path],
        ):
            if _is_banned_wire_name(field_name):
                violations.append(
                    f"{path}:{line_number}: TypedDict request model "
                    f"{model_name}.{field_name} would accept raw vault secret "
                    "material over the wire"
                )
    for key in sorted(request_functional_dataclasses):
        path, _, model_name = key
        fields, opaque_field_lines = _functional_dataclass_fields(
            functional_dataclasses[key],
            dict(parsed_modules)[path],
        )
        for field_name, line_number in fields:
            if _is_banned_wire_name(field_name):
                violations.append(
                    f"{path}:{line_number}: functional dataclass request model "
                    f"{model_name}.{field_name} would accept raw vault secret "
                    "material over the wire"
                )
        for line_number in opaque_field_lines:
            violations.append(
                f"{path}:{line_number}: functional dataclass request model "
                f"{model_name} uses fields that cannot be statically approved"
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
        create_model_fields, opaque_field_lines = _pydantic_create_model_fields(
            created_pydantic_models[key],
            tree=tree,
            field_factory_names=_pydantic_symbol_aliases(tree, "Field"),
            field_factory_module_names=_pydantic_module_aliases(tree),
            wire_alias_constants=wire_alias_constants[path],
        )
        for field_name, line_number in create_model_fields:
            if _is_banned_wire_name(field_name):
                violations.append(
                    f"{path}:{line_number}: Pydantic model "
                    f"{model_name}.{field_name} would accept raw vault secret "
                    "material over the wire"
                )
        for line_number in opaque_field_lines:
            violations.append(
                f"{path}:{line_number}: Pydantic request model {model_name} "
                "uses unpacked fields that cannot be statically approved"
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
            or any(function_path == path for function_path, _, _ in request_body_flows)
        ):
            continue
        depends_aliases, depends_module_aliases = _depends_aliases(tree)
        body_aliases, body_module_aliases = _body_aliases(tree)
        visitor = _ServiceContractVisitor(
            path,
            module_tree=tree,
            module_statements=tree.body,
            imported_module_values=imported_module_values[path],
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
            pydantic_dataclass_model_lines=frozenset(
                line_number
                for model_path, line_number, _ in (
                    request_dataclasses & pydantic_dataclass_models
                )
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
            root_validator_names=_pydantic_symbol_aliases(
                tree,
                "root_validator",
            ),
            wire_alias_constants=wire_alias_constants[path],
            request_type_names=request_type_names[path],
            request_module_names=_request_module_aliases(tree),
            websocket_type_names=websocket_type_names[path],
            websocket_module_names=_websocket_module_aliases(tree),
            json_loads_names=_json_loads_aliases(tree),
            json_module_names=_json_module_aliases(tree),
            raw_mapping_helpers=raw_mapping_helpers[path],
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
            included_boundary_parameters={
                line_number: included
                for (function_path, line_number, _), included in (
                    partial_dependency_parameters.items()
                )
                if function_path == path
            },
            delegated_request_names={
                line_number: flow[0]
                for (function_path, line_number, _), flow in request_body_flows.items()
                if function_path == path
            },
            delegated_websocket_names={
                line_number: flow[1]
                for (function_path, line_number, _), flow in request_body_flows.items()
                if function_path == path
            },
        )
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


class _ServiceContractVisitor(ast.NodeVisitor):
    def __init__(
        self,
        path: Path,
        *,
        module_tree: ast.Module,
        module_statements: list[ast.stmt],
        imported_module_values: dict[str, ast.expr],
        pydantic_model_lines: frozenset[int],
        dataclass_model_lines: frozenset[int],
        pydantic_dataclass_model_lines: frozenset[int],
        typed_dict_model_lines: frozenset[int],
        mapping_root_model_lines: frozenset[int],
        field_factory_names: frozenset[str],
        field_factory_module_names: frozenset[str],
        model_validator_names: frozenset[str],
        model_validator_module_names: frozenset[str],
        root_validator_names: frozenset[str],
        wire_alias_constants: dict[str, str],
        request_type_names: frozenset[str],
        request_module_names: frozenset[str],
        websocket_type_names: frozenset[str],
        websocket_module_names: frozenset[str],
        json_loads_names: frozenset[str],
        json_module_names: frozenset[str],
        raw_mapping_helpers: dict[str, tuple[tuple[str, ...], frozenset[str]]],
        route_owner_names: frozenset[str],
        depends_aliases: frozenset[str],
        depends_module_aliases: frozenset[str],
        body_aliases: frozenset[str],
        body_module_aliases: frozenset[str],
        unconstrained_type_names: frozenset[str],
        unconstrained_type_module_names: frozenset[str],
        boundary_function_lines: frozenset[int],
        included_boundary_parameters: dict[int, frozenset[str]],
        delegated_request_names: dict[int, frozenset[str]],
        delegated_websocket_names: dict[int, frozenset[str]],
    ) -> None:
        self.path = path
        self.module_tree = module_tree
        self.module_statements = module_statements
        self.imported_module_values = imported_module_values
        self.pydantic_model_lines = pydantic_model_lines
        self.dataclass_model_lines = dataclass_model_lines
        self.pydantic_dataclass_model_lines = pydantic_dataclass_model_lines
        self.typed_dict_model_lines = typed_dict_model_lines
        self.mapping_root_model_lines = mapping_root_model_lines
        self.field_factory_names = field_factory_names
        self.field_factory_module_names = field_factory_module_names
        self.model_validator_names = model_validator_names
        self.model_validator_module_names = model_validator_module_names
        self.root_validator_names = root_validator_names
        self.wire_alias_constants = wire_alias_constants
        self.request_type_names = request_type_names
        self.request_module_names = request_module_names
        self.websocket_type_names = websocket_type_names
        self.websocket_module_names = websocket_module_names
        self.json_loads_names = json_loads_names
        self.json_module_names = json_module_names
        self.raw_mapping_helpers = raw_mapping_helpers
        self.route_owner_names = route_owner_names
        self.depends_aliases = depends_aliases
        self.depends_module_aliases = depends_module_aliases
        self.body_aliases = body_aliases
        self.body_module_aliases = body_module_aliases
        self.unconstrained_type_names = unconstrained_type_names
        self.unconstrained_type_module_names = unconstrained_type_module_names
        self.boundary_function_lines = boundary_function_lines
        self.included_boundary_parameters = included_boundary_parameters
        self.delegated_request_names = delegated_request_names
        self.delegated_websocket_names = delegated_websocket_names
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
                extra_line := _permissive_model_extra_line(
                    node,
                    self.module_tree,
                    self.imported_module_values,
                )
            ) is not None:
                self.violations.append(
                    f"{self.path}:{extra_line}: Pydantic request model {node.name} "
                    "allows arbitrary extra wire fields"
                )
            if node.lineno in self.pydantic_model_lines and (
                alias_generator_line := _model_alias_generator_line(
                    node,
                    self.module_tree,
                    self.imported_module_values,
                )
            ) is not None:
                self.violations.append(
                    f"{self.path}:{alias_generator_line}: Pydantic request model "
                    f"{node.name} defines an alias generator that cannot be "
                    "statically approved"
                )
            if node.lineno in self.pydantic_model_lines and (
                raw_validator := _raw_model_validator(
                    node,
                    self.model_validator_names,
                    self.model_validator_module_names,
                    self.root_validator_names,
                )
            ) is not None:
                validator_kind, validator_line = raw_validator
                self.violations.append(
                    f"{self.path}:{validator_line}: Pydantic request model "
                    f"{node.name} defines a {validator_kind} that cannot "
                    "be statically approved"
                )
            if node.lineno in self.pydantic_dataclass_model_lines and (
                extra_line := _pydantic_dataclass_extra_line(
                    node,
                    self.module_tree,
                    self.imported_module_values,
                )
            ) is not None:
                self.violations.append(
                    f"{self.path}:{extra_line}: Pydantic dataclass request model "
                    f"{node.name} allows arbitrary extra wire fields"
                )
            if node.lineno in self.pydantic_dataclass_model_lines and (
                alias_generator_line := _pydantic_dataclass_alias_generator_line(
                    node,
                    self.module_tree,
                    self.imported_module_values,
                )
            ) is not None:
                self.violations.append(
                    f"{self.path}:{alias_generator_line}: Pydantic dataclass "
                    f"request model {node.name} defines an alias generator "
                    "that cannot be statically approved"
                )
            if node.lineno in self.mapping_root_model_lines:
                self.violations.append(
                    f"{self.path}:{node.lineno}: Pydantic request model "
                    f"{node.name} has an unconstrained mapping root"
                )
            if node.lineno in self.pydantic_model_lines:
                for field_name, line_number in _pydantic_v1_config_field_aliases(
                    node,
                    {
                        **self.wire_alias_constants,
                        **_string_constants(node.body),
                    },
                    self.module_statements,
                ):
                    if _is_banned_wire_name(field_name):
                        self.violations.append(
                            f"{self.path}:{line_number}: Pydantic model "
                            f"{node.name}.{field_name} would accept raw vault "
                            "secret material over the wire"
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
            for field_name, line_number in _unconstrained_mapping_model_fields(node):
                self.violations.append(
                    f"{self.path}:{line_number}: {model_kind} "
                    f"{node.name}.{field_name} is an unconstrained mapping field"
                )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_route_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_route_function(node)
        self.generic_visit(node)

    def _visit_route_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_boundary = node.lineno in self.boundary_function_lines
        delegated_request_names = self.delegated_request_names.get(
            node.lineno,
            frozenset(),
        )
        delegated_websocket_names = self.delegated_websocket_names.get(
            node.lineno,
            frozenset(),
        )
        if not is_boundary and not delegated_request_names and not delegated_websocket_names:
            return
        if is_boundary:
            if _has_unapproved_route_decorator(node, self.route_owner_names):
                self.violations.append(
                    f"{self.path}:{node.lineno}: decorated route signature cannot "
                    "be statically approved"
                )
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
                included_parameter_names=self.included_boundary_parameters.get(
                    node.lineno
                ),
            ):
                if _is_banned_wire_name(wire_name):
                    self.violations.append(
                        f"{self.path}:{line_number}: route parameter "
                        f"{node.name}.{wire_name} would accept raw vault "
                        "secret material over the wire"
                    )
        for wire_name, line_number in _route_body_wire_names(
            node,
            request_type_names=self.request_type_names,
            request_module_names=self.request_module_names,
            websocket_type_names=self.websocket_type_names,
            websocket_module_names=self.websocket_module_names,
            json_loads_names=self.json_loads_names,
            json_module_names=self.json_module_names,
            body_aliases=self.body_aliases,
            body_module_aliases=self.body_module_aliases,
            raw_mapping_helpers=self.raw_mapping_helpers,
            initial_request_names=delegated_request_names,
            initial_websocket_names=delegated_websocket_names,
            wire_key_constants={
                **self.wire_alias_constants,
                **_string_constants(node.body),
            },
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


def _framework_route_owner_names(
    tree: ast.Module,
    framework_class_names: set[str] | None = None,
) -> frozenset[str]:
    _, module_aliases = _framework_constructor_imports(tree)
    if framework_class_names is None:
        framework_class_names = _framework_class_names(tree)

    factory_names = _framework_app_factory_names(
        tree,
        framework_class_names,
        module_aliases,
    )

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
            ) or _is_framework_factory_call(value, factory_names) or (
                isinstance(value, ast.Name) and value.id in owners
            )
            if is_owner:
                previous_size = len(owners)
                owners.update(targets)
                changed = changed or len(owners) != previous_size
    return frozenset(owners)


def _framework_app_factory_names(
    tree: ast.Module,
    framework_class_names: set[str],
    module_aliases: set[str],
) -> frozenset[str]:
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    factories: set[str] = set()
    changed = True
    while changed:
        changed = False
        for function in functions:
            if function.name in factories:
                continue
            lexical_nodes = _lexical_body_nodes(function.body)
            local_owners: set[str] = set()
            assignments = [
                node
                for node in lexical_nodes
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
            ]
            local_changed = True
            while local_changed:
                local_changed = False
                for assignment in assignments:
                    targets, value = _assignment_targets_and_value(assignment)
                    if not (
                        _is_framework_constructor(
                            value,
                            framework_class_names,
                            module_aliases,
                        )
                        or _is_framework_factory_call(value, factories)
                        or (isinstance(value, ast.Name) and value.id in local_owners)
                    ):
                        continue
                    previous_size = len(local_owners)
                    local_owners.update(targets)
                    local_changed = local_changed or len(local_owners) != previous_size
            returns_owner = any(
                isinstance(node, ast.Return)
                and node.value is not None
                and (
                    _is_framework_constructor(
                        node.value,
                        framework_class_names,
                        module_aliases,
                    )
                    or _is_framework_factory_call(node.value, factories)
                    or (isinstance(node.value, ast.Name) and node.value.id in local_owners)
                )
                for node in lexical_nodes
            )
            if returns_owner:
                factories.add(function.name)
                changed = True
    return frozenset(factories)


def _lexical_body_nodes(statements: list[ast.stmt]) -> list[ast.AST]:
    nodes: list[ast.AST] = []

    class Collector(ast.NodeVisitor):
        def generic_visit(self, node: ast.AST) -> None:
            nodes.append(node)
            super().generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

    collector = Collector()
    for statement in statements:
        collector.visit(statement)
    return nodes


def _is_framework_factory_call(
    node: ast.AST,
    factory_names: set[str] | frozenset[str],
) -> bool:
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in factory_names
    )


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
        and (
            ".".join(parts) in framework_class_names
            or (
                parts[-1] in {"FastAPI", "APIRouter", "Starlette"}
                and parts[0] in module_aliases
            )
        )
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
    framework_class_names = _framework_class_names_by_path(
        root,
        parsed_modules,
        paths_by_module,
    )
    owners = {
        path: set(
            _framework_route_owner_names(
                tree,
                framework_class_names[path],
            )
        )
        for path, tree in parsed_modules
    }
    factory_keys = {
        (path, name)
        for path, tree in parsed_modules
        for name in _framework_app_factory_names(
            tree,
            framework_class_names[path],
            _framework_module_aliases(tree),
        )
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
                if _is_route_owner_reference(
                    value,
                    owners[path],
                ) or _resolved_framework_factory_call(
                    root,
                    path,
                    tree,
                    value,
                    paths_by_module,
                    trees_by_path,
                    factory_keys,
                ):
                    previous_size = len(owners[path])
                    owners[path].update(targets)
                    changed = changed or len(owners[path]) != previous_size
    return {path: frozenset(names) for path, names in owners.items()}


def _framework_class_names_by_path(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> dict[Path, set[str]]:
    trees_by_path = dict(parsed_modules)
    names = {
        path: set(_framework_class_names(tree))
        for path, tree in parsed_modules
    }
    changed = True
    while changed:
        changed = False
        class_keys = {
            (path, name)
            for path, path_names in names.items()
            for name in path_names
            if "." not in name
        }
        for path, tree in parsed_modules:
            symbol_targets, module_targets = _import_targets(
                root,
                path,
                tree,
                paths_by_module,
            )
            for local_name, target in symbol_targets.items():
                resolved = _follow_symbol_reexports(
                    root,
                    target,
                    paths_by_module,
                    trees_by_path,
                )
                if resolved in class_keys and local_name not in names[path]:
                    names[path].add(local_name)
                    changed = True
            for local_parts, target_path in module_targets.items():
                for target_name in names.get(target_path, set()):
                    if "." in target_name:
                        continue
                    qualified = ".".join((*local_parts, target_name))
                    if qualified not in names[path]:
                        names[path].add(qualified)
                        changed = True
            module_aliases = _framework_module_aliases(tree)
            for class_node in (
                node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
            ):
                if class_node.name in names[path]:
                    continue
                if any(
                    _is_framework_class_base(base, names[path], module_aliases)
                    or (
                        (
                            target := _resolve_reference_target(
                                root,
                                path,
                                tree,
                                base,
                                paths_by_module,
                                trees_by_path,
                            )
                        )
                        is not None
                        and _follow_symbol_reexports(
                            root,
                            target,
                            paths_by_module,
                            trees_by_path,
                        )
                        in class_keys
                    )
                    for base in class_node.bases
                ):
                    names[path].add(class_node.name)
                    changed = True
    return names


def _framework_class_names(tree: ast.Module) -> set[str]:
    constructor_aliases, module_aliases = _framework_constructor_imports(tree)
    class_names = set(constructor_aliases)
    class_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    changed = True
    while changed:
        changed = False
        for class_node in class_nodes:
            if class_node.name in class_names:
                continue
            if any(
                _is_framework_class_base(base, class_names, module_aliases)
                for base in class_node.bases
            ):
                class_names.add(class_node.name)
                changed = True
    return class_names


def _framework_module_aliases(tree: ast.Module) -> set[str]:
    return _framework_constructor_imports(tree)[1]


def _framework_constructor_imports(tree: ast.Module) -> tuple[set[str], set[str]]:
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
    return constructor_aliases, module_aliases


def _resolved_framework_factory_call(
    root: Path,
    path: Path,
    tree: ast.Module,
    node: ast.AST,
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    factory_keys: set[tuple[Path, str]],
) -> bool:
    if not isinstance(node, ast.Call):
        return False
    target = _resolve_reference_target(
        root,
        path,
        tree,
        node.func,
        paths_by_module,
        trees_by_path,
    )
    if target is None:
        return False
    return (
        _follow_symbol_reexports(
            root,
            target,
            paths_by_module,
            trees_by_path,
        )
        in factory_keys
    )


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


def _unresolved_mounted_asgi_violations(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    service_paths: frozenset[Path],
    route_owner_names: dict[Path, frozenset[str]],
    mounted_route_owners: frozenset[tuple[Path, str]],
) -> list[str]:
    trees_by_path = dict(parsed_modules)
    owner_keys = {
        (path, name)
        for path, names in route_owner_names.items()
        for name in names
        if "." not in name
    }
    violations: list[str] = []
    for path, tree in parsed_modules:
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "mount"
            ):
                continue
            caller = _resolve_reference_target(
                root, path, tree, call.func.value, paths_by_module, trees_by_path
            )
            if caller not in owner_keys or (
                path not in service_paths and caller not in mounted_route_owners
            ):
                continue
            mounted = next(
                (keyword.value for keyword in call.keywords if keyword.arg == "app"),
                call.args[1] if len(call.args) > 1 else None,
            )
            if mounted is None:
                continue
            target = _resolve_reference_target(
                root, path, tree, mounted, paths_by_module, trees_by_path
            )
            if target not in owner_keys:
                violations.append(
                    f"{path}:{mounted.lineno}: mounted ASGI callable cannot be "
                    "statically approved as a request boundary"
                )
    return violations


def _route_template_wire_violations(
    parsed_modules: list[tuple[Path, ast.Module]],
    service_paths: frozenset[Path],
    route_owner_names: dict[Path, frozenset[str]],
    mounted_route_owners: frozenset[tuple[Path, str]],
    wire_alias_constants: dict[Path, dict[str, str]],
) -> list[str]:
    violations: list[str] = []
    path_decorators = ROUTE_METHODS | {
        "api_route",
        "route",
        "websocket",
        "websocket_route",
    }
    path_registrars = PROGRAMMATIC_ROUTE_REGISTRARS - {"add_exception_handler"}
    for path, tree in parsed_modules:
        mounted_names = {
            name for owner_path, name in mounted_route_owners if owner_path == path
        }
        if path not in service_paths and not mounted_names:
            continue
        route_owners = route_owner_names.get(path, frozenset())
        route_constructor_aliases = _starlette_route_constructor_aliases(tree)
        route_module_aliases = _starlette_route_module_aliases(tree)
        _, framework_module_aliases = _framework_constructor_imports(tree)
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            template_nodes: list[ast.expr] = []
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr in path_decorators | path_registrars
                and _active_route_owner_reference(
                    call.func.value,
                    path_in_services=path in service_paths,
                    mounted_names=mounted_names,
                    route_owners=route_owners,
                )
            ):
                template_node = next(
                    (keyword.value for keyword in call.keywords if keyword.arg == "path"),
                    call.args[0] if call.args else None,
                )
                if template_node is not None:
                    template_nodes.append(template_node)
            elif _is_starlette_route_constructor(
                call,
                route_constructor_aliases,
                route_module_aliases,
            ):
                template_node = next(
                    (keyword.value for keyword in call.keywords if keyword.arg == "path"),
                    call.args[0] if call.args else None,
                )
                if template_node is not None:
                    template_nodes.append(template_node)
            elif _is_api_router_constructor(call, tree, framework_module_aliases):
                prefix_node = next(
                    (keyword.value for keyword in call.keywords if keyword.arg == "prefix"),
                    call.args[0] if call.args else None,
                )
                if prefix_node is not None:
                    template_nodes.append(prefix_node)
            elif (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "include_router"
                and _active_route_owner_reference(
                    call.func.value,
                    path_in_services=path in service_paths,
                    mounted_names=mounted_names,
                    route_owners=route_owners,
                )
            ):
                prefix_node = next(
                    (keyword.value for keyword in call.keywords if keyword.arg == "prefix"),
                    None,
                )
                if prefix_node is not None:
                    template_nodes.append(prefix_node)
            for template_node in template_nodes:
                template = _constant_string_value(
                    template_node,
                    wire_alias_constants[path],
                )
                if template is None:
                    continue
                try:
                    fields = [
                        field_name
                        for _, field_name, _, _ in Formatter().parse(template)
                        if field_name is not None
                    ]
                except ValueError:
                    continue
                for field_name in fields:
                    normalized = field_name.split(".", 1)[0].split("[", 1)[0]
                    if _is_banned_wire_name(normalized):
                        violations.append(
                            f"{path}:{template_node.lineno}: route template field "
                            f"{normalized} would accept raw vault secret material over "
                            "the wire"
                        )
    return violations


def _unapproved_asgi_middleware_violations(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    service_paths: frozenset[Path],
    route_owner_names: dict[Path, frozenset[str]],
    mounted_route_owners: frozenset[tuple[Path, str]],
) -> list[str]:
    trees_by_path = dict(parsed_modules)
    functions = _function_definitions(parsed_modules)
    function_scopes = _function_scopes(parsed_modules)
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    violations: list[str] = []
    for path, tree in parsed_modules:
        mounted_names = {
            name for owner_path, name in mounted_route_owners if owner_path == path
        }
        if path not in service_paths and not mounted_names:
            continue
        route_owners = route_owner_names.get(path, frozenset())
        direct_aliases = _base_http_middleware_aliases(tree)
        module_aliases = _base_http_middleware_module_aliases(tree)
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "add_middleware"
                and call.args
                and _active_route_owner_reference(
                    call.func.value,
                    path_in_services=path in service_paths,
                    mounted_names=mounted_names,
                    route_owners=route_owners,
                )
            ):
                continue
            middleware = call.args[0]
            if _is_base_http_middleware_reference(
                middleware,
                direct_aliases,
                module_aliases,
            ):
                continue
            owner_key = _enclosing_function_key(
                path,
                call,
                functions,
                function_scopes,
            )
            class_key = _resolve_model_key(
                root,
                path,
                tree,
                middleware,
                paths_by_module,
                trees_by_path,
                classes,
                class_scopes,
                owner_key,
                owner_scope=function_scopes.get(owner_key, ()),
                reference_line=call.lineno,
            )
            if _is_framework_middleware_reference(middleware, tree):
                continue
            call_key = (
                _resolve_class_method_key(
                    root=root,
                    class_key=class_key,
                    method_name="__call__",
                    paths_by_module=paths_by_module,
                    trees_by_path=trees_by_path,
                    classes=classes,
                    class_scopes=class_scopes,
                )
                if class_key is not None
                else None
            )
            if call_key is not None and not _asgi_callable_uses_unapproved_input(
                root,
                call_key,
                paths_by_module,
                trees_by_path,
                functions,
                function_scopes,
            ):
                continue
            violations.append(
                f"{path}:{middleware.lineno}: custom ASGI middleware cannot be "
                "statically approved as a request boundary"
            )
    return violations


def _is_framework_middleware_reference(node: ast.AST, tree: ast.Module) -> bool:
    parts = _qualified_name_parts(node)
    if not parts:
        return False
    if len(parts) == 1:
        return any(
            isinstance(import_node, ast.ImportFrom)
            and import_node.module is not None
            and import_node.module.startswith(
                ("fastapi.middleware", "starlette.middleware")
            )
            and any(
                (imported.asname or imported.name) == parts[0]
                for imported in import_node.names
            )
            for import_node in tree.body
        )
    return any(
        isinstance(import_node, ast.Import)
        and any(
            imported.name.startswith(
                ("fastapi.middleware", "starlette.middleware")
            )
            and (imported.asname or imported.name.split(".")[0]) == parts[0]
            for imported in import_node.names
        )
        for import_node in tree.body
    )


def _asgi_callable_uses_unapproved_input(
    root: Path,
    initial_key: tuple[Path, int, str],
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    functions: dict[tuple[Path, int, str], ast.FunctionDef | ast.AsyncFunctionDef],
    function_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
) -> bool:
    initial_function = functions[initial_key]
    initial_arguments = _function_arguments(initial_function)
    parsed_modules = list(trees_by_path.items())
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    pending = [
        (
            initial_key,
            frozenset(
                argument.arg
                for argument in initial_arguments
                if argument.arg == "scope"
                or (
                    argument.annotation is not None
                    and (_qualified_name_parts(argument.annotation) or [None])[-1]
                    == "Scope"
                )
            ),
            frozenset(
                argument.arg
                for argument in initial_arguments
                if argument.arg == "receive"
                or (
                    argument.annotation is not None
                    and (_qualified_name_parts(argument.annotation) or [None])[-1]
                    == "Receive"
                )
            ),
        )
    ]
    seen: set[tuple[tuple[Path, int, str], frozenset[str], frozenset[str]]] = set()
    while pending:
        function_key, initial_scopes, initial_receives = pending.pop()
        state = (function_key, initial_scopes, initial_receives)
        if state in seen:
            continue
        seen.add(state)
        function = functions[function_key]
        scope_names = set(initial_scopes)
        receive_names = set(initial_receives)
        assignments = [
            candidate
            for statement in function.body
            for candidate in ast.walk(statement)
            if isinstance(candidate, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        ]
        changed = True
        while changed:
            changed = False
            for assignment in assignments:
                targets, value = _assignment_targets_and_value(assignment)
                if _is_name_reference(value, scope_names):
                    before = len(scope_names)
                    scope_names.update(targets)
                    changed = changed or len(scope_names) != before
                if _is_name_reference(value, receive_names):
                    before = len(receive_names)
                    receive_names.update(targets)
                    changed = changed or len(receive_names) != before

        path = function_key[0]
        tree = trees_by_path[path]
        for statement in function.body:
            for candidate in ast.walk(statement):
                if isinstance(candidate, ast.Subscript):
                    container = candidate.value
                    key = candidate.slice
                elif (
                    isinstance(candidate, ast.Call)
                    and isinstance(candidate.func, ast.Attribute)
                    and candidate.func.attr in {"get", "getlist", "pop", "setdefault"}
                    and candidate.args
                ):
                    container = candidate.func.value
                    key = candidate.args[0]
                else:
                    container = None
                    key = None
                if container is not None and key is not None and _is_name_reference(
                    container,
                    scope_names,
                ):
                    key_value = _constant_string_value(key, {})
                    if key_value == "query_string" or (
                        key_value is not None and _is_banned_wire_name(key_value)
                    ):
                        return True
                if not isinstance(candidate, ast.Call):
                    continue
                if _is_name_reference(candidate.func, receive_names):
                    return True
                target = _resolve_bound_method_key(
                    root=root,
                    path=path,
                    tree=tree,
                    reference=candidate.func,
                    paths_by_module=paths_by_module,
                    trees_by_path=trees_by_path,
                    classes=classes,
                    class_scopes=class_scopes,
                    function_scopes=function_scopes,
                    owner_key=function_key,
                ) or _resolve_function_key(
                    root,
                    path,
                    tree,
                    candidate.func,
                    paths_by_module,
                    trees_by_path,
                    functions,
                    function_scopes,
                    function_key,
                )
                if target is None:
                    continue
                target_arguments = _function_arguments(functions[target])
                propagated_scopes = _bound_tracked_parameter_names(
                    candidate,
                    target_arguments,
                    scope_names,
                )
                propagated_receives = _bound_tracked_parameter_names(
                    candidate,
                    target_arguments,
                    receive_names,
                )
                if propagated_scopes or propagated_receives:
                    pending.append(
                        (
                            target,
                            frozenset(propagated_scopes),
                            frozenset(propagated_receives),
                        )
                    )
    return False


def _bound_tracked_parameter_names(
    call: ast.Call,
    parameters: list[ast.arg],
    tracked_names: set[str],
) -> set[str]:
    bound = {
        parameter.arg
        for parameter, argument in _bound_call_parameter_arguments(call, parameters)
        if _is_name_reference(argument, tracked_names)
    }
    parameters_by_name = {parameter.arg: parameter for parameter in parameters}
    bound.update(
        parameters_by_name[keyword.arg].arg
        for keyword in call.keywords
        if keyword.arg in parameters_by_name
        and _is_name_reference(keyword.value, tracked_names)
    )
    return bound


def _bound_call_parameter_arguments(
    call: ast.Call,
    parameters: list[ast.arg] | tuple[str, ...],
) -> list[tuple[ast.arg | str, ast.expr]]:
    positional_parameters = list(parameters)
    if (
        isinstance(call.func, ast.Attribute)
        and positional_parameters
        and len(call.args) < len(positional_parameters)
        and (
            positional_parameters[0].arg
            if isinstance(positional_parameters[0], ast.arg)
            else positional_parameters[0]
        )
        in {"self", "cls"}
    ):
        positional_parameters = positional_parameters[1:]
    return list(zip(positional_parameters, call.args, strict=False))


def _is_route_owner_reference(
    node: ast.AST,
    owner_names: set[str] | frozenset[str],
) -> bool:
    parts = _qualified_name_parts(node)
    if not parts:
        return False
    qualified = ".".join(parts)
    if parts[-1] in owner_names or qualified in owner_names:
        return True
    if len(parts) >= 2 and parts[-1] == "router":
        parent = ".".join(parts[:-1])
        return parts[-2] in owner_names or parent in owner_names
    return False


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
        and (
            ".".join(parts) in constructor_aliases
            or (
                parts[-1] in {"FastAPI", "APIRouter", "Starlette"}
                and parts[0] in module_aliases
            )
        )
    )


def _is_api_router_constructor(
    node: ast.Call,
    tree: ast.Module,
    module_aliases: set[str],
) -> bool:
    if isinstance(node.func, ast.Name):
        return any(
            isinstance(import_node, ast.ImportFrom)
            and import_node.module is not None
            and import_node.module.startswith("fastapi")
            and any(
                imported.name == "APIRouter"
                and (imported.asname or imported.name) == node.func.id
                for imported in import_node.names
            )
            for import_node in ast.walk(tree)
        )
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and len(parts) >= 2
        and parts[-1] == "APIRouter"
        and parts[0] in module_aliases
    )


def _unapproved_route_class_violations(
    parsed_modules: list[tuple[Path, ast.Module]],
    service_paths: frozenset[Path],
    route_owner_names: dict[Path, frozenset[str]],
) -> list[str]:
    violations: list[str] = []
    for path, tree in parsed_modules:
        if path not in service_paths:
            continue
        module_aliases = _framework_module_aliases(tree)
        owners = route_owner_names.get(path, frozenset())
        default_aliases = {
            imported.asname or imported.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "fastapi.routing"
            for imported in node.names
            if imported.name == "APIRoute"
        }
        def is_default(route_class: ast.AST) -> bool:
            parts = _qualified_name_parts(route_class)
            return (
                isinstance(route_class, ast.Name)
                and route_class.id in default_aliases
            ) or bool(
                parts
                and len(parts) >= 2
                and parts[-1] == "APIRoute"
                and parts[0] in module_aliases
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                route_class_keyword = next(
                    (
                        keyword
                        for keyword in node.keywords
                        if keyword.arg in {"route_class", "route_class_override"}
                    ),
                    None,
                )
                if route_class_keyword is None:
                    continue
                is_router_constructor = _is_api_router_constructor(
                    node,
                    tree,
                    module_aliases,
                )
                is_route_registration = (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr
                    in ROUTE_METHODS
                    | GENERIC_ROUTE_DECORATORS
                    | PROGRAMMATIC_ROUTE_REGISTRARS
                    and _is_route_owner_reference(node.func.value, owners)
                )
                if not (is_router_constructor or is_route_registration):
                    continue
                if is_default(route_class_keyword.value):
                    continue
                violations.append(
                    f"{path}:{route_class_keyword.value.lineno}: "
                    f"{route_class_keyword.arg} cannot be statically approved"
                )
                continue
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(
                isinstance(target, ast.Attribute)
                and target.attr == "route_class"
                and _is_route_owner_reference(target.value, owners)
                for target in targets
            ) or is_default(node.value):
                continue
            violations.append(
                f"{path}:{node.value.lineno}: assigned route_class cannot be "
                "statically approved"
            )
    return violations


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


def _pydantic_dataclass_model_keys(
    parsed_modules: list[tuple[Path, ast.Module]],
) -> frozenset[tuple[Path, int, str]]:
    return frozenset(
        key
        for path, tree in parsed_modules
        for key, node, _ in _module_class_entries(path, tree)
        if any(
            _is_pydantic_dataclass_decorator(decorator, tree)
            for decorator in node.decorator_list
        )
    )


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


def _functional_dataclass_definitions(
    parsed_modules: list[tuple[Path, ast.Module]],
) -> dict[tuple[Path, int, str], ast.Call]:
    definitions: dict[tuple[Path, int, str], ast.Call] = {}
    for path, tree in parsed_modules:
        direct_aliases = {
            imported.asname or imported.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "dataclasses"
            for imported in node.names
            if imported.name == "make_dataclass"
        }
        module_aliases = {
            imported.asname or imported.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for imported in node.names
            if imported.name == "dataclasses"
        }
        for key, value, _, _ in _module_assignment_entries(path, tree):
            if isinstance(value, ast.Call) and _is_make_dataclass_call(
                value,
                direct_aliases,
                module_aliases,
            ):
                definitions[key] = value
    return definitions


def _is_make_dataclass_call(
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
        and parts[-1] == "make_dataclass"
        and parts[0] in module_aliases
    )


def _functional_dataclass_fields(
    node: ast.Call,
    tree: ast.Module,
) -> tuple[list[tuple[str, int]], list[int]]:
    fields_node = next(
        (keyword.value for keyword in node.keywords if keyword.arg == "fields"),
        node.args[1] if len(node.args) > 1 else None,
    )
    if fields_node is None:
        return [], [node.lineno]
    resolved = _resolve_module_expression(
        fields_node,
        tree,
        before_line=node.lineno,
    )
    if not isinstance(resolved, (ast.List, ast.Tuple)):
        return [], [fields_node.lineno]
    fields: list[tuple[str, int]] = []
    opaque_lines: list[int] = []
    for element in resolved.elts:
        resolved_element = _resolve_module_expression(
            element,
            tree,
            before_line=element.lineno,
        )
        name_node: ast.AST | None
        if isinstance(resolved_element, ast.Constant):
            name_node = resolved_element
        elif isinstance(resolved_element, (ast.List, ast.Tuple)) and resolved_element.elts:
            name_node = resolved_element.elts[0]
        else:
            name_node = None
        if (
            isinstance(name_node, ast.Constant)
            and isinstance(name_node.value, str)
        ):
            fields.append((name_node.value, name_node.lineno))
        else:
            opaque_lines.append(element.lineno)
    return fields, opaque_lines


def _functional_dataclass_type_roots(
    node: ast.Call,
    tree: ast.Module,
) -> list[ast.AST]:
    fields_node = next(
        (keyword.value for keyword in node.keywords if keyword.arg == "fields"),
        node.args[1] if len(node.args) > 1 else None,
    )
    if fields_node is None:
        return []
    resolved = _resolve_module_expression(
        fields_node,
        tree,
        before_line=node.lineno,
    )
    if not isinstance(resolved, (ast.List, ast.Tuple)):
        return []
    roots: list[ast.AST] = []
    for element in resolved.elts:
        resolved_element = _resolve_module_expression(
            element,
            tree,
            before_line=element.lineno,
        )
        if (
            isinstance(resolved_element, (ast.List, ast.Tuple))
            and len(resolved_element.elts) > 1
        ):
            roots.append(resolved_element.elts[1])
    return roots


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
    tree: ast.Module,
    field_factory_names: frozenset[str],
    field_factory_module_names: frozenset[str],
    wire_alias_constants: dict[str, str],
) -> tuple[list[tuple[str, int]], list[int]]:
    fields: list[tuple[str, int]] = []
    opaque_lines: list[int] = []
    reserved = {
        "__base__",
        "__cls_kwargs__",
        "__config__",
        "__doc__",
        "__module__",
        "__validators__",
    }
    for keyword in node.keywords:
        if keyword.arg is None:
            unpacked = _resolve_module_expression(
                keyword.value,
                tree,
                before_line=node.lineno,
            )
            unpacked_fields, unpacked_opaque = _pydantic_create_model_mapping_fields(
                unpacked,
                tree=tree,
                before_line=keyword.value.lineno,
                reserved=reserved,
                field_factory_names=field_factory_names,
                field_factory_module_names=field_factory_module_names,
                wire_alias_constants=wire_alias_constants,
            )
            fields.extend(unpacked_fields)
            if unpacked_opaque:
                opaque_lines.append(keyword.value.lineno)
            continue
        if keyword.arg in reserved:
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
    return fields, opaque_lines


def _pydantic_create_model_mapping_fields(
    node: ast.expr,
    *,
    tree: ast.Module,
    before_line: int,
    reserved: set[str],
    field_factory_names: frozenset[str],
    field_factory_module_names: frozenset[str],
    wire_alias_constants: dict[str, str],
) -> tuple[list[tuple[str, int]], bool]:
    resolved = _resolve_module_expression(
        node,
        tree,
        before_line=before_line,
    )
    if not isinstance(resolved, ast.Dict):
        return [], True
    fields: list[tuple[str, int]] = []
    opaque = False
    for key, value in zip(resolved.keys, resolved.values, strict=True):
        if key is None:
            nested, nested_opaque = _pydantic_create_model_mapping_fields(
                value,
                tree=tree,
                before_line=value.lineno,
                reserved=reserved,
                field_factory_names=field_factory_names,
                field_factory_module_names=field_factory_module_names,
                wire_alias_constants=wire_alias_constants,
            )
            fields.extend(nested)
            opaque = opaque or nested_opaque
            continue
        if not (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
        ):
            opaque = True
            continue
        if key.value in reserved:
            continue
        fields.append((key.value, key.lineno))
        fields.extend(
            _pydantic_field_aliases(
                value,
                field_factory_names=field_factory_names,
                field_factory_module_names=field_factory_module_names,
                wire_alias_constants=wire_alias_constants,
            )
        )
    return fields, opaque


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
    imported_values: dict[str, ast.expr] | None = None,
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
        return (
            imported_values[node.id]
            if imported_values is not None and node.id in imported_values
            else node
        )
    line, value = max(candidates, key=lambda entry: entry[0])
    return _resolve_module_expression(
        value,
        tree,
        before_line=line,
        seen=seen | {node.id},
        imported_values=imported_values,
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


def _functional_typed_dict_fields(
    node: ast.Call,
    tree: ast.Module,
    imported_values: dict[str, ast.expr] | None = None,
) -> list[tuple[str, int]]:
    fields: list[tuple[str, int]] = []
    mapping = node.args[1] if len(node.args) > 1 else None
    resolved_mapping = _resolve_named_dict_literal(
        mapping,
        tree,
        imported_values=imported_values,
    )
    if resolved_mapping is not None:
        fields.extend(
            (key.value, key.lineno)
            for key in resolved_mapping.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        )
    fields.extend(
        (keyword.arg, keyword.value.lineno)
        for keyword in node.keywords
        if keyword.arg not in {None, "total", "closed"}
    )
    return fields


def _functional_typed_dict_type_roots(
    node: ast.Call,
    tree: ast.Module,
    imported_values: dict[str, ast.expr] | None = None,
) -> list[ast.AST]:
    mapping = node.args[1] if len(node.args) > 1 else None
    resolved_mapping = _resolve_named_dict_literal(
        mapping,
        tree,
        imported_values=imported_values,
    )
    roots = list(resolved_mapping.values) if resolved_mapping is not None else []
    roots.extend(
        keyword.value
        for keyword in node.keywords
        if keyword.arg not in {None, "total", "closed"}
    )
    return roots


def _resolve_named_dict_literal(
    node: ast.AST | None,
    tree: ast.Module,
    *,
    imported_values: dict[str, ast.expr] | None = None,
) -> ast.Dict | None:
    if isinstance(node, ast.Dict):
        return node
    if not isinstance(node, ast.Name):
        return None
    imported = imported_values.get(node.id) if imported_values is not None else None
    if isinstance(imported, ast.Dict):
        return imported
    candidates = [
        (key, value)
        for key, value, scope, _ in _module_assignment_entries(Path("."), tree)
        if key[2] == node.id
        and not scope
        and key[1] <= node.lineno
        and isinstance(value, ast.Dict)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0][1])[1]


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


def _is_pydantic_dataclass_decorator(
    decorator: ast.expr,
    tree: ast.Module,
) -> bool:
    candidate = decorator.func if isinstance(decorator, ast.Call) else decorator
    direct_aliases = {
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "pydantic.dataclasses"
        for imported in node.names
        if imported.name == "dataclass"
    }
    module_aliases = {
        imported.asname or imported.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name in {"pydantic", "pydantic.dataclasses"}
    }
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


def _permissive_model_extra_line(
    node: ast.ClassDef,
    module_tree: ast.Module,
    imported_values: dict[str, ast.expr],
) -> int | None:
    for keyword in node.keywords:
        if keyword.arg == "extra" and _is_allow_value(keyword.value):
            return keyword.value.lineno
    for child in node.body:
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            if any(isinstance(target, ast.Name) and target.id == "model_config" for target in targets):
                config = _resolve_class_or_module_expression(
                    child.value,
                    node,
                    module_tree,
                    before_line=child.lineno,
                    imported_values=imported_values,
                )
                if _expression_allows_extra(config):
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


def _model_alias_generator_line(
    node: ast.ClassDef,
    module_tree: ast.Module,
    imported_values: dict[str, ast.expr],
) -> int | None:
    for keyword in node.keywords:
        if keyword.arg == "alias_generator":
            return keyword.value.lineno
    for child in node.body:
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            if any(
                isinstance(target, ast.Name) and target.id == "model_config"
                for target in targets
            ):
                config = _resolve_class_or_module_expression(
                    child.value,
                    node,
                    module_tree,
                    before_line=child.lineno,
                    imported_values=imported_values,
                )
                if _expression_defines_config_option(config, "alias_generator"):
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


def _resolve_class_or_module_expression(
    expression: ast.expr,
    class_node: ast.ClassDef,
    module_tree: ast.Module,
    *,
    before_line: int,
    seen: frozenset[str] = frozenset(),
    imported_values: dict[str, ast.expr] | None = None,
) -> ast.expr:
    if not isinstance(expression, ast.Name) or expression.id in seen:
        return expression
    candidates: list[tuple[int, ast.expr]] = []
    for statement in class_node.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets, value = _assignment_targets_and_value(statement)
        if expression.id in targets and statement.lineno < before_line:
            candidates.append((statement.lineno, value))
    if candidates:
        line, value = max(candidates, key=lambda entry: entry[0])
        return _resolve_class_or_module_expression(
            value,
            class_node,
            module_tree,
            before_line=line,
            seen=seen | {expression.id},
            imported_values=imported_values,
        )
    return _resolve_module_expression(
        expression,
        module_tree,
        before_line=class_node.lineno,
        seen=seen,
        imported_values=imported_values,
    )


def _pydantic_dataclass_extra_line(
    node: ast.ClassDef,
    module_tree: ast.Module,
    imported_values: dict[str, ast.expr],
) -> int | None:
    for decorator in node.decorator_list:
        if not (
            isinstance(decorator, ast.Call)
            and _is_pydantic_dataclass_decorator(decorator, module_tree)
        ):
            continue
        config = next(
            (
                keyword.value
                for keyword in decorator.keywords
                if keyword.arg == "config"
            ),
            None,
        )
        if config is None:
            continue
        resolved = _resolve_module_expression(
            config,
            module_tree,
            before_line=node.lineno,
            imported_values=imported_values,
        )
        if _expression_allows_extra(resolved):
            return config.lineno
    return None


def _pydantic_dataclass_alias_generator_line(
    node: ast.ClassDef,
    module_tree: ast.Module,
    imported_values: dict[str, ast.expr],
) -> int | None:
    for decorator in node.decorator_list:
        if not (
            isinstance(decorator, ast.Call)
            and _is_pydantic_dataclass_decorator(decorator, module_tree)
        ):
            continue
        config = next(
            (
                keyword.value
                for keyword in decorator.keywords
                if keyword.arg == "config"
            ),
            None,
        )
        if config is None:
            continue
        resolved = _resolve_module_expression(
            config,
            module_tree,
            before_line=node.lineno,
            imported_values=imported_values,
        )
        if _expression_defines_config_option(resolved, "alias_generator"):
            return config.lineno
    return None


def _pydantic_v1_config_field_aliases(
    node: ast.ClassDef,
    wire_alias_constants: dict[str, str],
    module_statements: list[ast.stmt],
) -> list[tuple[str, int]]:
    aliases: list[tuple[str, int]] = []
    module_mappings = _named_dict_literals(
        module_statements,
        before_line=node.lineno,
    )
    for config in node.body:
        if not isinstance(config, ast.ClassDef) or config.name != "Config":
            continue
        named_mappings = _named_dict_literals(
            node.body,
            before_line=config.lineno,
            initial=module_mappings,
        )
        for setting in config.body:
            if not isinstance(setting, (ast.Assign, ast.AnnAssign)):
                continue
            config_mappings = _named_dict_literals(
                config.body,
                before_line=setting.lineno,
                initial=named_mappings,
            )
            targets = (
                setting.targets
                if isinstance(setting, ast.Assign)
                else [setting.target]
            )
            if not any(
                isinstance(target, ast.Name) and target.id == "fields"
                for target in targets
            ):
                continue
            field_mappings = _resolve_named_dict_alias(
                setting.value,
                config_mappings,
            )
            if field_mappings is None:
                continue
            for field_config in field_mappings.values:
                if isinstance(field_config, ast.Dict):
                    for key, value in zip(
                        field_config.keys,
                        field_config.values,
                        strict=True,
                    ):
                        if key is None or (
                            _constant_string_value(key, wire_alias_constants)
                            != "alias"
                        ):
                            continue
                        aliases.extend(
                            _wire_alias_values(value, wire_alias_constants)
                        )
                else:
                    aliases.extend(
                        _wire_alias_values(field_config, wire_alias_constants)
                    )
    return aliases


def _named_dict_literals(
    statements: list[ast.stmt],
    *,
    before_line: int,
    initial: dict[str, ast.Dict] | None = None,
) -> dict[str, ast.Dict]:
    mappings = dict(initial or {})
    for statement in statements:
        if statement.lineno >= before_line or not isinstance(
            statement,
            (ast.Assign, ast.AnnAssign),
        ):
            continue
        value = _resolve_named_dict_alias(statement.value, mappings)
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if value is None:
                mappings.pop(target.id, None)
            else:
                mappings[target.id] = value
    return mappings


def _resolve_named_dict_alias(
    node: ast.expr,
    mappings: dict[str, ast.Dict],
) -> ast.Dict | None:
    if isinstance(node, ast.Dict):
        return node
    if isinstance(node, ast.Name):
        return mappings.get(node.id)
    return None


def _raw_model_validator(
    node: ast.ClassDef,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
    root_direct_aliases: frozenset[str],
) -> tuple[str, int] | None:
    for child in node.body:
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in child.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if isinstance(decorator.func, ast.Name):
                is_model_validator = decorator.func.id in direct_aliases
                is_root_validator = decorator.func.id in root_direct_aliases
            else:
                parts = _qualified_name_parts(decorator.func)
                is_model_validator = bool(
                    parts
                    and len(parts) >= 2
                    and parts[-1] == "model_validator"
                    and parts[0] in module_aliases
                )
                is_root_validator = bool(
                    parts
                    and len(parts) >= 2
                    and parts[-1] == "root_validator"
                    and parts[0] in module_aliases
                )
            if not is_model_validator and not is_root_validator:
                continue
            if is_root_validator:
                pre = next(
                    (
                        keyword.value
                        for keyword in decorator.keywords
                        if keyword.arg == "pre"
                    ),
                    None,
                )
                if isinstance(pre, ast.Constant) and pre.value is True:
                    return "pre root validator", decorator.lineno
                continue
            mode = next(
                (
                    keyword.value
                    for keyword in decorator.keywords
                    if keyword.arg == "mode"
                ),
                None,
            )
            if (
                isinstance(mode, ast.Constant)
                and mode.value in {"before", "wrap"}
            ):
                return f"{mode.value} model validator", decorator.lineno
    return None


def _unconstrained_mapping_model_fields(
    node: ast.ClassDef,
) -> list[tuple[str, int]]:
    return [
        (child.target.id, child.annotation.lineno)
        for child in node.body
        if isinstance(child, ast.AnnAssign)
        and isinstance(child.target, ast.Name)
        and not _is_class_variable(child.annotation)
        and _annotation_contains_mapping(child.annotation)
    ]


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
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    *,
    depends_aliases: frozenset[str],
    depends_module_aliases: frozenset[str],
    wire_alias_constants: dict[str, str],
    included_parameter_names: frozenset[str] | None = None,
) -> list[tuple[str, int]]:
    parameters = _function_arguments(node)
    defaults_by_argument = _parameter_defaults(node)

    wire_names: list[tuple[str, int]] = []
    for argument in parameters:
        if (
            included_parameter_names is not None
            and argument.arg not in included_parameter_names
        ):
            continue
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
    parameter_uses = [
        candidate
        for statement in node.body
        for candidate in ast.walk(statement)
        if isinstance(candidate, ast.Name)
        and isinstance(candidate.ctx, ast.Load)
        and candidate.id == parameter_name
    ]
    validated_uses = {
        id(argument)
        for statement in node.body
        for candidate in ast.walk(statement)
        if isinstance(candidate, ast.Call)
        and isinstance(candidate.func, ast.Attribute)
        and candidate.func.attr == "model_validate"
        for argument in [
            *candidate.args,
            *(
                keyword.value
                for keyword in candidate.keywords
                if keyword.arg in {"obj", "object"}
            ),
        ]
        if isinstance(argument, ast.Name) and argument.id == parameter_name
    }
    return bool(validated_uses) and all(
        id(parameter_use) in validated_uses for parameter_use in parameter_uses
    )


def _parameter_defaults(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
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
    pending: list[ast.AST] = [annotation]
    seen_strings: set[str] = set()
    while pending:
        candidate = pending.pop()
        if isinstance(candidate, ast.Subscript) and _is_mapping_type_reference(
            candidate.value
        ):
            if not _mapping_key_excludes_banned_names(candidate.slice):
                return True
            continue
        if _is_mapping_type_reference(candidate):
            return True
        if (
            isinstance(candidate, ast.Constant)
            and isinstance(candidate.value, str)
            and candidate.value not in seen_strings
        ):
            seen_strings.add(candidate.value)
            try:
                pending.append(ast.parse(candidate.value, mode="eval").body)
            except SyntaxError:
                pass
        pending.extend(ast.iter_child_nodes(candidate))
    return False


def _is_mapping_type_reference(node: ast.AST) -> bool:
    mapping_names = {"dict", "Dict", "Mapping", "MutableMapping"}
    module_names = {"typing", "collections", "collections.abc"}
    parts = _qualified_name_parts(node)
    return bool(
        parts
        and parts[-1] in mapping_names
        and (len(parts) == 1 or ".".join(parts[:-1]) in module_names)
    )


def _mapping_key_excludes_banned_names(slice_node: ast.expr) -> bool:
    key_annotation = (
        slice_node.elts[0]
        if isinstance(slice_node, ast.Tuple) and slice_node.elts
        else slice_node
    )
    if not isinstance(key_annotation, ast.Subscript):
        return False
    parts = _qualified_name_parts(key_annotation.value)
    if not parts or parts[-1] != "Literal":
        return False
    literal_values = (
        key_annotation.slice.elts
        if isinstance(key_annotation.slice, ast.Tuple)
        else [key_annotation.slice]
    )
    return bool(literal_values) and all(
        isinstance(value, ast.Constant)
        and isinstance(value.value, str)
        and not _is_banned_wire_name(value.value)
        for value in literal_values
    )


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
    values = [
        value
        for child in children
        for value in _wire_alias_values(child, constants)
    ]
    if isinstance(node, ast.Call) and not values:
        return [("vault_key", node.lineno)]
    return values


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


def _string_constants_by_path(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> dict[Path, dict[str, str]]:
    trees_by_path = dict(parsed_modules)
    constants = {
        path: _string_constants(tree.body) for path, tree in parsed_modules
    }
    changed = True
    while changed:
        changed = False
        for path, tree in parsed_modules:
            import_lines = _import_binding_lines(tree)
            assignment_lines = _module_assignment_lines(tree)
            symbol_targets, module_targets = _import_targets(
                root,
                path,
                tree,
                paths_by_module,
            )
            for local_name, target in symbol_targets.items():
                if local_name not in import_lines or assignment_lines.get(
                    local_name,
                    -1,
                ) > import_lines.get(
                    local_name,
                    -1,
                ):
                    continue
                target_path, target_name = _follow_symbol_reexports(
                    root,
                    target,
                    paths_by_module,
                    trees_by_path,
                )
                value = constants.get(target_path, {}).get(target_name)
                if value is not None and constants[path].get(local_name) != value:
                    constants[path][local_name] = value
                    changed = True
            for local_parts, target_path in module_targets.items():
                if local_parts[0] not in import_lines or assignment_lines.get(
                    local_parts[0],
                    -1,
                ) > import_lines.get(
                    local_parts[0],
                    -1,
                ):
                    continue
                for target_name, value in constants.get(target_path, {}).items():
                    if "." in target_name:
                        continue
                    qualified_name = ".".join((*local_parts, target_name))
                    if constants[path].get(qualified_name) != value:
                        constants[path][qualified_name] = value
                        changed = True
    return constants


def _import_binding_lines(tree: ast.Module) -> dict[str, int]:
    lines: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for imported in node.names:
                local_name = imported.asname or imported.name.split(".")[0]
                lines[local_name] = max(lines.get(local_name, -1), node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for imported in node.names:
                if imported.name == "*":
                    continue
                local_name = imported.asname or imported.name
                lines[local_name] = max(lines.get(local_name, -1), node.lineno)
    return lines


def _module_assignment_lines(tree: ast.Module) -> dict[str, int]:
    lines: dict[str, int] = {}
    for statement in tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
        )
        for target in targets:
            if isinstance(target, ast.Name):
                lines[target.id] = statement.lineno
    return lines


def _constant_string_value(
    node: ast.expr,
    constants: dict[str, str],
) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute):
        parts = _qualified_name_parts(node)
        return constants.get(".".join(parts)) if parts else None
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
) -> tuple[
    frozenset[tuple[Path, int, str]],
    tuple[tuple[Path, ast.Lambda], ...],
    tuple[tuple[tuple[Path, int, str], frozenset[str]], ...],
    frozenset[tuple[Path, int, str]],
]:
    trees_by_path = dict(parsed_modules)
    functions = _function_definitions(parsed_modules)
    scopes = _function_scopes(parsed_modules)
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    handlers: set[tuple[Path, int, str]] = set()
    lambda_handlers: list[tuple[Path, ast.Lambda]] = []
    partial_handlers: list[
        tuple[tuple[Path, int, str], frozenset[str]]
    ] = []
    positional_request_handlers: set[tuple[Path, int, str]] = set()
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
            owner_key = _enclosing_function_key(path, node, functions, scopes)
            positional_request_handler = False
            is_route_constructor = _is_starlette_route_constructor(
                node,
                route_constructor_aliases,
                route_module_aliases,
            )
            if is_route_constructor:
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
            ):
                if _is_base_http_middleware_reference(
                    node.args[0],
                    middleware_aliases,
                    middleware_module_aliases,
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
                    class_key = _resolve_model_key(
                        root,
                        path,
                        tree,
                        node.args[0],
                        paths_by_module,
                        trees_by_path,
                        classes,
                        class_scopes,
                        None,
                        owner_scope=scopes.get(owner_key, ()),
                        reference_line=node.lineno,
                    )
                    if class_key is None:
                        continue
                    method_name = (
                        "dispatch"
                        if _class_inherits_base_http_middleware(
                            root=root,
                            class_key=class_key,
                            paths_by_module=paths_by_module,
                            trees_by_path=trees_by_path,
                            classes=classes,
                            class_scopes=class_scopes,
                        )
                        else "__call__"
                    )
                    target = _resolve_class_method_key(
                        root=root,
                        class_key=class_key,
                        method_name=method_name,
                        paths_by_module=paths_by_module,
                        trees_by_path=trees_by_path,
                        classes=classes,
                        class_scopes=class_scopes,
                    )
                    if target is not None:
                        handlers.add(target)
                    continue
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
                positional_request_handler = (
                    node.func.attr == "add_exception_handler"
                )
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
            if isinstance(endpoint, ast.Lambda):
                lambda_handlers.append((path, endpoint))
                continue
            partial_handler = _resolve_partial_route_handler(
                root=root,
                path=path,
                tree=tree,
                endpoint=endpoint,
                paths_by_module=paths_by_module,
                trees_by_path=trees_by_path,
                functions=functions,
                scopes=scopes,
                classes=classes,
                class_scopes=class_scopes,
                owner_key=owner_key,
            )
            if partial_handler is not None:
                partial_handlers.append(partial_handler)
                continue
            if is_route_constructor:
                class_key = _resolve_model_key(
                    root,
                    path,
                    tree,
                    endpoint,
                    paths_by_module,
                    trees_by_path,
                    classes,
                    class_scopes,
                    owner_key,
                    owner_scope=scopes.get(owner_key, ()),
                    reference_line=endpoint.lineno,
                )
                if class_key is not None and _class_inherits_starlette_endpoint(
                    root=root,
                    class_key=class_key,
                    paths_by_module=paths_by_module,
                    trees_by_path=trees_by_path,
                    classes=classes,
                    class_scopes=class_scopes,
                ):
                    class_handlers = {
                        target
                        for method_name in (
                            ROUTE_METHODS
                            | {
                                "__call__",
                                "dispatch",
                                "on_connect",
                                "on_disconnect",
                                "on_receive",
                            }
                        )
                        if (
                            target := _resolve_class_method_key(
                                root=root,
                                class_key=class_key,
                                method_name=method_name,
                                paths_by_module=paths_by_module,
                                trees_by_path=trees_by_path,
                                classes=classes,
                                class_scopes=class_scopes,
                            )
                        )
                        is not None
                    }
                    if class_handlers:
                        handlers.update(class_handlers)
                        continue
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
                if positional_request_handler:
                    positional_request_handlers.add(target)
    return (
        frozenset(handlers),
        tuple(lambda_handlers),
        tuple(partial_handlers),
        frozenset(positional_request_handlers),
    )


def _resolve_partial_route_handler(
    *,
    root: Path,
    path: Path,
    tree: ast.Module,
    endpoint: ast.expr,
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    functions: dict[tuple[Path, int, str], ast.FunctionDef | ast.AsyncFunctionDef],
    scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    classes: dict[tuple[Path, int, str], ast.ClassDef],
    class_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    owner_key: tuple[Path, int, str] | None,
) -> tuple[tuple[Path, int, str], frozenset[str]] | None:
    candidates: list[ast.expr] = [endpoint]
    if isinstance(endpoint, ast.Name):
        candidates = [
            value
            for _, value, _ in _visible_assignment_candidates(
                path,
                tree,
                endpoint.id,
                endpoint.lineno,
                scopes.get(owner_key, ()),
            )
        ]
    direct_aliases = {
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "functools"
        for imported in node.names
        if imported.name == "partial"
    }
    module_aliases = {
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name == "functools"
    }
    for candidate in candidates:
        if not isinstance(candidate, ast.Call):
            continue
        is_partial = (
            isinstance(candidate.func, ast.Name)
            and candidate.func.id in direct_aliases
        ) or (
            isinstance(candidate.func, ast.Attribute)
            and candidate.func.attr == "partial"
            and isinstance(candidate.func.value, ast.Name)
            and candidate.func.value.id in module_aliases
        )
        if not is_partial:
            continue
        wrapped = candidate.args[0] if candidate.args else next(
            (
                keyword.value
                for keyword in candidate.keywords
                if keyword.arg == "func"
            ),
            None,
        )
        if wrapped is None:
            continue
        bound_target = _resolve_bound_method_key(
            root=root,
            path=path,
            tree=tree,
            reference=wrapped,
            paths_by_module=paths_by_module,
            trees_by_path=trees_by_path,
            classes=classes,
            class_scopes=class_scopes,
            function_scopes=scopes,
            owner_key=owner_key,
        )
        callable_target = _resolve_callable_instance_key(
            root=root,
            path=path,
            tree=tree,
            reference=wrapped,
            paths_by_module=paths_by_module,
            trees_by_path=trees_by_path,
            classes=classes,
            class_scopes=class_scopes,
            function_scopes=scopes,
            owner_key=owner_key,
        )
        unbound_target: tuple[Path, int, str] | None = None
        if (
            bound_target is None
            and callable_target is None
            and isinstance(wrapped, ast.Attribute)
        ):
            class_key = _resolve_model_key(
                root,
                path,
                tree,
                wrapped.value,
                paths_by_module,
                trees_by_path,
                classes,
                class_scopes,
                owner_key,
                owner_scope=scopes.get(owner_key, ()),
                reference_line=wrapped.lineno,
            )
            if class_key is not None:
                unbound_target = _resolve_class_method_key(
                    root=root,
                    class_key=class_key,
                    method_name=wrapped.attr,
                    paths_by_module=paths_by_module,
                    trees_by_path=trees_by_path,
                    classes=classes,
                    class_scopes=class_scopes,
                )
        target = bound_target or callable_target or unbound_target or _resolve_function_key(
            root,
            path,
            tree,
            wrapped,
            paths_by_module,
            trees_by_path,
            functions,
            scopes,
            owner_key,
        )
        if target is None:
            continue
        parameters = _function_arguments(functions[target])
        if (
            (bound_target is not None or callable_target is not None)
            and parameters
            and parameters[0].arg in {"self", "cls"}
        ):
            parameters = parameters[1:]
        bound_positionals = max(len(candidate.args) - 1, 0)
        remaining = frozenset(
            parameter.arg
            for parameter in parameters[bound_positionals:]
        )
        return target, remaining
    return None


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


def _class_inherits_base_http_middleware(
    *,
    root: Path,
    class_key: tuple[Path, int, str],
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    classes: dict[tuple[Path, int, str], ast.ClassDef],
    class_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
) -> bool:
    for candidate_key in _resolve_class_mro_keys(
        root=root,
        class_key=class_key,
        paths_by_module=paths_by_module,
        trees_by_path=trees_by_path,
        classes=classes,
        class_scopes=class_scopes,
    ):
        class_node = classes.get(candidate_key)
        tree = trees_by_path.get(candidate_key[0])
        if class_node is None or tree is None:
            continue
        direct_aliases = _base_http_middleware_aliases(tree)
        module_aliases = _base_http_middleware_module_aliases(tree)
        if any(
            _is_base_http_middleware_reference(
                base,
                direct_aliases,
                module_aliases,
            )
            for base in class_node.bases
        ):
            return True
    return False


def _starlette_endpoint_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "starlette.endpoints"
        for imported in node.names
        if imported.name in {"HTTPEndpoint", "WebSocketEndpoint"}
    )


def _starlette_endpoint_module_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        imported.asname or imported.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported in node.names
        if imported.name in {"starlette", "starlette.endpoints"}
    )


def _is_starlette_endpoint_reference(
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
        and parts[-1] in {"HTTPEndpoint", "WebSocketEndpoint"}
        and parts[0] in module_aliases
    )


def _class_inherits_starlette_endpoint(
    *,
    root: Path,
    class_key: tuple[Path, int, str],
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    classes: dict[tuple[Path, int, str], ast.ClassDef],
    class_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
) -> bool:
    for candidate_key in _resolve_class_mro_keys(
        root=root,
        class_key=class_key,
        paths_by_module=paths_by_module,
        trees_by_path=trees_by_path,
        classes=classes,
        class_scopes=class_scopes,
    ):
        class_node = classes.get(candidate_key)
        tree = trees_by_path.get(candidate_key[0])
        if class_node is None or tree is None:
            continue
        direct_aliases = _starlette_endpoint_aliases(tree)
        module_aliases = _starlette_endpoint_module_aliases(tree)
        if any(
            _is_starlette_endpoint_reference(
                base,
                direct_aliases,
                module_aliases,
            )
            for base in class_node.bases
        ):
            return True
    return False


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
    if (
        isinstance(reference.value, ast.Name)
        and reference.value.id in {"self", "cls"}
    ):
        enclosing_classes = [
            key
            for key in classes
            if key[0] == path and key[1] in owner_scope
        ]
        if enclosing_classes:
            class_key = max(
                enclosing_classes,
                key=lambda key: owner_scope.index(key[1]),
            )
            return _resolve_class_method_key(
                root=root,
                class_key=class_key,
                method_name=reference.attr,
                paths_by_module=paths_by_module,
                trees_by_path=trees_by_path,
                classes=classes,
                class_scopes=class_scopes,
            )
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


def _is_scoped_route_handler(
    root: Path,
    path: Path,
    tree: ast.Module,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    owner_scope: tuple[int, ...],
    route_owner_names: frozenset[str],
    framework_class_names: set[str],
    factory_names: frozenset[str],
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    factory_keys: set[tuple[Path, str]],
) -> bool:
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        decorator_reference = call.func if call is not None else decorator
        for func, func_line, func_scope in _visible_route_decorator_references(
            path,
            tree,
            decorator_reference,
            decorator.lineno,
            owner_scope,
            frozenset(),
        ):
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr == "middleware":
                if call is None:
                    continue
                middleware_type = next(
                    (
                        keyword.value
                        for keyword in call.keywords
                        if keyword.arg == "middleware_type"
                    ),
                    call.args[0] if call is not None and call.args else None,
                )
                if not (
                    isinstance(middleware_type, ast.Constant)
                    and middleware_type.value == "http"
                ):
                    continue
            elif not (
                func.attr in ROUTE_METHODS
                or func.attr in GENERIC_ROUTE_DECORATORS
            ):
                continue
            if _is_visible_route_owner_reference(
                root,
                path,
                tree,
                func.value,
                func_line,
                func_scope,
                route_owner_names,
                framework_class_names,
                factory_names,
                paths_by_module,
                trees_by_path,
                factory_keys,
                frozenset(),
            ):
                return True
    return False


def _visible_route_decorator_references(
    path: Path,
    tree: ast.Module,
    reference: ast.expr,
    reference_line: int,
    owner_scope: tuple[int, ...],
    seen: frozenset[str],
) -> list[tuple[ast.expr, int, tuple[int, ...]]]:
    if isinstance(reference, ast.Attribute):
        return [(reference, reference_line, owner_scope)]
    if not isinstance(reference, ast.Name) or reference.id in seen:
        return []
    resolved: list[tuple[ast.expr, int, tuple[int, ...]]] = []
    for key, value, assignment_scope in _visible_assignment_candidates(
        path,
        tree,
        reference.id,
        reference_line,
        owner_scope,
    ):
        resolved.extend(
            _visible_route_decorator_references(
                path,
                tree,
                value,
                key[1],
                assignment_scope,
                seen | {reference.id},
            )
        )
    return resolved


def _visible_assignment_candidates(
    path: Path,
    tree: ast.Module,
    name: str,
    reference_line: int,
    owner_scope: tuple[int, ...],
) -> list[tuple[tuple[Path, int, str], ast.expr, tuple[int, ...]]]:
    assignments = [
        (key, value, scope)
        for key, value, scope, _ in _module_assignment_entries(path, tree)
        if key[2] == name
        and key[1] <= reference_line
        and len(scope) <= len(owner_scope)
        and owner_scope[: len(scope)] == scope
    ]
    if not assignments:
        return []
    deepest_scope = max(len(scope) for _, _, scope in assignments)
    assignments = [
        entry for entry in assignments if len(entry[2]) == deepest_scope
    ]
    conditional_lines = _conditional_assignment_lines(tree)
    unconditional = [
        entry for entry in assignments if entry[0][1] not in conditional_lines
    ]
    if not unconditional:
        return assignments
    latest_unconditional = max(unconditional, key=lambda entry: entry[0][1])
    return [
        latest_unconditional,
        *[
            entry
            for entry in assignments
            if entry[0][1] > latest_unconditional[0][1]
            and entry[0][1] in conditional_lines
        ],
    ]


def _conditional_assignment_lines(tree: ast.Module) -> frozenset[int]:
    lines: set[int] = set()

    class Collector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.conditional_depth = 0

        def visit_Assign(self, node: ast.Assign) -> None:
            if self.conditional_depth:
                lines.add(node.lineno)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if self.conditional_depth:
                lines.add(node.lineno)
            self.generic_visit(node)

        def visit_If(self, node: ast.If) -> None:
            self._visit_conditional(node)

        def visit_For(self, node: ast.For) -> None:
            self._visit_conditional(node)

        def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
            self._visit_conditional(node)

        def visit_While(self, node: ast.While) -> None:
            self._visit_conditional(node)

        def visit_Try(self, node: ast.Try) -> None:
            self._visit_conditional(node)

        def visit_TryStar(self, node: ast.TryStar) -> None:
            self._visit_conditional(node)

        def visit_Match(self, node: ast.Match) -> None:
            self._visit_conditional(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_scope(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_scope(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._visit_scope(node)

        def _visit_conditional(self, node: ast.AST) -> None:
            self.conditional_depth += 1
            self.generic_visit(node)
            self.conditional_depth -= 1

        def _visit_scope(
            self,
            node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            previous_depth = self.conditional_depth
            self.conditional_depth = 0
            for statement in node.body:
                self.visit(statement)
            self.conditional_depth = previous_depth

    Collector().visit(tree)
    return frozenset(lines)


def _is_visible_route_owner_reference(
    root: Path,
    path: Path,
    tree: ast.Module,
    reference: ast.AST,
    reference_line: int,
    owner_scope: tuple[int, ...],
    route_owner_names: frozenset[str],
    framework_class_names: set[str],
    factory_names: frozenset[str],
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    factory_keys: set[tuple[Path, str]],
    seen: frozenset[str],
) -> bool:
    if isinstance(reference, ast.Attribute) and reference.attr == "router":
        parts = _qualified_name_parts(reference)
        if parts and ".".join(parts) in route_owner_names:
            return True
        return _is_visible_route_owner_reference(
            root,
            path,
            tree,
            reference.value,
            reference_line,
            owner_scope,
            route_owner_names,
            framework_class_names,
            factory_names,
            paths_by_module,
            trees_by_path,
            factory_keys,
            seen,
        )
    parts = _qualified_name_parts(reference)
    if not parts:
        return False
    if len(parts) != 1:
        return _is_route_owner_reference(reference, route_owner_names)
    name = parts[0]
    if name in seen:
        return False
    assignments = _visible_assignment_candidates(
        path,
        tree,
        name,
        reference_line,
        owner_scope,
    )
    if not assignments:
        return _is_route_owner_reference(reference, route_owner_names)
    module_aliases = _framework_module_aliases(tree)
    for key, value, assignment_scope in assignments:
        if _is_framework_constructor(
            value,
            framework_class_names,
            module_aliases,
        ) or _is_framework_factory_call(value, factory_names) or (
            _resolved_framework_factory_call(
                root,
                path,
                tree,
                value,
                paths_by_module,
                trees_by_path,
                factory_keys,
            )
        ):
            return True
        if isinstance(value, (ast.Name, ast.Attribute)) and (
            _is_visible_route_owner_reference(
                root,
                path,
                tree,
                value,
                key[1],
                assignment_scope,
                route_owner_names,
                framework_class_names,
                factory_names,
                paths_by_module,
                trees_by_path,
                factory_keys,
                seen | {name},
            )
        ):
            return True
    return False


def _boundary_function_keys(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    programmatic_handlers: frozenset[tuple[Path, int, str]],
    service_paths: frozenset[Path],
    route_owner_names: dict[Path, frozenset[str]],
    mounted_route_owners: frozenset[tuple[Path, str]],
) -> tuple[
    frozenset[tuple[Path, int, str]],
    dict[tuple[Path, int, str], frozenset[str]],
]:
    trees_by_path = dict(parsed_modules)
    functions = _function_definitions(parsed_modules)
    scopes = _function_scopes(parsed_modules)
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    framework_class_names = _framework_class_names_by_path(
        root,
        parsed_modules,
        paths_by_module,
    )
    factory_names = {
        path: _framework_app_factory_names(
            tree,
            framework_class_names[path],
            _framework_module_aliases(tree),
        )
        for path, tree in parsed_modules
    }
    factory_keys = {
        (path, name)
        for path, names in factory_names.items()
        for name in names
    }
    boundary: set[tuple[Path, int, str]] = set(programmatic_handlers)
    partial_dependency_parameters: dict[
        tuple[Path, int, str], frozenset[str]
    ] = {}
    for key, node in functions.items():
        path = key[0]
        mounted_names = {
            name for owner_path, name in mounted_route_owners if owner_path == path
        }
        if _is_scoped_route_handler(
            root,
            path,
            trees_by_path[path],
            node,
            scopes.get(key, ()),
            route_owner_names.get(path, frozenset()),
            framework_class_names[path],
            factory_names[path],
            paths_by_module,
            trees_by_path,
            factory_keys,
        ) and (
            path in service_paths
            or _route_handler_uses_owner(node, mounted_names)
        ):
            boundary.add(key)

    direct_boundary = frozenset(boundary)

    for path, tree in parsed_modules:
        if path not in service_paths and not any(
            owner_path == path for owner_path, _ in mounted_route_owners
        ):
            continue
        mounted_names = {
            name for owner_path, name in mounted_route_owners if owner_path == path
        }
        route_owners = route_owner_names.get(path, frozenset())
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
                            classes,
                            class_scopes,
                            owner_key,
                            partial_parameter_filters=partial_dependency_parameters,
                        )
                    )
        for assignment in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
        ):
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else (assignment.target,)
            )
            if not any(
                _is_framework_dependency_override_target(
                    target,
                    path_in_services=path in service_paths,
                    mounted_names=mounted_names,
                    route_owners=route_owners,
                )
                for target in targets
            ):
                continue
            owner_key = _enclosing_function_key(path, assignment, functions, scopes)
            replacement = assignment.value
            target = _resolve_bound_method_key(
                root=root,
                path=path,
                tree=tree,
                reference=replacement,
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
                reference=replacement,
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
                replacement,
                paths_by_module,
                trees_by_path,
                functions,
                scopes,
                owner_key,
            ) or _resolve_dependency_class_init_key(
                root=root,
                path=path,
                tree=tree,
                reference=replacement,
                paths_by_module=paths_by_module,
                trees_by_path=trees_by_path,
                classes=classes,
                class_scopes=class_scopes,
                function_scopes=scopes,
                owner_key=owner_key,
            )
            if target is not None:
                boundary.add(target)

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
            classes,
            class_scopes,
            key,
            partial_parameter_filters=partial_dependency_parameters,
        )
        for target in discovered - boundary:
            boundary.add(target)
            pending.append(target)
    return frozenset(boundary), {
        key: included
        for key, included in partial_dependency_parameters.items()
        if key in boundary and key not in direct_boundary
    }


def _request_body_flow_names(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    boundary_functions: frozenset[tuple[Path, int, str]],
    request_type_names: dict[Path, frozenset[str]],
    websocket_type_names: dict[Path, frozenset[str]],
    route_owner_names: dict[Path, frozenset[str]],
    programmatic_positional_request_handlers: frozenset[tuple[Path, int, str]],
) -> dict[
    tuple[Path, int, str],
    tuple[frozenset[str], frozenset[str]],
]:
    trees_by_path = dict(parsed_modules)
    functions = _function_definitions(parsed_modules)
    scopes = _function_scopes(parsed_modules)
    classes = _class_definitions(parsed_modules)
    class_scopes = _class_scopes(parsed_modules)
    flows: dict[tuple[Path, int, str], tuple[set[str], set[str]]] = {}
    for key in boundary_functions:
        function = functions.get(key)
        if function is None:
            continue
        path = key[0]
        request_names = {
            argument.arg
            for argument in _function_arguments(function)
            if (
                argument.annotation is not None
                and _annotation_contains_request_type(
                    argument.annotation,
                    request_type_names[path],
                    _request_module_aliases(trees_by_path[path]),
                )
            )
            or (argument.annotation is None and argument.arg == "request")
        }
        request_names.update(
            _framework_positional_request_names(
                function,
                route_owner_names.get(path, frozenset()),
                registered=key in programmatic_positional_request_handlers,
            )
        )
        websocket_names = {
            argument.arg
            for argument in _function_arguments(function)
            if (
                argument.annotation is not None
                and _annotation_contains_websocket_type(
                    argument.annotation,
                    websocket_type_names[path],
                    _websocket_module_aliases(trees_by_path[path]),
                )
            )
            or (
                argument.annotation is None
                and argument.arg in {"socket", "websocket"}
            )
        }
        if request_names or websocket_names:
            flows[key] = (request_names, websocket_names)

    changed = True
    while changed:
        changed = False
        for key, (request_names, websocket_names) in list(flows.items()):
            function = functions[key]
            path = key[0]
            for call in (
                candidate
                for statement in function.body
                for candidate in ast.walk(statement)
                if isinstance(candidate, ast.Call)
            ):
                request_aliases = _local_name_aliases(
                    function,
                    request_names,
                    before_line=call.lineno,
                )
                websocket_aliases = _local_name_aliases(
                    function,
                    websocket_names,
                    before_line=call.lineno,
                )
                target = _resolve_bound_method_key(
                    root=root,
                    path=path,
                    tree=trees_by_path[path],
                    reference=call.func,
                    paths_by_module=paths_by_module,
                    trees_by_path=trees_by_path,
                    classes=classes,
                    class_scopes=class_scopes,
                    function_scopes=scopes,
                    owner_key=key,
                ) or _resolve_function_key(
                    root,
                    path,
                    trees_by_path[path],
                    call.func,
                    paths_by_module,
                    trees_by_path,
                    functions,
                    scopes,
                    key,
                )
                target_function = functions.get(target) if target is not None else None
                if target is None or target_function is None:
                    continue
                parameters = _function_arguments(target_function)
                if (
                    isinstance(call.func, ast.Attribute)
                    and parameters
                    and parameters[0].arg in {"self", "cls"}
                ):
                    parameters = parameters[1:]
                parameters_by_name = {
                    parameter.arg: parameter for parameter in parameters
                }
                propagated_requests: set[str] = set()
                propagated_websockets: set[str] = set()
                for parameter, argument in zip(
                    parameters,
                    call.args,
                    strict=False,
                ):
                    if _is_name_reference(argument, request_aliases):
                        propagated_requests.add(parameter.arg)
                    if _is_name_reference(argument, websocket_aliases):
                        propagated_websockets.add(parameter.arg)
                for keyword in call.keywords:
                    parameter = parameters_by_name.get(keyword.arg or "")
                    if parameter is None:
                        continue
                    if _is_name_reference(keyword.value, request_aliases):
                        propagated_requests.add(parameter.arg)
                    if _is_name_reference(keyword.value, websocket_aliases):
                        propagated_websockets.add(parameter.arg)
                if not propagated_requests and not propagated_websockets:
                    continue
                target_flow = flows.setdefault(target, (set(), set()))
                before = (len(target_flow[0]), len(target_flow[1]))
                target_flow[0].update(propagated_requests)
                target_flow[1].update(propagated_websockets)
                changed = changed or before != (
                    len(target_flow[0]),
                    len(target_flow[1]),
                )
    return {
        key: (frozenset(requests), frozenset(websockets))
        for key, (requests, websockets) in flows.items()
    }


def _local_name_aliases(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    initial: set[str],
    *,
    before_line: int,
) -> set[str]:
    aliases = set(initial)
    conditional_lines = _conditional_assignment_lines(
        ast.Module(body=function.body, type_ignores=[])
    )
    assignments = sorted(
        [
        candidate
        for statement in function.body
        for candidate in ast.walk(statement)
        if isinstance(candidate, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        and candidate.lineno < before_line
        ],
        key=lambda candidate: candidate.lineno,
    )
    for assignment in assignments:
        targets, value = _assignment_targets_and_value(assignment)
        is_alias = _is_name_reference(value, aliases)
        if assignment.lineno not in conditional_lines:
            aliases.difference_update(targets)
        if is_alias:
            aliases.update(targets)
    return aliases


def _is_name_reference(node: ast.AST, names: set[str]) -> bool:
    while isinstance(node, ast.Await):
        node = node.value
    return isinstance(node, ast.Name) and node.id in names


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
    if _is_starlette_route_constructor(
        call,
        _starlette_route_constructor_aliases(tree),
        _starlette_route_module_aliases(tree),
    ):
        return True
    if not isinstance(call.func, ast.Attribute):
        return False
    return call.func.attr in (
        ROUTE_METHODS
        | GENERIC_ROUTE_DECORATORS
        | PROGRAMMATIC_ROUTE_REGISTRARS
        | {"include_router"}
    ) and _is_route_owner_reference(call.func.value, route_owner_names)


def _is_framework_dependency_override_target(
    node: ast.AST,
    *,
    path_in_services: bool,
    mounted_names: set[str],
    route_owners: frozenset[str],
) -> bool:
    return bool(
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "dependency_overrides"
        and _active_route_owner_reference(
            node.value.value,
            path_in_services=path_in_services,
            mounted_names=mounted_names,
            route_owners=route_owners,
        )
    )


def _dependency_targets(
    root: Path,
    path: Path,
    tree: ast.Module,
    nodes: object,
    paths_by_module: dict[str, Path],
    trees_by_path: dict[Path, ast.Module],
    functions: dict[tuple[Path, int, str], ast.FunctionDef | ast.AsyncFunctionDef],
    scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    classes: dict[tuple[Path, int, str], ast.ClassDef],
    class_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    owner_key: tuple[Path, int, str] | None,
    seen_imported_bindings: frozenset[tuple[Path, str]] = frozenset(),
    partial_parameter_filters: dict[
        tuple[Path, int, str], frozenset[str]
    ] | None = None,
) -> set[tuple[Path, int, str]]:
    depends_aliases, fastapi_module_aliases = _depends_aliases(tree)
    targets: set[tuple[Path, int, str]] = set()
    root_nodes = nodes if isinstance(nodes, (list, tuple)) else (nodes,)
    expanded_nodes = _expanded_dependency_nodes(
        path,
        tree,
        root_nodes,
        scopes,
        owner_key,
    )
    symbol_targets, _ = _import_targets(
        root,
        path,
        tree,
        paths_by_module,
    )
    for root_node in expanded_nodes:
        for candidate in ast.walk(root_node):
            if not isinstance(candidate, ast.Name):
                continue
            binding = symbol_targets.get(candidate.id)
            if binding is None:
                continue
            binding = _follow_symbol_reexports(
                root,
                binding,
                paths_by_module,
                trees_by_path,
            )
            if binding in seen_imported_bindings:
                continue
            target_path, target_name = binding
            target_tree = trees_by_path.get(target_path)
            if target_tree is None:
                continue
            assignments = [
                (key, value)
                for key, value, scope, _ in _module_assignment_entries(
                    target_path,
                    target_tree,
                )
                if not scope and key[2] == target_name
            ]
            if not assignments:
                continue
            _, value = max(assignments, key=lambda entry: entry[0][1])
            targets.update(
                _dependency_targets(
                    root,
                    target_path,
                    target_tree,
                    (value,),
                    paths_by_module,
                    trees_by_path,
                    functions,
                    scopes,
                    classes,
                    class_scopes,
                    None,
                    seen_imported_bindings | {binding},
                    partial_parameter_filters=partial_parameter_filters,
                )
            )
    for root_node in expanded_nodes:
        if not isinstance(root_node, ast.AST):
            continue
        inferred_dependencies = _inferred_annotated_dependencies(
            root_node,
            tree,
            depends_aliases,
            fastapi_module_aliases,
        )
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
                dependency = inferred_dependencies.get(id(candidate))
            if dependency is None:
                continue
            partial_handler = _resolve_partial_route_handler(
                root=root,
                path=path,
                tree=tree,
                endpoint=dependency,
                paths_by_module=paths_by_module,
                trees_by_path=trees_by_path,
                functions=functions,
                scopes=scopes,
                classes=classes,
                class_scopes=class_scopes,
                owner_key=owner_key,
            )
            if partial_handler is not None and partial_parameter_filters is not None:
                partial_target, remaining_parameters = partial_handler
                previous = partial_parameter_filters.get(partial_target)
                partial_parameter_filters[partial_target] = (
                    remaining_parameters
                    if previous is None
                    else previous | remaining_parameters
                )
            target = partial_handler[0] if partial_handler is not None else None
            target = target or _resolve_bound_method_key(
                root=root,
                path=path,
                tree=tree,
                reference=dependency,
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
                reference=dependency,
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
                dependency,
                paths_by_module,
                trees_by_path,
                functions,
                scopes,
                owner_key,
            ) or _resolve_dependency_class_init_key(
                root=root,
                path=path,
                tree=tree,
                reference=dependency,
                paths_by_module=paths_by_module,
                trees_by_path=trees_by_path,
                classes=classes,
                class_scopes=class_scopes,
                function_scopes=scopes,
                owner_key=owner_key,
            )
            if target is not None:
                targets.add(target)
    return targets


def _inferred_annotated_dependencies(
    root_node: ast.AST,
    tree: ast.Module,
    depends_aliases: set[str],
    fastapi_module_aliases: set[str],
) -> dict[int, ast.expr]:
    annotated_aliases = _typing_symbol_aliases(tree, "Annotated")
    typing_module_aliases = _typing_module_aliases(tree)
    inferred: dict[int, ast.expr] = {}
    for annotation in ast.walk(root_node):
        if not isinstance(annotation, ast.Subscript):
            continue
        if isinstance(annotation.value, ast.Name):
            is_annotated = annotation.value.id in annotated_aliases
        else:
            parts = _qualified_name_parts(annotation.value)
            is_annotated = bool(
                parts
                and len(parts) >= 2
                and parts[-1] == "Annotated"
                and parts[0] in typing_module_aliases
            )
        if not is_annotated:
            continue
        elements = (
            list(annotation.slice.elts)
            if isinstance(annotation.slice, ast.Tuple)
            else [annotation.slice]
        )
        if len(elements) < 2:
            continue
        dependency_type = elements[0]
        for metadata in elements[1:]:
            for candidate in ast.walk(metadata):
                if not (
                    isinstance(candidate, ast.Call)
                    and _is_depends_call(
                        candidate,
                        depends_aliases,
                        fastapi_module_aliases,
                    )
                ):
                    continue
                explicit = bool(candidate.args) or any(
                    keyword.arg == "dependency"
                    for keyword in candidate.keywords
                )
                if not explicit:
                    inferred[id(candidate)] = dependency_type
    return inferred


def _resolve_dependency_class_init_key(
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
    class_key = _resolve_model_key(
        root,
        path,
        tree,
        reference,
        paths_by_module,
        trees_by_path,
        classes,
        class_scopes,
        owner_key,
        owner_scope=function_scopes.get(owner_key, ()),
        reference_line=reference.lineno,
    )
    if class_key is None:
        return None
    return _resolve_class_method_key(
        root=root,
        class_key=class_key,
        method_name="__init__",
        paths_by_module=paths_by_module,
        trees_by_path=trees_by_path,
        classes=classes,
        class_scopes=class_scopes,
    )


def _expanded_dependency_nodes(
    path: Path,
    tree: ast.Module,
    nodes: object,
    function_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    owner_key: tuple[Path, int, str] | None,
) -> list[ast.AST]:
    owner_scope = function_scopes.get(owner_key, ())
    assignments = _module_assignment_entries(path, tree)
    pending = [
        node
        for node in (nodes if isinstance(nodes, (list, tuple)) else (nodes,))
        if isinstance(node, ast.AST)
    ]
    expanded: list[ast.AST] = []
    seen_assignments: set[tuple[Path, int, str]] = set()
    while pending:
        root_node = pending.pop()
        expanded.append(root_node)
        for candidate in ast.walk(root_node):
            if not isinstance(candidate, ast.Name):
                continue
            visible = [
                (key, value, scope)
                for key, value, scope, _ in assignments
                if key[2] == candidate.id
                and key[1] <= candidate.lineno
                and len(scope) <= len(owner_scope)
                and owner_scope[: len(scope)] == scope
            ]
            if not visible:
                continue
            key, value, _ = max(
                visible,
                key=lambda entry: (len(entry[2]), entry[0][1]),
            )
            if key in seen_assignments:
                continue
            seen_assignments.add(key)
            pending.append(value)
    return expanded


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
                if imported.name in {"Body", "File", "Form"}
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
        and parts[-1] in {"Body", "File", "Form"}
        and parts[0] in module_aliases
    )


def _explicit_body_parameter_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> set[str]:
    defaults = _parameter_defaults(node)
    names: set[str] = set()
    for argument in _function_arguments(node):
        roots = [argument.annotation, defaults.get(id(argument))]
        if any(
            isinstance(candidate, ast.Call)
            and _is_body_call(
                candidate,
                set(direct_aliases),
                set(module_aliases),
            )
            for root in roots
            if root is not None
            for candidate in ast.walk(root)
        ):
            names.add(argument.arg)
    return names


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
    functional_dataclasses = _functional_dataclass_definitions(parsed_modules)
    created_pydantic_models = _pydantic_create_model_definitions(parsed_modules)
    model_definitions: dict[tuple[Path, int, str], ast.AST] = {
        **classes,
        **functional_typed_dicts,
        **functional_dataclasses,
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
            if key in functional_dataclasses
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
    functional_dataclasses = _functional_dataclass_definitions(parsed_modules)
    created_pydantic_models = _pydantic_create_model_definitions(parsed_modules)
    model_definitions: dict[tuple[Path, int, str], ast.AST] = {
        **classes,
        **functional_typed_dicts,
        **functional_dataclasses,
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
            if key in functional_dataclasses
        },
        **{
            key: scope
            for path, tree in parsed_modules
            for key, _, scope, _ in _module_assignment_entries(path, tree)
            if key in created_pydantic_models
        },
    }
    trees_by_path = dict(parsed_modules)
    imported_values = _imported_module_values_by_path(
        root,
        parsed_modules,
        paths_by_module,
    )
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
            reference_roots = _functional_typed_dict_type_roots(
                model,
                trees_by_path[path],
                imported_values[path],
            )
        elif key in functional_dataclasses and isinstance(model, ast.Call):
            reference_roots = _functional_dataclass_type_roots(
                model,
                trees_by_path[path],
            )
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
                if target is None and isinstance(candidate, ast.Name):
                    matching = [
                        model_key
                        for model_key in model_keys
                        if model_key[2] == candidate.id
                    ]
                    if len(matching) == 1:
                        target = matching[0]
                if target in model_keys and target not in closure:
                    closure.add(target)
                    pending.append(target)
    return frozenset(closure)


def _function_arguments(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
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
    body_aliases: frozenset[str],
    body_module_aliases: frozenset[str],
    wire_key_constants: dict[str, str],
    raw_mapping_helpers: dict[
        str,
        tuple[tuple[str, ...], frozenset[str]],
    ],
    initial_request_names: frozenset[str] = frozenset(),
    initial_websocket_names: frozenset[str] = frozenset(),
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
    request_names.update(initial_request_names)
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
    websocket_names.update(initial_websocket_names)
    explicit_body_names = _explicit_body_parameter_names(
        node,
        body_aliases,
        body_module_aliases,
    )
    if not request_names and not websocket_names and not explicit_body_names:
        return []

    request_aliases = set(request_names)
    websocket_aliases = set(websocket_names)
    raw_mapping_names: set[str] = set()
    if node.name == "on_receive":
        parameters = [
            argument
            for argument in arguments
            if argument.arg not in {"self", "cls"}
        ]
        for index, parameter in enumerate(parameters[:-1]):
            if parameter.arg in websocket_names:
                raw_mapping_names.add(parameters[index + 1].arg)
    request_body_names: set[str] = set(explicit_body_names)
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
            if _call_returns_raw_mapping(
                value,
                request_aliases,
                websocket_aliases,
                raw_mapping_names,
                request_body_names,
                websocket_text_names,
                json_loads_names,
                json_module_names,
                raw_mapping_helpers,
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
                and candidate.func.attr in {"get", "getlist", "pop", "setdefault"}
                and candidate.args
            ):
                key = candidate.args[0]
                container = candidate.func.value
            else:
                continue
            wire_key = _constant_string_value(key, wire_key_constants)
            if (
                wire_key is not None
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
                wire_names.append((wire_key, key.lineno))
    return wire_names


def _imported_module_values_by_path(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> dict[Path, dict[str, ast.expr]]:
    trees_by_path = dict(parsed_modules)
    module_values: dict[Path, dict[str, tuple[int, ast.expr]]] = {
        path: {} for path, _ in parsed_modules
    }
    for path, tree in parsed_modules:
        for key, value, scope, _ in _module_assignment_entries(path, tree):
            if not scope:
                module_values[path][key[2]] = (key[1], value)

    imported_values: dict[Path, dict[str, ast.expr]] = {
        path: {} for path, _ in parsed_modules
    }
    changed = True
    while changed:
        changed = False
        for path, tree in parsed_modules:
            symbol_targets, _ = _import_targets(
                root,
                path,
                tree,
                paths_by_module,
            )
            for local_name, (target_path, target_name) in symbol_targets.items():
                entry = module_values.get(target_path, {}).get(target_name)
                value = (
                    _resolve_module_expression(
                        entry[1],
                        trees_by_path[target_path],
                        before_line=entry[0],
                        imported_values=imported_values[target_path],
                    )
                    if entry is not None
                    else imported_values.get(target_path, {}).get(target_name)
                )
                if value is None or local_name in imported_values[path]:
                    continue
                imported_values[path][local_name] = value
                changed = True
    return imported_values


def _request_type_names_by_path(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> dict[Path, frozenset[str]]:
    trees_by_path = dict(parsed_modules)
    names = {
        path: set(_request_type_aliases(tree))
        for path, tree in parsed_modules
    }
    changed = True
    while changed:
        changed = False
        type_keys = {
            (path, name)
            for path, path_names in names.items()
            for name in path_names
            if "." not in name
        }
        for path, tree in parsed_modules:
            changed = _propagate_local_type_assignment_aliases(
                path,
                tree,
                names[path],
            ) or changed
            symbol_targets, module_targets = _import_targets(
                root,
                path,
                tree,
                paths_by_module,
            )
            for local_name, target in symbol_targets.items():
                resolved = _follow_symbol_reexports(
                    root,
                    target,
                    paths_by_module,
                    trees_by_path,
                )
                if resolved in type_keys and local_name not in names[path]:
                    names[path].add(local_name)
                    changed = True
            for local_parts, target_path in module_targets.items():
                for target_name in names.get(target_path, set()):
                    if "." in target_name:
                        continue
                    qualified = ".".join((*local_parts, target_name))
                    if qualified not in names[path]:
                        names[path].add(qualified)
                        changed = True
    return {path: frozenset(path_names) for path, path_names in names.items()}


def _propagate_local_type_assignment_aliases(
    path: Path,
    tree: ast.Module,
    names: set[str],
) -> bool:
    changed = False
    for key, value, scope, _ in _module_assignment_entries(path, tree):
        if scope:
            continue
        parts = _qualified_name_parts(value)
        if not parts or ".".join(parts) not in names or key[2] in names:
            continue
        names.add(key[2])
        changed = True
    return changed


def _websocket_type_names_by_path(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> dict[Path, frozenset[str]]:
    trees_by_path = dict(parsed_modules)
    names = {
        path: set(_websocket_type_aliases(tree))
        for path, tree in parsed_modules
    }
    changed = True
    while changed:
        changed = False
        type_keys = {
            (path, name)
            for path, path_names in names.items()
            for name in path_names
            if "." not in name
        }
        for path, tree in parsed_modules:
            changed = _propagate_local_type_assignment_aliases(
                path,
                tree,
                names[path],
            ) or changed
            symbol_targets, module_targets = _import_targets(
                root,
                path,
                tree,
                paths_by_module,
            )
            for local_name, target in symbol_targets.items():
                resolved = _follow_symbol_reexports(
                    root,
                    target,
                    paths_by_module,
                    trees_by_path,
                )
                if resolved in type_keys and local_name not in names[path]:
                    names[path].add(local_name)
                    changed = True
            for local_parts, target_path in module_targets.items():
                for target_name in names.get(target_path, set()):
                    if "." in target_name:
                        continue
                    qualified = ".".join((*local_parts, target_name))
                    if qualified not in names[path]:
                        names[path].add(qualified)
                        changed = True
    return {path: frozenset(path_names) for path, path_names in names.items()}


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
        if isinstance(node, ast.ImportFrom)
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


def _is_raw_body_decoder_call(
    node: ast.Call,
    direct_aliases: frozenset[str],
    module_aliases: frozenset[str],
) -> bool:
    if _is_json_loads_call(node, direct_aliases, module_aliases):
        return True
    parts = _qualified_name_parts(node.func)
    return bool(
        parts
        and parts[-1] in {"loads", "parse_qs", "parse_qsl"}
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
        target_name
        for target in target_nodes
        for target_name in _assignment_target_names(target)
    }
    return targets, node.value


def _assignment_target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        parts = _qualified_name_parts(node)
        return {".".join(parts)} if parts else set()
    if isinstance(node, ast.Starred):
        return _assignment_target_names(node.value)
    if isinstance(node, (ast.List, ast.Tuple)):
        return {
            name
            for element in node.elts
            for name in _assignment_target_names(element)
        }
    return set()


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
        and node.attr
        in {"cookies", "headers", "path_params", "query_params", "scope"}
        and isinstance(node.value, ast.Name)
        and node.value.id in request_aliases | websocket_aliases
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
    if _is_raw_body_decoder_call(node, json_loads_names, json_module_names):
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
    parts = _qualified_name_parts(node.func) or []
    is_mapping_wrapper = bool(
        parts
        and parts[-1]
        in {"Headers", "QueryParams", "FormData", "MultiDict", "ImmutableMultiDict"}
    )
    return (_name(node.func) == "dict" or is_mapping_wrapper) and any(
        _is_raw_request_mapping(
            value,
            request_aliases,
            websocket_aliases,
            raw_mapping_names,
            request_body_names,
            websocket_text_names,
            json_loads_names,
            json_module_names,
        )
        for value in [
            *node.args,
            *(keyword.value for keyword in node.keywords),
        ]
    )


def _raw_mapping_helper_summaries_by_path(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
) -> dict[Path, dict[str, tuple[tuple[str, ...], frozenset[str]]]]:
    trees_by_path = dict(parsed_modules)
    functions = _function_definitions(parsed_modules)
    function_scopes = _function_scopes(parsed_modules)
    summaries: dict[tuple[Path, int, str], frozenset[str]] = {}
    changed = True
    while changed:
        changed = False
        visible = _visible_raw_mapping_helper_summaries(
            root,
            parsed_modules,
            paths_by_module,
            functions,
            function_scopes,
            summaries,
        )
        for key, function in functions.items():
            path = key[0]
            raw_parameters = _raw_mapping_return_parameters(
                function,
                visible[path],
                _json_loads_aliases(trees_by_path[path]),
                _json_module_aliases(trees_by_path[path]),
            )
            if summaries.get(key) != raw_parameters:
                summaries[key] = raw_parameters
                changed = True
    return _visible_raw_mapping_helper_summaries(
        root,
        parsed_modules,
        paths_by_module,
        functions,
        function_scopes,
        summaries,
    )


def _visible_raw_mapping_helper_summaries(
    root: Path,
    parsed_modules: list[tuple[Path, ast.Module]],
    paths_by_module: dict[str, Path],
    functions: dict[tuple[Path, int, str], ast.FunctionDef | ast.AsyncFunctionDef],
    function_scopes: dict[tuple[Path, int, str], tuple[int, ...]],
    summaries: dict[tuple[Path, int, str], frozenset[str]],
) -> dict[Path, dict[str, tuple[tuple[str, ...], frozenset[str]]]]:
    trees_by_path = dict(parsed_modules)
    visible: dict[Path, dict[str, tuple[tuple[str, ...], frozenset[str]]]] = {
        path: {} for path, _ in parsed_modules
    }

    def add(path: Path, name: str, key: tuple[Path, int, str]) -> None:
        raw_parameters = summaries.get(key)
        if not raw_parameters:
            return
        visible[path][name] = (
            tuple(argument.arg for argument in _function_arguments(functions[key])),
            raw_parameters,
        )

    for key in sorted(functions):
        add(key[0], key[2], key)
    for path, tree in parsed_modules:
        symbol_targets, module_targets = _import_targets(
            root,
            path,
            tree,
            paths_by_module,
        )
        for local_name, target in symbol_targets.items():
            target_path, target_name = _follow_symbol_reexports(
                root,
                target,
                paths_by_module,
                trees_by_path,
            )
            candidates = [
                key
                for key in functions
                if key[0] == target_path
                and key[2] == target_name
                and not function_scopes[key]
            ]
            if candidates:
                add(path, local_name, min(candidates, key=lambda key: key[1]))
        for local_parts, target_path in module_targets.items():
            for key in sorted(functions):
                if key[0] == target_path and not function_scopes[key]:
                    add(path, ".".join((*local_parts, key[2])), key)
    changed = True
    while changed:
        changed = False
        for path, tree in parsed_modules:
            for key, value, _, _ in _module_assignment_entries(path, tree):
                parts = _qualified_name_parts(value)
                source = visible[path].get(".".join(parts)) if parts else None
                if source is not None and visible[path].get(key[2]) != source:
                    visible[path][key[2]] = source
                    changed = True
    return visible


def _raw_mapping_return_parameters(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    raw_mapping_helpers: dict[str, tuple[tuple[str, ...], frozenset[str]]],
    json_loads_names: frozenset[str],
    json_module_names: frozenset[str],
) -> frozenset[str]:
    raw_parameters: set[str] = set()
    assignments = [
        candidate
        for statement in function.body
        for candidate in ast.walk(statement)
        if isinstance(candidate, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    returns = [
        candidate
        for candidate in _lexical_body_nodes(function.body)
        if isinstance(candidate, ast.Return) and candidate.value is not None
    ]
    for source in _function_arguments(function):
        request_aliases = {source.arg}
        websocket_aliases = {source.arg}
        raw_mapping_names: set[str] = set()
        request_body_names: set[str] = set()
        websocket_text_names: set[str] = set()
        changed = True
        while changed:
            changed = False
            for assignment in assignments:
                targets, value = _assignment_targets_and_value(assignment)
                if _is_name_reference(value, request_aliases):
                    before = len(request_aliases)
                    request_aliases.update(targets)
                    changed = changed or len(request_aliases) != before
                if _is_name_reference(value, websocket_aliases):
                    before = len(websocket_aliases)
                    websocket_aliases.update(targets)
                    changed = changed or len(websocket_aliases) != before
                if _is_request_body_value(value, request_aliases, request_body_names):
                    before = len(request_body_names)
                    request_body_names.update(targets)
                    changed = changed or len(request_body_names) != before
                if _is_websocket_text_value(
                    value,
                    websocket_aliases,
                    websocket_text_names,
                ):
                    before = len(websocket_text_names)
                    websocket_text_names.update(targets)
                    changed = changed or len(websocket_text_names) != before
                if _is_raw_request_mapping(
                    value,
                    request_aliases,
                    websocket_aliases,
                    raw_mapping_names,
                    request_body_names,
                    websocket_text_names,
                    json_loads_names,
                    json_module_names,
                ) or _call_returns_raw_mapping(
                    value,
                    request_aliases,
                    websocket_aliases,
                    raw_mapping_names,
                    request_body_names,
                    websocket_text_names,
                    json_loads_names,
                    json_module_names,
                    raw_mapping_helpers,
                ):
                    before = len(raw_mapping_names)
                    raw_mapping_names.update(targets)
                    changed = changed or len(raw_mapping_names) != before
        if any(
            _is_raw_request_mapping(
                returned.value,
                request_aliases,
                websocket_aliases,
                raw_mapping_names,
                request_body_names,
                websocket_text_names,
                json_loads_names,
                json_module_names,
            )
            or _call_returns_raw_mapping(
                returned.value,
                request_aliases,
                websocket_aliases,
                raw_mapping_names,
                request_body_names,
                websocket_text_names,
                json_loads_names,
                json_module_names,
                raw_mapping_helpers,
            )
            for returned in returns
        ):
            raw_parameters.add(source.arg)
    return frozenset(raw_parameters)


def _call_returns_raw_mapping(
    node: ast.AST,
    request_aliases: set[str],
    websocket_aliases: set[str],
    raw_mapping_names: set[str],
    request_body_names: set[str],
    websocket_text_names: set[str],
    json_loads_names: frozenset[str],
    json_module_names: frozenset[str],
    raw_mapping_helpers: dict[str, tuple[tuple[str, ...], frozenset[str]]],
) -> bool:
    while isinstance(node, ast.Await):
        node = node.value
    if not isinstance(node, ast.Call):
        return False
    parts = _qualified_name_parts(node.func)
    if not parts:
        return False
    summary = raw_mapping_helpers.get(".".join(parts))
    if summary is None:
        summary = raw_mapping_helpers.get(parts[-1])
    if summary is None:
        return False
    parameters, raw_parameters = summary

    def is_raw(argument: ast.AST) -> bool:
        return _is_name_reference(
            argument,
            request_aliases | websocket_aliases,
        ) or _is_raw_request_mapping(
            argument,
            request_aliases,
            websocket_aliases,
            raw_mapping_names,
            request_body_names,
            websocket_text_names,
            json_loads_names,
            json_module_names,
        )

    if any(
        parameter in raw_parameters and is_raw(argument)
        for parameter, argument in _bound_call_parameter_arguments(node, parameters)
    ):
        return True
    return any(
        keyword.arg in raw_parameters and is_raw(keyword.value)
        for keyword in node.keywords
        if keyword.arg is not None
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
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return any(
            _is_request_body_value(
                generator.iter,
                request_aliases,
                request_body_names,
            )
            for generator in node.generators
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
    ):
        return any(
            _is_request_body_value(
                argument,
                request_aliases,
                request_body_names,
            )
            for argument in node.args
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"decode", "read", "strip", "lstrip", "rstrip"}
    ):
        if _is_request_body_value(
            node.func.value,
            request_aliases,
            request_body_names,
        ):
            return True
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"body", "stream"}
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
    if isinstance(node, ast.Subscript):
        return _is_websocket_text_value(
            node.value,
            websocket_aliases,
            websocket_text_names,
        )
    return bool(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"receive", "receive_bytes", "receive_text"}
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


def _has_unapproved_route_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    route_owner_names: frozenset[str],
) -> bool:
    route_registration_seen = False
    for decorator in node.decorator_list:
        if _is_http_middleware_decorator(decorator, route_owner_names):
            route_registration_seen = True
            continue
        call = decorator if isinstance(decorator, ast.Call) else None
        func = call.func if call is not None else decorator
        if (
            isinstance(func, ast.Attribute)
            and func.attr in ROUTE_METHODS | GENERIC_ROUTE_DECORATORS
            and _is_route_owner_reference(func.value, route_owner_names)
        ):
            route_registration_seen = True
            continue
        if route_registration_seen:
            return True
    if route_registration_seen:
        return False
    return bool(node.decorator_list)


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


def _framework_positional_request_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    owner_names: frozenset[str],
    *,
    registered: bool = False,
) -> frozenset[str]:
    is_positional_request_callback = registered
    for decorator in node.decorator_list:
        if _is_http_middleware_decorator(decorator, owner_names):
            is_positional_request_callback = True
            break
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "exception_handler"
            and _is_route_owner_reference(decorator.func.value, owner_names)
        ):
            is_positional_request_callback = True
            break
    if not is_positional_request_callback:
        return frozenset()
    positional = [*node.args.posonlyargs, *node.args.args]
    if positional and positional[0].arg in {"self", "cls"}:
        positional = positional[1:]
    return frozenset({positional[0].arg}) if positional else frozenset()


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
