$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimePath = Join-Path $RepoRoot ".runtime"
$PythonPath = @(
    (Join-Path $RepoRoot ".venv\Scripts\python.exe"),
    (Join-Path $RepoRoot ".venv\bin\python.exe")
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $PythonPath) { $PythonPath = "python" }
$Pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue

if (-not $Pnpm) {
    throw "pnpm.cmd was not found. Run scripts\setup-local.ps1 first."
}

New-Item -ItemType Directory -Force -Path $RuntimePath | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "apps\api\data") | Out-Null

$env:APP_ENV = "development"
$env:DEV_IDENTITY_SEED = "agent-market-demo"
$env:DEV_DID_PREFIX = "did:local"
$env:CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:8000"
$env:NEXT_PUBLIC_ENABLE_MOCK_FALLBACK = "false"

$Api = Start-Process -FilePath $PythonPath `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -WorkingDirectory (Join-Path $RepoRoot "apps\api") `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $RuntimePath "api.out.log") `
    -RedirectStandardError (Join-Path $RuntimePath "api.err.log")

$AgentRunner = Start-Process -FilePath $PythonPath `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8010") `
    -WorkingDirectory (Join-Path $RepoRoot "services\agent-runner") `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $RuntimePath "agent-runner.out.log") `
    -RedirectStandardError (Join-Path $RuntimePath "agent-runner.err.log")

$Web = Start-Process -FilePath $Pnpm.Source `
    -ArgumentList @("--dir", (Join-Path $RepoRoot "apps\web"), "dev") `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $RuntimePath "web.out.log") `
    -RedirectStandardError (Join-Path $RuntimePath "web.err.log")

$Api.Id | Set-Content -Encoding ascii (Join-Path $RuntimePath "api.pid")
$AgentRunner.Id | Set-Content -Encoding ascii (Join-Path $RuntimePath "agent-runner.pid")
$Web.Id | Set-Content -Encoding ascii (Join-Path $RuntimePath "web.pid")

Write-Host "API:          http://127.0.0.1:8000/api/docs"
Write-Host "Agent runner: http://127.0.0.1:8010/docs"
Write-Host "Web:          http://localhost:3000"
Write-Host "Logs:         $RuntimePath"
