#!/usr/bin/env python3
"""Register the fixed SEPOLIA->ARBITRUM_SEPOLIA CCIP route and queue its deployment.

Run on the ECS. Forges an admin JWT (HS256) using APP_SECRET (same as issue_token),
then POSTs /api/v1/chain-routes and /chain-routes/{id}/deploy.
"""
import base64, hashlib, hmac, json, time, uuid, urllib.request, urllib.error, os, subprocess, sys

# Read APP_SECRET + RPC/token values from the deployment .env
def env_get(name, path="/opt/agent-crosschain/.env"):
    out = subprocess.run(["grep", f"^{name}=", path], capture_output=True, text=True).stdout.strip()
    return out.split("=", 1)[1] if "=" in out else ""

APP_SECRET = env_get("APP_SECRET")
if not APP_SECRET:
    print("ERROR: APP_SECRET not found"); sys.exit(1)

API = "http://127.0.0.1:8000"

def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")

def admin_id():
    """Resolve the platform-admin agent's UUID primary key from the DB.

    get_current_agent() looks agents up by primary key (Agent.id), NOT by did,
    so the JWT 'sub' claim must be the UUID, not the did string.
    """
    cid = subprocess.run(["docker", "ps", "--filter", "name=postgres", "-q"],
                         capture_output=True, text=True).stdout.strip().splitlines()
    if not cid:
        return None
    out = subprocess.run(
        ["docker", "exec", cid[0], "psql", "-U", "agent_market", "-d", "agent_market",
         "-t", "-c", "SELECT id FROM agents WHERE did='did:local:platform-admin';"],
        capture_output=True, text=True).stdout.strip()
    return out.split()[0] if out else None

ADMIN_ID = admin_id()
if not ADMIN_ID:
    print("ERROR: platform-admin agent id not found in DB"); sys.exit(1)

def make_jwt(aid):
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": aid,
        "did": "did:local:platform-admin",
        "roles": ["admin"],
        "iat": now,
        "exp": now + 3600,
        "iss": "agent-market-api",
        "aud": "agent-market",
        "jti": str(uuid.uuid4()),
    }
    seg = b64(json.dumps(header, separators=(",", ":")).encode()) + b"." + b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(APP_SECRET.encode(), seg, hashlib.sha256).digest()
    return (seg + b"." + b64(sig)).decode()

TOKEN = make_jwt(ADMIN_ID)

SOURCE_TOKEN = env_get("SOURCE_SETTLEMENT_TOKEN")   # 0x1c7D4B19... (USDC, 6dp)
DEST_TOKEN = env_get("DESTINATION_SETTLEMENT_TOKEN") # 0x75faf1... (USDC, 6dp)

route = {
    "routeKey": "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1",
    "sourceChainId": 11155111,
    "destinationChainId": 421614,
    "rpcUrl": "secretref:DESTINATION_RPC_URL",
    "transport": "CHAINLINK_CCIP",
    "sourceChainSelector": "16015286601757825753",
    "destinationChainSelector": "3478487238524512106",
    "sourceRouterAddress": "0x0BF3dE8c5D3e8A2B34D2BEeB17ABfCeBaf363A59",
    "destinationRouterAddress": "0x2a9C5afB0d0e4BAb2BCdaE109EC4b0c4Be15a165",
    "sourceTokenAddress": SOURCE_TOKEN,
    "destinationTokenAddress": DEST_TOKEN,
    "tokenAddress": DEST_TOKEN,
    "tokenDecimals": 6,
    "settlementMode": "ccip_direct",
    "settlementAsset": "CCIP-BNM",
    "payoutAsset": "CCIP-BNM",
    "payoutDecimals": 6,
    "payoutRate": "1",
    "payoutSlippageBps": 0,
    "confirmationBlocks": 5,
}

def post_json(path, body, token):
    data = json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}",
                                           "Idempotency-Key": "managed-ccip-bnm-route-v1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

print("=== create route ===")
st, body = post_json("/api/v1/chain-routes", route, TOKEN)
print("STATUS", st)
print(body[:1500])

if st in (200, 201):
    try:
        res = json.loads(body)
        rid = res.get("id")
    except Exception:
        rid = None
    if rid:
        print("\n=== queue deploy ===")
        st2, body2 = post_json(f"/api/v1/chain-routes/{rid}/deploy", {}, TOKEN)
        print("STATUS", st2)
        print(body2[:1500])
