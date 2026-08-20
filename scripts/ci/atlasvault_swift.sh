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
isolated_tests=(
  'AtlasVaultProductionHostTests.testWillTerminateCancelsRetainedSavedSearchNetworkBeforeLifecycleHandlerReturns'
  'AtlasVaultUnlockRequestCoordinatorTests.testCoordinatorCancellationBeforeOperationStartClearsClaimedBuffer'
)
for test in "${isolated_tests[@]}"; do
  swift test --scratch-path "$TEMP_ROOT/full" --filter "$test"
done
swift test --scratch-path "$TEMP_ROOT/full" \
  --skip "${isolated_tests[0]}" \
  --skip "${isolated_tests[1]}"

cd "$FLUTTER_ROOT"
flutter pub get
flutter test test/tab_golden_test.dart test/search_golden_test.dart

cd "$APPLE_ROOT"

xcodebuild -scheme AtlasApple -destination 'generic/platform=iOS Simulator' -derivedDataPath "$TEMP_ROOT/AtlasApple" CODE_SIGNING_ALLOWED=NO build

xcodebuild -project AtlasIOSHost/AtlasIOSHost.xcodeproj -scheme AtlasIOSHost -destination 'generic/platform=iOS Simulator' -derivedDataPath "$TEMP_ROOT/AtlasIOSHost" CODE_SIGNING_ALLOWED=NO build

forbidden="$(find "$REPO_ROOT" -path "$REPO_ROOT/.git" -prune -o -type f \( -name '*.atlasvault' -o -name '*.atlaspair' \) -print)"
if [[ -n "$forbidden" ]]; then
  printf 'Forbidden AtlasVault artifact found in the repository:\n%s\n' "$forbidden" >&2
  exit 1
fi
