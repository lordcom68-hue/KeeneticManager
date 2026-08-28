[CmdletBinding()]
param(
    [string]$OutputRoot = "",
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
    throw "Не удалось определить версию приложения."
}
$version = $versionMatch.Groups["version"].Value
if ($version -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$') {
    throw "Версия содержит недопустимые символы: $version"
}

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Split-Path -Parent $projectRoot
}
$outputRootPath = [System.IO.Path]::GetFullPath($OutputRoot)
$destination = [System.IO.Path]::GetFullPath(
    (Join-Path $outputRootPath "KeeneticManager-$version-clean")
)

if ($destination -eq $projectRoot) {
    throw "Каталог назначения совпадает с рабочим проектом."
}
if (-not $destination.StartsWith(
    $outputRootPath + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Каталог назначения находится вне OutputRoot."
}
if (Test-Path -LiteralPath $destination) {
    throw "Каталог уже существует: $destination"
}

$rootFiles = @(
    "main.py",
    "requirements.txt",
    "requirements-build.txt",
    "constraints-release.txt",
    "pyproject.toml",
    "KeeneticManager.spec",
    "BUILD.md"
)
$runtimeDirectories = @(
    "core",
    "ui",
    "assets",
    "packaging"
)
$toolFiles = @(
    "tools\build_release.ps1",
    "tools\sign_windows_binary.ps1",
    "tools\verify_release.ps1",
    "tools\verify_upgrade.ps1",
    "tools\upgrade_fixture.py"
)
$excludedNames = @(
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache"
)
$excludedExtensions = @(
    ".pyc",
    ".pyo",
    ".db",
    ".log",
    ".jsonl",
    ".zip",
    ".tmp",
    ".bak"
)

New-Item -ItemType Directory -Path $destination -Force | Out-Null

function Copy-ReleaseFile {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $source = Join-Path $projectRoot $RelativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Не найден обязательный файл: $RelativePath"
    }
    $target = Join-Path $destination $RelativePath
    $targetDirectory = Split-Path -Parent $target
    New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Force
}

foreach ($relativePath in $rootFiles + $toolFiles) {
    Copy-ReleaseFile -RelativePath $relativePath
}

$releaseProfile = [ordered]@{
    profile_id = "beta"
    storage_namespace = "KeeneticManager-Beta"
    settings_organization = "KeeneticManagerBeta"
    settings_application = "KeeneticManager-Beta"
    router_credential_service = "KeeneticManager.Beta.Router.Admin"
    smb_credential_service = "KeeneticManager.Beta.SMB"
    legacy_storage_namespaces = @(
        "KeeneticManager-Beta-1.0.1-beta",
        "KeeneticManager-Beta-1.0.0-beta1"
    )
    legacy_settings_applications = @(
        "KeeneticManager-1.0.1-beta",
        "KeeneticManager-1.0.0-beta1"
    )
    legacy_router_credential_services = @(
        "KeeneticManager.1.0.1-beta.Router.Admin",
        "KeeneticManager.1.0.0-beta1.Router.Admin"
    )
    legacy_smb_credential_services = @(
        "KeeneticManager.1.0.1-beta.SMB",
        "KeeneticManager.1.0.0-beta1.SMB"
    )
}
$releaseProfile | ConvertTo-Json |
    Set-Content -LiteralPath (
        Join-Path $destination "release_profile.json"
    ) -Encoding UTF8

foreach ($directory in $runtimeDirectories) {
    $sourceDirectory = Join-Path $projectRoot $directory
    foreach ($file in Get-ChildItem -LiteralPath $sourceDirectory -Recurse -File -Force) {
        $relativePath = $file.FullName.Substring($projectRoot.Length).TrimStart(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        )
        $segments = $relativePath -split '[\\/]'
        if (
            @($segments | Where-Object { $_ -in $excludedNames }).Count -gt 0 -or
            $file.Extension -in $excludedExtensions
        ) {
            continue
        }
        Copy-ReleaseFile -RelativePath $relativePath
    }
}

$forbiddenDirectories = @(
    "tests",
    "database",
    "logs",
    "diagnostics",
    "diagnostics_history"
)
foreach ($name in $forbiddenDirectories) {
    if (Test-Path -LiteralPath (Join-Path $destination $name)) {
        throw "В чистой копии найден запрещённый каталог: $name"
    }
}

$forbiddenFiles = @(
    Get-ChildItem -LiteralPath $destination -Recurse -File -Force |
        Where-Object {
            $_.Extension -in $excludedExtensions -or
            $_.Name -match '(?i)(credential|password|secret).*\.(json|txt|ini)$'
        }
)
if ($forbiddenFiles.Count -gt 0) {
    throw "В чистой копии найдены запрещённые файлы."
}

$releaseFiles = @(
    Get-ChildItem -LiteralPath $destination -Recurse -File -Force
)
$releaseBytes = [int64](
    $releaseFiles | Measure-Object -Property Length -Sum
).Sum
$releaseInfo = @(
    "Keenetic Manager clean release source",
    "Version: $version",
    "Created: $((Get-Date).ToString('o'))",
    "Payload files: $($releaseFiles.Count)",
    "Bytes: $releaseBytes",
    "Contains user data: no",
    "Contains credentials: no",
    "Contains tests: no"
)
$releaseInfo | Set-Content -LiteralPath (
    Join-Path $destination "RELEASE_INFO.txt"
) -Encoding UTF8

Write-Host "Чистая сборочная копия создана." -ForegroundColor Green
Write-Host "Версия: $version"
Write-Host "Файлов: $($releaseFiles.Count + 1)"
Write-Host "Путь: $destination"
