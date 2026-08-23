#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
APPLE_ROOT="$REPO_ROOT/apps/apple"
FLUTTER_ROOT="$REPO_ROOT/apps/atlas_flutter"
TEMP_ROOT="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/atlasvault-swift-ci.XXXXXX")"
readonly SCRIPT_DIR REPO_ROOT APPLE_ROOT FLUTTER_ROOT TEMP_ROOT

cleanup() {
  rm -rf "$TEMP_ROOT"
}
trap cleanup EXIT

if [[ ! -f "$REPO_ROOT/.github/workflows/atlasvault-cross-platform-security.yml" ]]; then
  printf 'The AtlasVault pull-request workflow is missing.\n' >&2
  exit 1
fi

cd "$APPLE_ROOT"

swift test --scratch-path "$TEMP_ROOT/focused" --filter 'AtlasVault(DeviceIdentity|PairingFoundation|KeyDelivery|PairingTransaction|CrossPlatformTrustedPairing)Tests'

RUNTIME_VECTOR_DIR="$TEMP_ROOT/device-identity-runtime"
export ATLAS_DEVICE_IDENTITY_RUNTIME_VECTOR_DIR="$RUNTIME_VECTOR_DIR"
swift test --scratch-path "$TEMP_ROOT/focused" --filter 'AtlasVaultPairingFoundationTests.testWritesPublicFreshSwiftSignatureArtifactWhenRequested'
python3 -m pytest "$REPO_ROOT/packages/vaultsync/tests/test_pairing_vectors.py" \
  -k test_python_verifies_public_swift_runtime_signature_artifact
cd "$FLUTTER_ROOT"
flutter pub get
flutter test test/atlas_vault_pairing_test.dart \
  --plain-name 'Dart verifies the public Swift runtime signature artifact'
unset ATLAS_DEVICE_IDENTITY_RUNTIME_VECTOR_DIR

ATLAS_INTEROP_ARTIFACT_DIR="$TEMP_ROOT/encrypted-interoperability"
mkdir -p "$ATLAS_INTEROP_ARTIFACT_DIR"
export ATLAS_INTEROP_ARTIFACT_DIR

cd "$APPLE_ROOT"
swift test --scratch-path "$TEMP_ROOT/focused" \
  --filter 'AtlasIOSFlutterEncryptedInteroperabilityTests.testAppleProductionCoordinatorWritesExactFlutterArtifact'

cd "$FLUTTER_ROOT"
flutter test test/atlas_vault_interoperability_test.dart \
  --plain-name 'direct Apple artifact imports when exchange mode is enabled'
flutter test test/atlas_vault_interoperability_test.dart \
  --plain-name 'confirmed setup preserves records and saves exact Flutter vector bytes'

cd "$APPLE_ROOT"
swift test --scratch-path "$TEMP_ROOT/focused" \
  --filter 'AtlasIOSFlutterEncryptedInteroperabilityTests.testFlutterOriginExportImportsThroughProductionCoordinator'
unset ATLAS_INTEROP_ARTIFACT_DIR

isolated_tests=(
  'AtlasVaultProductionHostTests.testWillTerminateCancelsRetainedSavedSearchNetworkBeforeLifecycleHandlerReturns'
  'AtlasVaultUnlockRequestCoordinatorTests.testCoordinatorCancellationBeforeOperationStartClearsClaimedBuffer'
  'AtlasVaultUnlockRequestCoordinatorTests.testCancellationBeforeDispatchClearsBufferAndInvokesNothing'
)
for test in "${isolated_tests[@]}"; do
  swift test --scratch-path "$TEMP_ROOT/full" --filter "$test"
done
swift test --scratch-path "$TEMP_ROOT/full" \
  --skip "${isolated_tests[0]}" \
  --skip "${isolated_tests[1]}" \
  --skip "${isolated_tests[2]}"

cd "$FLUTTER_ROOT"
flutter test test/tab_golden_test.dart

cd "$APPLE_ROOT"

xcodebuild -scheme AtlasApple -destination 'generic/platform=iOS Simulator' -derivedDataPath "$TEMP_ROOT/AtlasApple" CODE_SIGNING_ALLOWED=NO build

xcodebuild -project AtlasIOSHost/AtlasIOSHost.xcodeproj -scheme AtlasIOSHost -destination 'generic/platform=iOS Simulator' -derivedDataPath "$TEMP_ROOT/AtlasIOSHost" CODE_SIGNING_ALLOWED=NO build

forbidden="$(
  { find "$REPO_ROOT" -path "$REPO_ROOT/.git" -prune -o -type f -print |
      LC_ALL=C grep -Ei '\.atlasvault$|\.atlaspair$|identity[^[:alnum:]]*secret|secret[^[:alnum:]]*identity|ephemeral[^[:alnum:]]*private|private[^[:alnum:]]*ephemeral'; } || true
)"
if [[ -n "$forbidden" ]]; then
  printf 'Forbidden AtlasVault artifact found in the repository:\n%s\n' "$forbidden" >&2
  exit 1
fi
