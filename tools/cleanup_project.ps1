[CmdletBinding()]
param(
    [string]$ProjectPath = "",
    [switch]$RemoveRootDiagnosticArchives,
    [switch]$RemoveRuntimeHistory
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    $ProjectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

if (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
    throw "Папка проекта не найдена: $ProjectPath"
}

$ProjectPath = [System.IO.Path]::GetFullPath($ProjectPath)

if (-not (Test-Path -LiteralPath (Join-Path $ProjectPath "main.py") -PathType Leaf)) {
    throw "В указанной папке не найден main.py: $ProjectPath"
}

$script:removedFiles = 0
$script:removedDirectories = 0
$script:movedFiles = 0
$script:freedBytes = [int64]0
$script:protectedRoots = @(
    [System.IO.Path]::GetFullPath((Join-Path $ProjectPath "database")),
    [System.IO.Path]::GetFullPath((Join-Path $ProjectPath "diagnostics_history")),
    [System.IO.Path]::GetFullPath((Join-Path $ProjectPath "logs"))
)

function Test-IsProtectedPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $separator = [System.IO.Path]::DirectorySeparatorChar.ToString()

    foreach ($root in $script:protectedRoots) {
        if ([string]::Equals($fullPath, $root, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }

        $prefix = $root
        if (-not $prefix.EndsWith($separator)) {
            $prefix = $prefix + $separator
        }

        if ($fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    return $false
}

function Remove-ProjectFile {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.FileInfo]$File
    )

    if (-not $File.Exists) {
        return
    }

    try {
        $length = [int64]$File.Length
        Remove-Item -LiteralPath $File.FullName -Force -ErrorAction Stop
        $script:freedBytes += $length
        $script:removedFiles++
    }
    catch {
        Write-Warning "Не удалось удалить файл: $($File.FullName) — $($_.Exception.Message)"
    }
}

function Remove-ProjectDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [System.IO.DirectoryInfo]$Directory
    )

    if (-not $Directory.Exists) {
        return
    }

    try {
        $sizeInfo = Get-ChildItem -LiteralPath $Directory.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum
        $directoryBytes = [int64]0
        if ($null -ne $sizeInfo.Sum) {
            $directoryBytes = [int64]$sizeInfo.Sum
        }

        Remove-Item -LiteralPath $Directory.FullName -Recurse -Force -ErrorAction Stop
        $script:freedBytes += $directoryBytes
        $script:removedDirectories++
    }
    catch {
        Write-Warning "Не удалось удалить папку: $($Directory.FullName) — $($_.Exception.Message)"
    }
}

$temporaryDirectoryNames = @(
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".vs",
    "htmlcov",
    "build",
    "dist"
)

$temporaryDirectories = @(
    Get-ChildItem -LiteralPath $ProjectPath -Recurse -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object {
            (-not (Test-IsProtectedPath -Path $_.FullName)) -and
            (
                ($_.Name -in $temporaryDirectoryNames) -or
                ($_.Name -like "*.egg-info") -or
                ($_.Name -eq "patch_install_smoke") -or
                (
                    ($_.Parent.Name -eq "patches") -and
                    ($_.Name -like "KeeneticManager_patch_*")
                )
            )
        } |
        Sort-Object { $_.FullName.Length } -Descending
)

foreach ($directory in $temporaryDirectories) {
    Remove-ProjectDirectory -Directory $directory
}

$temporaryExtensions = @(
    ".pyc",
    ".pyo",
    ".tmp",
    ".temp",
    ".bak",
    ".orig",
    ".rej",
    ".part",
    ".swp",
    ".swo"
)

$temporaryFileNames = @(
    ".coverage",
    "Thumbs.db",
    "desktop.ini"
)

$temporaryFiles = @(
    Get-ChildItem -LiteralPath $ProjectPath -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object {
            (-not (Test-IsProtectedPath -Path $_.FullName)) -and
            (
                ($_.Extension -in $temporaryExtensions) -or
                ($_.Name -in $temporaryFileNames) -or
                $_.Name.EndsWith("~")
            )
        }
)

foreach ($file in $temporaryFiles) {
    Remove-ProjectFile -File $file
}

# Ротированные отладочные журналы из корня уже не используются приложением.
$rotatedRootLogs = @(
    Get-ChildItem -LiteralPath $ProjectPath -File -Filter "priority_debug.log.*" -ErrorAction SilentlyContinue
)
foreach ($rotatedLog in $rotatedRootLogs) {
    Remove-ProjectFile -File $rotatedLog
}

# Диагностические ZIP обычно являются пользовательскими артефактами и
# сохраняются. Флаг нужен для явной очистки временных копий в корне проекта.
if ($RemoveRootDiagnosticArchives) {
    $rootDiagnosticArchives = @(
        Get-ChildItem -LiteralPath $ProjectPath -File -Filter "keenetic_diagnostic_*.zip" -ErrorAction SilentlyContinue
    )
    foreach ($diagnosticArchive in $rootDiagnosticArchives) {
        Remove-ProjectFile -File $diagnosticArchive
    }
}

# Переносим старые журналы из корня в пользовательские данные без потери
# диагностики и без повторного загрязнения папки исходников.
$dataRootOverride = [string]$env:KEENETIC_MANAGER_DATA_DIR
if (-not [string]::IsNullOrWhiteSpace($dataRootOverride)) {
    $applicationDataRoot = [System.IO.Path]::GetFullPath($dataRootOverride)
}
elseif (-not [string]::IsNullOrWhiteSpace([string]$env:LOCALAPPDATA)) {
    $applicationDataRoot = Join-Path $env:LOCALAPPDATA "KeeneticManager"
}
else {
    $applicationDataRoot = Join-Path $env:USERPROFILE "AppData\Local\KeeneticManager"
}
$logsDirectory = Join-Path $applicationDataRoot "logs"
New-Item -ItemType Directory -Path $logsDirectory -Force | Out-Null

$legacyLogs = @(
    Get-ChildItem -LiteralPath $ProjectPath -File -Filter "priority_debug_router_*.log" -ErrorAction SilentlyContinue
)

foreach ($name in @("priority_debug.log", "hardware_guard.log")) {
    $candidatePath = Join-Path $ProjectPath $name
    if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
        $legacyLogs += Get-Item -LiteralPath $candidatePath
    }
}

foreach ($legacyLog in $legacyLogs) {
    $destination = Join-Path $logsDirectory $legacyLog.Name

    try {
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            $content = Get-Content -LiteralPath $legacyLog.FullName -Raw -ErrorAction Stop
            Add-Content -LiteralPath $destination -Value $content -Encoding UTF8
            Remove-ProjectFile -File $legacyLog
        }
        else {
            Move-Item -LiteralPath $legacyLog.FullName -Destination $destination -Force -ErrorAction Stop
            $script:movedFiles++
        }
    }
    catch {
        Write-Warning "Не удалось перенести журнал: $($legacyLog.FullName) — $($_.Exception.Message)"
    }
}

# История выпусков хранится в docs\CHANGELOG.md.
$oldPatchNotes = @(
    Get-ChildItem -LiteralPath $ProjectPath -File -Filter "PATCH_NOTES_*.txt" -ErrorAction SilentlyContinue
)
foreach ($patchNote in $oldPatchNotes) {
    Remove-ProjectFile -File $patchNote
}

# Удаляем только незавершённые временные файлы диагностики.
$historyDirectory = Join-Path $ProjectPath "diagnostics_history"
if (Test-Path -LiteralPath $historyDirectory -PathType Container) {
    $historyTemporaryFiles = @(
        Get-ChildItem -LiteralPath $historyDirectory -File -Force -ErrorAction SilentlyContinue |
            Where-Object { ($_.Name -like "*.tmp") -or ($_.Name -like "*.json.tmp") }
    )

    foreach ($historyFile in $historyTemporaryFiles) {
        Remove-ProjectFile -File $historyFile
    }
}

# Пустые модули ранней структуры проекта больше не используются.
$legacyEmptyFiles = @(
    "core\keendns.py",
    "core\router.py",
    "ui\styles.py",
    "ui\router_info.py",
    "ui\router_list.py"
)

foreach ($relativePath in $legacyEmptyFiles) {
    $candidatePath = Join-Path $ProjectPath $relativePath
    if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
        $candidate = Get-Item -LiteralPath $candidatePath
        if ($candidate.Length -eq 0) {
            Remove-ProjectFile -File $candidate
        }
    }
}

# GUI-раннеры должны находиться только во временной папке теста.
$accidentalGuiTests = @(
    Get-ChildItem -LiteralPath $ProjectPath -File -Filter "gui_smoke_test_*.py" -ErrorAction SilentlyContinue
)
foreach ($guiTest in $accidentalGuiTests) {
    Remove-ProjectFile -File $guiTest
}

# Удаляем оставшиеся пустые каталоги старой структуры.
foreach ($relativePath in @("config", "icons")) {
    $candidatePath = Join-Path $ProjectPath $relativePath
    if (Test-Path -LiteralPath $candidatePath -PathType Container) {
        $children = @(Get-ChildItem -LiteralPath $candidatePath -Force)
        if ($children.Count -eq 0) {
            Remove-ProjectDirectory -Directory (Get-Item -LiteralPath $candidatePath)
        }
    }
}

# Полная очистка рабочих журналов выполняется только по явному флагу.
# База роутеров при этом никогда не затрагивается.
if ($RemoveRuntimeHistory) {
    foreach ($relativeRoot in @("logs", "diagnostics_history")) {
        $runtimeRoot = Join-Path $ProjectPath $relativeRoot
        if (-not (Test-Path -LiteralPath $runtimeRoot -PathType Container)) {
            continue
        }

        $runtimeFiles = @(
            Get-ChildItem -LiteralPath $runtimeRoot -Recurse -File -Force -ErrorAction SilentlyContinue
        )
        foreach ($runtimeFile in $runtimeFiles) {
            Remove-ProjectFile -File $runtimeFile
        }
    }
}

$freedMb = [math]::Round($script:freedBytes / 1MB, 2)
Write-Host "Очистка завершена." -ForegroundColor Green
Write-Host "Удалено файлов: $($script:removedFiles)"
Write-Host "Удалено папок: $($script:removedDirectories)"
Write-Host "Перенесено журналов: $($script:movedFiles)"
Write-Host "Освобождено: $freedMb МБ"
if ($RemoveRootDiagnosticArchives) {
    Write-Host "Временные диагностические ZIP из корня удалены по явному флагу."
}
else {
    Write-Host "Диагностические ZIP сохранены."
}
if ($RemoveRuntimeHistory) {
    Write-Host "Рабочие журналы и история диагностики очищены по явному флагу."
    Write-Host "База данных не удалялась."
}
else {
    Write-Host "База данных, diagnostics_history и logs не удалялись."
}
