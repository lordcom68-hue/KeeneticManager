[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [string]$Version = "",
    [string]$Python = "python",
    [switch]$SkipInstallerSmoke
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Join-Path $PSScriptRoot ".."
}
$projectRootPath = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath $ProjectRoot).Path
)
if ([string]::IsNullOrWhiteSpace($Version)) {
    $versionText = Get-Content -LiteralPath (
        Join-Path $projectRootPath "core\app_version.py"
    ) -Raw -Encoding UTF8
    $versionMatch = [regex]::Match(
        $versionText,
        'APP_VERSION\s*=\s*"(?<version>[^"]+)"'
    )
    if (-not $versionMatch.Success) {
        throw "Unable to determine the application version."
    }
    $Version = $versionMatch.Groups["version"].Value
}
if ($Version -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$') {
    throw "Version contains unsupported characters: $Version"
}

$distRoot = Join-Path $projectRootPath "dist"
$portableZip = Join-Path $distRoot (
    "portable\KeeneticManager-$Version-portable.zip"
)
$installer = Join-Path $distRoot (
    "installer\KeeneticManager-$Version-Setup.exe"
)
$verificationRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRootPath "build\release_verification")
)
$allowedBuildRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRootPath "build")
) + [System.IO.Path]::DirectorySeparatorChar
if (-not $verificationRoot.StartsWith(
    $allowedBuildRoot,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Unsafe release verification path: $verificationRoot"
}

function Invoke-SmokeExecutable {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    $process = Start-Process -FilePath $Executable `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(20000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "The application did not finish the smoke test in 20 seconds."
    }
    if ($process.ExitCode -ne 0) {
        throw "Smoke test exited with code $($process.ExitCode)."
    }
}

function Assert-EmptyRouterDatabase {
    param([Parameter(Mandatory = $true)][string]$DatabasePath)
    $script = @'
import sqlite3
import sys

path = sys.argv[1]
connection = sqlite3.connect('file:' + path + '?mode=ro', uri=True)
try:
    count = connection.execute('select count(*) from routers').fetchone()[0]
finally:
    connection.close()
raise SystemExit(0 if count == 0 else 3)
'@
    & $Python -c $script $DatabasePath
    if ($LASTEXITCODE -ne 0) {
        throw "Saved routers were found in a clean release profile."
    }
}

$previousSmokeTimeout = $env:KEENETIC_MANAGER_RELEASE_SMOKE_TEST_MS
$previousQtPlatform = $env:QT_QPA_PLATFORM
$previousDataDir = $env:KEENETIC_MANAGER_DATA_DIR
$previousSettingsFile = $env:KEENETIC_MANAGER_SETTINGS_FILE
$installedDirectory = Join-Path $verificationRoot "installed"
$uninstaller = Join-Path $installedDirectory "unins000.exe"
$uninstallRegistryKey = (
    "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\" +
    "{48E7B014-6BBF-45E4-97F7-C31BB7FCD240}_is1"
)
$registryBackup = Join-Path $verificationRoot "previous_uninstall_entry.reg"
$hadExistingUninstallEntry = $false

try {
    if (Test-Path -LiteralPath $verificationRoot) {
        Remove-Item -LiteralPath $verificationRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $verificationRoot -Force | Out-Null
    if (-not (Test-Path -LiteralPath $portableZip -PathType Leaf)) {
        throw "Portable archive was not found: $portableZip"
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($portableZip)
    try {
        $entries = @(
            $archive.Entries |
                ForEach-Object { $_.FullName.Replace('\', '/') }
        )
        foreach ($requiredPattern in @(
            '(^|/)KeeneticManager\.exe$',
            '(^|/)portable\.flag$',
            '(^|/)portable_profile\.json$'
        )) {
            if (-not ($entries -match $requiredPattern)) {
                throw "A required portable archive file is missing."
            }
        }
        $forbiddenEntries = @(
            $entries | Where-Object {
                $_ -match '(^|/)(database|logs|diagnostics|diagnostics_history)(/|$)' -or
                $_ -match '\.(db|log|jsonl|bak|tmp)$'
            }
        )
        if ($forbiddenEntries.Count -gt 0) {
            throw "The portable archive contains user data."
        }
    }
    finally {
        $archive.Dispose()
    }

    $portableExtract = Join-Path $verificationRoot "portable"
    Expand-Archive -LiteralPath $portableZip -DestinationPath (
        $portableExtract
    ) -Force
    $portableExecutables = @(
        Get-ChildItem -LiteralPath $portableExtract -Recurse -File |
            Where-Object { $_.Name -eq "KeeneticManager.exe" }
    )
    if ($portableExecutables.Count -ne 1) {
        throw "Exactly one portable executable was expected."
    }

    $env:KEENETIC_MANAGER_RELEASE_SMOKE_TEST_MS = "1500"
    $env:QT_QPA_PLATFORM = "offscreen"
    Remove-Item Env:KEENETIC_MANAGER_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:KEENETIC_MANAGER_SETTINGS_FILE -ErrorAction SilentlyContinue
    $portableExecutable = $portableExecutables[0]
    Invoke-SmokeExecutable `
        -Executable $portableExecutable.FullName `
        -WorkingDirectory $portableExecutable.DirectoryName
    $portableDatabases = @(
        Get-ChildItem -LiteralPath $portableExecutable.DirectoryName `
            -Recurse -File -Filter "routers.db"
    )
    if ($portableDatabases.Count -ne 1) {
        throw "The portable profile did not create an isolated database."
    }
    Assert-EmptyRouterDatabase -DatabasePath $portableDatabases[0].FullName
    Write-Host "Portable smoke test passed." -ForegroundColor Green

    if (-not $SkipInstallerSmoke) {
        if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
            Write-Warning "Installer not found; installer smoke test skipped."
        }
        else {
            $savedErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = "SilentlyContinue"
            & reg.exe query $uninstallRegistryKey 2>$null | Out-Null
            $registryQueryExitCode = $LASTEXITCODE
            $ErrorActionPreference = $savedErrorActionPreference
            if ($registryQueryExitCode -eq 0) {
                & reg.exe export $uninstallRegistryKey $registryBackup /y `
                    *> $null
                if ($LASTEXITCODE -ne 0) {
                    throw "Unable to preserve the existing uninstall entry."
                }
                $hadExistingUninstallEntry = $true
            }
            $installerLog = Join-Path $verificationRoot "installer.log"
            $arguments = @(
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/NOCLOSEAPPLICATIONS",
                "/NORESTARTAPPLICATIONS",
                "/NOICONS",
                ('/DIR="{0}"' -f $installedDirectory),
                ('/LOG="{0}"' -f $installerLog)
            )
            $installProcess = Start-Process -FilePath $installer `
                -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
            if ($installProcess.ExitCode -ne 0) {
                throw "Installer exited with code $($installProcess.ExitCode)."
            }
            $installedExecutable = Join-Path (
                $installedDirectory
            ) "KeeneticManager.exe"
            if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) {
                throw "Installed executable was not found."
            }

            $installedData = Join-Path $verificationRoot "installed-data"
            $env:KEENETIC_MANAGER_DATA_DIR = $installedData
            $env:KEENETIC_MANAGER_SETTINGS_FILE = Join-Path (
                $installedData
            ) "settings.ini"
            Invoke-SmokeExecutable `
                -Executable $installedExecutable `
                -WorkingDirectory $installedDirectory
            $installedDatabase = Join-Path (
                $installedData
            ) "database\routers.db"
            if (-not (Test-Path -LiteralPath $installedDatabase -PathType Leaf)) {
                throw "Installed build did not create an isolated database."
            }
            Assert-EmptyRouterDatabase -DatabasePath $installedDatabase
            Write-Host "Installer smoke test passed." -ForegroundColor Green
        }
    }

    Get-FileHash -Algorithm SHA256 -LiteralPath @(
        $portableZip,
        $installer
    ) -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "$($_.Hash)  $([System.IO.Path]::GetFileName($_.Path))"
    }
}
finally {
    if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
        $uninstallProcess = Start-Process -FilePath $uninstaller `
            -ArgumentList @(
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART"
            ) -WindowStyle Hidden -Wait -PassThru
        if ($uninstallProcess.ExitCode -ne 0) {
            Write-Warning (
                "Test installation was not removed: exit code " +
                $uninstallProcess.ExitCode
            )
        }
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
        @{ Name = "KEENETIC_MANAGER_RELEASE_SMOKE_TEST_MS"; Value = $previousSmokeTimeout },
        @{ Name = "QT_QPA_PLATFORM"; Value = $previousQtPlatform },
        @{ Name = "KEENETIC_MANAGER_DATA_DIR"; Value = $previousDataDir },
        @{ Name = "KEENETIC_MANAGER_SETTINGS_FILE"; Value = $previousSettingsFile }
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
