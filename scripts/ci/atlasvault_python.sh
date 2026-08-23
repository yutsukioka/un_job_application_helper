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
if script.index("\ndef _blocked_imports") > script.rindex(
    'python -m pytest "${focused_tests[@]}"'
):
    raise SystemExit("VaultSync pytest must follow the complete AST preflight.")
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
        "nntplib",
        "poplib",
        "requests",
        "smtplib",
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
        "module_from_spec": set(),
    }
    importlib_spec_aliases = set()
    importlib_loader_aliases = set()
    os_module_aliases = set()
    os_process_aliases = set()
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
                elif (
                    alias.name.startswith("importlib.")
                    and alias.asname is None
                ):
                    importlib_module_aliases.add("importlib")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and module.partition(".")[0] in blocked_import_roots:
                return True
            if module == "builtins":
                for alias in node.names:
                    if alias.name == "__import__":
                        builtin_import_aliases.add(alias.asname or alias.name)
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
            elif module == "os":
                for alias in node.names:
                    if alias.name in os_process_apis:
                        os_process_aliases.add(alias.asname or alias.name)

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
            aliases_builtin_import = _is_builtin_import_reference(value)
            aliases_os_module = _is_os_module_reference(value)
            aliases_os_process = _is_os_process_reference(value)
            aliases_importlib_spec = (
                isinstance(value, ast.Call)
                and _is_importlib_loader_factory_reference(
                    value.func, "find_spec"
                )
            )
            aliases_importlib_loader = _is_importlib_loader_reference(value)
            if (
                not aliases_importlib
                and not aliases_import_module
                and not aliases_builtin_import
                and not aliases_os_module
                and not aliases_os_process
                and not aliases_importlib_spec
                and not aliases_importlib_loader
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

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_os_process_reference(node.func):
            return True
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "exec_module"
            and (
                _is_importlib_loader_reference(node.func.value)
                or (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id in importlib_loader_aliases
                )
            )
        ):
            return True
        if (
            _is_builtin_import_reference(node.func)
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
    "import subprocess\nsubprocess.run(['python', '-c', 'import socket'])",
    "from subprocess import run\nrun(['python', '-c', 'import requests'])",
    "import os\nos.system('python -c \"import socket\"')",
    "import os\nos.popen('python -c \"import requests\"')",
    "import os\nos.spawnl(os.P_NOWAIT, 'python', 'python')",
    "import os\nos.spawnlp(os.P_NOWAIT, 'python', 'python')",
    "import os\nos.posix_spawn('/bin/echo', ['echo'], {})",
    "from os import spawnl as launch\nlaunch(0, 'python', 'python')",
    "from os import posix_spawn as launch\nlaunch('/bin/echo', ['echo'], {})",
    "import os\nlauncher = os\nlauncher.posix_spawn('/bin/echo', ['echo'], {})",
    "import os\nlaunch = os.spawnl\nlaunch(0, 'python', 'python')",
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
    if source.count("-iname '*.atlasvault'") < expected_count:
        raise SystemExit(
            f"{path} does not case-insensitively scan generated AtlasVault artifacts."
        )
    if source.count("-iname '*.atlaspair'") < expected_count:
        raise SystemExit(
            f"{path} does not case-insensitively scan generated AtlasPair artifacts."
        )
for path in artifact_scans:
    source = path.read_text(encoding="utf-8")
    if "-iname '*identity*secret*'" not in source or "-iname '*secret*identity*'" not in source:
        raise SystemExit(
            f"{path} does not scan generated identity-secret artifacts."
        )
    if "-iname '*ephemeral*private*'" not in source or "-iname '*private*ephemeral*'" not in source:
        raise SystemExit(
            f"{path} does not scan generated ephemeral-private artifacts."
        )
print("Validated case-insensitive generated-artifact scans.")
PY

forbidden="$(find "$REPO_ROOT" -path "$REPO_ROOT/.git" -prune -o -type f \( -iname '*.atlasvault' -o -iname '*.atlaspair' -o -iname '*identity*secret*' -o -iname '*secret*identity*' -o -iname '*ephemeral*private*' -o -iname '*private*ephemeral*' \) -print)"
if [[ -n "$forbidden" ]]; then
  printf 'Forbidden AtlasVault artifact found in the repository:\n%s\n' "$forbidden" >&2
  exit 1
fi
