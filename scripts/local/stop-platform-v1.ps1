[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "service-ownership.ps1")

$runtimeDir = if ($env:LOCAL_RUNTIME_DIR) {
    $runtimeCandidate = if ([System.IO.Path]::IsPathRooted($env:LOCAL_RUNTIME_DIR)) { $env:LOCAL_RUNTIME_DIR } else { Join-Path $projectRoot $env:LOCAL_RUNTIME_DIR }
    [System.IO.Path]::GetFullPath($runtimeCandidate)
} else {
    Join-Path $projectRoot ".runtime\local-v1"
}
$stateFile = Join-Path $runtimeDir "platform.json"

function Test-SamePath([object]$Left, [string]$Right) {
    if ([string]::IsNullOrWhiteSpace([string]$Left)) { return $false }
    try {
        $leftPath = [System.IO.Path]::GetFullPath([string]$Left).TrimEnd('\')
        $rightPath = [System.IO.Path]::GetFullPath($Right).TrimEnd('\')
        return $leftPath.Equals($rightPath, [StringComparison]::OrdinalIgnoreCase)
    }
    catch { return $false }
}

if (Test-Path -LiteralPath $stateFile) {
    $state = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
    $stateMatchesRuntime = (
        (Get-StateValue -State $state -Name "schemaVersion") -in @(2, 3) -and
        (Test-SamePath (Get-StateValue -State $state -Name "projectRoot") $projectRoot) -and
        (Test-SamePath (Get-StateValue -State $state -Name "runtimeDir") $runtimeDir)
    )
    if (-not $stateMatchesRuntime) {
        $legacyLivePids = @(
            (Get-StateValue -State $state -Name "apiPid"),
            (Get-StateValue -State $state -Name "webPid"),
            (Get-StateValue -State $state -Name "runnerPid"),
            (Get-StateValue -State $state -Name "protocolPid")
        ) | Where-Object { $null -ne $_ -and $null -ne (Get-Process -Id ([int]$_) -ErrorAction SilentlyContinue) }
        if ($legacyLivePids.Count -gt 0) {
            throw "Refusing to stop legacy or foreign platform state because PID(s) $($legacyLivePids -join ', ') are still running without verifiable start times. Stop them manually or choose another runtime directory."
        }
        Write-Host "Removed legacy platform state because none of its recorded processes are running."
        Remove-Item -LiteralPath $stateFile
    } else {
        foreach ($entry in @(
            @{ Name = "Agent Runner"; Pid = Get-StateValue -State $state -Name "runnerPid"; StartTimeUtc = Get-StateValue -State $state -Name "runnerStartTimeUtc" },
            @{ Name = "API"; Pid = Get-StateValue -State $state -Name "apiPid"; StartTimeUtc = Get-StateValue -State $state -Name "apiStartTimeUtc" },
            @{ Name = "Web"; Pid = Get-StateValue -State $state -Name "webPid"; StartTimeUtc = Get-StateValue -State $state -Name "webStartTimeUtc" },
            @{ Name = "Protocol Worker"; Pid = Get-StateValue -State $state -Name "protocolPid"; StartTimeUtc = Get-StateValue -State $state -Name "protocolStartTimeUtc" }
        )) {
            $stopped = Stop-RecordedProcessTree -Name $entry.Name -ProcessId $entry.Pid -StartTimeUtc $entry.StartTimeUtc
            if ($stopped) {
                Write-Host "Stopped $($entry.Name) process $($entry.Pid)."
            }
        }
        Remove-Item -LiteralPath $stateFile
    }
}

& (Join-Path $PSScriptRoot "stop-four-chains.ps1")
