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
print("Validated standard-tool-only AtlasVault source guards.")
PY

if grep -En -- 'requests\.|urllib\.|httpx\.|aiohttp\.|socket\.' packages/vaultsync/vaultsync/device_identity.py packages/vaultsync/vaultsync/pairing.py packages/vaultsync/vaultsync/key_delivery.py packages/vaultsync/vaultsync/pairing_artifacts.py packages/vaultsync/vaultsync/trusted_devices.py; then
  printf 'Network access is not permitted in AtlasVault pairing primitives.\n' >&2
  exit 1
fi

forbidden="$(find "$REPO_ROOT" -path "$REPO_ROOT/.git" -prune -o -type f \( -name '*.atlasvault' -o -name '*.atlaspair' -o -iname '*identity*secret*' -o -iname '*ephemeral*private*' \) -print)"
if [[ -n "$forbidden" ]]; then
  printf 'Forbidden AtlasVault artifact found in the repository:\n%s\n' "$forbidden" >&2
  exit 1
fi
