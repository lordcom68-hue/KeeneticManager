[CmdletBinding()]
param(
    [string]$Python = "python",
    [int]$MinimumCoverage = 45
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
)
$testsPath = Join-Path $projectRoot "tests"
if (-not (Test-Path -LiteralPath $testsPath -PathType Container)) {
    throw "Tests directory is missing: $testsPath"
}

Push-Location $projectRoot
try {
    & $Python -m ruff --version
    if ($LASTEXITCODE -ne 0) {
        throw (
            "Ruff is not installed. Run: " +
            "$Python -m pip install -r requirements-dev.txt"
        )
    }

    & $Python -m coverage --version
    if ($LASTEXITCODE -ne 0) {
        throw (
            "Coverage is not installed. Run: " +
            "$Python -m pip install -r requirements-dev.txt"
        )
    }

    & $Python -m compileall -q core ui tests tools main.py
    if ($LASTEXITCODE -ne 0) {
        throw "Python compilation failed with exit code $LASTEXITCODE."
    }

    & $Python -m ruff check core ui tests tools main.py
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff found errors."
    }

    & $Python -m coverage erase
    & $Python -m coverage run -m unittest discover -s tests -p "test_*.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Automated tests failed with exit code $LASTEXITCODE."
    }

    & $Python -m coverage report --fail-under=$MinimumCoverage
    if ($LASTEXITCODE -ne 0) {
        throw "Test coverage is below $MinimumCoverage%."
    }

    Write-Host "Quality checks passed." -ForegroundColor Green
}
finally {
    Pop-Location
}
