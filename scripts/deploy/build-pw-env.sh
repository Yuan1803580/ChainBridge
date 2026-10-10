#!/usr/bin/env bash
set -e
cd /opt/agent-crosschain

# 1) Fix nested hardhat-artifacts (scp -r created an extra level)
if [ -d /opt/agent-crosschain/hardhat-artifacts/hardhat-artifacts ]; then
  mv /opt/agent-crosschain/hardhat-artifacts/hardhat-artifacts/* /opt/agent-crosschain/hardhat-artifacts/ 2>/dev/null || true
  rmdir /opt/agent-crosschain/hardhat-artifacts/hardhat-artifacts 2>/dev/null || true
fi
if [ -f /opt/agent-crosschain/hardhat-artifacts/contracts/src/RemoteWorkBoard.sol/RemoteWorkBoard.json ]; then
  echo "ARTIFACTS_OK"
else
  echo "ARTIFACTS_MISSING"
fi

ENV=/opt/agent-crosschain/.env
get(){ grep "^$1=" "$ENV" | head -1 | cut -d= -f2-; }
SRC_RPC=$(get SOURCE_RPC_URL)
DST_RPC=$(get DESTINATION_RPC_URL)
ADMIN_DID=$(get PLATFORM_ADMIN_DID)
ADMIN_DID=${ADMIN_DID:-did:local:platform-admin}
# WORKER_INTENT_HMAC_SECRET / AGENT_RUNNER_API_TOKEN are not stored in .env.
# The relayer derives its HMAC secret from LISTENER_HMAC_KEYS (worker1:<secret>);
# reuse that secret here. It is only consumed when the worker signs agent-invocation
# intents (DEPLOY_ROUTE_STACK never invokes agents), so a shared value is sufficient.
LHK=$(get LISTENER_HMAC_KEYS)
WORKER_INTENT_HMAC_SECRET=${LHK#*:}
AGENT_RUNNER_API_TOKEN=${WORKER_INTENT_HMAC_SECRET}
# Protocol Worker also registers runtime contracts with the API listener, which
# authenticates via LISTENER_HMAC_KEYS (worker1:<secret>). The worker derives its
# listener identity from PROTOCOL_WORKER_LISTENER_ID / PROTOCOL_WORKER_LISTENER_SECRET.
PROTOCOL_WORKER_LISTENER_ID=${LHK%%:*}
PROTOCOL_WORKER_LISTENER_SECRET=${LHK#*:}

{
  echo "NODE_OPTIONS=--experimental-strip-types"
  echo "CHAIN_WORKER_PROFILE=testnet-ccip"
  echo "PLATFORM_PROFILE=managed-testnet"
  echo "SETTLEMENT_MODE=ccip_direct"
  echo "DYNAMIC_SOURCE_CHAIN_ID=11155111"
  echo "DYNAMIC_SOURCE_RPC_URL=$SRC_RPC"
  echo "DYNAMIC_SOURCE_SETTLEMENT_TOKEN=$(get SOURCE_SETTLEMENT_TOKEN)"
  echo "HOME_CHAIN_RPC_URLS={\"421614\":\"$DST_RPC\"}"
  echo "PROTOCOL_ADMIN_DID=$ADMIN_DID"
  echo "PROTOCOL_RELAYER_PRIVATE_KEY=$(get RELAYER_PRIVATE_KEY)"
  echo "PROTOCOL_WORKER_API_TOKEN=$(get PROTOCOL_WORKER_API_TOKEN)"
  echo "AGENT_RUNNER_API_TOKEN=$AGENT_RUNNER_API_TOKEN"
  echo "WORKER_INTENT_HMAC_SECRET=$WORKER_INTENT_HMAC_SECRET"
  echo "PROTOCOL_WORKER_LISTENER_ID=$PROTOCOL_WORKER_LISTENER_ID"
  echo "PROTOCOL_WORKER_LISTENER_SECRET=$PROTOCOL_WORKER_LISTENER_SECRET"
  echo "API_URL=http://api:8000"
  echo "LOCAL_PROTOCOL_RUNTIME_DIR=/app/.runtime/protocol-deploy"
  echo "AUTO_INVOKE_AGENTS=true"
  echo "SOURCE_RPC_URL=$SRC_RPC"
  echo "DESTINATION_RPC_URL=$DST_RPC"
  echo "SOURCE_SETTLEMENT_TOKEN=$(get SOURCE_SETTLEMENT_TOKEN)"
  echo "DESTINATION_SETTLEMENT_TOKEN=$(get DESTINATION_SETTLEMENT_TOKEN)"
  echo "SOURCE_CCIP_ROUTER=$(get SOURCE_CCIP_ROUTER)"
  echo "DESTINATION_CCIP_ROUTER=$(get DESTINATION_CCIP_ROUTER)"
  echo "SOURCE_CHAIN_SELECTOR=$(get SOURCE_CHAIN_SELECTOR)"
  echo "DESTINATION_CHAIN_SELECTOR=$(get DESTINATION_CHAIN_SELECTOR)"
  echo "LISTENER_HMAC_KEYS=$(get LISTENER_HMAC_KEYS)"
} > /opt/agent-crosschain/protocol-worker.env

echo "ENV_FILE_LINES=$(wc -l < /opt/agent-crosschain/protocol-worker.env)"
echo "DONE"
