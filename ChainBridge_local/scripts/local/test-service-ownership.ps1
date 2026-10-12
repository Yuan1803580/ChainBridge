[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "service-ownership.ps1")

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$current = Get-Process -Id $PID
$startTime = Get-ProcessStartTimeUtc -Process $current
Assert-True (Test-RecordedProcess -ProcessId $PID -StartTimeUtc $startTime) "Current process identity should match."
Assert-True (-not (Test-RecordedProcess -ProcessId $PID -StartTimeUtc "2000-01-01T00:00:00.0000000Z")) "A stale start time must not match."

$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
try {
    $listener.Start()
    $port = ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listenerPids = @(Get-ListeningProcessIds -Port $port)
    Assert-True ($listenerPids -contains $PID) "Listener ownership should include the current process PID."
}
finally {
    $listener.Stop()
}

Write-Host "SERVICE_OWNERSHIP_TESTS_PASSED"
