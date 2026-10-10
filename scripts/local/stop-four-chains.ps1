[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = if ($env:LOCAL_RUNTIME_DIR) {
    $runtimeCandidate = if ([System.IO.Path]::IsPathRooted($env:LOCAL_RUNTIME_DIR)) { $env:LOCAL_RUNTIME_DIR } else { Join-Path $projectRoot $env:LOCAL_RUNTIME_DIR }
    [System.IO.Path]::GetFullPath($runtimeCandidate)
} else {
    Join-Path $projectRoot ".runtime\local-v1"
}
$stateFile = Join-Path $runtimeDir "chains.json"

function Stop-OwnedProcessTree([int]$ProcessId) {
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return }
    & taskkill.exe /PID $ProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not stop process tree $ProcessId" }
}
if (-not (Test-Path -LiteralPath $stateFile)) {
    Write-Host "No locally started chain process state was found."
    exit 0
}

$records = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
foreach ($record in $records) {
    if ($null -eq $record.pid) {
        Write-Host "Leaving reused Chain $($record.name) process running on port $($record.port)."
        continue
    }
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($null -ne $process) {
        Stop-OwnedProcessTree $record.pid
        Write-Host "Stopped Chain $($record.name) process $($record.pid)."
    }
}
Remove-Item -LiteralPath $stateFile
