[CmdletBinding()]
param(
    [string]$ProjectPath = "",
    [switch]$Force,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

$packageRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$manifestPath = Join-Path $packageRoot "manifest.json"
$payloadRoot = Join-Path $packageRoot "payload"

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Не найден manifest.json рядом с установщиком: $manifestPath"
}

if (-not (Test-Path -LiteralPath $payloadRoot -PathType Container)) {
    throw "Не найдена папка payload рядом с установщиком: $payloadRoot"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
    ConvertFrom-Json
$patchVersion = [string]$manifest.patch_version

if ([string]::IsNullOrWhiteSpace($patchVersion)) {
    throw "В manifest.json не указана версия патча."
}

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    $candidates = @(
        (Get-Location).Path,
        (Split-Path -Parent $packageRoot),
        (Split-Path -Parent (Split-Path -Parent $packageRoot))
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate "main.py") -PathType Leaf) {
            $ProjectPath = $candidate
            break
        }
    }
}

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    throw "Укажите папку проекта: .\install_patch.ps1 -ProjectPath D:\path\KeeneticManager"
}

if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
    throw "Папка проекта не найдена: $ProjectPath"
}

$ProjectPath = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath $ProjectPath).Path
)
$projectPrefix = $ProjectPath.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar

if (-not (Test-Path -LiteralPath (Join-Path $ProjectPath "main.py") -PathType Leaf)) {
    throw "В целевой папке не найден main.py: $ProjectPath"
}

$versionFile = Join-Path $ProjectPath "core\app_version.py"
if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) {
    throw "Не найден core\app_version.py: $ProjectPath"
}

$versionText = Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8
$versionMatch = [regex]::Match(
    $versionText,
    'APP_VERSION\s*=\s*"(?<version>[^"]+)"'
)
if (-not $versionMatch.Success) {
    throw "Не удалось определить текущую версию проекта."
}

$currentVersion = $versionMatch.Groups["version"].Value
$supportedVersions = @($manifest.supported_base_versions | ForEach-Object {
    [string]$_
})

if (($currentVersion -notin $supportedVersions) -and (-not $Force)) {
    $supportedText = $supportedVersions -join ", "
    throw "Патч $patchVersion рассчитан на версии: $supportedText. Найдена версия $currentVersion. Для принудительной установки используйте -Force."
}

function Get-SafeTargetPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    $nativeRelative = $RelativePath.Replace(
        "/",
        [System.IO.Path]::DirectorySeparatorChar
    )
    $fullPath = [System.IO.Path]::GetFullPath(
        (Join-Path $ProjectPath $nativeRelative)
    )

    if (-not $fullPath.StartsWith(
        $projectPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Недопустимый путь в манифесте: $RelativePath"
    }

    return $fullPath
}

# Полная проверка пакета выполняется до изменения проекта.
foreach ($file in @($manifest.files)) {
    $relativePath = [string]$file.path
    $sourcePath = Join-Path $payloadRoot $relativePath.Replace(
        "/",
        [System.IO.Path]::DirectorySeparatorChar
    )

    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "В payload отсутствует файл: $relativePath"
    }

    $actualHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
    $expectedHash = [string]$file.sha256
    if (-not [string]::Equals(
        $actualHash,
        $expectedHash,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Контрольная сумма не совпала: $relativePath"
    }

    $null = Get-SafeTargetPath -RelativePath $relativePath
}

foreach ($relativePathValue in @($manifest.removed_files)) {
    $relativePath = [string]$relativePathValue
    if ([string]::IsNullOrWhiteSpace($relativePath)) {
        throw "В removed_files указан пустой путь."
    }
    $null = Get-SafeTargetPath -RelativePath $relativePath
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $ProjectPath (
    ".patch_backups\$patchVersion`_$timestamp"
)
$createdFiles = @()
$installedFiles = @()
$removedFiles = @()

New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null

try {
    foreach ($file in @($manifest.files)) {
        $relativePath = [string]$file.path
        $nativeRelative = $relativePath.Replace(
            "/",
            [System.IO.Path]::DirectorySeparatorChar
        )
        $sourcePath = Join-Path $payloadRoot $nativeRelative
        $targetPath = Get-SafeTargetPath -RelativePath $relativePath
        $targetDirectory = Split-Path -Parent $targetPath

        New-Item -ItemType Directory -Path $targetDirectory -Force |
            Out-Null

        if (Test-Path -LiteralPath $targetPath -PathType Leaf) {
            $backupPath = Join-Path $backupRoot $nativeRelative
            $backupDirectory = Split-Path -Parent $backupPath
            New-Item -ItemType Directory -Path $backupDirectory -Force |
                Out-Null
            Copy-Item -LiteralPath $targetPath -Destination $backupPath -Force
        }
        else {
            $createdFiles += $targetPath
        }

        Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
        $installedFiles += $targetPath
    }

    foreach ($relativePathValue in @($manifest.removed_files)) {
        $relativePath = [string]$relativePathValue
        $nativeRelative = $relativePath.Replace(
            "/",
            [System.IO.Path]::DirectorySeparatorChar
        )
        $targetPath = Get-SafeTargetPath -RelativePath $relativePath

        if (Test-Path -LiteralPath $targetPath -PathType Leaf) {
            $backupPath = Join-Path $backupRoot $nativeRelative
            $backupDirectory = Split-Path -Parent $backupPath
            New-Item -ItemType Directory -Path $backupDirectory -Force |
                Out-Null
            Copy-Item -LiteralPath $targetPath -Destination $backupPath -Force
            Remove-Item -LiteralPath $targetPath -Force
            $removedFiles += $targetPath
        }
    }

    $installedVersionText = Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8
    if ($installedVersionText -notmatch ('APP_VERSION\s*=\s*"' + [regex]::Escape($patchVersion) + '"')) {
        throw "После копирования не установилась версия $patchVersion."
    }

    if (-not $SkipTests) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) {
            throw "Python не найден. Повторите установку с -SkipTests либо добавьте Python в PATH."
        }

        Push-Location $ProjectPath
        try {
            & $pythonCommand.Source -m unittest discover `
                -s tests `
                -p "test_*.py"
            if ($LASTEXITCODE -ne 0) {
                throw "Проверка установленного патча завершилась с ошибкой."
            }
        }
        finally {
            Pop-Location
        }
    }
}
catch {
    $installError = $_

    foreach ($file in @($manifest.files)) {
        $relativePath = [string]$file.path
        $nativeRelative = $relativePath.Replace(
            "/",
            [System.IO.Path]::DirectorySeparatorChar
        )
        $targetPath = Get-SafeTargetPath -RelativePath $relativePath
        $backupPath = Join-Path $backupRoot $nativeRelative

        if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
            Copy-Item -LiteralPath $backupPath -Destination $targetPath -Force
        }
        elseif ($targetPath -in $createdFiles) {
            Remove-Item -LiteralPath $targetPath -Force -ErrorAction SilentlyContinue
        }
    }


    foreach ($relativePathValue in @($manifest.removed_files)) {
        $relativePath = [string]$relativePathValue
        $nativeRelative = $relativePath.Replace(
            "/",
            [System.IO.Path]::DirectorySeparatorChar
        )
        $targetPath = Get-SafeTargetPath -RelativePath $relativePath
        $backupPath = Join-Path $backupRoot $nativeRelative

        if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
            $targetDirectory = Split-Path -Parent $targetPath
            New-Item -ItemType Directory -Path $targetDirectory -Force |
                Out-Null
            Copy-Item -LiteralPath $backupPath -Destination $targetPath -Force
        }
    }

    throw "Установка отменена, исходные файлы восстановлены: $($installError.Exception.Message)"
}

Write-Host "Патч Keenetic Manager $patchVersion установлен." -ForegroundColor Green
Write-Host "Обновлено файлов: $($installedFiles.Count)"
Write-Host "Удалено устаревших файлов: $($removedFiles.Count)"
Write-Host "Резервная копия: $backupRoot"
Write-Host "База данных, журналы и диагностика не изменялись."

