[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = if ($env:MANAGED_RUNTIME_DIR) {
    [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($env:MANAGED_RUNTIME_DIR)) { $env:MANAGED_RUNTIME_DIR } else { Join-Path $projectRoot $env:MANAGED_RUNTIME_DIR }))
} else { Join-Path $projectRoot ".runtime\managed-testnet" }
$stateFile = Join-Path $runtimeDir "services.json"
if (-not (Test-Path -LiteralPath $stateFile)) {
    Write-Host "Managed testnet services are not recorded as running."
    return
}
$state = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
$services = @($state.services)
[array]::Reverse($services)
foreach ($entry in $services) {
    $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
    if (-not $process) { continue }
    $recorded = [DateTime]::Parse([string]$entry.startTimeUtc).ToUniversalTime()
    if ($process.StartTime.ToUniversalTime().Ticks -ne $recorded.Ticks) {
        throw "Refusing to stop $($entry.name) PID $($entry.pid): process start time no longer matches the managed state."
    }
    & taskkill.exe /PID ([int]$entry.pid) /T /F | Out-Null
}
Remove-Item -LiteralPath $stateFile
Write-Host "Managed testnet services stopped. Runtime databases and logs were retained in $runtimeDir."
