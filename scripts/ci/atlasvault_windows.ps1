Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$AppRoot = Join-Path $RepoRoot "apps\atlas_flutter"
$Workflow = Join-Path $RepoRoot ".github\workflows\atlasvault-cross-platform-security.yml"

if (-not (Test-Path -LiteralPath $Workflow -PathType Leaf)) {
    throw "The AtlasVault pull-request workflow is missing."
}

function Assert-LastExitCode([string] $Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed."
    }
}

Push-Location $AppRoot
try {
    & flutter pub get
    Assert-LastExitCode "flutter pub get"

    & dart format --output=none --set-exit-if-changed lib test integration_test
    Assert-LastExitCode "Dart format"

    & flutter analyze
    Assert-LastExitCode "Flutter analysis"

    $Focused = @(
        "test\atlas_vault_device_identity_test.dart",
        "test\atlas_vault_interoperability_test.dart",
        "test\atlas_vault_key_delivery_test.dart",
        "test\atlas_vault_pairing_test.dart",
        "test\atlas_vault_pairing_transaction_test.dart",
        "test\atlas_vault_pairing_view_test.dart",
        "test\atlas_vault_plaintext_migration_test.dart",
        "test\atlas_vault_trusted_devices_test.dart",
        "test\atlas_vault_windows_interoperability_test.dart",
        "test\atlas_vault_windows_storage_test.dart",
        "test\atlas_search_controller_test.dart"
    )
    & flutter test @Focused
    Assert-LastExitCode "Focused AtlasVault Flutter tests"

    & flutter test
    Assert-LastExitCode "Full Flutter tests"

    & flutter build windows --debug
    Assert-LastExitCode "Windows Debug build"

    & flutter build windows --release
    Assert-LastExitCode "Windows Release build"
}
finally {
    Pop-Location
}

$StorageSource = Join-Path $AppRoot "windows\runner\atlas_vault_windows_storage.cpp"
if (Select-String -LiteralPath $StorageSource -Pattern "CRYPTPROTECT_LOCAL_MACHINE" -Quiet) {
    throw "Machine-wide DPAPI is not permitted."
}
if (Select-String -LiteralPath $StorageSource -Pattern "SHAddToRecentDocs" -Quiet) {
    throw "AtlasVault document paths must not be added to recent documents."
}

$Forbidden = Get-ChildItem -LiteralPath $RepoRoot -Recurse -File |
    Where-Object {
        $_.FullName -notmatch '[\\/]\.git[\\/]' -and
        ($_.Extension -eq ".atlasvault" -or
         $_.Extension -eq ".atlaspair" -or
         $_.Name -match "(?i)identity.*secret|ephemeral.*private")
    }
if ($Forbidden) {
    throw "Forbidden AtlasVault artifact found in the repository."
}

