[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pythonCandidates = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot "apps\api\.venv\Scripts\python.exe")
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) { throw "Python environment is missing." }

Push-Location (Join-Path $projectRoot "services\agent-runner")
try {
    & $python -m pytest tests\test_mock_agent_runner_isolation.py tests\test_api.py `
        -q -p no:cacheprovider -k "runner_can_isolate or runner_hosts_only_selected"
    if ($LASTEXITCODE -ne 0) { throw "Mock Agent Runner isolation tests failed." }
}
finally {
    Pop-Location
}

