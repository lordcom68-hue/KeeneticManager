[CmdletBinding()]
param(
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
)
$versionFile = Join-Path $projectRoot "core\app_version.py"
$versionText = Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8

function Read-VersionValue {
    param([Parameter(Mandatory = $true)][string]$Name)

    $match = [regex]::Match(
        $versionText,
        [regex]::Escape($Name) + '\s*=\s*"(?<value>[^"]+)"'
    )
    if (-not $match.Success) {
        throw "Не удалось определить $Name."
    }
    return $match.Groups["value"].Value
}

$version = Read-VersionValue -Name "APP_VERSION"
$status = Read-VersionValue -Name "APP_RELEASE_STATUS"
$track = Read-VersionValue -Name "APP_RELEASE_TRACK"

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Split-Path -Parent $projectRoot
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupName = "KeeneticManager_${version}_${status}_${track}_${timestamp}"
$backupRoot = Join-Path $OutputRoot $backupName
$zipPath = Join-Path $OutputRoot ($backupName + ".zip")

if (
    (Test-Path -LiteralPath $backupRoot) -or
    (Test-Path -LiteralPath $zipPath)
) {
    throw "Бекап с таким временем уже существует: $backupName"
}

$excludedDirectories = @(
    ".git",
    ".github",
    ".idea",
    ".vscode",
    ".venv",
    ".vs",
    ".patch_backups",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    "build",
    "dist",
    "database",
    "diagnostics",
    "diagnostics_history",
    "htmlcov",
    "logs",
    "venv"
)
$excludedExtensions = @(
    ".pyc",
    ".pyo",
    ".tmp",
    ".temp",
    ".bak",
    ".orig",
    ".rej",
    ".log"
)

$sourceFiles = @(
    Get-ChildItem -LiteralPath $projectRoot -Recurse -File -Force |
        Where-Object {
            $relativePath = $_.FullName.Substring($projectRoot.Length).TrimStart(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            )
            $segments = $relativePath -split '[\\/]'
            $directoryBlocked = @(
                $segments | Where-Object { $_ -in $excludedDirectories }
            ).Count -gt 0
            $fileBlocked = (
                $_.Extension -in $excludedExtensions -or
                $_.Name -eq ".coverage" -or
                $_.Name -like "keenetic_diagnostic_*.zip" -or
                $_.Name.EndsWith("~")
            )
            (-not $directoryBlocked) -and (-not $fileBlocked)
        }
)

New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null

foreach ($file in $sourceFiles) {
    $relativePath = $file.FullName.Substring($projectRoot.Length).TrimStart(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $destinationPath = Join-Path $backupRoot $relativePath
    $destinationDirectory = Split-Path -Parent $destinationPath
    New-Item -ItemType Directory -Path $destinationDirectory -Force |
        Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destinationPath -Force
}

$sourceBytes = [int64](
    $sourceFiles | Measure-Object -Property Length -Sum
).Sum
$info = @(
    "Keenetic Manager clean source backup",
    "Version: $version",
    "Release status: $status",
    "Release track: $track",
    "Created: $((Get-Date).ToString('o'))",
    "Source: $projectRoot",
    "Source files: $($sourceFiles.Count)",
    "Source bytes: $sourceBytes",
    "",
    "Excluded: user database, credentials, logs, diagnostics, caches,",
    "virtual environments, editor settings and generated build artefacts."
)
$info | Set-Content -LiteralPath (Join-Path $backupRoot "BACKUP_INFO.txt") `
    -Encoding UTF8

Compress-Archive -LiteralPath $backupRoot -DestinationPath $zipPath `
    -CompressionLevel Optimal

$backupFiles = @(Get-ChildItem -LiteralPath $backupRoot -Recurse -File -Force)
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash

Write-Host "Чистый бекап создан." -ForegroundColor Green
Write-Host "Версия: $version ($status / $track)"
Write-Host "Файлов: $($backupFiles.Count)"
Write-Host "Папка: $backupRoot"
Write-Host "Архив: $zipPath"
Write-Host "SHA256: $zipHash"
