#!/usr/bin/env python3
"""Verify the chain-worker container is up, polling, and reporting healthy."""
import sys, time
from sshlib import run

def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)

def main():
    # 1. Container status
    section("1. docker ps - chain-worker")
    out, err = run("docker ps --filter name=chain-worker --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'")
    print(out.strip() or "(no matching container)")
    if err.strip():
        print("ERR:", err.strip())

    # 2. Recent logs
    section("2. chain-worker logs (tail 40)")
    out, err = run("docker logs --tail 40 agent-crosschain-shop-chain-worker-1 2>&1")
    print(out.strip() or "(no logs)")
    if err.strip():
        print("ERR:", err.strip())

    # 3. Health endpoint on 9090
    section("3. worker health http://127.0.0.1:9090/health")
    out, err = run("curl -s -m 10 -o /dev/null -w 'HTTP %{http_code}\\n' http://127.0.0.1:9090/health ; curl -s -m 10 http://127.0.0.1:9090/health ; echo")
    print(out.strip() or "(no response)")
    if err.strip():
        print("ERR:", err.strip())

    # 4. Env passed into the worker container (sanity: addresses present, masked)
    section("4. worker env (masked)")
    cmd = ("docker inspect agent-crosschain-shop-chain-worker-1 "
           "--format '{{range .Config.Env}}{{println .}}{{end}}'")
    out, err = run(cmd)
    for line in out.splitlines():
        if any(k in line for k in ("REAL_", "CHAIN_WORKER", "HMAC", "SETTLEMENT", "SOURCE_", "DEST_", "NODE_OPTIONS", "PROFILE")):
            # mask any secret-looking values
            if "SECRET" in line or "HMAC" in line or "PRIVATE" in line:
                k, _, v = line.partition("=")
                line = f"{k}=***{v[-4:] if v else ''}"
            print(line)
    if err.strip():
        print("ERR:", err.strip())

if __name__ == "__main__":
    main()
