[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AgentSlug,
    [Parameter(Mandatory = $true)][int]$ChainId,
    [Parameter(Mandatory = $true)][string]$RpcUrl,
    [Parameter(Mandatory = $true)][string]$AgentSecretsPath,
    [Parameter(Mandatory = $true)][string]$PolicyPath,
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$service = Join-Path $projectRoot "services\agent-signer"
$runtime = Join-Path $projectRoot ".runtime\agent-signers\$AgentSlug"

if (-not (Test-Path -LiteralPath $python)) { throw "Platform Python environment is missing" }
if (-not (Test-Path -LiteralPath $AgentSecretsPath)) { throw "Execution Agent secret file is missing" }
if (-not (Test-Path -LiteralPath $PolicyPath)) { throw "Signer policy file is missing" }
if (-not $env:SIGNER_API_TOKEN) { throw "Set SIGNER_API_TOKEN in this terminal" }

New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$env:SIGNER_AGENT_SLUG = $AgentSlug
$env:SIGNER_CHAIN_ID = "$ChainId"
$env:SIGNER_RPC_URL = $RpcUrl
$env:SIGNER_SECRETS_PATH = (Resolve-Path $AgentSecretsPath).Path
$env:SIGNER_POLICY_PATH = (Resolve-Path $PolicyPath).Path
$env:SIGNER_DATABASE_PATH = Join-Path $runtime "signer-audit.db"

$process = Start-Process -FilePath $python -WindowStyle Hidden -PassThru `
    -WorkingDirectory $service `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port") `
    -RedirectStandardOutput (Join-Path $runtime "stdout.log") `
    -RedirectStandardError (Join-Path $runtime "stderr.log")

@{ pid = $process.Id; port = $Port; agentSlug = $AgentSlug } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime "process.json") -Encoding utf8
Write-Host "Signer Sidecar started for $AgentSlug at http://127.0.0.1:$Port"
