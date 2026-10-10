[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = if ($env:MANAGED_RUNTIME_DIR) {
    [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($env:MANAGED_RUNTIME_DIR)) { $env:MANAGED_RUNTIME_DIR } else { Join-Path $projectRoot $env:MANAGED_RUNTIME_DIR }))
} else { Join-Path $projectRoot ".runtime\managed-testnet" }
$stateFile = Join-Path $runtimeDir "services.json"
$checks = @(
    @{ Name = "Platform API"; Url = "http://127.0.0.1:8000/api/v1/health" },
    @{ Name = "Agent Runner"; Url = "http://127.0.0.1:8010/health" },
    @{ Name = "Signer Sidecar"; Url = "http://127.0.0.1:8020/health" },
    @{ Name = "Platform Web"; Url = "http://127.0.0.1:3000/api/runtime-health" }
)

$results = foreach ($check in $checks) {
    try {
        $response = Invoke-RestMethod -UseBasicParsing -Uri $check.Url -TimeoutSec 3
        [PSCustomObject]@{ Service = $check.Name; Status = if ($response.status -eq "ok") { "OK" } else { "DEGRADED" }; Detail = $check.Url }
    }
    catch {
        [PSCustomObject]@{ Service = $check.Name; Status = "DOWN"; Detail = $check.Url }
    }
}

$workerStatus = "DOWN"
if (Test-Path -LiteralPath $stateFile -PathType Leaf) {
    $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $worker = @($state.services) | Where-Object { $_.name -eq "worker" } | Select-Object -First 1
    if ($worker -and (Get-Process -Id ([int]$worker.pid) -ErrorAction SilentlyContinue)) { $workerStatus = "OK" }
}
$results += [PSCustomObject]@{ Service = "Protocol Worker"; Status = $workerStatus; Detail = $stateFile }
$results | Format-Table -AutoSize
if ($results.Status -contains "DOWN" -or $results.Status -contains "DEGRADED") { exit 1 }
