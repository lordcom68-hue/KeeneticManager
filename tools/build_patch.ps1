[CmdletBinding()]
param(
    [string]$OutputRoot = "",
    [string[]]$SupportedBaseVersions = @(),
    [string]$Python = "python",
    [switch]$SkipQualityChecks
)

$ErrorActionPreference = "Stop"

$projectRoot = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
)
if (-not $SkipQualityChecks) {
    & (Join-Path $projectRoot "tools\quality_check.ps1") -Python $Python
    if ($LASTEXITCODE -ne 0) {
        throw "Проверка качества завершилась с кодом $LASTEXITCODE."
    }
}
$versionFile = Join-Path $projectRoot "core\app_version.py"
$versionText = Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8
$versionMatch = [regex]::Match(
    $versionText,
    'APP_VERSION\s*=\s*"(?<version>[^"]+)"'
)

if (-not $versionMatch.Success) {
    throw "Не удалось определить APP_VERSION."
}

$patchVersion = $versionMatch.Groups["version"].Value
$patchNotesPath = Join-Path $projectRoot (
    "docs\release_notes\PATCH_NOTES_$patchVersion.md"
)
if (-not (Test-Path -LiteralPath $patchNotesPath -PathType Leaf)) {
    throw "Файл примечаний к патчу не найден: $patchNotesPath"
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $projectRoot "dist\patches"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$packageName = "KeeneticManager_patch_$patchVersion"
$packageRoot = Join-Path $OutputRoot $packageName
$zipPath = Join-Path $OutputRoot ($packageName + ".zip")

if ($SupportedBaseVersions.Count -eq 0) {
    $SupportedBaseVersions = @($patchVersion)
    $changelogPath = Join-Path $projectRoot "docs\CHANGELOG.md"
    if (Test-Path -LiteralPath $changelogPath -PathType Leaf) {
        $changelog = Get-Content -LiteralPath $changelogPath -Raw -Encoding UTF8
        $previousVersion = [regex]::Matches(
            $changelog,
            '(?m)^##\s+(?<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)'
        ) | ForEach-Object {
            $_.Groups["version"].Value
        } | Where-Object {
            $_ -ne $patchVersion
        } | Select-Object -First 1

        if (-not [string]::IsNullOrWhiteSpace($previousVersion)) {
            $SupportedBaseVersions = @($previousVersion, $patchVersion)
        }
    }
}

$relativePaths = [System.Collections.Generic.List[string]]::new()
$rootFiles = @(
    ".gitignore",
    "main.py",
    "README.md",
    "requirements.txt",
    "requirements-build.txt",
    "requirements-dev.txt",
    "pyproject.toml"
)
$payloadDirectories = @("core", "ui", "tests", "tools", "docs")

foreach ($relativePath in $rootFiles) {
    $sourcePath = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Файл патча не найден: $relativePath"
    }
    $relativePaths.Add($relativePath.Replace("\", "/"))
}

foreach ($directoryName in $payloadDirectories) {
    $directoryPath = Join-Path $projectRoot $directoryName
    if (-not (Test-Path -LiteralPath $directoryPath -PathType Container)) {
        throw "Папка патча не найдена: $directoryName"
    }

    foreach ($file in Get-ChildItem -LiteralPath $directoryPath -Recurse -File) {
        if (
            $file.FullName -match '[\\/]__pycache__[\\/]' -or
            $file.Extension -in @(".pyc", ".pyo") -or
            $file.Name -match '(\.tmp|\.temp|\.bak|\.orig|\.rej)$'
        ) {
            continue
        }

        $relativePath = $file.FullName.Substring($projectRoot.Length).TrimStart(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        ).Replace("\", "/")
        $relativePaths.Add($relativePath)
    }
}

$payloadFiles = @($relativePaths | Sort-Object -Unique)
$removedFiles = @(
    "core/rci/redaction.py",
    "PATCH_NOTES_1.0.0-beta1.md"
) | Sort-Object -Unique

foreach ($relativePath in $removedFiles) {
    if ($relativePath -in $payloadFiles) {
        throw "Файл одновременно добавлен и удалён: $relativePath"
    }
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
if (Test-Path -LiteralPath $packageRoot) {
    Remove-Item -LiteralPath $packageRoot -Recurse -Force
}
if (Test-Path -LiteralPath $zipPath -PathType Leaf) {
    Remove-Item -LiteralPath $zipPath -Force
}

$payloadRoot = Join-Path $packageRoot "payload"
New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null

$manifestFiles = @()
foreach ($relativePath in $payloadFiles) {
    $nativeRelative = $relativePath.Replace(
        "/",
        [System.IO.Path]::DirectorySeparatorChar
    )
    $sourcePath = Join-Path $projectRoot $nativeRelative
    $destinationPath = Join-Path $payloadRoot $nativeRelative
    $destinationDirectory = Split-Path -Parent $destinationPath
    New-Item -ItemType Directory -Path $destinationDirectory -Force |
        Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force

    $manifestFiles += [ordered]@{
        path = $relativePath
        sha256 = (Get-FileHash -LiteralPath $destinationPath -Algorithm SHA256).Hash
    }
}

$statusMatch = [regex]::Match(
    $versionText,
    'APP_RELEASE_STATUS\s*=\s*"(?<value>[^"]+)"'
)
$trackMatch = [regex]::Match(
    $versionText,
    'APP_RELEASE_TRACK\s*=\s*"(?<value>[^"]+)"'
)
$manifest = [ordered]@{
    product = "Keenetic Manager"
    patch_version = $patchVersion
    release_status = $statusMatch.Groups["value"].Value
    release_track = $trackMatch.Groups["value"].Value
    supported_base_versions = @($SupportedBaseVersions | Sort-Object -Unique)
    created_at = (Get-Date).ToString("o")
    files = $manifestFiles
    removed_files = $removedFiles
}

$manifestPath = Join-Path $packageRoot "manifest.json"
$manifest | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath $manifestPath -Encoding UTF8

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install_patch.ps1") `
    -Destination (Join-Path $packageRoot "install_patch.ps1") `
    -Force
Copy-Item -LiteralPath $patchNotesPath `
    -Destination (Join-Path $packageRoot "PATCH_NOTES_$patchVersion.md") `
    -Force

Compress-Archive -LiteralPath $packageRoot -DestinationPath $zipPath `
    -CompressionLevel Optimal

$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
Write-Host "Пакет патча создан." -ForegroundColor Green
Write-Host "Файлов в payload: $($manifestFiles.Count)"
Write-Host "Папка: $packageRoot"
Write-Host "Архив: $zipPath"
Write-Host "SHA256: $zipHash"
