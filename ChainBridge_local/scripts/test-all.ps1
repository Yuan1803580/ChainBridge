$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = @(
    (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
    (Join-Path $RepoRoot ".venv\bin\python.exe")
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $PythonPath) { $PythonPath = "python" }
$Pnpm = Get-Command pnpm.cmd -ErrorAction Stop
$TestTemp = Join-Path $RepoRoot ".runtime\tests-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmssfff'))"

New-Item -ItemType Directory -Force -Path $TestTemp | Out-Null
$env:TEMP = $TestTemp
$env:TMP = $TestTemp

function Assert-LastCommand {
    param([Parameter(Mandatory = $true)][string]$Name)
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

Push-Location (Join-Path $RepoRoot "apps\api")
try {
    & $PythonPath -m pytest -q -p no:cacheprovider --basetemp (Join-Path $TestTemp "api")
    Assert-LastCommand "API tests"
} finally { Pop-Location }

Push-Location (Join-Path $RepoRoot "services\agent-runner")
try {
    & $PythonPath -m pytest -q -p no:cacheprovider --basetemp (Join-Path $TestTemp "agent-runner")
    Assert-LastCommand "Agent Runtime tests"
} finally { Pop-Location }

Push-Location (Join-Path $RepoRoot "packages\sdk\python")
try {
    & $PythonPath -m pytest -q -p no:cacheprovider --basetemp (Join-Path $TestTemp "sdk")
    Assert-LastCommand "SDK tests"
} finally { Pop-Location }

& $Pnpm.Source --dir (Join-Path $RepoRoot "apps\web") typecheck
Assert-LastCommand "Web typecheck"
& $Pnpm.Source --dir (Join-Path $RepoRoot "apps\web") build
Assert-LastCommand "Web build"
