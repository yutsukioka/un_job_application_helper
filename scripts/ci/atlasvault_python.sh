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

python - <<'PY'
from pathlib import Path

workflow = Path(
    ".github/workflows/atlasvault-cross-platform-security.yml"
).read_text(encoding="utf-8")
if "pull_request:\n    paths:" in workflow:
    raise SystemExit("AtlasVault admission must run for every pull request.")
required_admission_markers = (
    "admission:",
    "Path(\".\").rglob(\"*\")",
    "path.name.casefold()",
    "needs: admission",
)
missing = [marker for marker in required_admission_markers if marker not in workflow]
if missing:
    raise SystemExit("AtlasVault forbidden-artifact trigger coverage is incomplete.")
for forbidden in (
    "ROOT.ATLASVAULT",
    "root.AtlasPair",
    "private/IdentitySecret.json",
    "private/IDENTITY-SECRET.bin",
    "private/EphemeralPrivate.json",
    "nested/EPHEMERAL_private.dat",
):
    normalized = forbidden.casefold()
    if not (
        normalized.endswith((".atlasvault", ".atlaspair"))
        or ("identity" in normalized and "secret" in normalized)
        or ("ephemeral" in normalized and "private" in normalized)
    ):
        raise SystemExit("AtlasVault forbidden-artifact policy self-test failed.")
ordinary = "docs/ordinary-note.md".casefold()
if any(term in ordinary for term in ("identity", "secret", "ephemeral", "private")):
    raise SystemExit("AtlasVault ordinary-file policy self-test failed.")
print("Validated AtlasVault forbidden-artifact trigger coverage.")
PY

focused_tests=(
  packages/vaultsync/tests/test_device_identity_vectors.py
  packages/vaultsync/tests/test_pairing_vectors.py
  packages/vaultsync/tests/test_key_delivery_vectors.py
  packages/vaultsync/tests/test_pairing_artifact_vectors.py
  packages/vaultsync/tests/test_trusted_device_vectors.py
)
python - <<'PY'
from pathlib import Path

script = Path("scripts/ci/atlasvault_python.sh").read_text(encoding="utf-8")
obsolete_android_runner = "android_" + "runner = workflow.split("
if obsolete_android_runner in script:
    raise SystemExit("Android policy retains an obsolete runner extraction.")
if script.index("\ndef _blocked_imports") > script.rindex(
    'python -m pytest "${focused_tests[@]}"'
):
    raise SystemExit("VaultSync pytest must follow the complete AST preflight.")
PY

python - <<'PY'
import re
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

def parse_android_runner_script(raw):
    return [
        line.strip()
        for line in raw.strip().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _step_block(source, name):
    marker = f"\n      - name: {name}\n"
    if marker not in source:
        raise ValueError(f"Missing workflow step: {name}")
    return source.split(marker, 1)[1].split("\n      - name:", 1)[0]


def _android_action_script(source):
    runner = source.split(
        "uses: reactivecircus/android-emulator-runner@", 1
    )[1].split("\n      - name:", 1)[0]
    lines = runner.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("script:"):
            continue
        value = stripped.split(":", 1)[1].strip()
        if value not in {"|", "|-", "|+"}:
            return value
        body = []
        script_indent = len(line) - len(line.lstrip())
        for body_line in lines[index + 1 :]:
            body_indent = len(body_line) - len(body_line.lstrip())
            if body_line.strip() and body_indent <= script_indent:
                break
            body.append(body_line)
        return "\n".join(body)
    raise ValueError("Android emulator runner is missing script input.")


def _validate_android_command_boundary(source):
    materialize_name = "Materialize Android platform integration script"
    cleanup_name = "Remove Android platform integration script"
    materialize = _step_block(source, materialize_name)
    cleanup = _step_block(source, cleanup_name)
    runner_index = source.index("uses: reactivecircus/android-emulator-runner@")
    if source.index(f"- name: {materialize_name}") > runner_index:
        raise ValueError("Android script must be materialized before emulator launch.")
    if source.index(f"- name: {cleanup_name}") < runner_index:
        raise ValueError("Android script cleanup must follow emulator execution.")

    materialize_markers = (
        "shell: bash",
        'script_path="$RUNNER_TEMP/atlasvault-android-platform-integration.sh"',
        "cat > \"$script_path\" <<'ATLAS_ANDROID_SCRIPT'",
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'pairing_scenario="${1:?missing Android pairing scenario}"',
        'vector_root="${GITHUB_WORKSPACE:?}/contracts/sync/test_vectors"',
        'cd "${GITHUB_WORKSPACE:?}/apps/atlas_flutter"',
        'if [[ "$pairing_scenario" == "persistence" ]]',
        "else",
        "trap cleanup_android_pairing EXIT",
        'chmod 0700 "$script_path"',
        'bash -n "$script_path"',
    )
    if any(marker not in materialize for marker in materialize_markers):
        raise ValueError("Android materialized script policy is incomplete.")
    if materialize.count(
        "$RUNNER_TEMP/atlasvault-android-platform-integration.sh"
    ) != 1:
        raise ValueError("Android materialized script path must be assigned exactly once.")

    raw_script = _android_action_script(source)
    commands = parse_android_runner_script(raw_script)
    if len(commands) != 1:
        raise ValueError("Android emulator runner must receive exactly one command.")
    command = commands[0]
    expected = (
        'bash "$RUNNER_TEMP/atlasvault-android-platform-integration.sh" '
        '"${{ matrix.pairing_scenario }}"'
    )
    if "\n" in command or "<<" in command or "set -euo pipefail" in command:
        raise ValueError("Android emulator runner command contains multiline shell state.")
    if re.search(
        r"(?:\bfunction\b|\b[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*\{)",
        command,
    ):
        raise ValueError("Android emulator runner command defines a shell function.")
    if command != expected:
        raise ValueError("Android emulator runner command is not the approved invocation.")

    cleanup_markers = (
        "if: always()",
        "shell: bash",
        'rm -f -- "$RUNNER_TEMP/atlasvault-android-platform-integration.sh"',
    )
    if any(marker not in cleanup for marker in cleanup_markers):
        raise ValueError("Android materialized script cleanup policy is incomplete.")
    return commands


def _validate_android_kvm_boundary(source):
    step_name = "Enable Android KVM"
    kvm = _step_block(source, step_name)
    runner_index = source.index("uses: reactivecircus/android-emulator-runner@")
    if source.index(f"- name: {step_name}") > runner_index:
        raise ValueError("Android KVM must be enabled before emulator launch.")
    for marker in (
        "shell: bash",
        "test -e /dev/kvm",
        'KERNEL=="kvm"',
        'GROUP="kvm"',
        'MODE="0666"',
        'OPTIONS+="static_node=kvm"',
        "sudo udevadm control --reload-rules",
        "sudo udevadm trigger --name-match=kvm",
        "test -r /dev/kvm",
        "test -w /dev/kvm",
        "ls -l /dev/kvm",
    ):
        if marker not in kvm:
            raise ValueError("Android KVM permission policy is incomplete.")
    runner = source.split(
        "uses: reactivecircus/android-emulator-runner@", 1
    )[1].split("\n      - name:", 1)[0]
    if "disable-linux-hw-accel: false" not in runner:
        raise ValueError("Android hardware acceleration must be explicitly required.")
    if "disable-linux-hw-accel: auto" in runner:
        raise ValueError("Android security integration must not use automatic fallback.")
    if "-no-metrics" not in runner:
        raise ValueError("Android emulator metrics must be disabled explicitly.")
    _validate_android_command_boundary(source)


def _validate_windows_process_stage_boundary(source):
    normal_marker = "void _registerNormalRecoveryTests()"
    process_marker = "void _registerCrossProcessRecoveryTest(String stage)"
    runner_marker = "Future<void> _runCrossProcessRecoveryStage(String stage) async"
    for marker in (normal_marker, process_marker, runner_marker):
        if marker not in source:
            raise ValueError("Windows recovery test registration is not separated.")
    normal_index = source.index(normal_marker)
    process_index = source.index(process_marker)
    runner_index = source.index(runner_marker)
    if not normal_index < process_index < runner_index:
        raise ValueError("Windows recovery registration helpers are out of order.")

    main = source[source.index("void main()"):normal_index]
    for marker in (
        "if (_processStage == null)",
        "_registerNormalRecoveryTests();",
        "} else {",
        "_registerCrossProcessRecoveryTest(_processStage!);",
    ):
        if marker not in main:
            raise ValueError("Windows main does not select exactly one test lane.")

    normal = source[normal_index:process_index]
    if "testWidgets(" not in normal:
        raise ValueError("Windows normal recovery tests are missing.")
    if "_processStage" in normal:
        raise ValueError("Windows normal tests must not register in process-stage runs.")

    process = source[process_index:runner_index]
    if "testWidgets(" in process or "test(" not in process:
        raise ValueError("Windows process-stage lane must use a plain test.")
    for forbidden in ("WidgetTester", "tester.", "printToConsole"):
        if forbidden in process:
            raise ValueError("Windows process-stage test still depends on widget state.")
    if "await _runCrossProcessRecoveryStage(stage);" not in process:
        raise ValueError("Windows process-stage test does not invoke its stage runner.")

    stage_runner = source[runner_index:]
    for stage in (
        "admission-waiter",
        "admission-prepare",
        "admission-reset",
        "selection-waiter",
        "selection-run",
        "crash-holder",
        "crash-verify",
        "cleanup",
    ):
        if f"case '{stage}':" not in stage_runner:
            raise ValueError("Windows process-stage runner is incomplete.")
    for forbidden in (
        "FocusManager",
        "FlutterError.onError",
        "runZonedGuarded",
        "printToConsole",
    ):
        if forbidden in process or forbidden in stage_runner:
            raise ValueError("Windows process-stage lane suppresses framework errors.")


valid_fixture = '''
      - name: Materialize Android platform integration script
        shell: bash
        run: |
          script_path="$RUNNER_TEMP/atlasvault-android-platform-integration.sh"
          cat > "$script_path" <<'ATLAS_ANDROID_SCRIPT'
          #!/usr/bin/env bash
          set -euo pipefail
          pairing_scenario="${1:?missing Android pairing scenario}"
          vector_root="${GITHUB_WORKSPACE:?}/contracts/sync/test_vectors"
          cd "${GITHUB_WORKSPACE:?}/apps/atlas_flutter"
          if [[ "$pairing_scenario" == "persistence" ]]; then
            true
          else
            cleanup_android_pairing() { true; }
            trap cleanup_android_pairing EXIT
          fi
          ATLAS_ANDROID_SCRIPT
          chmod 0700 "$script_path"
          bash -n "$script_path"

      - name: Enable Android KVM
        shell: bash
        run: |
          set -euo pipefail
          test -e /dev/kvm
          echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' |
            sudo tee /etc/udev/rules.d/99-kvm4all.rules >/dev/null
          sudo udevadm control --reload-rules
          sudo udevadm trigger --name-match=kvm
          test -r /dev/kvm
          test -w /dev/kvm
          ls -l /dev/kvm

      - name: Run Android fake-data security integrations
        uses: reactivecircus/android-emulator-runner@example
        with:
          disable-linux-hw-accel: false
          emulator-options: -no-window -gpu swiftshader_indirect -no-snapshot -noaudio -no-boot-anim -no-metrics
          script: bash "$RUNNER_TEMP/atlasvault-android-platform-integration.sh" "${{ matrix.pairing_scenario }}"

      - name: Remove Android platform integration script
        if: always()
        shell: bash
        run: |
          rm -f -- "$RUNNER_TEMP/atlasvault-android-platform-integration.sh"

      - name: Enforce Android artifact policy
'''
if len(_validate_android_command_boundary(valid_fixture)) != 1:
    raise SystemExit("Android single-command boundary positive self-test failed.")
_validate_android_kvm_boundary(valid_fixture)

invalid_fixtures = (
    valid_fixture.replace(
        'script: bash "$RUNNER_TEMP/atlasvault-android-platform-integration.sh" "${{ matrix.pairing_scenario }}"',
        "script: |\n            bash -euo pipefail <<'EOF'\n"
        "            value=\"x\"\n            echo \"$value\"\n            EOF",
    ),
    valid_fixture.replace(
        'script: bash "$RUNNER_TEMP/atlasvault-android-platform-integration.sh" "${{ matrix.pairing_scenario }}"',
        'script: |\n            value="x"\n            echo "$value"',
    ),
    valid_fixture.replace(
        'script: bash "$RUNNER_TEMP/atlasvault-android-platform-integration.sh" "${{ matrix.pairing_scenario }}"',
        "script: bash -euo pipefail <<'EOF'",
    ),
    valid_fixture.replace(
        'script: bash "$RUNNER_TEMP/atlasvault-android-platform-integration.sh" "${{ matrix.pairing_scenario }}"',
        "script: set -euo pipefail",
    ),
    valid_fixture.replace(
        'script: bash "$RUNNER_TEMP/atlasvault-android-platform-integration.sh" "${{ matrix.pairing_scenario }}"',
        "script: helper() { true; }",
    ),
    valid_fixture.replace(
        "      - name: Materialize Android platform integration script",
        "      - name: Missing materialization step",
    ),
    valid_fixture.replace('          bash -n "$script_path"\n', ""),
    valid_fixture.replace(' "${{ matrix.pairing_scenario }}"', ""),
    valid_fixture.replace(
        "      - name: Remove Android platform integration script",
        "      - name: Missing Android script cleanup",
    ),
)
for fixture in invalid_fixtures:
    try:
        _validate_android_command_boundary(fixture)
    except (ValueError, IndexError):
        continue
    raise SystemExit("Android single-command boundary negative self-test failed.")

invalid_kvm_fixtures = (
    valid_fixture.replace("      - name: Enable Android KVM", "      - name: Missing KVM"),
    valid_fixture.replace("          test -e /dev/kvm\n", ""),
    valid_fixture.replace("          test -r /dev/kvm\n", ""),
    valid_fixture.replace("          test -w /dev/kvm\n", ""),
    valid_fixture.replace(
        "          disable-linux-hw-accel: false",
        "          disable-linux-hw-accel: auto",
    ),
    valid_fixture.replace(" -no-metrics", ""),
)
for fixture in invalid_kvm_fixtures:
    try:
        _validate_android_kvm_boundary(fixture)
    except (ValueError, IndexError):
        continue
    raise SystemExit("Android KVM boundary negative self-test failed.")

valid_windows_fixture = '''
void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  if (_processStage == null) {
    _registerNormalRecoveryTests();
  } else {
    _registerCrossProcessRecoveryTest(_processStage!);
  }
}

void _registerNormalRecoveryTests() {
  testWidgets('normal recovery', (tester) async {});
}

void _registerCrossProcessRecoveryTest(String stage) {
  test('cross-process recovery', () async {
    if (!Platform.isWindows) {
      return;
    }
    await _runCrossProcessRecoveryStage(stage);
  });
}

Future<void> _runCrossProcessRecoveryStage(String stage) async {
  switch (stage) {
    case 'admission-waiter':
    case 'admission-prepare':
    case 'admission-reset':
    case 'selection-waiter':
    case 'selection-run':
    case 'crash-holder':
    case 'crash-verify':
    case 'cleanup':
      return;
  }
}
'''
_validate_windows_process_stage_boundary(valid_windows_fixture)
invalid_windows_fixtures = (
    valid_windows_fixture.replace(
        "  test('cross-process recovery'",
        "  testWidgets('cross-process recovery'",
    ),
    valid_windows_fixture.replace(
        "  if (_processStage == null) {",
        "  if (true) {",
    ),
    valid_windows_fixture.replace(
        "  testWidgets('normal recovery', (tester) async {});",
        "  if (_processStage != null) return;\n"
        "  testWidgets('normal recovery', (tester) async {});",
    ),
    valid_windows_fixture.replace(
        "    await _runCrossProcessRecoveryStage(stage);",
        "    FocusManager.instance.applyFocusChangesIfNeeded();\n"
        "    await _runCrossProcessRecoveryStage(stage);",
    ),
)
for fixture in invalid_windows_fixtures:
    try:
        _validate_windows_process_stage_boundary(fixture)
    except (ValueError, IndexError):
        continue
    raise SystemExit("Windows process-stage boundary negative self-test failed.")

try:
    _validate_android_command_boundary(workflow)
except (ValueError, IndexError) as error:
    raise SystemExit(f"Android command-boundary policy failed: {error}") from error
try:
    _validate_android_kvm_boundary(workflow)
except (ValueError, IndexError) as error:
    raise SystemExit(f"Android KVM boundary policy failed: {error}") from error
recovery_source = Path(
    "apps/atlas_flutter/integration_test/"
    "atlas_vault_windows_interoperability_recovery_test.dart"
).read_text(encoding="utf-8")
try:
    _validate_windows_process_stage_boundary(recovery_source)
except (ValueError, IndexError) as error:
    raise SystemExit(f"Windows process-stage boundary policy failed: {error}") from error

android_section = workflow.split("\n  android:", 1)[1].split("\n  windows:", 1)[0]
windows_section = workflow.split("\n  windows:", 1)[1].split("\n  apple:", 1)[0]
android = android_section.split(
    'if [[ "$pairing_scenario" == "persistence" ]]', 1
)[1].split("\n          else", 1)[0]
windows = windows_section.split(
    'if ("${{ matrix.pairing_scenario }}" -eq "persistence")', 1
)[1].split("\n          else", 1)[0]
if "TRUSTED_PAIRING_STAGE=journey" in android or "TRUSTED_PAIRING_STAGE=journey" in windows:
    raise SystemExit("Pairing journey must use a fresh matrix runner.")
android_journey = android_section.split("\n          else", 1)[1].split(
    "\n          fi", 1
)[0]
if "--dart-define=ATLAS_PAIRING_ARTIFACT_DIR=" not in android_journey:
    raise SystemExit("Android pairing artifact exchange must use app-private staging.")

windows_journey = windows_section.split("\n          else", 1)[1].split(
    "\n\n      - name: Enforce Windows artifact policy", 1
)[0]
if "-ErrorAction SilentlyContinue" in windows_journey:
    raise SystemExit("Windows pairing-ring cleanup must not suppress deletion errors.")
for marker in (
    "$RingCleanupErrors = [System.Collections.Generic.List[string]]::new()",
    "Remove-Item -LiteralPath $Ring -Recurse -Force -ErrorAction Stop",
    "if (Test-Path -LiteralPath $Ring)",
    "Get-ChildItem -LiteralPath $Ring -Recurse -File -Filter *.atlaspair",
    "throw \"Windows pairing-ring cleanup failed:",
):
    if marker not in windows_journey:
        raise SystemExit("Windows pairing-ring cleanup must fail closed.")

def cleanup_succeeds(*, remove_failed: bool, directory_exists: bool, artifacts: int) -> bool:
    return not (remove_failed or directory_exists or artifacts)

if cleanup_succeeds(remove_failed=True, directory_exists=True, artifacts=1):
    raise SystemExit("A retained Windows pairing ring must fail the job.")
if not cleanup_succeeds(remove_failed=False, directory_exists=False, artifacts=0):
    raise SystemExit("A removed Windows pairing ring must pass cleanup.")
print("Validated isolated pairing persistence and canonical artifact-ring policy.")
PY

python - <<'PY'
from pathlib import Path

integration_tests = (
    Path(
        "apps/atlas_flutter/integration_test/"
        "atlas_vault_android_trusted_pairing_integration_test.dart"
    ),
    Path(
        "apps/atlas_flutter/integration_test/"
        "atlas_vault_windows_trusted_pairing_integration_test.dart"
    ),
)
for path in integration_tests:
    source = path.read_text(encoding="utf-8")
    for marker in (
        "final journey = await scenario.runExplicitRoleCycle();",
        "runtimeArtifacts: journey.artifacts,",
        "Future<AtlasVaultPairingPlatformJourneyEvidence>",
        "runExplicitRoleCycle() async",
        "required Map<AtlasVaultPairingArtifactKind, Uint8List> runtimeArtifacts,",
        "runtimeArtifacts[kind]",
        "await _verifyPairingArtifactSet(runtimeArtifacts, vector);",
        "final producedReadBack = await produced.readAsBytes();",
        "expect(producedReadBack, bytes);",
        "final consumerCalculatedDigest = await atlasVaultSha256Hex(",
        "expect(consumerCalculatedDigest, consumerRecordedDigest);",
        "expect(artifact.canonicalBytes(), consumedBytes);",
        "await _verifyPairingArtifactSet(consumedArtifacts, vector);",
        "AtlasVaultPairingArtifact.fromCanonicalBytes(tampered)",
    ):
        if marker not in source:
            raise SystemExit(
                f"{path} does not preserve runtime journey artifact semantics."
            )
    exchange = source.split("Future<void> _exchangePairingRing(", 1)[1]
    if "base64Decode(encoded['canonical_b64']" in exchange:
        raise SystemExit(f"{path} still uses fixture bytes as outgoing evidence.")
    for forbidden in (
        "expect(bytes, expectedBytes)",
        "expect(consumedBytes, bytes)",
        "expectedDigest = encoded['sha256']",
        "expect(digest, expectedDigest)",
    ):
        if forbidden in exchange:
            raise SystemExit(
                f"{path} conflates fixed-vector and runtime-journey evidence."
            )
print("Validated Android and Windows runtime journey artifact egress policy.")
PY

python - <<'PY'
from pathlib import Path

workflow = Path(
    ".github/workflows/atlasvault-platform-integration.yml"
).read_text(encoding="utf-8")
required = (
    "def validate_ring_artifact(*, prefix, kind, expected=None, expected_digest=None):",
    'prefix="apple-to-android",',
    "expected=expected,",
    "expected_digest=entry[\"sha256\"],",
    'prefix="android-to-windows",',
    "if artifact != canonical:",
    'decoded["format"] != "atlasvault-pairing-artifact"',
    'decoded["version"] != 1',
    'decoded["kind"] != kind',
    "if digest != recorded:",
)
missing = [marker for marker in required if marker not in workflow]
if missing:
    raise SystemExit(
        "Android fixed-vector and runtime-ring validation lanes are not separated."
    )
runtime_call = workflow.split('prefix="android-to-windows",', 1)[1].split(")", 1)[0]
if "expected=" in runtime_call or "expected_digest=" in runtime_call:
    raise SystemExit("Android runtime artifacts must not use the fixed-vector oracle.")
print("Validated separate fixed-vector and runtime pairing-ring workflow lanes.")
PY

python - <<'PY'
from pathlib import Path

workflow = Path(
    ".github/workflows/atlasvault-platform-integration.yml"
).read_text(encoding="utf-8")
architecture = Path(
    "docs/architecture/atlasvault_cross_platform_security_ci.md"
).read_text(encoding="utf-8")
for marker in (
    '$HostedIngressProvenance = "fixed-vector-conformance"',
    'Hosted Windows pairing ingress provenance: $HostedIngressProvenance',
):
    if marker not in workflow:
        raise SystemExit(
            "Hosted Windows pairing ingress is not classified as fixed-vector conformance."
        )
if (
    "Hosted Windows fixed-vector ingress is not a cross-runner runtime handoff."
    not in architecture
):
    raise SystemExit("Architecture does not separate hosted ingress from runtime handoff.")
if "actions/upload-artifact" in workflow or "actions/download-artifact" in workflow:
    raise SystemExit("Pairing documents must not be uploaded between hosted jobs.")
print("Validated hosted fixed-vector ingress provenance and no-upload policy.")
PY

python - <<'PY'
from pathlib import Path

workflow = Path(
    ".github/workflows/atlasvault-platform-integration.yml"
).read_text(encoding="utf-8")
required = (
    "ATLAS_WINDOWS_MIGRATION_RECOVERY_STAGE",
    "ATLAS_WINDOWS_MIGRATION_RECOVERY_VAULT_ID",
    "ATLAS_WINDOWS_INTEROP_RECOVERY_PROCESS_STAGE",
    "ATLAS_WINDOWS_INTEROP_RECOVERY_PROCESS_VAULT_ID",
    "Invoke-AtlasRecoveryStage",
    "Start-AtlasRecoveryWaiter",
    "Wait-AtlasRecoveryWaiter",
    "Wait-AtlasRecoverySignal",
    "Stop-AtlasRecoveryHolder",
    "admission-waiter",
    "admission-prepare",
    "admission-rollback",
    "finalization-waiter",
    "finalization-run",
    "selection-waiter",
    "selection-run",
    "crash-holder",
    "crash-verify",
    "cleanup",
)
missing = [marker for marker in required if marker not in workflow]
if missing:
    raise SystemExit("Windows recovery process-boundary policy is incomplete.")

for stage in ("prepare", "verify"):
    if workflow.count(f'"{stage}"') < 2:
        raise SystemExit("Windows migration prepare/verify stages are not distinct.")
for marker in (
    "WaitForExit(120000)",
    "Stop-Process -Id $Waiter.Process.Id -Force",
    "finally {",
    "foreach ($CleanupPath in @($MigrationCoordinationRoot, $InteropCoordinationRoot, $RecoveryLogRoot))",
    "Remove-Item -LiteralPath $CleanupPath -Recurse -Force -ErrorAction Stop",
):
    if marker not in workflow:
        raise SystemExit("Windows recovery process-boundary cleanup is incomplete.")

if ' /v:on /s /c ' not in workflow or '!errorlevel! >' not in workflow:
    raise SystemExit("Windows recovery waiters must record the post-command exit code.")
if '%errorlevel% >' in workflow:
    raise SystemExit("Windows recovery waiters must not capture a pre-command exit code.")
if "RunnerProcessId" in workflow or "Get-AtlasRecoverySignalProcessId" in workflow:
    raise SystemExit("Windows crash-holder signals do not carry a runner process ID.")
tree_start = workflow.index("function Get-AtlasRecoveryProcessTree")
tree_end = workflow.index("function Stop-AtlasRecoveryProcessTree", tree_start)
tree_function = workflow[tree_start:tree_end]
for marker in (
    "Get-CimInstance -ClassName Win32_Process",
    "ParentProcessId",
    "$RootProcessId",
):
    if marker not in tree_function:
        raise SystemExit("Windows recovery must walk an owned process tree.")
holder_start = workflow.index("function Stop-AtlasRecoveryHolder")
holder_end = workflow.index("$MigrationRecoveryTest", holder_start)
holder_function = workflow[holder_start:holder_end]
if "Get-Process -Name atlas" in holder_function:
    raise SystemExit("Windows recovery must not terminate every Atlas runner at one path.")
for marker in (
    "$ProcessTree = Get-AtlasRecoveryHolderProcessTree $Holder",
    "$TestRunner = Get-Process -Id $ProcessTree.Runner.Id",
    "Stop-AtlasRecoveryProcessTree $Holder",
):
    if marker not in holder_function:
        raise SystemExit("Windows recovery holder ownership must use its process tree.")
for marker in (
    "function Get-AtlasRecoveryProcessTree",
    "function Stop-AtlasRecoveryProcessTree",
    "[System.Collections.Generic.List[PSCustomObject]]::new()",
    "Sort-Object -Property Depth -Descending",
    "Get-AtlasRecoveryProcessTree ([int]$Waiter.Process.Id)",
    "Stop-Process -Id $Descendant.Id -Force",
    "WaitForExit($RemainingMilliseconds)",
):
    if marker not in workflow:
        raise SystemExit("Windows recovery must terminate the complete holder process tree.")
waiter_start = workflow.index("function Wait-AtlasRecoveryWaiter")
waiter_end = workflow.index("function Invoke-AtlasRecoveryStage", waiter_start)
waiter_function = workflow[waiter_start:waiter_end]
if "Stop-AtlasRecoveryProcessTree $Waiter" not in waiter_function:
    raise SystemExit(
        "Windows recovery timeouts must terminate the complete waiter process tree."
    )
for marker in (
    "function Stop-AtlasRecoveryHolderForCleanup",
    "Stop-AtlasRecoveryProcessTree $Holder",
    "finally {\n              $CleanupErrors",
    "Stop-AtlasRecoveryHolderForCleanup $Holder",
    "foreach ($CleanupPath",
):
    if marker not in workflow:
        raise SystemExit("Windows finally cleanup must tolerate pre-runner holders.")
cleanup_holder_start = workflow.index("function Stop-AtlasRecoveryHolderForCleanup")
cleanup_holder_end = workflow.index("$MigrationRecoveryTest", cleanup_holder_start)
cleanup_holder_function = workflow[cleanup_holder_start:cleanup_holder_end]
if "Get-AtlasRecoveryHolderProcessTree" in cleanup_holder_function:
    raise SystemExit("Finally cleanup must tolerate a holder without an Atlas runner.")
for marker in (
    "Stop-AtlasRecoveryProcessTree $Holder",
    "Write-AtlasRecoveryLogs $Holder",
):
    if marker not in cleanup_holder_function:
        raise SystemExit("Finally cleanup must stop and retain evidence for its holder root.")
finally_start = workflow.index("finally {\n              $CleanupErrors")
finally_end = workflow.index("flutter test integration_test\\atlas_vault_windows_device_identity", finally_start)
finally_block = workflow[finally_start:finally_end]
for marker in (
    "$CleanupErrors = [System.Collections.Generic.List[string]]::new()",
    "Stop-AtlasRecoveryProcessTree $Waiter",
    "Stop-AtlasRecoveryHolderForCleanup $Holder",
    "foreach ($CleanupPath",
    "if (Test-Path -LiteralPath $CleanupPath)",
    "if ($CleanupErrors.Count -gt 0)",
):
    if marker not in finally_block:
        raise SystemExit("Windows finally cleanup must attempt every owned resource.")
if finally_block.index("Stop-AtlasRecoveryHolderForCleanup $Holder") > finally_block.index(
    "foreach ($CleanupPath"
):
    raise SystemExit("Windows cleanup paths must be removed after holder cleanup attempts.")
print("Validated Windows recovery process-boundary orchestration policy.")
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
    "AtlasVaultUnlockRequestCoordinatorTests.testCancellationBeforeDispatchClearsBufferAndInvokesNothing",
    'for test in "${isolated_tests[@]}"',
    '--filter "$test"',
    '--skip "${isolated_tests[0]}"',
    '--skip "${isolated_tests[1]}"',
    '--skip "${isolated_tests[2]}"',
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
    "importlib_module_" + "aliases",
    "import_module_" + "aliases",
    "dynamic_import_" + "samples",
    "from importlib import import_" + "module",
    "loader.import_" + "module",
    "import importlib." + "util",
    "standard_library_network_" + "samples",
    "from http.client import " + "HTTPSConnection",
    "Validated Python AST " + "no-network policy.",
)
required_flutter_guard = (
    "_mask_dart_" + "non_code",
    "_mask_dart_" + "string",
    "_automatic_lifecycle_" + "bodies",
    "multiline_init_state_" + "samples",
    "interpolation_init_state_" + "samples",
    "${startPairing" + "()}",
    "operation_" + "reference",
    "tear_off_init_state_" + "samples",
    "Future.microtask(" + "startPairing);",
    "_operation_" + "aliases",
    "assigned_tear_off_init_state_" + "samples",
    "final callback = start" + "Pairing;",
    "Future.microtask(" + "callback);",
    "controller.start" + "Pairing.call()",
    "Validated Dart lifecycle-body " + "automatic-operation policy.",
)
if any(marker not in python_script for marker in required_python_guard):
    raise SystemExit("Python no-network policy must use structured AST checks.")
if any(marker not in flutter_script for marker in required_flutter_guard):
    raise SystemExit("Dart automatic-operation policy must inspect lifecycle bodies.")
required_swift_runtime_ring = (
    "ATLAS_DEVICE_IDENTITY_RUNTIME_VECTOR_DIR",
    "testWritesPublicFreshSwiftSignatureArtifactWhenRequested",
    "test_python_verifies_public_swift_runtime_signature_artifact",
    "Dart verifies the public Swift runtime signature artifact",
)
if any(marker not in swift_script for marker in required_swift_runtime_ring):
    raise SystemExit("Swift runtime signatures require Python and Dart verification.")
required_swift_interop_ring = (
    'ATLAS_INTEROP_' + 'ARTIFACT_DIR="$TEMP_ROOT/encrypted-interoperability"',
    "testAppleProductionCoordinatorWritesExact" + "FlutterArtifact",
    "direct Apple artifact imports when exchange mode is " + "enabled",
    "confirmed setup preserves records and saves exact Flutter vector " + "bytes",
    "testFlutterOriginExportImportsThroughProduction" + "Coordinator",
)
if any(marker not in swift_script for marker in required_swift_interop_ring):
    raise SystemExit("Swift encrypted exports require direct Dart exchange.")
interop_positions = [
    swift_script.index(marker) for marker in required_swift_interop_ring
]
if interop_positions != sorted(interop_positions):
    raise SystemExit("Direct encrypted export exchange must run in producer order.")
if re.search(r"!\s+-name\s+['\"]search_golden_test\.dart['\"]", flutter_script):
    raise SystemExit("Linux must execute the search pixel golden cases.")
if "flutter test test/tab_golden_test.dart test/search_golden_test.dart" in swift_script:
    raise SystemExit("macOS must not substitute for Linux search pixel goldens.")
setup_python = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
if workflow.count(setup_python) != 3:
    raise SystemExit("Python must be explicitly provisioned for all structured checks.")
if 'python -m pip install -e "packages/vaultsync[dev]"' not in workflow:
    raise SystemExit("Swift runtime signature verification requires VaultSync.")
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
import tempfile
from pathlib import Path

blocked_import_roots = frozenset(
    {
        "aiohttp",
        "asyncio",
        "ftplib",
        "grpc",
        "http",
        "httpcore",
        "httpx",
        "imaplib",
        "multiprocessing",
        "nntplib",
        "poplib",
        "requests",
        "smtplib",
        "_socket",
        "socket",
        "socketserver",
        "ssl",
        "subprocess",
        "telnetlib",
        "urllib",
        "urllib3",
        "websocket",
        "websockets",
        "xmlrpc",
    }
)
def _vaultsync_targets(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.py")))


targets = _vaultsync_targets(Path("packages/vaultsync/vaultsync"))
os_process_apis = frozenset(
    {
        "system",
        "popen",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "forkpty",
    }
)


def _blocked_imports(source: str) -> bool:
    tree = ast.parse(source)
    builtins_module_aliases = set()
    builtin_import_aliases = set()
    importlib_module_aliases = set()
    import_module_aliases = set()
    importlib_loader_factory_aliases = {
        "find_spec": set(),
        "spec_from_file_location": set(),
        "module_from_spec": set(),
    }
    importlib_spec_aliases = set()
    importlib_loader_aliases = set()
    importlib_loader_execution_aliases = set()
    importlib_direct_loader_aliases = set()
    importlib_direct_loader_instance_aliases = set()
    importlib_direct_loader_execution_aliases = set()
    runpy_module_aliases = set()
    runpy_execution_aliases = set()
    dynamic_execution_aliases = set()
    getattr_aliases = {"getattr"}
    dynamic_execution_builtins = frozenset({"eval", "exec"})
    pty_module_aliases = set()
    pty_spawn_aliases = set()
    os_module_aliases = set()
    os_process_aliases = set()
    importlib_direct_loader_names = frozenset(
        {
            "SourceFileLoader",
            "SourcelessFileLoader",
            "ExtensionFileLoader",
        }
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.partition(".")[0] in blocked_import_roots
                for alias in node.names
            ):
                return True
            for alias in node.names:
                if alias.name == "builtins":
                    builtins_module_aliases.add(alias.asname or alias.name)
                elif alias.name == "importlib":
                    importlib_module_aliases.add(alias.asname or alias.name)
                elif alias.name == "os":
                    os_module_aliases.add(alias.asname or alias.name)
                elif alias.name == "runpy":
                    runpy_module_aliases.add(alias.asname or alias.name)
                elif alias.name == "pty":
                    pty_module_aliases.add(alias.asname or alias.name)
                elif alias.name.startswith("importlib."):
                    importlib_module_aliases.add(alias.asname or "importlib")
                elif alias.name.startswith("os.") and alias.asname is None:
                    os_module_aliases.add("os")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and module.partition(".")[0] in blocked_import_roots:
                return True
            if module == "builtins":
                for alias in node.names:
                    if alias.name == "__import__":
                        builtin_import_aliases.add(alias.asname or alias.name)
                    elif alias.name in dynamic_execution_builtins:
                        dynamic_execution_aliases.add(alias.asname or alias.name)
            elif module == "importlib":
                for alias in node.names:
                    if alias.name == "import_module":
                        import_module_aliases.add(
                            alias.asname or alias.name
                        )
                    elif alias.name in importlib_loader_factory_aliases:
                        importlib_loader_factory_aliases[alias.name].add(
                            alias.asname or alias.name
                        )
                    elif alias.name in {"util", "machinery"}:
                        importlib_module_aliases.add(alias.asname or alias.name)
            elif module in {"importlib.util", "importlib.machinery"}:
                for alias in node.names:
                    if alias.name in importlib_loader_factory_aliases:
                        importlib_loader_factory_aliases[alias.name].add(
                            alias.asname or alias.name
                        )
                    elif (
                        module == "importlib.machinery"
                        and alias.name in importlib_direct_loader_names
                    ):
                        importlib_direct_loader_aliases.add(
                            alias.asname or alias.name
                        )
            elif module == "os":
                for alias in node.names:
                    if alias.name == "*":
                        return True
                    if alias.name in os_process_apis:
                        os_process_aliases.add(alias.asname or alias.name)
            elif module == "runpy":
                for alias in node.names:
                    if alias.name == "*":
                        return True
                    if alias.name in {"run_path", "run_module"}:
                        runpy_execution_aliases.add(alias.asname or alias.name)
            elif module == "pty":
                for alias in node.names:
                    if alias.name == "*":
                        return True
                    if alias.name == "spawn":
                        pty_spawn_aliases.add(alias.asname or alias.name)

    def _is_import_module_reference(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in import_module_aliases
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "import_module"
            and isinstance(node.value, ast.Name)
            and node.value.id in importlib_module_aliases
        )

    def _is_importlib_module_reference(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in importlib_module_aliases
        return (
            isinstance(node, ast.Attribute)
            and _is_importlib_module_reference(node.value)
        )

    def _is_builtin_import_reference(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id == "__import__" or node.id in builtin_import_aliases
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "__import__"
            and isinstance(node.value, ast.Name)
            and node.value.id in builtins_module_aliases
        )

    def _is_dynamic_import_reference(node: ast.expr) -> bool:
        return _is_builtin_import_reference(node) or (
            isinstance(node, ast.Attribute)
            and node.attr == "__import__"
            and _is_importlib_module_reference(node.value)
        )

    def _is_os_module_reference(node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id in os_module_aliases

    def _is_os_process_reference(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in os_process_aliases
        return (
            isinstance(node, ast.Attribute)
            and node.attr in os_process_apis
            and _is_os_module_reference(node.value)
        )

    def _is_runpy_execution_reference(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in runpy_execution_aliases
        return (
            isinstance(node, ast.Attribute)
            and node.attr in {"run_path", "run_module"}
            and isinstance(node.value, ast.Name)
            and node.value.id in runpy_module_aliases
        )

    def _is_dynamic_execution_reference(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return (
                node.id in dynamic_execution_builtins
                or node.id in dynamic_execution_aliases
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in getattr_aliases
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in builtins_module_aliases
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in dynamic_execution_builtins
        ):
            return True
        return (
            isinstance(node, ast.Attribute)
            and node.attr in dynamic_execution_builtins
            and isinstance(node.value, ast.Name)
            and node.value.id in builtins_module_aliases
        )

    def _is_pty_spawn_reference(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in pty_spawn_aliases
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "spawn"
            and isinstance(node.value, ast.Name)
            and node.value.id in pty_module_aliases
        )

    def _is_importlib_loader_factory_reference(
        node: ast.expr, factory: str
    ) -> bool:
        if isinstance(node, ast.Name):
            return node.id in importlib_loader_factory_aliases[factory]
        return (
            isinstance(node, ast.Attribute)
            and node.attr == factory
            and _is_importlib_module_reference(node.value)
        )

    def _is_importlib_loader_reference(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "loader"
            and isinstance(node.value, ast.Name)
            and node.value.id in importlib_spec_aliases
        )

    def _is_importlib_loader_execution_reference(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in importlib_loader_execution_aliases
        return (
            isinstance(node, ast.Attribute)
            and node.attr in {"exec_module", "load_module"}
            and (
                _is_importlib_loader_reference(node.value)
                or (
                    isinstance(node.value, ast.Name)
                    and node.value.id in importlib_loader_aliases
                )
            )
        )

    def _is_importlib_direct_loader_factory_reference(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id in importlib_direct_loader_aliases
        return (
            isinstance(node, ast.Attribute)
            and node.attr in importlib_direct_loader_names
            and _is_importlib_module_reference(node.value)
        )

    def _is_importlib_direct_loader_instance(node: ast.expr) -> bool:
        return isinstance(node, ast.Call) and (
            _is_importlib_direct_loader_factory_reference(node.func)
        )

    def _is_importlib_direct_loader_execution_reference(
        node: ast.expr,
    ) -> bool:
        if isinstance(node, ast.Name):
            return node.id in importlib_direct_loader_execution_aliases
        return (
            isinstance(node, ast.Attribute)
            and node.attr in {"exec_module", "load_module"}
            and (
                _is_importlib_direct_loader_instance(node.value)
                or (
                    isinstance(node.value, ast.Name)
                    and node.value.id in importlib_direct_loader_instance_aliases
                )
            )
        )

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = node.value
                targets = node.targets
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value = node.value
                targets = (node.target,)
            elif isinstance(node, ast.NamedExpr):
                value = node.value
                targets = (node.target,)
            else:
                continue
            aliases_importlib = _is_importlib_module_reference(value)
            aliases_import_module = _is_import_module_reference(value)
            aliases_builtin_import = _is_dynamic_import_reference(value)
            aliases_os_module = _is_os_module_reference(value)
            aliases_os_process = _is_os_process_reference(value)
            aliases_importlib_spec = (
                isinstance(value, ast.Call)
                and any(
                    _is_importlib_loader_factory_reference(value.func, factory)
                    for factory in ("find_spec", "spec_from_file_location")
                )
            )
            aliases_importlib_loader = _is_importlib_loader_reference(value)
            aliases_importlib_loader_execution = (
                _is_importlib_loader_execution_reference(value)
            )
            aliases_importlib_direct_loader_instance = (
                _is_importlib_direct_loader_instance(value)
            )
            aliases_importlib_direct_loader_factory = (
                _is_importlib_direct_loader_factory_reference(value)
            )
            aliases_importlib_direct_loader_execution = (
                _is_importlib_direct_loader_execution_reference(value)
            )
            aliases_runpy_execution = _is_runpy_execution_reference(value)
            aliases_dynamic_execution = _is_dynamic_execution_reference(value)
            aliases_getattr = (
                isinstance(value, ast.Name) and value.id in getattr_aliases
            )
            aliases_pty_spawn = _is_pty_spawn_reference(value)
            if (
                not aliases_importlib
                and not aliases_import_module
                and not aliases_builtin_import
                and not aliases_os_module
                and not aliases_os_process
                and not aliases_importlib_spec
                and not aliases_importlib_loader
                and not aliases_importlib_loader_execution
                and not aliases_importlib_direct_loader_instance
                and not aliases_importlib_direct_loader_factory
                and not aliases_importlib_direct_loader_execution
                and not aliases_runpy_execution
                and not aliases_dynamic_execution
                and not aliases_getattr
                and not aliases_pty_spawn
            ):
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if (
                    aliases_importlib
                    and target.id not in importlib_module_aliases
                ):
                    importlib_module_aliases.add(target.id)
                    changed = True
                if (
                    aliases_import_module
                    and target.id not in import_module_aliases
                ):
                    import_module_aliases.add(target.id)
                    changed = True
                if (
                    aliases_builtin_import
                    and target.id not in builtin_import_aliases
                ):
                    builtin_import_aliases.add(target.id)
                    changed = True
                if aliases_os_module and target.id not in os_module_aliases:
                    os_module_aliases.add(target.id)
                    changed = True
                if aliases_os_process and target.id not in os_process_aliases:
                    os_process_aliases.add(target.id)
                    changed = True
                if (
                    aliases_importlib_spec
                    and target.id not in importlib_spec_aliases
                ):
                    importlib_spec_aliases.add(target.id)
                    changed = True
                if (
                    aliases_importlib_loader
                    and target.id not in importlib_loader_aliases
                ):
                    importlib_loader_aliases.add(target.id)
                    changed = True
                if (
                    aliases_importlib_loader_execution
                    and target.id not in importlib_loader_execution_aliases
                ):
                    importlib_loader_execution_aliases.add(target.id)
                    changed = True
                if (
                    aliases_importlib_direct_loader_factory
                    and target.id not in importlib_direct_loader_aliases
                ):
                    importlib_direct_loader_aliases.add(target.id)
                    changed = True
                if (
                    aliases_importlib_direct_loader_instance
                    and target.id not in importlib_direct_loader_instance_aliases
                ):
                    importlib_direct_loader_instance_aliases.add(target.id)
                    changed = True
                if (
                    aliases_importlib_direct_loader_execution
                    and target.id
                    not in importlib_direct_loader_execution_aliases
                ):
                    importlib_direct_loader_execution_aliases.add(target.id)
                    changed = True
                if aliases_runpy_execution and target.id not in runpy_execution_aliases:
                    runpy_execution_aliases.add(target.id)
                    changed = True
                if (
                    aliases_dynamic_execution
                    and target.id not in dynamic_execution_aliases
                ):
                    dynamic_execution_aliases.add(target.id)
                    changed = True
                if aliases_getattr and target.id not in getattr_aliases:
                    getattr_aliases.add(target.id)
                    changed = True
                if aliases_pty_spawn and target.id not in pty_spawn_aliases:
                    pty_spawn_aliases.add(target.id)
                    changed = True

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_os_process_reference(node.func):
            return True
        if _is_importlib_loader_execution_reference(node.func):
            return True
        if _is_importlib_direct_loader_execution_reference(node.func):
            return True
        if _is_runpy_execution_reference(node.func):
            return True
        if _is_dynamic_execution_reference(node.func):
            return True
        if _is_pty_spawn_reference(node.func):
            return True
        if (
            _is_dynamic_import_reference(node.func)
            or _is_import_module_reference(node.func)
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
standard_library_network_samples = (
    "import http.client",
    "from http.client import HTTPSConnection",
    "import ftplib as ftp",
    "from xmlrpc.client import ServerProxy",
    "import smtplib",
)
if not all(
    _blocked_imports(sample) for sample in standard_library_network_samples
):
    raise SystemExit("Python AST no-network self-test failed.")
dynamic_import_samples = (
    "import importlib\nimportlib.import_module('requests').get('https://invalid')",
    "from importlib import import_module\nimport_module('http.client')",
    "from importlib import import_module as load\nload('requests')",
    "import importlib as loader\nloader.import_module('http.client')",
    "import importlib.util\nimportlib.import_module('requests')",
    (
        "import importlib\nloader = importlib\n"
        "loader.import_module('requests')"
    ),
    (
        "from importlib import import_module\n"
        "loader = import_module\nloader('requests')"
    ),
    "__import__(module_name)",
    "from builtins import __import__ as load\nload('requests')",
    "import importlib\nimportlib.__import__('socket')",
    "import importlib as loader\nload = loader.__import__\nload('socket')",
)
if not all(_blocked_imports(sample) for sample in dynamic_import_samples):
    raise SystemExit("Python AST dynamic-import self-test failed.")
asyncio_network_samples = (
    "import asyncio\nawait asyncio.open_connection(host, port)",
    "import asyncio\nasyncio.start_server(handler, host, port)",
)
if not all(_blocked_imports(sample) for sample in asyncio_network_samples):
    raise SystemExit("Python AST asyncio network self-test failed.")
process_launch_samples = (
    "import os.path\nos.system('python -c \"import socket\"')",
    "import subprocess\nsubprocess.run(['python', '-c', 'import socket'])",
    "from subprocess import run\nrun(['python', '-c', 'import requests'])",
    "import os\nos.system('python -c \"import socket\"')",
    "import os\nos.popen('python -c \"import requests\"')",
    "import os\nos.execl('/bin/echo', 'echo', 'blocked')",
    "import os\nos.execle('/bin/echo', 'echo', 'blocked', {})",
    "import os\nos.execlp('echo', 'echo', 'blocked')",
    "import os\nos.execlpe('echo', 'echo', 'blocked', {})",
    "import os\nos.spawnl(os.P_NOWAIT, 'python', 'python')",
    "import os\nos.spawnlp(os.P_NOWAIT, 'python', 'python')",
    "import os\nos.posix_spawn('/bin/echo', ['echo'], {})",
    "from os import spawnl as launch\nlaunch(0, 'python', 'python')",
    "from os import posix_spawn as launch\nlaunch('/bin/echo', ['echo'], {})",
    "import os\nlauncher = os\nlauncher.posix_spawn('/bin/echo', ['echo'], {})",
    "import os\nlaunch = os.spawnl\nlaunch(0, 'python', 'python')",
    "from multiprocessing.connection import Client\nClient((host, port))",
)
if not all(_blocked_imports(sample) for sample in process_launch_samples):
    raise SystemExit("Python AST process-launch self-test failed.")
importlib_loader_samples = (
    """import importlib.util
spec = importlib.util.find_spec('socket')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)""",
)
if not all(_blocked_imports(sample) for sample in importlib_loader_samples):
    raise SystemExit("Python AST importlib-loader self-test failed.")
if not _blocked_imports(
    """import importlib.util
spec = importlib.util.find_spec('socket')
module = importlib.util.module_from_spec(spec)
run = spec.loader.exec_module
run(module)"""
):
    raise SystemExit("Python AST importlib-loader alias self-test failed.")
if not _blocked_imports(
    """import importlib.util
spec = importlib.util.spec_from_file_location('unsafe', '/tmp/unsafe.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)"""
):
    raise SystemExit("Python AST file-location loader self-test failed.")
if not _blocked_imports(
    """import importlib.util
spec = importlib.util.spec_from_file_location('unsafe', '/tmp/unsafe.py')
spec.loader.load_module('unsafe')"""
):
    raise SystemExit("Python AST spec-loader load_module self-test failed.")
if not _blocked_imports(
    """import importlib.util
spec = importlib.util.find_spec('socket')
loader = spec.loader
run = loader.load_module
run('socket')"""
):
    raise SystemExit("Python AST spec-loader load_module alias self-test failed.")
if not _blocked_imports(
    """import importlib.util as util
spec = util.find_spec('socket')
module = util.module_from_spec(spec)
spec.loader.exec_module(module)"""
):
    raise SystemExit("Python AST dotted-importlib alias self-test failed.")
if not _blocked_imports(
    """from importlib.machinery import SourceFileLoader
SourceFileLoader('unsafe', '/tmp/unsafe.py').load_module()"""
):
    raise SystemExit("Python AST importlib-machinery loader self-test failed.")
if not _blocked_imports(
    """import importlib.machinery as machinery
Loader = machinery.SourceFileLoader
Loader('unsafe', '/tmp/unsafe.py').load_module()"""
):
    raise SystemExit("Python AST importlib-machinery factory alias self-test failed.")
if not _blocked_imports("from os import *\nsystem('blocked')"):
    raise SystemExit("Python AST os wildcard-import self-test failed.")
runpy_execution_samples = (
    "import runpy\nrunpy.run_path('/tmp/unsafe.py')",
    "from runpy import run_module\nrun_module('unsafe')",
)
if not all(_blocked_imports(sample) for sample in runpy_execution_samples):
    raise SystemExit("Python AST runpy execution self-test failed.")
dynamic_execution_samples = (
    "exec(\"import socket\")",
    "run = exec\nrun(\"import socket\")",
    "import builtins\nbuiltins.exec(\"import socket\")",
    "from builtins import exec as run\nrun(\"import socket\")",
    "eval(\"__import__('socket')\")",
    "from builtins import eval as run\nrun(\"__import__('socket')\")",
    "import builtins\ngetattr(builtins, 'exec')(\"import socket\")",
    "import builtins\nrun = getattr(builtins, 'eval')\nrun(\"__import__('socket')\")",
    "import builtins\nlookup = getattr\nlookup(builtins, 'exec')(\"import socket\")",
)
if not all(_blocked_imports(sample) for sample in dynamic_execution_samples):
    raise SystemExit("Python AST dynamic execution self-test failed.")
if not _blocked_imports("import _socket\n_socket.socket().connect(('127.0.0.1', 1))"):
    raise SystemExit("Python AST low-level socket self-test failed.")
pty_execution_samples = (
    "import pty\npty.spawn(['/bin/sh'])",
    "from pty import spawn\nspawn(['/bin/sh'])",
)
if not all(_blocked_imports(sample) for sample in pty_execution_samples):
    raise SystemExit("Python AST pty execution self-test failed.")
os_process_execution_samples = (
    "import os\nos.fork()",
    "from os import fork\nfork()",
    "import os\nos.forkpty()",
)
if not all(_blocked_imports(sample) for sample in os_process_execution_samples):
    raise SystemExit("Python AST os process execution self-test failed.")
if _blocked_imports("from .http import encode"):
    raise SystemExit("Python AST relative-import self-test failed.")
if _blocked_imports("import json\njson.loads('{}')"):
    raise SystemExit("Python AST no-network self-test failed.")
if _blocked_imports("import importlib\nimportlib.invalidate_caches()"):
    raise SystemExit("Python AST dynamic-import self-test failed.")
with tempfile.TemporaryDirectory() as temporary_directory:
    temporary_root = Path(temporary_directory)
    nested_module = temporary_root / "transports" / "http.py"
    nested_module.parent.mkdir()
    nested_module.write_text("import requests\n", encoding="utf-8")
    if nested_module not in _vaultsync_targets(temporary_root):
        raise SystemExit("Python AST no-network preflight must scan subpackages.")
for path in targets:
    if _blocked_imports(path.read_text(encoding="utf-8")):
        raise SystemExit("Network imports are not permitted in pairing primitives.")
print("Validated Python AST no-network policy.")
PY

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

artifact_scans = {
    Path("scripts/ci/atlasvault_python.sh"): 1,
    Path("scripts/ci/atlasvault_flutter.sh"): 1,
    Path("scripts/ci/atlasvault_swift.sh"): 1,
    Path(".github/workflows/atlasvault-platform-integration.yml"): 2,
}
for path, expected_count in artifact_scans.items():
    source = path.read_text(encoding="utf-8")
    if source.count("grep -Ei") < expected_count:
        raise SystemExit(f"{path} does not scan full generated artifact paths.")
for path in artifact_scans:
    source = path.read_text(encoding="utf-8")
    if r"\.atlasvault$" not in source or r"\.atlaspair$" not in source:
        raise SystemExit(
            f"{path} does not match generated AtlasVault artifact extensions."
        )
    if (
        "identity.*secret" not in source
        or "secret.*identity" not in source
    ):
        raise SystemExit(
            f"{path} does not scan generated identity-secret artifact paths."
        )
    if (
        "ephemeral.*private" not in source
        or "private.*ephemeral" not in source
    ):
        raise SystemExit(
            f"{path} does not scan generated ephemeral-private artifact paths."
        )
print("Validated case-insensitive generated-artifact scans.")
PY

forbidden="$(
  { find "$REPO_ROOT" -path "$REPO_ROOT/.git" -prune -o -type f -print |
      LC_ALL=C grep -Ei '\.atlasvault$|\.atlaspair$|identity.*secret|secret.*identity|ephemeral.*private|private.*ephemeral'; } || true
)"
if [[ -n "$forbidden" ]]; then
  printf 'Forbidden AtlasVault artifact found in the repository:\n%s\n' "$forbidden" >&2
  exit 1
fi
