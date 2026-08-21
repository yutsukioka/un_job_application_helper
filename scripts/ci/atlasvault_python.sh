#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
readonly SCRIPT_DIR REPO_ROOT

workflows=(
  "$REPO_ROOT/.github/workflows/atlasvault-cross-platform-security.yml"
  "$REPO_ROOT/.github/workflows/atlasvault-platform-integration.yml"
)
for workflow in "${workflows[@]}"; do
  if [[ ! -f "$workflow" ]]; then
    printf 'Required AtlasVault workflow is missing: %s\n' "$workflow" >&2
    exit 1
  fi
done

cd "$REPO_ROOT"

focused_tests=(
  packages/vaultsync/tests/test_device_identity_vectors.py
  packages/vaultsync/tests/test_pairing_vectors.py
  packages/vaultsync/tests/test_key_delivery_vectors.py
  packages/vaultsync/tests/test_pairing_artifact_vectors.py
  packages/vaultsync/tests/test_trusted_device_vectors.py
)
python -m pytest "${focused_tests[@]}"

python -m pytest packages/vaultsync/tests

if [[ ! -f tests/test_job_api_private_access.py ]]; then
  printf 'The secure-local-API admission test is missing.\n' >&2
  exit 1
fi
python -m pytest tests/test_job_api_private_access.py

python - <<'PY'
import json
from pathlib import Path

paths = sorted(Path("contracts/sync/test_vectors").glob("*.json"))
if not paths:
    raise SystemExit("No AtlasVault JSON vectors were found.")
for path in paths:
    with path.open("r", encoding="utf-8") as handle:
        json.load(handle)
print(f"Validated {len(paths)} AtlasVault JSON vectors.")
PY

python - <<'PY'
from pathlib import Path

workflow = Path(
    ".github/workflows/atlasvault-platform-integration.yml"
).read_text(encoding="utf-8")
required = (
    "pairing_scenario:",
    "ATLAS_PAIRING_ARTIFACT_DIR",
    "ATLAS_TRUSTED_PAIRING_VECTOR_B64",
    "ATLAS_PAIRING_RING_STAGE=produce",
    "ATLAS_PAIRING_RING_STAGE=verify",
    "--no-uninstall",
    "adb shell run-as",
    "adb exec-out run-as",
    "ATLAS_WINDOWS_STORAGE_TEST_STAGE",
    "ATLAS_WINDOWS_PRIVATE_TEST_STAGE",
    "AtlasIOSFlutterEncryptedInteroperabilityTests",
    "apple-to-android-",
    "android-to-windows-",
    "windows-to-apple-",
)
missing = [marker for marker in required if marker not in workflow]
if missing:
    raise SystemExit("AtlasVault integration isolation policy is incomplete.")
if workflow.count("pairing_scenario:") != 2:
    raise SystemExit("Android and Windows pairing scenarios must be isolated.")

android = workflow.split(
    'if [[ "${{ matrix.pairing_scenario }}" == "persistence" ]]', 1
)[1].split("\n            else", 1)[0]
windows = workflow.split(
    'if ("${{ matrix.pairing_scenario }}" -eq "persistence")', 1
)[1].split("\n          else", 1)[0]
if "TRUSTED_PAIRING_STAGE=journey" in android or "TRUSTED_PAIRING_STAGE=journey" in windows:
    raise SystemExit("Pairing journey must use a fresh matrix runner.")
android_journey = workflow.split("\n            else", 1)[1].split("\n            fi", 1)[0]
if "--dart-define=ATLAS_PAIRING_ARTIFACT_DIR=" not in android_journey:
    raise SystemExit("Android pairing artifact exchange must use app-private staging.")
print("Validated isolated pairing persistence and canonical artifact-ring policy.")
PY

python - <<'PY'
import re
from pathlib import Path

scripts = (
    Path("scripts/ci/atlasvault_python.sh"),
    Path("scripts/ci/atlasvault_flutter.sh"),
)
undeclared_rg = re.compile(r"^\s*(?:if\s+)?rg(?:\s|$)")
matches = []
for path in scripts:
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if undeclared_rg.search(line):
            matches.append(f"{path}:{line_number}")
if matches:
    raise SystemExit("AtlasVault CI scripts use undeclared ripgrep commands.")
swift_script = Path("scripts/ci/atlasvault_swift.sh").read_text(encoding="utf-8")
required_swift_isolation = (
    "AtlasVaultProductionHostTests.testWillTerminateCancelsRetainedSavedSearchNetworkBeforeLifecycleHandlerReturns",
    "AtlasVaultUnlockRequestCoordinatorTests.testCoordinatorCancellationBeforeOperationStartClearsClaimedBuffer",
    'for test in "${isolated_tests[@]}"',
    '--filter "$test"',
    '--skip "${isolated_tests[0]}"',
    '--skip "${isolated_tests[1]}"',
)
if any(marker not in swift_script for marker in required_swift_isolation):
    raise SystemExit("Swift cancellation tests must run in isolated processes.")
python_script = Path("scripts/ci/atlasvault_python.sh").read_text(encoding="utf-8")
flutter_script = Path("scripts/ci/atlasvault_flutter.sh").read_text(encoding="utf-8")
workflow = Path(
    ".github/workflows/atlasvault-cross-platform-security.yml"
).read_text(encoding="utf-8")
required_python_guard = (
    "import " + "ast",
    "ast." + "ImportFrom",
    "blocked_import_" + "roots",
    "Validated Python AST " + "no-network policy.",
)
required_flutter_guard = (
    "_mask_dart_" + "non_code",
    "_init_state_" + "bodies",
    "multiline_init_state_" + "samples",
    "operation_" + "reference",
    "tear_off_init_state_" + "samples",
    "Future.microtask(" + "startPairing);",
    "Validated Dart lifecycle-body " + "automatic-operation policy.",
)
if any(marker not in python_script for marker in required_python_guard):
    raise SystemExit("Python no-network policy must use structured AST checks.")
if any(marker not in flutter_script for marker in required_flutter_guard):
    raise SystemExit("Dart automatic-operation policy must inspect lifecycle bodies.")
setup_python = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
if workflow.count(setup_python) != 2:
    raise SystemExit("Python must be explicitly provisioned for both structured guards.")
swift_job = workflow.split("\n  swift:", 1)[1].split("\n  windows:", 1)[0]
swift_checkout = swift_job.split("- name: Check out source", 1)[1].split(
    "\n      - name:", 1
)[0]
if "fetch-depth: 0" not in swift_checkout:
    raise SystemExit("Swift history-sensitive tests require a full checkout.")
print("Validated standard-tool-only AtlasVault source guards.")
PY

python - <<'PY'
import ast
from pathlib import Path

blocked_import_roots = frozenset(
    {"aiohttp", "httpx", "requests", "socket", "urllib"}
)
targets = (
    Path("packages/vaultsync/vaultsync/device_identity.py"),
    Path("packages/vaultsync/vaultsync/pairing.py"),
    Path("packages/vaultsync/vaultsync/key_delivery.py"),
    Path("packages/vaultsync/vaultsync/pairing_artifacts.py"),
    Path("packages/vaultsync/vaultsync/trusted_devices.py"),
)


def _blocked_imports(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.partition(".")[0] in blocked_import_roots
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.partition(".")[0] in blocked_import_roots:
                return True
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.partition(".")[0] in blocked_import_roots
        ):
            return True
    return False


aliased_import_samples = (
    "import httpx as client",
    "from requests import get",
    "from socket import socket as connect",
    "import urllib.request as network",
    "__import__('aiohttp')",
)
if not all(_blocked_imports(sample) for sample in aliased_import_samples):
    raise SystemExit("Python AST no-network self-test failed.")
if _blocked_imports("import json\njson.loads('{}')"):
    raise SystemExit("Python AST no-network self-test failed.")
for path in targets:
    if _blocked_imports(path.read_text(encoding="utf-8")):
        raise SystemExit("Network imports are not permitted in pairing primitives.")
print("Validated Python AST no-network policy.")
PY

forbidden="$(find "$REPO_ROOT" -path "$REPO_ROOT/.git" -prune -o -type f \( -name '*.atlasvault' -o -name '*.atlaspair' -o -iname '*identity*secret*' -o -iname '*ephemeral*private*' \) -print)"
if [[ -n "$forbidden" ]]; then
  printf 'Forbidden AtlasVault artifact found in the repository:\n%s\n' "$forbidden" >&2
  exit 1
fi
