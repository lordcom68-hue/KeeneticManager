param(
    [string]$ProjectPath = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    $ProjectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectPath "main.py") -PathType Leaf)) {
    throw "В указанной папке не найден main.py: $ProjectPath"
}

Get-Process python, pythonw -ErrorAction SilentlyContinue |
    Stop-Process -Force

& (Join-Path $PSScriptRoot "cleanup_project.ps1") -ProjectPath $ProjectPath

Set-Location -LiteralPath $ProjectPath
python .\main.py
exit $LASTEXITCODE
