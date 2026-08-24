#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
APP_ROOT="$REPO_ROOT/apps/atlas_flutter"
readonly SCRIPT_DIR REPO_ROOT APP_ROOT

if [[ ! -f "$REPO_ROOT/.github/workflows/atlasvault-cross-platform-security.yml" ]]; then
  printf 'The AtlasVault pull-request workflow is missing.\n' >&2
  exit 1
fi

cd "$APP_ROOT"
flutter pub get
dart format --output=none --set-exit-if-changed lib test integration_test
flutter analyze

shopt -s nullglob
focused=(test/atlas_vault_*_test.dart test/atlas_search_controller_test.dart)
if (( ${#focused[@]} == 0 )); then
  printf 'No focused AtlasVault Flutter tests were found.\n' >&2
  exit 1
fi
flutter test "${focused[@]}"

full_tests=()
while IFS= read -r test_path; do
  full_tests+=("$test_path")
done < <(
  find test -type f -name '*_test.dart' \
    ! -name 'tab_golden_test.dart' \
    -print | sort
)
if (( ${#full_tests[@]} == 0 )); then
  printf 'No full Flutter test set was found.\n' >&2
  exit 1
fi
flutter test "${full_tests[@]}"
flutter build apk --debug

python3 - <<'PY'
import re
from pathlib import Path


def _mask_dart_non_code(source: str) -> str:
    masked = list(source)
    length = len(source)

    def blank(start: int, end: int) -> None:
        for offset in range(start, end):
            if masked[offset] != "\n":
                masked[offset] = " "

    def string_at(index: int) -> tuple[bool, int, str] | None:
        quote_index = index
        raw = False
        if (
            source[index] in "rR"
            and index + 1 < length
            and source[index + 1] in "'\""
            and (
                index == 0
                or not (
                    source[index - 1].isalnum()
                    or source[index - 1] in "_$"
                )
            )
        ):
            raw = True
            quote_index += 1
        if source[quote_index] not in "'\"":
            return None
        quote = source[quote_index]
        delimiter = (
            quote * 3 if source.startswith(quote * 3, quote_index) else quote
        )
        return raw, quote_index, delimiter

    def mask_comment(index: int) -> int | None:
        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            end = length if end < 0 else end
            blank(index, end)
            return end
        if source.startswith("/*", index):
            start = index
            index += 2
            depth = 1
            while index < length and depth:
                if source.startswith("/*", index):
                    depth += 1
                    index += 2
                elif source.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            blank(start, index)
            return index
        return None

    def _mask_dart_string(
        start: int,
        *,
        raw: bool,
        quote_index: int,
        delimiter: str,
    ) -> int:
        blank(start, quote_index + len(delimiter))
        index = quote_index + len(delimiter)
        while index < length:
            if source.startswith(delimiter, index):
                blank(index, index + len(delimiter))
                index += len(delimiter)
                return index
            if not raw and source[index] == "\\":
                end = min(index + 2, length)
                blank(index, end)
                index = end
                continue
            if not raw and source.startswith("${", index):
                blank(index, index + 1)
                index = scan_interpolation(index + 2)
                continue
            if (
                not raw
                and source[index] == "$"
                and index + 1 < length
                and (source[index + 1].isalpha() or source[index + 1] == "_")
            ):
                end = index + 2
                while end < length and (
                    source[end].isalnum() or source[end] in "_$"
                ):
                    end += 1
                blank(index, end)
                index = end
                continue
            blank(index, index + 1)
            index += 1
        return index

    def scan_interpolation(index: int) -> int:
        depth = 1
        while index < length:
            comment_end = mask_comment(index)
            if comment_end is not None:
                index = comment_end
                continue
            string = string_at(index)
            if string is not None:
                raw, quote_index, delimiter = string
                index = _mask_dart_string(
                    index,
                    raw=raw,
                    quote_index=quote_index,
                    delimiter=delimiter,
                )
                continue
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        return index

    index = 0
    while index < length:
        comment_end = mask_comment(index)
        if comment_end is not None:
            index = comment_end
            continue
        string = string_at(index)
        if string is not None:
            raw, quote_index, delimiter = string
            index = _mask_dart_string(
                index,
                raw=raw,
                quote_index=quote_index,
                delimiter=delimiter,
            )
            continue
        index += 1

    return "".join(masked)


class_declaration = re.compile(
    r"\bclass\s+(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*)(?P<heritage>[^{}]*)\{"
)
class_alias_declaration = re.compile(
    r"\bclass\s+(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"(?P<heritage>[^;{}]+);"
)
mixin_declaration = re.compile(
    r"\bmixin(?:\s+class)?\s+(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?P<heritage>[^{}]*)\{"
)
lifecycle_declaration = re.compile(
    r"(?m)^[ \t]*(?:[A-Za-z_$][A-Za-z0-9_$]*(?:<[^>\n]+>)?\s+)*"
    r"(?P<method>initState|didChangeDependencies|didUpdateWidget|"
    r"didChangeAccessibilityFeatures|didChangeAppLifecycleState|didChangeLocales|"
    r"didChangeMetrics|didChangePlatformBrightness|didChangeTextScaleFactor|"
    r"didHaveMemoryPressure|didPushRoute|didPopRoute|didRequestAppExit|"
    r"didChangeViewFocus|didPushRouteInformation|handleStartBackGesture|"
    r"handleUpdateBackGestureProgress|handleCommitBackGesture|"
    r"handleCancelBackGesture|activate|deactivate|dispose|"
    r"createState|createElement|build)"
    r"\s*\([^)]*\)\s*(?:async\*?\s*)?(?P<body>\{|=>)"
)
state_lifecycle_methods = frozenset(
    {
        "initState",
        "didChangeDependencies",
        "didUpdateWidget",
        "didChangeAppLifecycleState",
        "activate",
        "deactivate",
        "dispose",
        "build",
    }
)
widget_lifecycle_methods = frozenset({"build", "createState", "createElement"})
observer_lifecycle_methods = frozenset(
    {
        "didChangeAccessibilityFeatures",
        "didChangeAppLifecycleState",
        "didChangeLocales",
        "didChangeMetrics",
        "didChangePlatformBrightness",
        "didChangeTextScaleFactor",
        "didHaveMemoryPressure",
        "didPushRoute",
        "didPopRoute",
        "didRequestAppExit",
        "didChangeViewFocus",
        "didPushRouteInformation",
        "handleStartBackGesture",
        "handleUpdateBackGestureProgress",
        "handleCommitBackGesture",
        "handleCancelBackGesture",
    }
)


def _class_records(masked: str) -> tuple[tuple[str, str, str], ...]:
    records = []
    for match in class_declaration.finditer(masked):
        body_start = match.end() - 1
        body_end = _matching_delimiter_end(masked, body_start)
        if body_end is None:
            raise SystemExit("Unable to parse an AtlasVault Dart class.")
        records.append(
            (
                match.group("name"),
                match.group("heritage"),
                masked[body_start + 1 : body_end - 1],
            )
        )
    for match in class_alias_declaration.finditer(masked):
        records.append(
            (
                match.group("name"),
                "extends " + match.group("heritage"),
                "",
            )
        )
    return tuple(records)


def _mixin_records(masked: str) -> tuple[tuple[str, str, str], ...]:
    records = []
    for match in mixin_declaration.finditer(masked):
        body_start = match.end() - 1
        body_end = _matching_delimiter_end(masked, body_start)
        if body_end is None:
            raise SystemExit("Unable to parse an AtlasVault Dart mixin.")
        records.append(
            (
                match.group("name"),
                match.group("heritage"),
                masked[body_start + 1 : body_end - 1],
            )
        )
    return tuple(records)


def _unqualified_identifier(identifier: str) -> str:
    return re.split(r"\s*\.\s*", identifier)[-1]


def _heritage_base_name(heritage: str) -> str | None:
    start = _skip_dart_whitespace(heritage, 0)
    if start < len(heritage) and heritage[start] == "<":
        type_end = _matching_type_argument_end(heritage, start)
        if type_end is None:
            raise SystemExit("Unable to parse an AtlasVault Dart type parameter.")
        heritage = heritage[type_end:]
    match = re.search(
        r"\bextends\s+(?P<base>(?:_?[A-Za-z_$][A-Za-z0-9_$]*\s*\.\s*)*"
        r"_?[A-Za-z_$][A-Za-z0-9_$]*)",
        heritage,
    )
    return _unqualified_identifier(match.group("base")) if match else None


def _heritage_mixin_names(heritage: str) -> frozenset[str]:
    match = re.search(r"\bwith\s+(?P<mixins>.*?)(?=\bimplements\b|$)", heritage)
    if match is None:
        return frozenset()
    names = set()
    for declaration in match.group("mixins").split(","):
        name = re.match(
            r"\s*(?P<name>(?:_?[A-Za-z_$][A-Za-z0-9_$]*\s*\.\s*)*"
            r"_?[A-Za-z_$][A-Za-z0-9_$]*)",
            declaration,
        )
        if name is not None:
            names.add(_unqualified_identifier(name.group("name")))
    return frozenset(names)


def _at_class_member_depth(source: str, end: int) -> bool:
    return (
        sum(1 for character in source[:end] if character == "{")
        == sum(1 for character in source[:end] if character == "}")
    )


def _brace_depths(source: str) -> tuple[int, ...]:
    depth = 0
    depths = []
    for character in source:
        depths.append(depth)
        if character == "{":
            depth += 1
        elif character == "}":
            depth = max(0, depth - 1)
    return tuple(depths)


def _flutter_lifecycle_class_methods(
    masked_sources: tuple[str, ...],
) -> dict[str, frozenset[str]]:
    records = tuple(
        record for source in masked_sources for record in _class_records(source)
    )
    mixins = tuple(
        record for source in masked_sources for record in _mixin_records(source)
    )
    known_mixins = {name for name, _, _ in mixins}
    state_names = {"State"}
    widget_names = {"StatefulWidget", "StatelessWidget", "Widget"}
    observer_names = {"WidgetsBindingObserver"}
    changed = True
    while changed:
        changed = False
        for name, heritage, _ in records:
            base = _heritage_base_name(heritage)
            if base in state_names and name not in state_names:
                state_names.add(name)
                changed = True
            if base in widget_names and name not in widget_names:
                widget_names.add(name)
                changed = True
            if (
                (base in observer_names or "WidgetsBindingObserver" in heritage)
                and name not in observer_names
            ):
                observer_names.add(name)
                changed = True

    methods = {}
    for name, heritage, _ in records:
        allowed = set()
        if name in state_names:
            allowed.update(state_lifecycle_methods)
        if name in widget_names:
            allowed.update(widget_lifecycle_methods)
        if name in observer_names:
            allowed.update(observer_lifecycle_methods)
        if allowed:
            methods[name] = frozenset(allowed)
            for mixin in _heritage_mixin_names(heritage) & known_mixins:
                methods[mixin] = frozenset(
                    set(methods.get(mixin, frozenset())) | allowed
                )
    return methods


def _automatic_lifecycle_bodies(
    source: str,
    state_class_names: frozenset[str] | None = None,
    lifecycle_class_methods: dict[str, frozenset[str]] | None = None,
    widget_class_names: frozenset[str] | None = None,
    metadata_source: str | None = None,
) -> tuple[tuple[str, str, str], ...]:
    masked = _mask_dart_non_code(metadata_source or source)
    if lifecycle_class_methods is None:
        lifecycle_class_methods = _flutter_lifecycle_class_methods((masked,))
    bodies = []
    for owner, _, class_body in (*_class_records(masked), *_mixin_records(masked)):
        allowed = lifecycle_class_methods.get(owner, frozenset())
        if not allowed:
            continue
        for match in lifecycle_declaration.finditer(class_body):
            if (
                match.group("method") not in allowed
                or not _at_class_member_depth(class_body, match.start())
            ):
                continue
            start = match.start("body")
            if match.group("body") == "=>":
                end = _expression_end(class_body, match.end("body"))
                if end < 0:
                    raise SystemExit("Unable to parse an AtlasVault lifecycle body.")
                bodies.append(
                    (
                        owner,
                        match.group("method"),
                        class_body[match.end("body") : end],
                    )
                )
                continue

            end = _matching_delimiter_end(class_body, start)
            if end is None:
                raise SystemExit("Unable to parse an AtlasVault lifecycle body.")
            bodies.append(
                (
                    owner,
                    match.group("method"),
                    class_body[start + 1 : end - 1],
                )
            )
    return tuple(
        bodies
        + _state_construction_bodies(
            masked,
            state_class_names,
            widget_class_names,
            automatic_bodies=tuple(bodies),
        )
    )


def _matching_delimiter_end(source: str, start: int) -> int | None:
    delimiters = {"(": ")", "[": "]", "{": "}"}
    opening = source[start]
    closing = delimiters.get(opening)
    if closing is None:
        raise SystemExit("Expected a Dart delimiter.")

    depth = 0
    for index in range(start, len(source)):
        if source[index] == opening:
            depth += 1
        elif source[index] == closing:
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _matching_type_argument_end(source: str, start: int) -> int | None:
    if start >= len(source) or source[start] != "<":
        return None
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "<":
            depth += 1
        elif source[index] == ">":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _skip_dart_whitespace(source: str, start: int) -> int:
    while start < len(source) and source[start].isspace():
        start += 1
    return start


def _constructor_parts(source: str, after_parameters: int) -> tuple[str, int | None]:
    """Return constructor initializers and the block or arrow body delimiter."""
    start = _skip_dart_whitespace(source, after_parameters)
    if source.startswith("=>", start) or (start < len(source) and source[start] == "{"):
        return "", start
    if start >= len(source) or source[start] != ":":
        return "", None

    initializer_start = start + 1
    parenthesis_depth = 0
    bracket_depth = 0
    brace_depth = 0
    callback_initializer = re.compile(
        r"(?:^|,)\s*(?:this\.)?[A-Za-z_$][A-Za-z0-9_$]*\s*="
        r"\s*\([^()]*\)\s*(?:async\s*)?$"
    )
    for index in range(initializer_start, len(source)):
        character = source[index]
        if character == "(":
            parenthesis_depth += 1
            continue
        if character == ")":
            parenthesis_depth = max(0, parenthesis_depth - 1)
            continue
        if character == "[":
            bracket_depth += 1
            continue
        if character == "]":
            bracket_depth = max(0, bracket_depth - 1)
            continue
        if character == "{":
            if parenthesis_depth or bracket_depth or brace_depth:
                brace_depth += 1
                continue
            previous = index - 1
            while previous >= initializer_start and source[previous].isspace():
                previous -= 1
            if previous >= initializer_start and source[previous] in "=,:( [>":
                brace_depth += 1
                continue
            if callback_initializer.search(source[initializer_start:index]):
                brace_depth += 1
                continue
            return source[initializer_start:index], index
        if character == "}" and brace_depth:
            brace_depth -= 1
            continue
        if parenthesis_depth or bracket_depth or brace_depth:
            continue
        if source.startswith("=>", index):
            if callback_initializer.search(source[initializer_start:index]):
                continue
            return source[initializer_start:index], index
        if character == ";":
            return source[initializer_start:index], None
    raise SystemExit("Unable to parse an AtlasVault State constructor initializer.")


def _expression_end(source: str, start: int) -> int:
    depth = 0
    for end in range(start, len(source)):
        character = source[end]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth = max(0, depth - 1)
        elif character == ";" and depth == 0:
            return end
    return -1


def _immediately_invoked_closure_bodies(source: str) -> tuple[str, ...]:
    """Return only closures proven to execute during their containing expression."""
    bodies = []
    for opening in (index for index, character in enumerate(source) if character == "("):
        parameter_start = _skip_dart_whitespace(source, opening + 1)
        if parameter_start >= len(source) or source[parameter_start] != "(":
            continue
        parameter_end = _matching_delimiter_end(source, parameter_start)
        if parameter_end is None:
            continue
        body_start = _skip_dart_whitespace(source, parameter_end)
        if source.startswith("async", body_start):
            body_start = _skip_dart_whitespace(source, body_start + len("async"))
        if not (
            source.startswith("=>", body_start)
            or (body_start < len(source) and source[body_start] == "{")
        ):
            continue
        closure_end = _matching_delimiter_end(source, opening)
        if closure_end is None:
            continue
        invocation_start = _skip_dart_whitespace(source, closure_end)
        if not (
            invocation_start < len(source)
            and (
                source[invocation_start] == "("
                or re.match(r"\??\.\s*call\s*\(", source[invocation_start:])
            )
        ):
            continue
        if source.startswith("=>", body_start):
            bodies.append(source[body_start + 2 : closure_end - 1])
        else:
            block_end = _matching_delimiter_end(source, body_start)
            if block_end is None:
                continue
            bodies.append(source[body_start + 1 : block_end - 1])

    function_apply = re.compile(r"\bFunction\s*\.\s*apply\s*\(")
    for match in function_apply.finditer(source):
        parameter_start = _skip_dart_whitespace(source, match.end())
        if parameter_start >= len(source) or source[parameter_start] != "(":
            continue
        parameter_end = _matching_delimiter_end(source, parameter_start)
        if parameter_end is None:
            continue
        body_start = _skip_dart_whitespace(source, parameter_end)
        if source.startswith("async", body_start):
            body_start = _skip_dart_whitespace(source, body_start + len("async"))
        if source.startswith("=>", body_start):
            body_end = _arrow_expression_end(source, body_start + 2)
            bodies.append(source[body_start + 2 : body_end])
        elif body_start < len(source) and source[body_start] == "{":
            body_end = _matching_delimiter_end(source, body_start)
            if body_end is not None:
                bodies.append(source[body_start + 1 : body_end - 1])
    return tuple(bodies)


def _mask_field_closure_literals(source: str) -> str:
    """Leave non-closure field execution visible while hiding stored closures."""
    masked = list(source)

    def is_switch_arm(index: int) -> bool:
        braces = []
        for offset, character in enumerate(source[:index]):
            if character == "{":
                braces.append(offset)
            elif character == "}" and braces:
                braces.pop()
        if not braces:
            return False
        return re.search(
            r"\bswitch\s*\([^{}]*\)\s*$", source[: braces[-1]]
        ) is not None

    def is_synchronously_consumed_collection_callback(index: int) -> bool:
        for opening in range(index - 1, -1, -1):
            if source[opening] != "(":
                continue
            closing = _matching_delimiter_end(source, opening)
            if closing is None or closing <= index:
                continue
            callee = source[:opening].rstrip()
            method = re.search(
                r"\.\s*(?P<name>map|where|expand|forEach|fold|reduce)\s*$",
                callee,
            )
            if method is None:
                continue
            name = method.group("name")
            if name in {"forEach", "fold", "reduce"}:
                return True
            suffix = source[closing:].lstrip()
            return re.match(
                r"\.\s*(?:toList|toSet|forEach|fold|reduce)\s*\(", suffix
            ) is not None
        return False

    for parameter_start in (
        index for index, character in enumerate(source) if character == "("
    ):
        if re.search(r"\bswitch\s*$", source[:parameter_start]):
            continue
        parameter_end = _matching_delimiter_end(source, parameter_start)
        if parameter_end is None:
            continue
        body_start = _skip_dart_whitespace(source, parameter_end)
        if source.startswith("async", body_start):
            body_start = _skip_dart_whitespace(source, body_start + len("async"))
        if source.startswith("=>", body_start):
            body_end = _arrow_expression_end(source, body_start + 2)
        elif body_start < len(source) and source[body_start] == "{":
            body_end = _matching_delimiter_end(source, body_start)
            if body_end is None:
                continue
        else:
            continue
        if not is_synchronously_consumed_collection_callback(parameter_start):
            for index in range(parameter_start, body_end):
                masked[index] = " "
    bare_parameter_arrow = re.compile(
        r"(?<![A-Za-z0-9_$])(?P<parameter>[A-Za-z_$][A-Za-z0-9_$]*)\s*=>"
    )
    for match in bare_parameter_arrow.finditer(source):
        if is_switch_arm(match.start("parameter")):
            continue
        body_end = _arrow_expression_end(source, match.end())
        if not is_synchronously_consumed_collection_callback(match.start("parameter")):
            for index in range(match.start("parameter"), body_end):
                masked[index] = " "
    return "".join(masked)


def _state_class_names(masked_sources: tuple[str, ...]) -> frozenset[str]:
    state_class_names = {"State"}
    changed = True
    while changed:
        changed = False
        for source in masked_sources:
            for name, heritage, _ in _class_records(source):
                if (
                    _heritage_base_name(heritage) in state_class_names
                    and name not in state_class_names
                ):
                    state_class_names.add(name)
                    changed = True
    return frozenset(state_class_names)


def _widget_class_names(masked_sources: tuple[str, ...]) -> frozenset[str]:
    widget_class_names = {"StatefulWidget", "StatelessWidget", "Widget"}
    changed = True
    while changed:
        changed = False
        for source in masked_sources:
            for name, heritage, _ in _class_records(source):
                if (
                    _heritage_base_name(heritage) in widget_class_names
                    and name not in widget_class_names
                ):
                    widget_class_names.add(name)
                    changed = True
    return frozenset(widget_class_names)


def _state_construction_bodies(
    masked: str,
    state_class_names: frozenset[str] | None = None,
    widget_class_names: frozenset[str] | None = None,
    automatic_bodies: tuple[tuple[str, str, str], ...] = (),
) -> list[tuple[str, str, str]]:
    """Return automatically executed State and Widget construction expressions."""
    state_classes = tuple(class_declaration.finditer(masked))
    if state_class_names is None:
        state_class_names = _state_class_names((masked,))
    if widget_class_names is None:
        widget_class_names = _widget_class_names((masked,))
    state_mixin_names = set()
    mixin_records = {
        name: heritage for name, heritage, _ in _mixin_records(masked)
    }
    heritage_by_owner = {
        name: heritage for name, heritage, _ in _class_records(masked)
    }
    heritage_by_owner.update(mixin_records)

    def _owner_sees_declared_field(
        lifecycle_owner: str, field_owner: str
    ) -> bool:
        pending = [lifecycle_owner]
        visited = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            if current == field_owner:
                return True
            heritage = heritage_by_owner.get(current, "")
            base = _heritage_base_name(heritage)
            if base is not None:
                pending.append(base)
            pending.extend(_heritage_mixin_names(heritage))
        return False

    for name, heritage, _ in _class_records(masked):
        if name in state_class_names or name in widget_class_names:
            state_mixin_names.update(_heritage_mixin_names(heritage))
    changed = True
    while changed:
        changed = False
        for name in tuple(state_mixin_names):
            for inherited in _heritage_mixin_names(mixin_records.get(name, "")):
                if inherited not in state_mixin_names:
                    state_mixin_names.add(inherited)
                    changed = True
    state_declarations = (
        tuple(
            (match, True)
            for match in state_classes
            if match.group("name") in state_class_names
            or match.group("name") in widget_class_names
        )
        + tuple(
            (match, False)
            for match in mixin_declaration.finditer(masked)
            if match.group("name") in state_mixin_names
        )
    )
    bodies = []
    for class_match, has_constructor in state_declarations:
        owner = class_match.group("name")
        if (
            owner not in state_class_names
            and owner not in state_mixin_names
            and owner not in widget_class_names
        ):
            continue
        class_start = class_match.end() - 1
        depth = 0
        for class_end in range(class_start, len(masked)):
            if masked[class_end] == "{":
                depth += 1
            elif masked[class_end] == "}":
                depth -= 1
                if depth == 0:
                    break
        else:
            raise SystemExit("Unable to parse an AtlasVault State class.")

        class_body = masked[class_start + 1 : class_end]
        lazy_field_expressions: list[tuple[str, str]] = []
        if has_constructor:
            constructor = re.compile(
                rf"(?m)^[ \t]*(?:(?:const|factory)\s+)?{re.escape(owner)}"
                r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)?\s*\("
            )
            for match in constructor.finditer(class_body):
                parameter_end = _matching_delimiter_end(class_body, match.end() - 1)
                if parameter_end is None:
                    raise SystemExit("Unable to parse an AtlasVault State constructor signature.")
                initializers, body_start = _constructor_parts(class_body, parameter_end)
                constructor_parts = []
                if initializers.strip():
                    constructor_parts.append(_mask_field_closure_literals(initializers))
                if body_start is not None:
                    if class_body.startswith("=>", body_start):
                        end = _expression_end(class_body, body_start + 2)
                        if end < 0:
                            raise SystemExit("Unable to parse an AtlasVault State constructor.")
                        constructor_parts.append(class_body[body_start + 2 : end])
                    elif class_body[body_start] == "{":
                        end = _matching_delimiter_end(class_body, body_start)
                        if end is None:
                            raise SystemExit("Unable to parse an AtlasVault State constructor.")
                        constructor_parts.append(class_body[body_start + 1 : end - 1])
                if constructor_parts:
                    bodies.append((owner, "state_constructor", ";\n".join(constructor_parts)))

        statement_start = 0
        depth = 0
        index = 0
        while index < len(class_body):
            character = class_body[index]
            if character == "{" and depth == 0:
                prefix = class_body[statement_start:index]
                if _member_block_is_method(prefix, class_match.group("name")):
                    member_end = _matching_delimiter_end(class_body, index)
                    if member_end is None:
                        raise SystemExit("Unable to parse an AtlasVault State member.")
                    statement_start = member_end
                    index = member_end
                    continue
            if character in "([{":
                depth += 1
            elif character in ")]}":
                depth = max(0, depth - 1)
            elif character == ";" and depth == 0:
                statement = class_body[statement_start:index]
                statement_start = index + 1
                if (
                    "=" not in statement
                    or _member_expression_is_method(
                        statement, owner
                    )
                ):
                    index += 1
                    continue
                field_expression = statement.split("=", 1)[1]
                if re.match(r"^\s*(?:late|static)\b", statement):
                    names = re.findall(
                        r"_?[A-Za-z_$][A-Za-z0-9_$]*",
                        statement.split("=", 1)[0],
                    )
                    if names:
                        lazy_field_expressions.append(
                            (names[-1], field_expression)
                        )
                    index += 1
                    continue
                invoked_closure_bodies = _immediately_invoked_closure_bodies(
                    field_expression
                )
                if invoked_closure_bodies:
                    bodies.extend(
                        (owner, "state_field", closure_body)
                        for closure_body in invoked_closure_bodies
                    )
                if "=>" in field_expression or re.search(
                    r"\)\s*(?:async\s*)?\{", field_expression
                ):
                    bodies.append(
                        (
                            owner,
                            "state_field",
                            _mask_field_closure_literals(field_expression),
                        )
                    )
                    continue
                bodies.append(
                    (owner, "state_field", field_expression)
                )
            index += 1
        for field_name, field_expression in lazy_field_expressions:
            if not any(
                _owner_sees_declared_field(lifecycle_owner, owner)
                and re.search(
                    rf"(?<![A-Za-z0-9_$]){re.escape(field_name)}(?![A-Za-z0-9_$])",
                    lifecycle_body,
                )
                for lifecycle_owner, _, lifecycle_body in automatic_bodies
            ):
                continue
            invoked_closure_bodies = _immediately_invoked_closure_bodies(
                field_expression
            )
            if invoked_closure_bodies:
                bodies.extend(
                    (owner, "state_lazy_field", closure_body)
                    for closure_body in invoked_closure_bodies
                )
            if "=>" in field_expression or re.search(
                r"\)\s*(?:async\s*)?\{", field_expression
            ):
                bodies.append(
                    (
                        owner,
                        "state_lazy_field",
                        _mask_field_closure_literals(field_expression),
                    )
                )
                continue
            bodies.append((owner, "state_lazy_field", field_expression))
    return bodies


def _member_block_is_method(prefix: str, class_name: str) -> bool:
    stripped = prefix.strip()
    constructor = re.search(
        rf"(?m)(?:^|\n)\s*(?:(?:const|factory)\s+)?"
        rf"{re.escape(class_name)}(?:\.[A-Za-z_$][A-Za-z0-9_$]*)?\s*\(",
        stripped,
    )
    return constructor is not None or (
        "=" not in stripped
        and re.search(r"\)\s*(?:async\*?\s*)?$", stripped) is not None
    )


def _member_expression_is_method(statement: str, class_name: str) -> bool:
    stripped = statement.strip()
    return (
        _member_block_is_method(stripped, class_name)
        or re.match(
            r"(?:[A-Za-z_$][A-Za-z0-9_$]*(?:<[^>\n]+>)?\s+)*"
            r"[A-Za-z_$][A-Za-z0-9_$]*\s*\([^)]*\)\s*"
            r"(?:async\*?\s*)?=>",
            stripped,
        )
        is not None
    )


operation_reference = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"(?P<target>[A-Za-z_$][A-Za-z0-9_$]*)\s*"
    r"(?:\(|\??\.\s*call\s*\(|(?=[,)]))",
)
qualified_operation_tear_off = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"(?:[A-Za-z_$][A-Za-z0-9_$]*\s*(?:\?|!)?\.\s*)+"
    r"(?P<target>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?=:)"
)
operation_invocation = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"(?P<target>[A-Za-z_$][A-Za-z0-9_$]*)\s*"
    r"(?:\(|\??\.\s*call\s*\()"
)
parenthesized_operation_invocation = re.compile(
    r"\(\s*(?:[A-Za-z_$][A-Za-z0-9_$]*\s*(?:\?|!)?\.\s*)*"
    r"(?P<target>[A-Za-z_$][A-Za-z0-9_$]*)\s*\)\s*"
    r"(?:\(|\??\.\s*call\s*\()"
)
operation_identifiers = frozenset(
    {
        "startpairing",
        "createdeviceidentity",
        "createprimaryidentity",
        "generateatlasvaultdeviceidentity",
        "createpairingoffer",
        "createatlasvaultpairingoffer",
        "createatlasvaultpairingacceptance",
        "createatlasvaultpairingkeyrequest",
        "createatlasvaultpairingacknowledgement",
        "createatlasvaultkeydelivery",
        "savepairingoffer",
        "importpairingoffer",
        "savepairingacceptance",
        "importpairingacceptance",
        "confirmcodesmatch",
        "savekeydelivery",
        "importkeydelivery",
        "savepairingacknowledgement",
        "importpairingacknowledgement",
        "resumepairing",
        "discardpairing",
        "importencryptedbackup",
        "exportencryptedbackup",
        "beginrecoverysetup",
        "confirmrecoverysetup",
        "prepareexistingrecoveryexport",
        "savepreparedexport",
        "preparerecoveryimport",
        "confirmrecoveryimport",
        "discardpendingimport",
        "prepareencryptedmigration",
        "finalizemigration",
        "resumemigration",
        "activateencryptedprivatedata",
        "activateencryptedprivatestate",
        "pickencryptedexport",
        "saveencryptedexport",
    }
)
operation_alias_assignment = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"(?P<alias>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"(?P<value>[^;]+)\s*;"
)
operation_tear_off_target = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"(?:[A-Za-z_$][A-Za-z0-9_$]*\s*(?:\?|!)?\.\s*)*"
    r"(?P<target>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?=\s*(?:[,;\]\}]|\?\?|\Z))"
)

receiver_owned_sensitive_operations = {
    "atlasvaultplaintextmigrationcoordinator": frozenset(
        {
            "prepare",
            "resumepreparation",
            "discardprepared",
            "finalizeandactivate",
            "activateselected",
        }
    ),
}


def _is_sensitive_operation(
    identifier: str,
    local_wrappers: frozenset[str] = frozenset(),
) -> bool:
    identifier = identifier.casefold()
    return identifier in operation_identifiers or identifier in local_wrappers


def _is_receiver_owned_sensitive_operation(owner: str, method: str) -> bool:
    return method.casefold() in receiver_owned_sensitive_operations.get(
        owner.casefold(), frozenset()
    )


def _operation_invocations(source: str) -> tuple[re.Match[str], ...]:
    return (
        *operation_invocation.finditer(source),
        *parenthesized_operation_invocation.finditer(source),
    )


def _operation_references(source: str) -> tuple[re.Match[str], ...]:
    return (
        *operation_reference.finditer(source),
        *qualified_operation_tear_off.finditer(source),
    )


def _typed_operation_invocation_targets(source: str) -> tuple[str, ...]:
    targets = []
    prefix = re.compile(
        r"(?<![A-Za-z0-9_$])(?P<target>[A-Za-z_$][A-Za-z0-9_$]*)\s*<"
    )
    for match in prefix.finditer(source):
        type_end = _matching_type_argument_end(source, match.end() - 1)
        if type_end is None:
            continue
        if re.match(r"\s*(?:\(|\??\.\s*call\s*\()", source[type_end:]):
            targets.append(match.group("target"))
    return tuple(targets)


def _local_method_bodies(source: str) -> dict[str, dict[str, str]]:
    masked = _mask_dart_non_code(source)
    declaration = re.compile(
        r"(?m)^[ \t]*(?:[A-Za-z_$][A-Za-z0-9_$]*(?:<[^>\n]+>)?\s+)*"
        r"(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*)(?:<[^>\n]+>)?\s*\("
    )
    getter_declaration = re.compile(
        r"(?m)^[ \t]*(?:[A-Za-z_$][A-Za-z0-9_$]*(?:<[^>\n]+>)?\s+)*"
        r"get\s+(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*)\s*(?P<body>\{|=>)"
    )
    class_records = _class_records(masked)
    class_owner_names = frozenset(name for name, _, _ in class_records)
    methods_by_owner = {}
    for owner, _, class_body in (*class_records, *_mixin_records(masked)):
        methods = {}
        for match in declaration.finditer(class_body):
            if not _at_class_member_depth(class_body, match.start()):
                continue
            parameter_start = match.end() - 1
            parameter_end = _matching_delimiter_end(class_body, parameter_start)
            if parameter_end is None:
                raise SystemExit("Unable to parse an AtlasVault local method signature.")

            body_start = _skip_dart_whitespace(class_body, parameter_end)
            if class_body.startswith("async*", body_start):
                body_start += len("async*")
            elif class_body.startswith("async", body_start):
                body_start += len("async")
            body_start = _skip_dart_whitespace(class_body, body_start)
            if class_body.startswith("=>", body_start):
                end = _expression_end(class_body, body_start + 2)
                if end < 0:
                    raise SystemExit("Unable to parse an AtlasVault local method.")
                name = match.group("name").casefold()
                methods[name] = methods.get(name, "") + "\n" + class_body[
                    body_start + 2 : end
                ]
                continue
            if body_start >= len(class_body) or class_body[body_start] != "{":
                continue

            end = _matching_delimiter_end(class_body, body_start)
            if end is None:
                raise SystemExit("Unable to parse an AtlasVault local method.")
            name = match.group("name").casefold()
            methods[name] = methods.get(name, "") + "\n" + class_body[
                body_start + 1 : end - 1
            ]
        for match in getter_declaration.finditer(class_body):
            if not _at_class_member_depth(class_body, match.start()):
                continue
            body_start = match.start("body")
            if match.group("body") == "=>":
                end = _expression_end(class_body, match.end("body"))
                if end < 0:
                    raise SystemExit("Unable to parse an AtlasVault local getter.")
                body = class_body[match.end("body") : end]
            else:
                end = _matching_delimiter_end(class_body, body_start)
                if end is None:
                    raise SystemExit("Unable to parse an AtlasVault local getter.")
                body = class_body[body_start + 1 : end - 1]
            name = match.group("name").casefold()
            methods[name] = methods.get(name, "") + "\n" + body
        if owner in class_owner_names:
            named_constructor = re.compile(
                rf"(?m)^[ \t]*(?:(?:const|factory)\s+)?{re.escape(owner)}"
                r"\.(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*)\s*\("
            )
            for match in named_constructor.finditer(class_body):
                if not _at_class_member_depth(class_body, match.start()):
                    continue
                parameter_end = _matching_delimiter_end(
                    class_body, match.end() - 1
                )
                if parameter_end is None:
                    raise SystemExit(
                        "Unable to parse an AtlasVault named constructor signature."
                    )
                initializers, body_start = _constructor_parts(
                    class_body, parameter_end
                )
                parts = []
                if initializers.strip():
                    parts.append(_mask_field_closure_literals(initializers))
                if body_start is not None:
                    if class_body.startswith("=>", body_start):
                        end = _expression_end(class_body, body_start + 2)
                        if end < 0:
                            raise SystemExit(
                                "Unable to parse an AtlasVault named constructor."
                            )
                        parts.append(class_body[body_start + 2 : end])
                    elif class_body[body_start] == "{":
                        end = _matching_delimiter_end(class_body, body_start)
                        if end is None:
                            raise SystemExit(
                                "Unable to parse an AtlasVault named constructor."
                            )
                        parts.append(class_body[body_start + 1 : end - 1])
                if parts:
                    name = f"{owner}.{match.group('name')}".casefold()
                    methods[name] = methods.get(name, "") + "\n" + ";\n".join(parts)
        methods_by_owner[owner] = methods
    return methods_by_owner


def _sensitive_local_wrappers(source: str) -> dict[str, frozenset[str]]:
    wrappers_by_owner = {}
    for owner, methods in _local_method_bodies(source).items():
        wrappers_by_owner[owner] = _sensitive_wrapper_names(methods)
    return wrappers_by_owner


def _sensitive_wrapper_names(methods: dict[str, str]) -> frozenset[str]:
    wrappers = set()
    changed = True
    while changed:
        changed = False
        for name, body in methods.items():
            if name in wrappers:
                continue
            execution_body = _mask_deferred_build_closures(body)
            known_wrappers = frozenset(wrappers)
            aliases = _operation_aliases(execution_body, known_wrappers)
            if any(
                _is_sensitive_operation(reference.group("target"), known_wrappers)
                for reference in _operation_invocations(execution_body)
            ) or any(
                _is_sensitive_operation(target, known_wrappers)
                for target in _typed_operation_invocation_targets(execution_body)
            ) or any(
                re.search(
                    rf"(?<![A-Za-z0-9_$]){re.escape(wrapper)}\s*(?:\(|\??\.\s*call\s*\()",
                    execution_body,
                )
                for wrapper in wrappers
            ) or _build_scheduler_invokes_sensitive_operation(
                execution_body, aliases, known_wrappers
            ) or any(
                _alias_is_executed(execution_body, alias, build=True)
                for alias in aliases
            ):
                wrappers.add(name)
                changed = True
    return frozenset(wrappers)


def _package_executable_method_bodies(
    masked: str, *, normalize_names: bool = True
) -> dict[str, str]:
    """Return function declarations at the root of one package scope."""
    declaration = re.compile(
        r"(?m)^[ \t]*(?:[A-Za-z_$][A-Za-z0-9_$]*(?:<[^>\n]+>)?\s+)*"
        r"(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*)(?:<[^>\n]+>)?\s*\("
    )
    getter_declaration = re.compile(
        r"(?m)^[ \t]*(?:[A-Za-z_$][A-Za-z0-9_$]*(?:<[^>\n]+>)?\s+)*"
        r"get\s+(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*)\s*(?P<body>\{|=>)"
    )
    depths = _brace_depths(masked)
    methods = {}
    for match in declaration.finditer(masked):
        if depths[match.start()] != 0:
            continue
        parameter_end = _matching_delimiter_end(masked, match.end() - 1)
        if parameter_end is None:
            raise SystemExit("Unable to parse an AtlasVault top-level signature.")
        body_start = _skip_dart_whitespace(masked, parameter_end)
        if masked.startswith("async*", body_start):
            body_start += len("async*")
        elif masked.startswith("async", body_start):
            body_start += len("async")
        body_start = _skip_dart_whitespace(masked, body_start)
        if masked.startswith("=>", body_start):
            end = _expression_end(masked, body_start + 2)
            if end < 0:
                raise SystemExit("Unable to parse an AtlasVault top-level function.")
            body = masked[body_start + 2 : end]
        elif body_start < len(masked) and masked[body_start] == "{":
            end = _matching_delimiter_end(masked, body_start)
            if end is None:
                raise SystemExit("Unable to parse an AtlasVault top-level function.")
            body = masked[body_start + 1 : end - 1]
        else:
            continue
        name = match.group("name")
        if normalize_names:
            name = name.casefold()
        methods[name] = methods.get(name, "") + "\n" + body
    for match in getter_declaration.finditer(masked):
        if depths[match.start()] != 0:
            continue
        body_start = match.start("body")
        if match.group("body") == "=>":
            end = _expression_end(masked, match.end("body"))
            if end < 0:
                raise SystemExit("Unable to parse an AtlasVault top-level getter.")
            body = masked[match.end("body") : end]
        else:
            end = _matching_delimiter_end(masked, body_start)
            if end is None:
                raise SystemExit("Unable to parse an AtlasVault top-level getter.")
            body = masked[body_start + 1 : end - 1]
        name = match.group("name")
        if normalize_names:
            name = name.casefold()
        methods[name] = methods.get(name, "") + "\n" + body
    return methods


def _top_level_method_bodies(
    source: str, *, normalize_names: bool = True
) -> dict[str, str]:
    """Return package functions and extension methods, never class members."""
    masked = _mask_dart_non_code(source)
    methods = _package_executable_method_bodies(
        masked, normalize_names=normalize_names
    )
    extension_declaration = re.compile(
        r"\bextension(?:\s+_?[A-Za-z_$][A-Za-z0-9_$]*)?"
        r"(?:\s*<[^{}\n]*>)?\s+on\b[^{}\n]*\{"
    )
    for match in extension_declaration.finditer(masked):
        body_end = _matching_delimiter_end(masked, match.end() - 1)
        if body_end is None:
            raise SystemExit("Unable to parse an AtlasVault extension declaration.")
        extension_methods = _package_executable_method_bodies(
            masked[match.end() : body_end - 1], normalize_names=normalize_names
        )
        for name, body in extension_methods.items():
            methods[name] = methods.get(name, "") + "\n" + body
    return methods


def _operation_aliases(
    body: str,
    local_wrappers: frozenset[str] = frozenset(),
) -> frozenset[str]:
    assignments = tuple(
        (match.group("alias"), target_match.group("target"))
        for match in operation_alias_assignment.finditer(body)
        for target_match in operation_tear_off_target.finditer(
            match.group("value")
        )
    )
    aliases = set()
    changed = True
    while changed:
        changed = False
        for alias, target in assignments:
            if (
                not _is_sensitive_operation(target, local_wrappers)
                and target not in aliases
            ):
                continue
            if alias not in aliases:
                aliases.add(alias)
                changed = True
    return frozenset(aliases)


def _alias_is_executed(body: str, alias: str, *, build: bool) -> bool:
    invocation = r"(?:\(|\??\.\s*call\s*\()"
    if not build:
        invocation = r"(?:\(|\??\.\s*call\s*\(|(?=[,)]))"
    usage = re.compile(
        rf"(?<![A-Za-z0-9_$]){re.escape(alias)}\s*"
        r"(?:\[[^\]]+\]|\.\s*(?:first|last|single))?\s*"
        r"(?:!\s*)?"
        + invocation
    )
    return usage.search(body) is not None


def _owner_heritage(source: str) -> dict[str, str]:
    masked = _mask_dart_non_code(source)
    return {
        name: heritage
        for name, heritage, _ in (*_class_records(masked), *_mixin_records(masked))
    }


def _inherited_local_wrappers(
    owner: str,
    wrappers_by_owner: dict[str, frozenset[str]],
    methods_by_owner: dict[str, dict[str, str]],
    owner_heritage: dict[str, str],
) -> frozenset[str]:
    visible = {}

    def visit(current: str, visited: set[str]) -> None:
        if current in visited:
            return
        visited.add(current)
        heritage = owner_heritage.get(current, "")
        base = _heritage_base_name(heritage)
        if base is not None:
            visit(base, visited)
        for mixin in _heritage_mixin_names(heritage):
            visit(mixin, visited)
        for name in methods_by_owner.get(current, {}):
            visible[name] = name in wrappers_by_owner.get(current, frozenset())

    visit(owner, set())
    return frozenset(name for name, sensitive in visible.items() if sensitive)


def _receiver_owned_wrapper_invoked(
    body: str,
    wrappers_by_owner: dict[str, frozenset[str]],
) -> bool:
    invocation = re.compile(
        r"(?<![A-Za-z0-9_$])(?:(?:new|const)\s+)?"
        r"(?P<owner>_?[A-Za-z_$][A-Za-z0-9_$]*)"
        r"(?:\s*<[^()<>]*>)?\s*\([^()]*\)\s*\.\s*"
        r"(?P<method>_?[A-Za-z_$][A-Za-z0-9_$]*)\s*"
        r"(?:\(|\??\.\s*call\s*\()"
    )
    for match in invocation.finditer(body):
        wrappers = wrappers_by_owner.get(match.group("owner"), frozenset())
        if (
            match.group("method").casefold() in wrappers
            or match.group("owner").casefold() in wrappers
            or _is_receiver_owned_sensitive_operation(
                match.group("owner"), match.group("method")
            )
        ):
            return True
    assignments = re.compile(
        r"(?m)^\s*(?:final|var|late\s+final|"
        r"_?[A-Za-z_$][A-Za-z0-9_$]*(?:<[^;=()]*>)?\s+)?"
        r"(?P<alias>_?[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:(?:new|const)\s+)?"
        r"(?P<owner>_?[A-Za-z_$][A-Za-z0-9_$]*)"
        r"(?:\s*<[^()<>]*>)?\s*\([^()]*\)\s*;"
    )
    aliases = {
        match.group("alias"): match.group("owner")
        for match in assignments.finditer(body)
        if (
            wrappers_by_owner.get(match.group("owner"), frozenset())
            or match.group("owner").casefold()
            in receiver_owned_sensitive_operations
        )
    }
    for alias, owner in aliases.items():
        alias_invocation = re.compile(
            rf"(?<![A-Za-z0-9_$]){re.escape(alias)}\s*\.\s*"
            r"(?P<method>_?[A-Za-z_$][A-Za-z0-9_$]*)\s*"
            r"(?:\(|\??\.\s*call\s*\()"
        )
        wrappers = wrappers_by_owner.get(owner, frozenset())
        if any(
            match.group("method").casefold() in wrappers
            or _is_receiver_owned_sensitive_operation(
                owner, match.group("method")
            )
            for match in alias_invocation.finditer(body)
        ):
            return True
    return False


def _receiver_owned_constructor_invoked(
    body: str,
    wrappers_by_owner: dict[str, frozenset[str]],
) -> bool:
    constructor = re.compile(
        r"(?<![A-Za-z0-9_$])(?:(?:new|const)\s+)?"
        r"(?P<owner>_?[A-Za-z_$][A-Za-z0-9_$]*)"
        r"(?:\s*\.\s*(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*))?"
        r"(?:\s*<[^()<>]*>)?\s*\([^()]*\)"
    )
    for match in constructor.finditer(body):
        wrappers = wrappers_by_owner.get(match.group("owner"), frozenset())
        constructor_name = match.group("owner").casefold()
        if match.group("name") is not None:
            constructor_name += "." + match.group("name").casefold()
        if constructor_name in wrappers:
            return True
    return False


def _top_level_lazy_initializer_bodies(
    source: str, automatic_body: str
) -> tuple[tuple[str, str, str], ...]:
    """Return top-level lazy initializers reached by an automatic root."""
    masked = _mask_dart_non_code(source)
    depths = _brace_depths(masked)
    declaration = re.compile(
        r"(?m)^[ \t]*(?:late\s+)?(?:"
        r"(?:final|var)\s+|[A-Za-z_$][A-Za-z0-9_$]*(?:<[^;=]+>)?(?:[?!])?\s+"
        r")"
        r"(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
        r"(?P<expression>[^;]+);"
    )
    bodies = []
    for match in declaration.finditer(masked):
        if depths[match.start()] != 0:
            continue
        name = match.group("name")
        if re.search(
            rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])",
            automatic_body,
        ):
            bodies.append(
                (
                    "__atlasvault_top_level__",
                    "top_level_lazy_initializer",
                    match.group("expression"),
                )
            )
    return tuple(bodies)


def _mask_deferred_build_closures(body: str) -> str:
    """Hide allowlisted user-event callback bodies from build-time execution scans."""
    masked = list(body)
    deferred_callback_consumers = frozenset(
        {
            "ActionButton",
            "Autocomplete",
            "Checkbox",
            "CheckboxListTile",
            "ChoiceChip",
            "Dismissible",
            "DropdownButton",
            "DropdownMenu",
            "ElevatedButton",
            "FilledButton",
            "FilterChip",
            "FloatingActionButton",
            "GestureDetector",
            "IconButton",
            "InkResponse",
            "InkWell",
            "InputChip",
            "NavigationBar",
            "NavigationRail",
            "OutlinedButton",
            "Radio",
            "RadioListTile",
            "RangeSlider",
            "SearchAnchor",
            "SearchBar",
            "Slider",
            "Switch",
            "SwitchListTile",
            "TabBar",
            "TextButton",
            "TextField",
            "TextFormField",
        }
    )

    def is_deferred_callback_consumer(argument_start: int) -> bool:
        openings = []
        for index, character in enumerate(body[:argument_start]):
            if character == "(":
                openings.append(index)
            elif character == ")" and openings:
                openings.pop()
        if not openings:
            return False
        prefix = body[: openings[-1]].rstrip()
        match = re.search(
            r"(?P<name>_?[A-Za-z_$][A-Za-z0-9_$]*)"
            r"(?:\s*<[^()<>]*>)?\s*$",
            prefix,
        )
        return (
            match is not None
            and match.group("name") in deferred_callback_consumers
        )

    user_event = (
        r"(?:onPressed|onTap|onLongPress|onDoubleTap|onChanged|onSubmitted|"
        r"onFieldSubmitted|onDeleted|onDestinationSelected|onShowFilters|"
        r"onSourceSelected|onToggle|onSelected|onDismissed)"
    )
    arrow_closure = re.compile(
        rf"\b{user_event}\s*:\s*[^,;]*?"
        r"(?:\([^()]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>\s*"
    )
    block_closure = re.compile(
        rf"\b{user_event}\s*:\s*[^,;]*?"
        r"\([^()]*\)\s*(?:async\s*)?\{"
    )
    closures = [
        (match.start(), match.end(), False) for match in arrow_closure.finditer(body)
    ] + [
        (match.start(), match.end(), True) for match in block_closure.finditer(body)
    ]
    for argument_start, start, block in closures:
        if not is_deferred_callback_consumer(argument_start):
            continue
        if block:
            depth = 1
            end = start
            while end < len(body) and depth:
                if body[end] == "{":
                    depth += 1
                elif body[end] == "}":
                    depth -= 1
                end += 1
        else:
            end = _arrow_expression_end(body, start)
        for index in range(start, end):
            masked[index] = " "
    return "".join(masked)


def _arrow_expression_end(body: str, start: int) -> int:
    depth = 0
    for end in range(start, len(body)):
        character = body[end]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth = max(0, depth - 1)
        elif character in ",;" and depth == 0:
            return end
    return len(body)


def _build_scheduler_invokes_sensitive_operation(
    body: str,
    aliases: frozenset[str],
    local_wrappers: frozenset[str],
) -> bool:
    scheduler = re.compile(
        r"(?:Future(?:<[^>\n]+>)?(?:\.(?:microtask|sync|delayed))?|"
        r"scheduleMicrotask|Timer\.run|"
        r"WidgetsBinding\.instance\.addPostFrameCallback)\s*"
        r"\((?P<argument>[^;]+)\)",
        re.DOTALL,
    )
    for match in scheduler.finditer(body):
        argument = match.group("argument")
        if any(
            _is_sensitive_operation(reference.group("target"), local_wrappers)
            for reference in (
                *_operation_references(argument),
                *operation_tear_off_target.finditer(argument),
            )
        ):
            return True
        if any(
            re.search(rf"(?<![A-Za-z0-9_$]){re.escape(alias)}(?![A-Za-z0-9_$])", argument)
            for alias in aliases
        ):
            return True
    return False


def _build_iife_invokes_sensitive_operation(
    body: str,
    local_wrappers: frozenset[str],
) -> bool:
    arrow_iife = re.compile(
        r"\(\s*(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>\s*"
        r"(?P<expression>[^;{}]+?)\s*\)\s*\([^)]*\)",
        re.DOTALL,
    )
    block_iife = re.compile(
        r"\(\s*\([^)]*\)\s*\{(?P<expression>[^{}]*)\}\s*\)\s*\([^)]*\)",
        re.DOTALL,
    )
    return any(
        _is_sensitive_operation(reference.group("target"), local_wrappers)
        for match in (*arrow_iife.finditer(body), *block_iife.finditer(body))
        for reference in _operation_references(match.group("expression"))
    )


def _function_apply_invokes_sensitive_operation(
    body: str,
    aliases: frozenset[str],
    local_wrappers: frozenset[str],
) -> bool:
    function_apply = re.compile(
        r"\bFunction\s*\.\s*apply\s*\(\s*(?P<argument>[^,]+)"
    )
    for match in function_apply.finditer(body):
        argument = match.group("argument")
        if any(
            _is_sensitive_operation(reference.group("target"), local_wrappers)
            for reference in (
                *_operation_references(argument),
                *operation_tear_off_target.finditer(argument),
            )
        ):
            return True
        if any(
            re.search(
                rf"(?<![A-Za-z0-9_$]){re.escape(alias)}(?![A-Za-z0-9_$])",
                argument,
            )
            for alias in aliases
        ):
            return True
    return False


def _has_automatic_operation(
    source: str,
    state_class_names: frozenset[str] | None = None,
    lifecycle_class_methods: dict[str, frozenset[str]] | None = None,
    metadata_source: str | None = None,
) -> bool:
    wrapper_source = metadata_source or source
    methods_by_owner = _local_method_bodies(wrapper_source)
    local_wrappers_by_owner = _sensitive_local_wrappers(wrapper_source)
    top_level_wrappers = _sensitive_wrapper_names(
        _top_level_method_bodies(wrapper_source)
    )
    owner_heritage = _owner_heritage(wrapper_source)
    widget_class_names = _widget_class_names((wrapper_source,))
    automatic_bodies = list(
        _automatic_lifecycle_bodies(
            source,
            state_class_names,
            lifecycle_class_methods,
            widget_class_names,
            wrapper_source,
        )
    )
    for name, body in _top_level_method_bodies(source).items():
        if name == "main":
            automatic_bodies.append(("__atlasvault_main__", "main", body))
    for _, _, body in tuple(automatic_bodies):
        automatic_bodies.extend(_top_level_lazy_initializer_bodies(source, body))
    for owner, method, body in automatic_bodies:
        local_wrappers = _inherited_local_wrappers(
            owner, local_wrappers_by_owner, methods_by_owner, owner_heritage
        ) | top_level_wrappers
        build = method == "build"
        execution_body = _mask_deferred_build_closures(body) if build else body
        aliases = _operation_aliases(execution_body, local_wrappers)
        references = (
            _operation_invocations(execution_body)
            if build
            else _operation_references(execution_body)
        )
        if any(
            _is_sensitive_operation(match.group("target"), local_wrappers)
            for match in references
        ) or any(
            _is_sensitive_operation(target, local_wrappers)
            for target in _typed_operation_invocation_targets(execution_body)
        ):
            return True
        if build and (
            _build_scheduler_invokes_sensitive_operation(
                execution_body, aliases, local_wrappers
            )
            or _build_iife_invokes_sensitive_operation(
                execution_body, local_wrappers
            )
            or _function_apply_invokes_sensitive_operation(
                execution_body, aliases, local_wrappers
            )
        ):
            return True
        if any(
            _alias_is_executed(execution_body, alias, build=build)
            for alias in aliases
        ):
            return True
        if _receiver_owned_wrapper_invoked(
            execution_body, local_wrappers_by_owner
        ):
            return True
        if _receiver_owned_constructor_invoked(
            execution_body, local_wrappers_by_owner
        ):
            return True
    return False


def _sources_have_automatic_operation(
    sources: tuple[str, ...],
    source_paths: tuple[Path, ...] | None = None,
) -> bool:
    if source_paths is not None and len(source_paths) != len(sources):
        raise SystemExit("AtlasVault Dart source paths do not match sources.")
    library_directive = re.compile(
        r"(?m)^\s*library\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$.]*)\s*;"
    )
    part_of_directive = re.compile(
        r"(?m)^\s*part\s+of\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$.]*)\s*;"
    )
    part_of_uri_directive = re.compile(
        r"(?m)^\s*part\s+of\s+(?P<quote>['\"])(?P<uri>[^'\"\r\n]+)(?P=quote)\s*;"
    )
    directive_declaration = re.compile(
        r"(?m)^\s*(?P<kind>import|export)\s+(?P<declaration>[^;]+);"
    )
    import_uri = re.compile(r"(?P<quote>['\"])(?P<uri>[^'\"\r\n]+)(?P=quote)")
    import_prefix = re.compile(
        r"\bas\s+(?P<prefix>_?[A-Za-z_$][A-Za-z0-9_$]*)\b"
    )
    source_metadata = []
    path_groups = {}
    for index, source in enumerate(sources):
        path = source_paths[index].resolve() if source_paths is not None else None
        library_match = library_directive.search(source)
        group = (
            f"library:{library_match.group('name')}"
            if library_match is not None
            else f"path:{path}" if path is not None else f"source:{index}"
        )
        source_metadata.append((source, path, group))
        if path is not None:
            path_groups[path] = group

    libraries: dict[str, list[tuple[str, Path | None]]] = {}
    for source, path, group in source_metadata:
        part_of = part_of_directive.search(source)
        part_of_uri = part_of_uri_directive.search(source)
        if part_of is not None:
            library = f"library:{part_of.group('name')}"
        elif part_of_uri is not None and path is not None:
            owner_path = (path.parent / part_of_uri.group("uri")).resolve()
            library = path_groups.get(owner_path, f"path:{owner_path}")
        else:
            library = group
        libraries.setdefault(library, []).append((source, path))

    declared_names = {}
    for library, library_sources in libraries.items():
        names = {
            name
            for source, _ in library_sources
            for name, _, _ in (
                *_class_records(_mask_dart_non_code(source)),
                *_mixin_records(_mask_dart_non_code(source)),
            )
        }
        names.update(
            name
            for source, _ in library_sources
            for name in _top_level_method_bodies(source, normalize_names=False)
        )
        declared_names[library] = frozenset(names)

    package_paths = {}
    for path, group in path_groups.items():
        library_root = path.parent
        while library_root.name != "lib" and library_root != library_root.parent:
            library_root = library_root.parent
        if library_root.name != "lib":
            continue
        relative = path.relative_to(library_root).as_posix()
        package_paths.setdefault(relative, set()).add(group)

    def resolve_uri(path: Path, uri: str) -> str | None:
        if uri.startswith("package:"):
            _, separator, relative = uri.partition("/")
            if not separator:
                return None
            candidates = package_paths.get(relative, frozenset())
            return next(iter(candidates)) if len(candidates) == 1 else None
        if ":" in uri:
            return None
        return path_groups.get((path.parent / uri).resolve())

    def combinator_names(declaration: str, kind: str) -> frozenset[str] | None:
        match = re.search(
            rf"\b{kind}\s+(?P<names>[A-Za-z_$][A-Za-z0-9_$]*(?:\s*,\s*"
            r"[A-Za-z_$][A-Za-z0-9_$]*)*)"
            r"(?=\s+\b(?:show|hide|as)\b|\s*$)",
            declaration,
        )
        if match is None:
            return None
        return frozenset(
            name.strip() for name in match.group("names").split(",")
        )

    # A binding keeps conditional URI targets in a single namespace.  This is
    # essential: the platform alternatives are not simultaneously imported by
    # a Dart runtime, but static policy must conservatively analyze each one.
    imports_by_library = {}
    exports_by_library = {}
    for library, library_sources in libraries.items():
        imports = []
        exports = []
        for source, path in library_sources:
            if path is None:
                continue
            for match in directive_declaration.finditer(source):
                declaration = match.group("declaration")
                prefix_match = import_prefix.search(declaration)
                prefix = (
                    prefix_match.group("prefix")
                    if match.group("kind") == "import" and prefix_match
                    else None
                )
                targets = tuple(
                    imported_library
                    for uri_match in import_uri.finditer(declaration)
                    if (imported_library := resolve_uri(path, uri_match.group("uri")))
                    in libraries
                )
                if not targets:
                    continue
                binding = (
                    prefix,
                    tuple(dict.fromkeys(targets)),
                    combinator_names(declaration, "show"),
                    combinator_names(declaration, "hide") or frozenset(),
                )
                bindings = imports if match.group("kind") == "import" else exports
                if binding not in bindings:
                    bindings.append(binding)
        imports_by_library[library] = tuple(imports)
        exports_by_library[library] = tuple(exports)

    library_order = {library: index for index, library in enumerate(libraries)}

    def metadata_name(library: str, name: str, root: str) -> str:
        if library == root:
            return name
        return f"__atlas_meta_{library_order[library]}_{name}"

    public_definition_cache = {}
    public_definition_stack = set()

    def visible_definitions(binding):
        _, targets, shown, hidden = binding
        definitions = {}
        for target in targets:
            for name, defining_libraries in public_definitions(target).items():
                if (shown is not None and name not in shown) or name in hidden:
                    continue
                definitions.setdefault(name, [])
                for defining_library in defining_libraries:
                    if defining_library not in definitions[name]:
                        definitions[name].append(defining_library)
        return {
            name: tuple(defining_libraries)
            for name, defining_libraries in definitions.items()
        }

    def public_definitions(library: str):
        if library in public_definition_cache:
            return public_definition_cache[library]
        if library in public_definition_stack:
            return {}
        public_definition_stack.add(library)
        definitions = {
            name: (library,) for name in declared_names[library]
        }
        for binding in exports_by_library[library]:
            for name, defining_libraries in visible_definitions(binding).items():
                definitions.setdefault(name, ())
                definitions[name] = tuple(
                    dict.fromkeys((*definitions[name], *defining_libraries))
                )
        public_definition_stack.remove(library)
        public_definition_cache[library] = definitions
        return definitions

    def metadata_libraries(library: str) -> tuple[str, ...]:
        discovered = []
        pending = [library]
        seen = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            discovered.append(current)
            for binding in (*imports_by_library[current], *exports_by_library[current]):
                for imported_library in binding[1]:
                    if imported_library not in seen:
                        pending.append(imported_library)
        return tuple(discovered)

    def alternative_aliases(root: str) -> dict[tuple[str, str], str]:
        aliases = {}
        for index, binding in enumerate(imports_by_library[root]):
            if len(binding[1]) < 2:
                continue
            alternatives = []
            for target in binding[1]:
                alternatives.append(
                    visible_definitions(
                        (binding[0], (target,), binding[2], binding[3])
                    )
                )
            for name in set.intersection(*(set(item) for item in alternatives)):
                defining_libraries = tuple(
                    dict.fromkeys(
                        defining_library
                        for definitions in alternatives
                        for defining_library in definitions[name]
                    )
                )
                if len(defining_libraries) < 2:
                    continue
                alias = f"__atlas_alt_{library_order[root]}_{index}_{name}"
                for defining_library in defining_libraries:
                    aliases[(defining_library, name)] = alias
        return aliases

    def metadata_source_for(
        library: str, root: str, aliases: dict[tuple[str, str], str]
    ) -> str:
        masked = _mask_dart_non_code("\n".join(
            source for source, _ in libraries[library]
        ))
        imported_by_prefix = {}
        unprefixed_names = {}
        for binding in imports_by_library[library]:
            prefix, _, _, _ = binding
            definitions = visible_definitions(binding)
            if prefix is not None:
                for name, defining_libraries in definitions.items():
                    imported_by_prefix.setdefault((prefix, name), []).extend(
                        defining_libraries
                    )
                continue
            for name, defining_libraries in definitions.items():
                unprefixed_names.setdefault(name, []).extend(defining_libraries)

        def renamed(defining_library: str, name: str) -> str:
            return aliases.get(
                (defining_library, name),
                metadata_name(defining_library, name, root),
            )

        for (prefix, name), imported_libraries in imported_by_prefix.items():
            replacements = {
                renamed(imported_library, name) for imported_library in imported_libraries
            }
            if len(replacements) != 1:
                continue
            masked = re.sub(
                rf"(?<![A-Za-z0-9_$]){re.escape(prefix)}\s*\.\s*"
                rf"{re.escape(name)}(?![A-Za-z0-9_$])",
                replacements.pop(),
                masked,
            )
        for name, imported_libraries in unprefixed_names.items():
            replacements = {
                renamed(imported_library, name) for imported_library in imported_libraries
            }
            if name in declared_names[library] or len(replacements) != 1:
                continue
            masked = re.sub(
                rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])",
                replacements.pop(),
                masked,
            )
        for name in declared_names[library]:
            masked = re.sub(
                rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])",
                renamed(library, name),
                masked,
            )
        return masked

    for library, library_sources in libraries.items():
        combined = "\n".join(source for source, _ in library_sources)
        aliases = alternative_aliases(library)
        metadata_source = "\n".join(
            metadata_source_for(metadata_library, library, aliases)
            for metadata_library in metadata_libraries(library)
        )
        execution_source = metadata_source_for(library, library, aliases)
        state_class_names = _state_class_names((metadata_source,))
        lifecycle_class_methods = _flutter_lifecycle_class_methods((metadata_source,))
        if _has_automatic_operation(
            execution_source,
            state_class_names,
            lifecycle_class_methods,
            metadata_source,
        ):
            return True
    return False


def _production_target_scan(
    sources: tuple[str, ...], source_paths: tuple[Path, ...] | None = None
) -> bool:
    return _sources_have_automatic_operation(
        sources, source_paths=source_paths
    )


def _state_fixture(source: str) -> str:
    return (
        "class PolicyState extends State<PolicyWidget> {\n"
        + source
        + "\n}"
    )


multiline_init_state_samples = (
    (
        """void initState() {
  super.initState();
  startPairing(
    invitation,
  );
}""",
        True,
    ),
    (
        """void initState() =>
  importEncryptedBackup(
    document,
  );""",
        True,
    ),
    (
        """void initState() async {
  await startPairing(invitation);
}""",
        True,
    ),
    (
        """void initState() async {
  await controller.createDeviceIdentity();
}""",
        True,
    ),
    (
        """void initState() {
  if (mounted) {
    exportEncryptedBackup(
      document,
    );
  }
}""",
        True,
    ),
    (
        """void initState() {
  // startPairing(invitation);
  final label = 'exportVault()';
  _ownedPairingOwner = assembly.pairingOwner;
  super.initState();
}""",
        False,
    ),
)
if any(
    _has_automatic_operation(_state_fixture(source)) is not expected
    for source, expected in multiline_init_state_samples
):
    raise SystemExit("Dart lifecycle-body source-guard self-test failed.")

interpolation_init_state_samples = (
    (
        """void initState() {
  final message = '${startPairing()}';
}""",
        True,
    ),
    (
        """void initState() {
  final message = "${controller.importEncryptedBackup()}";
}""",
        True,
    ),
    (
        """void initState() {
  final message = r'${startPairing()}';
}""",
        False,
    ),
)
if any(
    _has_automatic_operation(_state_fixture(source)) is not expected
    for source, expected in interpolation_init_state_samples
):
    raise SystemExit("Dart interpolation source-guard self-test failed.")

tear_off_init_state_samples = (
    """void initState() {
  Future.microtask(startPairing);
}""",
    """void initState() {
  scheduleMicrotask(
    controller.importEncryptedBackup,
  );
}""",
    """void initState() {
  controller.startPairing.call();
}""",
    """void initState() {
  controller.importEncryptedBackup?.call();
}""",
)
if not all(
    _has_automatic_operation(_state_fixture(source))
    for source in tear_off_init_state_samples
):
    raise SystemExit("Dart lifecycle-body tear-off self-test failed.")

assigned_tear_off_init_state_samples = (
    (
        """void initState() {
  final callback = startPairing;
  Future.microtask(callback);
}""",
        True,
    ),
    (
        """void initState() {
  final first = controller.importEncryptedBackup;
  final callback = first;
  scheduleMicrotask(callback);
}""",
        True,
    ),
    (
        """void initState() {
  final callback = controller.exportEncryptedBackup;
  callback.call();
}""",
        True,
    ),
    (
        """void initState() {
  final callback = controller.importEncryptedBackup;
  callback?.call();
}""",
        True,
    ),
    (
        """void initState() {
  final callback = controller?.startPairing;
  callback?.call();
}""",
        True,
    ),
    (
        """void initState() {
  final callback = controller!.importEncryptedBackup;
  callback();
}""",
        True,
    ),
    (
        """void initState() {
  final callback = controller?.createDeviceIdentity;
  callback?.call();
}""",
        True,
    ),
    (
        """void initState() {
  final first = controller!.createDeviceIdentity;
  final second = first;
  Future.microtask(second);
}""",
        True,
    ),
    (
        """void initState() {
  final callback = enabled ? controller.startPairing : noop;
  callback();
}""",
        True,
    ),
    (
        """void initState() {
  final callback = enabled ? noop : controller.importEncryptedBackup;
  Future.microtask(callback);
}""",
        True,
    ),
    (
        """void initState() {
  final callback = controller?.createDeviceIdentity ?? fallback;
  callback();
}""",
        True,
    ),
    (
        """void initState() {
  final callback = custody.createPrimaryIdentity;
  callback();
}""",
        True,
    ),
    (
        """void initState() {
  Future.microtask(generateAtlasVaultDeviceIdentity);
}""",
        True,
    ),
    (
        """void initState() {
  final callback = fallback ?? controller.startPairing;
  Future.microtask(callback);
}""",
        True,
    ),
    (
        """void initState() {
  final owner = assembly.pairingOwner;
  _ownedPairingOwner = owner;
}""",
        False,
    ),
    (
        """void initState() {
  _ownedDeviceIdentityOwner = assembly.deviceIdentityOwner;
}""",
        False,
    ),
    (
        """void initState() {
  (enabled ? controller.startPairing : noop)();
}""",
        True,
    ),
    (
        """void initState() {
  final callbacks = [controller.startPairing];
  callbacks.first();
}""",
        True,
    ),
    (
        """void initState() {
  registerOwner(assembly.pairingOwner);
}""",
        False,
    ),
)
if any(
    _has_automatic_operation(_state_fixture(source)) is not expected
    for source, expected in assigned_tear_off_init_state_samples
):
    raise SystemExit("Dart assigned tear-off self-test failed.")

if not _has_automatic_operation(
    _state_fixture("void didChangeDependencies() { controller.startPairing(); }")
):
    raise SystemExit("Dart automatic-lifecycle self-test failed.")
if not _has_automatic_operation(
    _state_fixture(
        "void didChangeAppLifecycleState(state) { controller.startPairing(); }"
    )
):
    raise SystemExit("Dart observer-lifecycle self-test failed.")

build_execution_samples = (
    (
        """Widget build(context) {
  return ActionButton(action: owner.createDeviceIdentity);
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(onPressed: () => owner.createDeviceIdentity());
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(onPressed: () {
    owner.createDeviceIdentity();
  });
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(
    onPressed: busy ? null : () => owner.prepareRecoveryImport(),
  );
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(
    onPressed: enabled ? () => owner.startPairing() : null,
  );
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(
    onPressed: () => runAfterAudit(audit(), owner.startPairing()),
  );
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(
    onPressed: () => Future.microtask(controller.startPairing),
  );
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(
    onPressed: () {
      Future.microtask(controller.startPairing);
    },
  );
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(
    onPressed: () async {
      await owner.startPairing();
    },
  );
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(
    onPressed:
        enabled
            ? () async {
                await owner.startPairing();
              }
            : null,
  );
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(onTap: () async { await owner.startPairing(); });
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(onLongPress: () async { await owner.startPairing(); });
}""",
        False,
    ),
    (
        """Widget build(context) {
  return ActionButton(onSubmitted: (value) async { await owner.startPairing(); });
}""",
        False,
    ),
    (
        """Widget build(context) => (() {
  noop();
  controller.startPairing();
  return panel;
})();""",
        True,
    ),
    (
        """Widget build(context) {
  if (enabled) {
    controller.startPairing();
  }
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  return Builder(builder: (_) {
    controller.startPairing();
    return panel;
  });
}""",
        True,
    ),
    (
        """Widget build(context) {
  return MaterialApp(onGenerateRoute: (_) {
    controller.startPairing();
    return null;
  });
}""",
        True,
    ),
    (
        """Widget build(context) {
  final callback = owner.startPairing;
  return ActionButton(onPressed: callback);
}""",
        False,
    ),
    (
        """Widget build(context) {
  return Panel(owner: assembly.pairingOwner);
}""",
        False,
    ),
    ("Widget build(context) { controller.startPairing(); return panel; }", True),
    ("Widget build(context) { controller.startPairing.call(); return panel; }", True),
    (
        """Widget build(context) {
  final callback = controller.startPairing;
  callback();
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  Future.microtask(controller.startPairing);
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  scheduleMicrotask(() => controller.startPairing());
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  Future.sync(() => controller.startPairing());
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  Timer.run(() => controller.startPairing());
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  WidgetsBinding.instance.addPostFrameCallback((_) => controller.startPairing());
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  (() => controller.startPairing())();
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  ((_) => controller.startPairing())(null);
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  (() {
    controller.startPairing();
  })();
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  Future.delayed(Duration.zero, owner.createDeviceIdentity);
  Future(owner.createDeviceIdentity);
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  Future<void>.delayed(Duration.zero, controller.startPairing);
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  (() async {
    await owner.startPairing();
  })();
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  return MaterialApp(
    onGenerateRoute: (_) {
      Future.microtask(controller.startPairing);
      return route;
    },
  );
}""",
        True,
    ),
    (
        """Widget build(context) {
  (controller.startPairing)();
  (controller.createDeviceIdentity).call();
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  Function.apply(controller.startPairing, const []);
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  final callbacks = {'pair': controller.startPairing};
  callbacks['pair']();
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  final callbacks = {controller.createDeviceIdentity};
  callbacks.first();
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  final callbacks = [controller.startPairing];
  callbacks[index]();
  return panel;
}""",
        True,
    ),
    (
        """Widget build(context) {
  final callbacks = <String, void Function()>{
    'pair': controller.startPairing,
  };
  callbacks[index]!();
  return panel;
}""",
        True,
    ),
)
for source, expected in build_execution_samples:
    actual = _has_automatic_operation(_state_fixture(source))
    if actual is not expected:
        raise SystemExit(
            "Dart build execution-policy self-test failed: "
            f"{actual=} {expected=} {source=!r}."
        )

if not _has_automatic_operation(
    _state_fixture(
        """void _confirmRecoverySetup() {
  widget.owner.confirmRecoverySetup();
}
void initState() {
  _confirmRecoverySetup();
}"""
    )
):
    raise SystemExit("Dart local-wrapper lifecycle self-test failed.")

if not _has_automatic_operation(
    _state_fixture("void initState() { widget.owner.beginRecoverySetup(); }")
):
    raise SystemExit("Dart recovery-setup lifecycle self-test failed.")

for migration_operation in (
    "prepareEncryptedMigration",
    "finalizeMigration",
    "resumeMigration",
    "activateEncryptedPrivateData",
    "activateEncryptedPrivateState",
):
    if not _has_automatic_operation(
        _state_fixture(f"void initState() {{ owner.{migration_operation}(); }}")
    ):
        raise SystemExit("Dart migration lifecycle self-test failed.")

if not _has_automatic_operation(
    _state_fixture(
        """Future<void> _run<T>(Future<T> Function() operation) async {
  controller.startPairing();
}
void initState() {
  _run(noop);
}"""
    )
):
    raise SystemExit("Dart generic-wrapper lifecycle self-test failed.")

if not _has_automatic_operation(
    _state_fixture(
        """void _run<T>() {
  controller.startPairing();
}
void initState() {
  _run<void>();
}"""
    )
):
    raise SystemExit("Dart typed-wrapper lifecycle self-test failed.")

if not _has_automatic_operation(
    _state_fixture(
        """void _run() {
  final callback = controller.startPairing;
  Future.microtask(callback);
}
void initState() {
  _run();
}"""
    )
):
    raise SystemExit("Dart scheduled-wrapper lifecycle self-test failed.")

if not _has_automatic_operation(
    _state_fixture(
        """void _run() => (() {
  noop();
  controller.startPairing();
})();
void initState() {
  _run();
}"""
    )
):
    raise SystemExit("Dart arrow-wrapper lifecycle self-test failed.")

if not _has_automatic_operation(
    """class PairingWidget extends StatefulWidget {
  PairingState createState() {
    controller.startPairing();
    return PairingState();
  }
}
class PairingState extends State<PairingWidget> {
  PairingState() {
    controller.createDeviceIdentity();
  }
  final prepared = controller.importEncryptedBackup();
}"""
):
    raise SystemExit("Dart construction lifecycle self-test failed.")

if not _has_automatic_operation(
    """class BaseState<T> extends State<T> {}
class PairingState extends BaseState<PairingWidget> {
  PairingState() {
    controller.startPairing();
  }
}"""
):
    raise SystemExit("Dart indirect-State construction self-test failed.")

if not _has_automatic_operation(
    """class BaseState<T extends StatefulWidget> extends State<T> {
  void initState() {
    controller.startPairing();
  }
}"""
):
    raise SystemExit("Dart bounded-State type-parameter self-test failed.")

if not _has_automatic_operation(
    """class BaseState<T> extends State<T> {
  void _run() {
    controller.startPairing();
  }
}
class PairingState extends BaseState<PairingWidget> {
  void initState() {
    _run();
  }
}"""
):
    raise SystemExit("Dart inherited-wrapper lifecycle self-test failed.")

if not _sources_have_automatic_operation(
    (
        """library pairing_policy;
class BaseState<T> extends State<T> {
  void _run() {
    controller.startPairing();
  }
}""",
        """part of pairing_policy;
class PairingState extends BaseState<PairingWidget> {
  void initState() {
    _run();
  }
}""",
    ),
    source_paths=(Path("lib/pairing.dart"), Path("lib/pairing_part.dart")),
):
    raise SystemExit("Dart cross-file inherited-wrapper self-test failed.")

if _sources_have_automatic_operation(
    (
        """class SharedName extends State<PolicyWidget> {}""",
        """class SharedName {
  void dispose() {
    controller.startPairing();
  }
}""",
    )
):
    raise SystemExit("Dart library-scoped lifecycle ownership self-test failed.")

if not _has_automatic_operation(
    """mixin PairingLifecycle<T extends StatefulWidget> on State<T> {
  void initState() {
    controller.startPairing();
  }
}
class PairingState extends State<PairingWidget>
    with PairingLifecycle<PairingWidget> {}"""
):
    raise SystemExit("Dart State-mixin lifecycle self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends widgets.State<PairingWidget> {
  void initState() {
    controller.startPairing();
  }
}"""
):
    raise SystemExit("Dart prefixed-State lifecycle self-test failed.")

if not _sources_have_automatic_operation(
    (
        """library pairing_construction;
class BaseState<T> extends State<T> {}""",
        """part of pairing_construction;
class PairingState extends BaseState<PairingWidget> {
  PairingState() {
    controller.startPairing();
  }
}""",
    )
):
    raise SystemExit("Dart cross-file State construction self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  PairingState()
      : callbacks = <String, VoidCallback>{'pair': noop} {
    controller.startPairing();
  }
}"""
):
    raise SystemExit("Dart typed-collection initializer self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  PairingState() : callback = () { noop(); } {
    globalOwner.startPairing();
  }
}"""
):
    raise SystemExit("Dart callback-initializer constructor self-test failed.")

for teardown_hook in ("activate", "deactivate", "dispose"):
    if not _has_automatic_operation(
        _state_fixture(
            f"void {teardown_hook}() {{ controller.startPairing(); }}"
        )
    ):
        raise SystemExit("Dart teardown lifecycle self-test failed.")

named_state_constructor_samples = (
    (
        """class PairingState extends State<PairingWidget> {
  PairingState.named() {
    controller.startPairing();
  }
}""",
        True,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  PairingState._private() {
    controller.createDeviceIdentity();
  }
}""",
        True,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  const PairingState.named();
  PairingState.namedWithInitializer()
      : callback = controller.startPairing {
    callback();
  }
}""",
        True,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  PairingState.named(Future<void> Function() callback) {
    controller.startPairing();
  }
}""",
        True,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  PairingState.named() => controller.startPairing();
}""",
        True,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  factory PairingState.named() {
    controller.startPairing();
    return PairingState();
  }
}""",
        True,
    ),
    (
        """class NotAState {
  NotAState.named() {
    controller.startPairing();
  }
}""",
        False,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  void named() {
    controller.startPairing();
  }
}""",
        False,
    ),
)
if any(
    _has_automatic_operation(source) is not expected
    for source, expected in named_state_constructor_samples
):
    raise SystemExit("Dart named State-constructor source-guard self-test failed.")

state_field_initializer_samples = (
    (
        """class PairingState extends State<PairingWidget> {
  final token = (() => controller.startPairing())();
}""",
        True,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  final callback = () => controller.startPairing();
}""",
        False,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  final callback = value => controller.startPairing();
}""",
        False,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  final token = (() => controller.startPairing()).call();
}""",
        True,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  final token = (() => controller.startPairing())?.call();
}""",
        True,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  final token = Function.apply(() => controller.startPairing(), const []);
}""",
        True,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  final callbacks = [() => controller.startPairing(), (() => noop())()];
}""",
        False,
    ),
    (
        """class PairingState extends State<PairingWidget> {
  final values = [globalOwner.startPairing(), (() => noop())()];
}""",
        True,
    ),
)
if any(
    _has_automatic_operation(source) is not expected
    for source, expected in state_field_initializer_samples
):
    raise SystemExit("Dart State-field initializer source-guard self-test failed.")

if _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  PairingState() : callback = () => controller.startPairing();
}"""
):
    raise SystemExit("Dart arrow-callback constructor self-test failed.")

if _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  void _onPressed() {
    flag = true;
    controller.startPairing();
  }
  final count = 0;
}"""
):
    raise SystemExit("Dart instance-method field scan self-test failed.")

if _has_automatic_operation(
    """class PairingController {
  void dispose() {
    controller.startPairing();
  }
  Widget build(context) {
    return panel;
  }
}"""
):
    raise SystemExit("Dart non-Flutter lifecycle self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  void _run() {
    controller.startPairing();
  }
  void initState() {
    _run();
  }
}
class Helper {
  void _run() {
    noop();
  }
}"""
):
    raise SystemExit("Dart owner-scoped wrapper self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  void runPairing() {
    controller.startPairing();
  }
  void initState() {
    runPairing();
  }
}"""
):
    raise SystemExit("Dart public-wrapper lifecycle self-test failed.")

if _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  late final token = controller.startPairing();
}"""
):
    raise SystemExit("Dart lazy State-field initializer self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  late final token = controller.startPairing();
  Widget build(context) {
    return Text(token);
  }
}"""
):
    raise SystemExit("Dart lazy State-field read self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  static final token = controller.startPairing();
  Widget build(context) {
    return Text(token);
  }
}"""
):
    raise SystemExit("Dart static State-field read self-test failed.")

if not _has_automatic_operation(
    """class PairingHelper {
  const PairingHelper();
  void run() {
    controller.startPairing();
  }
}
class PairingState extends State<PairingWidget> {
  void initState() {
    final helper = const PairingHelper();
    helper.run();
  }
}"""
):
    raise SystemExit("Dart const helper-wrapper self-test failed.")

if not _has_automatic_operation(
    """class PairingBaseState extends State<PairingWidget> {
  late final token = controller.startPairing();
}
class PairingState extends PairingBaseState {
  Widget build(context) {
    return Text(token);
  }
}"""
):
    raise SystemExit("Dart inherited lazy State-field self-test failed.")

if not _has_automatic_operation(
    """mixin PairingFields on State<PairingWidget> {
  late final token = controller.startPairing();
}
class PairingState extends State<PairingWidget> with PairingFields {
  Widget build(context) {
    return Text(token);
  }
}"""
):
    raise SystemExit("Dart mixin lazy State-field self-test failed.")

if not _production_target_scan(
    (
        """library pairing_construction;
class BaseState<T> extends State<T> {
  void _run() {
    controller.startPairing();
  }
}""",
        """part of pairing_construction;
class PairingState extends BaseState<PairingWidget> {
  void initState() {
    _run();
  }
}""",
    )
):
    raise SystemExit("Dart production cross-source scanner self-test failed.")

if not _sources_have_automatic_operation(
    (
        """library pairing;
class BaseState<T> extends State<T> {
  void _run() {
    controller.startPairing();
  }
}""",
        """part of 'pairing.dart';
class PairingState extends BaseState<PairingWidget> {
  void initState() {
    _run();
  }
}""",
    ),
    source_paths=(Path("lib/pairing.dart"), Path("lib/pairing_part.dart")),
):
    raise SystemExit("Dart URI part ownership self-test failed.")

if not _production_target_scan(
    (
        """class BaseState<T> extends State<T> {}""",
        """import 'base.dart';
class PairingState extends BaseState<PairingWidget> {
  void initState() {
    controller.startPairing();
  }
}""",
    ),
    source_paths=(Path("lib/base.dart"), Path("lib/pairing.dart")),
):
    raise SystemExit("Dart imported-State ancestry self-test failed.")

if not _production_target_scan(
    (
        """class BaseState<T> extends State<T> {
  void runPairing() {
    controller.startPairing();
  }
}""",
        """import 'base.dart';
class PairingState extends BaseState<PairingWidget> {
  void initState() {
    runPairing();
  }
}""",
    ),
    source_paths=(Path("lib/base.dart"), Path("lib/pairing.dart")),
):
    raise SystemExit("Dart imported-wrapper lifecycle self-test failed.")

if not _production_target_scan(
    (
        """class StubState {}""",
        """class IoState<T> extends State<T> {}""",
        """import 'stub.dart' if (dart.library.io) 'base_io.dart';
class PairingState extends IoState<PairingWidget> {
  void initState() {
    controller.startPairing();
  }
}""",
    ),
    source_paths=(
        Path("lib/stub.dart"),
        Path("lib/base_io.dart"),
        Path("lib/pairing.dart"),
    ),
):
    raise SystemExit("Dart conditional-import State ancestry self-test failed.")

if _production_target_scan(
    (
        """class SharedName extends State<PolicyWidget> {}""",
        """class SharedName {}""",
        """import 'ui.dart' as ui;
import 'service.dart' as svc;
class ServiceOwner extends svc.SharedName {
  void dispose() {
    controller.startPairing();
  }
}
class UiOwner extends ui.SharedName {}""",
    ),
    source_paths=(
        Path("lib/ui.dart"),
        Path("lib/service.dart"),
        Path("lib/owner.dart"),
    ),
):
    raise SystemExit("Dart imported-prefix lifecycle ownership self-test failed.")

if not _production_target_scan(
    (
        """class BaseState<T> extends State<T> {}""",
        """import 'package:atlas/base.dart';
class PairingState extends BaseState<PairingWidget> {
  void initState() {
    controller.startPairing();
  }
}""",
    ),
    source_paths=(Path("lib/base.dart"), Path("lib/pairing.dart")),
):
    raise SystemExit("Dart package-import State ancestry self-test failed.")

if not _production_target_scan(
    (
        """class BaseState<T> extends State<T> {}""",
        """export 'base.dart';""",
        """import 'barrel.dart';
class PairingState extends BaseState<PairingWidget> {
  void initState() {
    controller.startPairing();
  }
}""",
    ),
    source_paths=(
        Path("lib/base.dart"),
        Path("lib/barrel.dart"),
        Path("lib/pairing.dart"),
    ),
):
    raise SystemExit("Dart re-export State ancestry self-test failed.")

if not _production_target_scan(
    (
        """class BaseState<T> extends State<T> {}""",
        """class BaseState {}""",
        """import 'state.dart' show BaseState;
import 'other.dart' hide BaseState;
class PairingState extends BaseState<PairingWidget> {
  void initState() {
    controller.startPairing();
  }
}""",
    ),
    source_paths=(
        Path("lib/state.dart"),
        Path("lib/other.dart"),
        Path("lib/pairing.dart"),
    ),
):
    raise SystemExit("Dart import-combinator State ancestry self-test failed.")

if not _production_target_scan(
    (
        """class PlatformState<T> {}""",
        """class PlatformState<T> extends State<T> {}""",
        """import 'stub.dart' if (dart.library.io) 'io.dart';
class PairingState extends PlatformState<PairingWidget> {
  void initState() {
    controller.startPairing();
  }
}""",
    ),
    source_paths=(
        Path("lib/stub.dart"),
        Path("lib/io.dart"),
        Path("lib/pairing.dart"),
    ),
):
    raise SystemExit("Dart conditional-import namespace self-test failed.")

if not _production_target_scan(
    (
        """void runPairing() {
  controller.startPairing();
}
class PairingState extends State<PairingWidget> {
  void initState() {
    runPairing();
  }
}""",
    ),
    source_paths=(Path("lib/pairing.dart"),),
):
    raise SystemExit("Dart top-level wrapper lifecycle self-test failed.")

if not _production_target_scan(
    (
        """extension PairingStateActions on PairingState {
  void runPairing() {
    controller.startPairing();
  }
}
class PairingState extends State<PairingWidget> {
  void initState() {
    runPairing();
  }
}""",
    ),
    source_paths=(Path("lib/pairing.dart"),),
):
    raise SystemExit("Dart extension-wrapper lifecycle self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  bool get shouldRun {
    controller.startPairing();
    return true;
  }
  void initState() {
    if (shouldRun) {}
  }
}"""
):
    raise SystemExit("Dart getter-wrapper lifecycle self-test failed.")

if _production_target_scan(
    (
        """void runPairing() {}""",
        """void runPairing() {
  controller.startPairing();
}""",
        """import 'safe.dart';
import 'sensitive.dart' as remote;
class PairingState extends State<PairingWidget> {
  void initState() {
    runPairing();
  }
}""",
    ),
    source_paths=(
        Path("lib/safe.dart"),
        Path("lib/sensitive.dart"),
        Path("lib/pairing.dart"),
    ),
):
    raise SystemExit("Dart top-level wrapper namespace self-test failed.")

for observer_callback in (
    "didChangeAccessibilityFeatures",
    "didChangeLocales",
    "didChangeMetrics",
    "didChangePlatformBrightness",
    "didChangeTextScaleFactor",
    "didHaveMemoryPressure",
    "didPushRoute",
    "didPopRoute",
    "didRequestAppExit",
    "didChangeViewFocus",
    "didPushRouteInformation",
    "handleStartBackGesture",
    "handleUpdateBackGestureProgress",
    "handleCommitBackGesture",
    "handleCancelBackGesture",
):
    if not _has_automatic_operation(
        f"""class PairingObserver with WidgetsBindingObserver {{
  void {observer_callback}() {{
    controller.startPairing();
  }}
}}"""
    ):
        raise SystemExit("Dart WidgetsBindingObserver self-test failed.")

for widget_construction_source in (
    """class PairingWidget extends StatelessWidget {
  final token = controller.startPairing();
  Widget build(context) => panel;
}""",
    """class PairingWidget extends StatefulWidget {
  PairingWidget() {
    controller.startPairing();
  }
  PairingState createState() => PairingState();
}""",
):
    if not _has_automatic_operation(widget_construction_source):
        raise SystemExit("Dart Widget construction self-test failed.")

if not _has_automatic_operation(
    """mixin PairingFields on StatelessWidget {
  final token = controller.startPairing();
}
class PairingWidget extends StatelessWidget with PairingFields {
  Widget build(context) => panel;
}"""
):
    raise SystemExit("Dart Widget-mixin construction self-test failed.")

if not _production_target_scan(
    (
        """mixin PairingFields on State<PairingWidget> {
  final token = controller.startPairing();
}""",
        """import 'fields.dart';
class PairingState extends State<PairingWidget> with PairingFields {}""",
    ),
    source_paths=(Path("lib/fields.dart"), Path("lib/pairing.dart")),
):
    raise SystemExit("Dart imported mixin execution self-test failed.")

if not _has_automatic_operation(
    """mixin class PairingFields on State<PairingWidget> {
  final token = controller.startPairing();
}
class PairingState extends State<PairingWidget> with PairingFields {}"""
):
    raise SystemExit("Dart mixin-class execution self-test failed.")

if not _has_automatic_operation(
    """mixin PairingLifecycle on State<PairingWidget> {
  void initState() {
    controller.startPairing();
  }
}
class PairingState extends State<PairingWidget> with PairingLifecycle {}
class Harmless {}"""
):
    raise SystemExit("Dart mixin lifecycle-heritage self-test failed.")

if not _production_target_scan(
    (
        """class BaseWidget extends StatelessWidget {}""",
        """import 'base.dart';
class PairingWidget extends BaseWidget {
  final token = controller.startPairing();
  Widget build(context) => panel;
}""",
    ),
    source_paths=(Path("lib/base.dart"), Path("lib/pairing.dart")),
):
    raise SystemExit("Dart imported Widget ancestry self-test failed.")

if not _production_target_scan(
    (
        """void main() {
  controller.startPairing();
}""",
    ),
    source_paths=(Path("lib/main.dart"),),
):
    raise SystemExit("Dart main entrypoint self-test failed.")

if not _has_automatic_operation(
    """class PairingHelper {
  void run() {
    controller.startPairing();
  }
}
class PairingState extends State<PairingWidget> {
  void initState() {
    PairingHelper().run();
  }
}"""
):
    raise SystemExit("Dart receiver-owned wrapper self-test failed.")

if not _has_automatic_operation(
    """class PairingHelper {
  void run() {
    controller.startPairing();
  }
}
class PairingState extends State<PairingWidget> {
  void initState() {
    final helper = PairingHelper();
    helper.run();
  }
}"""
):
    raise SystemExit("Dart stored receiver-wrapper self-test failed.")

if not _has_automatic_operation(
    """class PairingHelper {
  PairingHelper() {
    controller.startPairing();
  }
}
class PairingState extends State<PairingWidget> {
  void initState() {
    PairingHelper();
  }
}"""
):
    raise SystemExit("Dart receiver-owned constructor self-test failed.")

if not _has_automatic_operation(
    """class PairingHelper {
  PairingHelper.secure() {
    controller.startPairing();
  }
}
class PairingState extends State<PairingWidget> {
  void initState() {
    PairingHelper.secure();
  }
}"""
):
    raise SystemExit("Dart named receiver-owned constructor self-test failed.")

if not _has_automatic_operation(
    """mixin PairingLifecycle on State<PairingWidget> {
  void initState() {
    runPairing();
  }
  void runPairing() {
    controller.startPairing();
  }
}
class PairingState extends State<PairingWidget> with PairingLifecycle {}"""
):
    raise SystemExit("Dart mixin wrapper lifecycle self-test failed.")

if not _has_automatic_operation(
    """final token = controller.startPairing();
void main() {
  token;
}"""
):
    raise SystemExit("Dart top-level lazy main self-test failed.")

if not _has_automatic_operation(
    """late final token = controller.startPairing();
void main() {
  token;
}"""
):
    raise SystemExit("Dart late top-level lazy main self-test failed.")

if not _has_automatic_operation(
    """final token = controller.startPairing();
class PairingState extends State<PairingWidget> {
  Widget build(context) {
    token;
    return panel;
  }
}"""
):
    raise SystemExit("Dart top-level lazy lifecycle self-test failed.")

if not _has_automatic_operation(
    """var token = controller.startPairing();
void main() {
  token;
}"""
):
    raise SystemExit("Dart mutable top-level lazy main self-test failed.")

if not _has_automatic_operation(
    """Object token = controller.startPairing();
void main() {
  token;
}"""
):
    raise SystemExit("Dart typed top-level lazy main self-test failed.")

if not _has_automatic_operation(
    """class PairingWidget extends StatelessWidget {
  Element createElement() {
    controller.startPairing();
    return super.createElement();
  }
  Widget build(context) => panel;
}"""
):
    raise SystemExit("Dart Widget createElement lifecycle self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  void initState() {
    final coordinator = AtlasVaultPlaintextMigrationCoordinator();
    coordinator.prepare();
    coordinator.resumePreparation();
    coordinator.discardPrepared();
    coordinator.finalizeAndActivate();
    coordinator.activateSelected();
  }
}"""
):
    raise SystemExit("Dart migration coordinator lifecycle self-test failed.")

if _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  void initState() {
    configure(startPairing: false);
  }
}"""
):
    raise SystemExit("Dart named argument label lifecycle self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  void initState() {
    transport.pickEncryptedExport();
    transport.saveEncryptedExport();
  }
}"""
):
    raise SystemExit("Dart document transport lifecycle self-test failed.")

if _has_automatic_operation(
    """class SafeHelper {
  void run() {}
}
class PairingState extends State<PairingWidget> {
  void initState() {
    SafeHelper().run();
  }
}"""
):
    raise SystemExit("Dart receiver-owned wrapper isolation self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  final token = switch (enabled) {
    _ => controller.startPairing(),
  };
}"""
):
    raise SystemExit("Dart switch-arm State-field self-test failed.")

if _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  static final token = controller.startPairing();
}"""
):
    raise SystemExit("Dart static State-field initializer self-test failed.")

if not _has_automatic_operation(
    """mixin PairingFields<T extends StatefulWidget> on State<T> {
  final token = controller.startPairing();
}
class PairingState extends State<PairingWidget>
    with PairingFields<PairingWidget> {}"""
):
    raise SystemExit("Dart State-mixin field initializer self-test failed.")

if not _has_automatic_operation(
    """mixin PairingLifecycle<T extends StatefulWidget> on State<T> {
  void initState() {
    controller.startPairing();
  }
}
class PairingState = State<PairingWidget> with PairingLifecycle<PairingWidget>;"""
):
    raise SystemExit("Dart State mixin-application alias self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  final values = [1].map((_) => controller.startPairing()).toList();
}"""
):
    raise SystemExit("Dart eager collection callback self-test failed.")

if not _has_automatic_operation(
    _state_fixture(
        """Widget build(context) {
  invoke(onTap: () => controller.startPairing());
  return panel;
}"""
    )
):
    raise SystemExit("Dart immediate named-callback self-test failed.")

if not _has_automatic_operation(
    _state_fixture(
        """void initState() {
  createAtlasVaultPairingOffer();
}"""
    )
):
    raise SystemExit("Dart public pairing primitive self-test failed.")

if _has_automatic_operation(
    _state_fixture(
        """Widget _statusContent() {
  return ActionButton(onPressed: owner.prepareEncryptedMigration);
}
Widget build(context) {
  return _statusContent();
}"""
    )
):
    raise SystemExit("Dart passive-wrapper lifecycle self-test failed.")

if not _has_automatic_operation(
    """var first = 0, token = controller.startPairing();
class PairingState extends State<PairingWidget> {
  Widget build(context) {
    token;
    return panel;
  }
}"""
):
    raise SystemExit("Dart multi-declarator top-level lazy self-test failed.")

if not _has_automatic_operation(
    """final trigger = token;
final token = controller.startPairing();
void main() {
  trigger;
}"""
):
    raise SystemExit("Dart chained top-level lazy self-test failed.")

for coordinator_source in (
    """class PairingState extends State<PairingWidget> {
  final AtlasVaultPlaintextMigrationCoordinating coordinator;
  void initState() {
    coordinator.finalizeAndActivate();
  }
}""",
    """class PairingState extends State<PairingWidget> {
  void initState() {
    final AtlasVaultPlaintextMigrationCoordinating coordinator = lookup();
    coordinator.prepare();
  }
}""",
):
    if not _has_automatic_operation(coordinator_source):
        raise SystemExit("Dart typed migration coordinator self-test failed.")

if not _has_automatic_operation(
    """class PairingState extends State<PairingWidget> {
  void reassemble() {
    controller.startPairing();
  }
}"""
):
    raise SystemExit("Dart reassemble lifecycle self-test failed.")

if not _has_automatic_operation(
    """class BaseObserver implements WidgetsBindingObserver {}
class PairingObserver implements BaseObserver {
  void didChangeAppLifecycleState(state) {
    controller.startPairing();
  }
}"""
):
    raise SystemExit("Dart indirect observer lifecycle self-test failed.")

for eager_callback in (
    "[1].any((_) { controller.startPairing(); return true; })",
    "[1].every((_) { controller.startPairing(); return true; })",
    "[1].firstWhere((_) { controller.startPairing(); return true; })",
    "[1].singleWhere((_) { controller.startPairing(); return true; })",
    "[1].sort((_, __) { controller.startPairing(); return 0; })",
):
    if not _has_automatic_operation(
        f"""class PairingState extends State<PairingWidget> {{
  final value = {eager_callback};
}}"""
    ):
        raise SystemExit("Dart eager collection consumer self-test failed.")

flutter_policy_script = (Path("..").resolve().parent / "scripts/ci/atlasvault_flutter.sh")
flutter_policy_source = flutter_policy_script.read_text(encoding="utf-8")
if flutter_policy_source.index("python3 - <<'PY'") > flutter_policy_source.index(
    'flutter test "${focused[@]}"'
):
    raise SystemExit("Dart lifecycle admission ordering self-test failed.")

targets = tuple(sorted(Path("lib").rglob("*.dart")))
target_sources = tuple(path.read_text(encoding="utf-8") for path in targets)
if _production_target_scan(target_sources, source_paths=targets):
    raise SystemExit("Automatic AtlasVault pairing/import/export is not permitted.")
print("Validated Dart lifecycle-body automatic-operation policy.")
PY

forbidden="$(
  { find "$REPO_ROOT" -path "$REPO_ROOT/.git" -prune -o -type f -print |
      LC_ALL=C grep -Ei '\.atlasvault$|\.atlaspair$|identity.*secret|secret.*identity|ephemeral.*private|private.*ephemeral'; } || true
)"
if [[ -n "$forbidden" ]]; then
  printf 'Forbidden AtlasVault artifact found in the repository:\n%s\n' "$forbidden" >&2
  exit 1
fi
