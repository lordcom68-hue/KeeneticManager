[CmdletBinding()]
param(
    [string]$ProjectPath = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    $ProjectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

$ProjectPath = [System.IO.Path]::GetFullPath($ProjectPath)
$testScript = Join-Path $ProjectPath "tools\credential_store_smoke_test.py"

if (-not (Test-Path -LiteralPath $testScript -PathType Leaf)) {
    throw "Не найден тест Windows Credential Manager: $testScript"
}

Push-Location $ProjectPath
try {
    $env:PYTHONUTF8 = "1"
    & python $testScript

    if ($LASTEXITCODE -ne 0) {
        throw "Проверка Windows Credential Manager завершилась с кодом $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
