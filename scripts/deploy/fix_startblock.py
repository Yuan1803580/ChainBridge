#!/usr/bin/env python3
"""Find the escrow/receiver deployment blocks via eth_getCode binary search, then set
SOURCE_START_BLOCK / DESTINATION_START_BLOCK so the chain-worker syncs only the relevant
recent range (instead of backfilling from block 0, which 429s the RPC and stays far behind
head). Run on the ECS via python3."""
import json, urllib.request, time, os

def rpc(url, method, params, tries=6):
    data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    for _ in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                res = json.loads(r.read().decode())
            if "error" in res:
                time.sleep(2); continue
            return res.get("result")
        except Exception:
            time.sleep(2)
    return None

def get_code(url, addr, block):
    return rpc(url, "eth_getCode", [addr, hex(block)])

def find_deploy_block(url, addr, head):
    top = get_code(url, addr, head)
    if not top or top == "0x":
        return None  # contract not present at head (unexpected)
    low, high = 0, head
    while low < high:
        mid = (low + high) // 2
        code = get_code(url, addr, mid)
        if code and code != "0x":
            high = mid
        else:
            low = mid + 1
    return low

ESCROW = "0x192068e261b6eb553f58a084027cc38c40ef0d17"   # source (Sepolia)
RECEIVER = "0x409c31b165aa2f12d47c5eb60fa59f73d5277778"  # destination (Arbitrum Sepolia)

# load RPCs from compose .env
env = {}
with open("/opt/agent-crosschain/infra/.env") as f:
    for line in f:
        line = line.rstrip("\n")
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
SEP_RPC = env["SOURCE_RPC_URL"]
ARB_RPC = env["DESTINATION_RPC_URL"]

sep_head = int(rpc(SEP_RPC, "eth_blockNumber", []), 16)
arb_head = int(rpc(ARB_RPC, "eth_blockNumber", []), 16)
print(f"sep_head={sep_head} arb_head={arb_head}")

esc_deploy = find_deploy_block(SEP_RPC, ESCROW, sep_head)
rec_deploy = find_deploy_block(ARB_RPC, RECEIVER, arb_head)
print(f"esc_deploy={esc_deploy} rec_deploy={rec_deploy}")

if esc_deploy is not None:
    src_start = max(0, esc_deploy - 20)
else:
    src_start = max(0, sep_head - 300_000)
if rec_deploy is not None:
    dst_start = max(0, rec_deploy - 20)
else:
    dst_start = max(0, arb_head - 5_000_000)
print(f"SOURCE_START_BLOCK={src_start} DESTINATION_START_BLOCK={dst_start}")

for path in ["/opt/agent-crosschain/infra/.env", "/opt/agent-crosschain/.env"]:
    if not os.path.exists(path):
        print("MISSING", path); continue
    with open(path) as f:
        lines = f.readlines()
    out, seen_s, seen_d = [], False, False
    for l in lines:
        if l.startswith("SOURCE_START_BLOCK="):
            out.append(f"SOURCE_START_BLOCK={src_start}\n"); seen_s = True
        elif l.startswith("DESTINATION_START_BLOCK="):
            out.append(f"DESTINATION_START_BLOCK={dst_start}\n"); seen_d = True
        else:
            out.append(l)
    if not seen_s: out.append(f"SOURCE_START_BLOCK={src_start}\n")
    if not seen_d: out.append(f"DESTINATION_START_BLOCK={dst_start}\n")
    with open(path, "w") as f:
        f.writelines(out)
    print("WROTE", path)
