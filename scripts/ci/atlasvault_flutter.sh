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

mapfile -t full_tests < <(
  find test -type f -name '*_test.dart' \
    ! -name 'search_golden_test.dart' \
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
    index = 0

    def blank(start: int, end: int) -> None:
        for offset in range(start, end):
            if masked[offset] != "\n":
                masked[offset] = " "

    while index < length:
        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            end = length if end < 0 else end
            blank(index, end)
            index = end
            continue
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
            continue

        quote_index = index
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
            quote_index += 1
        if source[quote_index] not in "'\"":
            index += 1
            continue

        quote = source[quote_index]
        delimiter = quote * 3 if source.startswith(quote * 3, quote_index) else quote
        start = index
        index = quote_index + len(delimiter)
        while index < length:
            if source.startswith(delimiter, index):
                index += len(delimiter)
                break
            if len(delimiter) == 1 and source[index] == "\\":
                index = min(index + 2, length)
            else:
                index += 1
        blank(start, index)

    return "".join(masked)


def _init_state_bodies(source: str) -> tuple[str, ...]:
    masked = _mask_dart_non_code(source)
    declaration = re.compile(
        r"\b(?:void\s+)?initState\s*\(\s*\)\s*(?P<body>\{|=>)"
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
    r"(?=[A-Za-z_$][A-Za-z0-9_$]*\s*(?:\(|[,)]))"
    r"(?=[A-Za-z0-9_$]*(?:pair|import|export))"
    r"[A-Za-z_$][A-Za-z0-9_$]*\s*(?:\(|(?=[,)]))",
    re.IGNORECASE,
)


def _has_automatic_operation(source: str) -> bool:
    return any(
        operation_reference.search(body) for body in _init_state_bodies(source)
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

tear_off_init_state_samples = (
    """void initState() {
  Future.microtask(startPairing);
}""",
    """void initState() {
  scheduleMicrotask(
    controller.importEncryptedBackup,
  );
}""",
)
if not all(
    _has_automatic_operation(source) for source in tear_off_init_state_samples
):
    raise SystemExit("Dart lifecycle-body tear-off self-test failed.")

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

forbidden="$(find "$REPO_ROOT" -path "$REPO_ROOT/.git" -prune -o -type f \( -name '*.atlasvault' -o -name '*.atlaspair' \) -print)"
if [[ -n "$forbidden" ]]; then
  printf 'Forbidden AtlasVault artifact found in the repository:\n%s\n' "$forbidden" >&2
  exit 1
fi
