#!/usr/bin/env python3
"""Check presence of all expected worker env vars (presence only, no values leaked)."""
from sshlib import run

EXPECTED = [
    "REAL_CCIP_SENDER_ADDRESS",
    "REAL_CCIP_RECEIVER_ADDRESS",
    "REAL_MARKETPLACE_ESCROW_ADDRESS",
    "SOURCE_RPC_URL", "SOURCE_CHAIN_ID", "SOURCE_CHAIN_SELECTOR",
    "DESTINATION_RPC_URL", "DESTINATION_CHAIN_ID", "DESTINATION_CHAIN_SELECTOR",
    "CHAIN_WORKER_ID", "CHAIN_WORKER_HMAC_SECRET", "LISTENER_HMAC_KEYS",
    "SETTLEMENT_MODE", "REAL_PLATFORM_SIGNER", "MOCK_CCIP_SENDER_FOR_ESCROW",
    "SOURCE_SETTLEMENT_TOKEN", "DESTINATION_SETTLEMENT_TOKEN",
]

def main():
    cmd = ("docker inspect agent-crosschain-shop-chain-worker-1 "
           "--format '{{range .Config.Env}}{{println .}}{{end}}'")
    out, err = run(cmd)
    env = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        env[k] = v
    print("PRESENT / MISSING env check:")
    for k in EXPECTED:
        if k in env:
            v = env[k]
            masked = ("***" + v[-4:]) if ("SECRET" in k or "HMAC" in k or "PRIVATE" in k) else ("<set len=%d>" % len(v))
            print(f"  [OK]   {k} = {masked}")
        else:
            print(f"  [MISS] {k}")
    # Also report sourceNextBlock progress
    print("\nHealth snapshot:")
    out2, _ = run("curl -s -m 10 http://127.0.0.1:9090/health; echo")
    print(out2.strip())

if __name__ == "__main__":
    main()
