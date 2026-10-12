[CmdletBinding()]
param(
    [int]$ApiPort = 8200,
    [int]$RunnerPort = 8122,
    [string]$RuntimeToken = "",
    [string]$ExternalAgentRoot = "D:\kimi-external-agent"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = if ($env:LOCAL_RUNTIME_DIR) {
    if ([System.IO.Path]::IsPathRooted($env:LOCAL_RUNTIME_DIR)) {
        [System.IO.Path]::GetFullPath($env:LOCAL_RUNTIME_DIR)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $env:LOCAL_RUNTIME_DIR))
    }
} else {
    Join-Path $projectRoot ".runtime\local-experiment"
}
$apiUrl = "http://127.0.0.1:$ApiPort"
$runnerUrl = "http://127.0.0.1:$RunnerPort"
$slug = "workflow-research-kimi-external-v2"
$agentUrl = "$runnerUrl/agents/$slug"
$secretPath = Join-Path $ExternalAgentRoot "data\agent-secrets.json"
if (-not $RuntimeToken) {
    if (-not (Test-Path -LiteralPath $secretPath)) {
        throw "Independent Agent secrets are missing at $secretPath. Start D:\kimi-external-agent first."
    }
    $RuntimeToken = [string](Get-Content -Raw -LiteralPath $secretPath | ConvertFrom-Json).runtimeToken
}
$runnerHeaders = @{ Authorization = "Bearer $RuntimeToken" }
$statePath = Join-Path $runtimeDir "runner-kimi-onboarding.json"

function Invoke-JsonPost([string]$Uri, [object]$Body, [hashtable]$Headers = @{}) {
    return Invoke-RestMethod -Method Post -Uri $Uri -Headers $Headers `
        -ContentType "application/json; charset=utf-8" `
        -Body ($Body | ConvertTo-Json -Compress -Depth 12) -TimeoutSec 180
}

$manifest = Invoke-RestMethod -Uri "$agentUrl/manifest" -TimeoutSec 10
$wallet = Invoke-RestMethod -Uri "$agentUrl/wallet" -Headers $runnerHeaders -TimeoutSec 10
if ([int]$wallet.chainId -ne 31338) { throw "Kimi wallet must be bound to local chain B (31338)." }

$directory = Invoke-RestMethod -Uri "$apiUrl/api/v1/workflow-agents" -TimeoutSec 10
$profile = @($directory.items | Where-Object { $_.agentKey -eq $slug }) | Select-Object -First 1

if (-not $profile) {
    $registration = Invoke-JsonPost "$apiUrl/api/v1/agent-onboarding/registrations" @{
        ownerName = "Independent Kimi Operator"
        ownerEmail = "kimi.operator@example.com"
        organization = "Local Cross-chain Experiment"
        agentName = "Independent Kimi Research Agent"
        agentKey = $slug
        did = [string]$manifest.did
        endpoint = $agentUrl
        publicKey = [string]$manifest.verificationMethod.publicKeyHex
        llmProvider = "kimi"
        llmModel = if ($env:KIMI_MODEL) { [string]$env:KIMI_MODEL } else { "kimi-k2.6" }
        systemPrompt = "Complete auditable workflow research tasks using controlled project context."
        capabilities = @($manifest.capabilities | ForEach-Object { [string]$_.capabilityId })
        preferredChainId = 31338
        payoutAsset = "BTK"
        settlementAsset = "USDC"
        homeWallet = [string]$wallet.address
        priceFloor = "4.20"
        currency = "USDC"
    }
    $profile = $registration.profile
}

if ([string]$profile.homeWallet -ine [string]$wallet.address) {
    throw "The platform profile is bound to a different Kimi wallet."
}
if ([string]$profile.agent.did -and [string]$profile.agent.did -ne [string]$manifest.did) {
    throw "The platform profile is bound to a different Kimi DID."
}

if ([string]$profile.credentialStatus -eq "UNCONFIGURED") {
    $challenge = Invoke-JsonPost "$apiUrl/api/v1/agent-onboarding/registrations/$($profile.id)/challenge" @{}
    $identitySignature = Invoke-JsonPost "$agentUrl/identity-signatures" `
        @{ message = [string]$challenge.message } $runnerHeaders
    $walletSignature = Invoke-JsonPost "$agentUrl/wallet-signatures" `
        @{ message = [string]$challenge.message } $runnerHeaders
    $verification = Invoke-JsonPost "$apiUrl/api/v1/agent-onboarding/registrations/$($profile.id)/verify" @{
        challengeId = [string]$challenge.challengeId
        identitySignature = [string]$identitySignature.signature
        walletSignature = [string]$walletSignature.signature
    }
    $profile = $verification.profile
}

$adminLogin = Invoke-JsonPost "$apiUrl/api/v1/auth/dev-login" @{ did = "did:local:platform-admin" }
$adminHeaders = @{ Authorization = "Bearer $($adminLogin.accessToken)" }

if ([string]$profile.admissionStatus -ne "APPROVED") {
    $null = Invoke-JsonPost "$apiUrl/api/v1/agent-onboarding/registrations/$($profile.id)/checks" `
        @{ runtimeAccessToken = $RuntimeToken } $adminHeaders
    $activation = Invoke-JsonPost "$apiUrl/api/v1/agent-onboarding/registrations/$($profile.id)/activate" `
        @{ notes = "Local Kimi identity, wallet ownership, manifest, health and real invocation verified. Gas funding deferred." } $adminHeaders
    $profile = $activation.profile
    $admissionStatus = [string]$activation.admissionStatus
} else {
    $admissionStatus = "APPROVED"
}

$heartbeat = Invoke-JsonPost "$agentUrl/platform-registration" @{
    platformUrl = $apiUrl
    profileId = [string]$profile.id
    status = "ONLINE"
    capacityAvailable = $true
    endpointLatencyMs = 100
    heartbeatIntervalSeconds = 30
} $runnerHeaders

$state = [ordered]@{
    schemaVersion = 1
    profileId = [string]$profile.id
    agentKey = $slug
    did = [string]$manifest.did
    homeWallet = [string]$wallet.address
    chainId = [int]$wallet.chainId
    admissionStatus = $admissionStatus
    chainStatus = [string]$profile.chainStatus
    gasReady = [bool]$profile.gasReady
    heartbeatAccepted = [bool]$heartbeat.heartbeat.accepted
    updatedAt = [DateTime]::UtcNow.ToString("o")
}
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Host "Kimi Agent platform onboarding completed:"
Write-Host "  Profile: $($state.profileId)"
Write-Host "  EVM wallet: $($state.homeWallet)"
Write-Host "  Home chain: $($state.chainId)"
Write-Host "  Admission: $($state.admissionStatus)"
Write-Host "  Gas ready: $($state.gasReady)"
Write-Host "  Heartbeat: $($state.heartbeatAccepted)"
