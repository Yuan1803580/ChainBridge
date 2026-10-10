[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$PythonPath
)

$ErrorActionPreference = "Stop"
$requirements = @(
    (Join-Path $ProjectRoot "apps\api\requirements.txt"),
    (Join-Path $ProjectRoot "services\agent-runner\requirements.txt"),
    (Join-Path $ProjectRoot "services\agent-signer\requirements.txt")
)
foreach ($path in $requirements) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Python requirements file is missing: $path" }
}

$material = ($requirements | ForEach-Object {
    "$(Split-Path $_ -Leaf):$((Get-FileHash -Algorithm SHA256 -LiteralPath $_).Hash)"
}) -join "`n"
$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $requirementsHash = -join ($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($material)) | ForEach-Object { $_.ToString("x2") })
}
finally { $sha256.Dispose() }

$environmentRoot = Split-Path (Split-Path $PythonPath -Parent) -Parent
$marker = Join-Path $environmentRoot ".agent-crosschain-requirements.sha256"
$installedHash = if (Test-Path -LiteralPath $marker) { (Get-Content -Raw -LiteralPath $marker).Trim() } else { "" }
& $PythonPath -c "import alembic, fastapi, sqlalchemy, eth_account, httpx, cryptography" *> $null
$importsReady = $LASTEXITCODE -eq 0
if ($importsReady -and $installedHash -eq $requirementsHash) { return }
if ($importsReady -and -not $installedHash) {
    Set-Content -LiteralPath $marker -Value $requirementsHash -Encoding ASCII
    return
}

Write-Host "Synchronizing API, Runner, and Sidecar Python dependencies..."
foreach ($path in $requirements) {
    & $PythonPath -m pip install --disable-pip-version-check -r $path
    if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed for $path" }
}
Set-Content -LiteralPath $marker -Value $requirementsHash -Encoding ASCII
