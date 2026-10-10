[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$apiDir = Join-Path $projectRoot "apps\api"
$webDir = Join-Path $projectRoot "apps\web"
$pythonCandidates = @(
    (Join-Path $apiDir ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv\Scripts\python.exe")
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) { throw "Python environment is missing. Run scripts\setup-local.ps1 first." }

function Invoke-Checked([scriptblock]$Command, [string]$Label) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Label failed" }
}

Push-Location $apiDir
try {
    $pytestTemp = Join-Path $env:TEMP "agent-crosschain-api-pytest-$PID"
    Invoke-Checked { & $python -m pytest -q --basetemp $pytestTemp } "FastAPI tests"
}
finally { Pop-Location }

Push-Location $projectRoot
try {
    Invoke-Checked { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\local\test-service-ownership.ps1 } "Service ownership tests"
    Invoke-Checked { & (Join-Path $PSScriptRoot "invoke-hardhat-isolated.ps1") -Command test } "Contract tests"
    Invoke-Checked { node --test services\chain-worker\test\*.test.ts } "Worker tests"
    Invoke-Checked { & (Join-Path $projectRoot "node_modules\.bin\tsc.cmd") -p services\chain-worker\tsconfig.json } "Worker typecheck"
}
finally { Pop-Location }

Push-Location $webDir
try {
    Invoke-Checked { & (Join-Path $webDir "node_modules\.bin\tsc.cmd") --noEmit } "Web typecheck"
    $previousDistDir = $env:NEXT_DIST_DIR
    $env:NEXT_DIST_DIR = ".next-verify"
    try { Invoke-Checked { & (Join-Path $webDir "node_modules\.bin\next.cmd") build } "Web production build" }
    finally { $env:NEXT_DIST_DIR = $previousDistDir }
    Invoke-Checked { & (Join-Path $PSScriptRoot "start-platform-v1.ps1") } "Local platform startup"
    Invoke-Checked { & (Join-Path $webDir "node_modules\.bin\playwright.cmd") test } "Browser tests"
}
finally { Pop-Location }

Write-Host "LOCAL_V1_VERIFIED"
