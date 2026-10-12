[CmdletBinding()]
param(
    [switch]$Quiet,
    [switch]$ReuseExisting,
    [ValidateSet("A", "B", "C", "D")]
    [string[]]$Chains = @("A", "B", "C", "D")
)

$ErrorActionPreference = "Stop"
# Windows environment variables are case-insensitive while Start-Process builds a
# case-sensitive dictionary, so any duplicated key (PATH/Path, HTTPS_PROXY/https_proxy,
# ...) makes Start-Process fail before a chain is ever started.
$processPath = $env:PATH
Remove-Item Env:Path -ErrorAction SilentlyContinue
$env:Path = $processPath
foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")) {
    $upper = [Environment]::GetEnvironmentVariable($name, "Process")
    $lower = [Environment]::GetEnvironmentVariable($name.ToLowerInvariant(), "Process")
    if ($upper -and $lower) {
        Remove-Item "Env:$($name.ToLowerInvariant())" -ErrorAction SilentlyContinue
    } elseif (-not $upper -and $lower) {
        [Environment]::SetEnvironmentVariable($name, $lower, "Process")
        Remove-Item "Env:$($name.ToLowerInvariant())" -ErrorAction SilentlyContinue
    }
}
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = if ($env:LOCAL_RUNTIME_DIR) {
    $runtimeCandidate = if ([System.IO.Path]::IsPathRooted($env:LOCAL_RUNTIME_DIR)) { $env:LOCAL_RUNTIME_DIR } else { Join-Path $projectRoot $env:LOCAL_RUNTIME_DIR }
    [System.IO.Path]::GetFullPath($runtimeCandidate)
} else {
    Join-Path $projectRoot ".runtime\local-v1"
}
$hardhat = Join-Path $projectRoot "node_modules\.bin\hardhat.cmd"

if (-not (Test-Path -LiteralPath $hardhat)) {
    throw "Hardhat is not installed at $hardhat"
}
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

function Get-LocalDefinition([string]$Name, [int]$DefaultChainId, [int]$DefaultPort) {
    $chainIdValue = [Environment]::GetEnvironmentVariable("LOCAL_CHAIN_${Name}_ID")
    $rpcValue = [Environment]::GetEnvironmentVariable("LOCAL_CHAIN_${Name}_RPC_URL")
    $chainId = if ($chainIdValue) { [int]$chainIdValue } else { $DefaultChainId }
    $rpcUrl = if ($rpcValue) { $rpcValue } else { "http://127.0.0.1:$DefaultPort" }
    $uri = [uri]$rpcUrl
    if ($uri.Scheme -ne "http" -or $uri.Host -notin @("127.0.0.1", "localhost")) {
        throw "Local chain $Name RPC must be an http://127.0.0.1 or http://localhost URL"
    }
    return @{ Name = $Name; ChainId = $chainId; Port = $uri.Port; RpcUrl = $rpcUrl }
}

$definitions = @(
    (Get-LocalDefinition "A" 31337 8545),
    (Get-LocalDefinition "B" 31338 8546),
    (Get-LocalDefinition "C" 31339 8547),
    (Get-LocalDefinition "D" 31340 8548)
) | Where-Object { $_.Name -in $Chains }
$stateFile = Join-Path $runtimeDir "chains.json"
$previousRecords = if (Test-Path -LiteralPath $stateFile) {
    @(Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json)
} else { @() }

function Get-RpcChainId([int]$Port) {
    try {
        $body = @{ jsonrpc = "2.0"; id = 1; method = "eth_chainId"; params = @() } | ConvertTo-Json -Compress
        $result = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port" -ContentType "application/json" -Body $body -TimeoutSec 1
        return [Convert]::ToInt32($result.result, 16)
    }
    catch {
        return $null
    }
}

$records = @($previousRecords | Where-Object { $_.name -notin $Chains })
foreach ($definition in $definitions) {
    $existingChainId = Get-RpcChainId $definition.Port
    if ($null -ne $existingChainId) {
        if ($existingChainId -ne $definition.ChainId) {
            throw "Port $($definition.Port) is already serving chainId $existingChainId, expected $($definition.ChainId)"
        }
        $previous = $previousRecords | Where-Object { $_.name -eq $definition.Name } | Select-Object -First 1
        $ownedPid = if ($previous -and $previous.pid -and (Get-Process -Id $previous.pid -ErrorAction SilentlyContinue)) { $previous.pid } else { $null }
        if (-not $ownedPid -and -not $ReuseExisting) {
            throw "Port $($definition.Port) already serves chainId $existingChainId but is not owned by this project. Stop it, choose LOCAL_CHAIN_$($definition.Name)_RPC_URL, or pass -ReuseExisting explicitly."
        }
        $records += [pscustomobject]@{
            name = $definition.Name
            chainId = $definition.ChainId
            port = $definition.Port
            pid = $ownedPid
            reused = $null -eq $ownedPid
            rpcUrl = $definition.RpcUrl
        }
        continue
    }

    $stdout = Join-Path $runtimeDir "chain-$($definition.Name).stdout.log"
    $stderr = Join-Path $runtimeDir "chain-$($definition.Name).stderr.log"
    $process = Start-Process -FilePath $hardhat -ArgumentList @(
        "node",
        "--hostname", "127.0.0.1",
        "--port", "$($definition.Port)",
        "--chain-id", "$($definition.ChainId)"
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        if ((Get-RpcChainId $definition.Port) -eq $definition.ChainId) {
            $ready = $true
            break
        }
        if ($process.HasExited) {
            break
        }
    }
    if (-not $ready) {
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force
        }
        throw "Chain $($definition.Name) did not become ready. See $stderr"
    }
    $records += [pscustomobject]@{
        name = $definition.Name
        chainId = $definition.ChainId
        port = $definition.Port
        pid = $process.Id
        reused = $false
        rpcUrl = $definition.RpcUrl
    }
}

$records | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
if (-not $Quiet) {
    $records | Format-Table name, chainId, port, pid, reused
    Write-Host "Four local RPCs are ready. State: $stateFile"
}
