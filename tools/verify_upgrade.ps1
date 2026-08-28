[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string]$OldVersion = "1.0.11-beta",
    [string]$NewVersion = "",
    [string]$OldReleaseRoot = "",
    [string]$NewReleaseRoot = "",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}
$projectRootPath = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath $ProjectRoot).Path
)
if ([string]::IsNullOrWhiteSpace($NewVersion)) {
    $versionText = Get-Content -LiteralPath (
        Join-Path $projectRootPath "core\app_version.py"
    ) -Raw -Encoding UTF8
    $versionMatch = [regex]::Match(
        $versionText,
        'APP_VERSION\s*=\s*"(?<version>[^"]+)"'
    )
    if (-not $versionMatch.Success) {
        throw "Unable to determine the current application version."
    }
    $NewVersion = $versionMatch.Groups["version"].Value
}
foreach ($version in @($OldVersion, $NewVersion)) {
    if ($version -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$') {
        throw "Version contains unsupported characters: $version"
    }
}

$projectParent = Split-Path -Parent $projectRootPath
if ([string]::IsNullOrWhiteSpace($OldReleaseRoot)) {
    $OldReleaseRoot = Join-Path (
        $projectParent
    ) "KeeneticManager-$OldVersion-release"
}
if ([string]::IsNullOrWhiteSpace($NewReleaseRoot)) {
    $NewReleaseRoot = Join-Path (
        $projectParent
    ) "KeeneticManager-$NewVersion-release"
}
$oldReleasePath = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath $OldReleaseRoot).Path
)
$newReleasePath = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath $NewReleaseRoot).Path
)
$oldInstaller = Join-Path (
    $oldReleasePath
) "KeeneticManager-$OldVersion-Setup.exe"
$newInstaller = Join-Path (
    $newReleasePath
) "KeeneticManager-$NewVersion-Setup.exe"
$oldSource = Join-Path (
    $oldReleasePath
) "KeeneticManager-$OldVersion-clean"
$newSource = Join-Path (
    $newReleasePath
) "KeeneticManager-$NewVersion-clean"
foreach ($required in @(
    $oldInstaller,
    $newInstaller,
    (Join-Path $oldSource "core\database.py"),
    (Join-Path $newSource "core\database.py"),
    (Join-Path $oldSource "release_profile.json"),
    (Join-Path $newSource "release_profile.json")
)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required upgrade artifact was not found: $required"
    }
}

$oldProfile = Join-Path $oldSource "release_profile.json"
$newProfile = Join-Path $newSource "release_profile.json"
$oldProfileData = Get-Content -LiteralPath $oldProfile -Raw -Encoding UTF8 |
    ConvertFrom-Json
$newProfileData = Get-Content -LiteralPath $newProfile -Raw -Encoding UTF8 |
    ConvertFrom-Json
foreach ($property in @(
    "storage_namespace",
    "settings_organization",
    "settings_application",
    "router_credential_service"
)) {
    if ($oldProfileData.$property -ne $newProfileData.$property) {
        throw "Release profile changed across the upgrade: $property"
    }
}

$verificationRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRootPath "build\upgrade_verification")
)
$allowedBuildRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRootPath "build")
) + [System.IO.Path]::DirectorySeparatorChar
if (-not $verificationRoot.StartsWith(
    $allowedBuildRoot,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Unsafe upgrade verification path: $verificationRoot"
}
$installedDirectory = Join-Path $verificationRoot "installed"
$installedExecutable = Join-Path $installedDirectory "KeeneticManager.exe"
$dataDirectory = Join-Path $verificationRoot "profile"
$settingsFile = Join-Path $dataDirectory "settings.ini"
$stateFile = Join-Path $verificationRoot "fixture_state.json"
$fixtureScript = Join-Path $projectRootPath "tools\upgrade_fixture.py"
$reportDirectory = Join-Path $projectRootPath "diagnostics_history"
$reportFile = Join-Path (
    $reportDirectory
) "upgrade_${OldVersion}_to_${NewVersion}.json"

$uninstallRegistryPath = (
    "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows" +
    "\CurrentVersion\Uninstall" +
    "\{48E7B014-6BBF-45E4-97F7-C31BB7FCD240}_is1"
)
$uninstallRegistryKey = (
    "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\" +
    "{48E7B014-6BBF-45E4-97F7-C31BB7FCD240}_is1"
)
$registryBackup = Join-Path $verificationRoot "previous_uninstall_entry.reg"
$hadExistingUninstallEntry = $false
$fixtureSeeded = $false
$testUninstalled = $false

function Invoke-CheckedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )
    $process = Start-Process -FilePath $Executable `
        -ArgumentList $Arguments -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$Description exited with code $($process.ExitCode)."
    }
}

function Install-TestVersion {
    param(
        [Parameter(Mandatory = $true)][string]$Installer,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$LogName
    )
    Invoke-CheckedProcess -Executable $Installer -Description (
        "Installer $Version"
    ) -Arguments @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/NOCLOSEAPPLICATIONS",
        "/NORESTARTAPPLICATIONS",
        "/NOICONS",
        ('/DIR="{0}"' -f $installedDirectory),
        ('/LOG="{0}"' -f (Join-Path $verificationRoot $LogName))
    )
    if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) {
        throw "Installed executable was not found for version $Version."
    }
    $productVersion = (
        Get-Item -LiteralPath $installedExecutable
    ).VersionInfo.ProductVersion
    if ($productVersion -notlike "$Version*") {
        throw (
            "Installed product version mismatch: expected $Version, " +
            "found $productVersion."
        )
    }
}

function Invoke-SmokeExecutable {
    $process = Start-Process -FilePath $installedExecutable `
        -WorkingDirectory $installedDirectory `
        -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(20000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "The upgraded application did not exit its smoke run."
    }
    if ($process.ExitCode -ne 0) {
        throw "Application smoke run exited with code $($process.ExitCode)."
    }
}

function Invoke-FixtureAction {
    param(
        [Parameter(Mandatory = $true)][string]$Action,
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$Profile
    )
    & $Python $fixtureScript $Action `
        --source-root $SourceRoot `
        --profile $Profile `
        --data-dir $dataDirectory `
        --settings-file $settingsFile `
        --state-file $stateFile
    if ($LASTEXITCODE -ne 0) {
        throw "Upgrade fixture action failed: $Action"
    }
}

function Test-RegistryPointsToTestInstall {
    if (-not (Test-Path -LiteralPath $uninstallRegistryPath)) {
        return $false
    }
    $entry = Get-ItemProperty -LiteralPath $uninstallRegistryPath
    $installLocation = [string]$entry.InstallLocation
    return (
        -not [string]::IsNullOrWhiteSpace($installLocation) -and
        [System.IO.Path]::GetFullPath($installLocation).TrimEnd('\') -eq
        [System.IO.Path]::GetFullPath($installedDirectory).TrimEnd('\')
    )
}

$previousSmokeTimeout = $env:KEENETIC_MANAGER_RELEASE_SMOKE_TEST_MS
$previousQtPlatform = $env:QT_QPA_PLATFORM
$previousDataDir = $env:KEENETIC_MANAGER_DATA_DIR
$previousSettingsFile = $env:KEENETIC_MANAGER_SETTINGS_FILE
$startedAt = [DateTimeOffset]::Now

try {
    if (Test-Path -LiteralPath $verificationRoot) {
        Remove-Item -LiteralPath $verificationRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $verificationRoot -Force | Out-Null

    $savedErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & reg.exe query $uninstallRegistryKey 2>$null | Out-Null
    $registryQueryExitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorActionPreference
    if ($registryQueryExitCode -eq 0) {
        & reg.exe export $uninstallRegistryKey $registryBackup /y *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to preserve the existing uninstall entry."
        }
        $hadExistingUninstallEntry = $true
    }

    Invoke-FixtureAction -Action "seed" `
        -SourceRoot $oldSource -Profile $oldProfile
    $fixtureSeeded = $true
    Write-Host "Isolated upgrade fixture created." -ForegroundColor Green

    $env:KEENETIC_MANAGER_RELEASE_SMOKE_TEST_MS = "1500"
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:KEENETIC_MANAGER_DATA_DIR = $dataDirectory
    $env:KEENETIC_MANAGER_SETTINGS_FILE = $settingsFile

    Install-TestVersion -Installer $oldInstaller `
        -Version $OldVersion -LogName "install_old.log"
    Invoke-SmokeExecutable
    Invoke-FixtureAction -Action "assert" `
        -SourceRoot $oldSource -Profile $oldProfile
    Write-Host "$OldVersion baseline passed." -ForegroundColor Green

    Install-TestVersion -Installer $newInstaller `
        -Version $NewVersion -LogName "install_new.log"
    Invoke-SmokeExecutable
    Invoke-FixtureAction -Action "assert" `
        -SourceRoot $newSource -Profile $newProfile
    Write-Host "$NewVersion upgrade passed." -ForegroundColor Green

    $uninstallers = @(
        Get-ChildItem -LiteralPath $installedDirectory `
            -File -Filter "unins*.exe" |
            Sort-Object LastWriteTime -Descending
    )
    if ($uninstallers.Count -lt 1) {
        throw "The test uninstaller was not found."
    }
    Invoke-CheckedProcess -Executable $uninstallers[0].FullName `
        -Description "Test uninstaller" -Arguments @(
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART"
        )
    $testUninstalled = $true
    if (Test-Path -LiteralPath $installedExecutable -PathType Leaf) {
        throw "The test application remained after uninstall."
    }
    Invoke-FixtureAction -Action "assert" `
        -SourceRoot $newSource -Profile $newProfile
    Write-Host "User profile survived uninstall." -ForegroundColor Green

    New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
    $report = [ordered]@{
        schema_version = 1
        old_version = $OldVersion
        new_version = $NewVersion
        started_at = $startedAt.ToString("o")
        finished_at = [DateTimeOffset]::Now.ToString("o")
        old_installer_sha256 = (
            Get-FileHash -Algorithm SHA256 -LiteralPath $oldInstaller
        ).Hash
        new_installer_sha256 = (
            Get-FileHash -Algorithm SHA256 -LiteralPath $newInstaller
        ).Hash
        routers_preserved = $true
        settings_preserved = $true
        credential_preserved = $true
        plaintext_secret_in_database = $false
        user_profile_survived_uninstall = $true
        existing_install_registry_was_present = $hadExistingUninstallEntry
        result = "passed"
    }
    $report | ConvertTo-Json | Set-Content -LiteralPath (
        $reportFile
    ) -Encoding UTF8
    Write-Host "Upgrade verification passed: $reportFile" `
        -ForegroundColor Green
}
finally {
    if (
        -not $testUninstalled -and
        (Test-Path -LiteralPath $installedDirectory -PathType Container)
    ) {
        $remainingUninstallers = @(
            Get-ChildItem -LiteralPath $installedDirectory `
                -File -Filter "unins*.exe" -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending
        )
        if ($remainingUninstallers.Count -gt 0) {
            $cleanupProcess = Start-Process `
                -FilePath $remainingUninstallers[0].FullName `
                -ArgumentList @(
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART"
                ) -WindowStyle Hidden -Wait -PassThru
            if ($cleanupProcess.ExitCode -ne 0) {
                Write-Warning "The test installation could not be removed."
            }
        }
    }

    if ($fixtureSeeded -and (Test-Path -LiteralPath $stateFile -PathType Leaf)) {
        try {
            Invoke-FixtureAction -Action "cleanup" `
                -SourceRoot $newSource -Profile $newProfile
        }
        catch {
            Write-Warning "The synthetic credential could not be removed."
        }
    }

    if (Test-RegistryPointsToTestInstall) {
        Remove-Item -LiteralPath $uninstallRegistryPath `
            -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (
        $hadExistingUninstallEntry -and
        (Test-Path -LiteralPath $registryBackup -PathType Leaf)
    ) {
        & reg.exe import $registryBackup *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "The previous uninstall entry was not restored."
        }
    }

    foreach ($item in @(
        @{
            Name = "KEENETIC_MANAGER_RELEASE_SMOKE_TEST_MS"
            Value = $previousSmokeTimeout
        },
        @{ Name = "QT_QPA_PLATFORM"; Value = $previousQtPlatform },
        @{ Name = "KEENETIC_MANAGER_DATA_DIR"; Value = $previousDataDir },
        @{
            Name = "KEENETIC_MANAGER_SETTINGS_FILE"
            Value = $previousSettingsFile
        }
    )) {
        if ($null -eq $item.Value) {
            Remove-Item ("Env:" + $item.Name) -ErrorAction SilentlyContinue
        }
        else {
            Set-Item ("Env:" + $item.Name) $item.Value
        }
    }

    if (Test-Path -LiteralPath $verificationRoot) {
        Remove-Item -LiteralPath $verificationRoot -Recurse -Force
    }
}
