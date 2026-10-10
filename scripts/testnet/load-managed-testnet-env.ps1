[CmdletBinding()]
param(
    [string]$SecretRoot = (Join-Path $env:LOCALAPPDATA "AgentCrosschain\secrets"),
    [string]$PublicWebOrigin = "",
    [string]$PublicApiOrigin = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

function Get-DpapiSecret([string]$FileName) {
    $path = Join-Path $SecretRoot $FileName
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Missing protected secret file: $path"
    }
    $encrypted = (Get-Content -LiteralPath $path -Raw).Trim()
    $secure = ConvertTo-SecureString $encrypted
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        if ([string]::IsNullOrWhiteSpace($plain)) { throw "Protected secret file is empty: $FileName" }
        return $plain
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Require-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label is missing: $Path" }
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python environment is missing. Run scripts\setup-local.ps1 first."
}

$adminKeyFile = Join-Path $SecretRoot "platform-admin-identity.pem"
$signerConfig = Join-Path $SecretRoot "signer-config.json"
$relayerKeyFile = Join-Path $SecretRoot "protocol-relayer.key"
$sidecarKeyDir = Join-Path $SecretRoot "agents"
Require-File $adminKeyFile "Platform administrator identity key"
Require-File $signerConfig "Signer configuration"
Require-File $relayerKeyFile "Protocol relayer key"
if (-not (Test-Path -LiteralPath $sidecarKeyDir -PathType Container)) {
    throw "Sidecar Agent key directory is missing: $sidecarKeyDir"
}

$adminPublicKey = (& $python (Join-Path $PSScriptRoot "get-es256k-public-key.py") $adminKeyFile).Trim()
if ($LASTEXITCODE -ne 0 -or $adminPublicKey -notmatch '^04[0-9a-fA-F]{128}$') {
    throw "Could not derive the administrator ES256K public key"
}

$listenerSecret = Get-DpapiSecret "protocol-listener.dpapi"
$secretValues = @{
    APP_SECRET = Get-DpapiSecret "app-secret.dpapi"
    AGENT_RUNNER_API_TOKEN = Get-DpapiSecret "runner-api-token.dpapi"
    RUNNER_CONTROL_HMAC_SECRET = Get-DpapiSecret "runner-control.dpapi"
    WORKER_INTENT_HMAC_SECRET = Get-DpapiSecret "worker-intent.dpapi"
    PROTOCOL_WORKER_API_TOKEN = Get-DpapiSecret "protocol-worker-api.dpapi"
    PROTOCOL_WORKER_LISTENER_SECRET = $listenerSecret
    SIDECAR_STORAGE_KEY = Get-DpapiSecret "storage-key.dpapi"
    SIDECAR_PROVISIONING_TOKEN = Get-DpapiSecret "provisioning-token.dpapi"
    DEEPSEEK_API_KEY = Get-DpapiSecret "deepseek-key.dpapi"
    SOURCE_RPC_URL = Get-DpapiSecret "source-rpc.dpapi"
    DESTINATION_RPC_URL = Get-DpapiSecret "destination-rpc.dpapi"
}
foreach ($entry in $secretValues.GetEnumerator()) {
    Set-Item -Path "Env:$($entry.Key)" -Value $entry.Value
}

$publicValues = @{
    PLATFORM_ADMIN_DID = "did:agent:platform-admin:testnet"
    PLATFORM_ADMIN_PUBLIC_KEY = $adminPublicKey
    PROTOCOL_WORKER_LISTENER_ID = "ccip-worker-1"
    LISTENER_HMAC_KEYS = "ccip-worker-1:$listenerSecret"
    AGENT_SIGNER_CONFIG_FILE = $signerConfig
    SIDECAR_KEY_DIR = $sidecarKeyDir
    PROTOCOL_RELAYER_PRIVATE_KEY_FILE = $relayerKeyFile
    MANAGED_SELF_SERVICE_ONBOARDING = "true"
    MANAGED_SELF_SERVICE_CAPABILITIES = "research"
    MANAGED_SELF_SERVICE_EXECUTOR_CREDENTIAL_REF = "env:DEEPSEEK_API_KEY"
    SOURCE_CCIP_ROUTER = "0x0BF3dE8c5D3e8A2B34D2BEeB17ABfCeBaf363A59"
    DESTINATION_CCIP_ROUTER = "0x2a9C5afB0d0e4BAb2BCdaE109EC4b0c4Be15a165"
    SOURCE_CHAIN_SELECTOR = "16015286601757825753"
    DESTINATION_CHAIN_SELECTOR = "3478487238524512106"
    SOURCE_SETTLEMENT_TOKEN = "0xFd57b4ddBf88a4e07fF4e34C487b99af2Fe82a05"
    DESTINATION_SETTLEMENT_TOKEN = "0xA8C0c11bf64AF62CDCA6f93D3769B88BdD7cb93D"
    CCIP_BNM_DECIMALS = "18"
}
foreach ($entry in $publicValues.GetEnumerator()) {
    Set-Item -Path "Env:$($entry.Key)" -Value $entry.Value
}

if ($PublicWebOrigin) {
    if ($PublicWebOrigin -notmatch '^https://[^\s/]+(?:/.*)?$') { throw "PublicWebOrigin must use HTTPS" }
    $env:MANAGED_PUBLIC_WEB_ORIGIN = $PublicWebOrigin.TrimEnd('/')
}
if ($PublicApiOrigin) {
    if ($PublicApiOrigin -notmatch '^https://[^\s/]+(?:/.*)?$') { throw "PublicApiOrigin must use HTTPS" }
    $env:MANAGED_PUBLIC_API_ORIGIN = $PublicApiOrigin.TrimEnd('/')
}

$listenerSecret = $null
$secretValues = $null
Write-Host "Managed testnet configuration loaded from Windows DPAPI storage."
Write-Host "Administrator DID: $env:PLATFORM_ADMIN_DID"
Write-Host "No secret values were printed."
