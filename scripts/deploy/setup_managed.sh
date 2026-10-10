#!/usr/bin/env bash
set -e
BASE=/opt/agent-crosschain
cd "$BASE"

# Read the existing deployment values (avoid sourcing the whole .env)
SOURCE_RPC_URL=$(grep '^SOURCE_RPC_URL=' ./.env | head -1 | cut -d= -f2-)
DESTINATION_RPC_URL=$(grep '^DESTINATION_RPC_URL=' ./.env | head -1 | cut -d= -f2-)
SOURCE_SETTLEMENT_TOKEN=$(grep '^SOURCE_SETTLEMENT_TOKEN=' ./.env | head -1 | cut -d= -f2-)
DESTINATION_SETTLEMENT_TOKEN=$(grep '^DESTINATION_SETTLEMENT_TOKEN=' ./.env | head -1 | cut -d= -f2-)

SIDECAR_STORAGE_KEY=$(openssl rand -hex 32)
SIDECAR_PROVISIONING_TOKEN=$(openssl rand -hex 24)

mkdir -p signer-keys
cat > signer-config.json <<'JSON'
{
  "agents": []
}
JSON

add_env() {
  local k="$1" v="$2"
  if grep -q "^${k}=" ./.env; then
    sed -i "s|^${k}=.*|${k}=${v}|" ./.env
  else
    printf '%s=%s\n' "$k" "$v" >> ./.env
  fi
}

add_env PLATFORM_PROFILE managed-testnet
add_env MANAGED_SELF_SERVICE_ONBOARDING true
add_env MANAGED_SELF_SERVICE_CAPABILITIES research
add_env MANAGED_SIDECAR_PUBLIC_URL http://127.0.0.1:8020
add_env SIDECAR_PROVISIONING_TOKEN "$SIDECAR_PROVISIONING_TOKEN"
add_env SIDECAR_STORAGE_KEY "$SIDECAR_STORAGE_KEY"
add_env SIDECAR_KEY_DIR "$BASE/signer-keys"
add_env AGENT_SIGNER_CONFIG_FILE "$BASE/signer-config.json"
add_env DYNAMIC_SOURCE_CHAIN_ID 11155111
add_env DYNAMIC_SOURCE_RPC_URL "$SOURCE_RPC_URL"
add_env DYNAMIC_SOURCE_SETTLEMENT_TOKEN "$SOURCE_SETTLEMENT_TOKEN"
add_env HOME_CHAIN_RPC_URLS "{\"421614\":\"$DESTINATION_RPC_URL\"}"
add_env RUNNER_PROFILE managed

# Mirror managed vars into infra/.env as well
( cd infra && for k in PLATFORM_PROFILE MANAGED_SELF_SERVICE_ONBOARDING MANAGED_SELF_SERVICE_CAPABILITIES MANAGED_SIDECAR_PUBLIC_URL SIDECAR_PROVISIONING_TOKEN SIDECAR_STORAGE_KEY SIDECAR_KEY_DIR AGENT_SIGNER_CONFIG_FILE DYNAMIC_SOURCE_CHAIN_ID DYNAMIC_SOURCE_RPC_URL DYNAMIC_SOURCE_SETTLEMENT_TOKEN HOME_CHAIN_RPC_URLS RUNNER_PROFILE; do
    val=$(grep "^${k}=" "$BASE/.env" | head -1 | cut -d= -f2-)
    if grep -q "^${k}=" ./.env; then sed -i "s|^${k}=.*|${k}=${val}|" ./.env; else printf '%s=%s\n' "$k" "$val" >> ./.env; fi
  done )

echo "MANAGED_ENV_DONE"
echo "SRC_TOKEN=$SOURCE_SETTLEMENT_TOKEN"
echo "DST_TOKEN=$DESTINATION_SETTLEMENT_TOKEN"
echo "PROVISIONING_TOKEN_LEN=${#SIDECAR_PROVISIONING_TOKEN}"
