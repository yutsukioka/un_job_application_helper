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


def _automatic_lifecycle_bodies(source: str) -> tuple[str, ...]:
    masked = _mask_dart_non_code(source)
    declaration = re.compile(
        r"\b(?:void\s+)?(?:initState|didChangeDependencies|didUpdateWidget|didChangeAppLifecycleState|build)\s*\([^)]*\)\s*(?:async\s*)?(?P<body>\{|=>)"
    )
    bodies = []
    for match in declaration.finditer(masked):
        start = match.start("body")
        if match.group("body") == "=>":
            end = masked.find(";", match.end("body"))
            if end < 0:
                raise SystemExit("Unable to parse an AtlasVault initState body.")
            bodies.append(masked[match.end("body") : end])
            continue

        depth = 0
        for end in range(start, len(masked)):
            if masked[end] == "{":
                depth += 1
            elif masked[end] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(masked[start + 1 : end])
                    break
        else:
            raise SystemExit("Unable to parse an AtlasVault initState body.")
    return tuple(bodies)


operation_reference = re.compile(
    r"(?<![A-Za-z0-9_$])"
    r"(?P<target>[A-Za-z_$][A-Za-z0-9_$]*)\s*"
    r"(?:\(|\??\.\s*call\s*\(|(?=[,:)]))",
)
operation_identifiers = frozenset(
    {
        "startpairing",
        "createdeviceidentity",
        "createprimaryidentity",
        "generateatlasvaultdeviceidentity",
        "createpairingoffer",
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
        "confirmrecoverysetup",
        "prepareexistingrecoveryexport",
        "savepreparedexport",
        "preparerecoveryimport",
        "confirmrecoveryimport",
        "discardpendingimport",
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
    r"(?=\s*(?:[,:;\])]|\?\?|\Z))"
)


def _is_sensitive_operation(identifier: str) -> bool:
    return identifier.casefold() in operation_identifiers


def _operation_aliases(body: str) -> frozenset[str]:
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
                not _is_sensitive_operation(target)
                and target not in aliases
            ):
                continue
            if alias not in aliases:
                aliases.add(alias)
                changed = True
    return frozenset(aliases)


def _alias_is_executed(body: str, alias: str) -> bool:
    usage = re.compile(
        rf"(?<![A-Za-z0-9_$]){re.escape(alias)}\s*"
        r"(?:\[[^\]]+\]|\.\s*(?:first|last|single))?\s*"
        r"(?:\(|\??\.\s*call\s*\(|(?=[,)]))"
    )
    return usage.search(body) is not None


def _has_automatic_operation(source: str) -> bool:
    for body in _automatic_lifecycle_bodies(source):
        if any(
            _is_sensitive_operation(match.group("target"))
            for match in operation_reference.finditer(body)
        ):
            return True
        if any(
            _alias_is_executed(body, alias)
            for alias in _operation_aliases(body)
        ):
            return True
    return False


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
    _has_automatic_operation(source) is not expected
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
    _has_automatic_operation(source) is not expected
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
    _has_automatic_operation(source) for source in tear_off_init_state_samples
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
    _has_automatic_operation(source) is not expected
    for source, expected in assigned_tear_off_init_state_samples
):
    raise SystemExit("Dart assigned tear-off self-test failed.")

if not _has_automatic_operation("void didChangeDependencies() { controller.startPairing(); }"):
    raise SystemExit("Dart automatic-lifecycle self-test failed.")
if not _has_automatic_operation("void didChangeAppLifecycleState(state) { controller.startPairing(); }"):
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
  (() => controller.startPairing())();
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
)
if any(
    _has_automatic_operation(source) is not expected
    for source, expected in build_execution_samples
):
    raise SystemExit("Dart build execution-policy self-test failed.")

targets = tuple(sorted(Path("lib/src/atlas_vault").rglob("*.dart"))) + (
    Path("lib/features/app_shell/atlas_app.dart"),
)
if any(
    _has_automatic_operation(path.read_text(encoding="utf-8"))
    for path in targets
):
    raise SystemExit("Automatic AtlasVault pairing/import/export is not permitted.")
print("Validated Dart lifecycle-body automatic-operation policy.")
PY

forbidden="$(find "$REPO_ROOT" -path "$REPO_ROOT/.git" -prune -o -type f \( -iname '*.atlasvault' -o -iname '*.atlaspair' -o -iname '*identity*secret*' -o -iname '*secret*identity*' -o -iname '*ephemeral*private*' -o -iname '*private*ephemeral*' \) -print)"
if [[ -n "$forbidden" ]]; then
  printf 'Forbidden AtlasVault artifact found in the repository:\n%s\n' "$forbidden" >&2
  exit 1
fi
