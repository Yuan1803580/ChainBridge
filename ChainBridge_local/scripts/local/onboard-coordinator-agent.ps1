[CmdletBinding()]
param(
    [int]$ApiPort = 8200,
    [int]$CoordinatorPort = 8300,
    [string]$RuntimeToken = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = if ($env:LOCAL_RUNTIME_DIR) {
    if ([IO.Path]::IsPathRooted($env:LOCAL_RUNTIME_DIR)) {
        [IO.Path]::GetFullPath($env:LOCAL_RUNTIME_DIR)
    } else {
        [IO.Path]::GetFullPath((Join-Path $projectRoot $env:LOCAL_RUNTIME_DIR))
    }
} else {
    Join-Path $projectRoot ".runtime\local-experiment"
}
$apiUrl = "http://127.0.0.1:$ApiPort"
$coordinatorUrl = "http://127.0.0.1:$CoordinatorPort"
$RuntimeToken = if ($RuntimeToken) { $RuntimeToken } elseif ($env:COORDINATOR_API_TOKEN) { $env:COORDINATOR_API_TOKEN } else { "local-experiment-coordinator-token" }
$runnerHeaders = @{ Authorization = "Bearer $RuntimeToken" }
$statePath = Join-Path $runtimeDir "coordinator-onboarding.json"

function Invoke-JsonPost([string]$Uri, [object]$Body, [hashtable]$Headers = @{}) {
    return Invoke-RestMethod -Method Post -Uri $Uri -Headers $Headers `
        -ContentType "application/json; charset=utf-8" `
        -Body ($Body | ConvertTo-Json -Compress -Depth 20) -TimeoutSec 240
}

$manifest = Invoke-RestMethod -Uri "$coordinatorUrl/manifest" -Headers $runnerHeaders -TimeoutSec 10
if ([string]$manifest.agentType -ne "COORDINATOR") {
    throw "The selected runtime does not declare agentType COORDINATOR."
}

$directory = Invoke-RestMethod -Uri "$apiUrl/api/v1/workflow-agents?agentType=COORDINATOR" -TimeoutSec 10
$profile = @($directory.items | Where-Object { $_.agentKey -eq [string]$manifest.agentId }) | Select-Object -First 1

if (-not $profile) {
    $llmProvider = if ([string]$manifest.llm.provider) { [string]$manifest.llm.provider } else { "mock" }
    $llmModel = if ([string]$manifest.llm.model) { [string]$manifest.llm.model } else { "mock-deterministic-dag-v1" }
    $registration = Invoke-JsonPost "$apiUrl/api/v1/agent-onboarding/registrations" @{
        agentType = "COORDINATOR"
        ownerName = "Independent Coordinator Operator"
        ownerEmail = "coordinator.operator@example.com"
        organization = "Local Cross-chain Experiment"
        agentName = [string]$manifest.name
        agentKey = [string]$manifest.agentId
        did = [string]$manifest.did
        endpoint = $coordinatorUrl
        publicKey = [string]$manifest.verificationMethod.publicKeyHex
        llmProvider = $llmProvider
        llmModel = $llmModel
        systemPrompt = "Clarify user intent and propose a DAG; Coordinator Core remains authoritative."
        capabilities = @($manifest.capabilities | ForEach-Object { [string]$_.capabilityId })
        priceFloor = "0"
        currency = "USDC"
    }
    $profile = $registration.profile
}

if ([string]$profile.agentType -ne "COORDINATOR") {
    throw "The existing profile is not a Coordinator Agent."
}
if ([string]$profile.agent.did -and [string]$profile.agent.did -ne [string]$manifest.did) {
    throw "The existing Coordinator profile is bound to a different DID."
}

if ([string]$profile.credentialStatus -eq "UNCONFIGURED") {
    $challenge = Invoke-JsonPost "$apiUrl/api/v1/agent-onboarding/registrations/$($profile.id)/challenge" @{}
    $identitySignature = Invoke-JsonPost "$coordinatorUrl/identity-signatures" `
        @{ message = [string]$challenge.message } $runnerHeaders
    $verification = Invoke-JsonPost "$apiUrl/api/v1/agent-onboarding/registrations/$($profile.id)/verify" @{
        challengeId = [string]$challenge.challengeId
        identitySignature = [string]$identitySignature.signature
    }
    $profile = $verification.profile
}

$adminLogin = Invoke-JsonPost "$apiUrl/api/v1/auth/dev-login" @{ did = "did:local:platform-admin" }
$adminHeaders = @{ Authorization = "Bearer $($adminLogin.accessToken)" }

if ([string]$profile.admissionStatus -ne "APPROVED") {
    $null = Invoke-JsonPost "$apiUrl/api/v1/agent-onboarding/registrations/$($profile.id)/checks" `
        @{ runtimeAccessToken = $RuntimeToken } $adminHeaders
    $activation = Invoke-JsonPost "$apiUrl/api/v1/agent-onboarding/registrations/$($profile.id)/activate" `
        @{ notes = "Coordinator DID, manifest, health and real DAG proposal passed." } $adminHeaders
    $profile = $activation.profile
    $admissionStatus = [string]$activation.admissionStatus
} else {
    $admissionStatus = "APPROVED"
}

$heartbeat = Invoke-JsonPost "$coordinatorUrl/platform-registration" @{
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
    agentKey = [string]$manifest.agentId
    did = [string]$manifest.did
    agentType = "COORDINATOR"
    admissionStatus = $admissionStatus
    heartbeatAccepted = [bool]$heartbeat.heartbeat.accepted
    updatedAt = [DateTime]::UtcNow.ToString("o")
}
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Host "Coordinator Agent platform onboarding completed:"
Write-Host "  Profile: $($state.profileId)"
Write-Host "  DID: $($state.did)"
Write-Host "  Admission: $($state.admissionStatus)"
Write-Host "  Heartbeat: $($state.heartbeatAccepted)"
