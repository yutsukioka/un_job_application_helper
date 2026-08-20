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

if [[ -f tests/test_job_api_private_access.py ]]; then
  python -m pytest tests/test_job_api_private_access.py
fi

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

if rg -n 'requests\.|urllib\.|httpx\.|aiohttp\.|socket\.' packages/vaultsync/vaultsync/device_identity.py packages/vaultsync/vaultsync/pairing.py packages/vaultsync/vaultsync/key_delivery.py packages/vaultsync/vaultsync/pairing_artifacts.py packages/vaultsync/vaultsync/trusted_devices.py; then
  printf 'Network access is not permitted in AtlasVault pairing primitives.\n' >&2
  exit 1
fi

forbidden="$(find "$REPO_ROOT" -path "$REPO_ROOT/.git" -prune -o -type f \( -name '*.atlasvault' -o -name '*.atlaspair' -o -iname '*identity*secret*' -o -iname '*ephemeral*private*' \) -print)"
if [[ -n "$forbidden" ]]; then
  printf 'Forbidden AtlasVault artifact found in the repository:\n%s\n' "$forbidden" >&2
  exit 1
fi
