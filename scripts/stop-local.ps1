$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimePath = Join-Path $RepoRoot ".runtime"

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)

    $Children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $RootProcessId" -ErrorAction SilentlyContinue
    foreach ($Child in $Children) {
        Stop-ProcessTree -RootProcessId $Child.ProcessId
    }

    $Process = Get-Process -Id $RootProcessId -ErrorAction SilentlyContinue
    if ($Process) {
        Stop-Process -Id $RootProcessId -Force -ErrorAction SilentlyContinue
    }
}

foreach ($Name in @("api", "agent-runner", "web")) {
    $PidPath = Join-Path $RuntimePath "$Name.pid"
    if (-not (Test-Path -LiteralPath $PidPath)) {
        continue
    }

    $ProcessId = [int](Get-Content -LiteralPath $PidPath)
    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        Stop-ProcessTree -RootProcessId $ProcessId
        Write-Host "Stopped $Name (PID $ProcessId)."
    }
}
