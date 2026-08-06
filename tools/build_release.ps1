[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$SkipInstaller,
    [switch]$SkipQualityChecks
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
)
$specPath = Join-Path $projectRoot "KeeneticManager.spec"
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
$distRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $projectRoot "dist")
)
$applicationDirectory = Join-Path $distRoot "KeeneticManager"
$applicationExe = Join-Path $applicationDirectory "KeeneticManager.exe"

Push-Location $projectRoot
try {
    if (-not $SkipQualityChecks) {
        $qualityScript = Join-Path $projectRoot "tools\quality_check.ps1"
        $testsPath = Join-Path $projectRoot "tests"
        if (
            (Test-Path -LiteralPath $qualityScript -PathType Leaf) -and
            (Test-Path -LiteralPath $testsPath -PathType Container)
        ) {
            & $qualityScript -Python $Python
            if ($LASTEXITCODE -ne 0) {
                throw "Проверка качества завершилась с кодом $LASTEXITCODE."
            }
        }
        else {
            Write-Host (
                "Чистая сборочная копия уже прошла проверки " +
                "перед экспортом; тесты в неё не включены."
            ) -ForegroundColor DarkGray
        }
    }

    & $Python -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw (
            "PyInstaller не установлен. Выполните: " +
            "$Python -m pip install -r requirements.txt -r requirements-build.txt"
        )
    }

    & $Python -m PyInstaller --noconfirm --clean $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller завершился с кодом $LASTEXITCODE."
    }
    if (-not (Test-Path -LiteralPath $applicationExe -PathType Leaf)) {
        throw "Не найден собранный файл: $applicationExe"
    }

    Write-Host "Windows-приложение создано: $applicationExe" -ForegroundColor Green

    if (-not $SkipInstaller) {
        $innoCandidates = @(
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
        ) | Where-Object {
            -not [string]::IsNullOrWhiteSpace($_) -and
            (Test-Path -LiteralPath $_ -PathType Leaf)
        }

        $iscc = $innoCandidates | Select-Object -First 1
        if ([string]::IsNullOrWhiteSpace($iscc)) {
            Write-Warning (
                "Inno Setup 6 не найден. Приложение собрано, " +
                "но установщик не создан."
            )
        }
        else {
            & $iscc (Join-Path $projectRoot "packaging\KeeneticManager.iss")
            if ($LASTEXITCODE -ne 0) {
                throw "Inno Setup завершился с кодом $LASTEXITCODE."
            }

            Write-Host (
                "Установщик создан в: " +
                (Join-Path $distRoot "installer")
            ) -ForegroundColor Green
        }
    }

    $portableOutput = [System.IO.Path]::GetFullPath(
        (Join-Path $distRoot "portable")
    )
    $portableDirectory = [System.IO.Path]::GetFullPath(
        (Join-Path $portableOutput "KeeneticManager-$version-portable")
    )
    $portableZip = [System.IO.Path]::GetFullPath(
        (Join-Path $portableOutput "KeeneticManager-$version-portable.zip")
    )
    foreach ($target in @($portableDirectory, $portableZip)) {
        if (-not $target.StartsWith(
            $portableOutput + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Небезопасный путь portable-сборки: $target"
        }
    }

    New-Item -ItemType Directory -Path $portableOutput -Force | Out-Null
    if (Test-Path -LiteralPath $portableDirectory) {
        Remove-Item -LiteralPath $portableDirectory -Recurse -Force
    }
    if (Test-Path -LiteralPath $portableZip) {
        Remove-Item -LiteralPath $portableZip -Force
    }
    Copy-Item -LiteralPath $applicationDirectory `
        -Destination $portableDirectory -Recurse -Force

    "Keenetic Manager portable mode`n" | Set-Content -LiteralPath (
        Join-Path $portableDirectory "portable.flag"
    ) -Encoding UTF8
    $portableProfile = [ordered]@{
        profile_id = "portable"
        storage_namespace = "KeeneticManager-Portable"
        settings_organization = "KeeneticManagerPortable"
        settings_application = "KeeneticManager-Portable"
        router_credential_service = "KeeneticManager.Portable.Router.Admin"
        smb_credential_service = "KeeneticManager.Portable.SMB"
        legacy_router_credential_services = @(
            "KeeneticManager.1.0.1-beta.Portable.Router.Admin",
            "KeeneticManager.1.0.0-beta1.Portable.Router.Admin"
        )
        legacy_smb_credential_services = @(
            "KeeneticManager.1.0.1-beta.Portable.SMB",
            "KeeneticManager.1.0.0-beta1.Portable.SMB"
        )
    }
    $portableProfile | ConvertTo-Json | Set-Content -LiteralPath (
        Join-Path $portableDirectory "portable_profile.json"
    ) -Encoding UTF8

    Compress-Archive -LiteralPath $portableDirectory `
        -DestinationPath $portableZip -CompressionLevel Optimal
    Write-Host "Portable-сборка создана: $portableZip" -ForegroundColor Green
}
finally {
    Pop-Location
}
