[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [Parameter(Mandatory = $true)]
    [string]$CertificateThumbprint,
    [string]$TimestampUrl = ""
)

$ErrorActionPreference = "Stop"

$resolvedFile = [System.IO.Path]::GetFullPath(
    (Resolve-Path -LiteralPath $FilePath).Path
)
$thumbprint = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
if ($thumbprint -notmatch '^[0-9A-F]{40,64}$') {
    throw "Некорректный thumbprint сертификата подписи."
}

$signToolCommand = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
$signTool = if ($null -ne $signToolCommand) {
    $signToolCommand.Source
}
else {
    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path -LiteralPath $kitsRoot -PathType Container) {
        Get-ChildItem -Path (Join-Path $kitsRoot "*\x64\signtool.exe") `
            -File -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
}
if ([string]::IsNullOrWhiteSpace($signTool)) {
    throw "signtool.exe не найден. Установите Windows SDK."
}

$arguments = @(
    "sign",
    "/sha1", $thumbprint,
    "/fd", "SHA256"
)
if (-not [string]::IsNullOrWhiteSpace($TimestampUrl)) {
    $arguments += @(
        "/tr", $TimestampUrl,
        "/td", "SHA256"
    )
}
$arguments += $resolvedFile

& $signTool @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось подписать файл: $resolvedFile"
}

& $signTool "verify" "/pa" "/v" $resolvedFile
if ($LASTEXITCODE -ne 0) {
    throw "Проверка цифровой подписи не пройдена: $resolvedFile"
}

Write-Host "Цифровая подпись подтверждена: $resolvedFile" -ForegroundColor Green
