function Get-StateValue {
    param(
        [object]$State,
        [string]$Name
    )

    if ($null -eq $State) { return $null }
    $property = $State.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-ProcessStartTimeUtc {
    param([System.Diagnostics.Process]$Process)

    return $Process.StartTime.ToUniversalTime().ToString("o")
}

function Test-RecordedProcess {
    param(
        [object]$ProcessId,
        [object]$StartTimeUtc
    )

    if ($null -eq $ProcessId -or [string]::IsNullOrWhiteSpace([string]$StartTimeUtc)) {
        return $false
    }
    $process = Get-Process -Id ([int]$ProcessId) -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }

    try {
        $expected = [DateTime]::Parse(
            [string]$StartTimeUtc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).ToUniversalTime()
        return $process.StartTime.ToUniversalTime().Ticks -eq $expected.Ticks
    }
    catch {
        return $false
    }
}

function Get-ListeningProcessIds {
    param([int]$Port)

    $ids = [Collections.Generic.HashSet[int]]::new()
    try {
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | ForEach-Object {
            $null = $ids.Add([int]$_.OwningProcess)
        }
    }
    catch {
        # netstat remains available when Get-NetTCPConnection is restricted.
    }

    if ($ids.Count -eq 0) {
        $pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
        & netstat.exe -ano -p TCP | ForEach-Object {
            if ($_ -match $pattern) {
                $null = $ids.Add([int]$Matches[1])
            }
        }
    }

    return @($ids)
}

function Wait-ForPortRelease {
    param(
        [int]$Port,
        [int]$TimeoutMilliseconds = 5000
    )

    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
    do {
        if (@(Get-ListeningProcessIds -Port $Port).Count -eq 0) { return }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "Port $Port did not become free after stopping the owned service."
}

function Stop-RecordedProcessTree {
    param(
        [string]$Name,
        [object]$ProcessId,
        [object]$StartTimeUtc
    )

    if ($null -eq $ProcessId) { return $false }
    $process = Get-Process -Id ([int]$ProcessId) -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }
    if (-not (Test-RecordedProcess -ProcessId $ProcessId -StartTimeUtc $StartTimeUtc)) {
        throw "Refusing to stop $Name PID $ProcessId because its start time does not match the recorded process."
    }

    & taskkill.exe /PID ([int]$ProcessId) /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not stop $Name process tree $ProcessId" }
    return $true
}

function Get-HttpServiceIdentity {
    param([string]$Url)

    try {
        return Invoke-RestMethod -UseBasicParsing -Uri $Url -TimeoutSec 2
    }
    catch {
        return $null
    }
}

function Assert-OwnedHttpService {
    param(
        [string]$Name,
        [int]$Port,
        [string]$IdentityUrl,
        [string]$ExpectedService,
        [string]$ExpectedRuntimeId,
        [object]$RecordedPid,
        [object]$RecordedStartTimeUtc,
        [object]$RecordedPort,
        [bool]$StateMatchesRuntime
    )

    $listenerPids = @(Get-ListeningProcessIds -Port $Port)
    if ($listenerPids.Count -eq 0) { return $null }

    $owners = ($listenerPids | Sort-Object -Unique) -join ", "
    if (-not $StateMatchesRuntime -or $null -eq $RecordedPid -or $null -eq $RecordedPort) {
        throw "$Name port $Port is already used by PID(s) $owners but is not owned by this runtime. Stop it or choose another port."
    }
    if ([int]$RecordedPort -ne $Port) {
        throw "$Name port $Port is already used by PID(s) $owners, while this runtime recorded port $RecordedPort. Stop it or choose another port."
    }
    if (-not (Test-RecordedProcess -ProcessId $RecordedPid -StartTimeUtc $RecordedStartTimeUtc)) {
        throw "$Name port $Port is already used by PID(s) $owners, but the recorded PID $RecordedPid no longer identifies the same process. Stop it or choose another port."
    }
    if ($listenerPids -notcontains [int]$RecordedPid) {
        throw "$Name port $Port is already used by PID(s) $owners, not the recorded PID $RecordedPid. Stop it or choose another port."
    }

    $identity = Get-HttpServiceIdentity -Url $IdentityUrl
    if ($null -eq $identity -or $identity.service -ne $ExpectedService -or $identity.runtimeId -ne $ExpectedRuntimeId) {
        throw "$Name on port $Port does not return the expected service identity for runtime $ExpectedRuntimeId. Stop it or choose another port."
    }

    return (Get-Process -Id ([int]$RecordedPid))
}
