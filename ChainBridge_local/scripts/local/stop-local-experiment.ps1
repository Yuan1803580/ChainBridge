[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "service-ownership.ps1")
$runtimeValue = if ($env:LOCAL_RUNTIME_DIR) { $env:LOCAL_RUNTIME_DIR } else { ".runtime\local-experiment" }
$runtimeDir = if ([System.IO.Path]::IsPathRooted($runtimeValue)) {
    [System.IO.Path]::GetFullPath($runtimeValue)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $runtimeValue))
}
$stateFile = Join-Path $runtimeDir "experiment.json"
$env:LOCAL_RUNTIME_DIR = $runtimeDir

if (Test-Path -LiteralPath $stateFile) {
    $state = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json

    function Stop-OwnedExperimentHttpService {
        param(
            [string]$Name,
            [object]$ProcessId,
            [object]$StartTimeUtc,
            [int]$Port,
            [string]$IdentityPath,
            [string]$ExpectedService,
            [string]$ExpectedRunnerId = ""
        )
        $listenerPids = @(Get-ListeningProcessIds -Port $Port)
        if ($listenerPids.Count -eq 0) {
            Stop-RecordedProcessTree -Name $Name -ProcessId $ProcessId -StartTimeUtc $StartTimeUtc | Out-Null
            return
        }

        $identity = Get-HttpServiceIdentity -Url "http://127.0.0.1:$Port$IdentityPath"
        if (
            $null -eq $identity -or
            $identity.service -ne $ExpectedService -or
            $identity.runtimeId -ne $state.runtimeId -or
            ($ExpectedRunnerId -and $identity.runnerId -ne $ExpectedRunnerId)
        ) {
            if ($listenerPids.Count -gt 0) {
                throw "Refusing to stop $Name on port $Port because its runtime identity does not match this experiment."
            }
            return
        }

        foreach ($listenerPid in $listenerPids) {
            $process = Get-Process -Id ([int]$listenerPid) -ErrorAction SilentlyContinue
            if ($null -ne $process) {
                Stop-RecordedProcessTree -Name $Name -ProcessId $process.Id -StartTimeUtc (Get-ProcessStartTimeUtc -Process $process) | Out-Null
            }
        }
        Wait-ForPortRelease -Port $Port
    }

    foreach ($runner in @($state.extraRunners)) {
        Stop-OwnedExperimentHttpService `
            -Name ([string]$runner.name) -ProcessId $runner.pid -StartTimeUtc $runner.startTimeUtc `
            -Port ([int]$runner.port) -IdentityPath "/health" `
            -ExpectedService "agent-crosschain-platform-agent-runner" -ExpectedRunnerId ([string]$runner.name)
    }
    if ($state.requesterPid -and $state.requesterPort) {
        Stop-OwnedExperimentHttpService `
            -Name "Local Requester" -ProcessId $state.requesterPid -StartTimeUtc $state.requesterStartTimeUtc `
            -Port ([int]$state.requesterPort) -IdentityPath "/health" `
            -ExpectedService "local-requester-wallet-service"
    }
    Stop-OwnedExperimentHttpService `
        -Name "Coordinator Agent" -ProcessId $state.coordinatorPid -StartTimeUtc $state.coordinatorStartTimeUtc `
        -Port ([int]$state.coordinatorPort) -IdentityPath "/health" `
        -ExpectedService "agent-crosschain-platform-coordinator-agent"
    Remove-Item -LiteralPath $stateFile
}

& (Join-Path $PSScriptRoot "stop-platform-v1.ps1")
